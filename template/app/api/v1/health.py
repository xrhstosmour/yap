"""Health check and system status routes.

This module provides health check endpoints for
monitoring and system status.
"""

from __future__ import annotations

import asyncio
from typing import Any
from typing import cast

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from sqlmodel import text

from app.core.cache import RedisDependency
from app.core.logging import get_logger
from app.core.settings import settings
from app.database import async_engine
from app.dependencies import SessionDependency
from app.dependencies import SuperuserUser
from app.schemas.base import HealthResponse
from app.schemas.base import MessageResponse

router = APIRouter(tags=["Health"])
logger = get_logger("api.health")


class PoolStats(BaseModel):
    """Database pool statistics."""

    pool_size: int
    checked_in: int
    checked_out: int
    overflow: int
    total: int


class CacheStats(BaseModel):
    """Redis cache statistics."""

    connected: bool
    max_connections: int


class WorkerStats(BaseModel):
    """Celery worker statistics."""

    active: int
    scheduled: int
    queues: list[str]


class MetricsResponse(BaseModel):
    """System metrics response."""

    pool: PoolStats
    cache: CacheStats
    workers: WorkerStats | None = None


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Check the health status of the API and its dependencies.",
)
async def health_check(
    session: SessionDependency,
    redis: RedisDependency,
) -> HealthResponse:
    """Check health of API and dependencies.

    Returns the status of:
    - Overall API health
    - Database connection
    - Redis connection
    """
    db_status = "healthy"
    cache_status = "healthy"

    # Check database.
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("database_health_check_failed", error=str(e))
        db_status = "unhealthy"

    # Check Redis.
    try:
        await redis.ping()
    except Exception as e:
        logger.error("redis_health_check_failed", error=str(e))
        cache_status = "unhealthy"

    overall_status = (
        "healthy"
        if db_status == "healthy" and cache_status == "healthy"
        else "degraded"
    )

    return HealthResponse(
        status=overall_status,
        version="1.0.0",
        database=db_status,
        cache=cache_status,
    )


@router.get(
    "/ready",
    status_code=status.HTTP_200_OK,
    summary="Readiness check",
    description="Check if the API is ready to accept requests.",
)
async def readiness_check(session: SessionDependency) -> MessageResponse:
    """Check if API is ready.

    Used by load balancers and orchestrators to determine
    if the API should receive traffic.
    """
    try:
        await session.execute(text("SELECT 1"))
        return MessageResponse(message="Ready")
    except Exception as e:
        logger.error("readiness_check_failed", error=str(e))
        from fastapi import HTTPException
        from fastapi import status

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Not ready",
        ) from e


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    summary="Liveness check",
    description="Check if the API is alive.",
)
async def liveness_check() -> MessageResponse:
    """Check if API is alive.

    Simple endpoint that always returns 200 if the
    application is running.
    """
    return MessageResponse(message="Alive")


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="System metrics",
    description="Get database pool and cache statistics for monitoring. Admin only.",
)
async def get_metrics(
    current_user: SuperuserUser,
    session: SessionDependency,
    redis_client: RedisDependency,
) -> MetricsResponse:
    """Get system performance metrics (admin only).

    Returns database connection pool stats and cache stats
    for monitoring and capacity planning. Requires an authenticated
    superuser since this exposes internal DB connection-pool state,
    matching the ``ws/metrics`` WebSocket endpoint.
    """
    pool = cast(Any, async_engine.pool)
    pool_stats = PoolStats(
        pool_size=pool.size(),
        checked_in=pool.checkedin(),
        checked_out=pool.checkedout(),
        overflow=pool.overflow(),
        total=pool.size() + pool.overflow(),
    )

    cache_connected = False
    try:
        await redis_client.ping()
        cache_connected = True
    except Exception:
        pass

    cache_stats = CacheStats(
        connected=cache_connected,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
    )

    return MetricsResponse(pool=pool_stats, cache=cache_stats)


def _inspect_workers() -> tuple[dict | None, dict | None, dict | None]:
    """Probe the Celery cluster. Blocking, must not run on the event loop.

    Each of these broadcasts over the broker and waits for replies with a
    one-second default timeout, so calling them inline froze the whole
    process for up to three seconds per request: no other request could be
    served, not even a health check.

    Returns:
        The raw ``active``, ``scheduled`` and ``active_queues`` mappings.
        Each is ``None`` when no worker answered that probe.
    """
    from app.celery_app import celery_app

    inspect = celery_app.control.inspect()
    return inspect.active(), inspect.scheduled(), inspect.active_queues()


@router.get(
    "/workers",
    response_model=WorkerStats,
    status_code=status.HTTP_200_OK,
    summary="Worker status",
    description="Get Celery worker statistics for monitoring. Admin only.",
)
async def get_worker_stats(current_user: SuperuserUser) -> WorkerStats:
    """Get Celery worker monitoring stats (admin only).

    Returns active/scheduled task counts and active queue names.
    Requires an authenticated superuser since this exposes internal
    task queue state.
    """
    try:
        active, scheduled, active_queues = await asyncio.to_thread(_inspect_workers)
    except Exception as exc:
        # Previously this returned zeros with a 200. Zeros are exactly what
        # a healthy idle cluster reports, so a broker outage was
        # indistinguishable from "nothing to do" and any dashboard watching
        # this endpoint stayed green through it.
        logger.warning("worker_stats_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot reach the task broker to inspect workers.",
        ) from exc

    if active is None and scheduled is None and active_queues is None:
        # Celery returns None from every probe when nothing answered the
        # broadcast, as opposed to `{"worker@host": []}` for a worker that
        # answered with no tasks. Same reasoning as above: no reply is not
        # the same fact as no work.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No Celery workers responded.",
        )

    total_active = sum(len(tasks) for tasks in (active or {}).values())
    total_scheduled = sum(len(tasks) for tasks in (scheduled or {}).values())
    all_queues: list[str] = sorted(
        {queue["name"] for queues in (active_queues or {}).values() for queue in queues}
    )

    return WorkerStats(
        active=total_active,
        scheduled=total_scheduled,
        queues=all_queues,
    )
