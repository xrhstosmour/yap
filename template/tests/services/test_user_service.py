"""Unit tests for `UserService`."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4
from uuid import uuid7

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import system_context
from app.models.user import User
from app.models.user import UserRole
from app.schemas.user import UserCreate
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe
from app.services.user_service import UserService
from app.services.user_service import UserServiceError


def _user_service(session: AsyncSession) -> UserService:
    return UserService(cast(AsyncSession, session))


class TestUserServiceCreate:
    """Tests for UserService create operations."""

    @pytest.mark.asyncio
    async def test_create_user(self, session: AsyncSession) -> None:
        """User should be created successfully."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
            full_name="Test User",
        )

        user_service = _user_service(session)
        user = await user_service.create(user_create)

        assert user.email == "test@example.com"
        assert user.full_name == "Test User"
        assert user.id is not None

    @pytest.mark.asyncio
    async def test_create_user_hashes_password(self, session: AsyncSession) -> None:
        """User password should be hashed on creation."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
        )

        user_service = _user_service(session)
        user = await user_service.create(user_create)

        assert user.hashed_password != "password123"

    @pytest.mark.asyncio
    async def test_create_user_succeeds_when_audit_log_write_fails(
        self, session: AsyncSession
    ) -> None:
        """User creation should succeed even if the audit-log write fails."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
        )

        user_service = _user_service(session)
        user_service.audit_repository.log_user_action = AsyncMock(
            side_effect=Exception("db unavailable")
        )

        user = await user_service.create(user_create)

        assert user.email == "test@example.com"


class TestUserServiceGet:
    """Tests for UserService get operations."""

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession) -> None:
        """User should be retrieved by ID."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
        )

        user_service = _user_service(session)
        with system_context():
            created = await user_service.create(user_create)
            retrieved = await user_service.get_by_id(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, session: AsyncSession) -> None:
        """Non-existent ID should return None."""
        user_service = _user_service(session)
        with system_context():
            retrieved = await user_service.get_by_id(uuid7())

        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_by_email(self, session: AsyncSession) -> None:
        """User should be retrieved by email."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
        )

        user_service = _user_service(session)
        created = await user_service.create(user_create)
        retrieved = await user_service.get_by_email("test@example.com")

        assert retrieved is not None
        assert retrieved.email == created.email

    @pytest.mark.asyncio
    async def test_get_by_email_not_found(self, session: AsyncSession) -> None:
        """Non-existent email should return None."""
        user_service = _user_service(session)
        retrieved = await user_service.get_by_email("nonexistent@example.com")

        assert retrieved is None


