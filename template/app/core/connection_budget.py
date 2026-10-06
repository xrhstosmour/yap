"""How many Postgres connections this deployment can ask for, and may have.

The pool is sized per process. Nothing multiplied that by the number of
processes and compared it against what the server will actually grant, so a
deployment could ask for more connections than Postgres has and only find out
under load, as connection errors that read like the database being down.

The arithmetic is kept pure and separate from the query that reads the
server's limit, so the interesting part is testable without a database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("core.connection_budget")

# What `uvicorn` and `gunicorn` both read, and what the app compose file sets.
# The application itself never saw it, so the pool was sized as though there
# were always exactly one process.
WORKERS_VARIABLE = "WEB_CONCURRENCY"


@dataclass(frozen=True)
class ConnectionCeiling:
    """What the server will grant.

    Attributes:
        max_connections: The server's configured limit.
        reserved: Slots held back for superusers, which an application
            cannot have.
    """

    max_connections: int
    reserved: int

    @property
    def usable(self) -> int:
        """Connections an ordinary role can actually open.

        Returns:
            The limit minus the reserved slots.
        """
        return max(0, self.max_connections - self.reserved)


def worker_count(environment: dict[str, str] | None = None) -> int:
    """How many application processes will run.

    Args:
        environment: Environment to read, defaulting to this process's.

    Returns:
        The worker count, and 1 when the variable is absent or unreadable.
    """
    source = os.environ if environment is None else environment
    raw = source.get(WORKERS_VARIABLE, "")
    try:
        workers = int(raw)
    except ValueError:
        return 1
    return workers if workers > 0 else 1


def engines_per_worker(has_additional_database: bool) -> int:
    """How many pooled engines each process builds.

    The Celery engine is deliberately not counted. It uses `NullPool` and
    runs in its own processes, so it is not part of what a web worker holds.

    Args:
        has_additional_database: Whether a second database is configured.

    Returns:
        The number of pooled engines per process.
    """
    return 2 if has_additional_database else 1


def worst_case_demand(
    pool_size: int,
    max_overflow: int,
    workers: int,
    has_additional_database: bool,
) -> int:
    """The most connections this deployment can hold at once.

    Overflow counts, because it is what a burst actually opens. Sizing
    against `pool_size` alone is what makes the ceiling look far away right
    up to the moment traffic arrives.

    Args:
        pool_size: Connections each engine keeps.
        max_overflow: Extra connections each engine may open under load.
        workers: How many application processes run.
        has_additional_database: Whether a second database is configured.

    Returns:
        The worst-case connection count.
    """
    per_engine = pool_size + max_overflow
    return workers * engines_per_worker(has_additional_database) * per_engine


def over_budget(demand: int, ceiling: ConnectionCeiling) -> str | None:
    """Say why the demand does not fit, or nothing when it does.

    Args:
        demand: The worst-case connection count.
        ceiling: What the server will grant.

    Returns:
        A message naming the numbers and the knobs, or None when it fits.
    """
    if demand <= ceiling.usable:
        return None
    return (
        f"This deployment can open {demand} Postgres connections at once but "
        f"the server grants {ceiling.usable} "
        f"({ceiling.max_connections} max_connections minus "
        f"{ceiling.reserved} reserved for superusers). Lower "
        f"DATABASE_POOL_SIZE or DATABASE_MAX_OVERFLOW, lower "
        f"{WORKERS_VARIABLE}, or raise the server's max_connections. Left "
        f"alone this fails under load, as connection errors that read like "
        f"the database being down."
    )


async def read_ceiling(engine: AsyncEngine) -> ConnectionCeiling:
    """Ask the server what it will grant.

    Args:
        engine: The engine to ask through.

    Returns:
        The server's limit and its reserved slots.
    """
    async with engine.connect() as connection:
        limit = await connection.execute(text("show max_connections"))
        reserved = await connection.execute(text("show superuser_reserved_connections"))
        return ConnectionCeiling(
            max_connections=int(limit.scalar_one()),
            reserved=int(reserved.scalar_one()),
        )


def current_demand() -> int:
    """This deployment's worst-case demand, from its own settings.

    Returns:
        The worst-case connection count.
    """
    return worst_case_demand(
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        workers=worker_count(),
        has_additional_database=bool(settings.POSTGRESQL_ADDITIONAL_DATABASE),
    )


_ceiling: ConnectionCeiling | None = None


async def cached_ceiling(engine: AsyncEngine) -> ConnectionCeiling | None:
    """The server's limit, read once and held.

    `max_connections` cannot change without restarting the server, so reading
    it per request would add a round trip to an endpoint that is scraped. A
    failure is not cached, so a database that was down at the first call is
    asked again on the next.

    Args:
        engine: The engine to ask through.

    Returns:
        The ceiling, or None when the server could not be asked.
    """
    global _ceiling
    if _ceiling is not None:
        return _ceiling
    try:
        _ceiling = await read_ceiling(engine)
    except Exception as error:
        logger.warning("connection_ceiling_unreadable", error=str(error))
        return None
    return _ceiling
