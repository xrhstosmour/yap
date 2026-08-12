"""Outbox event dispatcher tasks."""

from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("tasks.outbox")


@celery_app.task(bind=True, name="app.tasks.outbox.process_outbox")
def process_outbox(self) -> dict:
    """Process pending outbox events and publish them to the broker.

    Polls the outbox_events table for pending events, publishes each
    to RabbitMQ/Celery, and marks them as published.

    Returns:
        Result dictionary with processed/failed counts
    """
    logger.info("outbox_processing_started", task_id=self.request.id)

    try:
        import asyncio
        import json

        from app.database import celery_session_factory
        from app.models.outbox import Outbox

        async def _run() -> tuple[int, int]:
            processed = 0
            failed = 0

            async with celery_session_factory() as session:
                outbox = Outbox(session)
                events = await outbox.get_pending(limit=100)

                for event in events:
                    try:
                        envelope = {
                            "tenant_id": (
                                str(event.tenant_id) if event.tenant_id else None
                            ),
                            "payload": event.payload,
                        }
                        payload = json.dumps(envelope)
                        celery_app.send_task(
                            f"app.events.{event.event_type}",
                            args=[payload],
                            queue="events",
                        )
                        await outbox.mark_published(event)
                        processed += 1
                    except Exception as e:
                        logger.error(
                            "outbox_event_failed",
                            event_id=str(event.id),
                            event_type=event.event_type,
                            error=str(e),
                        )
                        await outbox.mark_failed(event)
                        failed += 1

                await session.commit()

            return processed, failed

        processed, failed = asyncio.run(_run())

        logger.info("outbox_processing_completed", processed=processed, failed=failed)

        return {
            "status": "completed",
            "processed": processed,
            "failed": failed,
            "task_id": self.request.id,
        }

    except Exception as e:
        logger.error("outbox_processing_failed", task_id=self.request.id, error=str(e))
        raise