class TestUserServiceUpdate:
    """Tests for UserService update operations."""

    @pytest.mark.asyncio
    async def test_update_user(self, session: AsyncSession) -> None:
        """User should be updated successfully."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
        )

        user_service = _user_service(session)
        with system_context():
            created = await user_service.create(user_create)

            update_data = UserUpdate(full_name="Updated Name")
            updated = await user_service.update(
                created.id, update_data, updated_by=uuid7()
            )

        assert updated is not None
        assert updated.full_name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_user_not_found(self, session: AsyncSession) -> None:
        """Updating non-existent user should raise error."""
        user_service = _user_service(session)
        update_data = UserUpdate(full_name="Updated Name")

        with system_context():
            updated = await user_service.update(
                uuid7(), update_data, updated_by=uuid7()
            )

        assert updated is None


class TestUserServiceDelete:
    """Tests for UserService delete operations."""

    @pytest.mark.asyncio
    async def test_delete_user(self, session: AsyncSession) -> None:
        """User should be soft deleted."""
        user_create = UserCreate(
            email="test@example.com",
            password="password123",
        )

        user_service = _user_service(session)
        with system_context():
            created = await user_service.create(user_create)

            await user_service.delete(created.id, deleted_by=uuid7())

            # Should not be found after deletion.
            retrieved = await user_service.get_by_id(created.id)

        assert retrieved is None


# ============================================================================ #
# Mock-based unit tests
# ============================================================================ #


@pytest.fixture
def mock_session() -> MagicMock:
    """Build a MagicMock simulating an AsyncSession."""
    session = MagicMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock()
    nested.__aexit__ = AsyncMock()
    session.begin_nested.return_value = nested
    return session


@pytest.fixture
def mock_user_service(mock_session: MagicMock) -> UserService:
    """Build a UserService with mocked repositories."""
    svc = UserService(mock_session)
    svc.user_repository = MagicMock()
    svc.audit_repository = MagicMock()
    return svc


def _make_user_mock(**overrides: object) -> MagicMock:
    """Build a MagicMock with common User attributes."""
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.phone = None
    user.hashed_password = "$2b$12$hashedsecretvalueherehashedsecr"
    user.is_active = True
    user.is_verified = False
    user.token_version = 1
    user.tenant_id = uuid4()
    user.created_at = datetime.now(UTC)
    user.updated_at = datetime.now(UTC)
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


# update_me / update_profile


class TestUpdateProfile:
    """Tests for UserService.update_profile() (update_me)."""

    @pytest.mark.asyncio
    async def test_update_full_name(self, mock_user_service: UserService) -> None:
        """Should update the user's full_name field."""
        user = _make_user_mock(full_name="Old Name")
        updated = _make_user_mock(full_name="New Name", id=user.id)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(full_name="New Name")
        result = await mock_user_service.update_profile(user, data)

        assert result.full_name == "New Name"
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user.id, {"full_name": "New Name"}
        )
        mock_user_service.user_repository.get.assert_awaited_once_with(user.id)

    @pytest.mark.asyncio
    async def test_update_email(self, mock_user_service: UserService) -> None:
        """Should update the user's email field."""
        user = _make_user_mock(email="old@example.com")
        updated = _make_user_mock(email="new@example.com", id=user.id)
        mock_user_service.user_repository.get_by_email = AsyncMock(return_value=None)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(
            email="new@example.com", current_password="correct-password"
        )
        with patch("app.services.user_service.verify_password", return_value=True):
            result = await mock_user_service.update_profile(user, data)

        assert result.email == "new@example.com"
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user.id, {"email": "new@example.com"}
        )

    @pytest.mark.asyncio
    async def test_update_both_fields(self, mock_user_service: UserService) -> None:
        """Should update both full_name and email simultaneously."""
        user = _make_user_mock(full_name="Old", email="old@example.com")
        updated = _make_user_mock(full_name="New", email="new@example.com", id=user.id)
        mock_user_service.user_repository.get_by_email = AsyncMock(return_value=None)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(
            full_name="New",
            email="new@example.com",
            current_password="correct-password",
        )
        with patch("app.services.user_service.verify_password", return_value=True):
            result = await mock_user_service.update_profile(user, data)

        assert result.full_name == "New"
        assert result.email == "new@example.com"
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user.id, {"email": "new@example.com", "full_name": "New"}
        )

    @pytest.mark.asyncio
    async def test_no_changes_refreshes_and_returns_user(
        self, mock_user_service: UserService
    ) -> None:
        """Should refresh from DB and return user when no fields are set."""
        user = _make_user_mock()
        mock_user_service.user_repository.get = AsyncMock(return_value=user)

        data = UserUpdateMe.model_construct()  # no fields set
        result = await mock_user_service.update_profile(user, data)

        assert result is user
        mock_user_service.user_repository.update.assert_not_called()
        mock_user_service.user_repository.get.assert_awaited_once_with(user.id)

    @pytest.mark.asyncio
    async def test_update_phone(self, mock_user_service: UserService) -> None:
        """Should update the user's phone number."""
        user = _make_user_mock(phone=None)
        updated = _make_user_mock(phone="+306912345678", id=user.id)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(phone="+306912345678")
        result = await mock_user_service.update_profile(user, data)

        assert result.phone == "+306912345678"
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user.id, {"phone": "+306912345678"}
        )

    @pytest.mark.asyncio
    async def test_update_phone_clears_phone(
        self, mock_user_service: UserService
    ) -> None:
        """Should clear the phone field when phone is explicitly set to None."""
        user = _make_user_mock(phone="+306912345678")
        updated = _make_user_mock(phone=None, id=user.id)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(phone=None)
        result = await mock_user_service.update_profile(user, data)

        assert result.phone is None
        # phone=None in model_fields_set means the user wants to clear it.
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user.id, {"phone": None}
        )

    @pytest.mark.asyncio
    async def test_update_phone_called(self, mock_user_service: UserService) -> None:
        """Phone field triggers update."""
        user = _make_user_mock(phone=None)
        updated = _make_user_mock(phone="+12025551234", id=user.id)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(phone="+12025551234")
        result = await mock_user_service.update_profile(user, data)

        assert result.phone == "+12025551234"
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user.id, {"phone": "+12025551234"}
        )

    @pytest.mark.asyncio
    async def test_update_password_bumps_token_version_atomically(
        self, mock_user_service: UserService
    ) -> None:
        """token_version must be a SQL expression, not a stale Python read.

        `User.token_version + 1` (the class attribute) compiles to
        `SET token_version = token_version + 1`, atomic against a concurrent
        bump. `user.token_version + 1` (the loaded instance's value) would
        instead write a fixed number computed from a value that could already
        be stale by the time this UPDATE runs, silently losing a concurrent
        increment.
        """
        user = _make_user_mock(hashed_password="$2b$12$oldhash", token_version=5)
        updated = _make_user_mock(id=user.id, token_version=6)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.user_repository.get = AsyncMock(return_value=updated)

        data = UserUpdateMe.model_construct(
            new_password="new-strong-password",
            current_password="correct-password",
        )
        with patch("app.services.user_service.verify_password", return_value=True):
            await mock_user_service.update_profile(user, data)

        mock_user_service.user_repository.update.assert_awaited_once()
        call_data = mock_user_service.user_repository.update.await_args.args[1]
        assert str(call_data["token_version"]) == str(User.token_version + 1)


