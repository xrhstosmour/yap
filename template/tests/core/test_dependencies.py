"""Unit tests for FastAPI dependency injection functions.

Tests cover get_current_user, get_optional_current_user,
get_current_superuser, and get_api_key_auth with mocked
external services (JWT decode, DB repositories, rate limiting).
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi import status
from jwt import ExpiredSignatureError
from jwt import InvalidTokenError

from app.core.security import DUMMY_PASSWORD_HASH
from app.dependencies import get_api_key_auth
from app.dependencies import get_current_superuser
from app.dependencies import get_current_user
from app.dependencies import get_optional_current_user
from app.models.api_key import APIKey
from app.models.user import User
from app.models.user import UserRole
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.api_key_service import APIKeyService

# Helpers


def _make_user(
    *,
    user_id: uuid4 | None = None,
    email: str = "test@example.com",
    is_active: bool = True,
    role: UserRole = UserRole.USER,
    token_version: int = 1,
    tenant_id: uuid4 | None = None,
) -> User:
    """Build a User instance for testing."""
    return User(
        id=user_id or uuid4(),
        email=email,
        hashed_password="hashed_test_value",
        full_name="Test User",
        is_active=is_active,
        role=role,
        is_verified=True,
        token_version=token_version,
        tenant_id=tenant_id,
    )


def _mock_request(headers: dict[str, str] | None = None) -> MagicMock:
    """Create a mock FastAPI Request with the given headers."""
    mock = MagicMock()
    mock.headers = MagicMock()
    mock.headers.get.side_effect = lambda k, d=None: (headers or {}).get(k, d)
    mock.state = MagicMock()
    return mock


def _valid_payload(
    *,
    sub: str = "00000000-0000-0000-0000-000000000001",
    type_: str = "access",
    token_version: int | None = None,
) -> dict:
    """Return a minimal valid JWT payload."""
    payload: dict = {"sub": sub, "type": type_}
    if token_version is not None:
        payload["token_version"] = str(token_version)
    return payload


# get_current_user


class TestGetCurrentUser:
    """Tests for get_current_user()."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self) -> None:
        """Should return the user when token is valid."""
        user = _make_user()
        request = _mock_request()
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id)),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
        ):
            result = await get_current_user(
                session=session,
                token="valid_token_string",
                request=request,
            )

        assert result is user
        assert request.state.user is user
        assert request.state.user_id == user.id

    @pytest.mark.asyncio
    async def test_invalid_token_raises_403(self) -> None:
        """Should raise 403 when token is invalid (InvalidTokenError)."""
        request = _mock_request()
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            side_effect=InvalidTokenError("bad signature"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="bad_token",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_expired_token_raises_403(self) -> None:
        """Should raise 403 when token is expired."""
        request = _mock_request()
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            side_effect=ExpiredSignatureError("expired"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="expired_token",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_nonexistent_user_raises_404(self) -> None:
        """Should raise 404 when user ID from token is not in DB."""
        request = _mock_request()
        session = AsyncMock()

        with (
            patch("app.dependencies.decode_token", return_value=_valid_payload()),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=None
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="valid_but_user_deleted",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_inactive_user_raises_403(self) -> None:
        """Should raise 403 when user exists but is inactive."""
        user = _make_user(is_active=False)
        request = _mock_request()
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id)),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="valid_token",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_token_version_mismatch_raises_403(self) -> None:
        """Should raise 403 when token_version doesn't match user's."""
        user = _make_user(token_version=5)
        request = _mock_request()
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id), token_version=3),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="valid_token",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_blacklisted_token_raises_403(self) -> None:
        """Should raise 403 when token jti is blacklisted."""
        request = _mock_request()
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value={
                    "sub": str(uuid4()),
                    "type": "access",
                    "jti": "blacklisted-jti",
                },
            ),
            patch(
                "app.dependencies.is_token_blacklisted",
                AsyncMock(return_value=True),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="blacklisted_token",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_wrong_token_type_raises_403(self) -> None:
        """Should raise 403 when token type is not 'access'."""
        request = _mock_request()
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            return_value=_valid_payload(type_="refresh"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="refresh_token_used_as_access",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_sets_tenant_context_when_user_has_tenant(self) -> None:
        """Should set tenant context on request.state when user has tenant_id."""
        tenant_id = uuid4()
        user = _make_user(tenant_id=tenant_id)
        request = _mock_request()
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id)),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
            patch("app.dependencies.set_current_tenant_id") as mock_set_tenant,
        ):
            await get_current_user(
                session=session,
                token="valid_token",
                request=request,
            )

        mock_set_tenant.assert_called_once_with(tenant_id)
        assert request.state.tenant_id == tenant_id


