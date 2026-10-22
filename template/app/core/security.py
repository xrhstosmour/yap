"""Security utilities for authentication and authorization.

This module provides secure password hashing, JWT token management,
API key utilities, and single-use Redis-backed tokens for email verification,
password reset, and Google OAuth state management.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import string
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt
import pyotp
import qrcode
import qrcode.constants
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("core.security")

# Password hashing configuration.
# Using bcrypt directly as it's well-audited and production-proven.

# bcrypt refuses any secret longer than 72 bytes rather than silently
# truncating it, so this is a hard ceiling, not a policy choice.
MAX_PASSWORD_BYTES: int = 72


def generate_password_hash(password: str) -> str:
    """Hash a password using bcrypt.

    Uses bcrypt with automatic salt generation. The resulting hash
    includes the salt and can be verified using verify_password.

    Args:
        password: Plain text password to hash

    Returns:
        Bcrypt hash string suitable for storage

    Raises:
        ValueError: If the password exceeds `MAX_PASSWORD_BYTES` once encoded
            as UTF-8.

    Note:
        bcrypt is intentionally slow (cost factor) to resist brute force attacks.
        The default cost factor takes ~250ms per hash on modern hardware.

        The limit is counted in UTF-8 bytes, not characters, because that is
        what bcrypt itself enforces. Any non-ASCII character costs more than
        one byte, so a 42-character Greek password is already 84 bytes and
        bcrypt raises on it. Checking `len(password)` instead let such a
        password pass this guard and blow up inside `bcrypt.hashpw`, which
        surfaced as a 500 rather than a validation error.
    """
    encoded = password.encode()
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password exceeds maximum length of {MAX_PASSWORD_BYTES} bytes "
            "when encoded as UTF-8"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    Safely compares the plain password with the stored hash using
    constant-time comparison to prevent timing attacks.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Previously stored hash to compare against

    Returns:
        True if password matches, False otherwise.

    Note:
        An over-long candidate returns False rather than raising. bcrypt
        rejects anything over `MAX_PASSWORD_BYTES`, and since no stored hash
        can have been produced from such a password it cannot be a match.
        Letting the `ValueError` escape would turn a failed login into a 500
        and, on the `DUMMY_PASSWORD_HASH` path, break the timing-attack
        protection that path exists to provide.
    """
    encoded = plain_password.encode()
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(encoded, hashed_password.encode())


# Dummy hash for timing attack prevention.
# Generated at module load to avoid hardcoded values.
# Used when user/key is not found to maintain consistent response time.
# Any valid bcrypt hash works here, the actual value is not a secret.
DUMMY_PASSWORD_HASH: str = generate_password_hash(secrets.token_urlsafe(32))


# JWT Token Management.

ALGORITHM = settings.ALGORITHM
_JWT_BLACKLIST_PREFIX = "jwt_blacklist"


def _database_claim() -> dict[str, str]:
    """The claim naming the database a token was issued against.

    Both databases sign with the same `SECRET_KEY`, and the additional one
    is often seeded from production with the same user IDs, so a token from
    one would otherwise be accepted by the other. Only tokens issued against
    the additional database carry it, so every main database token stays
    exactly as it was.
    """
    # Imported here, `app.database` imports the core package this is part of.
    from app.database import ADDITIONAL_DATABASE_MODE
    from app.database import database_mode_variable

    if database_mode_variable.get() == ADDITIONAL_DATABASE_MODE:
        return {"db": ADDITIONAL_DATABASE_MODE}
    return {}


