"""Integration tests for the Stripe webhook endpoint."""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
import stripe
from httpx import ASGITransport
from httpx import AsyncClient

from app.core.settings import settings
from app.main import app

WEBHOOK_SECRET = "whsec_test_secret_for_webhook_tests"


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    timestamp = int(time.time())
    signed_string = f"{timestamp}.{payload.decode()}"
    signature = stripe.WebhookSignature._compute_signature(signed_string, secret)
    return f"t={timestamp},v1={signature}"


def _event_payload(event_id: str, event_type: str = "customer.updated") -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {"object": {"id": "cus_test", "object": "customer"}},
        }
    ).encode()


@pytest.mark.anyio
async def test_missing_signature_header_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/webhooks/stripe", content=_event_payload("evt_1")
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_invalid_signature_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=_event_payload("evt_2"),
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 400


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_unrecognized_event_type_is_processed_as_no_op(
    client: AsyncClient,
) -> None:
    payload = _event_payload("evt_unrecognized", event_type="some.unhandled.event")
    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_replayed_event_returns_200_without_reprocessing(
    client: AsyncClient,
) -> None:
    payload = _event_payload("evt_replay_test", event_type="customer.updated")
    headers = {"stripe-signature": _sign(payload)}

    first = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers=headers
    )
    assert first.status_code == 200
    assert first.json()["status"] == "processed"

    replay_headers = {"stripe-signature": _sign(payload)}
    second = await client.post(
        "/api/v1/webhooks/stripe", content=payload, headers=replay_headers
    )
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_stripe_retry_of_a_failed_event_is_reprocessed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previously-*failed* delivery must be reprocessed on retry, not
    swallowed by the dedup check.

    Regression test: `PaymentEventRepository.insert_if_new` used a
    plain `ON CONFLICT ... DO NOTHING`, so once an event's first
    delivery failed (e.g. a transient DB error), every one of Stripe's
    automatic retries for that same event ID would hit the conflict
    and be treated as an already-seen duplicate — silently dropped
    forever, despite the endpoint returning 500 specifically to ask
    Stripe to retry.
    """
    from app.api.v1 import billing_webhooks
    from app.services.billing_service import BillingService

    call_count = {"n": 0}
    original = BillingService.handle_customer_updated

    async def _fail_once_then_succeed(service, data):  # noqa: ANN001
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient failure")
        await original(service, data)

    # `_HANDLERS` captures a direct function reference at module import
    # time, not a dynamic `BillingService.handle_customer_updated`
    # lookup — patching the class attribute wouldn't reach it, so the
    # dict entry itself is what needs patching here.
    monkeypatch.setitem(
        billing_webhooks._HANDLERS,  # noqa: SLF001
        "customer.updated",
        _fail_once_then_succeed,
    )

    payload = _event_payload("evt_retry_test", event_type="customer.updated")

    first = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload)},
    )
    assert first.status_code == 500

    retry = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload)},
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "processed"
    assert call_count["n"] == 2


@pytest.mark.anyio
@pytest.mark.usefixtures("override_get_async_session")
async def test_illegal_transition_is_acked_not_retried(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An event whose transition doesn't apply to the subscription's
    current state (e.g. a dunning retry arriving after the grace period
    already lapsed) must be acked, not 500'd.

    Regression test: this used to bubble up as an unhandled 500, and
    since the transition is illegal regardless of how many times Stripe
    retries, every retry failed identically forever.
    """
    from app.api.v1 import billing_webhooks
    from app.services.billing_service import IllegalSubscriptionTransitionError

    async def _raise_illegal_transition(service, data):  # noqa: ANN001
        raise IllegalSubscriptionTransitionError("cannot transition")

    monkeypatch.setitem(
        billing_webhooks._HANDLERS,  # noqa: SLF001
        "customer.updated",
        _raise_illegal_transition,
    )

    payload = _event_payload("evt_illegal_transition", event_type="customer.updated")

    response = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

    # A Stripe retry of the same event ID is now a dedup hit, not another
    # attempt at the (still-illegal) transition.
    retry = await client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload)},
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "duplicate"