# delete_me


class TestDeleteMe:
    """Tests for UserService.delete_me() — GDPR account deletion."""

    @pytest.mark.asyncio
    async def test_delete_me_revokes_api_keys(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should revoke all active API keys via a bulk UPDATE statement."""
        user = _make_user_mock()

        mock_session.execute = AsyncMock()
        mock_user_service.user_repository.update = AsyncMock()
        mock_user_service.user_repository.increment_token_version = AsyncMock()
        mock_user_service.user_repository.delete = AsyncMock()
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.services.user_service.generate_password_hash",
            return_value="$2b$12$placeholder_hash...",
        ):
            await mock_user_service.delete_me(user)

        # API key revocation, recovery-code deletion and WebAuthn-credential
        # deletion statements were all issued.
        assert mock_session.execute.await_count == 3
        tables = {
            call.args[0].table.name for call in mock_session.execute.await_args_list
        }
        assert tables == {"api_keys", "totp_recovery_codes", "webauthn_credentials"}
        mock_user_service.audit_repository.log_user_action_safe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_me_deletes_totp_recovery_codes_and_webauthn_credentials(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should hard-delete 2FA credential material, not just soft-delete it."""
        user = _make_user_mock()

        mock_session.execute = AsyncMock()
        mock_user_service.user_repository.update = AsyncMock()
        mock_user_service.user_repository.increment_token_version = AsyncMock()
        mock_user_service.user_repository.delete = AsyncMock()
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.services.user_service.generate_password_hash",
            return_value="$2b$12$placeholder_hash...",
        ):
            await mock_user_service.delete_me(user)

        statements = [call.args[0] for call in mock_session.execute.await_args_list]
        recovery_code_delete = next(
            s for s in statements if s.table.name == "totp_recovery_codes"
        )
        webauthn_delete = next(
            s for s in statements if s.table.name == "webauthn_credentials"
        )
        assert recovery_code_delete.is_delete
        assert webauthn_delete.is_delete

    @pytest.mark.asyncio
    async def test_delete_me_anonymizes_personal_fields(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should replace email, phone, full_name, and hashed_password with
        GDPR-compliant placeholders, and clear 2FA state."""
        user = _make_user_mock(phone="+306912345678")

        mock_session.execute = AsyncMock()
        mock_user_service.user_repository.update = AsyncMock()
        mock_user_service.user_repository.increment_token_version = AsyncMock()
        mock_user_service.user_repository.delete = AsyncMock()
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.services.user_service.generate_password_hash",
            return_value="$2b$12$placeholder_hash...",
        ):
            await mock_user_service.delete_me(user)

        expected_email = f"deleted_{user.id}@deleted.invalid"
        mock_user_service.user_repository.update.assert_awaited_once()
        call = mock_user_service.user_repository.update.await_args
        assert call is not None
        assert call.args[0] == user.id
        data = call.args[1]
        assert data["email"] == expected_email
        assert data["phone"] is None
        assert data["full_name"] is None
        assert data["hashed_password"] == "$2b$12$placeholder_hash..."
        assert data["is_2fa_enabled"] is False
        assert data["totp_secret_encrypted"] is None
        assert data["totp_confirmed_at"] is None
        assert "token_version" in data

    @pytest.mark.asyncio
    async def test_delete_me_invalidates_tokens_and_soft_deletes(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should invalidate tokens atomically with anonymisation and soft-delete."""
        user = _make_user_mock()

        mock_session.execute = AsyncMock()
        mock_user_service.user_repository.update = AsyncMock()
        mock_user_service.user_repository.increment_token_version = AsyncMock()
        mock_user_service.user_repository.delete = AsyncMock()
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.services.user_service.generate_password_hash",
            return_value="$2b$12$placeholder...",
        ):
            await mock_user_service.delete_me(user)

        mock_user_service.user_repository.increment_token_version.assert_not_called()
        mock_user_service.user_repository.delete.assert_awaited_once_with(user.id)

    @pytest.mark.asyncio
    async def test_delete_me_logs_audit_entry(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should write an ACCOUNT_DELETION audit log entry."""
        user = _make_user_mock(tenant_id=uuid4())

        mock_session.execute = AsyncMock()
        mock_user_service.user_repository.update = AsyncMock()
        mock_user_service.user_repository.increment_token_version = AsyncMock()
        mock_user_service.user_repository.delete = AsyncMock()
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.services.user_service.generate_password_hash",
            return_value="$2b$12$placeholder...",
        ):
            await mock_user_service.delete_me(user)

        mock_user_service.audit_repository.log_user_action_safe.assert_awaited_once()
        call_kwargs = (
            mock_user_service.audit_repository.log_user_action_safe.call_args.kwargs
        )
        assert call_kwargs["user_id"] == user.id
        assert call_kwargs["resource_type"] == "user"
        assert call_kwargs["resource_id"] == str(user.id)


class TestRevokeAllSessions:
    """Tests for UserService.revoke_all_sessions()."""

    @pytest.mark.asyncio
    async def test_revoke_all_sessions_success(
        self, mock_user_service: UserService
    ) -> None:
        """Should increment token version and log audit entry."""
        target_user = _make_user_mock()
        revoked_by = _make_user_mock()

        mock_user_service.user_repository.get = AsyncMock(return_value=target_user)
        mock_user_service.user_repository.increment_token_version = AsyncMock()
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        result = await mock_user_service.revoke_all_sessions(
            user_id=target_user.id,
            revoked_by=revoked_by,
        )

        assert result is True
        mock_user_service.user_repository.increment_token_version.assert_awaited_once_with(
            target_user.id
        )
        mock_user_service.audit_repository.log_user_action_safe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_all_sessions_user_not_found(
        self, mock_user_service: UserService
    ) -> None:
        """Should return False when target user does not exist."""
        revoked_by = _make_user_mock()
        mock_user_service.user_repository.get = AsyncMock(return_value=None)

        result = await mock_user_service.revoke_all_sessions(
            user_id=uuid4(),
            revoked_by=revoked_by,
        )

        assert result is False
        mock_user_service.user_repository.increment_token_version.assert_not_called()


# export_my_data


class TestExportMyData:
    """Tests for UserService.export_my_data() — GDPR data export."""

    @pytest.mark.asyncio
    async def test_export_returns_profile_data(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should include profile fields in the exported data."""
        from app.models.audit_log import AuditLog

        user = _make_user_mock(
            email="export@example.com",
            full_name="Export User",
        )

        mock_log = MagicMock(spec=AuditLog)
        mock_log.action = "login"
        mock_log.resource_type = "user"
        mock_log.resource_id = str(user.id)
        mock_log.status = "success"
        mock_log.created_at = datetime.now(UTC)
        mock_user_service.audit_repository.get_by_actor = AsyncMock(
            return_value=([], 0)
        )

        mock_api_key_repo = MagicMock()
        mock_api_key_repo.list = AsyncMock(return_value=([], 0))

        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.repositories.api_key_repository.APIKeyRepository",
            return_value=mock_api_key_repo,
        ):
            result = await mock_user_service.export_my_data(user)

        assert result["profile"]["id"] == str(user.id)
        assert result["profile"]["email"] == "export@example.com"
        assert result["profile"]["full_name"] == "Export User"
        assert result["profile"]["is_active"] is True
        assert result["profile"]["is_verified"] is False

    @pytest.mark.asyncio
    async def test_export_includes_api_keys(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should include API key metadata (no secrets) in exported data."""
        user = _make_user_mock()

        mock_user_service.audit_repository.get_by_actor = AsyncMock(
            return_value=([], 0)
        )

        now = datetime.now(UTC)
        mock_api_key = MagicMock()
        mock_api_key.name = "Production Key"
        mock_api_key.description = "Used for CI/CD"
        mock_api_key.scopes = ["read", "write"]
        mock_api_key.is_active = True
        mock_api_key.created_at = now
        mock_api_key.last_used_at = now
        mock_api_key.expires_at = None

        mock_api_key_repo = MagicMock()
        mock_api_key_repo.list = AsyncMock(return_value=([mock_api_key], 1))

        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.repositories.api_key_repository.APIKeyRepository",
            return_value=mock_api_key_repo,
        ):
            result = await mock_user_service.export_my_data(user)

        assert len(result["api_keys"]) == 1
        exported_key = result["api_keys"][0]
        assert exported_key["name"] == "Production Key"
        assert exported_key["description"] == "Used for CI/CD"
        assert exported_key["scopes"] == ["read", "write"]
        assert exported_key["is_active"] is True
        # Secrets must never be included.
        assert "key_hash" not in exported_key
        assert "key_id" not in exported_key

    @pytest.mark.asyncio
    async def test_export_includes_activity_logs(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should include recent audit activity in exported data."""
        from app.models.audit_log import AuditLog

        user = _make_user_mock()

        log1 = MagicMock(spec=AuditLog)
        log1.action = "login"
        log1.resource_type = "session"
        log1.resource_id = "sess-1"
        log1.status = "success"
        log1.created_at = datetime.now(UTC)

        log2 = MagicMock(spec=AuditLog)
        log2.action = "apikey_create"
        log2.resource_type = "api_key"
        log2.resource_id = "key-1"
        log2.status = "success"
        log2.created_at = datetime.now(UTC)

        mock_user_service.audit_repository.get_by_actor = AsyncMock(
            return_value=([log1, log2], 2)
        )

        mock_api_key_repo = MagicMock()
        mock_api_key_repo.list = AsyncMock(return_value=([], 0))

        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.repositories.api_key_repository.APIKeyRepository",
            return_value=mock_api_key_repo,
        ):
            result = await mock_user_service.export_my_data(user)

        assert len(result["activity"]) == 2
        assert result["activity"][0]["action"] == "login"
        assert result["activity"][0]["resource_type"] == "session"
        assert result["activity"][1]["action"] == "apikey_create"

    @pytest.mark.asyncio
    async def test_export_logs_audit_entry(
        self, mock_user_service: UserService, mock_session: MagicMock
    ) -> None:
        """Should write a DATA_EXPORT audit log entry."""
        user = _make_user_mock()

        mock_user_service.audit_repository.get_by_actor = AsyncMock(
            return_value=([], 0)
        )

        mock_api_key_repo = MagicMock()
        mock_api_key_repo.list = AsyncMock(return_value=([], 0))

        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        with patch(
            "app.repositories.api_key_repository.APIKeyRepository",
            return_value=mock_api_key_repo,
        ):
            await mock_user_service.export_my_data(user)

        mock_user_service.audit_repository.log_user_action_safe.assert_awaited_once()