def create_access_token(
    subject: str | UUID,
    expires_delta: timedelta | None = None,
    additional_claims: dict | None = None,
) -> str:
    """Create a JWT access token.

    Generates a signed JWT containing the subject (user/API key ID)
    and optional claims. Tokens are signed using HS256 with the
    application secret key.

    Args:
        subject: Unique identifier to encode in the token (user ID or API key ID)
        expires_delta: Optional custom expiration time, defaults to settings value
        additional_claims: Optional dict of additional JWT claims

    Returns:
        Encoded JWT string

    Example:
        token = create_access_token(user_id, additional_claims={"role": "admin"})
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.now(UTC) + expires_delta

    to_encode: dict[str, Any] = {
        "exp": expire,
        "sub": str(subject),
        "type": "access",
        "iat": datetime.now(UTC),
        "jti": secrets.token_urlsafe(16),
        **_database_claim(),
    }

    if additional_claims:
        to_encode.update(additional_claims)

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(
    subject: str | UUID,
    expires_delta: timedelta | None = None,
    additional_claims: dict | None = None,
) -> str:
    """Create a JWT refresh token.

    Generates a long-lived JWT refresh token. Refresh tokens have
    a longer lifetime than access tokens and can be exchanged
    for new access tokens.

    Args:
        subject: Unique identifier to encode in the token
        expires_delta: Optional custom expiration, defaults to 7 days

    Returns:
        Encoded JWT refresh token string
    """
    if expires_delta is None:
        expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    expire = datetime.now(UTC) + expires_delta

    to_encode: dict[str, Any] = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "iat": datetime.now(UTC),
        "jti": secrets.token_urlsafe(16),
        **_database_claim(),
    }

    if additional_claims:
        to_encode.update(additional_claims)

    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT token.

    Decodes the JWT and validates the signature and expiration.
    Raises an exception if the token is invalid or expired.

    Args:
        token: Encoded JWT string

    Returns:
        Dict containing the token payload

    Raises:
        jwt.ExpiredSignatureError: If token has expired
        jwt.InvalidTokenError: If token is invalid, or was issued against
            the other database than this request is routed to
    """
    payload: dict[str, Any] = jwt.decode(
        token, settings.SECRET_KEY, algorithms=[ALGORITHM]
    )
    if payload.get("db", "") != _database_claim().get("db", ""):
        raise jwt.InvalidTokenError("Token was issued for another database")
    return payload


async def blacklist_token(token_identifier: str, expires_at: datetime) -> None:
    """Blacklist a JWT token identifier in Redis until it expires.

    Args:
        token_identifier: JWT ``jti`` claim value.
        expires_at: UTC expiration time from token ``exp``.
    """
    from app.core.cache import get_redis

    now = datetime.now(UTC)
    remaining_seconds = int((expires_at - now).total_seconds())
    if remaining_seconds <= 0:
        return

    try:
        redis = await get_redis()
        key = f"{_JWT_BLACKLIST_PREFIX}:{token_identifier}"
        await redis.setex(key, remaining_seconds, "1")
    except (RedisConnectionError, RedisTimeoutError) as error:
        logger.warning(
            "blacklist_failed",
            token_identifier=token_identifier,
            error=str(error),
        )
        return


async def claim_token_for_rotation(token_identifier: str, expires_at: datetime) -> bool:
    """Blacklist a token and report whether this caller was the one to do it.

    `SET NX` is atomic, so exactly one caller can win for a given `jti`.
    Checking the blacklist and then writing to it as two steps leaves a
    window where two concurrent refreshes both read "not blacklisted" and
    both mint a fresh token pair, which is the reuse the blacklist exists
    to catch.

    Fails open on Redis errors, matching `is_token_blacklisted`: a
    transient outage must not lock every user out, and `token_version`
    remains the hard revocation guarantee.

    Args:
        token_identifier: JWT ``jti`` claim value.
        expires_at: UTC expiration time from token ``exp``.

    Returns:
        True if this call claimed the token, False if it was already used.
    """
    from app.core.cache import get_redis

    now = datetime.now(UTC)
    remaining_seconds = int((expires_at - now).total_seconds())
    if remaining_seconds <= 0:
        return False

    try:
        redis = await get_redis()
        key = f"{_JWT_BLACKLIST_PREFIX}:{token_identifier}"
        return bool(await redis.set(key, "1", ex=remaining_seconds, nx=True))
    except (RedisConnectionError, RedisTimeoutError) as error:
        logger.warning(
            "token_rotation_claim_failed",
            token_identifier=token_identifier,
            error=str(error),
        )
        return True


