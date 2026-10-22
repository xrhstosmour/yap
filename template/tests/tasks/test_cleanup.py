"""Tests for the cleanup Celery tasks."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

# tests/core/test_celery_app.py stubs sys.modules["app.tasks.cleanup"] with a
# bare MagicMock() at collection time (a defensive placeholder for when
# celery_app is imported before the template is rendered), guarded by `if not
# already in sys.modules`. Because "core" sorts before "tasks" alphabetically,
# it can win the race and leave the fake module cached here, silently
# breaking the import below. Evict any such stub so it resolves to the real
# module. Mirrors the same fix in tests/core/test_telemetry.py.
if isinstance(sys.modules.get("app.tasks.cleanup"), MagicMock):
    del sys.modules["app.tasks.cleanup"]

from app.tasks.cleanup import generate_reports  # noqa: E402
from app.tasks.cleanup import purge_outbox  # noqa: E402


class _FakeCountResult:
    """Stand-in for the SQLAlchemy Result returned by `session.execute(select(func.count()...))`."""

    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value


@pytest.fixture
def mock_session_factory(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch celery_session_factory to yield a session with scripted count results.

    generate_reports() queries users, files, and audit_log counts in that
    order, so the three scripted results line up positionally.
    """
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        side_effect=[_FakeCountResult(3), _FakeCountResult(5), _FakeCountResult(7)]
    )

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    monkeypatch.setattr("app.database.celery_session_factory", _factory)
    return mock_session


def test_generate_reports_returns_counts(mock_session_factory: AsyncMock) -> None:
    """generate_reports() should return the queried counts, keyed by report label."""
    result = generate_reports.apply()

    assert result.successful()
    assert result.result["status"] == "completed"
    assert result.result["new_users"] == 3
    assert result.result["new_files"] == 5
    assert result.result["audit_events"] == 7


def test_generate_reports_queries_three_models(mock_session_factory: AsyncMock) -> None:
    """generate_reports() should issue exactly one count query per model."""
    generate_reports.apply()

    assert mock_session_factory.execute.await_count == 3


def test_generate_reports_defaults_missing_count_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A None scalar() result (no rows) should be reported as 0, not propagate None."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=_FakeCountResult(None))  # type: ignore[arg-type]

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    monkeypatch.setattr("app.database.celery_session_factory", _factory)

    result = generate_reports.apply()

    assert result.successful()
    assert result.result["new_users"] == 0


def test_purge_outbox_commits_each_chunk_until_one_comes_back_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large first run neither holds one long transaction nor stops early."""
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    monkeypatch.setattr("app.database.celery_session_factory", _factory)
    purge = AsyncMock(side_effect=[10_000, 10_000, 3])

    with patch("app.models.outbox.Outbox.purge_published", purge):
        result = purge_outbox.apply(kwargs={"retention_days": 7})

    assert result.result["purged_count"] == 20_003
    assert purge.await_count == 3
    assert mock_session.commit.await_count == 3
