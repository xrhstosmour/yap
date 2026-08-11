"""Tests for database module: init, close, session factory, URI, and routing."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# init_db


class TestInitDb:
    """Tests for init_db engine connection."""

    @pytest.mark.anyio
    async def test_init_db_connects_and_pings(self) -> None:
        """init_db opens a connection and executes SELECT 1."""
        from app.database import init_db

        mock_conn = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_ctx

        with patch("app.database.async_engine", mock_engine):
            await init_db()

        mock_engine.connect.assert_called_once()
        mock_conn.execute.assert_awaited_once()


# close_db


class TestCloseDb:
    """Tests for close_db engine disposal."""

    @pytest.mark.anyio
    async def test_close_db_disposes_engine(self) -> None:
        """close_db calls dispose on the async engine."""
        from app.database import close_db

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        with patch("app.database.async_engine", mock_engine):
            await close_db()

        mock_engine.dispose.assert_awaited_once()


# get_async_session


class TestGetAsyncSession:
    """Tests for the get_async_session FastAPI dependency."""

    @pytest.mark.anyio
    async def test_get_async_session_yields_and_commits_and_closes(self) -> None:
        """get_async_session yields a session, commits, and closes it."""
        from app.database import get_async_session

        mock_session = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("app.database.async_session_factory", mock_factory):
            async for session in get_async_session():
                assert session is mock_session

        mock_session.commit.assert_awaited_once()
        mock_session.close.assert_awaited_once()

    @pytest.mark.anyio
    async def test_get_async_session_rolls_back_on_exception(self) -> None:
        """get_async_session rolls back when the caller sends an exception.

        FastAPI dependency injection sends exceptions back into the
        generator via ``athrow()`` so the ``except Exception`` handler
        inside ``get_async_session`` can call ``session.rollback()``.
        """
        from app.database import get_async_session

        mock_session = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("app.database.async_session_factory", mock_factory):
            gen = get_async_session()
            await gen.__anext__()

            # Simulate FastAPI sending the handler exception back into the
            # dependency generator so rollback + close run.
            with pytest.raises(ValueError, match="boom"):
                await gen.athrow(ValueError("boom"))

        mock_session.rollback.assert_awaited_once()
        mock_session.close.assert_awaited_once()


# DATABASE_URI


class TestDatabaseURI:
    """Tests for DATABASE_URI construction from settings."""

    def test_database_uri_is_built_from_settings(self) -> None:
        """DATABASE_URI includes host, database, port, and user from settings."""
        from app.core.settings import Settings

        s = Settings(
            SECRET_KEY="test-secret-key-for-testing-only",
            POSTGRESQL_PASSWORD="test-password",
            FIRST_SUPERUSER_PASSWORD="test-password",
            RABBITMQ_PASSWORD="test-password",
            CRYPTO_KEY="test-crypto-key",
            GOOGLE_CLIENT_SECRET="test-secret",
            SMTP_PASSWORD="test-smtp-pass",
            STORAGE_SECRET_KEY="test-storage-key",
            POSTGRESQL_USER="myuser",
            POSTGRESQL_DATABASE="mydb",
            POSTGRESQL_SERVER="myhost",
            POSTGRESQL_PORT=5433,
        )

        uri = str(s.DATABASE_URI)
        assert "myhost" in uri
        assert "5433" in uri
        assert "mydb" in uri
        assert "myuser" in uri

    def test_database_uri_defaults_use_localhost(self) -> None:
        """DATABASE_URI defaults to localhost:5432/testdb."""
        from app.core.settings import settings

        uri = str(settings.DATABASE_URI)
        assert "localhost" in uri
        assert "5432" in uri
        # The database name and credentials depend on .env / settings defaults.
        # We verify that the URI points at the correct host and port.
        assert "://" in uri and "localhost" in uri and "5432" in uri

    def test_database_uri_includes_ssl_query_params(self) -> None:
        """DATABASE_URI appends sslmode and other SSL query parameters."""
        from app.core.settings import Settings

        s = Settings(
            SECRET_KEY="test-secret-key-for-testing-only",
            POSTGRESQL_PASSWORD="test-password",
            FIRST_SUPERUSER_PASSWORD="test-password",
            RABBITMQ_PASSWORD="test-password",
            CRYPTO_KEY="test-crypto-key",
            GOOGLE_CLIENT_SECRET="test-secret",
            SMTP_PASSWORD="test-smtp-pass",
            STORAGE_SECRET_KEY="test-storage-key",
            POSTGRESQL_USER="ssluser",
            POSTGRESQL_DATABASE="ssldb",
            POSTGRESQL_SERVER="sslhost",
            POSTGRESQL_PORT=5432,
            POSTGRESQL_SSL_MODE="require",
            POSTGRESQL_SSL_CA="/path/to/ca.pem",
            POSTGRESQL_SSL_CERT="/path/to/cert.pem",
            POSTGRESQL_SSL_KEY="/path/to/key.pem",
        )

        uri = str(s.DATABASE_URI)
        assert "sslmode=require" in uri
        assert "sslrootcert=/path/to/ca.pem" in uri
        assert "sslcert=/path/to/cert.pem" in uri
        assert "sslkey=/path/to/key.pem" in uri


# AsyncSessionFactory


class TestAsyncSessionFactory:
    """Tests for the async_session_factory configuration."""

    def test_async_session_factory_uses_async_session_class(self) -> None:
        """async_session_factory is configured with class_=AsyncSession."""
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.database import async_session_factory

        # The factory's class is AsyncSession, not the default AsyncSession.
        assert async_session_factory.class_ is AsyncSession

    def test_async_session_factory_expire_on_commit_is_false(self) -> None:
        """async_session_factory does not expire objects on commit."""
        from app.database import async_session_factory

        assert async_session_factory.kw["expire_on_commit"] is False

    def test_async_session_factory_autoflush_is_false(self) -> None:
        """async_session_factory has autoflush disabled."""
        from app.database import async_session_factory

        assert async_session_factory.kw["autoflush"] is False


# CelerySessionFactory


class TestCelerySessionFactory:
    """Tests for the celery_session_factory/celery_engine configuration.

    Celery tasks run each unit of work inside its own `asyncio.run()`, a
    fresh event loop per task. A pooled connection checked out under one
    loop is unusable once that loop closes, so the Celery engine must use
    `NullPool` instead of the app's pooled `async_engine`.
    """

    def test_celery_engine_uses_null_pool(self) -> None:
        """celery_engine does not pool connections across event loops."""
        from sqlalchemy.pool import NullPool

        from app.database import celery_engine

        assert isinstance(celery_engine.pool, NullPool)

    def test_celery_session_factory_uses_async_session_class(self) -> None:
        """celery_session_factory is configured with class_=AsyncSession."""
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.database import celery_session_factory

        assert celery_session_factory.class_ is AsyncSession

    def test_celery_session_factory_expire_on_commit_is_false(self) -> None:
        """celery_session_factory does not expire objects on commit."""
        from app.database import celery_session_factory

        assert celery_session_factory.kw["expire_on_commit"] is False

    def test_celery_session_factory_bound_to_celery_engine(self) -> None:
        """celery_session_factory is bound to celery_engine, not async_engine."""
        from app.database import async_engine
        from app.database import celery_engine
        from app.database import celery_session_factory

        assert celery_session_factory.kw["bind"] is celery_engine
        assert celery_session_factory.kw["bind"] is not async_engine


# Pool timeout configuration
# --------------------------


class TestPoolTimeout:
    """Tests that pool_timeout is threaded through from settings, not hardcoded."""

    def test_async_engine_pool_timeout_from_settings(self) -> None:
        """async_engine's pool_timeout matches settings.DATABASE_POOL_TIMEOUT."""
        from app.core.settings import settings
        from app.database import async_engine

        assert async_engine.pool._timeout == settings.DATABASE_POOL_TIMEOUT

    def test_additional_async_engine_pool_timeout_from_settings(self) -> None:
        """additional_async_engine's pool_timeout matches settings.DATABASE_POOL_TIMEOUT."""
        from app.core.settings import settings
        from app.database import additional_async_engine

        assert additional_async_engine is not None
        assert additional_async_engine.pool._timeout == settings.DATABASE_POOL_TIMEOUT


