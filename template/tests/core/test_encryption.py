"""Unit tests for Fernet encryption utilities."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken as FernetInvalidToken

from app.core.encryption import CryptoService
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
        """Fernet uses random IVs, same input gives different output."""
        c1 = encrypt("test")
        c2 = encrypt("test")
        assert c1 != c2
        assert decrypt(c1) == "test"
        assert decrypt(c2) == "test"


class TestCryptoService:
    """Tests for CryptoService with explicit keys (no global dependency)."""

    #  No keys / availability

    def test_available_false_when_no_keys(self) -> None:
        """CryptoService with no keys should report unavailable."""
        svc = CryptoService(encryption_keys=[])
        assert svc.available is False

    def test_encrypt_raises_when_no_keys(self) -> None:
        """Encrypt without keys should raise RuntimeError."""
        svc = CryptoService(encryption_keys=[])
        with pytest.raises(RuntimeError, match="Encryption not configured"):
            svc.encrypt("hello")

    def test_decrypt_raises_when_no_keys(self) -> None:
        """Decrypt without keys should raise RuntimeError."""
        svc = CryptoService(encryption_keys=[])
        with pytest.raises(RuntimeError, match="Encryption not configured"):
            svc.decrypt("enc:abc")

    def test_hash_for_search_raises_when_no_keys(self) -> None:
        """hash_for_search without keys should raise RuntimeError."""
        svc = CryptoService(encryption_keys=[])
        with pytest.raises(RuntimeError, match="Encryption not configured"):
            svc.hash_for_search("hello")

    #  Invalid key validation

    def test_invalid_key_raises_value_error(self) -> None:
        """Invalid base64 key should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid CRYPTO_KEY"):
            CryptoService(encryption_keys=["not-valid-base64!!!!"])

    def test_wrong_length_key_raises_value_error(self) -> None:
        """Key that decodes to incorrect length should raise ValueError."""
        bad_key = base64.urlsafe_b64encode(b"too-short").decode()
        with pytest.raises(ValueError, match="must be exactly 32 bytes"):
            CryptoService(encryption_keys=[bad_key])

    def test_invalid_key_from_settings_raises_value_error(self) -> None:
        """Default init with invalid CRYPTO_KEY in settings raises ValueError."""
        from app.core.settings import settings

        original_key = settings.CRYPTO_KEY
        settings.CRYPTO_KEY = "!!!not-valid!!!"
        try:
            with pytest.raises(ValueError, match="Invalid CRYPTO_KEY"):
                CryptoService()
        finally:
            settings.CRYPTO_KEY = original_key

    #  hash_for_search

    def test_hash_for_search_is_deterministic(self) -> None:
        """Same input always produces the same hash."""
        key = Fernet.generate_key().decode()
        svc = CryptoService(encryption_keys=[key])
        h1 = svc.hash_for_search("user@yap.com")
        h2 = svc.hash_for_search("user@yap.com")
        assert h1 == h2

    def test_hash_for_search_different_inputs(self) -> None:
        """Different inputs produce different hashes."""
        key = Fernet.generate_key().decode()
        svc = CryptoService(encryption_keys=[key])
        h1 = svc.hash_for_search("alice@yap.com")
        h2 = svc.hash_for_search("bob@yap.com")
        assert h1 != h2

    def test_hash_for_search_domain_separation(self) -> None:
        """HMAC uses derived key (domain separation), not raw encryption key."""
        key = Fernet.generate_key().decode()
        svc = CryptoService(encryption_keys=[key])

        # Compute what a naive HMAC would produce using the raw key directly
        key_bytes = base64.urlsafe_b64decode(key)
        naive = hmac.new(key_bytes, b"hello", hashlib.sha256).digest()
        naive_hash = "hmac:" + base64.urlsafe_b64encode(naive).decode()

        actual_hash = svc.hash_for_search("hello")

        # Domain separation must produce a different result
        assert actual_hash != naive_hash

    #  Key rotation with MultiFernet

    def test_key_rotation_decrypt_old_ciphertext(self) -> None:
        """Ciphertext encrypted with old key is decryptable after rotation."""
        key1 = Fernet.generate_key().decode()  # old key
        key2 = Fernet.generate_key().decode()  # new key

        # Encrypt with old key only
        old_svc = CryptoService(encryption_keys=[key1])
        ciphertext = old_svc.encrypt("secret")

        # After rotation: key2 is primary, key1 still valid for decryption
        rotated_svc = CryptoService(encryption_keys=[key2, key1])
        assert rotated_svc.decrypt(ciphertext) == "secret"

    def test_key_rotation_encrypt_with_new_decrypt_with_both(self) -> None:
        """After rotation, encrypts with new primary; both old and new decrypt."""
        key1 = Fernet.generate_key().decode()  # old
        key2 = Fernet.generate_key().decode()  # new primary

        # Pre-rotation: encrypt old data with key1 only
        old_svc = CryptoService(encryption_keys=[key1])
        old_ciphertext = old_svc.encrypt("old-data")

        # After rotation: key2 is primary, key1 still valid for decryption
        rotated = CryptoService(encryption_keys=[key2, key1])

        # Encrypt new data, uses primary key2
        new_ciphertext = rotated.encrypt("new-data")

        # Both ciphertexts are decryptable by the rotated service
        assert rotated.decrypt(old_ciphertext) == "old-data"
        assert rotated.decrypt(new_ciphertext) == "new-data"

        # Old-only service cannot decrypt data encrypted with new primary
        old_only_svc = CryptoService(encryption_keys=[key1])
        with pytest.raises(FernetInvalidToken):
            old_only_svc.decrypt(new_ciphertext)

    def test_key_rotation_old_only_cannot_decrypt_new(self) -> None:
        """Service with only old key cannot decrypt new-primary ciphertext."""
        key1 = Fernet.generate_key().decode()  # old
        key2 = Fernet.generate_key().decode()  # new primary

        # Encrypt with rotated service (key2 primary)
        rotated_svc = CryptoService(encryption_keys=[key2, key1])
        ciphertext = rotated_svc.encrypt("secret")

        # Old-only service cannot decrypt (Fernet encrypts with primary)
        old_only_svc = CryptoService(encryption_keys=[key1])
        with pytest.raises(FernetInvalidToken):
            old_only_svc.decrypt(ciphertext)

    def test_hash_candidates_for_search_lookup_survives_rotation(self) -> None:
        """A value hashed under the old primary key must still be found
        via `hash_candidates_for_search()` after that key is rotated out.

        `hash_for_search()` alone only reflects the current primary key,
        so an equality lookup against a single `hash_for_search()` value
        breaks for every row hashed before the rotation, even though
        `decrypt()` on the same row's ciphertext still works fine. This
        proves the fix: `hash_candidates_for_search()` includes a
        candidate for every configured key, so the pre-rotation hash is
        always one of them.
        """
        key1 = Fernet.generate_key().decode()  # old, primary at hash time.
        key2 = Fernet.generate_key().decode()  # new, primary after rotation.

        # Pre-rotation: hash computed while key1 was primary, this is
        # what would have been stored in the `email_hash` column.
        old_svc = CryptoService(encryption_keys=[key1])
        stored_hash = old_svc.hash_for_search("user@yap.com")

        # After rotation: key2 is primary, key1 retained for old data.
        rotated_svc = CryptoService(encryption_keys=[key2, key1])

        # A single hash_for_search() no longer matches the stored value.
        assert rotated_svc.hash_for_search("user@yap.com") != stored_hash

        # But the stored hash is among the rotated service's candidates,
        # so a `column_hash.in_(candidates)` lookup still finds the row.
        candidates = rotated_svc.hash_candidates_for_search("user@yap.com")
        assert stored_hash in candidates

    #  Edge cases

    def test_encrypt_large_data(self) -> None:
        """Encrypting large data should work correctly."""
        key = Fernet.generate_key().decode()
        svc = CryptoService(encryption_keys=[key])
        large = "x" * 100_000  # 100 KB
        ciphertext = svc.encrypt(large)
        assert svc.decrypt(ciphertext) == large

    def test_decrypt_corrupted_ciphertext(self) -> None:
        """Corrupted ciphertext should raise InvalidToken."""
        key = Fernet.generate_key().decode()
        svc = CryptoService(encryption_keys=[key])
        ciphertext = svc.encrypt("secret")

        # Mangle the ciphertext
        corrupted = ciphertext[:10] + "X" + ciphertext[11:]
        with pytest.raises(FernetInvalidToken):
            svc.decrypt(corrupted)

    def test_decrypt_corrupted_without_prefix(self) -> None:
        """Random string passed to decrypt raises InvalidToken."""
        key = Fernet.generate_key().decode()
        svc = CryptoService(encryption_keys=[key])
        with pytest.raises(FernetInvalidToken):
            svc.decrypt("not-a-valid-fernet-token-at-all")
