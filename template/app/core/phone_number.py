"""Phone number validation and formatting.

Uses the `phonenumbers` library (Google's libphonenumber) to parse,
validate, and format phone numbers. Provides a `PhoneNumberString`
Pydantic type for use in schemas.

The type accepts phone numbers in any parseable format (E.164, national,
international) and normalizes them to E.164 for storage and response.
"""

from __future__ import annotations

import re
from typing import Annotated

import phonenumbers
from phonenumbers import PhoneNumberFormat
from pydantic import BeforeValidator

# Matches the international dialing prefix (00) used by most countries
# outside North America to replace it with + so phonenumbers can parse
# it without a default region.
_INTERNATIONAL_PREFIX_RE = re.compile(r"^00")


def validate_and_format(  # noqa: ANN201
    value: object,
):
    """Validate and normalize a phone number to E.164 format.

    Parses the input using the phonenumbers library, checks it is a
    possible number, and returns the E.164 representation.

    Accepts E.164 format (``+306912345678``), international dialing format
    (``00306912345678``), and formatted variants with spaces, dashes, or
    parentheses. All are normalized to E.164 for storage and response.

    Args:
        value: Phone number as a string (various formats) or
            phonenumbers.PhoneNumber object.

    Returns:
        E.164 formatted phone number string.

    Raises:
        ValueError: If the number cannot be parsed or is impossible.
    """
    if value is None:
        return None
    if isinstance(value, phonenumbers.PhoneNumber):
        return phonenumbers.format_number(value, PhoneNumberFormat.E164)
    if not isinstance(value, str):
        raise ValueError("Phone number must be a string")
    # Normalize the international dialing prefix (00) to + so phonenumbers
    # can parse it without knowing the caller's country.
    normalized = _INTERNATIONAL_PREFIX_RE.sub("+", value.strip())
    try:
        parsed = phonenumbers.parse(normalized, None)
    except phonenumbers.NumberParseException as error:
        raise ValueError(str(error)) from error
    if not phonenumbers.is_possible_number(parsed):
        raise ValueError(f"Invalid phone number: {value}")
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


PhoneNumberString = Annotated[
    str | None,
    BeforeValidator(validate_and_format),
]
"""Pydantic-compatible phone number type.

Usage in schemas::

    from app.core.phone_number import PhoneNumberString

    class UserUpdateMe(BaseSchema):
        phone: PhoneNumberString = Field(default=None, max_length=16)

Validates input with phonenumbers and normalizes to E.164 on input
and output.
"""
