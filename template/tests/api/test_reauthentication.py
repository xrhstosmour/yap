"""Tests for how step-up failures are answered."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.reauthentication import require_reauthentication
from app.core.settings import settings
from app.services.two_factor_service import TwoFactorRateLimitError


@pytest.mark.asyncio
async def test_too_many_attempts_is_429_retrying_after_the_attempt_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Retry-After` follows `TOTP_RATE_LIMIT_WINDOW`, not a fixed number."""
    monkeypatch.setattr(settings, "TOTP_RATE_LIMIT_WINDOW", 123)
    monkeypatch.setattr(
        "app.api.reauthentication.require_fresh_proof",
        AsyncMock(side_effect=TwoFactorRateLimitError("slow down")),
    )

    with pytest.raises(HTTPException) as excinfo:
        await require_reauthentication(MagicMock(), MagicMock(), "guess", None, None)

    assert excinfo.value.status_code == 429
    assert excinfo.value.headers == {"Retry-After": "123"}
