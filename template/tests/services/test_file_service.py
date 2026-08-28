"""Tests for FileService."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest

from app.core import SYSTEM_TENANT_ID
from app.core.settings import settings
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
    repo.create_or_increment = AsyncMock()
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
    async def test_creates_new_record_for_new_file(self, service: FileService) -> None:
        """Should create a new file record when hash is not found."""
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "test.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(side_effect=[b"unique content", b""])

        service.file_repository.get_by_content_hash = AsyncMock(return_value=None)
        new_record = File(
            filename="test.txt",
            mimetype="text/plain",
            size=15,
            content_hash="dummy",
            bucket="default",
            object_key="uploads/abc123",
            reference_count=1,
            uploaded_by=user.id,
        )
        service.file_repository.create_or_increment = AsyncMock(
            return_value=(new_record, True)
        )

        with (
            patch("app.services.file_service.upload_file") as mock_upload,
        ):
            mock_upload.return_value = ("uploads/abc123", "abc123")
            record = await service.upload(mock_file, user)

        assert record.filename == "test.txt"
        assert record.mimetype == "text/plain"
        assert record.reference_count == 1
        service.file_repository.create_or_increment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_increments_reference_count_for_duplicate(
        self, service: FileService
    ) -> None:
        """Should increment reference_count when hash already exists."""
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "dup.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(side_effect=[b"duplicate content", b""])

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

    @pytest.mark.asyncio
    async def test_dedup_lookup_is_scoped_to_the_uploader(
        self, service: FileService
    ) -> None:
        """The dedup lookup must name the uploader, not just the tenant.

        Scoped to the tenant alone it matched a colleague's row and handed
        it back: their filename, their visibility, their file ID, plus a
        reference this caller could never release, since `get_owned`
        filters on `uploaded_by`.
        """
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "shared.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(side_effect=[b"content two users share", b""])

        service.file_repository.get_by_content_hash = AsyncMock(return_value=None)
        service.file_repository.create_or_increment = AsyncMock(
            return_value=(
                File(
                    filename="shared.txt",
                    mimetype="text/plain",
                    size=23,
                    content_hash="shared-hash",
                    bucket="default",
                    object_key="uploads/shared",
                    reference_count=1,
                    uploaded_by=user.id,
                ),
                True,
            )
        )

        with patch("app.services.file_service.upload_file") as mock_upload:
            mock_upload.return_value = ("uploads/shared", "shared-hash")
            await service.upload(mock_file, user)

        arguments = service.file_repository.get_by_content_hash.await_args
        assert user.id in arguments.args or user.id in arguments.kwargs.values()
        # The blob is keyed by uploader too, or the two rows collide again.
        assert mock_upload.await_args.kwargs["uploaded_by"] == user.id

    @pytest.mark.asyncio
    async def test_keeps_the_blob_and_returns_winner_when_insert_race_lost(
        self, service: FileService
    ) -> None:
        """Should leave the blob alone when the upsert reveals a concurrent
        upload of identical content already won the race.

        Object keys are content-addressed and tenant-namespaced, so both
        racers wrote the same bytes to the same key. Deleting "the loser's"
        blob deletes the only blob, the one the winner's row points at.
        """
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "race.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(side_effect=[b"raced content", b""])

        service.file_repository.get_by_content_hash = AsyncMock(return_value=None)
        shared_key = f"uploads/{SYSTEM_TENANT_ID}/raced-hash"
        winner = File(
            filename="race.txt",
            mimetype="text/plain",
            size=13,
            content_hash="raced-hash",
            bucket=settings.STORAGE_BUCKET,
            object_key=shared_key,
            reference_count=2,
            uploaded_by=user.id,
        )
        service.file_repository.create_or_increment = AsyncMock(
            return_value=(winner, False)
        )

        with (
            patch("app.services.file_service.upload_file") as mock_upload,
            patch("app.services.file_service.delete_object") as mock_delete,
        ):
            mock_upload.return_value = (shared_key, "raced-hash")
            record = await service.upload(mock_file, user)

        assert record is winner
        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_thumbnail_task_for_new_image(
        self, service: FileService
    ) -> None:
        """Should dispatch generate_thumbnail_task for a newly created image file."""
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "photo.png"
        mock_file.content_type = "image/png"
        mock_file.read = AsyncMock(side_effect=[b"image bytes", b""])

        service.file_repository.get_by_content_hash = AsyncMock(return_value=None)
        new_record = File(
            filename="photo.png",
            mimetype="image/png",
            size=11,
            content_hash="dummy",
            bucket="default",
            object_key="uploads/abc123",
            reference_count=1,
            uploaded_by=user.id,
        )
        service.file_repository.create_or_increment = AsyncMock(
            return_value=(new_record, True)
        )

        with (
            patch("app.services.file_service.upload_file") as mock_upload,
            patch("app.tasks.storage.generate_thumbnail_task") as mock_task,
        ):
            mock_upload.return_value = ("uploads/abc123", "abc123")
            await service.upload(mock_file, user)

        mock_task.delay.assert_called_once_with(file_id=str(new_record.id))

    @pytest.mark.asyncio
    async def test_skips_thumbnail_task_for_non_image(
        self, service: FileService
    ) -> None:
        """Should not dispatch generate_thumbnail_task for a non-image file."""
        user = make_user()
        mock_file = MagicMock()
        mock_file.filename = "test.txt"
        mock_file.content_type = "text/plain"
        mock_file.read = AsyncMock(side_effect=[b"unique content", b""])

        service.file_repository.get_by_content_hash = AsyncMock(return_value=None)
        new_record = File(
            filename="test.txt",
            mimetype="text/plain",
            size=15,
            content_hash="dummy",
            bucket="default",
            object_key="uploads/abc123",
            reference_count=1,
            uploaded_by=user.id,
        )
        service.file_repository.create_or_increment = AsyncMock(
            return_value=(new_record, True)
        )

        with (
            patch("app.services.file_service.upload_file") as mock_upload,
            patch("app.tasks.storage.generate_thumbnail_task") as mock_task,
        ):
            mock_upload.return_value = ("uploads/abc123", "abc123")
            await service.upload(mock_file, user)

        mock_task.delay.assert_not_called()


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

        mock_delete.assert_called_once_with(
            object_key="uploads/abc123", bucket="default"
        )
        service.file_repository.delete.assert_awaited_once_with(file_id)

    @pytest.mark.asyncio
    async def test_keeps_the_row_while_other_references_remain(
        self, service: FileService
    ) -> None:
        """Should only decrement while other references still hold the row.

        Deduplication gives every reference to the same content within a
        tenant one shared row. Soft-deleting it on the first delete hid the
        file from every other referencer that still owned it.
        """
        user = make_user()
        file_id = UUID("00000000-0000-0000-0000-000000000003")

        record = File(
            id=file_id,
            filename="shared.txt",
            mimetype="text/plain",
            size=10,
            content_hash="abc123",
            bucket="default",
            object_key="uploads/abc123",
            thumbnail_object_key="thumbnails/abc123",
            reference_count=2,
            uploaded_by=user.id,
        )
        service.file_repository.get_owned = AsyncMock(return_value=record)
        service.file_repository.decrement_reference_count = AsyncMock(return_value=1)

        with patch("app.services.file_service.delete_object") as mock_delete:
            await service.delete(file_id, user)

        mock_delete.assert_not_called()
        service.file_repository.delete.assert_not_awaited()