# list_users


class TestListUsers:
    """Tests for UserService.list_users()."""

    @pytest.mark.asyncio
    async def test_search_delegates_to_search_repository(
        self, mock_user_service: UserService
    ) -> None:
        """When a search term is provided, delegate to repository.search."""
        mock_user_service.user_repository.search = AsyncMock(return_value=([], 0))

        await mock_user_service.list_users(search="john")

        mock_user_service.user_repository.search.assert_awaited_once_with(
            "john", skip=0, limit=20
        )
        mock_user_service.user_repository.list.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_active_filter(self, mock_user_service: UserService) -> None:
        """Should pass is_active filter to repository.list."""
        mock_user_service.user_repository.list = AsyncMock(return_value=([], 0))

        await mock_user_service.list_users(is_active=True)

        mock_user_service.user_repository.list.assert_awaited_once_with(
            skip=0, limit=20, filters={"is_active": True}
        )

    @pytest.mark.asyncio
    async def test_role_filter(self, mock_user_service: UserService) -> None:
        """Should pass role filter to repository.list."""
        mock_user_service.user_repository.list = AsyncMock(return_value=([], 0))

        await mock_user_service.list_users(role=UserRole.SUPERUSER)

        mock_user_service.user_repository.list.assert_awaited_once_with(
            skip=0, limit=20, filters={"role": UserRole.SUPERUSER}
        )

    @pytest.mark.asyncio
    async def test_pagination_params(self, mock_user_service: UserService) -> None:
        """Should forward skip and limit to repository.list."""
        mock_user_service.user_repository.list = AsyncMock(return_value=([], 100))

        await mock_user_service.list_users(skip=10, limit=5)

        mock_user_service.user_repository.list.assert_awaited_once_with(
            skip=10, limit=5, filters=None
        )

    @pytest.mark.asyncio
    async def test_combined_filters(self, mock_user_service: UserService) -> None:
        """Should pass both is_active and role when both are set."""
        mock_user_service.user_repository.list = AsyncMock(return_value=([], 0))

        await mock_user_service.list_users(is_active=True, role=UserRole.USER)

        mock_user_service.user_repository.list.assert_awaited_once_with(
            skip=0, limit=20, filters={"is_active": True, "role": UserRole.USER}
        )

    @pytest.mark.asyncio
    async def test_returns_users_and_total(
        self, mock_user_service: UserService
    ) -> None:
        """Should return the user list and total count from the repository."""
        user1 = _make_user_mock(email="user1@example.com")
        user2 = _make_user_mock(email="user2@example.com")
        mock_user_service.user_repository.list = AsyncMock(
            return_value=([user1, user2], 2)
        )

        users, total = await mock_user_service.list_users()

        assert len(users) == 2
        assert total == 2
        assert users[0].email == "user1@example.com"


