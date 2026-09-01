"""Unit tests for StorageService helpers."""

from __future__ import annotations

from unittest.mock import ANY
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from app.core import SYSTEM_TENANT_ID
from app.core.storage import _build_thumbnail
from app.core.storage import _ensure_bucket
from app.core.storage import _s3_client
from app.core.storage import build_object_key
from app.core.storage import delete_object
from app.core.storage import download_object
from app.core.storage import get_download_url
from app.core.storage import is_thumbnailable
from app.core.storage import upload_file
from app.core.storage import upload_object
from app.core.tenant import tenant_context

# Object keys name the uploader, so every call needs one.
UPLOADER = UUID("00000000-0000-0000-0000-0000000000b1")


def _client_error(code: str, operation_name: str = "HeadBucket") -> ClientError:
    """Build a botocore ClientError with the given error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation_name)


@pytest.fixture(autouse=True)
def mock_s3_client() -> MagicMock:
    """Mock boto3 S3 client for all tests."""
    with patch("app.core.storage._s3_client") as mock:
        client = MagicMock()
        client.head_bucket = MagicMock()
        client.create_bucket = MagicMock()
        client.put_object = MagicMock()
        client.generate_presigned_url = MagicMock(
            return_value="https://presigned.url/test"
        )
        mock.return_value = client
        yield client


class TestUploadFile:
    """Tests for upload_file()."""

    @pytest.mark.asyncio
    async def test_uploads_content_and_returns_key(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Should upload content and return object key with hash."""
        content = b"hello world"
        object_key, content_hash = await upload_file(
            content=content,
            mimetype="text/plain",
            uploaded_by=UPLOADER,
        )

        assert (
            content_hash
            == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )
        assert "uploads/" in object_key
        mock_s3_client.put_object.assert_called_once_with(
            Bucket=ANY, Key=ANY, Body=content, ContentType="text/plain"
        )

    @pytest.mark.asyncio
    async def test_creates_bucket_if_missing(self, mock_s3_client: MagicMock) -> None:
        """Should create bucket on first upload."""
        mock_s3_client.head_bucket.side_effect = _client_error("404")

        await upload_file(content=b"test", mimetype="text/plain", uploaded_by=UPLOADER)

        mock_s3_client.create_bucket.assert_called_once()


class TestObjectKeyNamespacing:
    """Object keys must name both the tenant and the uploader.

    Deduplication is per uploader (see the `File` model docstring), so
    every distinct uploader of identical bytes owns a row with its own
    reference count. On a shared key, the first of them to reach zero
    purges the object out from under the rest, whose downloads then 404.
    """

    FIRST_TENANT = UUID("00000000-0000-0000-0000-0000000000a1")
    SECOND_TENANT = UUID("00000000-0000-0000-0000-0000000000a2")
    FIRST_UPLOADER = UUID("00000000-0000-0000-0000-0000000000b1")
    SECOND_UPLOADER = UUID("00000000-0000-0000-0000-0000000000b2")

    @pytest.mark.asyncio
    async def test_identical_content_gets_a_key_per_tenant(self) -> None:
        """Two tenants uploading the same bytes get two distinct objects."""
        content = b"content uploaded by two tenants"

        with tenant_context(self.FIRST_TENANT):
            first_key, first_hash = await upload_file(
                content=content,
                mimetype="text/plain",
                uploaded_by=self.FIRST_UPLOADER,
            )
        with tenant_context(self.SECOND_TENANT):
            second_key, second_hash = await upload_file(
                content=content,
                mimetype="text/plain",
                uploaded_by=self.FIRST_UPLOADER,
            )

        assert first_hash == second_hash
        assert first_key != second_key
        assert (
            first_key
            == f"uploads/{self.FIRST_TENANT}/{self.FIRST_UPLOADER}/{first_hash}"
        )
        assert (
            second_key
            == f"uploads/{self.SECOND_TENANT}/{self.FIRST_UPLOADER}/{second_hash}"
        )

    @pytest.mark.asyncio
    async def test_identical_content_gets_a_key_per_uploader(self) -> None:
        """Two colleagues uploading the same bytes get two distinct objects."""
        content = b"content uploaded by two colleagues"

        with tenant_context(self.FIRST_TENANT):
            first_key, first_hash = await upload_file(
                content=content,
                mimetype="text/plain",
                uploaded_by=self.FIRST_UPLOADER,
            )
            second_key, second_hash = await upload_file(
                content=content,
                mimetype="text/plain",
                uploaded_by=self.SECOND_UPLOADER,
            )

        assert first_hash == second_hash
        assert first_key != second_key

    def test_falls_back_to_the_system_tenant_without_context(self) -> None:
        """No tenant context keys under `SYSTEM_TENANT_ID`, as rows do."""
        with tenant_context(None):
            key = build_object_key("uploads", "abc123", self.FIRST_UPLOADER)

        assert key == f"uploads/{SYSTEM_TENANT_ID}/{self.FIRST_UPLOADER}/abc123"

    def test_explicit_tenant_wins_over_the_context(self) -> None:
        """A passed tenant is used even when a different one is in context."""
        with tenant_context(self.FIRST_TENANT):
            key = build_object_key(
                "thumbnails", "abc123", self.FIRST_UPLOADER, self.SECOND_TENANT
            )

        assert key == f"thumbnails/{self.SECOND_TENANT}/{self.FIRST_UPLOADER}/abc123"


