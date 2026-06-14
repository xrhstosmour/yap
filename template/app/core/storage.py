"""Blob storage service for MinIO / S3-compatible object storage.

Provides upload, download, delete, and presigned URL generation.
Image files get automatic thumbnail generation and dimension extraction.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from app.core.settings import settings


def _s3_client() -> Any:
    """Create a boto3 S3 client configured for MinIO or cloud S3."""
    import boto3

    session = boto3.Session(
        aws_access_key_id=settings.STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.STORAGE_SECRET_KEY,
        region_name=settings.STORAGE_REGION,
    )
    kwargs: dict[str, Any] = {}
    if settings.STORAGE_ENDPOINT:
        kwargs["endpoint_url"] = settings.STORAGE_ENDPOINT
        # MinIO uses path-style addressing by default.
        kwargs["config"] = boto3.session.Config(signature_version="s3v4")

    return session.client("s3", **kwargs)


def _ensure_bucket(bucket: str) -> None:
    """Create the bucket if it does not exist (idempotent)."""
    client = _s3_client()
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)


async def upload_file(
    content: bytes,
    filename: str,
    mimetype: str,
    bucket: str | None = None,
) -> tuple[str, str, int | None, int | None, str | None]:
    """Upload file bytes to blob storage.

    Computes the SHA-256 hash, uploads to MinIO/S3, and extracts
    image dimensions. If the file is an image, a thumbnail is also
    generated.

    Args:
        content: Raw file bytes.
        filename: Original filename (used for content-type).
        mimetype: MIME type of the file.
        bucket: Storage bucket. Defaults to ``settings.STORAGE_BUCKET``.

    Returns:
        Tuple of (object_key, content_hash, image_width, image_height,
        thumbnail_object_key).
    """
    import PIL.Image

    bucket = bucket or settings.STORAGE_BUCKET
    _ensure_bucket(bucket)
    client = _s3_client()

    content_hash = hashlib.sha256(content).hexdigest()
    object_key = f"uploads/{content_hash}"

    # Upload original.
    client.put_object(
        Bucket=bucket,
        Key=object_key,
        Body=content,
        ContentType=mimetype,
    )

    image_width: int | None = None
    image_height: int | None = None
    thumbnail_object_key: str | None = None

    if mimetype.startswith("image/") and mimetype != "image/gif":
        try:
            img = PIL.Image.open(io.BytesIO(content))
            image_width, image_height = img.size

            # Generate thumbnail (800px max dimension).
            thumb = img.copy()
            thumb.thumbnail((800, 800), PIL.Image.LANCZOS)
            thumb_buffer = io.BytesIO()
            thumb_format = "JPEG" if mimetype == "image/jpeg" else "PNG"
            thumb.save(thumb_buffer, format=thumb_format)
            thumb_bytes = thumb_buffer.getvalue()

            thumbnail_object_key = f"thumbnails/{content_hash}"
            client.put_object(
                Bucket=bucket,
                Key=thumbnail_object_key,
                Body=thumb_bytes,
                ContentType=mimetype,
            )
        except Exception:
            pass  # Non-image or unprocessable — thumbnails not critical.

    return object_key, content_hash, image_width, image_height, thumbnail_object_key


async def get_download_url(
    object_key: str,
    bucket: str | None = None,
    expires_in: int = 3600,
) -> str:
    """Generate a presigned download URL for an object.

    Args:
        object_key: The object key in the bucket.
        bucket: Storage bucket. Defaults to ``settings.STORAGE_BUCKET``.
        expires_in: URL expiry in seconds (default 1 hour).

    Returns:
        Presigned URL string.
    """
    client = _s3_client()
    bucket = bucket or settings.STORAGE_BUCKET
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": object_key},
        ExpiresIn=expires_in,
    )


async def delete_object(
    object_key: str,
    bucket: str | None = None,
) -> None:
    """Delete an object from blob storage.

    Args:
        object_key: The object key to delete.
        bucket: Storage bucket. Defaults to ``settings.STORAGE_BUCKET``.
    """
    client = _s3_client()
    bucket = bucket or settings.STORAGE_BUCKET
    try:
        client.delete_object(Bucket=bucket, Key=object_key)
    except client.exceptions.ClientError:
        pass  # Already deleted — idempotent.
