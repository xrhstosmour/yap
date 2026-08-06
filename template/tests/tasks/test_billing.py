"""Unit tests for the billing lifecycle sweep task."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest


class TestSweepBillingLifecycleLock:
    def test_skips_when_lock_not_acquired(self) -> None:
        """A concurrent sweep run (redbeat at-least-once) is a no-op, not an error."""
        from app.tasks.billing import _run

        with patch("app.core.idempotency.idempotency_service") as mock_service:
            mock_service.try_lock = AsyncMock(return_value=False)
            mock_service.release_lock = AsyncMock()

            result = asyncio.run(_run())

        assert result == {"transitioned": 0, "reclaimed_coupons": 0}
        mock_service.release_lock.assert_not_awaited()

    def test_releases_lock_after_sweep(self) -> None:
        from app.tasks.billing import _run

        with (
            patch("app.core.idempotency.idempotency_service") as mock_service,
            patch(
                "app.tasks.billing._sweep",
                new=AsyncMock(
                    return_value={
                        "transitioned": 3,
                        "reclaimed_coupons": 1,
                    }
                ),
            ) as mock_sweep,
        ):
            mock_service.try_lock = AsyncMock(return_value=True)
            mock_service.release_lock = AsyncMock()

            result = asyncio.run(_run())

        assert result == {"transitioned": 3, "reclaimed_coupons": 1}
        mock_sweep.assert_awaited_once()
        mock_service.release_lock.assert_awaited_once()

    def test_releases_lock_even_if_sweep_raises(self) -> None:
        from app.tasks.billing import _run

        with (
            patch("app.core.idempotency.idempotency_service") as mock_service,
            patch(
                "app.tasks.billing._sweep",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            mock_service.try_lock = AsyncMock(return_value=True)
            mock_service.release_lock = AsyncMock()

            with pytest.raises(RuntimeError):
                asyncio.run(_run())

        mock_service.release_lock.assert_awaited_once()
