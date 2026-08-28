"""Unit tests for `AuthService`."""

from typing import cast
from unittest.mock import ANY
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid7

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenRateLimitError
from app.core.security import decode_token
from app.core.security import generate_password_hash
from app.core.tenant import system_context
from app.models.user import UserRole
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthenticationError
from app.services.auth_service import AuthService
from app.services.auth_service import EmailAlreadyExistsError
from app.services.auth_service import InvalidCredentialsError
from app.services.auth_service import UserInactiveError
from app.services.auth_service import UserNotFoundError


def _auth_service(session: AsyncSession) -> AuthService:
    return AuthService(cast(AsyncSession, session))


class TestAuthService:
    """Tests for AuthService."""

    @pytest.mark.asyncio
    async def test_authenticate_user_success(self, session: AsyncSession) -> None:
        """User should authenticate with correct credentials."""
        user_create = RegisterRequest(
            email="test@example.com",
            password="password123",
        )

        auth_service = _auth_service(session)
        user = await auth_service.register(user_create)

        authenticated = await auth_service.authenticate(
            email="test@example.com",
            password="password123",
        )

        assert authenticated is not None
        assert authenticated.email == user.email

    @pytest.mark.asyncio
    async def test_authenticate_succeeds_when_audit_log_write_fails(
        self, session: AsyncSession
    ) -> None:
        """Login should succeed even if the audit-log write fails."""
        user_create = RegisterRequest(
            email="test@example.com",
            password="password123",
        )

        auth_service = _auth_service(session)
        user = await auth_service.register(user_create)
        auth_service.audit_repository.log_user_action = AsyncMock(
            side_effect=Exception("db unavailable")
        )

        authenticated = await auth_service.authenticate(
            email="test@example.com",
            password="password123",
        )

        assert authenticated is not None
        assert authenticated.email == user.email

    @pytest.mark.asyncio
    async def test_authenticate_user_wrong_password(
        self, session: AsyncSession
    ) -> None:
        """Authentication should fail with wrong password."""
        user_create = RegisterRequest(
            email="test@example.com",
            password="password123",
        )

        auth_service = _auth_service(session)
        await auth_service.register(user_create)

        with pytest.raises(InvalidCredentialsError):
            await auth_service.authenticate(
                email="test@example.com",
                password="wrongpassword",
            )

    @pytest.mark.asyncio
    async def test_authenticate_user_not_found(self, session: AsyncSession) -> None:
        """Authentication should fail for non-existent user."""
        auth_service = _auth_service(session)

        with pytest.raises(InvalidCredentialsError):
            await auth_service.authenticate(
                email="nonexistent@example.com",
                password="password123",
            )

    @pytest.mark.asyncio
    async def test_authenticate_inactive_user_raises_inactive(
        self, session: AsyncSession
    ) -> None:
        """Authentication should raise UserInactiveError for inactive users."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="inactive@example.com",
                password="password123",
            )
        )
        with system_context():
            await auth_service.user_repository.update(user.id, {"is_active": False})

        with pytest.raises(UserInactiveError):
            await auth_service.authenticate(
                email="inactive@example.com",
                password="password123",
            )

    @pytest.mark.asyncio
    async def test_register_existing_email_raises_error(
        self, session: AsyncSession
    ) -> None:
        """Registering with an existing email should raise EmailAlreadyExistsError."""
        auth_service = _auth_service(session)
        await auth_service.register(
            RegisterRequest(
                email="duplicate@example.com",
                password="password123",
            )
        )

        with pytest.raises(EmailAlreadyExistsError):
            await auth_service.register(
                RegisterRequest(
                    email="duplicate@example.com",
                    password="password456",
                )
            )

    def test_create_tokens_returns_token_response(self, session: AsyncSession) -> None:
        """create_tokens should return TokenResponse with access and refresh tokens."""
        auth_service = _auth_service(session)
        # Create a minimal User-like object to avoid DB round-trip.
        from app.models.user import User as UserModel
        from app.models.user import UserRole

        user = MagicMock(spec=UserModel)
        user.id = uuid7()
        user.email = "tokens@example.com"
        user.tenant_id = None
        user.token_version = 0
        user.role = UserRole.USER

        tokens = auth_service.create_tokens(user)

        assert tokens.access_token
        assert tokens.refresh_token
        assert tokens.token_type == "bearer"
        assert tokens.expires_in > 0

    @pytest.mark.asyncio
    async def test_change_password_wrong_current_raises_error(
        self, session: AsyncSession
    ) -> None:
        """Change password with wrong current password should raise."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="wrongpw@example.com",
                password="correctpassword",
            )
        )

        with pytest.raises(InvalidCredentialsError):
            await auth_service.change_password(user, "wrongcurrent", "newpassword1")

    @pytest.mark.asyncio
    async def test_verify_user_marks_as_verified(self, session: AsyncSession) -> None:
        """verify_user should set is_verified to True."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="verifyme@example.com",
                password="password123",
            )
        )
        assert user.is_verified is False

        await auth_service.verify_user(user.id)

        with system_context():
            reloaded = await auth_service.user_repository.get(user.id)

        assert reloaded is not None
        assert reloaded.is_verified is True

    @pytest.mark.asyncio
    async def test_refresh_tokens_blacklists_old_refresh_token(
        self, session: AsyncSession
    ) -> None:
        """Refreshing tokens should blacklist used refresh token jti."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="refresh-blacklist@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)

        with patch(
            "app.services.auth_service.blacklist_token",
            AsyncMock(),
        ) as mock_blacklist_token:
            refreshed_tokens = await auth_service.refresh_tokens(tokens.refresh_token)

        assert refreshed_tokens.access_token
        assert refreshed_tokens.refresh_token
        assert refreshed_tokens.refresh_token != tokens.refresh_token
        mock_blacklist_token.assert_awaited_once_with(ANY, ANY)

    @pytest.mark.asyncio
    async def test_refresh_tokens_rejects_blacklisted_refresh_token(
        self, session: AsyncSession
    ) -> None:
        """A refresh token already blacklisted (e.g. by logout) must not
        be honoured to mint a fresh token pair.
        """
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="refresh-revoked@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)

        with patch(
            "app.services.auth_service.is_token_blacklisted",
            AsyncMock(return_value=True),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.refresh_tokens(tokens.refresh_token)

    @pytest.mark.asyncio
    async def test_logout_blacklists_access_and_refresh_token(
        self, session: AsyncSession
    ) -> None:
        """Logout should blacklist both provided token identifiers."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="logout-service@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)
        access_payload = decode_token(tokens.access_token)

        with patch(
            "app.services.auth_service.blacklist_token",
            AsyncMock(),
        ) as mock_blacklist_token:
            await auth_service.logout(
                user=user,
                access_payload=access_payload,
                refresh_token=tokens.refresh_token,
            )

        assert mock_blacklist_token.await_count == 2

    @pytest.mark.asyncio
    async def test_logout_invalid_refresh_token_raises(
        self, session: AsyncSession
    ) -> None:
        """Logout should reject invalid refresh token payload."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="logout-invalid-service@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)
        access_payload = decode_token(tokens.access_token)

        with pytest.raises(AuthenticationError):
            await auth_service.logout(
                user=user,
                access_payload=access_payload,
                refresh_token="bad-refresh-token",
            )