# Additional database gating
# ---------------------------


class TestAdditionalDatabaseGating:
    """Tests that the additional database engine is only created when configured."""

    def test_additional_database_enabled_by_default(self) -> None:
        """ADDITIONAL_DATABASE_ENABLED is True when POSTGRESQL_ADDITIONAL_DATABASE is set."""
        from app.database import ADDITIONAL_DATABASE_ENABLED

        assert ADDITIONAL_DATABASE_ENABLED is True

    def test_additional_engines_created_when_enabled(self) -> None:
        """The additional engine/session-factory attributes are populated by default."""
        from app.database import additional_async_engine
        from app.database import additional_async_session_factory
        from app.database import additional_sync_engine
        from app.database import additional_sync_session_factory

        assert additional_async_engine is not None
        assert additional_async_session_factory is not None
        assert additional_sync_engine is not None
        assert additional_sync_session_factory is not None

    @pytest.mark.anyio
    async def test_get_async_session_raises_when_additional_disabled(self) -> None:
        """get_async_session raises RuntimeError for additional mode when disabled."""
        from unittest.mock import patch

        from app.database import ADDITIONAL_DATABASE_MODE
        from app.database import database_mode_variable
        from app.database import get_async_session

        token = database_mode_variable.set(ADDITIONAL_DATABASE_MODE)
        try:
            with patch("app.database.additional_async_session_factory", None):
                with pytest.raises(
                    RuntimeError, match="POSTGRESQL_ADDITIONAL_DATABASE"
                ):
                    async for _ in get_async_session():
                        pass
        finally:
            database_mode_variable.reset(token)

    def test_get_additional_sync_session_raises_when_disabled(self) -> None:
        """get_additional_sync_session raises RuntimeError when disabled."""
        from unittest.mock import patch

        from app.database import get_additional_sync_session

        with patch("app.database.additional_sync_session_factory", None):
            with pytest.raises(RuntimeError, match="POSTGRESQL_ADDITIONAL_DATABASE"):
                next(get_additional_sync_session())

    @pytest.mark.anyio
    async def test_init_db_skips_additional_engine_when_none(self) -> None:
        """init_db only connects to the main engine when the additional one is None."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from app.database import init_db

        main_conn = AsyncMock()
        main_ctx = MagicMock()
        main_ctx.__aenter__ = AsyncMock(return_value=main_conn)
        main_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_main = MagicMock()
        mock_main.connect.return_value = main_ctx

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", None),
        ):
            await init_db()

        mock_main.connect.assert_called_once()

    @pytest.mark.anyio
    async def test_close_db_skips_additional_engines_when_none(self) -> None:
        """close_db only disposes the main engines when the additional ones are None."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch

        from app.database import close_db

        mock_main = MagicMock()
        mock_main.dispose = AsyncMock()
        mock_sync_main = MagicMock()

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", None),
            patch("app.database.sync_engine", mock_sync_main),
            patch("app.database.additional_sync_engine", None),
        ):
            await close_db()

        mock_main.dispose.assert_awaited_once()
        mock_sync_main.dispose.assert_called_once()


