"""Database connection and session management.

This module provides async SQLModel session management with connection pooling.
Sessions are created per-request and automatically closed after use.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("database")

# Async Engine Configuration.
# Using psycopg for async PostgreSQL support.
async_database_url = str(settings.DATABASE_URI).replace(
    "postgresql://", "postgresql+psycopg://"
)

async_engine = create_async_engine(
    async_database_url,
    echo=settings.ENVIRONMENT == "local",
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=settings.DATABASE_POOL_RECYCLE,
    pool_timeout=30,
)

# Session factory.
async_session_factory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession]:
    """Get async database session for dependency injection.

    Creates a new session for each request, ensuring proper
    cleanup after the request completes. Use as a FastAPI dependency.

    Yields:
        AsyncSession instance for database operations

    Example:
        @app.get("/users")
        async def get_users(session: AsyncSession = Depends(get_async_session)):
            result = await session.execute(select(User))
            return result.scalars().all()
    """
    async with async_session_factory() as session:
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


def get_sync_session() -> Generator[Session]:
    """Get sync database session for Alembic migrations.

    Yields:
        Sync Session instance for migrations

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


async def init_db() -> None:
    """Initialize database connection and create tables.

    Called during application startup to verify connectivity
    and optionally create all tables if using create_all mode.

    Note:
        In production, use Alembic migrations instead of create_all.
    """
    logger.info("database_initializing", url=settings.POSTGRESQL_SERVER)

    try:
        # Test connection.
        async with async_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        logger.info("database_connected", url=settings.POSTGRESQL_SERVER)
    except Exception as e:
        logger.error("database_connection_failed", error=str(e))
        raise


async def close_db() -> None:
    """Close database connections gracefully.

    Called during application shutdown to properly close
    all database connections and release resources.
    """
    logger.info("database_closing")
    await async_engine.dispose()
    logger.info("database_closed")


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

    async with async_engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
