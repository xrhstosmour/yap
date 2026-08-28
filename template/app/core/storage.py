"""Blob storage service for MinIO / S3-compatible object storage.

Provides upload, download, delete, and presigned URL generation.
Image files get a thumbnail and dimensions generated asynchronously
after upload by ``app.tasks.storage.generate_thumbnail_task``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from typing import Any
from uuid import UUID

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.core.settings import settings
from app.core.tenant import get_current_tenant_id

logger = get_logger("storage")

_cached_s3_client: Any | None = None

# boto3/botocore error codes head_bucket raises when the bucket is missing.
_BUCKET_NOT_FOUND_CODES = {"404", "NoSuchBucket"}


def _s3_client() -> Any:  # noqa: ANN401
    """Return a cached boto3 S3 client, creating it on first call.

    boto3 clients are thread-safe and safe to reuse across calls.
    """
    global _cached_s3_client
    if _cached_s3_client is None:
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
        _cached_s3_client = session.client("s3", **kwargs)
    return _cached_s3_client


async def _ensure_bucket(bucket: str) -> None:
    """Create the bucket if it does not exist (idempotent)."""
    from botocore.exceptions import ClientError

    client = _s3_client()
    try:
        await asyncio.to_thread(client.head_bucket, Bucket=bucket)
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code not in _BUCKET_NOT_FOUND_CODES:
            logger.error(
                "bucket_head_check_failed",
                bucket=bucket,
                error_code=error_code,
                error=str(error),
            )
            raise
        await asyncio.to_thread(client.create_bucket, Bucket=bucket)


def _build_thumbnail(content: bytes, mimetype: str) -> tuple[int, int, bytes]:
    """Decode an image and build a thumbnail (CPU-bound, run off the event loop).

    Args:
        content: Raw image bytes.
        mimetype: MIME type of the source image.

    Returns:
        Tuple of (width, height, thumbnail_bytes).
    """
    import PIL.Image

    img = PIL.Image.open(io.BytesIO(content))
    width, height = img.size

    # Generate thumbnail (800px max dimension).
    thumb = img.copy()
    thumb.thumbnail((800, 800), PIL.Image.LANCZOS)  # type: ignore[attr-defined]
    thumb_buffer = io.BytesIO()
    thumb_format = "JPEG" if mimetype == "image/jpeg" else "PNG"
    thumb.save(thumb_buffer, format=thumb_format)

    return width, height, thumb_buffer.getvalue()


def is_thumbnailable(mimetype: str) -> bool:
    """Whether a mimetype is an image type that gets a generated thumbnail."""
    return mimetype.startswith("image/") and mimetype != "image/gif"


def build_object_key(
    prefix: str,
    content_hash: str,
    uploaded_by: UUID,
    tenant_id: UUID | None = None,
) -> str:
    """Build a content-addressed object key namespaced by tenant and uploader.

    Deduplication is per uploader, not per tenant and not global (see the
    ``File`` model docstring): every distinct uploader of identical bytes
    owns a row and owns a storage object. A key that stops at the tenant
    breaks that, two of that tenant's rows point at one object, so the
    moment the first uploader's ``reference_count`` hits zero its purge
    deletes the bytes the second still references and every download 404s.

    Falls back to ``SYSTEM_TENANT_ID`` when no tenant context is set,
    matching how ``BaseRepository`` fills ``tenant_id`` on insert, so the
    key always names the same tenant as the row that stores it.

    Args:
        prefix: Key namespace, ``uploads`` or ``thumbnails``.
        content_hash: SHA-256 hash of the content.
        uploaded_by: ID of the user the stored row belongs to.
        tenant_id: Owning tenant. Defaults to the current tenant context.

    Returns:
        The object key to store the content under.
    """
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
    return f"{prefix}/{tenant_id or SYSTEM_TENANT_ID}/{uploaded_by}/{content_hash}"


async def upload_object(
    object_key: str,
    content: bytes,
    mimetype: str,
    bucket: str | None = None,
) -> None:
    """Upload raw bytes to blob storage under the given key.

    Args:
        object_key: The object key to store the content under.
        content: Raw bytes to upload.
        mimetype: MIME type of the content.
        bucket: Storage bucket. Defaults to ``settings.STORAGE_BUCKET``.
    """
    bucket = bucket or settings.STORAGE_BUCKET
    await _ensure_bucket(bucket)
    client = _s3_client()
    await asyncio.to_thread(
        client.put_object,
        Bucket=bucket,
        Key=object_key,
        Body=content,
        ContentType=mimetype,
    )


async def download_object(object_key: str, bucket: str | None = None) -> bytes:
    """Download raw bytes from blob storage.

    Args:
        object_key: The object key to fetch.
        bucket: Storage bucket. Defaults to ``settings.STORAGE_BUCKET``.

    Returns:
        Raw object bytes.
    """
    bucket = bucket or settings.STORAGE_BUCKET
    client = _s3_client()
    response = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=object_key)
    return await asyncio.to_thread(response["Body"].read)


async def upload_file(
    content: bytes,
    mimetype: str,
    uploaded_by: UUID,
    bucket: str | None = None,
) -> tuple[str, str]:
    """Upload file bytes to blob storage.

    Computes the SHA-256 hash and uploads the original to MinIO/S3.
    Image thumbnails and dimensions are generated afterward by
    ``app.tasks.storage.generate_thumbnail_task``, not inline, so this
    call doesn't hold the request open for the resize.

    Args:
        content: Raw file bytes.
        mimetype: MIME type of the file.
        uploaded_by: ID of the user the stored row will belong to.
        bucket: Storage bucket. Defaults to ``settings.STORAGE_BUCKET``.

    Returns:
        Tuple of (object_key, content_hash).
    """
    bucket = bucket or settings.STORAGE_BUCKET
    content_hash = hashlib.sha256(content).hexdigest()
    object_key = build_object_key("uploads", content_hash, uploaded_by)

    await upload_object(object_key, content, mimetype, bucket=bucket)

    return object_key, content_hash


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
    from typing import cast

    client = _s3_client()
    bucket = bucket or settings.STORAGE_BUCKET
    url = await asyncio.to_thread(
        client.generate_presigned_url,
        "get_object",
        Parameters={"Bucket": bucket, "Key": object_key},
        ExpiresIn=expires_in,
    )
    return cast(str, url)


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
        await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=object_key)
    except client.exceptions.ClientError:
        pass  # Already deleted. Idempotent.
