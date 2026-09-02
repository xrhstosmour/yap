"""Tests for initial_data: tenant and superuser seeding, Metabase provisioning."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from sqlalchemy.engine import URL
from sqlalchemy.engine import make_url

from app.core import SYSTEM_TENANT_ID
from app.models.user import UserRole


class TestInit:
    """Tests for the init() function that seeds initial data."""

    # Helpers

    @staticmethod
    def _make_session_mock(
        tenant_result: object | None = None,
        user_result: object | None = None,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Build a mock session factory + session with controlled query results.

        Returns (session, context, factory) where:
          - factory() returns context
          - context.__aenter__ returns session
          - session.execute returns a result whose scalar_one_or_none
            returns tenant_result then user_result.
        """
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(
            side_effect=[tenant_result, user_result],
        )
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=None)
        factory = MagicMock(return_value=context)

        return session, context, factory

    @staticmethod
    def _patch_deps(session_factory: MagicMock) -> tuple:
        """Return a context-manager tuple that patches init() dependencies."""
        return (
            patch("app.initial_data.async_session_factory", session_factory),
            patch(
                "app.initial_data.generate_password_hash",
                return_value="hashed_password",
            ),
            patch("app.initial_data.settings"),
        )

    # Tenant creation / skip

    @pytest.mark.anyio
    async def test_creates_tenant_when_missing(self) -> None:
        """init() creates a tenant with SYSTEM_TENANT_ID when none exists."""
        from app.initial_data import init

        session, context, factory = self._make_session_mock(
            tenant_result=None,  # no tenant yet
            user_result=None,  # no user yet
        )

        p1, p2, mock_settings_patch = self._patch_deps(factory)
        with p1, p2, mock_settings_patch as mock_settings:
            mock_settings.FIRST_SUPERUSER_EMAIL = "admin@test.com"
            mock_settings.FIRST_SUPERUSER_PASSWORD = "testpass"
            mock_settings.FIRST_SUPERUSER_FULL_NAME = "Admin"

            await init()

        # Both tenant and user should be added.
        assert session.add.call_count == 2

        tenant_call = session.add.call_args_list[0][0][0]
        assert tenant_call.id == SYSTEM_TENANT_ID
        assert tenant_call.name == "System"
        assert tenant_call.slug == "system"

    @pytest.mark.anyio
    async def test_skips_tenant_when_exists(self) -> None:
        """init() does not create a second tenant when one already exists."""
        from app.initial_data import init
        from app.models.tenant import Tenant

        existing_tenant = Tenant(id=SYSTEM_TENANT_ID, name="System", slug="system")

        session, context, factory = self._make_session_mock(
            tenant_result=existing_tenant,
            user_result=None,
        )

        p1, p2, mock_settings_patch = self._patch_deps(factory)
        with p1, p2, mock_settings_patch as mock_settings:
            mock_settings.FIRST_SUPERUSER_EMAIL = "admin@test.com"
            mock_settings.FIRST_SUPERUSER_PASSWORD = "testpass"
            mock_settings.FIRST_SUPERUSER_FULL_NAME = "Admin"

            await init()

        # Only user is added, tenant is skipped.
        assert session.add.call_count == 1
        added_user = session.add.call_args_list[0][0][0]
        assert added_user.role == UserRole.SUPERUSER

    # Superuser creation / skip

    @pytest.mark.anyio
    async def test_creates_superuser_when_missing(self) -> None:
        """init() creates a superuser with role=SUPERUSER when none exists."""
        from app.initial_data import init

        session, context, factory = self._make_session_mock(
            tenant_result=None,
            user_result=None,
        )

        p1, p2, mock_settings_patch = self._patch_deps(factory)
        with p1, p2, mock_settings_patch as mock_settings:
            mock_settings.FIRST_SUPERUSER_EMAIL = "admin@test.com"
            mock_settings.FIRST_SUPERUSER_PASSWORD = "testpass"
            mock_settings.FIRST_SUPERUSER_FULL_NAME = "Admin"

            await init()

        # Second add should be the user.
        user_call = session.add.call_args_list[1][0][0]
        assert user_call.email == "admin@test.com"
        assert user_call.hashed_password == "hashed_password"
        assert user_call.full_name == "Admin"
        assert user_call.role == UserRole.SUPERUSER
        assert user_call.is_active is True
        assert user_call.is_verified is True
        assert user_call.tenant_id == SYSTEM_TENANT_ID

    @pytest.mark.anyio
    async def test_skips_superuser_when_exists(self) -> None:
        """init() does not create a second superuser when one already exists."""
        from app.initial_data import init
        from app.models.user import User

        existing_user = User(email="admin@test.com")

        session, context, factory = self._make_session_mock(
            tenant_result=None,
            user_result=existing_user,
        )

        p1, p2, mock_settings_patch = self._patch_deps(factory)
        with p1, p2, mock_settings_patch as mock_settings:
            mock_settings.FIRST_SUPERUSER_EMAIL = "admin@test.com"
            mock_settings.FIRST_SUPERUSER_PASSWORD = "testpass"
            mock_settings.FIRST_SUPERUSER_FULL_NAME = "Admin"

            await init()

        # Only tenant should be added, user is skipped.
        assert session.add.call_count == 1
        tenant_call = session.add.call_args_list[0][0][0]
        assert tenant_call.id == SYSTEM_TENANT_ID

    # Happy path, full init

    @pytest.mark.anyio
    async def test_init_runs_successfully(self) -> None:
        """The init() function completes without error when both are missing."""
        from app.initial_data import init

        session, context, factory = self._make_session_mock(
            tenant_result=None,
            user_result=None,
        )

        p1, p2, mock_settings_patch = self._patch_deps(factory)
        with p1, p2, mock_settings_patch as mock_settings:
            mock_settings.FIRST_SUPERUSER_EMAIL = "admin@test.com"
            mock_settings.FIRST_SUPERUSER_PASSWORD = "testpass"
            mock_settings.FIRST_SUPERUSER_FULL_NAME = "Admin"

            await init()

        # Both tenant and user were created.
        assert session.add.call_count == 2
        assert session.commit.call_count == 2


