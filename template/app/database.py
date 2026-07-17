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
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import Generator
from contextvars import ContextVar

from sqlalchemy import create_engine
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


# Main engine.
async_database_url = str(settings.DATABASE_URI)

async_engine = create_async_engine(
    async_database_url,
    echo=settings.ENVIRONMENT == "local",
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
    pool_timeout=30,
)

async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Additional engine (preview/demo/staging database).
additional_async_database_url = str(settings.ADDITIONAL_DATABASE_URI)

additional_async_engine = create_async_engine(
    additional_async_database_url,
    echo=settings.ENVIRONMENT == "local",
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
    pool_timeout=30,
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
    """
    factory = (
        additional_async_session_factory
        if database_mode_variable.get() == ADDITIONAL_DATABASE_MODE
        else async_session_factory
    )
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

# Additional sync engine for Alembic migrations on the additional database.
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
    """
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

    for label, engine in [
        ("main", async_engine),
        ("additional", additional_async_engine),
    ]:
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
    for label, async_eng in [
        ("main", async_engine),
        ("additional", additional_async_engine),
    ]:
        await async_eng.dispose()
        logger.info("database_closed", label=label)
    for label, sync_eng in [
        ("main-sync", sync_engine),
        ("additional-sync", additional_sync_engine),
    ]:
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

    for label, engine in [
        ("main", async_engine),
        ("additional", additional_async_engine),
    ]:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)
            logger.info("tables_created", label=label)
        except Exception as e:
            logger.error("tables_creation_failed", label=label, error=str(e))
            if label == "main":
                raise
