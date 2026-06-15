"""Tests for the email sending utility."""

import pytest

from app.core.email import send_batch_emails


@pytest.mark.asyncio
async def test_send_batch_emails_with_no_recipients() -> None:
    """Batch email with empty list should return zero counts."""
    result = await send_batch_emails(
        recipients=[],
        subject="Test",
        body="Test body",
    )
    assert result == {"sent": 0, "failed": 0}


@pytest.mark.asyncio
async def test_send_batch_emails_skips_missing_email() -> None:
    """Recipients without 'email' key should be counted as failed."""
    result = await send_batch_emails(
        recipients=[{"name": "No Email"}],
        subject="Test",
        body="Test body",
    )
    assert result["failed"] == 1
    assert result["sent"] == 0
