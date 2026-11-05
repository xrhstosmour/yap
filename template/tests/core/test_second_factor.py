"""Tests for the rule that decides when a superuser needs a second factor."""

from __future__ import annotations

import pytest

from app.core.second_factor import lacks_required_second_factor
from app.core.settings import settings
from app.models.user import User


@pytest.mark.parametrize(
    ("environment", "has_second_factor", "lacks"),
    [
        ("local", False, False),
        ("production", False, True),
        ("production", True, False),
        ("staging", False, True),
    ],
)
def test_only_local_may_do_without(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    has_second_factor: bool,
    lacks: bool,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    user = User(
        email="superuser@example.com",
        hashed_password="hash",
        is_2fa_enabled=has_second_factor,
    )

    assert lacks_required_second_factor(user) is lacks
