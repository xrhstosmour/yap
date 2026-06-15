"""Tests for the email sending utility.

Covers send_email, send_email_from_template, and send_batch_emails
with mocked SMTP server to avoid real network calls.
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from app.core.email import send_batch_emails
from app.core.email import send_email
from app.core.email import send_email_from_template


def _make_mock_settings(**overrides) -> MagicMock:
    """Create a mock Settings object with SMTP defaults."""
    defaults = {
        "SMTP_HOST": "smtp.test.local",
        "SMTP_PORT": 587,
        "SMTP_USER": "",
        "SMTP_PASSWORD": "",
        "SMTP_FROM_EMAIL": "noreply@test.local",
        "SMTP_USE_TLS": True,
        "PROJECT_NAME": "TestApp",
    }
    defaults.update(overrides)
    settings = MagicMock()
    for key, val in defaults.items():
        setattr(settings, key, val)
    return settings


class TestSendEmail:
    """Tests for send_email() with mocked SMTP server."""

    @pytest.mark.asyncio
    async def test_send_email_with_list_of_recipients(self) -> None:
        """Should send to all recipients in a list."""
        settings = _make_mock_settings()
        recipients = ["a@test.com", "b@test.com"]

        with patch("app.core.email.smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            await send_email(
                to_email=recipients,
                subject="Hello",
                body="Body text",
                settings=settings,
            )

        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        assert msg["To"] == "a@test.com, b@test.com"

    @pytest.mark.asyncio
    async def test_send_email_with_html_body(self) -> None:
        """Should include both plain text and HTML parts when html is given."""
        settings = _make_mock_settings()

        with patch("app.core.email.smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            await send_email(
                to_email="user@test.com",
                subject="Hi",
                body="Plain",
                html="<p>HTML</p>",
                settings=settings,
            )

        mock_server.send_message.assert_called_once()
        msg = mock_server.send_message.call_args[0][0]
        assert isinstance(msg, MIMEMultipart)
        # Should have exactly 2 parts: text/plain and text/html
        payload = msg.get_payload()
        assert len(payload) == 2

    @pytest.mark.asyncio
    async def test_send_email_without_tls(self) -> None:
        """When SMTP_USE_TLS is False, starttls should not be called."""
        settings = _make_mock_settings(SMTP_USE_TLS=False)

        with patch("app.core.email.smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            await send_email(
                to_email="user@test.com",
                subject="Hi",
                body="Body",
                settings=settings,
            )

        mock_smtp_class.assert_called_once_with(settings.SMTP_HOST, settings.SMTP_PORT)
        mock_server.starttls.assert_not_called()
        mock_server.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_email_with_smtp_auth(self) -> None:
        """When SMTP_USER and SMTP_PASSWORD are set, login should be called."""
        settings = _make_mock_settings(
            SMTP_USER="auser",
            SMTP_PASSWORD="apass",
        )

        with patch("app.core.email.smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            await send_email(
                to_email="user@test.com",
                subject="Hi",
                body="Body",
                settings=settings,
            )

        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("auser", "apass")
        mock_server.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_email_without_smtp_auth(self) -> None:
        """When SMTP_USER and SMTP_PASSWORD are empty, login should not be called."""
        settings = _make_mock_settings(
            SMTP_USER="",
            SMTP_PASSWORD="",
        )

        with patch("app.core.email.smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            await send_email(
                to_email="user@test.com",
                subject="Hi",
                body="Body",
                settings=settings,
            )

        mock_server.starttls.assert_called_once()
        mock_server.login.assert_not_called()
        mock_server.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_email_with_custom_smtp_settings(self) -> None:
        """Custom settings should be used for host/port/from."""
        settings = _make_mock_settings(
            SMTP_HOST="custom.host",
            SMTP_PORT=25,
            SMTP_FROM_EMAIL="custom@test.local",
        )

        with patch("app.core.email.smtplib.SMTP") as mock_smtp_class:
            mock_server = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_server

            await send_email(
                to_email="user@test.com",
                subject="Hi",
                body="Body",
                settings=settings,
            )

        mock_smtp_class.assert_called_once_with("custom.host", 25)
        msg = mock_server.send_message.call_args[0][0]
        assert msg["From"] == "custom@test.local"


class TestSendEmailFromTemplate:
    """Tests for send_email_from_template()."""

    @pytest.mark.asyncio
    async def test_with_valid_template_and_context(self) -> None:
        """Should render the template and send email via send_email."""
        settings = _make_mock_settings()

        with patch(
            "app.core.email.send_email", new_callable=AsyncMock
        ) as mock_send:
            await send_email_from_template(
                to_email="user@test.com",
                subject="Verify Email",
                template_name="verification.html",
                context={
                    "name": "Alice",
                    "project": "TestApp",
                    "verification_url": "https://example.com/verify",
                },
                settings=settings,
            )

        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["to_email"] == "user@test.com"
        assert call_kwargs["subject"] == "Verify Email"
        assert "Verify your email, Alice" in call_kwargs["body"]
        assert '<a href="https://example.com/verify"' in call_kwargs["html"]

    @pytest.mark.asyncio
    async def test_with_missing_template_raises_template_not_found(self) -> None:
        """Should raise jinja2.TemplateNotFound for non-existent template."""
        settings = _make_mock_settings()

        with pytest.raises(Exception) as exc_info:
            await send_email_from_template(
                to_email="user@test.com",
                subject="Test",
                template_name="nonexistent_template.html",
                context={},
                settings=settings,
            )

        # jinja2.TemplateNotFound is a subclass of TemplateNotFound
        assert "nonexistent_template.html" in str(exc_info.value)


# NOTE: test_send_batch_emails_with_no_recipients and
# test_send_batch_emails_skips_missing_email remain below as standalone functions.


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


class TestSendBatchEmails:
    """Additional tests for send_batch_emails()."""

    @pytest.mark.asyncio
    async def test_with_multiple_valid_recipients(self) -> None:
        """Should send to all valid recipients and return sent count."""
        settings = _make_mock_settings()

        with patch(
            "app.core.email.send_email", new_callable=AsyncMock
        ) as mock_send:
            result = await send_batch_emails(
                recipients=[
                    {"email": "a@test.com", "name": "Alice"},
                    {"email": "b@test.com", "name": "Bob"},
                ],
                subject="Hello {name}",
                body="Hi {name}",
                settings=settings,
            )

        assert result["sent"] == 2
        assert result["failed"] == 0
        assert mock_send.await_count == 2
