"""Outbox event dispatcher tasks."""

from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("tasks.outbox")

# Events claimed per transaction.
BATCH_SIZE = 100

# Under the 10 second beat interval, so a run hands over to the next tick
# rather than overlapping it. Overlap would still be safe, the rows are
# claimed with `SKIP LOCKED`, just wasteful.
DRAIN_SECONDS = 8.0


@celery_app.task(bind=True, name="app.tasks.outbox.process_outbox")
def process_outbox(self) -> dict:
    """Process pending outbox events and publish them to the broker.

    Polls the outbox_events table for pending events, publishes each to
    RabbitMQ/Celery, and marks them as published.

    Each event reaches the broker before the row recording that it did is
    committed, so delivery is at-least-once and consumers must be idempotent.
    Each batch commits once at its end, which means a crash part way through
    republishes every event in that batch. See `app.models.outbox` for why
    the ordering and the commit granularity are what they are.

    Batches are drained until one comes back short or `DRAIN_SECONDS` pass.
    One batch per run capped dispatch at `BATCH_SIZE` events per beat tick,
    ten a second, however far behind the outbox was.

    Returns:
        Result dictionary with processed/failed counts
    """
    logger.info("outbox_processing_started", task_id=self.request.id)

    try:
        import asyncio
        import json
        import time

        from app.database import celery_session_factory
        from app.models.outbox import Outbox
        from app.models.outbox import OutboxEvent

        async def _run() -> tuple[int, int]:
            processed = 0
            failed = 0

            deadline = time.monotonic() + DRAIN_SECONDS
            async with celery_session_factory() as session:
                outbox = Outbox(session)
                while True:
                    events = await outbox.get_pending(limit=BATCH_SIZE)
                    batch_processed, batch_failed = await _dispatch(outbox, events)
                    await session.commit()
                    processed += batch_processed
                    failed += batch_failed
                    # A failure usually means the broker is down. Draining on
                    # would refetch the same events at once and spend their
                    # retries in seconds rather than across beat ticks.
                    if (
                        batch_failed
                        or len(events) < BATCH_SIZE
                        or time.monotonic() >= deadline
                    ):
                        break

            return processed, failed

        async def _dispatch(
            outbox: Outbox, events: list[OutboxEvent]
        ) -> tuple[int, int]:
            processed = 0
            failed = 0
            for event in events:
                try:
                    envelope = {
                        # The outbox event's own id, carried so a
                        # consumer can recognise a redelivery. Delivery
                        # is at-least-once, so a consumer that writes
                        # anything durable needs a key to be idempotent
                        # against, and the payload rarely contains one.
                        "event_id": str(event.id),
                        "event_type": event.event_type,
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