class TestAuthServiceCreateUser:
    """Tests for AuthService user creation."""

    @pytest.mark.asyncio
    async def test_create_user_hashes_password(self, session: AsyncSession) -> None:
        """Created user should have hashed password."""
        user_create = RegisterRequest(
            email="test@example.com",
            password="password123",
        )

        auth_service = _auth_service(session)
        user = await auth_service.register(user_create)

        assert user.hashed_password != "password123"
        assert len(user.hashed_password) > 0

    @pytest.mark.asyncio
    async def test_create_user_sets_email_verified_false(
        self, session: AsyncSession
    ) -> None:
        """New user should have email_verified as False."""
        user_create = RegisterRequest(
            email="test@example.com",
            password="password123",
        )

        auth_service = _auth_service(session)
        user = await auth_service.register(user_create)

        assert user.is_verified is False

    @pytest.mark.asyncio
    async def test_create_superuser(self, session: AsyncSession) -> None:
        """Superuser should have role set to SUPERUSER."""
        from app.core import SYSTEM_TENANT_ID

        auth_service = _auth_service(session)
        user = await auth_service.user_repository.create_user(
            email="admin@example.com",
            password_hash=generate_password_hash("adminpass"),
            tenant_id=SYSTEM_TENANT_ID,
            role=UserRole.SUPERUSER,
        )

        assert user.role == UserRole.SUPERUSER


