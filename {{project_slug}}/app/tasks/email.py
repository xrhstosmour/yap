"""Email background tasks via Celery.

Queues emails to be sent asynchronously via SMTP.
Supports single emails, batch sends, and templated emails.
"""

import html as _html

from app.celery_app import celery_app
from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("tasks.email")


@celery_app.task(
    bind=True,
    name="app.tasks.email.send_email",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def send_email_task(
    self,
    to_email: str | list[str],
    subject: str,
    body: str,
    html: str | None = None,
) -> dict:
    """Send a single email via SMTP.

    Args:
        to_email: Recipient or list of recipients
        subject: Email subject
        body: Plain text body
        html: Optional HTML body

    Returns:
        Result dict with status
    """
    import asyncio

    from app.core.email import send_email

    try:
        asyncio.run(
            send_email(
                to_email=to_email,
                subject=subject,
                body=body,
                html=html,
                settings=settings,
            )
        )

        return {
            "status": "sent",
            "recipients": to_email if isinstance(to_email, list) else [to_email],
        }

    except Exception as e:
        logger.error("email_task_failed", error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="app.tasks.email.send_batch_emails",
    max_retries=2,
    default_retry_delay=120,
    autoretry_for=(Exception,),
)
def send_batch_emails_task(
    self,
    recipients: list[dict],
    subject: str,
    body: str,
    html: str | None = None,
) -> dict:
    """Send emails to a batch of recipients.

    Each recipient receives a personalized email with their
    context variables substituted into subject/body/html.

    Args:
        recipients: List of {"email": "...", "name": "...", ...} dicts
        subject: Email subject, may contain {name} placeholders
        body: Plain text body, may contain placeholders
        html: Optional HTML body, may contain placeholders

    Returns:
        Result dict with sent/failed counts
    """
    import asyncio

    from app.core.email import send_batch_emails

    try:
        result = asyncio.run(
            send_batch_emails(
                recipients=recipients,
                subject=subject,
                body=body,
                html=html,
                settings=settings,
            )
        )

        return result

    except Exception as e:
        logger.error("batch_email_task_failed", error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="app.tasks.email.send_template_email",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
)
def send_template_email_task(
    self,
    to_email: str,
    subject: str,
    template_name: str,
    context: dict | None = None,
) -> dict:
    """Send an email rendered from a Jinja2 HTML template.

    Templates are loaded from templates/email/ directory.

    Args:
        to_email: Recipient email
        subject: Email subject
        template_name: Template file name (e.g. 'welcome.html')
        context: Template variables dict

    Returns:
        Result dict with status
    """
    import asyncio

    from app.core.email import send_email_from_template

    try:
        asyncio.run(
            send_email_from_template(
                to_email=to_email,
                subject=subject,
                template_name=template_name,
                context=context or {},
                settings=settings,
            )
        )

        return {"status": "sent", "to_email": to_email, "template": template_name}

    except Exception as e:
        logger.error("template_email_failed", error=str(e))
        raise self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="app.tasks.email.send_welcome_email",
    max_retries=2,
    default_retry_delay=300,
)
def send_welcome_email_task(self, email: str, name: str | None = None) -> dict:
    """Send a welcome email to a new user.

    Args:
        email: User's email address
        name: User's name (optional)

    Returns:
        Result dict with status
    """
    display_name = name or "there"
    project = settings.PROJECT_NAME

    subject = f"Welcome to {project}!"
    body = f"Hi {display_name},\n\nWelcome to {project}! We're glad to have you on board.\n\nBest regards,\nThe {project} Team"
    html = f"""<h1>Welcome, {_html.escape(display_name)}!</h1>
<p>Thank you for joining <strong>{_html.escape(project)}</strong>. We're glad to have you on board.</p>
<p>Best regards,<br>The {_html.escape(project)} Team</p>"""

    send_email_task.delay(
        to_email=email,
        subject=subject,
        body=body,
        html=html,
    )

    return {"status": "queued", "to_email": email}
