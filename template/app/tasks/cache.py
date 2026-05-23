"""Cache warming and management tasks.

This module defines background tasks for cache management.
"""

from app.celery_app import celery_app
from app.core.cache import get_cache
from app.core.logging import get_logger

logger = get_logger("tasks.cache")


@celery_app.task(bind=True, name="app.tasks.cache.warm_cache")
def warm_cache(self) -> dict:
    """Warm cache with commonly accessed data.

    Pre-populates cache with data that's frequently accessed
    to improve response times.

    Returns:
        Result dictionary with status
    """
    logger.info("cache_warm_started", task_id=self.request.id)

    try:
        import asyncio

        asyncio.run(_warm_cache_async())

        logger.info("cache_warm_completed", task_id=self.request.id)

        return {
            "status": "completed",
            "task_id": self.request.id,
        }

    except Exception as e:
        logger.error("cache_warm_failed", task_id=self.request.id, error=str(e))
        raise


async def _warm_cache_async() -> None:
    """Async cache warming logic."""
    cache = await get_cache()

    # Add placeholder cache entries.
    # In production, load actual frequently accessed data.
    await cache.set("system:health", {"status": "ok"}, ttl=300)
    await cache.set("system:version", "1.0.0", ttl=3600)


@celery_app.task(bind=True, name="app.tasks.cache.clear_cache")
def clear_cache(self, pattern: str = "*") -> dict:
    """Clear cache entries matching pattern.

    Args:
        pattern: Redis key pattern to match

    Returns:
        Result dictionary
    """
    logger.info("cache_clear_started", task_id=self.request.id, pattern=pattern)

    try:
        import asyncio

        asyncio.run(_clear_cache_async(pattern))

        logger.info("cache_clear_completed", task_id=self.request.id)

        return {
            "status": "completed",
            "pattern": pattern,
            "task_id": self.request.id,
        }

    except Exception as e:
        logger.error("cache_clear_failed", task_id=self.request.id, error=str(e))
        raise


async def _clear_cache_async(pattern: str) -> None:
    """Async cache clearing logic."""
    cache = await get_cache()
    deleted = await cache.delete_pattern(pattern)
    logger.info("cache_entries_deleted", count=deleted)
