"""NUL characters are a 422 at the schema, never a 500 from the database."""

import pytest
from pydantic import ValidationError

from app.schemas.auth import AppleNativeLoginRequest
from app.schemas.auth import RegisterRequest
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe


def _register(**overrides: str) -> RegisterRequest:
    fields = {"email": "person@example.com", "password": "correct horse"}
    return RegisterRequest(**{**fields, **overrides})


@pytest.mark.parametrize("field", ["password", "full_name", "username"])
def test_register_rejects_nul_character(field: str) -> None:
    """A NUL in any free-text register field fails validation."""
    with pytest.raises(ValidationError, match="NUL"):
        _register(**{field: "ab\x00cd1234"})


def test_register_accepts_ordinary_text() -> None:
    """Ordinary values still pass, so the guard is not over-broad."""
    request = _register(full_name="Ada Lovelace", username="ada_l")
    assert request.username == "ada_l"


@pytest.mark.parametrize(
    ("schema", "field"),
    [
        (UserUpdate, "full_name"),
        (UserUpdateMe, "full_name"),
        (UserUpdateMe, "username"),
        (UserUpdateMe, "bio"),
    ],
)
def test_profile_updates_reject_nul_character(schema: type, field: str) -> None:
    """The profile and admin update paths hold the same line as register."""
    with pytest.raises(ValidationError, match="NUL"):
        schema(**{field: "ab\x00cd1234"})


def test_apple_login_rejects_nul_in_name() -> None:
    """A name Apple posts back is client-supplied text too."""
    with pytest.raises(ValidationError, match="NUL"):
        AppleNativeLoginRequest(identity_token="x", full_name="ab\x00cd")


@pytest.mark.parametrize("field", ["full_name", "username"])
def test_register_rejects_whitespace_only_text(field: str) -> None:
    """A name of spaces passed every length rule and rendered as nothing."""
    with pytest.raises(ValidationError, match="only whitespace"):
        _register(**{field: "   \t "})


@pytest.mark.parametrize("name", [" ", "\t\n", ""])
def test_a_blank_apple_name_is_no_name_rather_than_a_refusal(name: str) -> None:
    """Apple sends the name once, and a joined empty one must not fail sign-in."""
    request = AppleNativeLoginRequest(
        identity_token="x", nonce="n" * 16, full_name=name
    )

    assert request.full_name is None


def test_empty_text_stays_valid() -> None:
    """A client clearing a field is not refused by the whitespace rule."""
    assert _register(full_name="").full_name == ""
