"""Tests for the generate_thumbnail_task() Celery task."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

import pytest

# See tests/tasks/test_cleanup.py for why this stub eviction is needed.
if isinstance(sys.modules.get("app.tasks.storage"), MagicMock):
    del sys.modules["app.tasks.storage"]

from app.tasks.storage import generate_thumbnail_task  # noqa: E402


def _make_file_record(**overrides: object) -> MagicMock:
    record = MagicMock()
    record.id = uuid4()
    record.object_key = "uploads/abc123"
    record.bucket = "default"
    record.mimetype = "image/png"
    record.content_hash = "abc123"
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def mock_session_factory(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    monkeypatch.setattr("app.database.async_session_factory", _factory)
    return mock_session


class TestGenerateThumbnailTask:
    """Tests for generate_thumbnail_task()."""

    def test_generates_and_stores_thumbnail(
        self, mock_session_factory: AsyncMock
    ) -> None:
        """Should download the original, build a thumbnail, upload it, and
        update the File row's thumbnail fields."""
        record = _make_file_record()
        mock_repository = MagicMock()
        mock_repository.get = AsyncMock(return_value=record)
        mock_repository.update = AsyncMock()

        with (
            patch(
                "app.repositories.file_repository.FileRepository",
                return_value=mock_repository,
            ),
            patch(
                "app.core.storage.download_object",
                new=AsyncMock(return_value=b"original-bytes"),
            ),
            patch(
                "app.core.storage._build_thumbnail",
                return_value=(1920, 1080, b"thumb-bytes"),
            ),
            patch(
                "app.core.storage.upload_object", new=AsyncMock()
            ) as mock_upload_object,
        ):
            result = generate_thumbnail_task.apply(kwargs={"file_id": str(record.id)})

        assert result.successful()
        assert result.result["status"] == "completed"

        mock_upload_object.assert_awaited_once_with(
            f"thumbnails/{record.content_hash}",
            b"thumb-bytes",
            record.mimetype,
            bucket=record.bucket,
        )
        mock_repository.update.assert_awaited_once_with(
            record.id,
            {
                "thumbnail_object_key": f"thumbnails/{record.content_hash}",
                "image_width": 1920,
                "image_height": 1080,
            },
        )
        mock_session_factory.commit.assert_awaited_once()

    def test_skips_when_file_not_found(self, mock_session_factory: AsyncMock) -> None:
        """Should skip cleanly when the File row no longer exists."""
        mock_repository = MagicMock()
        mock_repository.get = AsyncMock(return_value=None)

        with patch(
            "app.repositories.file_repository.FileRepository",
            return_value=mock_repository,
        ):
            result = generate_thumbnail_task.apply(kwargs={"file_id": str(uuid4())})

        assert result.successful()
        assert result.result == {"status": "skipped", "reason": "file_not_found"}

    def test_retries_on_failure(self, mock_session_factory: AsyncMock) -> None:
        """Should retry when downloading or building the thumbnail fails."""
        record = _make_file_record()
        mock_repository = MagicMock()
        mock_repository.get = AsyncMock(return_value=record)

        with (
            patch(
                "app.repositories.file_repository.FileRepository",
                return_value=mock_repository,
            ),
            patch(
                "app.core.storage.download_object",
                new=AsyncMock(side_effect=OSError("storage unavailable")),
            ),
        ):
            result = generate_thumbnail_task.apply(kwargs={"file_id": str(record.id)})

        assert result.failed()
