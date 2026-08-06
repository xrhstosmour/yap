"""Tests for `SubscriptionService`'s state machine."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
from app.services.billing_service import ALLOWED_TRANSITIONS
from app.services.billing_service import IllegalSubscriptionTransitionError
from app.services.billing_service import SubscriptionNotFoundError
from app.services.billing_service import SubscriptionService

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
SUBSCRIPTION_ID = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_session: MagicMock) -> SubscriptionService:
    service = SubscriptionService(mock_session)
    service.subscription_repository = MagicMock()
    return service


def _subscription(status: SubscriptionStatus) -> Subscription:
    return Subscription(
        id=SUBSCRIPTION_ID,
        tenant_id=TENANT_ID,
        status=status,
    )


class TestAllowedTransitionsTable:
    def test_terminal_statuses_have_no_outgoing_transitions(self) -> None:
        assert ALLOWED_TRANSITIONS[SubscriptionStatus.EXPIRED] == frozenset()
        assert ALLOWED_TRANSITIONS[SubscriptionStatus.CANCELED] == frozenset()

    def test_active_is_reachable_from_every_non_terminal_status(self) -> None:
        for status in (
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.PAST_DUE,
            SubscriptionStatus.GRACE_PERIOD,
        ):
            assert SubscriptionStatus.ACTIVE in ALLOWED_TRANSITIONS[status]

    def test_every_status_has_a_table_entry(self) -> None:
        for status in SubscriptionStatus:
            assert status in ALLOWED_TRANSITIONS


class TestTransition:
    def test_legal_transition_updates_status(self, service: SubscriptionService) -> None:
        current = _subscription(SubscriptionStatus.TRIALING)
        updated = _subscription(SubscriptionStatus.ACTIVE)
        service.subscription_repository.get_for_update = AsyncMock(
            return_value=current
        )
        service.subscription_repository.update = AsyncMock(return_value=updated)

        result = asyncio.run(
            service.transition(
                SUBSCRIPTION_ID, SubscriptionStatus.ACTIVE, source="checkout.session.completed"
            )
        )

        assert result.status == SubscriptionStatus.ACTIVE
        service.subscription_repository.update.assert_awaited_once()
        call_args = service.subscription_repository.update.call_args
        assert call_args[0][0] == SUBSCRIPTION_ID
        assert call_args[0][1]["status"] == SubscriptionStatus.ACTIVE

    def test_illegal_transition_raises(self, service: SubscriptionService) -> None:
        current = _subscription(SubscriptionStatus.EXPIRED)
        service.subscription_repository.get_for_update = AsyncMock(
            return_value=current
        )

        with pytest.raises(IllegalSubscriptionTransitionError):
            asyncio.run(
                service.transition(
                    SUBSCRIPTION_ID, SubscriptionStatus.ACTIVE, source="test"
                )
            )
        service.subscription_repository.update.assert_not_called()  # type: ignore[attr-defined]

    def test_active_to_expired_is_illegal(self, service: SubscriptionService) -> None:
        """`active -> expired` skips the grace-period sweep step and is not allowed."""
        current = _subscription(SubscriptionStatus.ACTIVE)
        service.subscription_repository.get_for_update = AsyncMock(
            return_value=current
        )

        with pytest.raises(IllegalSubscriptionTransitionError):
            asyncio.run(
                service.transition(
                    SUBSCRIPTION_ID, SubscriptionStatus.EXPIRED, source="test"
                )
            )

    def test_same_status_transition_is_idempotent_no_op(
        self, service: SubscriptionService
    ) -> None:
        current = _subscription(SubscriptionStatus.ACTIVE)
        service.subscription_repository.get_for_update = AsyncMock(
            return_value=current
        )
        service.subscription_repository.update = AsyncMock()

        result = asyncio.run(
            service.transition(
                SUBSCRIPTION_ID, SubscriptionStatus.ACTIVE, source="replayed_webhook"
            )
        )

        assert result is current
        service.subscription_repository.update.assert_not_called()

    def test_missing_subscription_raises(self, service: SubscriptionService) -> None:
        service.subscription_repository.get_for_update = AsyncMock(return_value=None)

        with pytest.raises(SubscriptionNotFoundError):
            asyncio.run(
                service.transition(
                    SUBSCRIPTION_ID, SubscriptionStatus.ACTIVE, source="test"
                )
            )

    def test_cancel_sets_canceled_at(self, service: SubscriptionService) -> None:
        current = _subscription(SubscriptionStatus.ACTIVE)
        updated = _subscription(SubscriptionStatus.CANCELED)
        service.subscription_repository.get_for_update = AsyncMock(
            return_value=current
        )
        service.subscription_repository.update = AsyncMock(return_value=updated)

        asyncio.run(
            service.transition(
                SUBSCRIPTION_ID, SubscriptionStatus.CANCELED, source="portal"
            )
        )

        call_args = service.subscription_repository.update.call_args
        assert "canceled_at" in call_args[0][1]

    def test_extra_fields_applied_on_transition(
        self, service: SubscriptionService
    ) -> None:
        current = _subscription(SubscriptionStatus.TRIALING)
        updated = _subscription(SubscriptionStatus.ACTIVE)
        service.subscription_repository.get_for_update = AsyncMock(
            return_value=current
        )
        service.subscription_repository.update = AsyncMock(return_value=updated)

        asyncio.run(
            service.transition(
                SUBSCRIPTION_ID,
                SubscriptionStatus.ACTIVE,
                source="checkout.session.completed",
                extra_fields={"stripe_subscription_id": "sub_123"},
            )
        )

        call_args = service.subscription_repository.update.call_args
        assert call_args[0][1]["stripe_subscription_id"] == "sub_123"


class TestStartTrial:
    def test_start_trial_creates_trialing_subscription(
        self, service: SubscriptionService
    ) -> None:
        created = _subscription(SubscriptionStatus.TRIALING)
        service.subscription_repository.create = AsyncMock(return_value=created)

        result = asyncio.run(service.start_trial(TENANT_ID))

        assert result.status == SubscriptionStatus.TRIALING
        service.subscription_repository.create.assert_awaited_once()
        call_kwargs = service.subscription_repository.create.call_args[0][0]
        assert call_kwargs["tenant_id"] == TENANT_ID
        assert call_kwargs["status"] == SubscriptionStatus.TRIALING
        assert call_kwargs["trial_ends_at"] is not None
        assert call_kwargs["grace_period_ends_at"] > call_kwargs["trial_ends_at"]
