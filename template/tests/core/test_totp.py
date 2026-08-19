"""Unit tests for TOTP helpers in `app.core.security`."""

from __future__ import annotations

from base64 import b32decode
from unittest.mock import patch

import pyotp

from app.core.security import build_totp_provisioning_uri
from app.core.security import generate_recovery_codes
from app.core.security import generate_totp_secret
from app.core.security import verify_totp
from app.core.settings import settings


class TestGenerateTotpSecret:
    """Tests for generate_totp_secret()."""

    def test_returns_base32_string(self) -> None:
        """Secret should be a non-empty string."""
        secret = generate_totp_secret()
        assert isinstance(secret, str)
        assert len(secret) > 0

    def test_secrets_are_unique(self) -> None:
        """Each call should return a different secret."""
        assert generate_totp_secret() != generate_totp_secret()

    def test_secret_is_valid_base32(self) -> None:
        """Secret should be decodable as base32."""
        secret = generate_totp_secret()
        # pyotp uses base32, must be valid base32 characters.
        padded = secret + "=" * (-len(secret) % 8)
        b32decode(padded)


class TestBuildTotpProvisioningUri:
    """Tests for build_totp_provisioning_uri()."""

    def test_returns_otpauth_uri(self) -> None:
        """URI should start with otpauth://."""
        secret = generate_totp_secret()
        uri = build_totp_provisioning_uri("test@example.com", secret)
        assert uri.startswith("otpauth://totp/")

    def test_uri_contains_email(self) -> None:
        """URI should embed the user's email."""
        secret = generate_totp_secret()
        uri = build_totp_provisioning_uri("alice@example.com", secret)
        assert "alice" in uri

    def test_uri_contains_secret(self) -> None:
        """URI should contain the secret parameter."""
        secret = generate_totp_secret()
        uri = build_totp_provisioning_uri("test@example.com", secret)
        assert f"secret={secret}" in uri


class TestVerifyTotp:
    """Tests for verify_totp()."""

    def test_valid_code_returns_true(self) -> None:
        """A freshly generated code should verify."""
        secret = generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert verify_totp(secret, code) is True

    def test_invalid_code_returns_false(self) -> None:
        """A wrong code should not verify."""
        secret = generate_totp_secret()
        assert verify_totp(secret, "000000") is False

    def test_wrong_secret_returns_false(self) -> None:
        """A code for a different secret should not verify."""
        secret1 = generate_totp_secret()
        secret2 = generate_totp_secret()
        code = pyotp.TOTP(secret1).now()
        assert verify_totp(secret2, code) is False


class TestGenerateRecoveryCodes:
    """Tests for generate_recovery_codes()."""

    def test_returns_ten_codes_by_default(self) -> None:
        """Should return 10 codes by default."""
        codes = generate_recovery_codes()
        assert len(codes) == settings.TOTP_RECOVERY_CODE_COUNT

    def test_custom_count(self) -> None:
        """Should return the requested number of codes."""
        codes = generate_recovery_codes(5)
        assert len(codes) == 5

    def test_code_format_is_xxxx_xxxx(self) -> None:
        """Each code should be in XXXX-XXXX format."""
        for code in generate_recovery_codes():
            parts = code.split("-")
            assert len(parts) == 2
            assert len(parts[0]) == 4
            assert len(parts[1]) == 4

    def test_retries_on_collision(self) -> None:
        """A colliding draw must be discarded and retried, not kept.

        Forces `secrets.choice` to draw the same 8 characters twice in a
        row (a collision under the 36^8 keyspace, astronomically unlikely
        in practice but not impossible) before a third, different draw.
        Without a dedup-and-retry loop, the second draw would silently
        produce a duplicate code instead of a fresh one.
        """
        with patch(
            "app.core.security.secrets.choice",
            side_effect=list("AAAAAAAA") + list("AAAAAAAA") + list("BBBBBBBB"),
        ):
            codes = generate_recovery_codes(2)

        assert set(codes) == {"AAAA-AAAA", "BBBB-BBBB"}

    def test_codes_are_unique(self) -> None:
        """All codes in a batch should be unique."""
        codes = generate_recovery_codes(10)
        assert len(set(codes)) == 10
