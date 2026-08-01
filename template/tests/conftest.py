"""Test fixtures and configuration.

The whole suite runs against PostgreSQL, not SQLite, so tests exercise the same
database engine, constraints, and DDL as production. Each xdist worker gets its
own database created from the current migrations, and every test runs inside a
transaction that is rolled back afterwards for isolation.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import SYSTEM_TENANT_ID
from app.core.settings import Settings
from app.core.settings import settings

# Backend root (parent of tests/), where alembic.ini and migrations/ live.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _worker_database_name() -> str:
    """Unique database name per xdist worker so parallel tests do not collide."""
    worker = os.getenv("PYTEST_XDIST_WORKER", "main")
    return f"{settings.POSTGRESQL_DATABASE}_test_{worker}"


def _connection_info(database: str) -> str:
    """libpq connection string to the given database."""
    return (
        f"host={settings.POSTGRESQL_SERVER} "
        f"port={settings.POSTGRESQL_PORT} "
        f"user={settings.POSTGRESQL_USER} "
        f"password={settings.POSTGRESQL_PASSWORD} "
        f"dbname={database}"
    )


def _database_url(database: str) -> str:
    """Async SQLAlchemy URL (psycopg driver) for the given database."""
    password = quote(settings.POSTGRESQL_PASSWORD, safe="")
    return (
        f"postgresql+psycopg://{settings.POSTGRESQL_USER}:{password}"
        f"@{settings.POSTGRESQL_SERVER}:{settings.POSTGRESQL_PORT}/{database}"
    )


@pytest.fixture(scope="session")
def test_database() -> str:
    """Create a fresh per-worker database, migrate it, and drop it at the end.

    Runs synchronously (no event loop) so it is not tied to a function-scoped
    loop, and builds the schema via ``alembic upgrade head`` so migration-only
    DDL (extensions, exclusion constraints) is applied exactly as in production.
    """
    database = _worker_database_name()

    with psycopg.connect(_connection_info("postgres"), autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{database}"')

    url = _database_url(database)
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_config, "head")

    # Seed the system tenant that production provisions via initial_data, so
    # records written with the fallback tenant satisfy the tenant foreign key.
    with psycopg.connect(_connection_info(database), autocommit=True) as connection:
        connection.execute(
            "INSERT INTO tenants "
            "(id, created_at, updated_at, name, slug, is_active, settings) "
            "VALUES (%s::uuid, now(), now(), 'System', 'system', true, '{}'::json) "
            "ON CONFLICT (id) DO NOTHING",
            (str(SYSTEM_TENANT_ID),),
        )

    try:
        yield url
    finally:
        with psycopg.connect(
            _connection_info("postgres"), autocommit=True
        ) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


@pytest.fixture(name="engine")
async def engine_fixture(test_database: str) -> AsyncEngine:
    """Async engine bound to the per-worker test database."""
    engine = create_async_engine(test_database, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(name="session")
async def session_fixture(engine: AsyncEngine) -> AsyncSession:
    """Async session inside a transaction rolled back after each test.

    The session joins the connection's outer transaction via a savepoint, so
    application code may ``commit`` freely while every change is discarded on
    teardown, keeping tests isolated without recreating the schema each time.
    """
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture(name="override_get_async_session")
async def override_get_async_session_fixture(session: AsyncSession) -> None:
    """Override FastAPI DB dependency to use test session."""
    from app.database import get_async_session
    from app.main import app

    async def _get_test_session():
        yield session

    app.dependency_overrides[get_async_session] = _get_test_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_async_session, None)


@pytest.fixture(
    name="disable_rate_limit", autouse=True
)  # Disable rate limiting in tests by default
def disable_rate_limit_fixture(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Disable rate limit checks to avoid Redis dependency in tests."""
    from app.core.rate_limit import check_auth_rate_limit
    from app.main import app

    async def _no_op(  # noqa: ANN401
        *_args: Any,  # noqa: ANN401
        **_kwargs: Any,  # noqa: ANN401
    ) -> None:
        return None

    monkeypatch.setattr("app.core.rate_limit.check_user_rate_limit", _no_op)
    monkeypatch.setattr("app.core.rate_limit.check_api_key_rate_limit", _no_op)
    monkeypatch.setattr("app.dependencies.check_user_rate_limit", _no_op)
    monkeypatch.setattr("app.dependencies.check_api_key_rate_limit", _no_op)

    # check_auth_rate_limit is wired via `dependencies=[Depends(check_auth_rate_limit)]`
    # on the route decorators, so FastAPI captures the callable at import time.
    # Overriding the module attribute via monkeypatch would not reach it; use
    # FastAPI's dependency_overrides, which is keyed by the original callable.
    # The override must take no parameters: FastAPI re-derives the request
    # schema from the override's own signature, so a `*_args, **_kwargs`
    # no-op would surface `_args`/`_kwargs` as required query parameters.
    async def _no_op_dependency() -> None:
        return None

    app.dependency_overrides[check_auth_rate_limit] = _no_op_dependency
    yield
    app.dependency_overrides.pop(check_auth_rate_limit, None)


@pytest.fixture(name="disable_token_blacklist", autouse=True)
def disable_token_blacklist_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable Redis blacklist checks and writes in tests by default."""

    async def _not_blacklisted(*_args: Any, **_kwargs: Any) -> bool:  # noqa: ANN401
        return False

    async def _noop_blacklist(*_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        return None

    monkeypatch.setattr("app.dependencies.is_token_blacklisted", _not_blacklisted)
    monkeypatch.setattr("app.core.security.blacklist_token", _noop_blacklist)
    monkeypatch.setattr("app.services.auth_service.blacklist_token", _noop_blacklist)


@pytest.fixture(name="override_get_redis", autouse=True)
def override_get_redis_fixture() -> None:
    """Override FastAPI Redis dependency so route handlers do not need a real Redis."""
    from unittest.mock import AsyncMock

    from app.core.cache import get_redis
    from app.main import app

    mock_redis = AsyncMock()

    async def _mock_get_redis() -> AsyncMock:  # noqa: ANN202
        return mock_redis

    app.dependency_overrides[get_redis] = _mock_get_redis
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_redis, None)


@pytest.fixture(name="override_settings")
def override_settings_fixture() -> Settings:
    """Override settings for testing."""

    # NOTE: Only essential settings are overridden for tests.
    # Other settings (storage, email, OAuth) fall back to environment variables.
    return Settings(
        SECRET_KEY="test-secret-key-for-testing-only",
        POSTGRESQL_PASSWORD="test-password",
        FIRST_SUPERUSER_PASSWORD="test-password",
        RABBITMQ_PASSWORD="test-password",
        CRYPTO_KEY="dGVzdC1rZXktMzItYnl0ZXMtbG9uZw==",
    )


TEST_FERNET_KEY = Fernet.generate_key()


@pytest.fixture(name="patch_test_fernet", autouse=True)
def patch_test_fernet_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a test Fernet cipher into the global crypto service.

    Ensures all encryption/decryption calls in tests use a consistent
    in-memory key without requiring CRYPTO_KEY in the environment.
    """
    import app.core.encryption as enc_mod

    test_fernet = Fernet(TEST_FERNET_KEY)
    monkeypatch.setattr(enc_mod.crypto, "_fernet", test_fernet)
    monkeypatch.setattr(enc_mod.crypto, "_keys", [TEST_FERNET_KEY.decode()])