async def is_token_blacklisted(token_identifier: str) -> bool:
    """Check whether a JWT token identifier is blacklisted.

    Fails open on Redis errors: returns ``False`` so that transient
    outages do not lock out every user.  The ``token_version`` column
    in the database provides hard revocation guarantees independently.

    Args:
        token_identifier: JWT ``jti`` claim value.

    Returns:
        True if blacklisted, False otherwise (including Redis errors).
    """
    from app.core.cache import get_redis

    try:
        redis = await get_redis()
        key = f"{_JWT_BLACKLIST_PREFIX}:{token_identifier}"
        return bool(await redis.exists(key) == 1)
    except (RedisConnectionError, RedisTimeoutError):
        return False


# API Key Management.

API_KEY_LENGTH = 32
API_KEY_PREFIX = "sk"


def generate_api_key(prefix: str = API_KEY_PREFIX) -> tuple[str, str]:
    """Generate a new API key.

    Creates a cryptographically secure API key with a prefix for
    identification. Returns both the full key (shown once) and
    a hashed version for storage.

    Args:
        prefix: Prefix to identify the key type (default: "sk")

    Returns:
        Tuple of (full_key, hashed_key) where full_key is shown once to user

    Example:
        full_key, hashed_key = generate_api_key()
        # Store hashed_key in database
        # Show full_key to user (only time it's visible)
    """
    # Generate random characters.
    random_part = "".join(
        secrets.choice(string.ascii_letters + string.digits)
        for _ in range(API_KEY_LENGTH)
    )

    full_key = f"{prefix}_{random_part}"
    hashed_key = hash_api_key(full_key)

    return full_key, hashed_key


API_KEY_HASH_PREFIX = "sha256:"


def hash_api_key(key: str) -> str:
    """Hash an API key for storage.

    SHA-256 rather than bcrypt. A password needs a slow hash because people
    pick guessable ones, but these keys are long random secrets, so a fast
    hash leaves nothing to guess while bcrypt cost a full check, about a
    quarter of a second of CPU, on every API-key request. Unkeyed, so
    rotating `CRYPTO_KEY` or `SECRET_KEY` does not invalidate every key.

    Args:
        key: The full API key.

    Returns:
        The hex digest, prefixed with `sha256:`.
    """
    return API_KEY_HASH_PREFIX + hashlib.sha256(key.encode()).hexdigest()


# Compared against when no key matches, so an unknown key ID takes as long
# as a known one.
DUMMY_API_KEY_HASH: str = hash_api_key(secrets.token_urlsafe(32))


def is_legacy_api_key_hash(stored: str) -> bool:
    """Whether a stored key hash is bcrypt, from before `hash_api_key`."""
    return not stored.startswith(API_KEY_HASH_PREFIX)


def verify_api_key(key: str, stored: str) -> bool:
    """Check an API key against its stored SHA-256 hash.

    Args:
        key: The full API key presented.
        stored: The stored hash, from `hash_api_key`.

    Returns:
        Whether they match, compared in constant time.
    """
    return hmac.compare_digest(hash_api_key(key), stored)


def generate_api_key_id() -> str:
    """Generate a unique API key ID.

    Creates a short identifier for the API key that can be
    used in URLs and logs without exposing the actual key.

    Returns:
        Unique key ID string
    """
    return f"key_{secrets.token_urlsafe(16)}"


def mask_api_key(key: str) -> str:
    """Mask an API key for safe logging/display.

    Shows only the first 8 characters and adds asterisks.
    Useful for displaying keys in UI or logs.

    Args:
        key: Full API key

    Returns:
        Masked key string (e.g., "sk_abc123****")

    Example:
        >>> mask_api_key("sk_abc123def456ghi789")
        'sk_abc123****'
    """
    if len(key) <= 12:
        return "***"

    return f"{key[:8]}****"


