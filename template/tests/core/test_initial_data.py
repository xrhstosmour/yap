"""Tests for initial_data module: system tenant and superuser seeding."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

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