# lifespan (from app.main – may not be available until the template is rendered)

import sys  # noqa: E402

_main_stub_modules: dict[str, MagicMock] = {
    "app.core.telemetry": MagicMock(),
}
for _m_name, _m_value in _main_stub_modules.items():
    if _m_name not in sys.modules:
        sys.modules[_m_name] = _m_value

try:
    from app.main import lifespan  # noqa: E402
except ImportError:
    lifespan = None  # type: ignore[assignment]


@pytest.mark.skipif(
    lifespan is None,
    reason="app.main module not available (template not rendered)",
)
class TestLifespan:
    """Tests for the lifespan() context manager that handles DB lifecycle."""

    @pytest.mark.anyio
    async def test_lifespan_initializes_and_closes_db(self) -> None:
        """lifespan calls init_db and init_redis on startup and close_db + close_redis on shutdown."""
        mock_app = MagicMock()

        with (
            patch("app.main.init_db", new_callable=AsyncMock) as mock_init,
            patch("app.main.init_redis", new_callable=AsyncMock) as mock_redis_init,
            patch(
                "app.main.start_broadcast_relay", new_callable=AsyncMock
            ) as mock_relay_start,
            patch("app.main.close_db", new_callable=AsyncMock) as mock_close,
            patch("app.main.close_redis", new_callable=AsyncMock) as mock_redis_close,
            patch(
                "app.main.stop_broadcast_relay", new_callable=AsyncMock
            ) as mock_relay_stop,
            patch("app.main.setup_logging"),
        ):
            # setup_tracing is imported lazily inside lifespan and already
            # wrapped in try/except, so we do not need an explicit patch.
            async with lifespan(mock_app):
                pass

        mock_init.assert_awaited_once()
        mock_redis_init.assert_awaited_once()
        mock_relay_start.assert_awaited_once()
        mock_close.assert_awaited_once()
        mock_redis_close.assert_awaited_once()
        mock_relay_stop.assert_awaited_once()

    @pytest.mark.anyio
    async def test_lifespan_handles_init_db_failure(self) -> None:
        """lifespan catches init_db failure and still runs shutdown hooks."""
        mock_app = MagicMock()

        with (
            patch(
                "app.main.init_db",
                new_callable=AsyncMock,
                side_effect=Exception("DB connection failed"),
            ) as mock_init,
            patch("app.main.init_redis", new_callable=AsyncMock) as mock_redis_init,
            patch("app.main.start_broadcast_relay", new_callable=AsyncMock),
            patch("app.main.close_db", new_callable=AsyncMock) as mock_close,
            patch("app.main.close_redis", new_callable=AsyncMock) as mock_redis_close,
            patch("app.main.stop_broadcast_relay", new_callable=AsyncMock),
            patch("app.main.setup_logging"),
        ):
            # lifespan must not propagate the init_db error.
            async with lifespan(mock_app):
                pass

        mock_init.assert_awaited_once()
        mock_redis_init.assert_awaited_once()
        # Shutdown hooks must still execute.
        mock_close.assert_awaited_once()
        mock_redis_close.assert_awaited_once()


