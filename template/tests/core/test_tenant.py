"""Tests for the multi-tenancy context primitive (contextvars-based)."""

from __future__ import annotations

import asyncio
import contextvars
from uuid import uuid4

import pytest

from app.core.tenant import SystemContext
from app.core.tenant import TenantContext
from app.core.tenant import get_current_tenant_id
from app.core.tenant import is_system_access
from app.core.tenant import set_current_tenant_id
from app.core.tenant import system_context
from app.core.tenant import tenant_context

# Stands in for an authenticated caller's tenant.
SCOPED_TENANT = uuid4()


def test_get_current_tenant_id_defaults_to_none() -> None:
    """With no tenant set in this context, get_current_tenant_id() returns None."""
    assert get_current_tenant_id() is None


def test_set_current_tenant_id_updates_current_context() -> None:
    """set_current_tenant_id() should be visible to get_current_tenant_id()."""
    tenant_id = uuid4()

    set_current_tenant_id(tenant_id)

    try:
        assert get_current_tenant_id() == tenant_id
    finally:
        # Reset via the module-private ContextVar to avoid leaking into
        # other tests, since set_current_tenant_id() has no reset helper.
        from app.core.tenant import _current_tenant_id

        _current_tenant_id.set(None)


def test_tenant_context_sets_tenant_id_within_block() -> None:
    """tenant_context() should make the tenant ID visible inside the block."""
    tenant_id = uuid4()

    with tenant_context(tenant_id):
        assert get_current_tenant_id() == tenant_id


def test_tenant_context_restores_previous_state_on_exit() -> None:
    """tenant_context() should restore the prior tenant ID after the block exits."""
    assert get_current_tenant_id() is None

    with tenant_context(uuid4()):
        pass

    assert get_current_tenant_id() is None


def test_tenant_context_restores_state_on_exception() -> None:
    """tenant_context() should restore prior state even if the block raises."""
    with pytest.raises(ValueError, match="boom"):
        with tenant_context(uuid4()):
            raise ValueError("boom")

    assert get_current_tenant_id() is None


def test_tenant_context_supports_nesting() -> None:
    """Nested tenant_context() blocks should restore the outer tenant on exit."""
    outer_tenant = uuid4()
    inner_tenant = uuid4()

    with tenant_context(outer_tenant):
        assert get_current_tenant_id() == outer_tenant

        with tenant_context(inner_tenant):
            assert get_current_tenant_id() == inner_tenant

        assert get_current_tenant_id() == outer_tenant


def test_tenant_context_accepts_none_for_system_context() -> None:
    """tenant_context(None) should represent the unscoped/system context."""
    with tenant_context(uuid4()):
        with tenant_context(None):
            assert get_current_tenant_id() is None


def test_tenant_context_returns_tenant_context_instance() -> None:
    """tenant_context() should return a TenantContext instance.

    Construction alone must not touch the ContextVar: the value is only
    applied on __enter__, so simply not entering the `with` block leaves
    no state to tear down here.
    """
    result = tenant_context(uuid4())
    assert isinstance(result, TenantContext)
    assert get_current_tenant_id() is None


@pytest.mark.asyncio
async def test_concurrent_tasks_have_isolated_tenant_context() -> None:
    """Tenant context set in one asyncio task must not leak into another."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    observed: dict[str, object] = {}

    async def _run_as_tenant(label: str, tenant_id) -> None:
        with tenant_context(tenant_id):
            # Yield control so the other task can run and (if isolation were
            # broken) clobber this context's tenant ID.
            await asyncio.sleep(0)
            observed[label] = get_current_tenant_id()

    await asyncio.gather(
        _run_as_tenant("a", tenant_a),
        _run_as_tenant("b", tenant_b),
    )

    assert observed["a"] == tenant_a
    assert observed["b"] == tenant_b


def test_copy_context_isolates_tenant_id() -> None:
    """A copied context should not see tenant IDs set in the original context."""
    tenant_id = uuid4()

    def _set_and_read_in_copy() -> object | None:
        set_current_tenant_id(tenant_id)
        return get_current_tenant_id()

    copied = contextvars.copy_context()
    result = copied.run(_set_and_read_in_copy)

    assert result == tenant_id
    # The original (current) context must be unaffected by the copy's mutation.
    assert get_current_tenant_id() is None


def test_is_system_access_defaults_to_false() -> None:
    """With no system_context() block active, is_system_access() is False."""
    assert is_system_access() is False


def test_system_context_sets_flag_within_block() -> None:
    """system_context() should make is_system_access() True inside the block."""
    with system_context():
        assert is_system_access() is True


def test_system_context_restores_previous_state_on_exit() -> None:
    """system_context() should restore False after the block exits."""
    assert is_system_access() is False

    with system_context():
        pass

    assert is_system_access() is False


def test_system_context_restores_state_on_exception() -> None:
    """system_context() should restore prior state even if the block raises."""
    with pytest.raises(ValueError, match="boom"):
        with system_context():
            raise ValueError("boom")

    assert is_system_access() is False


def test_system_context_supports_nesting() -> None:
    """A nested system_context() should not break the outer block's state."""
    with system_context():
        assert is_system_access() is True

        with system_context():
            assert is_system_access() is True

        assert is_system_access() is True

    assert is_system_access() is False


