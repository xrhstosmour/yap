"""Tests for APIKeyService."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.api_key import APIKey
from app.schemas.api_key import APIKeyCreate
from app.schemas.api_key import APIKeyUpdate
from app.services.api_key_service import APIKeyService


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_session: MagicMock) -> APIKeyService:
    svc = APIKeyService(mock_session)
    svc.apikey_repository = MagicMock()
    svc.user_repository = MagicMock()
    svc.audit_repository = MagicMock()
    return svc


def _make_api_key_mock(**overrides: object) -> MagicMock:
    """Build a MagicMock with common APIKey attributes.

    Recognised keyword arguments:
        is_valid_return: Set the return value of api_key.is_valid().
        is_expired_return: Set the return value of api_key.is_expired().
    """
    api_key = MagicMock(spec=APIKey)
    api_key.id = uuid4()
    api_key.key_id = f"key_{uuid4().hex[:16]}"
    api_key.key_hash = "$2b$12$hashedsecretvalueherehashedsecretvalue"
    api_key.key_prefix = "sk_abcdefgh"
    api_key.name = "Test Key"
    api_key.description = "A test key"
    api_key.scopes = ["read"]
    api_key.is_active = True
    api_key.expires_at = None
    api_key.deleted_at = None
    api_key.created_at = datetime.now(UTC)
    api_key.updated_at = datetime.now(UTC)
    api_key.last_used_at = None
    api_key.user_id = uuid4()
    api_key.tenant_id = uuid4()
    # -- special override keys --
    is_valid_return: bool = True
    is_expired_return: bool = False
    if "is_valid_return" in overrides:
        is_valid_return = overrides.pop("is_valid_return")  # type: ignore[assignment]
    if "is_expired_return" in overrides:
        is_expired_return = overrides.pop("is_expired_return")  # type: ignore[assignment]
    api_key.is_valid.return_value = is_valid_return  # type: ignore[attr-defined]
    api_key.is_expired.return_value = is_expired_return  # type: ignore[attr-defined]
    for key, value in overrides.items():
        setattr(api_key, key, value)
    return api_key


def _make_create_data(
    name: str = "My API Key",
    expires_in_days: int | None = None,
    description: str | None = None,
    scopes: list[str] | None = None,
) -> APIKeyCreate:
    return APIKeyCreate.model_construct(
        name=name,
        expires_in_days=expires_in_days,
        description=description,
        scopes=scopes or ["read"],
    )


# Create


class TestCreate:
    """Tests for APIKeyService.create()."""

    @pytest.mark.asyncio
    async def test_create_with_valid_expiry(
        self,
        service: APIKeyService,
    ) -> None:
        """Should create key with expires_at set when expires_in_days=30."""
        user_id = uuid4()
        tenant_id = uuid4()
        data = _make_create_data(name="Expiring Key", expires_in_days=30)

        full_key = "sk_validfullkey1234567890"
        hashed_key = "$2b$12$hashedvalidfullkey1234567890"
        key_id = "key_mocktestkeyid1234"

        mock_created = _make_api_key_mock(
            name="Expiring Key",
            key_id=key_id,
            key_hash=hashed_key,
            key_prefix=full_key[:12],
        )
        service.apikey_repository.create = AsyncMock(return_value=mock_created)
        service.audit_repository.log_user_action_safe = AsyncMock()

        with (
            patch(
                "app.services.api_key_service.generate_api_key",
                return_value=(full_key, hashed_key),
            ),
            patch(
                "app.services.api_key_service.generate_api_key_id",
                return_value=key_id,
            ),
        ):
            result, raw_key = await service.create(user_id, tenant_id, data)

        assert result.name == "Expiring Key"
        assert raw_key == full_key
        service.apikey_repository.create.assert_awaited_once()
        service.audit_repository.log_user_action_safe.assert_awaited_once()

        # Verify expires_at is approximately 30 days from now.
        call_args = service.apikey_repository.create.call_args[0][0]
        assert call_args["expires_at"] is not None
        expires = call_args["expires_at"]
        assert isinstance(expires, datetime)
        delta = expires - datetime.now(UTC)
        assert timedelta(days=29) < delta < timedelta(days=31)

    @pytest.mark.asyncio
    async def test_create_succeeds_when_audit_log_write_fails(
        self,
        service: APIKeyService,
    ) -> None:
        """Should create the key even if the audit-log write fails."""
        from app.repositories.audit_repository import AuditLogRepository

        user_id = uuid4()
        tenant_id = uuid4()
        data = _make_create_data(name="My Key")

        mock_created = _make_api_key_mock(name="My Key")
        service.apikey_repository.create = AsyncMock(return_value=mock_created)
        service.audit_repository = AuditLogRepository(MagicMock())
        service.audit_repository.log_user_action = AsyncMock(
            side_effect=Exception("db unavailable")
        )

        result, _ = await service.create(user_id, tenant_id, data)

        assert result.name == "My Key"

    @pytest.mark.asyncio
    async def test_create_with_expiry_zero_raises(
        self,
        service: APIKeyService,
    ) -> None:
        """Should raise ValueError when expires_in_days=0."""
        user_id = uuid4()
        tenant_id = uuid4()
        data = _make_create_data(expires_in_days=0)

        with pytest.raises(ValueError, match="between 1 and 365"):
            await service.create(user_id, tenant_id, data)

    @pytest.mark.asyncio
    async def test_create_with_expiry_366_raises(
        self,
        service: APIKeyService,
    ) -> None:
        """Should raise ValueError when expires_in_days=366."""
        user_id = uuid4()
        tenant_id = uuid4()
        data = _make_create_data(expires_in_days=366)

        with pytest.raises(ValueError, match="between 1 and 365"):
            await service.create(user_id, tenant_id, data)

    @pytest.mark.asyncio
    async def test_create_with_no_expiry(
        self,
        service: APIKeyService,
    ) -> None:
        """Should create key with expires_at=None when expires_in_days=None."""
        user_id = uuid4()
        tenant_id = uuid4()
        data = _make_create_data(name="No Expiry Key", expires_in_days=None)

        full_key = "sk_nonexpirykey1234567890"
        hashed_key = "$2b$12$hashednonexpirykey1234567890"
        key_id = "key_noexpiryid1234567"

        mock_created = _make_api_key_mock(
            name="No Expiry Key",
            key_id=key_id,
            expires_at=None,
        )
        service.apikey_repository.create = AsyncMock(return_value=mock_created)
        service.audit_repository.log_user_action_safe = AsyncMock()

        with (
            patch(
                "app.services.api_key_service.generate_api_key",
                return_value=(full_key, hashed_key),
            ),
            patch(
                "app.services.api_key_service.generate_api_key_id",
                return_value=key_id,
            ),
        ):
            result, raw_key = await service.create(user_id, tenant_id, data)

        assert result.expires_at is None
        assert raw_key == full_key

        call_args = service.apikey_repository.create.call_args[0][0]
        assert call_args["expires_at"] is None

    @pytest.mark.asyncio
    async def test_create_generates_proper_key_format(
        self,
        service: APIKeyService,
    ) -> None:
        """Should produce a key_id and full_key with correct format."""
        user_id = uuid4()
        tenant_id = uuid4()
        data = _make_create_data(name="Format Test")

        full_key = "sk_testkey1234567890abcd"
        hashed_key = "$2b$12$hashedtestkey1234567890abcd"
        key_id = "key_formattestid12345"

        mock_created = _make_api_key_mock(
            key_id=key_id,
            key_prefix=full_key[:12],
        )
        service.apikey_repository.create = AsyncMock(return_value=mock_created)
        service.audit_repository.log_user_action_safe = AsyncMock()

        with (
            patch(
                "app.services.api_key_service.generate_api_key",
                return_value=(full_key, hashed_key),
            ),
            patch(
                "app.services.api_key_service.generate_api_key_id",
                return_value=key_id,
            ),
        ):
            result, raw_key = await service.create(user_id, tenant_id, data)

        # key_id starts with "key_".
        assert result.key_id.startswith("key_")
        # Raw key starts with "sk_".
        assert raw_key.startswith("sk_")
        # key_prefix is first 12 chars of full_key.
        assert result.key_prefix == full_key[:12]


# List


class TestList:
    """Tests for APIKeyService.list_for_user()."""

    @pytest.mark.asyncio
    async def test_list_returns_keys_for_user(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return keys and total count for the given user."""
        user_id = uuid4()
        mock_key1 = _make_api_key_mock(name="Key 1")
        mock_key2 = _make_api_key_mock(name="Key 2")

        service.apikey_repository.list_by_user = AsyncMock(
            return_value=([mock_key1, mock_key2], 2)
        )

        keys, total = await service.list_for_user(user_id)

        assert len(keys) == 2
        assert total == 2
        assert keys[0].name == "Key 1"
        assert keys[1].name == "Key 2"
        service.apikey_repository.list_by_user.assert_awaited_once_with(
            user_id=user_id,
            skip=0,
            limit=20,
        )

    @pytest.mark.asyncio
    async def test_list_respects_pagination_params(
        self,
        service: APIKeyService,
    ) -> None:
        """Should forward skip and limit to the repository."""
        user_id = uuid4()
        service.apikey_repository.list_by_user = AsyncMock(return_value=([], 0))

        await service.list_for_user(user_id, skip=10, limit=5)

        service.apikey_repository.list_by_user.assert_awaited_once_with(
            user_id=user_id,
            skip=10,
            limit=5,
        )