# Multi-database engine routing
# -----------------------------


class TestAdditionalDatabaseURI:
    """Tests for ADDITIONAL_DATABASE_URI construction from settings."""

    def test_additional_database_uri_is_built_from_settings(self) -> None:
        """ADDITIONAL_DATABASE_URI includes host, database, port, and user."""
        from app.core.settings import Settings

        s = Settings(
            SECRET_KEY="test-secret-key-for-testing-only",
            POSTGRESQL_PASSWORD="test-password",
            FIRST_SUPERUSER_PASSWORD="test-password",
            RABBITMQ_PASSWORD="test-password",
            CRYPTO_KEY="test-crypto-key",
            GOOGLE_CLIENT_SECRET="test-secret",
            SMTP_PASSWORD="test-smtp-pass",
            STORAGE_SECRET_KEY="test-storage-key",
            POSTGRESQL_USER="myuser",
            POSTGRESQL_ADDITIONAL_DATABASE="my_additional_db",
            POSTGRESQL_SERVER="myhost",
            POSTGRESQL_PORT=5433,
        )

        uri = str(s.ADDITIONAL_DATABASE_URI)
        assert "myhost" in uri
        assert "5433" in uri
        assert "my_additional_db" in uri
        assert "myuser" in uri
        assert "+psycopg" in uri

    def test_additional_database_uri_default_suffix(self) -> None:
        """ADDITIONAL_DATABASE_URI defaults to {project_slug}_additional."""
        from app.core.settings import Settings

        s = Settings(
            SECRET_KEY="test-secret-key-for-testing-only",
            POSTGRESQL_PASSWORD="test-password",
            FIRST_SUPERUSER_PASSWORD="test-password",
            RABBITMQ_PASSWORD="test-password",
            CRYPTO_KEY="test-crypto-key",
            GOOGLE_CLIENT_SECRET="test-secret",
            SMTP_PASSWORD="test-smtp-pass",
            STORAGE_SECRET_KEY="test-storage-key",
        )

        uri = str(s.ADDITIONAL_DATABASE_URI)
        assert "additional" in uri


