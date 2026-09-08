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
    autoretry_for=(Exception,),
    # `retry_backoff` computes the retry delay itself (`base * 2**retries`),
    # `default_retry_delay` is never consulted once it's set, passed as the
    # base delay in seconds rather than `True` (a factor of 1, backing off
    # from ~1 second instead of ~30).
    retry_backoff=30,
    retry_jitter=True,
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
    from app.core.storage import build_object_key
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

                import PIL.Image

                content = await download_object(record.object_key, bucket=record.bucket)
                try:
                    width, height, thumbnail_bytes = await asyncio.to_thread(
                        _build_thumbnail, content, record.mimetype
                    )
                except (
                    PIL.Image.DecompressionBombError,
                    PIL.Image.DecompressionBombWarning,
                ):
                    # `_build_thumbnail` escalates PIL's decompression-bomb
                    # warning (raised for 1x-2x MAX_IMAGE_PIXELS) to an
                    # exception too, so both it and the error PIL raises
                    # above 2x land here. Deterministic for this file's
                    # bytes, retrying would just fail the same way three
                    # more times, caught here explicitly rather than
                    # falling through to the generic `except Exception`
                    # below, which would autoretry it.
                    logger.warning(
                        "thumbnail_decompression_bomb_rejected", file_id=file_id
                    )
                    return {"status": "rejected", "reason": "decompression_bomb"}

                # Keyed off the record's own uploader and tenant, not the
                # context: this runs inside `system_context()`, so the
                # ambient tenant is the system one and would collide across
                # tenants again.
                thumbnail_object_key = build_object_key(
                    "thumbnails",
                    record.content_hash,
                    record.uploaded_by,
                    record.tenant_id,
                )
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
        # Retrying is handled declaratively by `autoretry_for` above; a
        # manual `self.retry(exc=e)` here duplicated that mechanism and
        # raced it. Logging is kept, then the exception is re-raised so
        # Celery's own retry machinery takes over.
        logger.warning("thumbnail_generation_failed", file_id=file_id, error=str(e))
        raise