# Rate limiting for token creation to prevent abuse.


class TokenRateLimitError(Exception):
    """Raised when a token creation rate limit is exceeded."""


async def _check_token_rate_limit(
    redis_client, key_prefix: str, user_id: str, cooldown_seconds: int
) -> None:
    """Check and enforce a cooldown on token creation per user.

    Uses Redis SET with NX and EX options atomically to create a cooldown
    key with expiration. If the key already exists, the user must wait
    before creating another token.

    Args:
        redis_client: Redis client instance.
        key_prefix: Prefix for the rate limit key
            (e.g. "rate_limit:email_verification").
        user_id: User identifier for the rate limit key.
        cooldown_seconds: Cooldown duration in seconds.

    Raises:
        TokenRateLimitError: If a token was recently created for this user.
    """
    key = f"rate_limit:{key_prefix}:{user_id}"
    result = await redis_client.set(key, "1", nx=True, ex=cooldown_seconds)
    if result is None:
        raise TokenRateLimitError(
            f"Token creation rate limited for {key_prefix}. "
            f"Wait {cooldown_seconds} seconds."
        )


# Single-use Redis-backed token configuration.

# Email verification.
_EMAIL_VERIFICATION_PREFIX = "email_verification"


async def create_email_verification_token(user_id: UUID) -> str:
    """Create a single-use email verification token stored in Redis.

    Generates a cryptographically secure random token and stores a
    mapping of token → user_id with a 24-hour TTL. The token is
    consumed (deleted) on first successful verification.

    Args:
        user_id: UUID of the user whose email is being verified.

    Returns:
        Opaque URL-safe token string (32 bytes, base64url encoded).

    Raises:
        TokenRateLimitError: If a token was recently created for this user.
    """
    from app.core.cache import get_redis

    redis = await get_redis()
    await _check_token_rate_limit(redis, "email_verification", str(user_id), 60)

    token = secrets.token_urlsafe(32)
    key = f"{_EMAIL_VERIFICATION_PREFIX}:{token}"
    await redis.setex(key, settings.EMAIL_VERIFICATION_TOKEN_TTL_SECONDS, str(user_id))
    return token


async def verify_email_verification_token(token: str) -> UUID | None:
    """Consume and validate an email verification token.

    Deletes the token from Redis immediately after retrieval to enforce
    single-use semantics. A token cannot be used twice.

    Args:
        token: Opaque token string received from the verification email.

    Returns:
        User UUID if the token is valid and unexpired, None otherwise.
    """
    from app.core.cache import get_redis

    redis = await get_redis()
    key = f"{_EMAIL_VERIFICATION_PREFIX}:{token}"

    # Atomic get-and-delete ensures single-use semantics with no race window.
    user_id_str: str | None = await redis.getdel(key)

    if not user_id_str:
        return None

    return UUID(user_id_str)


# Password reset.
_PASSWORD_RESET_PREFIX = "password_reset"


async def create_password_reset_token(user_id: UUID) -> str:
    """Create a single-use password reset token stored in Redis.

    Generates a cryptographically secure random token and stores a
    mapping of token → user_id with a 1-hour TTL. The token is
    consumed (deleted) when the password is successfully reset.

    Args:
        user_id: UUID of the user requesting a password reset.

    Returns:
        Opaque URL-safe token string (32 bytes, base64url encoded).

    Raises:
        TokenRateLimitError: If a token was recently created for this user.
    """
    from app.core.cache import get_redis

    redis = await get_redis()
    await _check_token_rate_limit(redis, "password_reset", str(user_id), 60)

    token = secrets.token_urlsafe(32)
    key = f"{_PASSWORD_RESET_PREFIX}:{token}"
    await redis.setex(key, settings.PASSWORD_RESET_TOKEN_TTL_SECONDS, str(user_id))
    return token