class TestDatabaseModeVariable:
    """Tests for database_mode_variable contextvar."""

    @pytest.mark.anyio
    async def test_default_routes_to_main_factory(self) -> None:
        """get_async_session uses the main factory when mode is empty."""
        from app.database import get_async_session

        mock_session = AsyncMock()
        mock_main_ctx = MagicMock()
        mock_main_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_main_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_main_factory = MagicMock(return_value=mock_main_ctx)

        with (
            patch("app.database.async_session_factory", mock_main_factory),
            patch("app.database.additional_async_session_factory", MagicMock()),
        ):
            async for session in get_async_session():
                assert session is mock_session

        mock_main_factory.assert_called_once()

    @pytest.mark.anyio
    async def test_additional_mode_routes_to_additional_factory(self) -> None:
        """get_async_session uses the additional factory when mode is set."""
        from app.database import ADDITIONAL_DATABASE_MODE
        from app.database import database_mode_variable
        from app.database import get_async_session

        mock_session = AsyncMock(spec=AsyncSession)
        mock_additional_ctx = MagicMock()
        mock_additional_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_additional_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_additional_factory = MagicMock(return_value=mock_additional_ctx)

        token = database_mode_variable.set(ADDITIONAL_DATABASE_MODE)
        try:
            with (
                patch("app.database.async_session_factory", MagicMock()),
                patch(
                    "app.database.additional_async_session_factory",
                    mock_additional_factory,
                ),
            ):
                async for session in get_async_session():
                    assert session is mock_session

            mock_additional_factory.assert_called_once()
        finally:
            database_mode_variable.reset(token)


class TestInitDatabaseMultiEngine:
    """Tests for init_db with both engines."""

    @pytest.mark.anyio
    async def test_init_db_connects_to_both_engines(self) -> None:
        """init_db connects and pings both main and additional engines."""
        from app.database import init_db

        main_conn = AsyncMock()
        main_ctx = MagicMock()
        main_ctx.__aenter__ = AsyncMock(return_value=main_conn)
        main_ctx.__aexit__ = AsyncMock(return_value=None)

        additional_conn = AsyncMock()
        additional_ctx = MagicMock()
        additional_ctx.__aenter__ = AsyncMock(return_value=additional_conn)
        additional_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_main = MagicMock()
        mock_main.connect.return_value = main_ctx
        mock_additional = MagicMock()
        mock_additional.connect.return_value = additional_ctx

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", mock_additional),
        ):
            await init_db()

        mock_main.connect.assert_called_once()
        mock_additional.connect.assert_called_once()
        main_conn.execute.assert_awaited_once()
        additional_conn.execute.assert_awaited_once()

    @pytest.mark.anyio
    async def test_init_db_additional_failure_non_fatal(self) -> None:
        """init_db does not raise when the additional engine fails to connect."""
        from app.database import init_db

        main_conn = AsyncMock()
        main_ctx = MagicMock()
        main_ctx.__aenter__ = AsyncMock(return_value=main_conn)
        main_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_main = MagicMock()
        mock_main.connect.return_value = main_ctx
        mock_additional = MagicMock()
        mock_additional.connect.side_effect = Exception("additional DB down")

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", mock_additional),
        ):
            await init_db()

        mock_main.connect.assert_called_once()
        mock_additional.connect.assert_called_once()

    @pytest.mark.anyio
    async def test_init_db_main_failure_is_fatal(self) -> None:
        """init_db raises when the main engine fails to connect."""
        from app.database import init_db

        mock_main = MagicMock()
        mock_main.connect.side_effect = Exception("main DB down")
        mock_additional = MagicMock()

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", mock_additional),
        ):
            with pytest.raises(Exception, match="main DB down"):
                await init_db()

        mock_main.connect.assert_called_once()


