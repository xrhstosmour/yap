"""Celery beat task: subscription lifecycle sweep.

Advances subscriptions past their trial/grace-period deadlines and
reclaims coupon slots from abandoned checkouts. Registered in
`celery_app.py.template`'s `beat_schedule`, on an interval configurable
via `settings.BILLING_SWEEP_INTERVAL_MINUTES`.
"""

from __future__ import annotations

from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("tasks.billing")

# How long an abandoned checkout's `CouponRedemption` (no attached
# subscription) is kept before its coupon slot is reclaimed. Not user
# configurable — 24 hours comfortably exceeds any realistic Stripe
# Checkout session lifetime (Stripe's own default expiry is 24 hours).
_ABANDONED_REDEMPTION_HOURS = 24

# Redis lock TTL is fixed by `app.core.idempotency.LOCK_TTL_SECONDS`
# (60s) — reused here as defense-in-depth against celery-redbeat's
# at-least-once scheduling semantics, not as the primary correctness
# mechanism (that's `FOR UPDATE SKIP LOCKED` in the sweep query itself).
_SWEEP_LOCK_KEY = "billing_sweep"


@celery_app.task(bind=True, name="app.tasks.billing.sweep_billing_lifecycle")
def sweep_billing_lifecycle(self) -> dict:
    """Advance subscriptions past trial/grace-period deadlines; reclaim coupons.

    Queries `trialing`/`past_due`/`grace_period` subscriptions whose
    `trial_ends_at`/`grace_period_ends_at` has passed, using
    `FOR UPDATE SKIP LOCKED` — this is the deliberate, explicit way
    this task bypasses `BaseRepository._apply_tenant_filter`'s
    no-tenant-context behavior: it must see all tenants, on purpose.
    Each transitioned row enters `tenant_context(tenant_id)` before
    writing, so `AuditLog.tenant_id` is correct.

    Returns:
        Result dictionary with counts of subscriptions transitioned and
        stale coupon redemptions reclaimed.
    """
    logger.info("billing_sweep_started", task_id=self.request.id)

    try:
        import asyncio

        result = asyncio.run(_run())

        logger.info("billing_sweep_completed", task_id=self.request.id, **result)

        return {"status": "completed", "task_id": self.request.id, **result}

    except Exception as e:
        logger.error("billing_sweep_failed", task_id=self.request.id, error=str(e))
        raise


async def _run() -> dict[str, int]:
    from app.core.idempotency import idempotency_service

    locked = await idempotency_service.try_lock(_SWEEP_LOCK_KEY)
    if not locked:
        logger.info("billing_sweep_skipped_already_running")
        return {"transitioned": 0, "reclaimed_coupons": 0}

    try:
        return await _sweep()
    finally:
        await idempotency_service.release_lock(_SWEEP_LOCK_KEY)


async def _sweep() -> dict[str, int]:
    from datetime import UTC
    from datetime import datetime
    from datetime import timedelta

    from app.core.tenant import tenant_context
    from app.database import async_session_factory
    from app.models.subscription import SubscriptionStatus
    from app.repositories.audit_repository import AuditLogRepository
    from app.repositories.coupon_repository import CouponRedemptionRepository
    from app.repositories.coupon_repository import CouponRepository
    from app.repositories.subscription_repository import SubscriptionRepository
    from app.services.billing_service import SubscriptionService

    now = datetime.now(UTC)
    # `DateTime` columns are stored without a timezone (see the
    # migrations), so values read back from the DB are naive. Comparing
    # them against an aware `now` raises `TypeError` — use this naive
    # counterpart for any Python-side (as opposed to SQL-side) comparison.
    now_naive = now.replace(tzinfo=None)
    transitioned = 0
    reclaimed_coupons = 0

    async with async_session_factory() as session:
        subscription_repository = SubscriptionRepository(session)
        audit_repository = AuditLogRepository(session)
        subscription_service = SubscriptionService(session, audit_repository)

        due = await subscription_repository.list_due_for_sweep(now)
        for subscription in due:
            if subscription.status == SubscriptionStatus.GRACE_PERIOD:
                target = SubscriptionStatus.EXPIRED
            else:
                # `trialing`/`past_due`: if the grace period has *also*
                # already elapsed (e.g. the sweep missed a run), skip
                # straight to `expired` rather than parking in
                # `grace_period` for one tick only to expire on the next.
                grace_ends = subscription.grace_period_ends_at
                target = (
                    SubscriptionStatus.EXPIRED
                    if grace_ends is not None and grace_ends <= now_naive
                    else SubscriptionStatus.GRACE_PERIOD
                )

            with tenant_context(subscription.tenant_id):
                # `grace_period_ends_at` was already set when the
                # subscription entered `trialing`/`past_due` — nothing
                # to recompute here for either target status.
                await subscription_service.transition(
                    subscription.id,
                    target,
                    source="sweep",
                )

                # Downstream notification (email, etc.) is fire-and-forget
                # via the outbox — the sweep task itself makes no
                # synchronous calls.
                from app.models.outbox import Outbox

                outbox = Outbox(session)
                await outbox.publish(
                    event_type="billing.subscription_status_changed",
                    payload={
                        "subscription_id": str(subscription.id),
                        "status": target.value,
                    },
                    tenant_id=subscription.tenant_id,
                )

            transitioned += 1

        # Reclaim coupon slots from abandoned checkouts.
        coupon_redemption_repository = CouponRedemptionRepository(session)
        coupon_repository = CouponRepository(session)
        abandoned_cutoff = now - timedelta(hours=_ABANDONED_REDEMPTION_HOURS)
        abandoned = await coupon_redemption_repository.list_abandoned(abandoned_cutoff)
        for redemption in abandoned:
            await coupon_repository.decrement_redemption_count(redemption.coupon_id)
            await coupon_redemption_repository.delete_hard(redemption.id)
            reclaimed_coupons += 1

        await session.commit()

    return {"transitioned": transitioned, "reclaimed_coupons": reclaimed_coupons}