class TestSqlstate:
    """Tests for _sqlstate(), which decides what a failure may be shrugged off as."""

    def test_reads_the_code_off_a_wrapped_driver_error(self) -> None:
        """A SQLAlchemy error wrapping a psycopg error exposes its SQLSTATE."""
        from app.initial_data import _sqlstate

        error = Exception("wrapper")
        error.orig = SimpleNamespace(sqlstate="42P04")  # type: ignore[attr-defined]

        assert _sqlstate(error) == "42P04"

    def test_returns_none_for_an_error_with_no_driver_underneath(self) -> None:
        """A plain exception has no SQLSTATE, so nothing may be tolerated."""
        from app.initial_data import _sqlstate

        assert _sqlstate(ValueError("not a database error")) is None

    def test_returns_none_when_the_driver_error_has_no_code(self) -> None:
        """An orig without sqlstate must not be mistaken for a match."""
        from app.initial_data import _sqlstate

        error = Exception("wrapper")
        error.orig = SimpleNamespace()  # type: ignore[attr-defined]

        assert _sqlstate(error) is None


class TestSetupMetabase:
    """Tests for the provisioning branches that only a broken cluster reaches.

    On a cluster the project owns, the app role is a superuser and every
    statement here succeeds, so the happy path never enters the error handling.
    These drive it directly instead.
    """

    # Helpers

    @staticmethod
    def _make_engine_mock(execute_effects: list[object]) -> MagicMock:
        """Build a create_engine mock whose connections replay execute_effects.

        Both engines opened by _setup_metabase share one connection mock, so
        execute_effects is the whole statement sequence in order: CREATE
        DATABASE, CREATE USER, GRANT CONNECT, GRANT SELECT, ALTER DEFAULT
        PRIVILEGES, then the pg_roles check. An entry that is an Exception is
        raised, anything else is returned.
        """

        def execute(_statement: object) -> object:
            effect = execute_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect

        conn = MagicMock()
        conn.execute = MagicMock(side_effect=execute)

        context = MagicMock()
        context.__enter__ = MagicMock(return_value=conn)
        context.__exit__ = MagicMock(return_value=None)

        engine = MagicMock()
        engine.connect = MagicMock(return_value=context)
        return MagicMock(return_value=engine)

    @staticmethod
    def _role_found(found: bool = True) -> MagicMock:
        """Result object for the pg_roles verification query."""
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=1 if found else None)
        return result

    @staticmethod
    def _database_error(sqlstate: str) -> Exception:
        """A driver-shaped error carrying the given SQLSTATE."""
        error = Exception(f"database error {sqlstate}")
        error.orig = SimpleNamespace(sqlstate=sqlstate)  # type: ignore[attr-defined]
        return error

    @staticmethod
    def _patch_settings() -> object:
        """Patch settings with the three attributes provisioning reads."""
        return patch(
            "app.initial_data.settings",
            MagicMock(
                DATABASE_URI="postgresql+psycopg://user:pass@host:5432/appdb",
                POSTGRESQL_DATABASE="appdb",
                POSTGRESQL_USER="app",
            ),
        )

    # Tolerated failures

    def test_tolerates_the_database_already_existing(self) -> None:
        """42P04 on CREATE DATABASE is what a re-run looks like."""
        from app.initial_data import _setup_metabase

        effects: list[object] = [
            self._database_error("42P04"),
            None,
            None,
            None,
            None,
            self._role_found(),
        ]
        with (
            self._patch_settings(),
            patch("app.initial_data.create_engine", self._make_engine_mock(effects)),
        ):
            _setup_metabase("secret")

    def test_tolerates_the_role_already_existing(self) -> None:
        """42710 on CREATE USER is what a re-run looks like."""
        from app.initial_data import _setup_metabase

        effects: list[object] = [
            None,
            self._database_error("42710"),
            None,
            None,
            None,
            self._role_found(),
        ]
        with (
            self._patch_settings(),
            patch("app.initial_data.create_engine", self._make_engine_mock(effects)),
        ):
            _setup_metabase("secret")

    # Failures that must not be tolerated

    def test_reraises_when_the_role_cannot_be_created(self) -> None:
        """42501 is insufficient privilege, the case that used to be swallowed.

        This is the regression: a connecting user without CREATEROLE was
        reported as "already exists" and then as "setup complete".
        """
        from app.initial_data import _setup_metabase

        # Padded past the failure so that a swallowed error reports as
        # "DID NOT RAISE" rather than running the mock out of effects.
        effects: list[object] = [
            None,
            self._database_error("42501"),
            None,
            None,
            None,
            self._role_found(),
        ]
        with (
            self._patch_settings(),
            patch("app.initial_data.create_engine", self._make_engine_mock(effects)),
            pytest.raises(Exception, match="42501"),
        ):
            _setup_metabase("secret")

    def test_reraises_when_the_database_cannot_be_created(self) -> None:
        """A CREATE DATABASE failure that is not 42P04 stops provisioning."""
        from app.initial_data import _setup_metabase

        effects: list[object] = [
            self._database_error("42501"),
            None,
            None,
            None,
            None,
            self._role_found(),
        ]
        with (
            self._patch_settings(),
            patch("app.initial_data.create_engine", self._make_engine_mock(effects)),
            pytest.raises(Exception, match="42501"),
        ):
            _setup_metabase("secret")

    def test_raises_when_the_role_is_absent_after_provisioning(self) -> None:
        """Every statement can pass and the role still not be there."""
        from app.initial_data import _setup_metabase

        effects: list[object] = [
            None,
            None,
            None,
            None,
            None,
            self._role_found(found=False),
        ]
        with (
            self._patch_settings(),
            patch("app.initial_data.create_engine", self._make_engine_mock(effects)),
            pytest.raises(RuntimeError, match="does not exist after provisioning"),
        ):
            _setup_metabase("secret")

    def test_completes_when_the_role_is_present(self) -> None:
        """The happy path returns without raising."""
        from app.initial_data import _setup_metabase

        effects: list[object] = [None, None, None, None, None, self._role_found()]
        with (
            self._patch_settings(),
            patch("app.initial_data.create_engine", self._make_engine_mock(effects)),
        ):
            _setup_metabase("secret")

    # The public wrapper

    def test_skips_provisioning_without_a_password(self) -> None:
        """Metabase is optional, so no password means nothing to do."""
        from app.initial_data import setup_metabase

        inner = MagicMock()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("app.initial_data._setup_metabase", inner),
        ):
            setup_metabase()

        inner.assert_not_called()

    def test_reports_failure_without_propagating(self) -> None:
        """A failure is logged at error level and the rest of seeding continues."""
        from app.initial_data import setup_metabase

        inner = MagicMock(side_effect=RuntimeError("no CREATEROLE"))
        with (
            patch.dict(
                os.environ, {"METABASE_READ_ONLY_PASSWORD": "secret"}, clear=True
            ),
            patch("app.initial_data._setup_metabase", inner),
            patch("app.initial_data.logger") as logger,
        ):
            setup_metabase()

        inner.assert_called_once_with("secret")
        logger.error.assert_called_once()


