"""Unit tests for `Security` module."""

from datetime import datetime
from datetime import timedelta

import pytest

from app.core.security import create_access_token
from app.core.security import create_refresh_token
from app.core.security import decode_token
from app.core.security import generate_api_key
from app.core.security import generate_api_key_id
from app.core.security import generate_password_hash
from app.core.security import mask_api_key
from app.core.security import verify_password


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_generate_password_hash_returns_hash(self):
        """Hash should be generated from password."""
        password = "secure_password_123"
        hashed = generate_password_hash(password)

        assert hashed != password
        assert len(hashed) > 0

    def test_generate_password_hash_different_for_same_input(self):
        """Each hash should be unique due to salt."""
        password = "secure_password_123"
        hash1 = generate_password_hash(password)
        hash2 = generate_password_hash(password)

        assert hash1 != hash2

    def test_verify_password_correct(self):
        """Correct password should verify successfully."""
        password = "secure_password_123"
        hashed = generate_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Incorrect password should fail verification."""
        password = "secure_password_123"
        wrong_password = "wrong_password"
        hashed = generate_password_hash(password)

        assert verify_password(wrong_password, hashed) is False


class TestTokenCreation:
    """Tests for JWT token creation."""

    def test_create_access_token_default_expiry(self):
        """Access token should be created with default expiry."""
        token = create_access_token(subject="user123")

        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_custom_expiry(self):
        """Access token should accept custom expiry."""
        expires_delta = timedelta(hours=2)
        token = create_access_token(subject="user123", expires_delta=expires_delta)

        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_refresh_token(self):
        """Refresh token should be created."""
        token = create_refresh_token(subject="user123")

        assert isinstance(token, str)
        assert len(token) > 0


class TestTokenDecoding:
    """Tests for JWT token decoding."""

    def test_decode_valid_access_token(self):
        """Valid token should decode correctly."""
        subject = "user123"
        token = create_access_token(subject=subject)
        payload = decode_token(token)

        assert payload["sub"] == subject
        assert "exp" in payload
        assert "iat" in payload

    def test_decode_valid_refresh_token(self):
        """Valid refresh token should decode correctly."""
        subject = "user456"
        token = create_refresh_token(subject=subject)
        payload = decode_token(token)

        assert payload["sub"] == subject
        assert payload["type"] == "refresh"

    def test_decode_valid_access_token_includes_iat_and_jti(self):
        """Access tokens should include iat and jti claims."""
        token = create_access_token(subject="user123")
        payload = decode_token(token)

        assert "iat" in payload
        assert datetime.fromtimestamp(payload["iat"])
        assert "jti" in payload
        assert isinstance(payload["jti"], str)
        assert payload["jti"]

    def test_decode_valid_refresh_token_includes_iat_and_jti(self):
        """Refresh tokens should include iat and jti claims."""
        token = create_refresh_token(subject="user123")
        payload = decode_token(token)

        assert "iat" in payload
        assert datetime.fromtimestamp(payload["iat"])
        assert "jti" in payload
        assert isinstance(payload["jti"], str)
        assert payload["jti"]

    def test_decode_invalid_token_raises(self):
        """Invalid token should raise exception."""
        with pytest.raises(Exception):  # noqa: B017
            decode_token("invalid.token.here")


class TestAPIKeyGeneration:
    """Tests for API key generation."""

    def test_generate_api_key_returns_key_and_hash(self):
        """API key generator should return full key and hash."""
        full_key, hashed_key = generate_api_key()

        assert isinstance(full_key, str)
        assert isinstance(hashed_key, str)
        assert full_key.startswith("sk_")
        assert len(full_key) == 35

    def test_generate_api_key_id_returns_id(self):
        """API key ID should be generated."""
        key_id = generate_api_key_id()

        assert isinstance(key_id, str)
        assert len(key_id) >= 25

    def test_mask_api_key_masks_middle(self):
        """API key should be masked in the middle."""
        key = "sk_abc123def456ghi789"
        masked = mask_api_key(key)

        assert masked.startswith("sk_")
        assert "***" in masked
