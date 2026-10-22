"""Unit tests for the Sign in with Apple helpers in `app.core.security`."""

import hashlib
import json
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import security
from app.core.security import APPLE_ISSUER
from app.core.security import create_apple_client_secret
from app.core.security import verify_apple_identity_token

BUNDLE_ID = "com.example.app"
NONCE = "raw-nonce-from-the-client"


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_set(key: rsa.RSAPrivateKey, key_id: str) -> dict[str, Any]:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": key_id, "alg": "RS256", "use": "sig"})
    return {"keys": [public]}


def _identity_token(
    key: rsa.RSAPrivateKey,
    key_id: str = "apple-key",
    **overrides: Any,  # noqa: ANN401
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": APPLE_ISSUER,
        "aud": BUNDLE_ID,
        "sub": "001234.apple-subject",
        "iat": now,
        "exp": now + 600,
        "email": "apple@example.com",
        "email_verified": "true",
        "nonce": hashlib.sha256(NONCE.encode()).hexdigest(),
    }
    claims.update(overrides)
    # A `None` override drops the claim.
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": key_id})


def _apple_serving(key_set: dict[str, Any]) -> tuple[MagicMock, AsyncMock]:
    """Patch `httpx.AsyncClient` to answer the key set request."""
    response = MagicMock()
    response.json.return_value = key_set
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return MagicMock(return_value=client), client.get


@pytest.fixture(autouse=True)
def _empty_key_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(security, "_apple_keys", {})
    monkeypatch.setattr(security, "_apple_keys_fetched_at", None)
    yield


class TestVerifyAppleIdentityToken:
    """Tests for `verify_apple_identity_token`."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_its_claims(self) -> None:
        """A token Apple signed for this app and nonce verifies."""
        key = _rsa_key()
        client, _ = _apple_serving(_key_set(key, "apple-key"))

        with patch("httpx.AsyncClient", client):
            claims = await verify_apple_identity_token(
                _identity_token(key), BUNDLE_ID, NONCE
            )

        assert claims["sub"] == "001234.apple-subject"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("overrides", "audience", "nonce"),
        [
            pytest.param({}, "com.example.other", NONCE, id="another app"),
            pytest.param({}, BUNDLE_ID, "another-nonce", id="another nonce"),
            pytest.param({"nonce": None}, BUNDLE_ID, NONCE, id="no nonce claim"),
            pytest.param(
                {"iss": "https://example.com"}, BUNDLE_ID, NONCE, id="not Apple"
            ),
            pytest.param(
                {"exp": int(time.time()) - 60}, BUNDLE_ID, NONCE, id="expired"
            ),
        ],
    )
    async def test_rejects_a_token_not_meant_for_this_request(
        self, overrides: dict[str, Any], audience: str, nonce: str
    ) -> None:
        """Audience, nonce, issuer and expiry are each enforced."""
        key = _rsa_key()
        client, _ = _apple_serving(_key_set(key, "apple-key"))
        token = _identity_token(key, **overrides)

        with patch("httpx.AsyncClient", client):
            with pytest.raises(jwt.InvalidTokenError):
                await verify_apple_identity_token(token, audience, nonce)

    @pytest.mark.asyncio
    async def test_rejects_a_token_signed_by_another_key(self) -> None:
        """A forged token naming Apple's key ID fails the signature check."""
        apple_key = _rsa_key()
        client, _ = _apple_serving(_key_set(apple_key, "apple-key"))

        with patch("httpx.AsyncClient", client):
            with pytest.raises(jwt.InvalidSignatureError):
                await verify_apple_identity_token(
                    _identity_token(_rsa_key()), BUNDLE_ID, NONCE
                )

    @pytest.mark.asyncio
    async def test_caches_the_key_set(self) -> None:
        """A second sign-in reuses the keys fetched for the first."""
        key = _rsa_key()
        client, get = _apple_serving(_key_set(key, "apple-key"))

        with patch("httpx.AsyncClient", client):
            await verify_apple_identity_token(_identity_token(key), BUNDLE_ID, NONCE)
            await verify_apple_identity_token(_identity_token(key), BUNDLE_ID, NONCE)

        assert get.await_count == 1

    @pytest.mark.asyncio
    async def test_unknown_key_ids_refetch_at_most_once_a_minute(self) -> None:
        """Made-up key IDs cannot turn every request into a call to Apple."""
        key = _rsa_key()
        client, get = _apple_serving(_key_set(key, "apple-key"))

        with patch("httpx.AsyncClient", client):
            for key_id in ("made-up-1", "made-up-2", "made-up-3"):
                with pytest.raises(jwt.InvalidTokenError, match="Unknown Apple"):
                    await verify_apple_identity_token(
                        _identity_token(key, key_id=key_id), BUNDLE_ID, NONCE
                    )

        assert get.await_count == 1

    @pytest.mark.asyncio
    async def test_a_rotated_key_is_picked_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key ID missing from an older cache triggers a refetch."""
        old_key = _rsa_key()
        new_key = _rsa_key()
        monkeypatch.setattr(
            security,
            "_apple_keys",
            {"old": jwt.PyJWK.from_dict(_key_set(old_key, "old")["keys"][0])},
        )
        monkeypatch.setattr(security, "_apple_keys_fetched_at", time.monotonic() - 120)
        client, get = _apple_serving(_key_set(new_key, "new"))

        with patch("httpx.AsyncClient", client):
            claims = await verify_apple_identity_token(
                _identity_token(new_key, key_id="new"), BUNDLE_ID, NONCE
            )

        assert claims["aud"] == BUNDLE_ID
        assert get.await_count == 1


class TestCreateAppleClientSecret:
    """Tests for `create_apple_client_secret`."""

    def test_signs_the_claims_apple_expects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The secret is an ES256 JWT for the team, key and Services ID."""
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        monkeypatch.setattr(security.settings, "APPLE_PRIVATE_KEY", pem)
        monkeypatch.setattr(security.settings, "APPLE_TEAM_ID", "TEAM123456")
        monkeypatch.setattr(security.settings, "APPLE_KEY_ID", "KEY1234567")
        monkeypatch.setattr(security.settings, "APPLE_CLIENT_ID", "com.example.web")

        secret = create_apple_client_secret()
        claims = jwt.decode(
            secret,
            private_key.public_key(),
            algorithms=["ES256"],
            audience=APPLE_ISSUER,
        )

        assert jwt.get_unverified_header(secret)["kid"] == "KEY1234567"
        assert (claims["iss"], claims["sub"]) == ("TEAM123456", "com.example.web")
        assert claims["exp"] - claims["iat"] <= 300

    def test_speaks_for_the_client_it_is_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A code from the app is exchanged and revoked as the bundle ID."""
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        monkeypatch.setattr(security.settings, "APPLE_PRIVATE_KEY", pem)
        monkeypatch.setattr(security.settings, "APPLE_CLIENT_ID", "com.example.web")

        secret = create_apple_client_secret("com.example.app")
        claims = jwt.decode(
            secret,
            private_key.public_key(),
            algorithms=["ES256"],
            audience=APPLE_ISSUER,
        )

        assert claims["sub"] == "com.example.app"
