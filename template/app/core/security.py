"""Security utilities for authentication and authorization.

This module provides secure password hashing, JWT token management,
and API key utilities. Uses Argon2 with bcrypt fallback for password
hashing, following industry best practices.
"""

from __future__ import annotations

import secrets
import string
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

import bcrypt
from jose import jwt

from app.core.settings import settings

if TYPE_CHECKING:
    from uuid import UUID

# Password hashing configuration.
# Using bcrypt directly as it's well-audited and production-proven.


def generate_password_hash(password: str) -> str:
    """Hash a password using bcrypt.

    Uses bcrypt with automatic salt generation. The resulting hash
    includes the salt and can be verified using verify_password.

    Args:
        password: Plain text password to hash

    Returns:
        Bcrypt hash string suitable for storage

    Note:
        bcrypt is intentionally slow (cost factor) to resist brute force attacks.
        The default cost factor takes ~250ms per hash on modern hardware.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    Safely compares the plain password with the stored hash using
    constant-time comparison to prevent timing attacks.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Previously stored hash to compare against

    Returns:
        True if password matches, False otherwise
    """
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


# JWT Token Management.

ALGORITHM = settings.ALGORITHM


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
    }

    if additional_claims:
        to_encode.update(additional_claims)

    encoded_jwt = cast(
        str,
        jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM),
    )
    return encoded_jwt


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
    }

    if additional_claims:
        to_encode.update(additional_claims)

    encoded_jwt = cast(
        str,
        jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM),
    )
    return encoded_jwt


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
        jwt.InvalidTokenError: If token is invalid
    """
    payload = cast(
        dict[str, Any],
        jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM]),
    )
    return payload


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
    hashed_key = generate_password_hash(full_key)

    return full_key, hashed_key


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


# Dummy hash for timing attack prevention.
# Used when user/key is not found to maintain consistent response time.
DUMMY_PASSWORD_HASH = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.VTMBQdxfZvPLOa"