async def verify_password_reset_token(token: str) -> UUID | None:
    """Consume and validate a password reset token.

    Deletes the token from Redis immediately after retrieval to enforce
    single-use semantics. A reset link cannot be used twice.

    Args:
        token: Opaque token string received from the reset email.

    Returns:
        User UUID if the token is valid and unexpired, None otherwise.
    """
    from app.core.cache import get_redis

    redis = await get_redis()
    key = f"{_PASSWORD_RESET_PREFIX}:{token}"

    # Atomic get-and-delete ensures single-use semantics with no race window.
    user_id_str: str | None = await redis.getdel(key)

    if not user_id_str:
        return None

    return UUID(user_id_str)


# Google OAuth CSRF state.
_GOOGLE_OAUTH_STATE_PREFIX = "google_oauth_state"


async def create_google_oauth_state(redirect_uri: str) -> str:
    """Create a single-use CSRF state token for Google OAuth.

    Generates a cryptographically secure random token and stores the
    caller's `redirect_uri` as the Redis value with a 10-minute TTL.
    Binding the URI to the state token prevents open-redirect attacks
    where an attacker substitutes a different callback URI on the return trip.

    No cooldown of its own. `_check_token_rate_limit` keys on a per-user
    identifier, and this flow has no user yet, so it was called with
    `redirect_uri` instead. Every caller of a given deployment sends the
    same one, that is the point of a registered callback URL, so the
    cooldown key was shared by everybody: one request parked
    `rate_limit:google_oauth_state:<the callback URL>` for ten seconds and
    every other Google sign-in in the deployment got a 429 until it
    expired, repeatable indefinitely by an anonymous caller. `/auth/google`
    carries `check_auth_rate_limit` instead, which keys on client IP, the
    same limiter the other unauthenticated auth endpoints use.

    Args:
        redirect_uri: The redirect URI that will be sent to Google.
            Stored in Redis so it can be validated on callback.

    Returns:
        Opaque URL-safe state string (32 bytes, base64url encoded).
    """
    return await _create_oauth_state(
        _GOOGLE_OAUTH_STATE_PREFIX,
        redirect_uri,
        settings.GOOGLE_OAUTH_STATE_TTL_SECONDS,
    )


async def verify_google_oauth_state(state: str) -> str | None:
    """Consume and validate a Google OAuth CSRF state token.

    Atomically retrieves and deletes the token from Redis to enforce
    single-use semantics and close the TOCTOU race window.

    Args:
        state: CSRF state string received in the OAuth callback.

    Returns:
        The bound `redirect_uri` if the token is valid and unexpired,
        `None` if the token is missing or already consumed.
    """
    return await _consume_oauth_state(_GOOGLE_OAUTH_STATE_PREFIX, state)


async def _create_oauth_state(prefix: str, redirect_uri: str, ttl: int) -> str:
    """Store a fresh state token bound to `redirect_uri` under `prefix`."""
    from app.core.cache import get_redis

    redis = await get_redis()
    state = secrets.token_urlsafe(32)
    await redis.setex(f"{prefix}:{state}", ttl, redirect_uri)
    return state


async def _consume_oauth_state(prefix: str, state: str) -> str | None:
    """Return the `redirect_uri` bound to `state` and delete it, once."""
    from app.core.cache import get_redis

    redis = await get_redis()

    # Atomic get-and-delete: no race window between read and delete.
    stored_uri: str | None = await redis.getdel(f"{prefix}:{state}")
    return stored_uri


# Sign in with Apple.
APPLE_ISSUER = "https://appleid.apple.com"
_APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_OAUTH_STATE_PREFIX = "apple_oauth_state"
_APPLE_OAUTH_STATE_TTL_SECONDS = 600
# Apple rotates its signing keys rarely, an hour of caching saves a fetch
# per sign-in. A token naming a key not in the cache refetches early, but
# at most once a minute, so made-up key IDs cannot turn every request into
# a call to Apple.
_APPLE_KEYS_TTL_SECONDS = 3600
_APPLE_KEYS_REFETCH_SECONDS = 60
_apple_keys: dict[str, jwt.PyJWK] = {}
_apple_keys_fetched_at: float | None = None


