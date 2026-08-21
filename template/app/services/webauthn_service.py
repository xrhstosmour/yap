"""WebAuthn (passkey) authentication service.

Handles FIDO2 WebAuthn registration and authentication ceremonies
for passwordless login via passkeys (device-bound or cross-platform).
"""

from __future__ import annotations

import dataclasses
import json
import secrets
from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import generate_authentication_options
from webauthn import generate_registration_options
from webauthn import verify_authentication_response
from webauthn import verify_registration_response
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import AuthenticationCredential
from webauthn.helpers.structs import AuthenticatorSelectionCriteria
from webauthn.helpers.structs import PublicKeyCredentialDescriptor
from webauthn.helpers.structs import RegistrationCredential
from webauthn.helpers.structs import UserVerificationRequirement

from app.core.cache import get_redis
from app.core.logging import get_logger
from app.core.settings import settings
from app.core.tenant import system_context
from app.models.user import User
from app.models.webauthn_credential import WebAuthnCredential
from app.repositories.user_repository import UserRepository

logger = get_logger("service.webauthn")

_WEBAUTHN_CHALLENGE_PREFIX = "webauthn_challenge"


class WebAuthnError(Exception):
    """Base exception for WebAuthn errors."""

    pass


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:  # noqa: ANN401
    """Convert a dataclass to a JSON-safe dict."""
    result: dict[str, object] = json.loads(
        json.dumps(dataclasses.asdict(obj), default=str)
    )
    return result