# get_optional_current_user


class TestGetOptionalCurrentUser:
    """Tests for get_optional_current_user()."""

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self) -> None:
        """Should return the user when a valid Bearer token is provided."""
        user = _make_user()
        request = _mock_request(headers={"Authorization": "Bearer valid_token"})
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id)),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is user

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self) -> None:
        """Should return None when the Bearer token is invalid."""
        request = _mock_request(headers={"Authorization": "Bearer bad_token"})
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            side_effect=InvalidTokenError("bad"),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self) -> None:
        """Should return None when the Bearer token is expired."""
        request = _mock_request(headers={"Authorization": "Bearer expired_token"})
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            side_effect=ExpiredSignatureError("expired"),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_no_authorization_header_returns_none(self) -> None:
        """Should return None when no Authorization header is present."""
        request = _mock_request(headers={})
        session = AsyncMock()

        result = await get_optional_current_user(
            session=session,
            request=request,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_non_bearer_authorization_returns_none(self) -> None:
        """Should return None when Authorization is not Bearer."""
        request = _mock_request(headers={"Authorization": "Basic dGVzdDp0ZXN0"})
        session = AsyncMock()

        result = await get_optional_current_user(
            session=session,
            request=request,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_inactive_user_returns_none(self) -> None:
        """Should return None when user exists but is inactive."""
        user = _make_user(is_active=False)
        request = _mock_request(headers={"Authorization": "Bearer token"})
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id)),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_user_not_found_returns_none(self) -> None:
        """Should return None when user ID from token doesn't exist."""
        request = _mock_request(headers={"Authorization": "Bearer valid_token"})
        session = AsyncMock()

        with (
            patch("app.dependencies.decode_token", return_value=_valid_payload()),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=None
            ),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_token_type_returns_none(self) -> None:
        """Should return None when token type is not 'access'."""
        request = _mock_request(headers={"Authorization": "Bearer refresh_token"})
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            return_value=_valid_payload(type_="refresh"),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_sub_claim_returns_none(self) -> None:
        """Should return None when token decodes but has no 'sub' claim."""
        request = _mock_request(headers={"Authorization": "Bearer token_no_sub"})
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            return_value={"type": "access"},  # no "sub"
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_blacklisted_token_returns_none(self) -> None:
        """Should return None when token jti is blacklisted."""
        request = _mock_request(headers={"Authorization": "Bearer token"})
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value={
                    "sub": str(uuid4()),
                    "type": "access",
                    "jti": "blacklisted-jti",
                },
            ),
            patch(
                "app.dependencies.is_token_blacklisted",
                AsyncMock(return_value=True),
            ),
        ):
            result = await get_optional_current_user(
                session=session,
                request=request,
            )

        assert result is None


# get_current_superuser


class TestGetCurrentSuperuser:
    """Tests for get_current_superuser()."""

    @pytest.mark.asyncio
    async def test_superuser_passes_through(self) -> None:
        """Should return the user when they are a superuser."""
        user = _make_user(role=UserRole.SUPERUSER)

        result = await get_current_superuser(current_user=user)

        assert result is user

    @pytest.mark.asyncio
    async def test_non_superuser_raises_403(self) -> None:
        """Should raise 403 when user is not a superuser."""
        user = _make_user(role=UserRole.USER)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_superuser(current_user=user)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail == "Not enough permissions"


# get_api_key_auth


