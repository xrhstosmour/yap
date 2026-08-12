"""Blob storage background tasks via Celery.

Generates image thumbnails asynchronously after upload so the HTTP
request isn't held open for the resize.
"""

from __future__ import annotations

from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("tasks.storage")


@celery_app.task(
    bind=True,
    name="app.tasks.storage.generate_thumbnail",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
)
def generate_thumbnail_task(self, file_id: str) -> dict:
    """Generate and store a thumbnail for an uploaded image file.

    Downloads the original object, builds a thumbnail, uploads it, and
    updates the ``File`` row's ``thumbnail_object_key``, ``image_width``,
    and ``image_height`` columns.

    Args:
        file_id: UUID (as string) of the ``File`` record to thumbnail.

    Returns:
        Result dict with status.
    """
    import asyncio

    from app.core.storage import _build_thumbnail
    from app.core.storage import download_object
    from app.core.storage import upload_object
    from app.core.tenant import system_context
    from app.database import celery_session_factory
    from app.repositories.file_repository import FileRepository

    async def _run() -> dict:
        async with celery_session_factory() as session:
            repository = FileRepository(session)
            # The caller already resolved ownership (this task only runs
            # right after that caller's own upload), and this task has no
            # tenant context of its own to filter by: it is handed a
            # `file_id` uploaded by an unknown tenant.
            with system_context():
                record = await repository.get(file_id)
                if record is None:
                    return {"status": "skipped", "reason": "file_not_found"}

                content = await download_object(record.object_key, bucket=record.bucket)
                width, height, thumbnail_bytes = await asyncio.to_thread(
                    _build_thumbnail, content, record.mimetype
                )

                thumbnail_object_key = f"thumbnails/{record.content_hash}"
                await upload_object(
                    thumbnail_object_key,
                    thumbnail_bytes,
                    record.mimetype,
                    bucket=record.bucket,
                )

                await repository.update(
                    record.id,
                    {
                        "thumbnail_object_key": thumbnail_object_key,
                        "image_width": width,
                        "image_height": height,
                    },
                )
                await session.commit()
                return {"status": "completed", "file_id": file_id}

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.warning("thumbnail_generation_failed", file_id=file_id, error=str(e))
        raise self.retry(exc=e) from e
