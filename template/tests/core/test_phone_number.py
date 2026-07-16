"""Tests for `PhoneNumberString` Pydantic type."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError

from app.core.phone_number import PhoneNumberString


class PhoneModel(BaseModel):
    """Test model using PhoneNumberString."""

    phone: PhoneNumberString = Field(default=None, max_length=16)


class TestPhoneNumberString:
    """Tests for the PhoneNumberString validator and formatter."""

    def test_accepts_e164_with_plus(self) -> None:
        """E.164 format with + prefix should be accepted."""
        model = PhoneModel(phone="+306912345678")
        assert model.phone == "+306912345678"

    def test_normalizes_international_format(self) -> None:
        """International format without leading + should be normalized to E.164."""
        model = PhoneModel(phone="00306912345678")
        assert model.phone == "+306912345678"

    def test_normalizes_dashed_format(self) -> None:
        """Input with dashes should be normalized to E.164."""
        model = PhoneModel(phone="+30-691-234-5678")
        assert model.phone == "+306912345678"

    def test_normalizes_spaced_format(self) -> None:
        """Input with spaces should be normalized to E.164."""
        model = PhoneModel(phone="+30 691 234 5678")
        assert model.phone == "+306912345678"

    def test_accepts_us_number(self) -> None:
        """US number with country code should be accepted."""
        model = PhoneModel(phone="+12125551234")
        assert model.phone == "+12125551234"

    def test_accepts_uk_number(self) -> None:
        """UK number with country code should be accepted."""
        model = PhoneModel(phone="+442079460118")
        assert model.phone == "+442079460118"

    def test_accepts_none(self) -> None:
        """None should be accepted and returned as None."""
        model = PhoneModel(phone=None)
        assert model.phone is None

    def test_rejects_empty_string(self) -> None:
        """Empty string should be rejected as invalid."""
        with pytest.raises(ValidationError, match="phone"):
            PhoneModel(phone="")

    def test_rejects_not_a_number(self) -> None:
        """String that does not look like a phone number should be rejected."""
        with pytest.raises(ValidationError, match="phone"):
            PhoneModel(phone="+12")

    def test_strips_extension_in_e164_output(self) -> None:
        """Number with extension should be stripped to E.164 (extensions not valid in E.164)."""
        model = PhoneModel(phone="+306912345678x123")
        assert model.phone == "+306912345678"

    def test_normalizes_mobile_vs_landline(self) -> None:
        """Both mobile and landline numbers should be accepted."""
        mobile = PhoneModel(phone="+33612345678")
        landline = PhoneModel(phone="+33123456789")
        assert mobile.phone == "+33612345678"
        assert landline.phone == "+33123456789"

    def test_rejects_invalid_country_code(self) -> None:
        """Invalid country code should be rejected."""
        with pytest.raises(ValidationError, match="phone"):
            PhoneModel(phone="+999123456789123")