def test_system_context_returns_system_context_instance() -> None:
    """system_context() should return a SystemContext instance.

    Construction alone must not touch the ContextVar: the value is only
    applied on __enter__, so simply not entering the `with` block leaves
    no state to tear down here.
    """
    result = system_context()
    assert isinstance(result, SystemContext)
    assert is_system_access() is False


def test_system_context_independent_of_tenant_context() -> None:
    """system_context() and tenant_context() are separate flags.

    Entering one must not implicitly set or clear the other: a caller
    that wraps a bootstrap lookup in system_context() while a real
    tenant is already set (e.g. re-entrant code within an authenticated
    request) must not lose that tenant_id.
    """
    tenant_id = uuid4()

    with tenant_context(tenant_id):
        with system_context():
            assert get_current_tenant_id() == tenant_id
            assert is_system_access() is True

        assert is_system_access() is False
        assert get_current_tenant_id() == tenant_id


class TestTenantContextIsScopedToOneRequest:
    """`tenant_middleware` in `main.py` looks dead and is not.

    `request.state.tenant_id` is set by `get_current_user`, a dependency,
    which runs downstream of every middleware. So the value the middleware
    reads is always `None`, and the middleware appears to do nothing. What
    it actually does is scope the tenant ContextVar to the request: without
    the `with` block, the tenant one request's dependency set stays set for
    the next request handled on the same task, and an unauthenticated
    request inherits the previous caller's tenant.

    An audit of this repository flagged the middleware as dead code. These
    tests are what showed it was not.
    """

    @staticmethod
    def _build(with_middleware: bool):  # noqa: ANN205
        """Build a minimal app with and without the middleware under test.

        Args:
            with_middleware: Whether to register the tenant middleware.

        Returns:
            The configured application.
        """
        from collections.abc import Awaitable
        from collections.abc import Callable

        from fastapi import Depends
        from fastapi import FastAPI
        from fastapi import Request
        from fastapi import Response

        application = FastAPI()

        if with_middleware:

            @application.middleware("http")
            async def tenant_middleware(
                request: Request,
                call_next: Callable[[Request], Awaitable[Response]],
            ) -> Response:
                tenant_id = getattr(request.state, "tenant_id", None)
                with tenant_context(tenant_id):
                    return await call_next(request)

        # Mirrors what `get_current_user` does. Async on purpose: a sync
        # dependency runs in a threadpool with its own context, which would
        # hide the leak this is testing for.
        async def authenticate() -> None:
            set_current_tenant_id(SCOPED_TENANT)

        @application.get("/authenticated", dependencies=[Depends(authenticate)])
        async def authenticated() -> dict[str, str | None]:
            current = get_current_tenant_id()
            return {"tenant": str(current) if current else None}

        @application.get("/anonymous")
        async def anonymous() -> dict[str, str | None]:
            current = get_current_tenant_id()
            return {"tenant": str(current) if current else None}

        return application

    async def _observe(self, with_middleware: bool) -> tuple[str | None, str | None]:
        """Make an authenticated request, then an anonymous one.

        Args:
            with_middleware: Whether to register the tenant middleware.

        Returns:
            The tenant each request observed.
        """
        from httpx import ASGITransport
        from httpx import AsyncClient

        application = self._build(with_middleware)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = (await client.get("/authenticated")).json()["tenant"]
            second = (await client.get("/anonymous")).json()["tenant"]
        return first, second

    @pytest.mark.asyncio
    async def test_the_tenant_does_not_leak_into_the_next_request(self) -> None:
        """With the middleware, each request starts clean."""
        authenticated, anonymous = await self._observe(with_middleware=True)

        assert authenticated == str(SCOPED_TENANT)
        assert anonymous is None

    @pytest.mark.asyncio
    async def test_without_the_middleware_the_tenant_leaks(self) -> None:
        """Removing it is a cross-tenant data leak, not a cleanup.

        This is the test that makes the middleware's purpose legible. If it
        ever starts passing with `anonymous is None`, the isolation moved
        somewhere else and the middleware may genuinely be redundant.
        """
        authenticated, anonymous = await self._observe(with_middleware=False)

        assert authenticated == str(SCOPED_TENANT)
        assert anonymous == str(SCOPED_TENANT)
