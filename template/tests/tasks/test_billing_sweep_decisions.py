"""Tests for the sweep task's transition decision logic.

Follows this codebase's established pattern for testing `async_session_
factory()`-based Celery tasks (see `tests/tasks/test_cleanup.py`,
`tests/tasks/test_storage.py`): patch `app.database.async_session_
factory` with a fake, and patch the repository/service methods the task
calls rather than exercising real SQL. `tests/tasks/test_billing.py`
covers the Redis-lock wrapper (`_run`) with `_sweep` itself mocked out;
this covers the decision logic `_sweep` contains.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

# See tests/tasks/test_cleanup.py for why this stub eviction is needed.
if isinstance(sys.modules.get("app.tasks.billing"), MagicMock):
    del sys.modules["app.tasks.billing"]

from app.models.subscription import SubscriptionStatus  # noqa: E402
from app.tasks.billing import _sweep  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _fake_subscription(status: SubscriptionStatus, **overrides: Any) -> MagicMock:
    subscription = MagicMock()
    subscription.id = uuid4()
    subscription.tenant_id = uuid4()
    subscription.status = status
    subscription.trial_ends_at = None
    subscription.grace_period_ends_at = None
    for key, value in overrides.items():
        setattr(subscription, key, value)
    return subscription


@pytest.fixture
def mock_session_factory(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    monkeypatch.setattr("app.database.async_session_factory", _factory)
    return mock_session


@pytest.fixture(autouse=True)
def _no_abandoned_coupons(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repositories.coupon_repository import CouponRedemptionRepository

    monkeypatch.setattr(
        CouponRedemptionRepository, "list_abandoned", AsyncMock(return_value=[])
    )


class TestSweepTransitionDecisions:
    async def test_trialing_past_trial_end_moves_to_grace_period(
        self, mock_session_factory: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repositories.subscription_repository import SubscriptionRepository
        from app.services.billing_service import SubscriptionService

        subscription = _fake_subscription(
            SubscriptionStatus.TRIALING,
            trial_ends_at=(datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None),
            grace_period_ends_at=(datetime.now(UTC) + timedelta(days=5)).replace(
                tzinfo=None
            ),
        )
        monkeypatch.setattr(
            SubscriptionRepository,
            "list_due_for_sweep",
            AsyncMock(return_value=[subscription]),
        )
        transition_mock = AsyncMock()
        monkeypatch.setattr(SubscriptionService, "transition", transition_mock)

        result = await _sweep()

        assert result == {"transitioned": 1, "reclaimed_coupons": 0}
        transition_mock.assert_awaited_once_with(
            subscription.id, SubscriptionStatus.GRACE_PERIOD, source="sweep"
        )

    async def test_grace_period_past_deadline_moves_to_expired(
        self, mock_session_factory: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repositories.subscription_repository import SubscriptionRepository
        from app.services.billing_service import SubscriptionService

        subscription = _fake_subscription(
            SubscriptionStatus.GRACE_PERIOD,
            grace_period_ends_at=(datetime.now(UTC) - timedelta(hours=1)).replace(
                tzinfo=None
            ),
        )
        monkeypatch.setattr(
            SubscriptionRepository,
            "list_due_for_sweep",
            AsyncMock(return_value=[subscription]),
        )
        transition_mock = AsyncMock()
        monkeypatch.setattr(SubscriptionService, "transition", transition_mock)

        result = await _sweep()

        assert result == {"transitioned": 1, "reclaimed_coupons": 0}
        transition_mock.assert_awaited_once_with(
            subscription.id, SubscriptionStatus.EXPIRED, source="sweep"
        )

    async def test_trial_and_grace_both_elapsed_skips_straight_to_expired(
        self, mock_session_factory: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sweep run missed for a while: trial AND grace period both
        elapsed by the time the sweep finally runs — goes straight to
        `expired` rather than parking in `grace_period` for one tick."""
        from app.repositories.subscription_repository import SubscriptionRepository
        from app.services.billing_service import SubscriptionService

        subscription = _fake_subscription(
            SubscriptionStatus.TRIALING,
            trial_ends_at=(datetime.now(UTC) - timedelta(days=10)).replace(tzinfo=None),
            grace_period_ends_at=(datetime.now(UTC) - timedelta(days=1)).replace(
                tzinfo=None
            ),
        )
        monkeypatch.setattr(
            SubscriptionRepository,
            "list_due_for_sweep",
            AsyncMock(return_value=[subscription]),
        )
        transition_mock = AsyncMock()
        monkeypatch.setattr(SubscriptionService, "transition", transition_mock)

        await _sweep()

        transition_mock.assert_awaited_once_with(
            subscription.id, SubscriptionStatus.EXPIRED, source="sweep"
        )

    async def test_no_due_subscriptions_is_a_no_op(
        self, mock_session_factory: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repositories.subscription_repository import SubscriptionRepository

        monkeypatch.setattr(
            SubscriptionRepository, "list_due_for_sweep", AsyncMock(return_value=[])
        )

        result = await _sweep()

        assert result == {"transitioned": 0, "reclaimed_coupons": 0}

    async def test_abandoned_coupon_redemptions_are_reclaimed(
        self, mock_session_factory: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repositories.coupon_repository import CouponRedemptionRepository
        from app.repositories.coupon_repository import CouponRepository
        from app.repositories.subscription_repository import SubscriptionRepository

        monkeypatch.setattr(
            SubscriptionRepository, "list_due_for_sweep", AsyncMock(return_value=[])
        )

        abandoned = MagicMock()
        abandoned.id = uuid4()
        abandoned.coupon_id = uuid4()
        monkeypatch.setattr(
            CouponRedemptionRepository,
            "list_abandoned",
            AsyncMock(return_value=[abandoned]),
        )
        decrement_mock = AsyncMock()
        delete_mock = AsyncMock()
        monkeypatch.setattr(
            CouponRepository, "decrement_redemption_count", decrement_mock
        )
        monkeypatch.setattr(CouponRedemptionRepository, "delete_hard", delete_mock)

        result = await _sweep()

        assert result == {"transitioned": 0, "reclaimed_coupons": 1}
        decrement_mock.assert_awaited_once_with(abandoned.coupon_id)
        delete_mock.assert_awaited_once_with(abandoned.id)
