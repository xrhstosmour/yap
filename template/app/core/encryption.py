"""Field-level encryption for PII and sensitive data.

Provides symmetric encryption using Fernet (AES-128-CBC + HMAC-SHA256)
for encrypting fields at the application layer before database storage.

GDPR-ready features:
- Encrypt/decrypt individual fields (email, phone, name, etc.)
- Key rotation support via key versioning
- Transparent serialization of encrypted values
- Deterministic mode for searchable encrypted fields (uses HMAC)

Usage
-----
Basic encrypt/decrypt::

    from app.core.encryption import crypto

    encrypted = crypto.encrypt("user@yap.com")
    decrypted = crypto.decrypt(encrypted)

Module-level convenience wrappers::

    from app.core.encryption import encrypt, decrypt

    encrypted = encrypt("my-totp-secret")
    original = decrypt(encrypted)

Transparent model field pattern (used by `User.email` / `User.phone`)::

    from app.core.encryption import EncryptedString

    class User(BaseModel, table=True):
        # The column stores ciphertext; the Python attribute always
        # reads/writes plain text, encryption happens at the SQLAlchemy
        # bind/result boundary, transparently to the rest of the app.
        email: EmailStr = Field(sa_type=EncryptedString(512))

Searchable encrypted field (deterministic)::

    search_token = crypto.hash_for_search("user@yap.com")
    # Store search_token in a companion `*_hash` column (e.g. `email_hash`)
    # alongside the encrypted value, and filter on it for equality lookups,
    # the encrypted column itself cannot be searched or indexed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any


from cryptography.fernet import Fernet
from cryptography.fernet import MultiFernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class CryptoService:
    """Field-level encryption service with key rotation support.

    Uses Fernet for symmetric encryption. Supports multiple keys
    for rotation, new data is encrypted with the primary key,
    old data can be decrypted with any valid key.
    """

    def __init__(self, encryption_keys: list[str] | None = None) -> None:
        """Initialize encryption service.

        Args:
            encryption_keys: List of Fernet keys. First key is primary.
                             If None, uses CRYPTO_KEY from environment.
        """
        if encryption_keys is None:
            from app.core.settings import settings

            key = getattr(settings, "CRYPTO_KEY", None)
            if key:
                encryption_keys = [key]

        self._keys: list[str] = encryption_keys or []
        if self._keys:
            fernets = []
            for k in self._keys:
                key_str = k.encode() if isinstance(k, str) else k
                try:
                    key_bytes = base64.urlsafe_b64decode(key_str)
                    if len(key_bytes) != 32:
                        raise ValueError(
                            "Encryption key must be exactly 32 bytes (44 base64 chars)"
                        )
                except Exception as e:
                    raise ValueError(f"Invalid CRYPTO_KEY: {e}") from e
                fernets.append(Fernet(key_str))
            self._fernet: Fernet | MultiFernet | None = (
                MultiFernet(fernets) if len(fernets) > 1 else fernets[0]
            )
        else:
            self._fernet = None

    @property
    def available(self) -> bool:
        """Check if encryption is configured."""
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        """Encrypt a string value.

        Args:
            value: Plain text to encrypt.

        Returns:
            Base64-encoded encrypted value prefixed with "enc:".

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if self._fernet is None:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        encrypted = self._fernet.encrypt(value.encode())
        return "enc:" + encrypted.decode()

    def decrypt(self, value: str) -> str:
        """Decrypt an encrypted string value.

        Args:
            value: Encrypted value (with or without "enc:" prefix).

        Returns:
            Decrypted plain text.

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if self._fernet is None:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        if value.startswith("enc:"):
            value = value[4:]
        return self._fernet.decrypt(value.encode()).decode()

    def hash_for_search(self, value: str) -> str:
        """Create deterministic hash for searching encrypted fields.

        Uses HMAC-SHA256 with the primary Fernet key as the HMAC key.
        The same input always produces the same hash, enabling equality
        lookups on encrypted columns.

        Only reflects the current primary key. A value hashed before a
        key rotation no longer matches the hash this returns afterwards,
        use `hash_candidates_for_search()` for lookups that must survive
        rotation.

        Args:
            value: Value to hash.

        Returns:
            Base64-encoded HMAC hash prefixed with "hmac:".

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if not self._keys:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        return self._hash_with_key(value, self._keys[0])

    def hash_candidates_for_search(self, value: str) -> list[str]:
        """Create a deterministic hash of `value` under every configured key.

        Mirrors `decrypt()`'s try-every-key approach to key rotation:
        `hash_for_search()` alone only reflects the current primary key,
        so a value hashed before a rotation no longer matches after one.
        Lookups that must keep matching across rotation (e.g. filtering
        `User.email_hash`) should compare against ANY of these candidates
        instead of a single `hash_for_search()` value.

        Args:
            value: Value to hash.

        Returns:
            List of base64-encoded HMAC hashes prefixed with "hmac:",
            one per configured key, primary key first.

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if not self._keys:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        return [self._hash_with_key(value, key) for key in self._keys]

    def _hash_with_key(self, value: str, key: str) -> str:
        """Compute the deterministic search hash of `value` under one key."""
        # Derive a separate HMAC key via domain separation, never reuse
        # the Fernet encryption key directly as an HMAC key.
        key_bytes = base64.urlsafe_b64decode(key)
        hmac_key = hashlib.sha256(key_bytes + b":search-hmac").digest()
        h = hmac.new(hmac_key, value.encode(), hashlib.sha256)
        return "hmac:" + base64.urlsafe_b64encode(h.digest()).decode()


class EncryptedString(TypeDecorator[str]):
    """SQLAlchemy column type that transparently encrypts values at rest.

    Wraps a `String` column so application code reads and writes plain
    text while the database only ever stores ciphertext. Encryption
    happens on bind (write) via `crypto.encrypt()` and decryption
    happens on load (read) via `crypto.decrypt()`, using the global
    `crypto` `CryptoService` instance.

    Because Fernet ciphertext is randomised (unique IV per call), this
    type is not suitable for columns that need equality lookups or
    uniqueness constraints, pair it with a deterministic HMAC hash
    column (see `CryptoService.hash_for_search()`) for that purpose.

    Example::

        class User(BaseModel, table=True):
            email: EmailStr = Field(sa_type=EncryptedString(512))
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        """Encrypt the plain text value before it is written to the database."""
        if value is None:
            return None
        return crypto.encrypt(str(value))

    def process_result_value(self, value: Any, dialect: Any) -> str | None:
        """Decrypt the stored ciphertext after it is read from the database."""
        if value is None:
            return None
        return crypto.decrypt(str(value))


def generate_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        Base64-encoded Fernet key suitable for CRYPTO_KEY.
    """
    return Fernet.generate_key().decode()


# Global encryption service instance, initialized from settings.CRYPTO_KEY.
crypto = CryptoService()


def encrypt(value: str) -> str:
    """Encrypt a value using the global CryptoService instance.

    Convenience wrapper around crypto.encrypt() for direct import.

    Args:
        value: Plain text string to encrypt.

    Returns:
        Encrypted string prefixed with "enc:".
    """
    return crypto.encrypt(value)


def decrypt(value: str) -> str:
    """Decrypt a value using the global CryptoService instance.

    Convenience wrapper around crypto.decrypt() for direct import.

    Args:
        value: Encrypted string (with or without "enc:" prefix).

    Returns:
        Original plain text string.
    """
    return crypto.decrypt(value)
