"""Multi-tenancy context management using contextvars.

This module provides thread-safe tenant context for the current request.
The tenant context is set by middleware and accessed throughout the request
lifecycle for automatic tenant filtering.
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

# Context variable to store current tenant ID.
_current_tenant_id: ContextVar[UUID | None] = ContextVar(
    "current_tenant_id", default=None
)

# Deliberate cross-tenant access, distinct from "nobody set a tenant". See
# system_context() and BaseRepository._apply_tenant_filter().
_system_access: ContextVar[bool] = ContextVar("system_access", default=False)


def get_current_tenant_id() -> UUID | None:
    """Get the current tenant ID from context.

    Returns the tenant ID for the current request context.
    This is set by the tenant middleware and should be accessed
    through this function rather than directly.

    Returns:
        UUID of the current tenant, or None if not in a tenant context

    Example:
        tenant_id = get_current_tenant_id()
        if tenant_id:
            query = query.filter(Model.tenant_id == tenant_id)
    """
    return _current_tenant_id.get()


def set_current_tenant_id(tenant_id: UUID | None) -> None:
    """Set the current tenant ID in context.

    Called by tenant middleware to set the tenant context.
    Should be used with a context manager for automatic cleanup.

    Args:
        tenant_id: UUID of the tenant to set, or None for system context

    Example:
        with tenant_context(tenant_id):
            # All database queries will be filtered by tenant
            users = user_repository.list()
    """
    _current_tenant_id.set(tenant_id)


def tenant_context(tenant_id: UUID | None) -> TenantContext:
    """Context manager for tenant scope.

    Provides automatic cleanup of tenant context when exiting.
    Recommended way to set tenant context for a block of code.

    Args:
        tenant_id: UUID of the tenant to set

    Yields:
        None

    Example:
        with tenant_context(request.state.tenant_id):
            # Tenant context is active
            result = service.process()
        # Tenant context is automatically cleared
    """
    return TenantContext(tenant_id)


class TenantContext:
    """Context manager for safe tenant context manipulation.

    Ensures tenant context is properly restored even if an exception
    occurs during the context scope.
    """

    __slots__ = ("tenant_id", "token")

    def __init__(self, tenant_id: UUID | None) -> None:
        """Store the tenant ID to set on entry.

        Args:
            tenant_id: UUID of the tenant for this context
        """
        self.tenant_id = tenant_id

    def __enter__(self) -> None:
        """Enter the tenant context, setting the context variable."""
        self.token = _current_tenant_id.set(self.tenant_id)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the tenant context, restoring previous state."""
        _current_tenant_id.reset(self.token)


def is_system_access() -> bool:
    """Whether the current context has deliberately opted into cross-tenant
    access via `system_context()`.

    Returns:
        True inside a `system_context()` block, False otherwise.
    """
    return _system_access.get()


def system_context() -> SystemContext:
    """Context manager that marks deliberate, unscoped cross-tenant access.

    `BaseRepository._apply_tenant_filter()` raises `TenantContextRequiredError`
    when no tenant is set, unless this context is active. Without it, code
    that runs with no tenant context (background tasks, startup scripts) has
    no way to say "I meant to query across every tenant" versus "I forgot to
    set a tenant", and the two used to look identical: both silently ran
    unfiltered.

    Reach for this only when cross-tenant access is actually intended, a
    maintenance sweep, a lookup keyed by a globally unique ID the caller
    already resolved ownership for some other way. It does not grant any
    permission check of its own.

    Example:
        with system_context():
            # Queries run unfiltered by tenant, deliberately.
            record = await repository.get(known_id)
    """
    return SystemContext()


class SystemContext:
    """Context manager for `system_context()`. See its docstring."""

    __slots__ = ("token",)

    def __enter__(self) -> None:
        """Enter the system-access context."""
        self.token = _system_access.set(True)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the system-access context, restoring previous state."""
        _system_access.reset(self.token)
