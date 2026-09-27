"""Unit tests for sending a push notification.

Both services are stubbed at the HTTP layer with `httpx.MockTransport`,
so what these check is the part we wrote: which host each platform goes
to, what shape the body is, and which rejections mean a token is dead.
Nothing here reaches Apple or Google.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.models.device_token import DevicePlatform
from app.services.push_service import PushNotification
from app.services.push_service import PushService


def _throwaway_ec_key() -> str:
    """A fresh P-256 key, so signing is exercised rather than mocked.

    Generated per run rather than pasted in as a literal. A committed PEM
    is indistinguishable from a leaked one to everything that scans for
    them, and being able to say a repository contains no private keys at
    all is worth more than the microsecond this costs.
    """
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )


@pytest.fixture
def notification() -> PushNotification:
    return PushNotification(
        title="Μαρία",
        body="πήγε στο Διπόρτο",
        data={"visit_id": "abc"},
    )


def _configure_apple(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import settings

    monkeypatch.setattr(settings, "APNS_KEY_ID", "KEY123", raising=False)
    monkeypatch.setattr(settings, "APNS_TEAM_ID", "TEAM123", raising=False)
    monkeypatch.setattr(
        settings, "APNS_PRIVATE_KEY", _throwaway_ec_key(), raising=False
    )
    monkeypatch.setattr(settings, "APNS_TOPIC", "com.example.app", raising=False)
    monkeypatch.setattr(settings, "APNS_USE_SANDBOX", False, raising=False)


def _configure_google(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import settings

    monkeypatch.setattr(settings, "FCM_PROJECT_ID", "demo-project", raising=False)
    monkeypatch.setattr(
        settings,
        "FCM_SERVICE_ACCOUNT_JSON",
        json.dumps(
            {
                "client_email": "push@demo-project.iam.gserviceaccount.com",
                "private_key": "unused, the token exchange is stubbed",
            }
        ),
        raising=False,
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    seen: list[httpx.Request] | None = None,
) -> None:
    """Make every client the service builds answer from `handler`."""
    real = httpx.AsyncClient

    def _build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("http2", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _build)
    if seen is not None:
        seen.clear()


class TestUnconfigured:
    """Tests for the state every developer machine is in."""

    @pytest.mark.asyncio
    async def test_sends_nothing_without_credentials(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """Inert rather than raising, so everything above it stays testable."""
        from app.core.settings import settings

        monkeypatch.setattr(settings, "APNS_KEY_ID", "", raising=False)
        monkeypatch.setattr(settings, "FCM_PROJECT_ID", "", raising=False)

        result = await PushService().send([("token", DevicePlatform.IOS)], notification)

        assert result.sent == 0
        assert result.dead_tokens == []

    @pytest.mark.asyncio
    async def test_an_empty_audience_is_not_an_error(
        self,
        notification: PushNotification,
    ) -> None:
        """Most events have nobody to notify, so this is the common path."""
        result = await PushService().send([], notification)

        assert result.sent == 0


class TestApple:
    """Tests for the APNs path."""

    @pytest.mark.asyncio
    async def test_posts_one_request_per_device_to_the_live_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """Apple has no batch endpoint, so this is a request each."""
        _configure_apple(monkeypatch)
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        _patch_client(monkeypatch, handler)

        result = await PushService().send(
            [("one", DevicePlatform.IOS), ("two", DevicePlatform.IOS)],
            notification,
        )

        assert result.sent == 2
        assert [str(request.url) for request in seen] == [
            "https://api.push.apple.com/3/device/one",
            "https://api.push.apple.com/3/device/two",
        ]
        body = json.loads(seen[0].content)
        assert body["aps"]["alert"]["title"] == "Μαρία"
        # Tap-through data rides alongside `aps`, not inside it.
        assert body["visit_id"] == "abc"
        assert seen[0].headers["apns-topic"] == "com.example.app"

    @pytest.mark.asyncio
    async def test_debug_builds_go_to_the_sandbox_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """A sandbox token is refused by the live host, and the other way round."""
        from app.core.settings import settings

        _configure_apple(monkeypatch)
        monkeypatch.setattr(settings, "APNS_USE_SANDBOX", True, raising=False)
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        _patch_client(monkeypatch, handler)

        await PushService().send([("one", DevicePlatform.IOS)], notification)

        assert str(seen[0].url).startswith("https://api.sandbox.push.apple.com/")

    @pytest.mark.asyncio
    async def test_a_410_marks_the_token_dead(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """The app was deleted, so the row should stop being sent to."""
        _configure_apple(monkeypatch)
        _patch_client(monkeypatch, lambda request: httpx.Response(410))

        result = await PushService().send([("gone", DevicePlatform.IOS)], notification)

        assert result.sent == 0
        assert result.dead_tokens == ["gone"]

    @pytest.mark.asyncio
    async def test_a_bad_payload_does_not_condemn_the_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """A 400 covers our own bugs too, so the reason has to be read.

        Deleting a live token because we sent it malformed JSON would
        silently unsubscribe somebody from a fault that was ours.
        """
        _configure_apple(monkeypatch)
        _patch_client(
            monkeypatch,
            lambda request: httpx.Response(400, json={"reason": "PayloadTooLarge"}),
        )

        result = await PushService().send([("fine", DevicePlatform.IOS)], notification)

        assert result.dead_tokens == []

    @pytest.mark.asyncio
    async def test_a_bad_device_token_is_dead(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """The other half of reading the 400 reason."""
        _configure_apple(monkeypatch)
        _patch_client(
            monkeypatch,
            lambda request: httpx.Response(400, json={"reason": "BadDeviceToken"}),
        )

        result = await PushService().send(
            [("rubbish", DevicePlatform.IOS)], notification
        )

        assert result.dead_tokens == ["rubbish"]

    @pytest.mark.asyncio
    async def test_one_unreachable_device_does_not_stop_the_rest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """A connection failure is about the network, not the audience."""
        _configure_apple(monkeypatch)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/broken"):
                raise httpx.ConnectError("no route")
            return httpx.Response(200)

        _patch_client(monkeypatch, handler)

        result = await PushService().send(
            [
                ("broken", DevicePlatform.IOS),
                ("working", DevicePlatform.IOS),
            ],
            notification,
        )

        assert result.sent == 1
        assert result.dead_tokens == []


class TestGoogle:
    """Tests for the FCM path."""

    @pytest.mark.asyncio
    async def test_exchanges_the_service_account_then_sends(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """One token exchange for the whole batch, then a send per device."""
        _configure_google(monkeypatch)
        monkeypatch.setattr(
            "app.services.push_service.jwt.encode",
            lambda *args, **kwargs: "signed-assertion",
        )
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if "oauth2" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "google-token", "expires_in": 3600}
                )
            return httpx.Response(200, json={})

        _patch_client(monkeypatch, handler)

        result = await PushService().send(
            [("one", DevicePlatform.ANDROID), ("two", DevicePlatform.ANDROID)],
            notification,
        )

        assert result.sent == 2
        # One exchange, then two sends.
        assert len(seen) == 3
        assert seen[1].headers["authorization"] == "Bearer google-token"
        message = json.loads(seen[1].content)["message"]
        assert message["token"] == "one"
        assert message["notification"]["body"] == "πήγε στο Διπόρτο"
        assert message["data"] == {"visit_id": "abc"}

    @pytest.mark.asyncio
    async def test_a_404_marks_the_token_dead(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """Google's documented answer for an unregistered token."""
        _configure_google(monkeypatch)
        monkeypatch.setattr(
            "app.services.push_service.jwt.encode",
            lambda *args, **kwargs: "signed-assertion",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if "oauth2" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "google-token", "expires_in": 3600}
                )
            return httpx.Response(404, json={})

        _patch_client(monkeypatch, handler)

        result = await PushService().send(
            [("gone", DevicePlatform.ANDROID)], notification
        )

        assert result.dead_tokens == ["gone"]

    @pytest.mark.asyncio
    async def test_a_refused_service_account_sends_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """One misconfigured project must not take the worker down."""
        _configure_google(monkeypatch)
        monkeypatch.setattr(
            "app.services.push_service.jwt.encode",
            lambda *args, **kwargs: "signed-assertion",
        )
        _patch_client(monkeypatch, lambda request: httpx.Response(401, json={}))

        result = await PushService().send(
            [("one", DevicePlatform.ANDROID)], notification
        )

        assert result.sent == 0
        assert result.dead_tokens == []


class TestMixedAudience:
    """Tests for a fan-out that spans both platforms."""

    @pytest.mark.asyncio
    async def test_each_platform_goes_to_its_own_service(
        self,
        monkeypatch: pytest.MonkeyPatch,
        notification: PushNotification,
    ) -> None:
        """The usual case: one event, followers on both stores."""
        _configure_apple(monkeypatch)
        _configure_google(monkeypatch)
        monkeypatch.setattr(
            "app.services.push_service.jwt.encode",
            lambda *args, **kwargs: "signed-assertion",
        )
        hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hosts.append(request.url.host)
            if "oauth2" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "google-token", "expires_in": 3600}
                )
            return httpx.Response(200, json={})

        _patch_client(monkeypatch, handler)

        result = await PushService().send(
            [
                ("apple-one", DevicePlatform.IOS),
                ("google-one", DevicePlatform.ANDROID),
            ],
            notification,
        )

        assert result.sent == 2
        assert "api.push.apple.com" in hosts
        assert "fcm.googleapis.com" in hosts
