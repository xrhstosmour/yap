"""Tests that the data export classification keeps up with the schema.

An export that silently skips a new table is the failure this guards: the
first table added after a hand-written list is the one it forgets.
"""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel

import app.models  # noqa: F401
from app.core.personal_data import EXPORTED
from app.core.personal_data import SECRET_COLUMN
from app.core.personal_data import USER_TABLE
from app.core.personal_data import WITHHELD


def _user_references() -> set[str]:
    """Every `table.column` with a foreign key to the account."""
    return {
        f"{table.name}.{column.name}"
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        for foreign_key in column.foreign_keys
        if foreign_key.column.table.name == USER_TABLE
    }


class TestClassification:
    """Each column pointing at a person is exported or withheld, on purpose."""

    def test_every_user_reference_is_classified(self) -> None:
        """A new column pointing at the account fails here until it is decided."""
        unclassified = _user_references() - EXPORTED.keys() - WITHHELD.keys()

        assert not unclassified, f"Export or withhold: {sorted(unclassified)}"

    def test_nothing_is_both_exported_and_withheld(self) -> None:
        """One column, one decision."""
        assert not EXPORTED.keys() & WITHHELD.keys()

    def test_every_parent_is_exported(self) -> None:
        """A child row only follows a parent that is itself in the export."""
        exported_tables = {USER_TABLE} | {
            reference.split(".", 1)[0] for reference in EXPORTED
        }

        for reference, target in EXPORTED.items():
            assert target in exported_tables, f"{reference} points into {target}"

    def test_every_withheld_column_says_why(self) -> None:
        """A reason is what makes withholding a decision rather than a gap."""
        for reference, reason in WITHHELD.items():
            assert reason.strip(), f"{reference} has no reason"


class TestSecretColumns:
    """Credentials never leave in an export, whatever table they sit in."""

    @pytest.mark.parametrize(
        "name",
        [
            "hashed_password",
            "totp_secret_encrypted",
            "email_hash",
            "token",
            "token_version",
            "key_hash",
            "public_key",
            "object_key",
        ],
    )
    def test_credential_columns_are_dropped(self, name: str) -> None:
        """Matched on a whole part of the name."""
        assert SECRET_COLUMN.search(name)

    @pytest.mark.parametrize(
        "name", ["email", "provider_user_id", "keyword", "monkey_count", "hashtag"]
    )
    def test_ordinary_columns_are_kept(self, name: str) -> None:
        """A part that only contains a secret word is not one."""
        assert not SECRET_COLUMN.search(name)