class WebAuthnService:
    """Service for WebAuthn (passkey) registration and authentication."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repository = UserRepository(session)

    # --- Registration ---

    async def begin_registration(self, user: User) -> dict[str, Any]:
        challenge = secrets.token_bytes(32)

        redis = await get_redis()
        await redis.setex(
            f"{_WEBAUTHN_CHALLENGE_PREFIX}:register:{user.id}",
            settings.WEBAUTHN_CHALLENGE_TTL_SECONDS,
            bytes_to_base64url(challenge),
        )

        exclude = await self._get_user_credentials(user.id)
        exclude_descriptors = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
            for c in exclude
        ]

        options = generate_registration_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            rp_name=settings.WEBAUTHN_RP_NAME,
            user_id=str(user.id).encode(),
            user_name=user.email,
            user_display_name=user.full_name or user.email,
            challenge=challenge,
            timeout=60000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=exclude_descriptors,
        )

        logger.debug("webauthn_registration_began", user_id=str(user.id))
        return _dataclass_to_dict(options)

    async def complete_registration(
        self, user: User, credential: dict, device_name: str = "Passkey"
    ) -> WebAuthnCredential:
        redis = await get_redis()
        challenge_b64 = await redis.getdel(
            f"{_WEBAUTHN_CHALLENGE_PREFIX}:register:{user.id}"
        )
        if not challenge_b64:
            raise WebAuthnError("Registration challenge expired or invalid.")

        expected_challenge = base64url_to_bytes(challenge_b64)
        reg_cred = RegistrationCredential(**credential)

        verification = verify_registration_response(
            credential=reg_cred,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.FRONTEND_HOST,
            require_user_verification=False,
        )

        credential_id = bytes_to_base64url(verification.credential_id)
        public_key = bytes_to_base64url(verification.credential_public_key)
        user_handle = bytes_to_base64url(str(user.id).encode())

        new_cred = WebAuthnCredential(
            user_id=user.id,
            credential_id=credential_id,
            public_key=public_key,
            user_handle=user_handle,
            sign_count=verification.sign_count,
            device_name=device_name,
        )
        self.session.add(new_cred)
        await self.session.flush()

        logger.info(
            "webauthn_registered",
            user_id=str(user.id),
            credential_id=credential_id[:16],
        )
        return new_cred

    # --- Authentication ---

    async def begin_authentication(
        self, email: str | None = None
    ) -> tuple[dict[str, Any], str | None]:
        """Begin WebAuthn authentication.

        Returns:
            Tuple of (options_dict, challenge_session_key). challenge_session_key
            is None when email is known (challenge is stored under user_id);
            it is a random nonce when email is absent and must be echoed back
            in complete_authentication() so the challenge can be found.
        """
        challenge = secrets.token_bytes(32)

        allow_credentials: list[PublicKeyCredentialDescriptor] | None = None
        user_id: UUID | None = None

        if email:
            user = await self.user_repository.get_by_email(email)
            if user:
                user_id = user.id
                creds = await self._get_user_credentials(user.id)
                allow_credentials = [
                    PublicKeyCredentialDescriptor(
                        id=base64url_to_bytes(c.credential_id)
                    )
                    for c in creds
                ]

        # Use user_id as challenge key when known; otherwise generate a unique
        # nonce per request so concurrent anonymous flows cannot collide.
        challenge_session_key: str | None
        if user_id:
            challenge_key = str(user_id)
            challenge_session_key = None
        else:
            challenge_key = secrets.token_hex(16)
            challenge_session_key = challenge_key

        redis = await get_redis()
        await redis.setex(
            f"{_WEBAUTHN_CHALLENGE_PREFIX}:auth:{challenge_key}",
            settings.WEBAUTHN_CHALLENGE_TTL_SECONDS,
            bytes_to_base64url(challenge),
        )

        options = generate_authentication_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            challenge=challenge,
            timeout=60000,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        logger.debug("webauthn_authentication_began", user_id=challenge_key)
        return _dataclass_to_dict(options), challenge_session_key

    async def complete_authentication(
        self, credential: dict, challenge_session_key: str | None = None
    ) -> User:
        auth_cred = AuthenticationCredential(**credential)
        cred_id = bytes_to_base64url(auth_cred.raw_id)

        result = await self.session.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.credential_id == cred_id,  # type: ignore[arg-type]
                WebAuthnCredential.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        stored_cred = result.scalar_one_or_none()
        if not stored_cred:
            raise WebAuthnError("Credential not found.")

        # Use challenge_session_key for anonymous flows, user_id for email flows.
        lookup_key = (
            challenge_session_key if challenge_session_key else str(stored_cred.user_id)
        )
        redis = await get_redis()
        challenge_b64 = await redis.getdel(
            f"{_WEBAUTHN_CHALLENGE_PREFIX}:auth:{lookup_key}"
        )
        if not challenge_b64:
            raise WebAuthnError("Authentication challenge expired or invalid.")

        expected_challenge = base64url_to_bytes(challenge_b64)
        credential_public_key = base64url_to_bytes(stored_cred.public_key)

        verification = verify_authentication_response(
            credential=auth_cred,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.FRONTEND_HOST,
            credential_public_key=credential_public_key,
            credential_current_sign_count=stored_cred.sign_count,
            require_user_verification=False,
        )

        stored_cred.sign_count = verification.new_sign_count
        stored_cred.last_used_at = datetime.now(UTC)

        # Reload user from DB since WebAuthnCredential has no relationship.
        # Bootstrapping identity from a WebAuthn assertion: the tenant isn't
        # known yet, that's what this lookup establishes, the same
        # situation as magic-link and Google OAuth login (see
        # AuthService.verify_magic_link and .google_login). Without
        # system_context(), User being tenant-scoped means this raises
        # TenantContextRequiredError on every single login attempt, since
        # no tenant context can exist yet at this point in the request.
        with system_context():
            user = await self.user_repository.get(stored_cred.user_id)
        if not user or not user.is_active:
            raise WebAuthnError("User not found or inactive.")

        await self.session.flush()

        logger.info(
            "webauthn_authenticated",
            user_id=str(user.id),
            credential_id=cred_id[:16],
        )
        return user

    # --- Helpers ---

    async def _get_user_credentials(self, user_id: UUID) -> list[WebAuthnCredential]:
        result = await self.session.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.user_id == user_id,  # type: ignore[arg-type]
                WebAuthnCredential.deleted_at.is_(None),  # type: ignore[union-attr]
            )
        )
        return list(result.scalars().all())
