"""Unit tests for `AuthService`."""

from typing import Any
from typing import cast
from unittest.mock import ANY
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid7

import httpx
import pytest
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenRateLimitError
from app.core.security import decode_token
from app.core.security import generate_password_hash
from app.core.settings import settings
from app.core.tenant import system_context
from app.core.tenant import tenant_context
from app.models.consent_record import MINIMUM_AGE_KIND
from app.models.user import UserRole
from app.repositories.consent_record_repository import ConsentRecordRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthenticationError
from app.services.auth_service import AuthService
from app.services.auth_service import EmailAlreadyExistsError
from app.services.auth_service import InvalidCredentialsError
from app.services.auth_service import MinimumAgeNotConfirmedError
from app.services.auth_service import OAuthProviderUnavailableError
from app.services.auth_service import UserInactiveError
from app.services.auth_service import UsernameAlreadyExistsError
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
    async def _authenticate_like_a_route_would(
        self,
        session: AsyncSession,
        **kwargs: Any,
    ) -> None:
        """Call `authenticate()` and roll back on failure, exactly like the
        real `get_async_session()` dependency does around a raised
        `HTTPException` (see `app/database.py`).

        `override_get_async_session_fixture` swaps in a bare passthrough
        for `get_async_session` in HTTP-level tests, which skips that
        commit/rollback wrapper entirely, so exercising it here is the
        only way to catch a fix that only holds up under the real
        dependency's behavior.
        """
        auth_service = _auth_service(session)
        try:
            await auth_service.authenticate(**kwargs)
        except Exception:
            await session.rollback()
            raise

    async def test_wrong_password_writes_login_failed_audit_entry(
        self, session: AsyncSession
    ) -> None:
        """A failed login for a real account must leave an audit trail that
        survives the rollback the route triggers by re-raising.

        Regression: `AuditAction.LOGIN_FAILED` was written inside a
        `begin_nested()` SAVEPOINT (`log_user_action_safe`) but nothing
        committed it before `authenticate()` raised, so the route's
        exception handler rolled the SAVEPOINT back along with everything
        else, silently discarding the audit trail this exists for.
        """
        from app.models.audit_log import AuditAction
        from app.models.audit_log import AuditLog

        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(email="wrong-pw-audit@example.com", password="password123")
        )

        with pytest.raises(InvalidCredentialsError):
            await self._authenticate_like_a_route_would(
                session,
                email="wrong-pw-audit@example.com",
                password="wrongpassword",
            )

        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGIN_FAILED,
                AuditLog.actor_id == str(user.id),
            )
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_unknown_email_writes_login_failed_audit_entry(
        self, session: AsyncSession
    ) -> None:
        """A failed login for an email with no account still leaves a trail,
        under a placeholder actor since no `user_id` exists to attribute it
        to, and it must survive the route's rollback the same way."""
        from app.models.audit_log import AuditAction
        from app.models.audit_log import AuditLog

        with pytest.raises(InvalidCredentialsError):
            await self._authenticate_like_a_route_would(
                session,
                email="no-such-account@example.com",
                password="password123",
            )

        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGIN_FAILED,
                AuditLog.actor_id == "unknown",
            )
        )
        entry = result.scalar_one_or_none()
        assert entry is not None
        # `actor_email` is an `EncryptedString` column, comparing it to a
        # plaintext value in a SQL `WHERE` clause can never match, since the
        # stored value is ciphertext, decryption only happens on Python-level
        # attribute access. Compared here instead of in the query above.
        assert entry.actor_email == "no-such-account@example.com"

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
    async def test_inactive_user_login_writes_login_failed_audit_entry(
        self, session: AsyncSession
    ) -> None:
        """A login attempt against a disabled account must also leave a
        trail, and it must survive the route's rollback.

        Regression: this branch previously raised with no audit call at
        all, a lockout attempt on a disabled account left no trace.
        """
        from app.models.audit_log import AuditAction
        from app.models.audit_log import AuditLog

        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(email="inactive-audit@example.com", password="password123")
        )
        with system_context():
            await auth_service.user_repository.update(user.id, {"is_active": False})

        with pytest.raises(UserInactiveError):
            await self._authenticate_like_a_route_would(
                session,
                email="inactive-audit@example.com",
                password="password123",
            )

        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == AuditAction.LOGIN_FAILED,
                AuditLog.actor_id == str(user.id),
            )
        )
        assert result.scalar_one_or_none() is not None

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

    @pytest.mark.asyncio
    async def test_register_stores_the_handle_and_its_normalised_form(
        self, session: AsyncSession
    ) -> None:
        """Registering with a handle should store both spellings of it."""
        auth_service = _auth_service(session)

        user = await auth_service.register(
            RegisterRequest(
                email="handle@example.com",
                password="password123",
                username="Thanásis",
            )
        )

        assert user.username == "Thanásis"
        assert user.username_normalized == "thanasis"

    @pytest.mark.asyncio
    async def test_register_existing_username_raises_error(
        self, session: AsyncSession
    ) -> None:
        """A handle already held should raise UsernameAlreadyExistsError.

        Compared on the normalised form, so a different spelling of the
        same handle is still a conflict rather than a second account.
        """
        auth_service = _auth_service(session)
        await auth_service.register(
            RegisterRequest(
                email="first-holder@example.com",
                password="password123",
                username="Thanásis",
            )
        )

        with pytest.raises(UsernameAlreadyExistsError):
            await auth_service.register(
                RegisterRequest(
                    email="second-holder@example.com",
                    password="password123",
                    username="thanasis",
                )
            )

    @pytest.mark.asyncio
    async def test_register_without_confirming_the_minimum_age_is_refused(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refused, and no account is left behind to be cleaned up."""
        monkeypatch.setattr(settings, "MINIMUM_AGE", 15)
        auth_service = _auth_service(session)

        with pytest.raises(MinimumAgeNotConfirmedError, match="15"):
            await auth_service.register(
                RegisterRequest(email="too-young@example.com", password="password123")
            )

        with system_context():
            assert (
                await UserRepository(session).get_by_email("too-young@example.com")
                is None
            )

    @pytest.mark.asyncio
    async def test_register_keeps_the_age_it_was_confirmed_against(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The age goes in as the document version, so a later change of
        the minimum shows in what each account actually agreed to."""
        monkeypatch.setattr(settings, "MINIMUM_AGE", 15)
        auth_service = _auth_service(session)

        user = await auth_service.register(
            RegisterRequest(
                email="old-enough@example.com",
                password="password123",
                is_minimum_age_confirmed=True,
            )
        )

        with system_context():
            current = await ConsentRecordRepository(session).current_for(user.id)
        assert current[MINIMUM_AGE_KIND].is_granted is True
        assert current[MINIMUM_AGE_KIND].document_version == "15"

    @pytest.mark.asyncio
    async def test_register_asks_nothing_when_there_is_no_minimum(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "MINIMUM_AGE", 0)
        auth_service = _auth_service(session)

        user = await auth_service.register(
            RegisterRequest(email="no-minimum@example.com", password="password123")
        )

        with system_context():
            current = await ConsentRecordRepository(session).current_for(user.id)
        assert MINIMUM_AGE_KIND not in current

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
        """Refreshing tokens should claim the used refresh token jti.

        The claim replaced a plain blacklist write. It has to happen
        exactly once, and it has to carry the token's own identifier and
        expiry so the entry expires with the token.
        """
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="refresh-blacklist@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)

        with patch(
            "app.services.auth_service.claim_token_for_rotation",
            AsyncMock(return_value=True),
        ) as mock_claim:
            refreshed_tokens = await auth_service.refresh_tokens(tokens.refresh_token)

        assert refreshed_tokens.access_token
        assert refreshed_tokens.refresh_token
        assert refreshed_tokens.refresh_token != tokens.refresh_token
        mock_claim.assert_awaited_once_with(ANY, ANY)

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


class TestPasswordChangesEndSessions:
    """A new password must sign out every token issued before it."""

    @staticmethod
    async def _register_with_tokens(
        auth_service: AuthService, email: str
    ) -> tuple[Any, Any]:
        """Register a user and issue them a token pair.

        Args:
            auth_service: Service under test.
            email: Email for the new user.

        Returns:
            The user and their tokens.
        """
        user = await auth_service.register(
            RegisterRequest(email=email, password="oldpassword1")
        )
        return user, auth_service.create_tokens(user)

    @pytest.mark.asyncio
    async def test_a_reset_refuses_earlier_refresh_tokens(
        self, session: AsyncSession
    ) -> None:
        """Someone holding a stolen token loses it once the owner resets."""
        auth_service = _auth_service(session)
        user, tokens = await self._register_with_tokens(
            auth_service, "reset-ends@example.com"
        )

        with (
            patch(
                "app.core.security.verify_password_reset_token",
                AsyncMock(return_value=user.id),
            ),
            patch(
                "app.services.auth_service.is_token_blacklisted",
                AsyncMock(return_value=False),
            ),
        ):
            await auth_service.reset_password("valid-token", "newpassword2")
            with pytest.raises(AuthenticationError, match="password change"):
                await auth_service.refresh_tokens(tokens.refresh_token)

    @pytest.mark.asyncio
    async def test_a_change_refuses_earlier_refresh_tokens(
        self, session: AsyncSession
    ) -> None:
        """Changing the password signs out the other devices too."""
        auth_service = _auth_service(session)
        user, tokens = await self._register_with_tokens(
            auth_service, "change-ends@example.com"
        )

        with patch(
            "app.services.auth_service.is_token_blacklisted",
            AsyncMock(return_value=False),
        ):
            # A signed-in request carries the user's tenant.
            with tenant_context(user.tenant_id):
                await auth_service.change_password(user, "oldpassword1", "newpassword2")
            with pytest.raises(AuthenticationError, match="password change"):
                await auth_service.refresh_tokens(tokens.refresh_token)


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


APPLE_CLAIMS = {
    "sub": "001234.apple-subject",
    "email": "apple@example.com",
    "email_verified": "true",
}


def _verified_apple(claims: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
    """Patch Apple token verification to accept with `claims`."""
    return patch(
        "app.core.security.verify_apple_identity_token",
        AsyncMock(return_value=APPLE_CLAIMS if claims is None else claims),
    )


@pytest.fixture
def apple_native(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the native Apple flow."""
    monkeypatch.setattr(
        "app.services.auth_service.settings.APPLE_BUNDLE_ID", "com.example.app"
    )


@pytest.fixture
def apple_web(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the Apple web flow."""
    monkeypatch.setattr(
        "app.services.auth_service.settings.APPLE_CLIENT_ID", "com.example.web"
    )
    monkeypatch.setattr(
        "app.services.auth_service.settings.APPLE_PRIVATE_KEY", "test-private-key"
    )


class TestAppleNativeLogin:
    """Tests for AuthService.apple_native_login."""

    @pytest.mark.asyncio
    async def test_not_configured(self, session: AsyncSession) -> None:
        """Without a bundle ID the flow is unavailable, not a bad request."""
        with pytest.raises(OAuthProviderUnavailableError):
            await _auth_service(session).apple_native_login("token", "nonce")

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_new_user_is_registered_and_linked(
        self, session: AsyncSession
    ) -> None:
        """A first Apple sign-in creates a verified account with Apple's name."""
        auth_service = _auth_service(session)

        with _verified_apple() as verify:
            tokens = await auth_service.apple_native_login(
                "token", "nonce", "Apple User"
            )

        verify.assert_awaited_once_with("token", "com.example.app", "nonce")
        assert tokens.access_token
        user = await auth_service.user_repository.get_by_email("apple@example.com")
        assert user is not None
        assert (user.full_name, user.is_verified) == ("Apple User", True)
        link = await auth_service.oauth_account_repository.get_by_provider(
            "apple", "001234.apple-subject"
        )
        assert link is not None
        assert link.user_id == user.id

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_existing_account_is_linked_by_email(
        self, session: AsyncSession
    ) -> None:
        """An account with the same email gets Apple linked to it."""
        auth_service = _auth_service(session)
        existing = await auth_service.register(
            RegisterRequest(email="apple@example.com", password="password123")
        )

        with _verified_apple():
            await auth_service.apple_native_login("token", "nonce")

        link = await auth_service.oauth_account_repository.get_by_provider(
            "apple", "001234.apple-subject"
        )
        assert link is not None
        assert link.user_id == existing.id

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_linked_identity_signs_in_without_an_email(
        self, session: AsyncSession
    ) -> None:
        """A later token without an email still reaches the linked account."""
        auth_service = _auth_service(session)
        with _verified_apple():
            await auth_service.apple_native_login("token", "nonce")

        with _verified_apple({"sub": "001234.apple-subject"}):
            tokens = await auth_service.apple_native_login("token", "nonce")

        user = await auth_service.user_repository.get_by_email("apple@example.com")
        assert user is not None
        assert decode_token(tokens.access_token)["sub"] == str(user.id)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_unlinked_identity_without_an_email_is_refused(
        self, session: AsyncSession
    ) -> None:
        """No link and no email leaves nothing to sign in to."""
        with _verified_apple({"sub": "001234.apple-subject"}):
            with pytest.raises(AuthenticationError, match="No email"):
                await _auth_service(session).apple_native_login("token", "nonce")

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_unverified_email_is_refused(self, session: AsyncSession) -> None:
        """An email Apple has not verified cannot claim an account."""
        claims = {**APPLE_CLAIMS, "email_verified": "false"}

        with _verified_apple(claims):
            with pytest.raises(AuthenticationError, match="not verified"):
                await _auth_service(session).apple_native_login("token", "nonce")

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_invalid_token_is_an_authentication_error(
        self, session: AsyncSession
    ) -> None:
        """A token that fails verification is a bad request, not an outage."""
        with patch(
            "app.core.security.verify_apple_identity_token",
            AsyncMock(side_effect=InvalidTokenError("bad")),
        ):
            with pytest.raises(AuthenticationError) as raised:
                await _auth_service(session).apple_native_login("token", "nonce")

        assert not isinstance(raised.value, OAuthProviderUnavailableError)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_native")
    async def test_apple_unreachable_is_unavailable(
        self, session: AsyncSession
    ) -> None:
        """Failing to fetch Apple's keys is surfaced as an outage."""
        with patch(
            "app.core.security.verify_apple_identity_token",
            AsyncMock(side_effect=httpx.ConnectError("down")),
        ):
            with pytest.raises(OAuthProviderUnavailableError):
                await _auth_service(session).apple_native_login("token", "nonce")


class TestAppleWebLogin:
    """Tests for AuthService.get_apple_auth_url and apple_login."""

    @staticmethod
    def _apple_token_endpoint(status_code: int = 200, body: Any = None) -> Any:  # noqa: ANN401
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = {"id_token": "token"} if body is None else body
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock()
        client.post = AsyncMock(return_value=response)
        return patch("httpx.AsyncClient", MagicMock(return_value=client))

    @staticmethod
    def _state(redirect_uri: str | None = "https://app.example.com/cb") -> Any:  # noqa: ANN401
        return patch(
            "app.core.security.verify_apple_oauth_state",
            AsyncMock(return_value=redirect_uri),
        )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_web")
    async def test_auth_url(self, session: AsyncSession) -> None:
        """The URL asks for a form post with the name and email."""
        with patch(
            "app.core.security.create_apple_oauth_state",
            AsyncMock(return_value="state-token"),
        ):
            url = await _auth_service(session).get_apple_auth_url(
                "https://app.example.com/cb"
            )

        assert url.startswith("https://appleid.apple.com/auth/authorize?")
        assert "client_id=com.example.web" in url
        assert "response_mode=form_post" in url
        assert "scope=name+email" in url
        assert "state=state-token" in url

    @pytest.mark.asyncio
    async def test_not_configured(self, session: AsyncSession) -> None:
        """Without a Services ID and key both web steps are unavailable."""
        auth_service = _auth_service(session)

        with pytest.raises(OAuthProviderUnavailableError):
            await auth_service.get_apple_auth_url("https://app.example.com/cb")
        with pytest.raises(OAuthProviderUnavailableError):
            await auth_service.apple_login(
                "code", "state", "https://app.example.com/cb"
            )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_web")
    @pytest.mark.parametrize(
        "stored_redirect_uri",
        [None, "https://elsewhere.example.com/cb"],
        ids=["unknown state", "another redirect"],
    )
    async def test_state_must_match(
        self, session: AsyncSession, stored_redirect_uri: str | None
    ) -> None:
        """A state that is unknown or bound elsewhere is refused."""
        with self._state(stored_redirect_uri):
            with pytest.raises(AuthenticationError):
                await _auth_service(session).apple_login(
                    "code", "state", "https://app.example.com/cb"
                )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_web")
    @pytest.mark.parametrize(
        ("status_code", "body"),
        [(400, {"error": "invalid_grant"}), (200, {})],
        ids=["refused", "no identity token"],
    )
    async def test_failed_exchange_is_refused(
        self, session: AsyncSession, status_code: int, body: dict[str, Any]
    ) -> None:
        """A refused code or an answer without a token signs nobody in."""
        with (
            self._state(),
            self._apple_token_endpoint(status_code, body),
            patch("app.core.security.create_apple_client_secret", return_value="s"),
        ):
            with pytest.raises(AuthenticationError) as raised:
                await _auth_service(session).apple_login(
                    "code", "state", "https://app.example.com/cb"
                )

        assert not isinstance(raised.value, OAuthProviderUnavailableError)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("apple_web")
    async def test_exchanged_token_is_verified_for_the_services_id(
        self, session: AsyncSession
    ) -> None:
        """The web flow's token is checked against the Services ID."""
        auth_service = _auth_service(session)

        with (
            self._state(),
            self._apple_token_endpoint(),
            patch("app.core.security.create_apple_client_secret", return_value="s"),
            _verified_apple() as verify,
        ):
            tokens = await auth_service.apple_login(
                "code", "state", "https://app.example.com/cb", "Web User"
            )

        verify.assert_awaited_once_with("token", "com.example.web", None)
        assert tokens.access_token
        user = await auth_service.user_repository.get_by_email("apple@example.com")
        assert user is not None
        assert user.full_name == "Web User"


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


class TestRefreshRotationIsAtomic:
    """Only one caller may exchange a given refresh token.

    Rotation checked the blacklist and wrote to it as two separate steps
    with the whole token mint in between. Two requests carrying the same
    refresh token both read "not blacklisted" and both walked away with a
    fresh pair, so a stolen token replayed alongside the real one worked
    and the blacklist caught nothing.
    """

    @pytest.mark.asyncio
    async def test_concurrent_refreshes_yield_one_winner(
        self, session: AsyncSession
    ) -> None:
        """Two simultaneous refreshes of one token: one succeeds, one fails.

        Args:
            session: Async database session fixture.
        """
        import asyncio

        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="refresh-race@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)

        claimed: set[str] = set()

        async def _claim(token_identifier: str, expires_at: object) -> bool:
            # Stand in for Redis SET NX, including a suspension point at
            # exactly the spot the old two-step version was vulnerable.
            already = token_identifier in claimed
            await asyncio.sleep(0)
            if already:
                return False
            claimed.add(token_identifier)
            return True

        with patch(
            "app.services.auth_service.claim_token_for_rotation",
            AsyncMock(side_effect=_claim),
        ):
            results = await asyncio.gather(
                auth_service.refresh_tokens(tokens.refresh_token),
                auth_service.refresh_tokens(tokens.refresh_token),
                return_exceptions=True,
            )

        succeeded = [r for r in results if not isinstance(r, BaseException)]
        rejected = [r for r in results if isinstance(r, AuthenticationError)]

        assert len(succeeded) == 1, results
        assert len(rejected) == 1, results

    @pytest.mark.asyncio
    async def test_a_lost_claim_is_rejected(self, session: AsyncSession) -> None:
        """Losing the claim must not still return a token pair.

        Args:
            session: Async database session fixture.
        """
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="refresh-lost-claim@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)

        with patch(
            "app.services.auth_service.claim_token_for_rotation",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(AuthenticationError):
                await auth_service.refresh_tokens(tokens.refresh_token)

    @pytest.mark.asyncio
    async def test_a_rejected_refresh_does_not_burn_the_token(
        self, session: AsyncSession
    ) -> None:
        """Validation runs before the claim, so a failure leaves it usable.

        Args:
            session: Async database session fixture.
        """
        auth_service = _auth_service(session)
        user = await auth_service.register(
            RegisterRequest(
                email="refresh-not-burned@example.com",
                password="password123",
            )
        )
        tokens = auth_service.create_tokens(user)

        with system_context():
            await auth_service.user_repository.update(user.id, {"is_active": False})

        with patch(
            "app.services.auth_service.claim_token_for_rotation",
            AsyncMock(return_value=True),
        ) as mock_claim:
            # `refresh_tokens` converts `UserInactiveError` on its way out.
            with pytest.raises(AuthenticationError):
                await auth_service.refresh_tokens(tokens.refresh_token)

        mock_claim.assert_not_awaited()
