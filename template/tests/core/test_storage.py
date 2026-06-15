"""Unit tests for StorageService helpers."""
from __future__ import annotations

from unittest.mock import ANY
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from app.core.storage import get_download_url
from app.core.storage import upload_file


@pytest.fixture(autouse=True)
def mock_s3_client() -> MagicMock:
    """Mock boto3 S3 client for all tests."""
    with patch("app.core.storage._s3_client") as mock:
        client = MagicMock()
        client.head_bucket = MagicMock()
        client.create_bucket = MagicMock()
        client.put_object = MagicMock()
        client.generate_presigned_url = MagicMock(return_value="https://presigned.url/test")
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
    async def test_creates_bucket_if_missing(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Should create bucket on first upload."""
        mock_s3_client.head_bucket.side_effect = Exception("Not found")

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
