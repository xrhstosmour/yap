"""Stripe webhook endpoint.

`POST /api/v1/webhooks/stripe` — public, no `CurrentUser`/`AnyAuth`
dependency. Secured only by Stripe signature verification. Must never
be gated by `ActiveBilling` (`app.core.billing_access`) or any auth
dependency — Stripe itself is the caller.
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import status

from app.core.logging import get_logger
from app.core.providers.stripe_provider import SignatureVerificationError
from app.core.providers.stripe_provider import StripeProvider
from app.dependencies import SessionDependency
from app.repositories.payment_event_repository import PaymentEventRepository
from app.services.billing_service import BillingService
from app.services.billing_service import IllegalSubscriptionTransitionError

router = APIRouter(prefix="/webhooks", tags=["Billing Webhooks"])
logger = get_logger("api.billing_webhooks")

# Minimum event types handled. Unrecognized types are marked processed
# and no-op'd — Stripe sends many event types this baseline doesn't act
# on, and that's expected, not an error condition.
_HANDLERS: dict[str, Callable[[BillingService, dict[str, Any]], Awaitable[None]]] = {
    "checkout.session.completed": BillingService.handle_checkout_completed,
    "customer.subscription.updated": BillingService.handle_subscription_updated,
    "customer.subscription.deleted": BillingService.handle_subscription_deleted,
    "invoice.paid": BillingService.handle_invoice_paid,
    "invoice.payment_failed": BillingService.handle_invoice_payment_failed,
    "charge.refunded": BillingService.handle_charge_refunded,
    "payment_method.attached": BillingService.handle_payment_method_attached,
    "payment_method.detached": BillingService.handle_payment_method_detached,
    "customer.updated": BillingService.handle_customer_updated,
}
# `customer.subscription.trial_will_end` deliberately not handled: our
# trial predates any Stripe subscription, so it never fires for the
# trial itself. The "trial ending soon" prompt is driven by the sweep
# task checking `trial_ends_at` proximity, not a webhook.


@router.post(
    "/stripe",
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook",
    description=(
        "Receives and processes Stripe webhook events. Public endpoint, "
        "secured only by Stripe signature verification."
    ),
)
async def stripe_webhook(
    request: Request, session: SessionDependency
) -> dict[str, str]:
    """Verify, deduplicate, and dispatch a Stripe webhook event.

    1. Read the raw request body (must be unparsed — the signature
       covers the exact bytes) and the `Stripe-Signature` header.
    2. Verify the signature; failure -> `400`.
    3. Dedup-insert-first into `payment_events`; a replay returns `200`
       immediately without reprocessing (Stripe needs a `2xx` response
       or it retries indefinitely).
    4. Dispatch by `event.type`, marking the `PaymentEvent`
       `processed`/`failed` in the same transaction as the handler's
       writes.
    """
    payload = await request.body()
    signature_header = request.headers.get("stripe-signature")
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    provider = StripeProvider()
    try:
        event = provider.verify_webhook_signature(
            payload=payload, signature_header=signature_header
        )
    except SignatureVerificationError:
        logger.warning("stripe_webhook_signature_invalid")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature"
        ) from None

    payment_event_repository = PaymentEventRepository(session)
    stored_event = await payment_event_repository.insert_if_new(
        provider="stripe",
        provider_event_id=event.id,
        event_type=event.type,
        payload=event.data,
    )
    await session.commit()

    if stored_event is None:
        logger.info("stripe_webhook_replay_ignored", event_id=event.id)
        return {"status": "duplicate"}

    billing_service = BillingService(session, provider)
    handler = _HANDLERS.get(event.type)

    try:
        if handler is not None:
            await handler(billing_service, event.data)
        else:
            logger.debug("stripe_webhook_unhandled_event_type", event_type=event.type)
        await payment_event_repository.mark_processed(stored_event.id)
        await session.commit()
    except IllegalSubscriptionTransitionError:
        # Not a transient failure: this event's transition genuinely
        # doesn't apply to the subscription's current state (e.g. a
        # dunning retry arriving after the grace period already ended).
        # Retrying would fail identically forever, so ack rather than
        # 500 and let Stripe retry into a permanent loop.
        logger.warning(
            "stripe_webhook_illegal_transition_ignored",
            event_type=event.type,
            event_id=event.id,
        )
        await session.rollback()
        await payment_event_repository.mark_processed(stored_event.id)
        await session.commit()
        return {"status": "ignored"}
    except Exception:
        logger.exception(
            "stripe_webhook_processing_failed",
            event_type=event.type,
            event_id=event.id,
        )
        await session.rollback()
        await payment_event_repository.mark_failed(stored_event.id)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        ) from None

    return {"status": "processed"}
