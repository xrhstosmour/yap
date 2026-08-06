"""Tests for `StripeProvider.verify_webhook_signature`.

No network calls: `stripe.StripeClient.construct_event` is a pure
signature-verification routine (HMAC over the raw payload), not an API
call, so it is exercised directly against real Stripe test fixtures
rather than mocked.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
import stripe

from app.core.payment_provider import PaymentProviderError
from app.core.providers.stripe_provider import StripeProvider

WEBHOOK_SECRET = "whsec_test_secret"


def _signed_payload(payload: bytes, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Build a payload + `Stripe-Signature` header the way Stripe would."""
    timestamp = int(time.time())
    signed_string = f"{timestamp}.{payload.decode()}"
    header = stripe.WebhookSignature._compute_signature(signed_string, secret)
    signature_header = f"t={timestamp},v1={header}"
    return payload, signature_header


@pytest.fixture
def provider() -> StripeProvider:
    return StripeProvider(api_key="sk_test_x", webhook_secret=WEBHOOK_SECRET)


class TestVerifyWebhookSignature:
    def test_valid_signature_returns_event(self, provider: StripeProvider) -> None:
        payload = json.dumps(
            {
                "id": "evt_test123",
                "object": "event",
                "type": "checkout.session.completed",
                "data": {"object": {"id": "cs_test123"}},
            }
        ).encode()
        body, signature_header = _signed_payload(payload)

        event = provider.verify_webhook_signature(
            payload=body, signature_header=signature_header
        )

        assert event.id == "evt_test123"
        assert event.type == "checkout.session.completed"
        assert event.data["id"] == "cs_test123"

    def test_invalid_signature_raises(self, provider: StripeProvider) -> None:
        payload = json.dumps({"id": "evt_test123", "type": "invoice.paid"}).encode()

        with pytest.raises(stripe.SignatureVerificationError):
            provider.verify_webhook_signature(
                payload=payload, signature_header="t=1,v1=deadbeef"
            )

    def test_tampered_payload_raises(self, provider: StripeProvider) -> None:
        original = json.dumps({"id": "evt_test123", "type": "invoice.paid"}).encode()
        _, signature_header = _signed_payload(original)

        tampered = json.dumps({"id": "evt_tampered", "type": "invoice.paid"}).encode()

        with pytest.raises(stripe.SignatureVerificationError):
            provider.verify_webhook_signature(
                payload=tampered, signature_header=signature_header
            )


def _fake_session(**overrides) -> SimpleNamespace:
    defaults = {
        "id": "cs_test123",
        "url": "https://checkout.stripe.com/cs_test123",
        "customer": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCreateCheckoutSession:
    """`create_checkout_session` must always enable Stripe Tax.

    Regression tests for a bug where `automatic_tax`/`tax_id_collection`
    /`billing_address_collection` were never set on the Checkout Session
    params — Stripe Tax silently never ran, so every VAT/OSS/reverse-
    charge field this codebase mirrors onto `Invoice` stayed empty in
    production despite the extraction logic being fully implemented.
    No network calls: `_client` is replaced with a mock.
    """

    @pytest.fixture
    def provider(self) -> StripeProvider:
        instance = StripeProvider(api_key="sk_test_x", webhook_secret=WEBHOOK_SECRET)
        instance._client = MagicMock()
        instance._client.v1.checkout.sessions.create_async = AsyncMock(
            return_value=_fake_session()
        )
        return instance

    async def test_enables_automatic_tax_and_tax_id_collection(
        self, provider: StripeProvider
    ) -> None:
        await provider.create_checkout_session(
            stripe_customer_id=None,
            customer_email="new-customer@example.com",
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        create_async = provider._client.v1.checkout.sessions.create_async
        create_async.assert_awaited_once()
        params = create_async.await_args[0][0]
        assert params["automatic_tax"] == {"enabled": True}
        assert params["tax_id_collection"] == {"enabled": True}
        assert params["billing_address_collection"] == "required"

    async def test_new_customer_uses_customer_email_not_customer_update(
        self, provider: StripeProvider
    ) -> None:
        await provider.create_checkout_session(
            stripe_customer_id=None,
            customer_email="new-customer@example.com",
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        params = provider._client.v1.checkout.sessions.create_async.await_args[0][0]
        assert params["customer_email"] == "new-customer@example.com"
        assert "customer" not in params
        assert "customer_update" not in params

    async def test_existing_customer_gets_customer_update(
        self, provider: StripeProvider
    ) -> None:
        await provider.create_checkout_session(
            stripe_customer_id="cus_existing123",
            customer_email="ignored@example.com",
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        params = provider._client.v1.checkout.sessions.create_async.await_args[0][0]
        assert params["customer"] == "cus_existing123"
        assert params["customer_update"] == {"address": "auto", "name": "auto"}
        assert "customer_email" not in params

    async def test_trial_period_days_sets_subscription_data(
        self, provider: StripeProvider
    ) -> None:
        await provider.create_checkout_session(
            stripe_customer_id=None,
            customer_email="new-customer@example.com",
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            trial_period_days=14,
        )

        params = provider._client.v1.checkout.sessions.create_async.await_args[0][0]
        assert params["subscription_data"] == {"trial_period_days": 14}

    async def test_idempotency_key_is_forwarded_as_request_options(
        self, provider: StripeProvider
    ) -> None:
        """A network retry or double-submit for the same attempt must
        reuse Stripe's own idempotency handling, not create a second
        Checkout Session.
        """
        await provider.create_checkout_session(
            stripe_customer_id=None,
            customer_email="new-customer@example.com",
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            idempotency_key="checkout-session-sub_123",
        )

        create_async = provider._client.v1.checkout.sessions.create_async
        options = create_async.await_args.kwargs["options"]
        assert options == {"idempotency_key": "checkout-session-sub_123"}

    async def test_stripe_error_is_translated_to_payment_provider_error(
        self, provider: StripeProvider
    ) -> None:
        """`StripeProvider` callers must never need to import the `stripe`
        SDK to handle a provider failure.
        """
        provider._client.v1.checkout.sessions.create_async = AsyncMock(
            side_effect=stripe.APIConnectionError("network blip")
        )

        with pytest.raises(PaymentProviderError):
            await provider.create_checkout_session(
                stripe_customer_id=None,
                customer_email="new-customer@example.com",
                price_id="price_test123",
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )


class TestStripeClientConstruction:
    def test_pins_api_version_and_configures_retries(self) -> None:
        """Pinning `stripe.api_version` explicitly (rather than trusting
        the Stripe account's dashboard default) means a future Stripe-
        side default-version bump can't silently change response shapes
        under us.
        """
        provider = StripeProvider(api_key="sk_test_x", webhook_secret=WEBHOOK_SECRET)

        assert provider._client._requestor.stripe_version == stripe.api_version
        assert provider._client._requestor._options.max_network_retries == 2
