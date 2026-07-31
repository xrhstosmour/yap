"""Unit tests for StorageService helpers."""

from __future__ import annotations

from unittest.mock import ANY
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from app.core.storage import _ensure_bucket
from app.core.storage import _s3_client
from app.core.storage import delete_object
from app.core.storage import get_download_url
from app.core.storage import upload_file


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
        object_key, content_hash, *_ = await upload_file(
            content=content,
            filename="test.txt",
            mimetype="text/plain",
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

        await upload_file(content=b"test", filename="t.txt", mimetype="text/plain")

        mock_s3_client.create_bucket.assert_called_once()


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


class TestUploadFileImage:
    """Tests for upload_file() with image content."""

    @pytest.mark.asyncio
    async def test_image_upload_creates_thumbnail(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Should generate a thumbnail when uploading an image."""
        mock_img = MagicMock()
        mock_img.size = (1920, 1080)
        mock_img.copy.return_value = mock_img

        with patch("PIL.Image.open", return_value=mock_img):
            object_key, content_hash, width, height, thumb_key = await upload_file(
                content=b"fake-image-data",
                filename="photo.png",
                mimetype="image/png",
            )

        assert width == 1920
        assert height == 1080
        assert thumb_key is not None
        assert thumb_key.startswith("thumbnails/")

        # Should have two put_object calls: original + thumbnail.
        assert mock_s3_client.put_object.call_count == 2

    @pytest.mark.asyncio
    async def test_gif_images_skip_thumbnail(self, mock_s3_client: MagicMock) -> None:
        """Should NOT generate a thumbnail for GIF images."""
        mock_img = MagicMock()
        mock_img.size = (320, 240)

        with patch("PIL.Image.open", return_value=mock_img):
            object_key, content_hash, width, height, thumb_key = await upload_file(
                content=b"fake-gif-data",
                filename="anim.gif",
                mimetype="image/gif",
            )

        # GIF is excluded from thumbnail generation.
        assert thumb_key is None
        # Only one put_object call (original only).
        assert mock_s3_client.put_object.call_count == 1

    @pytest.mark.asyncio
    async def test_unprocessable_image_logs_warning(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Should log a warning when thumbnail generation fails."""
        with (
            patch("PIL.Image.open", side_effect=OSError("cannot identify image")),
            patch("app.core.storage.logger") as mock_logger,
        ):
            object_key, content_hash, width, height, thumb_key = await upload_file(
                content=b"not-actually-an-image",
                filename="broken.png",
                mimetype="image/png",
            )

        assert thumb_key is None
        assert width is None
        assert height is None
        mock_logger.warning.assert_called_once()
        arguments, keyword_arguments = mock_logger.warning.call_args
        assert arguments[0] == "thumbnail_generation_failed"
        assert keyword_arguments.get("exc_info") is True


class TestDeleteObject:
    """Tests for delete_object()."""

    @pytest.mark.asyncio
    async def test_delete_object_idempotent_on_client_error(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Should not raise when ClientError occurs (already deleted)."""

        # Define a local ClientError class (botocore may not be installed directly).
        class ClientError(Exception):
            pass

        mock_s3_client.exceptions.ClientError = ClientError
        mock_s3_client.delete_object.side_effect = ClientError("NoSuchKey")

        # Should not raise.
        await delete_object(object_key="non-existent-key")

        mock_s3_client.delete_object.assert_called_once_with(
            Bucket=ANY, Key="non-existent-key"
        )
