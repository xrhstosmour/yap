"""Tests for FileService."""
from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest

from app.models.file import File
from app.services.file_service import FileService


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def service(mock_session: MagicMock) -> FileService:
    svc = FileService(mock_session)
    repo = AsyncMock()
    repo.get_by_content_hash = AsyncMock()
    repo.get_owned = AsyncMock()
    repo.increment_reference_count = AsyncMock()
    repo.decrement_reference_count = AsyncMock()
    repo.delete = AsyncMock()
    svc.file_repository = repo
    return svc


def make_user(user_id: str | None = None) -> MagicMock:
    user = MagicMock()
    user.id = UUID(user_id or "00000000-0000-0000-0000-000000000001")
    return user


class TestUpload:
    """Tests for upload()."""

    @pytest.mark.asyncio
    async def test_creates_new_record_for_new_file(
        self, service: FileService
    ) -> None:
        """Should create a new file record when hash is not found."""
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "test.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(return_value=b"unique content")

        service.file_repository.get_by_content_hash = AsyncMock(return_value=None)

        with (
            patch("app.services.file_service.upload_file") as mock_upload,
        ):
            mock_upload.return_value = (
                "uploads/abc123", "abc123", None, None, None
            )
            record = await service.upload(mock_file, user)

        assert record.filename == "test.txt"
        assert record.mimetype == "text/plain"
        assert record.reference_count == 1
        service.session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_increments_reference_count_for_duplicate(
        self, service: FileService
    ) -> None:
        """Should increment reference_count when hash already exists."""
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "dup.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(return_value=b"duplicate content")

        existing = File(
            filename="dup.txt",
            mimetype="text/plain",
            size=10,
            content_hash="abc123",
            bucket="default",
            object_key="uploads/abc123",
            reference_count=1,
            uploaded_by=user.id,
        )
        service.file_repository.get_by_content_hash = AsyncMock(return_value=existing)

        record = await service.upload(mock_file, user)

        assert record is existing
        service.file_repository.increment_reference_count.assert_awaited_once()


class TestDelete:
    """Tests for delete()."""

    @pytest.mark.asyncio
    async def test_purges_from_storage_when_count_reaches_zero(
        self, service: FileService
    ) -> None:
        """Should delete from blob storage when reference_count hits zero."""
        user = make_user()
        file_id = UUID("00000000-0000-0000-0000-000000000002")

        record = File(
            id=file_id,
            filename="test.txt",
            mimetype="text/plain",
            size=10,
            content_hash="abc123",
            bucket="default",
            object_key="uploads/abc123",
            reference_count=1,
            uploaded_by=user.id,
        )
        service.file_repository.get_owned = AsyncMock(return_value=record)
        service.file_repository.decrement_reference_count = AsyncMock(return_value=0)

        with patch("app.services.file_service.delete_object") as mock_delete:
            await service.delete(file_id, user)

        mock_delete.assert_called_once_with(object_key="uploads/abc123", bucket="default")
        service.file_repository.delete.assert_awaited_once_with(file_id)
