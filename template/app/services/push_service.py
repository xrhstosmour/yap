"""Sending a push notification to a set of registered installs.

Apple is talked to directly over HTTP/2, which is the only protocol APNs
accepts. Android goes through FCM, because Google offers no direct
equivalent. Both are free, and the Apple key is needed for either route:
FCM delivers to iOS by forwarding through APNs with a key uploaded to it,
so routing iOS through FCM would add a hop and a third party without
removing a credential.

Both services authenticate with a short-lived JWT rather than a long-lived
secret. Apple's is signed with the `.p8` key directly and is good for an
hour; Google's is exchanged for an OAuth access token first. Both are
cached until shortly before they expire, because minting one per
notification would dominate the cost of sending.

Nothing here retries. A push is worth sending now or not at all, and the
caller already runs inside a task the broker will redeliver. What it does
report is which tokens the service called dead, since that is the only
reliable signal a token has expired and the caller is expected to act on
it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import httpx
import jwt

from app.core.logging import get_logger
from app.core.settings import settings
from app.models.device_token import DevicePlatform

logger = get_logger("service.push")

# Apple's tokens are valid for an hour and it refuses one minted less than
# twenty minutes ago, so this reuses each for fifty minutes: comfortably
# inside the hour, comfortably outside the twenty.
_APNS_TOKEN_LIFETIME = 3000
_GOOGLE_TOKEN_LIFETIME = 3000
# Refresh a little early rather than racing the expiry of a token already
# in flight.
_TOKEN_REFRESH_MARGIN = 300

_APNS_HOST = "https://api.push.apple.com"
_APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# Apple says the token is gone; Google says it was never valid or no
# longer is. Either way the row should stop being sent to.
_APNS_DEAD_REASONS = frozenset({"BadDeviceToken", "Unregistered"})
_FCM_DEAD_STATUSES = frozenset({"UNREGISTERED", "INVALID_ARGUMENT"})


@dataclass(frozen=True)
class PushNotification:
    """What one notification says and carries.

    Attributes:
        title: The bold line.
        body: The rest of it.
        data: Key-value pairs handed to the app when the notification is
            tapped, used to open the right screen. Both services require
            these to be strings, so callers stringify ids themselves
            rather than having it happen silently here.
    """

    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PushResult:
    """What came back from one fan-out.

    Attributes:
        sent: How many the service accepted.
        dead_tokens: Tokens the service says will never be deliverable
            again. The caller is expected to stop storing these.
    """

    sent: int
    dead_tokens: list[str]


class PushService:
    """Sends notifications to registered installs.

    Configured entirely from settings. With no credentials set it is
    inert: `send` reports nothing sent rather than raising, which is what
    lets everything above it be exercised in development and in tests
    without a Google or Apple account.
    """

    def __init__(self) -> None:
        """Initialize the push service."""
        self._apns_token: str | None = None
        self._apns_token_minted_at: float = 0.0
        self._google_token: str | None = None
        self._google_token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        """Whether either service has enough configuration to be used."""
        return bool(settings.APNS_KEY_ID) or bool(settings.FCM_PROJECT_ID)

    async def send(
        self,
        tokens: list[tuple[str, DevicePlatform]],
        notification: PushNotification,
    ) -> PushResult:
        """Deliver one notification to every given install.

        Args:
            tokens: The installs to reach, each with its platform.
            notification: What to say.

        Returns:
            How many were accepted and which tokens are dead.
        """
        if not tokens or not self.is_configured:
            return PushResult(sent=0, dead_tokens=[])

        apple = [token for token, platform in tokens if platform is DevicePlatform.IOS]
        google = [
            token for token, platform in tokens if platform is DevicePlatform.ANDROID
        ]

        sent = 0
        dead: list[str] = []
        if apple and settings.APNS_KEY_ID:
            apple_sent, apple_dead = await self._send_apple(apple, notification)
            sent += apple_sent
            dead.extend(apple_dead)
        if google and settings.FCM_PROJECT_ID:
            google_sent, google_dead = await self._send_google(google, notification)
            sent += google_sent
            dead.extend(google_dead)

        logger.info("push_sent", sent=sent, dead=len(dead), audience=len(tokens))
        return PushResult(sent=sent, dead_tokens=dead)

    async def _send_apple(
        self,
        tokens: list[str],
        notification: PushNotification,
    ) -> tuple[int, list[str]]:
        """Send to APNs, one request per token over a shared connection.

        Apple has no batch endpoint, so the saving available here is
        connection reuse rather than fewer requests: HTTP/2 multiplexes
        them all down one socket, which is why the client is built once
        around the whole loop.
        """
        host = _APNS_SANDBOX_HOST if settings.APNS_USE_SANDBOX else _APNS_HOST
        payload = {
            "aps": {
                "alert": {
                    "title": notification.title,
                    "body": notification.body,
                },
                "sound": "default",
            },
            **notification.data,
        }
        headers = {
            "authorization": f"bearer {self._apple_token()}",
            "apns-topic": settings.APNS_TOPIC,
            "apns-push-type": "alert",
        }

        sent = 0
        dead: list[str] = []
        async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
            for token in tokens:
                try:
                    response = await client.post(
                        f"{host}/3/device/{token}",
                        json=payload,
                        headers=headers,
                    )
                except httpx.HTTPError as error:
                    # One unreachable device must not cost the rest of the
                    # audience their notification.
                    logger.warning("apns_request_failed", error=str(error))
                    continue

                if response.status_code == 200:
                    sent += 1
                elif self._apple_says_dead(response):
                    dead.append(token)
                else:
                    logger.warning(
                        "apns_rejected",
                        status=response.status_code,
                        body=response.text[:200],
                    )
        return sent, dead

    @staticmethod
    def _apple_says_dead(response: httpx.Response) -> bool:
        """Whether Apple's rejection means this token is finished.

        A 410 is unambiguous. A 400 needs its reason read, because the
        same status covers a malformed payload, which is our bug and not
        the device's problem.
        """
        if response.status_code == 410:
            return True
        if response.status_code != 400:
            return False
        try:
            reason = response.json().get("reason")
        except ValueError:
            return False
        return reason in _APNS_DEAD_REASONS

    async def _send_google(
        self,
        tokens: list[str],
        notification: PushNotification,
    ) -> tuple[int, list[str]]:
        """Send to FCM, one request per token.

        The v1 API dropped the multicast endpoint the legacy one had, so
        this is a request each. They share a connection and an access
        token, which is where the cost actually was.
        """
        access_token = await self._google_access_token()
        if access_token is None:
            return 0, []

        url = (
            f"https://fcm.googleapis.com/v1/projects/"
            f"{settings.FCM_PROJECT_ID}/messages:send"
        )
        headers = {"authorization": f"Bearer {access_token}"}

        sent = 0
        dead: list[str] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for token in tokens:
                message: dict[str, Any] = {
                    "message": {
                        "token": token,
                        "notification": {
                            "title": notification.title,
                            "body": notification.body,
                        },
                        "data": notification.data,
                    }
                }
                try:
                    response = await client.post(url, json=message, headers=headers)
                except httpx.HTTPError as error:
                    logger.warning("fcm_request_failed", error=str(error))
                    continue

                if response.status_code == 200:
                    sent += 1
                elif self._google_says_dead(response):
                    dead.append(token)
                else:
                    logger.warning(
                        "fcm_rejected",
                        status=response.status_code,
                        body=response.text[:200],
                    )
        return sent, dead

    @staticmethod
    def _google_says_dead(response: httpx.Response) -> bool:
        """Whether Google's rejection means this token is finished.

        A 404 is the documented answer for a token that has been
        unregistered. A 400 is read the same way Apple's is, because it
        also covers our own malformed requests.
        """
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        details = body.get("error", {}).get("details", [])
        return any(
            detail.get("errorCode") in _FCM_DEAD_STATUSES
            for detail in details
            if isinstance(detail, dict)
        )

    def _apple_token(self) -> str:
        """The JWT Apple wants, minted at most every fifty minutes.

        Apple rejects a token minted less than twenty minutes ago, so this
        cannot simply mint one per request even though that would be
        simpler.
        """
        now = time.time()
        if (
            self._apns_token is not None
            and now - self._apns_token_minted_at < _APNS_TOKEN_LIFETIME
        ):
            return self._apns_token

        self._apns_token = jwt.encode(
            {"iss": settings.APNS_TEAM_ID, "iat": int(now)},
            settings.APNS_PRIVATE_KEY,
            algorithm="ES256",
            headers={"kid": settings.APNS_KEY_ID},
        )
        self._apns_token_minted_at = now
        return self._apns_token

    async def _google_access_token(self) -> str | None:
        """Exchange the service account for an OAuth token, and keep it.

        Returns:
            The access token, or None if the service account is unusable,
            which is reported rather than raised so one misconfigured
            project does not take the worker down.
        """
        now = time.time()
        if (
            self._google_token is not None
            and now < self._google_token_expires_at - _TOKEN_REFRESH_MARGIN
        ):
            return self._google_token

        try:
            account = json.loads(settings.FCM_SERVICE_ACCOUNT_JSON)
        except ValueError:
            logger.error("fcm_service_account_unreadable")
            return None

        assertion = jwt.encode(
            {
                "iss": account["client_email"],
                "scope": _FCM_SCOPE,
                "aud": _GOOGLE_TOKEN_URL,
                "iat": int(now),
                "exp": int(now) + _GOOGLE_TOKEN_LIFETIME,
            },
            account["private_key"],
            algorithm="RS256",
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    _GOOGLE_TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                )
            except httpx.HTTPError as error:
                logger.error("google_token_request_failed", error=str(error))
                return None

        if response.status_code != 200:
            logger.error("google_token_refused", status=response.status_code)
            return None

        body = response.json()
        self._google_token = body["access_token"]
        self._google_token_expires_at = now + body.get("expires_in", 3600)
        return self._google_token