async def create_apple_oauth_state(redirect_uri: str) -> str:
    """Create a single-use CSRF state token for the Apple web flow.

    Same contract as `create_google_oauth_state`, under its own prefix so
    a Google state cannot complete an Apple sign-in.

    Args:
        redirect_uri: The redirect URI that will be sent to Apple.

    Returns:
        Opaque URL-safe state string.
    """
    return await _create_oauth_state(
        _APPLE_OAUTH_STATE_PREFIX, redirect_uri, _APPLE_OAUTH_STATE_TTL_SECONDS
    )


async def verify_apple_oauth_state(state: str) -> str | None:
    """Consume an Apple CSRF state token.

    Args:
        state: CSRF state string received in the callback.

    Returns:
        The bound `redirect_uri`, `None` if missing or already consumed.
    """
    return await _consume_oauth_state(_APPLE_OAUTH_STATE_PREFIX, state)


def create_apple_client_secret(client_id: str | None = None) -> str:
    """Sign the short-lived client secret Apple's token endpoint expects.

    Apple has no static client secret, it takes a JWT signed with the
    `.p8` key from the developer account, naming the team and the client.

    Args:
        client_id: The client the secret speaks for, the app's bundle ID
            for a code from the app. Defaults to the web Services ID.

    Returns:
        ES256 JWT valid for five minutes.
    """
    issued_at = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "iss": settings.APPLE_TEAM_ID,
            "iat": issued_at,
            "exp": issued_at + 300,
            "aud": APPLE_ISSUER,
            "sub": client_id or settings.APPLE_CLIENT_ID,
        },
        settings.APPLE_PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": settings.APPLE_KEY_ID},
    )


async def _apple_signing_key(key_id: str) -> jwt.PyJWK | None:
    """Return Apple's public key `key_id`, fetching the set when needed.

    Raises:
        httpx.HTTPError: If Apple's key set cannot be fetched.
    """
    import time

    import httpx

    global _apple_keys_fetched_at

    now = time.monotonic()
    age = None if _apple_keys_fetched_at is None else now - _apple_keys_fetched_at
    stale = age is None or age > _APPLE_KEYS_TTL_SECONDS
    unknown = key_id not in _apple_keys and (
        age is None or age > _APPLE_KEYS_REFETCH_SECONDS
    )
    if stale or unknown:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(_APPLE_KEYS_URL)
        response.raise_for_status()
        key_set = jwt.PyJWKSet.from_dict(response.json())
        _apple_keys.clear()
        _apple_keys.update({key.key_id: key for key in key_set.keys if key.key_id})
        _apple_keys_fetched_at = now

    return _apple_keys.get(key_id)


async def verify_apple_identity_token(
    token: str, audience: str, nonce: str | None = None
) -> dict[str, Any]:
    """Verify an Apple identity token and return its claims.

    Checks the signature against Apple's published keys, the issuer, the
    audience and the expiry. With `nonce`, also checks the token was
    minted for it: the client sends Apple the SHA-256 of a random value
    and the server the value itself, so a token lifted from elsewhere
    cannot be replayed without it.

    Args:
        token: The identity token, a JWT.
        audience: The bundle ID for the native flow, the Services ID for
            the web flow.
        nonce: The raw nonce the client hashed into the request, if any.

    Returns:
        The verified claims.

    Raises:
        jwt.InvalidTokenError: If any check fails.
        httpx.HTTPError: If Apple's key set cannot be fetched.
    """
    key_id = jwt.get_unverified_header(token).get("kid")
    key = await _apple_signing_key(key_id) if isinstance(key_id, str) else None
    if key is None:
        raise jwt.InvalidTokenError("Unknown Apple signing key.")

    claims: dict[str, Any] = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=audience,
        issuer=APPLE_ISSUER,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )

    if nonce is not None:
        expected = hashlib.sha256(nonce.encode()).hexdigest()
        if not hmac.compare_digest(str(claims.get("nonce", "")), expected):
            raise jwt.InvalidTokenError("Apple nonce mismatch.")

    return claims


