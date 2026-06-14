"""Tests for WebAuthnService."""
from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest

from app.services.webauthn_service import WebAuthnError
from app.services.webauthn_service import WebAuthnService


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def service(mock_session: MagicMock) -> WebAuthnService:
    svc = WebAuthnService(mock_session)
    svc.user_repository = MagicMock()
    return svc


def make_user(user_id: str | None = None) -> MagicMock:
    user = MagicMock()
    user.id = UUID(user_id or "00000000-0000-0000-0000-000000000001")
    user.email = "user@example.com"
    user.full_name = "Test User"
    user.is_active = True
    return user


class TestBeginRegistration:
    """Tests for begin_registration()."""

    @pytest.mark.asyncio
    async def test_returns_options_dict(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should return a dict with registration options."""
        user = make_user()

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch.object(service, "_get_user_credentials", return_value=[]),
        ):
            options = await service.begin_registration(user)

        assert isinstance(options, dict)
        assert "rp" in options
        assert "user" in options
        assert "challenge" in options
        assert "pub_key_cred_params" in options
        mock_redis.setex.assert_awaited_once()


class TestBeginAuthentication:
    """Tests for begin_authentication()."""

    @pytest.mark.asyncio
    async def test_returns_options_dict(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should return a dict with authentication options."""
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch("app.services.webauthn_service.get_redis", return_value=mock_redis):
            options = await service.begin_authentication(email=None)

        assert isinstance(options, dict)
        assert "challenge" in options
        mock_redis.setex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_email_returns_options(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should return options with allow_credentials when email is provided."""
        user = make_user()
        service.user_repository.get_by_email = AsyncMock(return_value=user)

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch.object(service, "_get_user_credentials", return_value=[]),
        ):
            options = await service.begin_authentication(email="user@example.com")

        assert isinstance(options, dict)
        assert "challenge" in options


class TestCompleteRegistration:
    """Tests for complete_registration()."""

    @pytest.mark.asyncio
    async def test_raises_without_challenge(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should raise WebAuthnError when no challenge exists in Redis."""
        user = make_user()
        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value=None)

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            pytest.raises(WebAuthnError, match="Registration challenge expired"),
        ):
            await service.complete_registration(user, {"cred": "fake"})


class TestCompleteAuthentication:
    """Tests for complete_authentication()."""

    @pytest.mark.asyncio
    async def test_raises_without_credential(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should raise WebAuthnError when credential is not in DB."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        service.session.execute = AsyncMock(return_value=mock_result)

        from webauthn.helpers import base64url_to_bytes

        with pytest.raises(WebAuthnError, match="Credential not found"):
            await service.complete_authentication(
                {
                    "id": "fake-id",
                    "raw_id": base64url_to_bytes("ZmFrZS1pZA=="),
                    "response": {"client_data_json": "{}", "authenticator_data": ""},
                    "type": "public-key",
                }
            )