class TestAdminConnection:
    """Tests the connection `_setup_metabase` opens to create the database.

    `CREATE DATABASE` cannot run inside the database being created, so
    provisioning reconnects to `postgres` first. That URL was derived with
    `base_url.rsplit("/", 1)[0] + "/postgres"`, which throws away everything
    after the last slash. With `POSTGRESQL_SSL_MODE` set that is the whole
    query string, so the admin connection silently dropped TLS. With a
    `sslrootcert` path the last slash is inside the path itself, so the
    database was never switched and the certificate path was mangled.
    """

    APP_URI_PLAIN = "postgresql+psycopg://user:pass@host:5432/appdb"
    APP_URI_SSL = "postgresql+psycopg://user:pass@host:5432/appdb?sslmode=require"
    APP_URI_CERTIFICATE = (
        "postgresql+psycopg://user:pass@host:5432/appdb"
        "?sslmode=verify-full&sslrootcert=/etc/ssl/ca.pem"
    )

    @staticmethod
    def _provision(application_uri: str) -> MagicMock:
        """Run provisioning against a stubbed engine and return the factory.

        Args:
            application_uri: What `settings.DATABASE_URI` resolves to.

        Returns:
            The `create_engine` mock, carrying both calls it received.
        """
        from app.initial_data import _setup_metabase

        effects: list[object] = [
            None,
            None,
            None,
            None,
            None,
            TestSetupMetabase._role_found(),
        ]
        engine_factory = TestSetupMetabase._make_engine_mock(effects)
        settings_patch = patch(
            "app.initial_data.settings",
            MagicMock(
                DATABASE_URI=application_uri,
                POSTGRESQL_DATABASE="appdb",
                POSTGRESQL_USER="app",
            ),
        )
        with settings_patch, patch("app.initial_data.create_engine", engine_factory):
            _setup_metabase("secret")

        return engine_factory

    def _admin_url(self, application_uri: str) -> URL:
        """The URL provisioning uses for its administrative connection.

        Args:
            application_uri: What `settings.DATABASE_URI` resolves to.

        Returns:
            The parsed URL of the first `create_engine` call.
        """
        engine_factory = self._provision(application_uri)
        return make_url(engine_factory.call_args_list[0].args[0])

    @pytest.mark.parametrize(
        "application_uri",
        [APP_URI_PLAIN, APP_URI_SSL, APP_URI_CERTIFICATE],
    )
    def test_it_connects_to_the_maintenance_database(
        self, application_uri: str
    ) -> None:
        """`CREATE DATABASE metabase` has to run from outside `appdb`.

        Args:
            application_uri: What `settings.DATABASE_URI` resolves to.
        """
        assert self._admin_url(application_uri).database == "postgres"

    @pytest.mark.parametrize(
        "application_uri",
        [APP_URI_PLAIN, APP_URI_SSL, APP_URI_CERTIFICATE],
    )
    def test_the_connection_parameters_are_kept(self, application_uri: str) -> None:
        """A cluster reachable only over TLS refuses the plaintext attempt.

        Args:
            application_uri: What `settings.DATABASE_URI` resolves to.
        """
        expected = dict(make_url(application_uri).query)

        assert dict(self._admin_url(application_uri).query) == expected

    @pytest.mark.parametrize(
        "application_uri",
        [APP_URI_PLAIN, APP_URI_SSL, APP_URI_CERTIFICATE],
    )
    def test_only_the_database_changes(self, application_uri: str) -> None:
        """Host, port and credentials are the application's own.

        Args:
            application_uri: What `settings.DATABASE_URI` resolves to.
        """
        application_url = make_url(application_uri)
        admin_url = self._admin_url(application_uri)

        assert (admin_url.host, admin_url.port) == (
            application_url.host,
            application_url.port,
        )
        assert (admin_url.username, admin_url.password) == (
            application_url.username,
            application_url.password,
        )

    def test_the_second_connection_is_the_application_database(self) -> None:
        """The grants have to run inside `appdb`, not the maintenance one."""
        engine_factory = self._provision(self.APP_URI_CERTIFICATE)

        assert engine_factory.call_args_list[1].args[0] == self.APP_URI_CERTIFICATE