# TOTP / 2FA Utilities.
# Uses pyotp for TOTP generation and verification per RFC 6238.


def generate_totp_secret() -> str:
    """Generate a cryptographically secure base32 TOTP secret.

    Returns:
        Base32-encoded secret string suitable for pyotp.TOTP.
    """
    return pyotp.random_base32()


def build_totp_provisioning_uri(email: str, secret: str) -> str:
    """Build the otpauth:// provisioning URI for QR code generation.

    Args:
        email: User's email address (used as the account name).
        secret: Base32 TOTP secret.

    Returns:
        otpauth:// URI string for use in authenticator apps.
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=settings.PRODUCT_NAME)


def generate_totp_qr_data_url(provisioning_uri: str) -> str:
    """Generate a base64-encoded PNG QR code from a provisioning URI.

    Args:
        provisioning_uri: otpauth:// URI from build_totp_provisioning_uri().

    Returns:
        Data URL string (data:image/png;base64,...) for embedding in HTML/JSON.
    """
    qr: qrcode.QRCode = qrcode.QRCode(  # type: ignore[type-arg]
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    encoded = base64.b64encode(buffer.read()).decode()
    return f"data:image/png;base64,{encoded}"


def verify_totp(secret: str, code: str, valid_window: int = 1) -> bool:
    """Verify a TOTP code against a secret.

    Args:
        secret: Base32 TOTP secret.
        code: 6-digit TOTP code from authenticator app.
        valid_window: Number of 30-second intervals to accept on each side
            of the current time (default 1 allows ±30s clock drift).

    Returns:
        True if code is valid, False otherwise.
    """
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code, valid_window=valid_window))


def generate_recovery_codes(count: int = 10) -> list[str]:
    """Generate plaintext one-time recovery codes for 2FA backup.

    Args:
        count: Number of codes to generate (default 10).

    Returns:
        List of formatted recovery codes (XXXX-XXXX uppercase alphanumeric),
        each unique within the batch. These are shown once and must be
        stored as bcrypt hashes.
    """
    alphabet = string.ascii_uppercase + string.digits
    codes: set[str] = set()
    while len(codes) < count:
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.add(f"{raw[:4]}-{raw[4:]}")
    return list(codes)


# Magic link (passwordless login).
_MAGIC_LINK_PREFIX = "magic_link"


async def create_magic_link_token(user_id: UUID) -> str:
    """Create a single-use magic link token stored in Redis.

    The token expires after ``MAGIC_LINK_TOKEN_TTL_SECONDS`` and is
    consumed (deleted) on first successful verification.

    Args:
        user_id: UUID of the user requesting a login link.

    Returns:
        Opaque URL-safe token string (32 bytes, base64url encoded).

    Raises:
        TokenRateLimitError: If a token was recently created for this user.
    """
    from app.core.cache import get_redis

    redis = await get_redis()
    await _check_token_rate_limit(redis, "magic_link", str(user_id), 30)

    token = secrets.token_urlsafe(32)
    key = f"{_MAGIC_LINK_PREFIX}:{token}"
    await redis.setex(key, settings.MAGIC_LINK_TOKEN_TTL_SECONDS, str(user_id))
    return token


async def verify_magic_link_token(token: str) -> UUID | None:
    """Consume and validate a magic link token.

    Atomically retrieves and deletes the token from Redis to enforce
    single-use semantics. A login link cannot be used twice.

    Args:
        token: Opaque token string from the magic link email.

    Returns:
        User UUID if the token is valid and unexpired, None otherwise.
    """
    from app.core.cache import get_redis

    redis = await get_redis()
    key = f"{_MAGIC_LINK_PREFIX}:{token}"

    # Atomic get-and-delete ensures single-use semantics with no race window.
    user_id_str: str | None = await redis.getdel(key)

    if not user_id_str:
        return None

    return UUID(user_id_str)