class TestResetPassword:
    """Tests for AuthService.reset_password."""

    @pytest.mark.asyncio
    async def test_reset_password_valid_token(self, session: AsyncSession) -> None:
        """Password should be reset with a valid token."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="reset@example.com",
                password="oldpassword1",
            )
        )

        with patch(
            "app.core.security.verify_password_reset_token",
            AsyncMock(return_value=user.id),
        ):
            await auth_service.reset_password("valid-token", "newpassword2")

        # Old password should fail.
        with pytest.raises(InvalidCredentialsError):
            await auth_service.authenticate(
                email="reset@example.com",
                password="oldpassword1",
            )

        # New password should work.
        authenticated = await auth_service.authenticate(
            email="reset@example.com",
            password="newpassword2",
        )
        assert authenticated is not None
        assert authenticated.id == user.id

    @pytest.mark.asyncio
    async def test_reset_password_invalid_token(self, session: AsyncSession) -> None:
        """Invalid or expired token should raise AuthenticationError."""
        auth_service = _auth_service(session)

        with patch(
            "app.core.security.verify_password_reset_token",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.reset_password("invalid-token", "newpassword2")

    @pytest.mark.asyncio
    async def test_reset_password_user_not_found(self, session: AsyncSession) -> None:
        """Token pointing to non-existent user should raise UserNotFoundError."""
        auth_service = _auth_service(session)

        with patch(
            "app.core.security.verify_password_reset_token",
            AsyncMock(return_value=uuid7()),
        ):
            with pytest.raises(UserNotFoundError):
                await auth_service.reset_password("orphan-token", "newpassword2")


class TestGetGoogleAuthUrl:
    """Tests for AuthService.get_google_auth_url."""

    @pytest.mark.asyncio
    async def test_get_google_auth_url_configured(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return a valid Google OAuth URL when configured."""
        monkeypatch.setattr(
            "app.services.auth_service.settings.GOOGLE_CLIENT_ID",
            "test-client-id",
        )

        auth_service = _auth_service(session)
        redirect_uri = "https://example.com/oauth/callback"

        with patch(
            "app.core.security.create_google_oauth_state",
            AsyncMock(return_value="test-state-token"),
        ):
            url = await auth_service.get_google_auth_url(redirect_uri)

        assert "https://accounts.google.com/o/oauth2/v2/auth" in url
        assert "client_id=test-client-id" in url
        assert "state=test-state-token" in url
        assert "redirect_uri=https%3A%2F%2Fexample.com%2Foauth%2Fcallback" in url

    @pytest.mark.asyncio
    async def test_get_google_auth_url_not_configured(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should raise AuthenticationError when GOOGLE_CLIENT_ID is empty."""
        monkeypatch.setattr("app.services.auth_service.settings.GOOGLE_CLIENT_ID", "")

        auth_service = _auth_service(session)

        with pytest.raises(AuthenticationError):
            await auth_service.get_google_auth_url("https://example.com/callback")


class TestGoogleLogin:
    """Tests for AuthService.google_login."""

    # Helpers

    @staticmethod
    def _make_token_response(
        status_code: int = 200,
        access_token: str | None = "google-access-token",
    ) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = {"access_token": access_token} if access_token else {}
        return resp

    @staticmethod
    def _make_userinfo_response(
        status_code: int = 200,
        user_id: str | None = "12345",
        email: str | None = "oauth@example.com",
        verified_email: bool = True,
        name: str | None = "OAuth User",
    ) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        body: dict = {}
        if user_id is not None:
            body["id"] = user_id
        if email is not None:
            body["email"] = email
        body["verified_email"] = verified_email
        if name is not None:
            body["name"] = name
        resp.json.return_value = body
        return resp

    @staticmethod
    def _setup_httpx_mock(
        token_resp: MagicMock,
        userinfo_resp: MagicMock,
    ) -> MagicMock:
        """Patch httpx.AsyncClient to return controlled responses."""
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()
        mock_client.post = AsyncMock(return_value=token_resp)
        mock_client.get = AsyncMock(return_value=userinfo_resp)
        return MagicMock(return_value=mock_client)

    # State verification

    @pytest.mark.asyncio
    async def test_google_login_state_verification_failure(
        self, session: AsyncSession
    ) -> None:
        """Invalid or expired state should raise AuthenticationError."""
        auth_service = _auth_service(session)

        with patch(
            "app.core.security.verify_google_oauth_state",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "bad-state", "http://cb")

    @pytest.mark.asyncio
    async def test_google_login_redirect_uri_mismatch(
        self, session: AsyncSession
    ) -> None:
        """State bound to a different redirect_uri should raise error."""
        auth_service = _auth_service(session)

        with patch(
            "app.core.security.verify_google_oauth_state",
            AsyncMock(return_value="https://expected.example.com"),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login(
                    "code", "state", "https://different.example.com"
                )

    # Token exchange

    @pytest.mark.asyncio
    async def test_google_login_token_exchange_failure(
        self, session: AsyncSession
    ) -> None:
        """Non-200 from Google token endpoint should raise AuthenticationError."""
        auth_service = _auth_service(session)
        bad_token = self._make_token_response(status_code=400)
        mock_async_client = self._setup_httpx_mock(
            bad_token,
            self._make_userinfo_response(),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "state", "http://cb")

    @pytest.mark.asyncio
    async def test_google_login_no_access_token(self, session: AsyncSession) -> None:
        """Missing access_token in Google response should raise error."""
        auth_service = _auth_service(session)
        no_token = self._make_token_response(access_token=None)
        mock_async_client = self._setup_httpx_mock(
            no_token,
            self._make_userinfo_response(),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "state", "http://cb")

    # Userinfo fetch

    @pytest.mark.asyncio
    async def test_google_login_userinfo_fetch_failure(
        self, session: AsyncSession
    ) -> None:
        """Non-200 from Google userinfo should raise AuthenticationError."""
        auth_service = _auth_service(session)
        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(status_code=400),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "state", "http://cb")

    @pytest.mark.asyncio
    async def test_google_login_unverified_email(self, session: AsyncSession) -> None:
        """Unverified Google email should raise AuthenticationError."""
        auth_service = _auth_service(session)
        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(verified_email=False),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "state", "http://cb")

    @pytest.mark.asyncio
    async def test_google_login_missing_google_id(self, session: AsyncSession) -> None:
        """Missing 'id' in userinfo should raise AuthenticationError."""
        auth_service = _auth_service(session)
        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(user_id=None),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "state", "http://cb")

    @pytest.mark.asyncio
    async def test_google_login_missing_email(self, session: AsyncSession) -> None:
        """Missing 'email' in userinfo should raise AuthenticationError."""
        auth_service = _auth_service(session)
        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(email=None),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.google_login("code", "state", "http://cb")

    # Successful flows

    @pytest.mark.asyncio
    async def test_google_login_new_user_registration(
        self, session: AsyncSession
    ) -> None:
        """First-time Google login should create a new user and return tokens."""
        auth_service = _auth_service(session)
        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(
                user_id="g-999",
                email="new-oauth@example.com",
                name="New OAuth",
            ),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            tokens = await auth_service.google_login("code", "state", "http://cb")

        assert tokens.access_token
        assert tokens.refresh_token
        assert tokens.token_type == "bearer"

        # Verify user was created in the database.
        db_user = await auth_service.user_repository.get_by_email(
            "new-oauth@example.com"
        )
        assert db_user is not None
        assert db_user.full_name == "New OAuth"
        assert db_user.is_verified is True

    @pytest.mark.asyncio
    async def test_google_login_existing_user_by_email(
        self, session: AsyncSession
    ) -> None:
        """Google login should link to an existing password-based account."""
        auth_service = _auth_service(session)

        # Create an existing password user.
        await auth_service.register(
            RegisterRequest(
                email="existing@example.com",
                password="password123",
            )
        )

        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(
                user_id="g-888",
                email="existing@example.com",
                name="Existing User",
            ),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            tokens = await auth_service.google_login("code", "state", "http://cb")

        assert tokens.access_token
        assert tokens.token_type == "bearer"

        # Verify OAuth account was linked.
        oauth = await auth_service.oauth_account_repository.get_by_provider(
            "google", "g-888"
        )
        assert oauth is not None
        assert oauth.provider_email == "existing@example.com"

    @pytest.mark.asyncio
    async def test_google_login_inactive_user(self, session: AsyncSession) -> None:
        """Inactive user should raise UserInactiveError."""
        auth_service = _auth_service(session)

        # Create an inactive user.
        with system_context():
            inactive = await auth_service.user_repository.create_user(
                email="inactive@example.com",
                password_hash=generate_password_hash("doesntmatter"),
                is_verified=True,
            )
            await auth_service.user_repository.update(inactive.id, {"is_active": False})

        mock_async_client = self._setup_httpx_mock(
            self._make_token_response(),
            self._make_userinfo_response(
                user_id="g-777",
                email="inactive@example.com",
            ),
        )

        with (
            patch(
                "app.core.security.verify_google_oauth_state",
                AsyncMock(return_value="http://cb"),
            ),
            patch("httpx.AsyncClient", mock_async_client),
        ):
            with pytest.raises(UserInactiveError):
                await auth_service.google_login("code", "state", "http://cb")


class TestMagicLink:
    """Tests for AuthService.send_magic_link and verify_magic_link."""

    @pytest.mark.asyncio
    async def test_send_magic_link_user_exists(self, session: AsyncSession) -> None:
        """Should queue a magic link email for an existing user."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="magic@example.com",
                password="password123",
            )
        )

        with (
            patch(
                "app.core.security.create_magic_link_token",
                AsyncMock(return_value="magic-test-token"),
            ),
            patch.object(auth_service, "_send_token_email", AsyncMock()) as mock_send,
        ):
            await auth_service.send_magic_link(user.email)

        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["user"] == user
        assert call_kwargs["url_path"] == "/auth/magic-link"
        assert call_kwargs["template_name"] == "magic_link.html"
        assert call_kwargs["url_field_name"] == "login_url"

    @pytest.mark.asyncio
    async def test_send_magic_link_user_not_found(self, session: AsyncSession) -> None:
        """Should silently do nothing for unknown emails."""
        auth_service = _auth_service(session)

        with patch.object(auth_service, "_send_token_email", AsyncMock()) as mock_send:
            await auth_service.send_magic_link("nobody@example.com")

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_magic_link_valid(self, session: AsyncSession) -> None:
        """Valid magic link token should return tokens."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="magic-verify@example.com",
                password="password123",
            )
        )

        with patch(
            "app.core.security.verify_magic_link_token",
            AsyncMock(return_value=user.id),
        ):
            tokens = await auth_service.verify_magic_link("valid-token")

        assert tokens.access_token
        assert tokens.refresh_token
        assert tokens.token_type == "bearer"

    @pytest.mark.asyncio
    async def test_verify_magic_link_invalid(self, session: AsyncSession) -> None:
        """Invalid or expired token should raise AuthenticationError."""
        auth_service = _auth_service(session)

        with patch(
            "app.core.security.verify_magic_link_token",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.verify_magic_link("invalid-token")

    @pytest.mark.asyncio
    async def test_verify_magic_link_inactive_user(self, session: AsyncSession) -> None:
        """Token pointing to inactive user should raise UserInactiveError."""
        auth_service = _auth_service(session)
        with system_context():
            user = await auth_service.user_repository.create_user(
                email="inactive@example.com",
                password_hash=generate_password_hash("doesntmatter"),
            )
            await auth_service.user_repository.update(user.id, {"is_active": False})

        with patch(
            "app.core.security.verify_magic_link_token",
            AsyncMock(return_value=user.id),
        ):
            with pytest.raises(UserInactiveError):
                await auth_service.verify_magic_link("token-for-inactive")


class TestEmailTokens:
    """Tests for send_verification_email and send_password_reset_email."""

    @pytest.mark.asyncio
    async def test_send_verification_email(self, session: AsyncSession) -> None:
        """Should queue a verification email for an existing user."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="verify@example.com",
                password="password123",
            )
        )

        with (
            patch(
                "app.core.security.create_email_verification_token",
                AsyncMock(return_value="verify-test-token"),
            ),
            patch.object(auth_service, "_send_token_email", AsyncMock()) as mock_send,
        ):
            await auth_service.send_verification_email(user)

        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["user"] == user
        assert call_kwargs["url_path"] == "/auth/verify-email"
        assert call_kwargs["template_name"] == "verification.html"
        assert call_kwargs["url_field_name"] == "verification_url"

    @pytest.mark.asyncio
    async def test_send_password_reset_email_user_exists(
        self, session: AsyncSession
    ) -> None:
        """Should queue a password reset email when user is found."""
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="pwreset@example.com",
                password="password123",
            )
        )

        with (
            patch(
                "app.core.security.create_password_reset_token",
                AsyncMock(return_value="reset-test-token"),
            ),
            patch.object(auth_service, "_send_token_email", AsyncMock()) as mock_send,
        ):
            await auth_service.send_password_reset_email(user.email)

        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["user"] == user
        assert call_kwargs["url_path"] == "/auth/reset-password"
        assert call_kwargs["template_name"] == "password_reset.html"
        assert call_kwargs["url_field_name"] == "reset_url"

    @pytest.mark.asyncio
    async def test_send_password_reset_email_user_not_found(
        self, session: AsyncSession
    ) -> None:
        """Should silently do nothing when email is unknown."""
        auth_service = _auth_service(session)

        with patch.object(auth_service, "_send_token_email", AsyncMock()) as mock_send:
            await auth_service.send_password_reset_email("unknown@example.com")

        mock_send.assert_not_called()


