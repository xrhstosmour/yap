"""Database connection and session management.

This module provides async SQLModel session management with connection pooling.
Sessions are created per-request and automatically closed after use.

Database routing:
    A `database_mode_variable` contextvar controls which database to use. When set to
    `ADDITIONAL_DATABASE_MODE` (``"additional"``, from the ``X-Database-Mode``
    header), the additional engine supplies the session. This enables
    preview/demo/staging database isolation without per-model flag columns.
    Call ``get_additional_sync_session()`` for Alembic migrations on the
    additional database.

    The additional database engines are only created when
    ``settings.POSTGRESQL_ADDITIONAL_DATABASE`` is configured (non-empty), the
    same "empty string disables the feature" convention used elsewhere in
    ``app.core.settings`` (e.g. ``SSL_CERTIFICATE_PATH``, ``GOOGLE_CLIENT_ID``).
    When disabled, ``ADDITIONAL_DATABASE_ENABLED`` is ``False`` and the
    additional engine/session-factory attributes are ``None``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import Generator
from contextvars import ContextVar

from sqlalchemy import Engine
from sqlalchemy import NullPool
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel import text

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("database")

ADDITIONAL_DATABASE_MODE = "additional"
"""Value that triggers routing to the additional database."""

# Context variable for database routing.
# Set by middleware when the X-Database-Mode header is present.
database_mode_variable: ContextVar[str] = ContextVar("database_mode", default="")

# The additional database is optional. It is only provisioned when a name is
# configured, mirroring the empty-string-disables convention used by other
# optional features in app.core.settings.
ADDITIONAL_DATABASE_ENABLED = bool(settings.POSTGRESQL_ADDITIONAL_DATABASE)


# Main engine.
async_database_url = str(settings.DATABASE_URI)

async_engine = create_async_engine(
    async_database_url,
    echo=settings.ENVIRONMENT == "local",
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT,
)

async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Celery engine: unpooled, for use inside worker tasks only. Each task wraps
# its body in `asyncio.run()`, which creates and destroys an event loop per
# task. `async_engine` above pools connections for the process's lifetime;
# a connection checked out under one task's loop and returned to the pool
# is unusable once that loop closes, since asyncpg/psycopg bind an async
# connection to the loop that created it. `NullPool` opens a fresh
# connection per checkout instead of reusing one across loops, at the cost
# of a new connection per task, an acceptable trade for background work.
celery_engine = create_async_engine(
    async_database_url,
    echo=settings.ENVIRONMENT == "local",
    poolclass=NullPool,
)

celery_session_factory = async_sessionmaker(
    celery_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Additional engine (preview/demo/staging database), created only when enabled.
additional_async_engine: AsyncEngine | None = None
additional_async_session_factory: async_sessionmaker[AsyncSession] | None = None

if ADDITIONAL_DATABASE_ENABLED:
    additional_async_database_url = str(settings.ADDITIONAL_DATABASE_URI)

    additional_async_engine = create_async_engine(
        additional_async_database_url,
        echo=settings.ENVIRONMENT == "local",
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=settings.DATABASE_POOL_RECYCLE,
        pool_timeout=settings.DATABASE_POOL_TIMEOUT,
    )

    additional_async_session_factory = async_sessionmaker(
        additional_async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def get_async_session() -> AsyncGenerator[AsyncSession]:
    """Get async database session for dependency injection.

    Routes to the additional database engine when ``database_mode_variable`` is set to
    ``ADDITIONAL_DATABASE_MODE`` (``"additional"``, e.g. from the ``X-Database-Mode``
    header middleware), otherwise routes to the main database engine.

    Yields:
        AsyncSession instance for database operations.

    Raises:
        RuntimeError: If additional database mode is requested but
            ``settings.POSTGRESQL_ADDITIONAL_DATABASE`` is not configured.
    """
    if database_mode_variable.get() == ADDITIONAL_DATABASE_MODE:
        if additional_async_session_factory is None:
            raise RuntimeError(
                "Additional database mode requested but "
                "POSTGRESQL_ADDITIONAL_DATABASE is not configured."
            )
        factory = additional_async_session_factory
    else:
        factory = async_session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Sync engine for Alembic migrations.
# Alembic doesn't fully support async engines.
sync_engine = create_engine(
    str(settings.DATABASE_URI),
    echo=settings.ENVIRONMENT == "local",
    pool_pre_ping=True,
    pool_recycle=3600,
)

sync_session_factory = sessionmaker(
    sync_engine,
    autocommit=False,
    autoflush=False,
)

# Additional sync engine for Alembic migrations on the additional database,
# created only when the additional database is enabled.
additional_sync_engine: Engine | None = None
additional_sync_session_factory: sessionmaker[Session] | None = None

if ADDITIONAL_DATABASE_ENABLED:
    additional_sync_database_url = str(settings.ADDITIONAL_DATABASE_URI)
    additional_sync_engine = create_engine(
        additional_sync_database_url,
        echo=settings.ENVIRONMENT == "local",
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    additional_sync_session_factory = sessionmaker(
        additional_sync_engine,
        autocommit=False,
        autoflush=False,
    )


def get_sync_session() -> Generator[Session]:
    """Get sync database session for Alembic migrations on the main database.

    Yields:
        Sync Session instance for migrations.

    Note:
        Only used by Alembic during migrations.
        Use async session in application code.
    """
    with sync_session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_additional_sync_session() -> Generator[Session]:
    """Get sync database session for Alembic migrations on the additional database.

    Yields:
        Sync Session instance for migrations.

    Note:
        Only used by Alembic when targeting the additional database.

    Raises:
        RuntimeError: If ``settings.POSTGRESQL_ADDITIONAL_DATABASE`` is not
            configured.
    """
    if additional_sync_session_factory is None:
        raise RuntimeError(
            "Additional database sync session requested but "
            "POSTGRESQL_ADDITIONAL_DATABASE is not configured."
        )
    with additional_sync_session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def init_db() -> None:
    """Initialize database connections.

    Tests connectivity to both the main and additional databases.
    Called during application startup to verify connectivity.
    Only the main database connection failure is fatal; additional
    database failures are logged as warnings so startup succeeds
    when the additional database has not been provisioned yet.

    Note:
        In production, use Alembic migrations instead of create_all.
    """
    logger.info("database_initializing", url=settings.POSTGRESQL_SERVER)

    engines: list[tuple[str, AsyncEngine]] = [("main", async_engine)]
    if additional_async_engine is not None:
        engines.append(("additional", additional_async_engine))

    for label, engine in engines:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            logger.info(
                "database_connected", label=label, url=settings.POSTGRESQL_SERVER
            )
        except Exception as e:
            logger.error("database_connection_failed", label=label, error=str(e))
            if label == "main":
                raise


async def close_db() -> None:
    """Close database connections gracefully.

    Called during application shutdown to properly close
    all database connections and release resources.
    """
    logger.info("database_closing")
    async_engines: list[tuple[str, AsyncEngine]] = [("main", async_engine)]
    if additional_async_engine is not None:
        async_engines.append(("additional", additional_async_engine))

    for label, async_eng in async_engines:
        await async_eng.dispose()
        logger.info("database_closed", label=label)

    sync_engines: list[tuple[str, Engine]] = [("main-sync", sync_engine)]
    if additional_sync_engine is not None:
        sync_engines.append(("additional-sync", additional_sync_engine))

    for label, sync_eng in sync_engines:
        sync_eng.dispose()
        logger.info("database_closed", label=label)


async def create_tables() -> None:
    """Create all tables using SQLModel metadata.

    Used in development and testing. Production should use Alembic.
    """

    # Import all models to register them with SQLModel metadata.
    from app.models import api_key  # noqa: F401
    from app.models import audit_log  # noqa: F401
    from app.models import feature_flag  # noqa: F401
    from app.models import graveyard  # noqa: F401
    from app.models import outbox  # noqa: F401
    from app.models import tenant  # noqa: F401
    from app.models import user  # noqa: F401

    engines: list[tuple[str, AsyncEngine]] = [("main", async_engine)]
    if additional_async_engine is not None:
        engines.append(("additional", additional_async_engine))

    for label, engine in engines:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            logger.info("tables_created", label=label)
        except Exception as e:
            logger.error("tables_creation_failed", label=label, error=str(e))
            if label == "main":
                raise