# update (admin fields)


class TestAdminUpdate:
    """Tests for UserService.update() — admin-specific field updates."""

    @pytest.mark.asyncio
    async def test_update_is_active_field(self, mock_user_service: UserService) -> None:
        """Admin should be able to deactivate a user via is_active=False."""
        user_id = uuid4()
        admin_id = uuid4()
        existing = _make_user_mock(id=user_id, is_active=True)
        updated = _make_user_mock(id=user_id, is_active=False)

        mock_user_service.user_repository.get = AsyncMock(return_value=existing)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        data = UserUpdate.model_construct(is_active=False)
        result = await mock_user_service.update(user_id, data, updated_by=admin_id)

        assert result.is_active is False
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user_id, {"is_active": False}
        )
        mock_user_service.audit_repository.log_user_action_safe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_role_field(self, mock_user_service: UserService) -> None:
        """Admin should be able to grant admin role."""
        user_id = uuid4()
        admin_id = uuid4()
        existing = _make_user_mock(id=user_id, role=UserRole.USER)
        updated = _make_user_mock(id=user_id, role=UserRole.SUPERUSER)

        mock_user_service.user_repository.get = AsyncMock(return_value=existing)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        data = UserUpdate.model_construct(role="superuser")
        result = await mock_user_service.update(user_id, data, updated_by=admin_id)

        assert result.role == UserRole.SUPERUSER
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user_id, {"role": "superuser"}
        )

    @pytest.mark.asyncio
    async def test_update_multiple_admin_fields(
        self, mock_user_service: UserService
    ) -> None:
        """Should update full_name and is_active in a single call."""
        user_id = uuid4()
        admin_id = uuid4()
        existing = _make_user_mock(id=user_id, full_name="Old", is_active=True)
        updated = _make_user_mock(id=user_id, full_name="New", is_active=False)

        mock_user_service.user_repository.get = AsyncMock(return_value=existing)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        data = UserUpdate.model_construct(full_name="New", is_active=False)
        result = await mock_user_service.update(user_id, data, updated_by=admin_id)

        assert result.full_name == "New"
        assert result.is_active is False
        mock_user_service.user_repository.update.assert_awaited_once_with(
            user_id, {"full_name": "New", "is_active": False}
        )

    @pytest.mark.asyncio
    async def test_update_no_fields_returns_existing(
        self, mock_user_service: UserService
    ) -> None:
        """Should return the existing user when no update fields are provided."""
        user_id = uuid4()
        admin_id = uuid4()
        existing = _make_user_mock(id=user_id)

        mock_user_service.user_repository.get = AsyncMock(return_value=existing)

        data = UserUpdate.model_construct()
        result = await mock_user_service.update(user_id, data, updated_by=admin_id)

        assert result is existing
        mock_user_service.user_repository.update.assert_not_called()
        mock_user_service.audit_repository.log_user_action_safe.assert_not_called()