class TestGetDownloadUrl:
    """Tests for get_download_url()."""

    @pytest.mark.asyncio
    async def test_returns_presigned_url(self, mock_s3_client) -> None:
        """Should return a presigned URL."""
        url = await get_download_url(object_key="test-key")
        assert url == "https://presigned.url/test"
        mock_s3_client.generate_presigned_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_expiry_passed_to_presigned_url(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Should pass custom expiry time to generate_presigned_url."""
        await get_download_url(object_key="test-key", expires_in=900)

        mock_s3_client.generate_presigned_url.assert_called_once()
        _, kwargs = mock_s3_client.generate_presigned_url.call_args
        assert kwargs.get("ExpiresIn") == 900


class TestS3Client:
    """Tests for _s3_client()."""

    def test_s3_client_returns_boto3_client(self) -> None:
        """_s3_client returns a boto3 S3 client via session."""
        mock_client = MagicMock()
        mock_session_instance = MagicMock()
        mock_session_instance.client.return_value = mock_client

        # boto3 is imported inside _s3_client(); inject a mock into sys.modules.
        mock_boto3 = MagicMock()
        mock_boto3.Session.return_value = mock_session_instance

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            result = _s3_client()

        assert result is mock_client
        mock_boto3.Session.assert_called_once()


class TestEnsureBucket:
    """Tests for _ensure_bucket()."""

    @pytest.mark.asyncio
    async def test_ensure_bucket_creates_when_missing(self) -> None:
        """_ensure_bucket calls create_bucket when head_bucket raises 404."""
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = _client_error("404")

        with patch("app.core.storage._s3_client", return_value=mock_client):
            await _ensure_bucket("test-bucket")

        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    @pytest.mark.asyncio
    async def test_ensure_bucket_skips_create_when_exists(self) -> None:
        """_ensure_bucket does not call create_bucket when bucket exists."""
        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}  # No error

        with patch("app.core.storage._s3_client", return_value=mock_client):
            await _ensure_bucket("test-bucket")

        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_client.create_bucket.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_bucket_reraises_non_not_found_error(self) -> None:
        """_ensure_bucket propagates errors other than 'bucket not found'."""
        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = _client_error("403")

        with (
            patch("app.core.storage._s3_client", return_value=mock_client),
            pytest.raises(ClientError),
        ):
            await _ensure_bucket("test-bucket")

        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        mock_client.create_bucket.assert_not_called()


class TestIsThumbnailable:
    """Tests for is_thumbnailable()."""

    def test_png_is_thumbnailable(self) -> None:
        assert is_thumbnailable("image/png") is True

    def test_jpeg_is_thumbnailable(self) -> None:
        assert is_thumbnailable("image/jpeg") is True

    def test_gif_is_not_thumbnailable(self) -> None:
        """GIFs are excluded from thumbnail generation."""
        assert is_thumbnailable("image/gif") is False

    def test_non_image_is_not_thumbnailable(self) -> None:
        assert is_thumbnailable("text/plain") is False


class TestBuildThumbnail:
    """Tests for _build_thumbnail()."""

    def test_builds_thumbnail_from_image(self) -> None:
        """Should return source dimensions and resized thumbnail bytes."""
        mock_img = MagicMock()
        mock_img.size = (1920, 1080)
        mock_img.copy.return_value = mock_img

        with patch("PIL.Image.open", return_value=mock_img):
            width, height, thumbnail_bytes = _build_thumbnail(
                b"fake-image-data", "image/png"
            )

        assert width == 1920
        assert height == 1080
        assert isinstance(thumbnail_bytes, bytes)
        mock_img.thumbnail.assert_called_once()

    def test_unprocessable_image_raises(self) -> None:
        """Should propagate the decode error for the caller to handle."""
        with (
            patch("PIL.Image.open", side_effect=OSError("cannot identify image")),
            pytest.raises(OSError, match="cannot identify image"),
        ):
            _build_thumbnail(b"not-actually-an-image", "image/png")


class TestUploadObject:
    """Tests for upload_object()."""

    @pytest.mark.asyncio
    async def test_uploads_bytes_under_given_key(
        self, mock_s3_client: MagicMock
    ) -> None:
        await upload_object("thumbnails/abc123", b"thumb-bytes", "image/png")

        mock_s3_client.put_object.assert_called_once_with(
            Bucket=ANY,
            Key="thumbnails/abc123",
            Body=b"thumb-bytes",
            ContentType="image/png",
        )

    @pytest.mark.asyncio
    async def test_creates_bucket_if_missing(self, mock_s3_client: MagicMock) -> None:
        mock_s3_client.head_bucket.side_effect = _client_error("404")

        await upload_object("key", b"data", "text/plain")

        mock_s3_client.create_bucket.assert_called_once()


class TestDownloadObject:
    """Tests for download_object()."""

    @pytest.mark.asyncio
    async def test_returns_object_bytes(self, mock_s3_client: MagicMock) -> None:
        mock_body = MagicMock()
        mock_body.read.return_value = b"original-bytes"
        mock_s3_client.get_object.return_value = {"Body": mock_body}

        content = await download_object("uploads/abc123")

        assert content == b"original-bytes"
        mock_s3_client.get_object.assert_called_once_with(
            Bucket=ANY, Key="uploads/abc123"
        )


class TestDeleteObject:
    """Tests for delete_object()."""

    @pytest.mark.asyncio
    async def test_delete_object_idempotent_when_already_gone(
        self, mock_s3_client: MagicMock
    ) -> None:
        """A missing object is not an error, the delete is idempotent."""
        mock_s3_client.delete_object.side_effect = _client_error(
            "NoSuchKey", "DeleteObject"
        )

        await delete_object(object_key="non-existent-key")

        mock_s3_client.delete_object.assert_called_once_with(
            Bucket=ANY, Key="non-existent-key"
        )

    @pytest.mark.asyncio
    async def test_delete_object_raises_on_access_denied(
        self, mock_s3_client: MagicMock
    ) -> None:
        """A denied delete must not be reported as a successful one.

        This replaces a test that asserted the opposite. Swallowing every
        `ClientError` meant a bucket policy without `s3:DeleteObject`
        turned the purge into a silent no-op, the row went away, the blob
        stayed, and nothing was logged.
        """
        mock_s3_client.delete_object.side_effect = _client_error(
            "AccessDenied", "DeleteObject"
        )

        with pytest.raises(ClientError):
            await delete_object(object_key="uploads/abc123")

    @pytest.mark.asyncio
    async def test_delete_object_raises_on_an_unexpected_error(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Any other failure surfaces too, rather than reading as success."""
        mock_s3_client.delete_object.side_effect = _client_error(
            "InternalError", "DeleteObject"
        )

        with pytest.raises(ClientError):
            await delete_object(object_key="uploads/abc123")
