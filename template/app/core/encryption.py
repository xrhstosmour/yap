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

Model field pattern::

    from app.core.encryption import crypto

    class User(BaseModel, table=True):
        email_encrypted: str = Field(...)

        @property
        def email(self) -> str:
            return crypto.decrypt(self.email_encrypted)

        @email.setter
        def email(self, value: str) -> None:
            self.email_encrypted = crypto.encrypt(value)

Searchable encrypted field (deterministic)::

    search_token = crypto.hash_for_search("user@yap.com")
    # Store search_token alongside encrypted value for lookups
"""

from __future__ import annotations

import base64
import hashlib
import hmac


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

        Args:
            value: Value to hash.

        Returns:
            Base64-encoded HMAC hash prefixed with "hmac:".

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if not self._keys:
            raise RuntimeError("Encryption not configured. Set CRYPTO_KEY in .env")
        # Derive a separate HMAC key via domain separation — never reuse
        # the Fernet encryption key directly as an HMAC key.
        key_bytes = base64.urlsafe_b64decode(self._keys[0])
        hmac_key = hashlib.sha256(key_bytes + b":search-hmac").digest()
        h = hmac.new(hmac_key, value.encode(), hashlib.sha256)
        return "hmac:" + base64.urlsafe_b64encode(h.digest()).decode()


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