class TestGetApiKeyAuth:
    """Tests for get_api_key_auth()."""

    @pytest.mark.asyncio
    async def test_valid_key_returns_api_key(self) -> None:
        """Should return the APIKey when a valid X-API-Key header is provided."""
        api_key = APIKey(
            id=uuid4(),
            key_id="key_test123",
            key_hash="hashed_in_test",
            key_prefix="sk_testab",
            name="Test Key",
            scopes=[],
            tenant_id=uuid4(),
            user_id=uuid4(),
        )
        request = _mock_request(headers={"X-API-Key": "key_test123:test_secret_value"})
        session = AsyncMock()

        with patch.object(
            APIKeyService, "verify", new_callable=AsyncMock, return_value=api_key
        ):
            result = await get_api_key_auth(
                session=session,
                request=request,
            )

        assert result is api_key
        assert request.state.api_key is api_key
        assert request.state.tenant_id == api_key.tenant_id

    @pytest.mark.asyncio
    async def test_unknown_key_id_still_costs_a_hash_comparison(self) -> None:
        """An unknown key ID must take as long as a known one.

        Verifying through the repository returned the moment no row
        matched, so response time told an anonymous caller which key IDs
        exist. The service path compares against a dummy hash instead.
        """
        request = _mock_request(headers={"X-API-Key": "key_unknown:some_secret"})
        session = AsyncMock()

        with (
            patch.object(
                APIKeyRepository,
                "get_by_key_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.api_key_service.verify_password", return_value=False
            ) as mock_verify,
        ):
            result = await get_api_key_auth(
                session=session,
                request=request,
            )

        assert result is None
        mock_verify.assert_called_once_with("some_secret", DUMMY_PASSWORD_HASH)

    @pytest.mark.asyncio
    async def test_valid_key_stamps_last_used_at(self) -> None:
        """Usage tracking has to happen on the path that actually runs.

        `last_used_at` stayed NULL forever because the only code stamping
        it lived in a service method nothing called.
        """
        api_key = APIKey(
            id=uuid4(),
            key_id="key_test123",
            key_hash="hashed_in_test",
            key_prefix="sk_testab",
            name="Test Key",
            scopes=[],
            tenant_id=uuid4(),
            user_id=uuid4(),
        )
        request = _mock_request(headers={"X-API-Key": "key_test123:test_secret_value"})
        session = AsyncMock()

        with (
            patch.object(
                APIKeyRepository,
                "get_by_key_id",
                new_callable=AsyncMock,
                return_value=api_key,
            ),
            patch("app.services.api_key_service.verify_password", return_value=True),
            patch.object(
                APIKeyRepository, "update_last_used", new_callable=AsyncMock
            ) as mock_stamp,
        ):
            result = await get_api_key_auth(
                session=session,
                request=request,
            )

        assert result is api_key
        mock_stamp.assert_awaited_once_with(api_key.id)

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self) -> None:
        """Should return None when API key verification fails.

        Note: get_api_key_auth does NOT raise 401 for invalid keys;
        it returns None so that downstream auth (e.g., get_any_auth)
        can decide how to handle the missing credential.
        """
        request = _mock_request(headers={"X-API-Key": "key_bad:bad_secret"})
        session = AsyncMock()

        with patch.object(
            APIKeyService, "verify", new_callable=AsyncMock, return_value=None
        ):
            result = await get_api_key_auth(
                session=session,
                request=request,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self) -> None:
        """Should return None when X-API-Key header is absent."""
        request = _mock_request(headers={})
        session = AsyncMock()

        result = await get_api_key_auth(
            session=session,
            request=request,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_header_returns_none(self) -> None:
        """Should return None when X-API-Key header lacks colon separator."""
        request = _mock_request(headers={"X-API-Key": "no_colon_separator"})
        session = AsyncMock()

        result = await get_api_key_auth(
            session=session,
            request=request,
        )

        assert result is None


# Additional get_current_user uncovered paths


class TestGetCurrentUserUncovered:
    """Tests for get_current_user paths not covered by main test class."""

    @pytest.mark.asyncio
    async def test_missing_sub_in_payload_raises_403(self) -> None:
        """Should raise 403 when token payload has no 'sub' claim."""
        request = _mock_request()
        session = AsyncMock()

        with patch(
            "app.dependencies.decode_token",
            return_value={"type": "access"},  # no "sub"
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(
                    session=session,
                    token="token_no_sub",
                    request=request,
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_calls_check_user_rate_limit(self) -> None:
        """Should call check_user_rate_limit with the user's ID."""
        user = _make_user()
        request = _mock_request()
        session = AsyncMock()

        with (
            patch(
                "app.dependencies.decode_token",
                return_value=_valid_payload(sub=str(user.id)),
            ),
            patch.object(
                UserRepository, "get", new_callable=AsyncMock, return_value=user
            ),
            patch("app.dependencies.set_current_tenant_id"),
            patch("app.dependencies.check_user_rate_limit") as mock_rate_limit,
        ):
            await get_current_user(
                session=session,
                token="valid_token",
                request=request,
            )

        mock_rate_limit.assert_awaited_once_with(str(user.id))
