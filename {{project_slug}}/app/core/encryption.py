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

    encrypted = crypto.encrypt("user@example.com")
    decrypted = crypto.decrypt(encrypted)

Model field pattern::

    from app.core.encryption import EncryptedField

    class User(BaseModel, table=True):
        email_encrypted: str = Field(...)

        @property
        def email(self) -> str:
            return crypto.decrypt(self.email_encrypted)

        @email.setter
        def email(self, value: str):
            self.email_encrypted = crypto.encrypt(value)

Searchable encrypted field (deterministic)::

    search_token = crypto.hash_for_search("user@example.com")
    # Store search_token alongside encrypted value for lookups
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet
from cryptography.fernet import MultiFernet


class CryptoService:
    """Field-level encryption service with key rotation support.

    Uses Fernet for symmetric encryption. Supports multiple keys
    for rotation — new data is encrypted with the primary key,
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
            if not key:
                key = os.environ.get("CRYPTO_KEY", "")
            if key:
                encryption_keys = [key]

        self._keys: list[str] = encryption_keys or []
        if self._keys:
            fernets = [
                Fernet(k.encode() if isinstance(k, str) else k) for k in self._keys
            ]
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
            value: Plain text to encrypt

        Returns:
            Base64-encoded encrypted value prefixed with "enc:"

        Raises:
            RuntimeError: If encryption is not configured
        """
        if self._fernet is None:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        encrypted = self._fernet.encrypt(value.encode())
        return "enc:" + encrypted.decode()

    def decrypt(self, value: str) -> str:
        """Decrypt an encrypted string value.

        Args:
            value: Encrypted value (with or without "enc:" prefix)

        Returns:
            Decrypted plain text

        Raises:
            RuntimeError: If encryption is not configured
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

        Args:
            value: Value to hash.

        Returns:
            Base64-encoded HMAC hash prefixed with "hmac:".

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if not self._keys:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        key = base64.urlsafe_b64decode(self._keys[0])
        h = hmac.new(key, value.encode(), hashlib.sha256)
        return "hmac:" + base64.urlsafe_b64encode(h.digest()).decode()


def generate_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        Base64-encoded Fernet key suitable for CRYPTO_KEY
    """
    return Fernet.generate_key().decode()


crypto = CryptoService()
