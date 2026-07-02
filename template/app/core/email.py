"""Email sending utility.

Provides async email sending using stdlib smtplib wrapped with
asyncio.to_thread for non-blocking operation. Supports plain text
and HTML emails with optional Jinja2 template rendering.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    import jinja2

    from app.core.settings import Settings

_jinja2_env: jinja2.Environment | None = None


def _get_jinja2_env() -> jinja2.Environment:
    """Return a cached Jinja2 environment, creating it on first call."""
    global _jinja2_env
    env = _jinja2_env
    if env is None:
        import jinja2 as _jinja2

        templates_dir = Path(__file__).parent.parent.parent / "templates" / "email"
        env = _jinja2.Environment(
            loader=_jinja2.FileSystemLoader(str(templates_dir)),
            autoescape=True,
        )
        _jinja2_env = env
    return env

logger = get_logger("email")


async def send_email(
    to_email: str | list[str],
    subject: str,
    body: str,
    html: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Send an email via SMTP.

    Uses asyncio.to_thread to run blocking SMTP calls without
    blocking the event loop.

    Args:
        to_email: Recipient or list of recipients
        subject: Email subject line
        body: Plain text body
        html: Optional HTML body (multipart alternative)
        settings: Settings instance (uses global if not provided)

    Raises:
        smtplib.SMTPException: On SMTP errors
    """
    if settings is None:
        from app.core.settings import settings as _settings

        settings = _settings

    recipients = [to_email] if isinstance(to_email, str) else to_email

    def _send_sync() -> None:
        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))

        if settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)

    await asyncio.to_thread(_send_sync)
    logger.info("email_sent", to_count=len(recipients), subject=subject)


async def send_email_from_template(
    to_email: str | list[str],
    subject: str,
    template_name: str,
    context: dict | None = None,
    settings: Settings | None = None,
) -> None:
    """Send an email using a Jinja2 template for the HTML body.

    Templates are loaded from the `templates/email/` directory.
    The plain text body is auto-extracted from the HTML by stripping tags.

    Args:
        to_email: Recipient or list of recipients
        subject: Email subject line
        template_name: Template filename (e.g. 'welcome.html')
        context: Template variables
        settings: Settings instance

    Raises:
        jinja2.TemplateNotFound: If template does not exist
    """
    ctx = context or {}
    env = _get_jinja2_env()
    template = env.get_template(template_name)
    html_body = template.render(**ctx)

    import re

    plain_body = re.sub(r"<[^>]+>", "", html_body)
    plain_body = re.sub(r"\s+", " ", plain_body).strip()

    await send_email(
        to_email=to_email,
        subject=subject,
        body=plain_body,
        html=html_body,
        settings=settings,
    )


async def send_batch_emails(
    recipients: list[dict],
    subject: str,
    body: str,
    html: str | None = None,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Send an email to multiple recipients individually.

    Each recipient receives a separate email. Failures per recipient
    do not block the batch.

    Args:
        recipients: List of dicts with at least 'email' key, plus template vars
        subject: Email subject (may contain {var} placeholders)
        body: Plain text body (may contain {var} placeholders)
        html: Optional HTML body (may contain {var} placeholders)
        settings: Settings instance

    Returns:
        Dict with 'sent' and 'failed' counts
    """
    max_concurrent = 10
    semaphore = asyncio.Semaphore(max_concurrent)

    async def send_one(recipient: dict) -> bool:
        email_addr = recipient.get("email")
        if not email_addr:
            return False

        try:
            personalized_subject = subject.format(**recipient)
            personalized_body = body.format(**recipient)
            personalized_html = html.format(**recipient) if html else None

            async with semaphore:
                await send_email(
                    to_email=email_addr,
                    subject=personalized_subject,
                    body=personalized_body,
                    html=personalized_html,
                    settings=settings,
                )

            return True
        except Exception as e:
            logger.error(
                "batch_email_failed",
                recipient=email_addr,
                error=str(e),
            )
            return False

    results = await asyncio.gather(
        *(send_one(recipient) for recipient in recipients),
        return_exceptions=True,
    )

    sent = 0
    failed = 0
    for result in results:
        if isinstance(result, Exception):
            failed += 1
            continue
        if result:
            sent += 1
        else:
            failed += 1

    logger.info("batch_email_complete", sent=sent, failed=failed)
    return {"sent": sent, "failed": failed}
