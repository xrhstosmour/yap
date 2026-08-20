"""Tests for the User model's email_hash/phone_hash sync listeners."""

from __future__ import annotations

from app.models.user import User


class TestEmailHashSync:
    """Tests for the `_sync_email_hash` event listener."""

    def test_hash_is_set_on_construction(self) -> None:
        """Constructing a User computes email_hash from the given email."""
        user = User(email="person@example.com", hashed_password="hash")

        assert user.email_hash
        assert user.email_hash != "person@example.com"

    def test_hash_updates_when_email_changes(self) -> None:
        """Reassigning email recomputes email_hash to match the new value."""
        user = User(email="old@example.com", hashed_password="hash")
        original_hash = user.email_hash

        user.email = "new@example.com"

        assert user.email_hash != original_hash

    def test_hash_is_recomputed_when_email_is_cleared(self) -> None:
        """Clearing email to an empty string must not leave a stale hash.

        A caller erasing a user's email (e.g. a GDPR erasure flow) expects
        email_hash to stop pointing at the erased value. email_hash is
        `nullable=False`, so it cannot become NULL like phone_hash does;
        it is instead recomputed from the empty string.
        """
        user = User(email="person@example.com", hashed_password="hash")
        original_hash = user.email_hash

        user.email = ""

        assert user.email_hash != original_hash

    def test_erased_users_do_not_collide_on_email_hash(self) -> None:
        """Two separately erased users must not end up with the same email_hash.

        `hash_for_search("")` is deterministic, so if the empty string were
        hashed directly, every erased user would get the exact same
        `email_hash`, and the column's `unique=True` constraint would let
        only the first erasure in the database's lifetime succeed. Each
        erasure must produce a distinct hash instead.
        """
        first_user = User(email="first@example.com", hashed_password="hash")
        second_user = User(email="second@example.com", hashed_password="hash")

        first_user.email = ""
        second_user.email = ""

        assert first_user.email_hash != second_user.email_hash


class TestPhoneHashSync:
    """Tests for the `_sync_phone_hash` event listener."""

    def test_hash_is_none_when_phone_is_unset(self) -> None:
        """A user without a phone number has no phone_hash."""
        user = User(email="person@example.com", hashed_password="hash")

        assert user.phone_hash is None

    def test_hash_is_cleared_when_phone_is_cleared(self) -> None:
        """Clearing phone to None also clears phone_hash."""
        user = User(email="person@example.com", hashed_password="hash")
        user.phone = "+15555550123"
        assert user.phone_hash is not None

        user.phone = None

        assert user.phone_hash is None