# UserServiceError


class TestUserServiceErrors:
    """Tests for UserServiceError scenarios."""

    @pytest.mark.asyncio
    async def test_update_duplicate_email_raises_error(
        self, mock_user_service: UserService
    ) -> None:
        """Should raise UserServiceError when updating to an already-taken email."""
        user_id = uuid4()
        admin_id = uuid4()
        existing = _make_user_mock(id=user_id, email="old@example.com")
        conflicting = _make_user_mock(id=uuid4(), email="taken@example.com")

        mock_user_service.user_repository.get = AsyncMock(return_value=existing)
        mock_user_service.user_repository.get_by_email = AsyncMock(
            return_value=conflicting
        )

        data = UserUpdate.model_construct(email="taken@example.com")

        with pytest.raises(UserServiceError, match="Email already in use"):
            await mock_user_service.update(user_id, data, updated_by=admin_id)

        mock_user_service.user_repository.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_same_email_no_conflict_check(
        self, mock_user_service: UserService
    ) -> None:
        """Should not check for duplicates when the email is unchanged."""
        user_id = uuid4()
        admin_id = uuid4()
        existing = _make_user_mock(id=user_id, email="same@example.com")
        updated = _make_user_mock(id=user_id, email="same@example.com")

        mock_user_service.user_repository.get = AsyncMock(return_value=existing)
        mock_user_service.user_repository.update = AsyncMock(return_value=updated)
        mock_user_service.audit_repository.log_user_action_safe = AsyncMock()

        data = UserUpdate.model_construct(email="same@example.com")
        result = await mock_user_service.update(user_id, data, updated_by=admin_id)

        assert result.email == "same@example.com"
        mock_user_service.user_repository.get_by_email.assert_not_called()
        mock_user_service.user_repository.update.assert_awaited_once()
