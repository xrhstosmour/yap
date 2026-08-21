"""Tests for WebAuthnService."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers import bytes_to_base64url

from app.core.tenant import tenant_context
from app.models.tenant import Tenant
from app.models.webauthn_credential import WebAuthnCredential
from app.repositories.user_repository import UserRepository
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


class TestBeginRegistrationExpanded:
    """Additional tests for begin_registration()."""

    @pytest.mark.asyncio
    async def test_passes_existing_credentials_as_exclude(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should include existing credentials in exclude_credentials."""
        user = make_user()

        fake_cred = MagicMock()
        fake_cred.credential_id = "ZXhhbXBsZS1jcmVkZW50aWFsLWlk"
        service._get_user_credentials = AsyncMock(return_value=[fake_cred])

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch("app.services.webauthn_service.get_redis", return_value=mock_redis):
            options = await service.begin_registration(user)

        assert "exclude_credentials" in options
        assert len(options["exclude_credentials"]) == 1

    @pytest.mark.asyncio
    async def test_raises_on_redis_failure(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should propagate redis errors during begin_registration."""
        user = make_user()

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock(side_effect=RuntimeError("Redis down"))

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            pytest.raises(RuntimeError, match="Redis down"),
        ):
            await service.begin_registration(user)


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
            options, session_key = await service.begin_authentication(email=None)

        assert isinstance(options, dict)
        assert "challenge" in options
        # Anonymous flow returns a unique nonce as session key.
        assert isinstance(session_key, str)
        mock_redis.setex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_email_returns_options(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should return options with no session key when email resolves to a user."""
        user = make_user()
        service.user_repository.get_by_email = AsyncMock(return_value=user)

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch.object(service, "_get_user_credentials", return_value=[]),
        ):
            options, session_key = await service.begin_authentication(
                email="user@example.com"
            )

        assert isinstance(options, dict)
        assert "challenge" in options
        assert session_key is None  # email flow stores challenge under user_id

    @pytest.mark.asyncio
    async def test_anon_when_user_not_found(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should use a unique nonce as challenge key when email is not matched."""
        service.user_repository.get_by_email = AsyncMock(return_value=None)

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch("app.services.webauthn_service.get_redis", return_value=mock_redis):
            options, session_key = await service.begin_authentication(
                email="nobody@example.com"
            )

        assert isinstance(options, dict)
        assert "challenge" in options
        assert isinstance(session_key, str)
        # The nonce must appear at the end of the Redis key.
        call_args = mock_redis.setex.call_args
        assert call_args[0][0].endswith(session_key)

    @pytest.mark.asyncio
    async def test_with_email_user_has_credentials_populates_allow(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should populate allow_credentials when user has passkeys."""
        user = make_user()
        service.user_repository.get_by_email = AsyncMock(return_value=user)

        fake_cred = MagicMock()
        fake_cred.credential_id = "ZXhhbXBsZS1jcmVkZW50aWFsLWlk"
        service._get_user_credentials = AsyncMock(return_value=[fake_cred])

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()

        with patch("app.services.webauthn_service.get_redis", return_value=mock_redis):
            options, session_key = await service.begin_authentication(
                email="user@example.com"
            )

        assert "allow_credentials" in options
        assert session_key is None  # known user, challenge stored under user_id
        assert len(options["allow_credentials"]) == 1


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

    @pytest.mark.asyncio
    async def test_success_stores_credential(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should verify, create model, add to session, and flush."""
        user = make_user()

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="Y2hhbGxlbmdl")

        fake_verification = MagicMock()
        fake_verification.credential_id = b"cred-id-bytes"
        fake_verification.credential_public_key = b"pub-key-bytes"
        fake_verification.sign_count = 0

        credential_payload = {
            "id": "credential-id-b64",
            "raw_id": "Y3JlZGVudGlhbC1pZC1iNjRz",
            "response": {
                "attestation_object": "o2NmbXRkbm9uZQ==",
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_registration_response",
                return_value=fake_verification,
            ),
        ):
            result = await service.complete_registration(user, credential_payload)

        assert result is not None
        assert result.user_id == user.id
        assert result.credential_id is not None
        service.session.add.assert_called_once()
        service.session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_on_verification_failure(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should raise WebAuthnError when verification throws."""
        user = make_user()

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="Y2hhbGxlbmdl")

        from webauthn.helpers.exceptions import InvalidRegistrationResponse

        credential_payload = {
            "id": "bad-credential",
            "raw_id": "YmFkLWNyZWRlbnRpYWw=",
            "response": {
                "attestation_object": "invalid",
                "client_data_json": "invalid",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_registration_response",
                side_effect=InvalidRegistrationResponse("Invalid"),
            ),
            pytest.raises(InvalidRegistrationResponse),
        ):
            await service.complete_registration(user, credential_payload)

    @pytest.mark.asyncio
    async def test_respects_device_name(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should set device_name on the stored credential."""
        user = make_user()

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="Y2hhbGxlbmdl")

        fake_verification = MagicMock()
        fake_verification.credential_id = b"cred-id-bytes"
        fake_verification.credential_public_key = b"pub-key-bytes"
        fake_verification.sign_count = 0

        credential_payload = {
            "id": "credential-id-b64",
            "raw_id": "Y3JlZGVudGlhbC1pZC1iNjRz",
            "response": {
                "attestation_object": "o2NmbXRkbm9uZQ==",
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_registration_response",
                return_value=fake_verification,
            ),
        ):
            result = await service.complete_registration(
                user, credential_payload, device_name="YubiKey 5"
            )

        assert result.device_name == "YubiKey 5"


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

    @pytest.mark.asyncio
    async def test_success_updates_sign_count_and_last_used(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should verify credential, update sign_count, return user."""
        user = make_user()
        stored_cred = MagicMock()
        stored_cred.credential_id = (
            "dGVzdC1jcmVkZW50aWFsLWlk"  # base64url "test-credential-id"
        )
        stored_cred.public_key = "cHViLWtleQ=="  # base64url "pub-key"
        stored_cred.user_id = user.id
        stored_cred.sign_count = 3

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=stored_cred)
        service.session.execute = AsyncMock(return_value=mock_result)

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="YXV0aC1jaGFsbGVuZ2U=")

        fake_verification = MagicMock()
        fake_verification.new_sign_count = 5

        service.user_repository.get = AsyncMock(return_value=user)

        from webauthn.helpers import base64url_to_bytes

        credential_payload = {
            "id": "dGVzdC1jcmVkZW50aWFsLWlk",
            "raw_id": base64url_to_bytes("dGVzdC1jcmVkZW50aWFsLWlk"),
            "response": {
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
                "authenticator_data": "oA==",
                "signature": "sig",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_authentication_response",
                return_value=fake_verification,
            ),
        ):
            result = await service.complete_authentication(credential_payload)

        assert result is user
        assert stored_cred.sign_count == 5
        assert stored_cred.last_used_at is not None
        service.session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_challenge_expired(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should raise WebAuthnError when auth challenge expired."""
        user = make_user()
        stored_cred = MagicMock()
        stored_cred.credential_id = "dGVzdC1jcmVkZW50aWFsLWlk"
        stored_cred.public_key = "cHViLWtleQ=="
        stored_cred.user_id = user.id
        stored_cred.sign_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=stored_cred)
        service.session.execute = AsyncMock(return_value=mock_result)

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value=None)

        from webauthn.helpers import base64url_to_bytes

        credential_payload = {
            "id": "dGVzdC1jcmVkZW50aWFsLWlk",
            "raw_id": base64url_to_bytes("dGVzdC1jcmVkZW50aWFsLWlk"),
            "response": {
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
                "authenticator_data": "oA==",
                "signature": "sig",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            pytest.raises(WebAuthnError, match="Authentication challenge expired"),
        ):
            await service.complete_authentication(credential_payload)

    @pytest.mark.asyncio
    async def test_raises_when_user_not_found_after_verification(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should raise WebAuthnError when user lookup fails after verification."""
        stored_cred = MagicMock()
        stored_cred.credential_id = "dGVzdC1jcmVkZW50aWFsLWlk"
        stored_cred.public_key = "cHViLWtleQ=="
        stored_cred.user_id = UUID("00000000-0000-0000-0000-000000000099")
        stored_cred.sign_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=stored_cred)
        service.session.execute = AsyncMock(return_value=mock_result)

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="YXV0aC1jaGFsbGVuZ2U=")

        fake_verification = MagicMock()
        fake_verification.new_sign_count = 1

        service.user_repository.get = AsyncMock(return_value=None)

        from webauthn.helpers import base64url_to_bytes

        credential_payload = {
            "id": "dGVzdC1jcmVkZW50aWFsLWlk",
            "raw_id": base64url_to_bytes("dGVzdC1jcmVkZW50aWFsLWlk"),
            "response": {
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
                "authenticator_data": "oA==",
                "signature": "sig",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_authentication_response",
                return_value=fake_verification,
            ),
            pytest.raises(WebAuthnError, match="User not found or inactive"),
        ):
            await service.complete_authentication(credential_payload)

    @pytest.mark.asyncio
    async def test_raises_when_user_inactive(
        self,
        service: WebAuthnService,
    ) -> None:
        """Should raise WebAuthnError when user is inactive."""
        user = make_user()
        user.is_active = False

        stored_cred = MagicMock()
        stored_cred.credential_id = "dGVzdC1jcmVkZW50aWFsLWlk"
        stored_cred.public_key = "cHViLWtleQ=="
        stored_cred.user_id = user.id
        stored_cred.sign_count = 0

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=stored_cred)
        service.session.execute = AsyncMock(return_value=mock_result)

        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value="YXV0aC1jaGFsbGVuZ2U=")

        fake_verification = MagicMock()
        fake_verification.new_sign_count = 1

        service.user_repository.get = AsyncMock(return_value=user)

        from webauthn.helpers import base64url_to_bytes

        credential_payload = {
            "id": "dGVzdC1jcmVkZW50aWFsLWlk",
            "raw_id": base64url_to_bytes("dGVzdC1jcmVkZW50aWFsLWlk"),
            "response": {
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
                "authenticator_data": "oA==",
                "signature": "sig",
            },
            "type": "public-key",
        }

        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_authentication_response",
                return_value=fake_verification,
            ),
            pytest.raises(WebAuthnError, match="User not found or inactive"),
        ):
            await service.complete_authentication(credential_payload)


