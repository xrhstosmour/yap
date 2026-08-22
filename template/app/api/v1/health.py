"""Health check and system status routes.

This module provides health check endpoints for
monitoring and system status.
"""

from __future__ import annotations

from typing import Any
from typing import cast

from fastapi import APIRouter
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
        from app.celery_app import celery_app

        inspect = celery_app.control.inspect()
        active = inspect.active() or {}
        scheduled = inspect.scheduled() or {}
        active_queues = inspect.active_queues() or {}

        total_active = sum(len(tasks) for tasks in active.values())
        total_scheduled = sum(len(tasks) for tasks in scheduled.values())
        all_queues: list[str] = sorted(
            {q["name"] for queues in active_queues.values() for q in queues}
        )

        return WorkerStats(
            active=total_active,
            scheduled=total_scheduled,
            queues=all_queues,
        )
    except Exception:
        return WorkerStats(active=0, scheduled=0, queues=[])
