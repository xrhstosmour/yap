"""Tests for the multi-tenancy context primitive (contextvars-based)."""

from __future__ import annotations

import asyncio
import contextvars
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.core.tenant import TenantContext
from app.core.tenant import TenantContextMiddleware
from app.core.tenant import get_current_tenant_id
from app.core.tenant import set_current_tenant_id
from app.core.tenant import tenant_context


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


class TestTenantContextMiddleware:
    """Tests for TenantContextMiddleware."""

    @pytest.mark.asyncio
    async def test_sets_tenant_context_from_request_state(self) -> None:
        """Middleware should set tenant context from request.state.tenant_id."""
        tenant_id = uuid4()
        request = MagicMock()
        request.state.tenant_id = tenant_id

        observed: dict[str, object] = {}

        async def call_next(_request) -> str:  # noqa: ANN001
            observed["tenant_id"] = get_current_tenant_id()
            return "response"

        middleware = TenantContextMiddleware()
        response = await middleware(request, call_next)

        assert response == "response"
        assert observed["tenant_id"] == tenant_id
        # Context should be cleared again after the middleware returns.
        assert get_current_tenant_id() is None

    @pytest.mark.asyncio
    async def test_defaults_to_none_when_request_has_no_tenant_state(self) -> None:
        """Middleware should default to None when request.state has no tenant_id."""
        request = MagicMock(spec=["state"])
        request.state = MagicMock(spec=[])

        observed: dict[str, object] = {}

        async def call_next(_request) -> str:  # noqa: ANN001
            observed["tenant_id"] = get_current_tenant_id()
            return "response"

        middleware = TenantContextMiddleware()
        await middleware(request, call_next)

        assert observed["tenant_id"] is None

    @pytest.mark.asyncio
    async def test_calls_call_next_with_request(self) -> None:
        """Middleware should forward the request to call_next."""
        request = MagicMock()
        request.state.tenant_id = None
        call_next = AsyncMock(return_value="ok")

        middleware = TenantContextMiddleware()
        result = await middleware(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert result == "ok"
