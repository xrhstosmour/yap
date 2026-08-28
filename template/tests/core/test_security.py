"""Unit tests for `Security` module."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import AsyncMock
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.security import DUMMY_PASSWORD_HASH
from app.core.security import TokenRateLimitError
from app.core.security import _check_token_rate_limit
from app.core.security import blacklist_token
from app.core.security import create_access_token
from app.core.security import create_email_verification_token
from app.core.security import create_google_oauth_state
from app.core.security import create_magic_link_token
from app.core.security import create_password_reset_token
from app.core.security import create_refresh_token
from app.core.security import decode_token
from app.core.security import generate_api_key
from app.core.security import generate_api_key_id
from app.core.security import generate_password_hash
from app.core.security import is_token_blacklisted
from app.core.security import mask_api_key
from app.core.security import verify_email_verification_token
from app.core.security import verify_magic_link_token
from app.core.security import verify_password
from app.core.security import verify_password_reset_token


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_generate_password_hash_returns_hash(self) -> None:
        """Hash should be generated from password."""
        password = "secure_password_123"
        hashed = generate_password_hash(password)

        assert hashed != password
        assert len(hashed) > 0

    def test_generate_password_hash_different_for_same_input(self) -> None:
        """Each hash should be unique due to salt."""
        password = "secure_password_123"
        hash1 = generate_password_hash(password)
        hash2 = generate_password_hash(password)

        assert hash1 != hash2

    def test_verify_password_correct(self) -> None:
        """Correct password should verify successfully."""
        password = "secure_password_123"
        hashed = generate_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self) -> None:
        """Incorrect password should fail verification."""
        password = "secure_password_123"
        wrong_password = "wrong_password"
        hashed = generate_password_hash(password)

        assert verify_password(wrong_password, hashed) is False


class TestTokenCreation:
    """Tests for JWT token creation."""

    def test_create_access_token_default_expiry(self) -> None:
        """Access token should be created with default expiry."""
        token = create_access_token(subject="user123")

        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_custom_expiry(self) -> None:
        """Access token should accept custom expiry."""
        expires_delta = timedelta(hours=2)
        token = create_access_token(subject="user123", expires_delta=expires_delta)

        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_refresh_token(self) -> None:
        """Refresh token should be created."""
        token = create_refresh_token(subject="user123")

        assert isinstance(token, str)
        assert len(token) > 0


class TestTokenDecoding:
    """Tests for JWT token decoding."""

    def test_decode_valid_access_token(self) -> None:
        """Valid token should decode correctly."""
        subject = "user123"
        token = create_access_token(subject=subject)
        payload = decode_token(token)

        assert payload["sub"] == subject
        assert "exp" in payload
        assert "iat" in payload

    def test_decode_valid_refresh_token(self) -> None:
        """Valid refresh token should decode correctly."""
        subject = "user456"
        token = create_refresh_token(subject=subject)
        payload = decode_token(token)

        assert payload["sub"] == subject
        assert payload["type"] == "refresh"

    def test_decode_valid_access_token_includes_iat_and_jti(self) -> None:
        """Access tokens should include iat and jti claims."""
        token = create_access_token(subject="user123")
        payload = decode_token(token)

        assert "iat" in payload
        assert datetime.fromtimestamp(payload["iat"])
        assert "jti" in payload
        assert isinstance(payload["jti"], str)
        assert payload["jti"]

    def test_decode_valid_refresh_token_includes_iat_and_jti(self) -> None:
        """Refresh tokens should include iat and jti claims."""
        token = create_refresh_token(subject="user123")
        payload = decode_token(token)

        assert "iat" in payload
        assert datetime.fromtimestamp(payload["iat"])
        assert "jti" in payload
        assert isinstance(payload["jti"], str)
        assert payload["jti"]

    def test_decode_invalid_token_raises(self) -> None:
        """Invalid token should raise exception."""
        with pytest.raises(Exception):  # noqa: B017
            decode_token("invalid.token.here")


class TestAPIKeyGeneration:
    """Tests for API key generation."""

    def test_generate_api_key_returns_key_and_hash(self) -> None:
        """API key generator should return full key and hash."""
        full_key, hashed_key = generate_api_key()

        assert isinstance(full_key, str)
        assert isinstance(hashed_key, str)
        assert full_key.startswith("sk_")
        assert len(full_key) == 35

    def test_generate_api_key_id_returns_id(self) -> None:
        """API key ID should be generated."""
        key_id = generate_api_key_id()

        assert isinstance(key_id, str)
        assert len(key_id) >= 25

    def test_mask_api_key_masks_middle(self) -> None:
        """API key should be masked in the middle."""
        key = "sk_abc123def456ghi789"
        masked = mask_api_key(key)

        assert masked.startswith("sk_")
        assert "***" in masked


# Password edge cases


class TestPasswordEdgeCases:
    """Additional tests for password hashing edge cases."""

    def test_generate_password_hash_too_long_raises_value_error(self) -> None:
        """Password > 128 characters should raise ValueError."""
        long_password = "a" * 129

        with pytest.raises(ValueError, match="exceeds maximum length"):
            generate_password_hash(long_password)

    def test_dummy_password_hash_is_valid_for_verification(self) -> None:
        """DUMMY_PASSWORD_HASH should be a valid bcrypt hash that verifies
        without errors, used for timing attack protection."""
        # Should not raise; always returns False for arbitrary input.
        result = verify_password("any_password", DUMMY_PASSWORD_HASH)

        assert result is False


# Token rate limiting


class TestTokenRateLimit:
    """Tests for _check_token_rate_limit and TokenRateLimitError."""

    @pytest.mark.asyncio
    async def test_rate_limit_not_hit(self) -> None:
        """When redis.set returns non-None, no exception is raised."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value="OK")

        # Should not raise.
        await _check_token_rate_limit(mock_redis, "test", "user-1", 60)

        mock_redis.set.assert_awaited_once_with(
            "rate_limit:test:user-1", "1", nx=True, ex=60
        )

    @pytest.mark.asyncio
    async def test_rate_limit_hit_raises_token_rate_limit_error(self) -> None:
        """When redis.set returns None (key already exists),
        TokenRateLimitError is raised."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=None)

        with pytest.raises(TokenRateLimitError, match="Token creation rate limited"):
            await _check_token_rate_limit(mock_redis, "test", "user-1", 60)


# Redis-backed token management (shared fixtures)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Return an AsyncMock that mimics a redis.asyncio.Redis client."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value="OK")
    redis.setex = AsyncMock()
    redis.getdel = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    return redis


class TestJWTBlacklist:
    """Tests for JWT blacklist helper functions."""

    @pytest.mark.asyncio
    async def test_blacklist_token_stores_identifier_with_ttl(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """blacklist_token should store key with remaining TTL."""
        expires_at = datetime.now(UTC) + timedelta(minutes=5)

        await blacklist_token("test-jti", expires_at)

        mock_redis.setex.assert_awaited_once()
        call_arguments = mock_redis.setex.call_args[0]
        assert call_arguments[0] == "jwt_blacklist:test-jti"
        assert isinstance(call_arguments[1], int)
        assert call_arguments[1] > 0
        assert call_arguments[2] == "1"

    @pytest.mark.asyncio
    async def test_blacklist_token_skips_expired_token(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """blacklist_token should skip already expired tokens."""
        expires_at = datetime.now(UTC) - timedelta(seconds=1)

        await blacklist_token("expired-jti", expires_at)

        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_returns_true(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """is_token_blacklisted should return True when key exists."""
        mock_redis.exists = AsyncMock(return_value=1)

        result = await is_token_blacklisted("known-jti")

        assert result is True
        mock_redis.exists.assert_awaited_once_with("jwt_blacklist:known-jti")

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_returns_false(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """is_token_blacklisted should return False when key does not exist."""
        mock_redis.exists = AsyncMock(return_value=0)

        result = await is_token_blacklisted("unknown-jti")

        assert result is False

    @pytest.mark.asyncio
    async def test_blacklist_token_swallows_redis_connection_error(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """blacklist_token should not raise when Redis is unreachable."""
        mock_redis.setex = AsyncMock(side_effect=RedisConnectionError("refused"))
        expires_at = datetime.now(UTC) + timedelta(minutes=5)

        await blacklist_token("jti-conn-err", expires_at)

    @pytest.mark.asyncio
    async def test_blacklist_token_swallows_redis_timeout_error(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """blacklist_token should not raise on Redis timeout."""
        mock_redis.setex = AsyncMock(side_effect=RedisTimeoutError("timed out"))
        expires_at = datetime.now(UTC) + timedelta(minutes=5)

        await blacklist_token("jti-timeout", expires_at)

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_returns_false_on_redis_connection_error(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """is_token_blacklisted should fail open when Redis is unreachable."""
        mock_redis.exists = AsyncMock(side_effect=RedisConnectionError("refused"))

        result = await is_token_blacklisted("jti-conn-err")

        assert result is False

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_returns_false_on_redis_timeout(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """is_token_blacklisted should fail open on Redis timeout."""
        mock_redis.exists = AsyncMock(side_effect=RedisTimeoutError("timed out"))

        result = await is_token_blacklisted("jti-timeout")

        assert result is False


@pytest.fixture
def _patch_get_redis(mock_redis: AsyncMock) -> None:
    """Patch get_redis() to return the mocked Redis client."""
    with patch(
        "app.core.cache.get_redis",
        AsyncMock(return_value=mock_redis),
    ):
        yield


# Email verification tokens


class TestEmailVerificationTokens:
    """Tests for create/verify email verification tokens."""

    @pytest.mark.asyncio
    async def test_create_email_verification_token(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Token is created, rate-limited, and stored in Redis."""
        user_id = uuid4()

        token = await create_email_verification_token(user_id)

        # Rate limit check was called.
        mock_redis.set.assert_awaited_once_with(
            f"rate_limit:email_verification:{user_id}", "1", nx=True, ex=60
        )
        # Token was stored with the expected prefix and user_id value.
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0].startswith("email_verification:")
        assert call_args[0][1] == 86400  # 24h TTL default
        assert call_args[0][2] == str(user_id)
        # Returned token is a non-empty string.
        assert isinstance(token, str)
        assert len(token) > 0
        # The token is part of the Redis key.
        assert token in call_args[0][0]

    @pytest.mark.asyncio
    async def test_verify_email_verification_token_valid(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Valid token returns the stored user UUID."""
        user_id = uuid4()
        mock_redis.getdel = AsyncMock(return_value=str(user_id))

        result = await verify_email_verification_token("valid-token")

        assert result == user_id
        mock_redis.getdel.assert_awaited_once_with("email_verification:valid-token")

    @pytest.mark.asyncio
    async def test_verify_email_verification_token_invalid(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Invalid or consumed token returns None."""
        mock_redis.getdel = AsyncMock(return_value=None)

        result = await verify_email_verification_token("invalid-token")

        assert result is None


# Password reset tokens


class TestPasswordResetTokens:
    """Tests for create/verify password reset tokens."""

    @pytest.mark.asyncio
    async def test_create_password_reset_token(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Token is created, rate-limited, and stored in Redis."""
        user_id = uuid4()

        token = await create_password_reset_token(user_id)

        mock_redis.set.assert_awaited_once_with(
            f"rate_limit:password_reset:{user_id}", "1", nx=True, ex=60
        )
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0].startswith("password_reset:")
        assert call_args[0][1] == 3600  # 1h TTL default
        assert call_args[0][2] == str(user_id)
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio
    async def test_verify_password_reset_token_valid(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Valid token returns the stored user UUID."""
        user_id = uuid4()
        mock_redis.getdel = AsyncMock(return_value=str(user_id))

        result = await verify_password_reset_token("valid-token")

        assert result == user_id
        mock_redis.getdel.assert_awaited_once_with("password_reset:valid-token")

    @pytest.mark.asyncio
    async def test_verify_password_reset_token_invalid(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Invalid or consumed token returns None."""
        mock_redis.getdel = AsyncMock(return_value=None)

        result = await verify_password_reset_token("invalid-token")

        assert result is None


# Magic link tokens


class TestMagicLinkTokens:
    """Tests for create/verify magic link tokens."""

    @pytest.mark.asyncio
    async def test_create_magic_link_token(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Token is created, rate-limited, and stored in Redis."""
        user_id = uuid4()

        token = await create_magic_link_token(user_id)

        mock_redis.set.assert_awaited_once_with(
            f"rate_limit:magic_link:{user_id}", "1", nx=True, ex=30
        )
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0].startswith("magic_link:")
        assert call_args[0][1] == 900  # 15min TTL default
        assert call_args[0][2] == str(user_id)
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio
    async def test_verify_magic_link_token_valid(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Valid token returns the stored user UUID."""
        user_id = uuid4()
        mock_redis.getdel = AsyncMock(return_value=str(user_id))

        result = await verify_magic_link_token("valid-token")

        assert result == user_id
        mock_redis.getdel.assert_awaited_once_with("magic_link:valid-token")

    @pytest.mark.asyncio
    async def test_verify_magic_link_token_invalid(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """Invalid or consumed token returns None."""
        mock_redis.getdel = AsyncMock(return_value=None)

        result = await verify_magic_link_token("invalid-token")

        assert result is None


# JWT edge cases


class TestJWTEdgeCases:
    """Edge case tests for JWT token decoding."""

    def test_decode_expired_token_raises_jwt_error(self) -> None:
        """Expired token should raise InvalidTokenError."""
        token = create_access_token(
            subject="user123", expires_delta=timedelta(seconds=-1)
        )

        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token)

    def test_decode_invalid_signature_raises_jwt_error(self) -> None:
        """Tampered token should raise InvalidTokenError."""
        token = create_access_token(subject="user123")
        # Modify the payload section to break the signature.
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1][:-1] + "X" + "." + parts[2]

        with pytest.raises(jwt.InvalidTokenError):
            decode_token(tampered)

    def test_decode_invalid_payload_raises_jwt_error(self) -> None:
        """Garbage token should raise InvalidTokenError."""
        with pytest.raises(jwt.InvalidTokenError):
            decode_token("not.a.valid.jwt")


# Access token additional_claims


class TestAccessTokenAdditionalClaims:
    """Tests for create_access_token with additional_claims."""

    def test_create_access_token_with_additional_claims(self) -> None:
        """Additional claims should be embedded in the token payload."""
        claims = {"role": "admin", "tenant": "t-123"}
        token = create_access_token(subject="user123", additional_claims=claims)
        payload = decode_token(token)

        assert payload["role"] == "admin"
        assert payload["tenant"] == "t-123"
        assert payload["sub"] == "user123"
        assert payload["type"] == "access"

    def test_create_access_token_with_explicit_expires_delta(self) -> None:
        """Custom expires_delta should result in correct expiration window."""
        from datetime import UTC
        from datetime import datetime

        delta = timedelta(minutes=10)
        token = create_access_token(subject="user123", expires_delta=delta)
        payload = decode_token(token)

        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
        assert exp > now
        # Should expire within ~12 minutes of now (allow clock skew).
        assert exp <= now + timedelta(minutes=12)


# Refresh token explicit expires_delta and additional_claims


class TestRefreshTokenVariants:
    """Tests for create_refresh_token with custom options."""

    def test_create_refresh_token_with_explicit_expires_delta(self) -> None:
        """Custom expires_delta on refresh token should be respected."""
        from datetime import UTC
        from datetime import datetime

        delta = timedelta(days=1)
        token = create_refresh_token(subject="user123", expires_delta=delta)
        payload = decode_token(token)

        assert payload["type"] == "refresh"
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
        assert exp > now
        assert exp <= now + timedelta(days=2)

    def test_create_refresh_token_with_additional_claims(self) -> None:
        """Additional claims should be embedded in the refresh token payload."""
        claims = {"session_id": "abc-123"}
        token = create_refresh_token(subject="user456", additional_claims=claims)
        payload = decode_token(token)

        assert payload["type"] == "refresh"
        assert payload["session_id"] == "abc-123"
        assert payload["sub"] == "user456"


# TOTP / 2FA


class TestTOTP:
    """Tests for TOTP secret generation, verification, and provisioning URI."""

    def test_generate_totp_secret_returns_base32_string(self) -> None:
        """generate_totp_secret should return a non-empty base32 string."""
        from app.core.security import generate_totp_secret

        secret = generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) > 0
        # Base32 alphabet: A-Z and 2-7
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)

    def test_verify_totp_correct_code_returns_true(self) -> None:
        """verify_totp with the current correct code should return True."""
        import pyotp

        from app.core.security import verify_totp

        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        assert verify_totp(secret, code) is True

    def test_verify_totp_wrong_code_returns_false(self) -> None:
        """verify_totp with an incorrect code should return False."""
        import pyotp

        from app.core.security import verify_totp

        secret = pyotp.random_base32()
        # Generate a code that's definitely wrong (out of valid_window).
        assert verify_totp(secret, "000000") is False

    def test_build_totp_provisioning_uri_contains_username_and_issuer(self) -> None:
        """build_totp_provisioning_uri should return a valid otpauth:// URI."""
        from app.core.security import build_totp_provisioning_uri
        from app.core.security import generate_totp_secret

        secret = generate_totp_secret()
        email = "test@example.com"
        uri = build_totp_provisioning_uri(email, secret)

        from urllib.parse import quote

        assert uri.startswith("otpauth://totp/")
        # pyotp URL-encodes the name/email in the URI.
        assert quote(email) in uri
        assert "issuer" in uri.lower()

    def test_generate_recovery_codes_returns_10_formatted_codes(self) -> None:
        """generate_recovery_codes should return 10 codes in XXXX-XXXX format."""
        from app.core.security import generate_recovery_codes

        codes = generate_recovery_codes()
        assert isinstance(codes, list)
        assert len(codes) == 10
        for code in codes:
            assert isinstance(code, str)
            assert len(code) == 9  # XXXX-XXXX
            assert code[4] == "-"
            # All chars should be uppercase alphanumeric.
            assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in code)


# DUMMY_PASSWORD_HASH always returns False (already in TestPasswordEdgeCases,
# but explicit verification_with_any_password test)


class TestDummyPasswordHash:
    """Explicit tests for DUMMY_PASSWORD_HASH behavior."""

    def test_verify_password_with_dummy_hash_always_false(self) -> None:
        """verify_password with DUMMY_PASSWORD_HASH returns False for any input."""
        from app.core.security import DUMMY_PASSWORD_HASH
        from app.core.security import verify_password

        assert verify_password("anything", DUMMY_PASSWORD_HASH) is False
        assert verify_password("", DUMMY_PASSWORD_HASH) is False
        assert (
            verify_password("correcthorsebatterystaple", DUMMY_PASSWORD_HASH) is False
        )


class TestGoogleOAuthStateHasNoSharedCooldown:
    """The Google state token must not throttle the whole deployment.

    `_check_token_rate_limit` keys on a per-user identifier, but this flow
    has no user yet, so it was called with `redirect_uri`. Every caller of
    a deployment sends the same registered callback URL, so one request
    parked the shared cooldown key and every other Google sign-in got a
    429 until it expired, repeatable indefinitely by anyone.
    """

    @pytest.mark.asyncio
    async def test_two_callers_sharing_a_redirect_uri_both_get_a_state(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """A second caller must not be blocked by the first.

        Args:
            mock_redis: Mocked Redis client.
            _patch_get_redis: Fixture patching `get_redis`.
        """
        redirect_uri = "https://app.example.com/auth/callback"

        # A cooldown key set by the first caller would already exist, so
        # `SET NX` returns None for the second. That is what used to raise.
        mock_redis.set = AsyncMock(return_value=None)

        first = await create_google_oauth_state(redirect_uri)
        second = await create_google_oauth_state(redirect_uri)

        assert first != second
        assert mock_redis.setex.await_count == 2

    @pytest.mark.asyncio
    async def test_no_cooldown_key_is_written(
        self, mock_redis: AsyncMock, _patch_get_redis: None
    ) -> None:
        """No shared cooldown key means nothing to park.

        Args:
            mock_redis: Mocked Redis client.
            _patch_get_redis: Fixture patching `get_redis`.
        """
        await create_google_oauth_state("https://app.example.com/auth/callback")

        assert not any(
            "rate_limit" in str(call) for call in mock_redis.set.await_args_list
        )