# Verify


class TestVerify:
    """Tests for APIKeyService.verify()."""

    @pytest.mark.asyncio
    async def test_verify_valid_key(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return the APIKey when key_id and secret are valid."""
        key_id = "key_valid12345"
        secret = "sk_validsecret1234567890"
        mock_key = _make_api_key_mock(key_id=key_id, is_valid_return=True)

        service.apikey_repository.get_by_key_id = AsyncMock(return_value=mock_key)
        service.apikey_repository.update_last_used = AsyncMock()

        with patch(
            "app.services.api_key_service.verify_password",
            return_value=True,
        ):
            result = await service.verify(key_id, secret)

        assert result is mock_key
        service.apikey_repository.get_by_key_id.assert_awaited_once_with(key_id)
        service.apikey_repository.update_last_used.assert_awaited_once_with(mock_key.id)

    @pytest.mark.asyncio
    async def test_verify_invalid_key_id(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return None and use dummy hash when key_id not found."""
        key_id = "key_nonexistent"
        secret = "sk_somesecret1234567890"

        service.apikey_repository.get_by_key_id = AsyncMock(return_value=None)

        with patch(
            "app.services.api_key_service.verify_password",
            return_value=False,
        ) as mock_verify:
            result = await service.verify(key_id, secret)

        assert result is None
        # Should verify against dummy hash to prevent timing attacks.
        mock_verify.assert_called_once()
        # The first call should include DUMMY_PASSWORD_HASH.
        from app.core.security import DUMMY_PASSWORD_HASH

        mock_verify.assert_called_with(secret, DUMMY_PASSWORD_HASH)

    @pytest.mark.asyncio
    async def test_verify_wrong_secret(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return None when password verification fails."""
        key_id = "key_exists_but_wrong_secret"
        secret = "sk_wrongsecret1234567890"
        mock_key = _make_api_key_mock(key_id=key_id)

        service.apikey_repository.get_by_key_id = AsyncMock(return_value=mock_key)

        with patch(
            "app.services.api_key_service.verify_password",
            return_value=False,
        ):
            result = await service.verify(key_id, secret)

        assert result is None
        service.apikey_repository.update_last_used.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_inactive_key(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return None when key is not valid (inactive/expired/deleted)."""
        key_id = "key_inactive12345"
        secret = "sk_inactivesecret1234567890"
        mock_key = _make_api_key_mock(key_id=key_id, is_valid_return=False)

        service.apikey_repository.get_by_key_id = AsyncMock(return_value=mock_key)

        with patch(
            "app.services.api_key_service.verify_password",
            return_value=True,
        ):
            result = await service.verify(key_id, secret)

        assert result is None
        service.apikey_repository.update_last_used.assert_not_called()


# Update


class TestUpdate:
    """Tests for APIKeyService.update()."""

    @pytest.mark.asyncio
    async def test_update_key_name(
        self,
        service: APIKeyService,
    ) -> None:
        """Should update the name field on an existing key."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(name="Old Name", id=key_id, user_id=user_id)
        updated = _make_api_key_mock(name="New Name", id=key_id, user_id=user_id)

        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.apikey_repository.update = AsyncMock(return_value=updated)
        service.audit_repository.log_user_action_safe = AsyncMock()

        data = APIKeyUpdate.model_construct(name="New Name")

        result = await service.update(key_id, user_id, tenant_id, data)

        assert result.name == "New Name"
        service.apikey_repository.update.assert_awaited_once_with(
            key_id, {"name": "New Name"}
        )
        service.audit_repository.log_user_action_safe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_key_not_found(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return None when key does not exist."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        service.apikey_repository.get = AsyncMock(return_value=None)

        data = APIKeyUpdate.model_construct(name="Won't Work")

        result = await service.update(key_id, user_id, tenant_id, data)

        assert result is None
        service.apikey_repository.update.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_no_changes_skips_persistence(
        self,
        service: APIKeyService,
    ) -> None:
        """Should not call repository.update when no fields are set."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(id=key_id, user_id=user_id)
        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.audit_repository.log_user_action_safe = AsyncMock()

        # All fields None — nothing to update.
        data = APIKeyUpdate.model_construct()

        result = await service.update(key_id, user_id, tenant_id, data)

        assert result is existing
        service.apikey_repository.update.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_key_owned_by_other_user_returns_none(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return None when the key belongs to a different user."""
        key_id = uuid4()
        owner_id = uuid4()
        other_user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(id=key_id, user_id=owner_id)
        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.audit_repository.log_user_action_safe = AsyncMock()

        data = APIKeyUpdate.model_construct(name="Won't Work")

        result = await service.update(key_id, other_user_id, tenant_id, data)

        assert result is None
        service.apikey_repository.update.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()


# Revoke


class TestRevoke:
    """Tests for APIKeyService.revoke()."""

    @pytest.mark.asyncio
    async def test_revoke_sets_is_active_false(
        self,
        service: APIKeyService,
    ) -> None:
        """Should set is_active=False on the key and log the action."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(id=key_id, is_active=True, user_id=user_id)
        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.apikey_repository.update = AsyncMock()
        service.audit_repository.log_user_action_safe = AsyncMock()

        result = await service.revoke(key_id, user_id, tenant_id)

        assert result is True
        service.apikey_repository.update.assert_awaited_once_with(
            key_id, {"is_active": False}
        )
        service.audit_repository.log_user_action_safe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_key_not_found(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return False when key does not exist."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        service.apikey_repository.get = AsyncMock(return_value=None)

        result = await service.revoke(key_id, user_id, tenant_id)

        assert result is False
        service.apikey_repository.update.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_key_owned_by_other_user_returns_false(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return False when the key belongs to a different user."""
        key_id = uuid4()
        owner_id = uuid4()
        other_user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(id=key_id, is_active=True, user_id=owner_id)
        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.apikey_repository.update = AsyncMock()
        service.audit_repository.log_user_action_safe = AsyncMock()

        result = await service.revoke(key_id, other_user_id, tenant_id)

        assert result is False
        service.apikey_repository.update.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()


# Delete


class TestDelete:
    """Tests for APIKeyService.delete()."""

    @pytest.mark.asyncio
    async def test_delete_soft_deletes_key(
        self,
        service: APIKeyService,
    ) -> None:
        """Should call repository.delete (soft delete) and log the action."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(id=key_id, user_id=user_id)
        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.apikey_repository.delete = AsyncMock(return_value=True)
        service.audit_repository.log_user_action_safe = AsyncMock()

        result = await service.delete(key_id, user_id, tenant_id)

        assert result is True
        service.apikey_repository.delete.assert_awaited_once_with(key_id)
        service.audit_repository.log_user_action_safe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_key_not_found(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return False when key does not exist."""
        key_id = uuid4()
        user_id = uuid4()
        tenant_id = uuid4()

        service.apikey_repository.get = AsyncMock(return_value=None)

        result = await service.delete(key_id, user_id, tenant_id)

        assert result is False
        service.apikey_repository.delete.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_key_owned_by_other_user_returns_false(
        self,
        service: APIKeyService,
    ) -> None:
        """Should return False when the key belongs to a different user."""
        key_id = uuid4()
        owner_id = uuid4()
        other_user_id = uuid4()
        tenant_id = uuid4()

        existing = _make_api_key_mock(id=key_id, user_id=owner_id)
        service.apikey_repository.get = AsyncMock(return_value=existing)
        service.apikey_repository.delete = AsyncMock(return_value=True)
        service.audit_repository.log_user_action_safe = AsyncMock()

        result = await service.delete(key_id, other_user_id, tenant_id)

        assert result is False
        service.apikey_repository.delete.assert_not_called()
        service.audit_repository.log_user_action_safe.assert_not_called()
