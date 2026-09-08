"""Tests for the Sentry `before_send` PII/secret scrubber."""

from __future__ import annotations

from app.main import SENTRY_SENSITIVE_KEYS
from app.main import _scrub_sensitive_data


class TestSentryScrub:
    """`_scrub_sensitive_data()` redacts known-sensitive keys recursively."""

    def test_password_value_is_redacted(self) -> None:
        """A `password` key's value is replaced, not the key itself."""
        event = {"extra": {"password": "hunter2"}}

        scrubbed = _scrub_sensitive_data(event)

        assert scrubbed["extra"]["password"] == "[Filtered]"

    def test_email_value_is_redacted(self) -> None:
        """`email` is scrubbed like other PII keys, e.g. `password`.

        Regression test: `SENTRY_SENSITIVE_KEYS` originally omitted
        `email`/`phone`/`totp`/`recovery_code`, so those PII values leaked
        into Sentry events unredacted.
        """
        event = {"extra": {"email": "alice@example.com", "name": "Alice"}}

        scrubbed = _scrub_sensitive_data(event)

        assert scrubbed["extra"]["email"] == "[Filtered]"
        assert scrubbed["extra"]["name"] == "Alice"

    def test_sensitive_keys_include_pii_fields(self) -> None:
        """`SENTRY_SENSITIVE_KEYS` covers PII, not just secrets."""
        for expected in ("email", "phone", "totp", "recovery_code"):
            assert expected in SENTRY_SENSITIVE_KEYS
