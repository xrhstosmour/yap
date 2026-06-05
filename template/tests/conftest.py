"""Test fixtures and configuration."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.pool import StaticPool


@pytest.fixture(name="engine")
async def engine_fixture():
    """Create an in-memory async SQLite engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Ensure all models are registered before creating tables.
    from app.models import api_key  # noqa: F401
    from app.models import audit_log  # noqa: F401
    from app.models import feature_flag  # noqa: F401
    from app.models import graveyard  # noqa: F401
    from app.models import outbox  # noqa: F401
    from app.models import tenant  # noqa: F401
    from app.models import totp_recovery_code  # noqa: F401
    from app.models import user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(name="session")
async def session_fixture(engine):
    """Create an async database session for testing."""
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        yield session


@pytest.fixture(name="override_get_async_session")
async def override_get_async_session_fixture(session: AsyncSession):
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


@pytest.fixture(name="disable_rate_limit", autouse=True)
def disable_rate_limit_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable rate limit checks to avoid Redis dependency in tests."""

    async def _no_op(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("app.core.rate_limit.check_user_rate_limit", _no_op)
    monkeypatch.setattr("app.core.rate_limit.check_api_key_rate_limit", _no_op)
    monkeypatch.setattr("app.dependencies.check_user_rate_limit", _no_op)
    monkeypatch.setattr("app.dependencies.check_api_key_rate_limit", _no_op)


@pytest.fixture(name="override_settings")
def override_settings_fixture():
    """Override settings for testing."""
    from app.core.settings import Settings

    return Settings(
        SECRET_KEY="test-secret-key-for-testing-only",
        POSTGRESQL_PASSWORD="test-password",
        FIRST_SUPERUSER_PASSWORD="test-password",
        RABBITMQ_PASSWORD="test-password",
    )


@pytest.fixture(name="patch_test_fernet", autouse=True)
def patch_test_fernet_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a test Fernet cipher into the global crypto service.

    Ensures all encryption/decryption calls in tests use a consistent
    in-memory key without requiring CRYPTO_KEY in the environment.
    """
    from cryptography.fernet import Fernet

    import app.core.encryption as enc_mod

    test_key = Fernet.generate_key()
    test_fernet = Fernet(test_key)
    monkeypatch.setattr(enc_mod.crypto, "_fernet", test_fernet)
    monkeypatch.setattr(enc_mod.crypto, "_keys", [test_key.decode()])