class TestTokenCooldownIsNotAnEnumerationOracle:
    """A throttled send must look identical to an unknown address.

    The per-user cooldown on `create_password_reset_token` and
    `create_magic_link_token` only fires for addresses that exist, so
    letting `TokenRateLimitError` surface defeated the "always 204"
    promise both endpoints document: submit an address twice, a 429 on
    the second attempt meant the account was real and two 204s meant it
    was not.
    """

    @pytest.mark.asyncio
    async def test_password_reset_swallows_the_cooldown(
        self, session: AsyncSession
    ) -> None:
        """A throttled reset must return, not raise.

        Args:
            session: Async database session fixture.
        """
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(email="throttled-reset@example.com", password="password123")
        )

        with patch.object(
            auth_service,
            "_send_token_email",
            AsyncMock(side_effect=TokenRateLimitError("cooldown")),
        ):
            await auth_service.send_password_reset_email(user.email)

    @pytest.mark.asyncio
    async def test_magic_link_swallows_the_cooldown(
        self, session: AsyncSession
    ) -> None:
        """A throttled magic link must return, not raise.

        Args:
            session: Async database session fixture.
        """
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(email="throttled-magic@example.com", password="password123")
        )

        with patch.object(
            auth_service,
            "_send_token_email",
            AsyncMock(side_effect=TokenRateLimitError("cooldown")),
        ):
            await auth_service.send_magic_link(user.email)
