"""Tests for `app.dependencies` billing-authorization helpers."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.dependencies import get_tenant_owner
from app.models.user import User
from app.models.user import UserRole

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _user(*, is_tenant_owner: bool, role: UserRole = UserRole.USER) -> User:
    return User(
        email="owner@example.com",
        hashed_password="x",
        tenant_id=TENANT_ID,
        is_tenant_owner=is_tenant_owner,
        role=role,
    )


class TestGetTenantOwner:
    def test_tenant_owner_passes(self) -> None:
        user = _user(is_tenant_owner=True)
        result = asyncio.run(get_tenant_owner(user))
        assert result is user

    def test_superuser_bypasses_ownership_check(self) -> None:
        user = _user(is_tenant_owner=False, role=UserRole.SUPERUSER)
        result = asyncio.run(get_tenant_owner(user))
        assert result is user

    def test_non_owner_non_superuser_raises_403(self) -> None:
        user = _user(is_tenant_owner=False)
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(get_tenant_owner(user))
        assert excinfo.value.status_code == 403