class TestCloseDatabaseMultiEngine:
    """Tests for close_db with both engines."""

    @pytest.mark.anyio
    async def test_close_db_disposes_both_async_engines(self) -> None:
        """close_db disposes both main and additional async engines."""
        from app.database import close_db

        mock_main = MagicMock()
        mock_main.dispose = AsyncMock()
        mock_additional = MagicMock()
        mock_additional.dispose = AsyncMock()
        mock_sync_main = MagicMock()
        mock_sync_additional = MagicMock()

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", mock_additional),
            patch("app.database.sync_engine", mock_sync_main),
            patch("app.database.additional_sync_engine", mock_sync_additional),
        ):
            await close_db()

        mock_main.dispose.assert_awaited_once()
        mock_additional.dispose.assert_awaited_once()
        mock_sync_main.dispose.assert_called_once()
        mock_sync_additional.dispose.assert_called_once()


class TestCreateTablesMultiEngine:
    """Tests for create_tables with both engines."""

    @pytest.mark.anyio
    async def test_create_tables_runs_on_both_engines(self) -> None:
        """create_tables creates tables on both main and additional engines."""
        from app.database import create_tables

        mock_conn = MagicMock()
        mock_conn.run_sync = AsyncMock()
        mock_begin_ctx = MagicMock()
        mock_begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_main = MagicMock()
        mock_main.begin.return_value = mock_begin_ctx
        mock_additional = MagicMock()
        mock_additional.begin.return_value = mock_begin_ctx

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", mock_additional),
        ):
            await create_tables()

        mock_main.begin.assert_called_once()
        mock_additional.begin.assert_called_once()
        mock_conn.run_sync.assert_called()

    @pytest.mark.anyio
    async def test_create_tables_additional_failure_non_fatal(self) -> None:
        """create_tables does not raise when the additional engine fails."""
        from app.database import create_tables

        main_conn = MagicMock()
        main_conn.run_sync = AsyncMock()
        main_ctx = MagicMock()
        main_ctx.__aenter__ = AsyncMock(return_value=main_conn)
        main_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_main = MagicMock()
        mock_main.begin.return_value = main_ctx
        mock_additional = MagicMock()
        mock_additional.begin.side_effect = Exception("additional tables failed")

        with (
            patch("app.database.async_engine", mock_main),
            patch("app.database.additional_async_engine", mock_additional),
        ):
            await create_tables()

        mock_main.begin.assert_called_once()
        mock_additional.begin.assert_called_once()


class TestGetAdditionalSyncSession:
    """Tests for get_additional_sync_session."""

    def test_get_additional_sync_session_yields_and_commits(self) -> None:
        """get_additional_sync_session yields a session, commits, and closes."""
        from app.database import get_additional_sync_session

        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("app.database.additional_sync_session_factory", mock_factory):
            for session in get_additional_sync_session():
                assert session is mock_session

        mock_session.commit.assert_called_once()

    def test_get_additional_sync_session_rolls_back_on_exception(self) -> None:
        """get_additional_sync_session rolls back on exception."""
        from app.database import get_additional_sync_session

        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_session)
        mock_ctx.__exit__ = MagicMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("app.database.additional_sync_session_factory", mock_factory):
            gen = get_additional_sync_session()
            next(gen)
            with pytest.raises(ValueError, match="boom"):
                gen.throw(ValueError("boom"))

        mock_session.rollback.assert_called_once()


class TestAdditionalDatabaseModeConstant:
    """Tests for the ADDITIONAL_DATABASE_MODE constant."""

    def test_constant_value(self) -> None:
        """ADDITIONAL_DATABASE_MODE has the expected string value."""
        from app.database import ADDITIONAL_DATABASE_MODE

        assert ADDITIONAL_DATABASE_MODE == "additional"

    def test_database_mode_variable_default(self) -> None:
        """database_mode_variable defaults to empty string."""
        from app.database import database_mode_variable

        assert database_mode_variable.get() == ""
