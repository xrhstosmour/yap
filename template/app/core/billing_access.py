"""Billing access-gating dependency.

**Applied opt-in per-router, not as global middleware.** Billing-
management routes (checkout, portal, cancel) must stay reachable while
a subscription is expired — a lapsed tenant could never pay to
reactivate otherwise. Auth routes, health checks, and the webhook
endpoint must never be gated by this; superuser/admin routes are
tenant-agnostic.

Apply via `APIRouter(dependencies=[Depends(get_active_billing_status)])`
on the routers that represent the actual generated app's product
surface — one line per router, so the gate is understood as opt-in by
design rather than something callers have to remember per-endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import status

from app.core.settings import settings
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.models.subscription import SubscriptionStatus
from app.repositories.subscription_repository import SubscriptionRepository


@dataclass
class BillingStatus:
    """The caller's tenant billing status, as resolved by the gate.

    Attributes:
        subscription_status: The tenant's current subscription status,
            or `None` if the tenant has no subscription row at all
        grace_period_active: Whether the tenant is currently inside its
            grace period.
        grace_period_mode: `settings.BILLING_GRACE_PERIOD_MODE`, read
            once here rather than scattered across route handlers. A
            route opts into read-only handling itself when
            `grace_period_active` is `True` and this is `"read_only"`
            — the dependency does not enforce it generically, since
            different routes have different notions of "read".
    """

    subscription_status: SubscriptionStatus | None
    grace_period_active: bool
    grace_period_mode: str = settings.BILLING_GRACE_PERIOD_MODE


async def get_active_billing_status(
    current_user: CurrentUser, session: SessionDependency
) -> BillingStatus:
    """Enforce the hard access cutoff once a tenant's subscription has expired.

    Trusted from the JWT `tenant_id` embedded in `current_user` — no
    cross-tenant risk. Raises `402 Payment Required` once the
    subscription is `expired`; otherwise passes through.
    """
    if current_user.tenant_id is None:
        # No tenant context: nothing to gate against.
        return BillingStatus(subscription_status=None, grace_period_active=False)

    subscription_repository = SubscriptionRepository(session)
    subscription = await subscription_repository.get_most_recent_for_tenant(
        current_user.tenant_id
    )

    if subscription is None:
        return BillingStatus(subscription_status=None, grace_period_active=False)

    if subscription.status == SubscriptionStatus.EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Subscription has expired. Please renew to continue.",
        )

    return BillingStatus(
        subscription_status=subscription.status,
        grace_period_active=subscription.status == SubscriptionStatus.GRACE_PERIOD,
    )


ActiveBilling = Annotated[BillingStatus, Depends(get_active_billing_status)]
