"""Unit tests for Fernet encryption utilities."""

from __future__ import annotations

from app.core.encryption import decrypt
from app.core.encryption import encrypt


class TestEncryption:
    """Tests for encrypt() and decrypt()."""

    def test_encrypt_returns_ciphertext(self) -> None:
        """Encrypted value should differ from plaintext."""
        result = encrypt("hello")
        assert result != "hello"
        assert len(result) > 0

    def test_decrypt_reverses_encrypt(self) -> None:
        """Decrypting an encrypted value should return the original."""
        plaintext = "my-totp-secret-base32"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_different_ciphertexts_for_same_plaintext(self) -> None:
        """Fernet uses random IVs — same input gives different output."""
        c1 = encrypt("test")
        c2 = encrypt("test")
        assert c1 != c2
        assert decrypt(c1) == "test"
        assert decrypt(c2) == "test"
