"""Tests for the Celery email tasks in app.tasks.email."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

# See tests/tasks/test_cleanup.py for why this stub eviction is needed.
if isinstance(sys.modules.get("app.tasks.email"), MagicMock):
    del sys.modules["app.tasks.email"]

from app.tasks.email import send_email_task  # noqa: E402


class TestSendEmailTask:
    """Tests for send_email_task()."""

    def test_sends_and_returns_status(self) -> None:
        """A successful send should report status "sent" with the recipients."""
        with patch("app.core.email.send_email", new=AsyncMock(return_value=None)):
            result = send_email_task.apply(
                kwargs={
                    "to_email": "user@example.com",
                    "subject": "Hi",
                    "body": "Hello",
                }
            )

        assert result.successful()
        assert result.result == {
            "status": "sent",
            "recipients": ["user@example.com"],
        }

    def test_retries_declaratively_on_failure(self) -> None:
        """A send failure should be retried by `autoretry_for`, not a manual retry.

        Regression test: this task used to combine `autoretry_for` on the
        decorator with a manual `try/except: self.retry(exc=e)` in the
        body, retrying the same failure through two competing mechanisms.
        Only `autoretry_for` remains, so a failing send still ends up
        marked failed (retries exhausted), driven by the decorator alone.
        """
        with patch(
            "app.core.email.send_email",
            new=AsyncMock(side_effect=OSError("smtp unavailable")),
        ):
            result = send_email_task.apply(
                kwargs={
                    "to_email": "user@example.com",
                    "subject": "Hi",
                    "body": "Hello",
                }
            )

        assert result.failed()