class TestCompleteAuthenticationTenantContext:
    """Regression test for complete_authentication() against a real
    repository, not the mocked one every other test in this file uses.

    Every other test replaces `service.user_repository` with a MagicMock,
    which cannot reveal a TenantContextRequiredError: complete_authentication
    reloads the user by ID before any tenant context exists yet (this is
    what establishes it), and BaseRepository.get() fails closed when a
    tenant-scoped model is queried with no tenant context set. Without
    system_context() around that one call, every real WebAuthn login
    crashed with a 500, this passed with the mocked repository regardless.
    """

    @pytest.mark.anyio
    async def test_succeeds_with_no_tenant_context_active(
        self, session: AsyncSession
    ) -> None:
        """complete_authentication() must not require an active tenant context."""
        tenant = Tenant(name="Test Org", slug="webauthn-tenant-context-org")
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

        user_repository = UserRepository(session)
        with tenant_context(tenant.id):
            user = await user_repository.create_user(
                email="webauthn-tenant-context@example.com",
                password_hash="hash",
                tenant_id=tenant.id,
            )

        credential = WebAuthnCredential(
            user_id=user.id,
            credential_id="dGVzdC1jcmVkZW50aWFsLWlk",
            public_key="cHViLWtleQ==",
            user_handle="dXNlci1oYW5kbGU=",
            sign_count=3,
            device_name="Test Key",
        )
        session.add(credential)
        await session.flush()
        await session.commit()

        service = WebAuthnService(session)
        mock_redis = AsyncMock()
        mock_redis.getdel = AsyncMock(return_value=bytes_to_base64url(b"a" * 32))
        fake_verification = MagicMock()
        fake_verification.new_sign_count = 5

        credential_payload = {
            "id": "dGVzdC1jcmVkZW50aWFsLWlk",
            "raw_id": base64url_to_bytes("dGVzdC1jcmVkZW50aWFsLWlk"),
            "response": {
                "client_data_json": "eyJjaGFsbGVuZ2UiOiJZMiU=",
                "authenticator_data": "oA==",
                "signature": "sig",
            },
            "type": "public-key",
        }

        # No tenant_context(...) or system_context() active here, matching
        # the real request state: the tenant isn't known until this call
        # resolves it.
        with (
            patch("app.services.webauthn_service.get_redis", return_value=mock_redis),
            patch(
                "app.services.webauthn_service.verify_authentication_response",
                return_value=fake_verification,
            ),
        ):
            result = await service.complete_authentication(credential_payload)

        assert result.id == user.id
