"""Multi-tenancy context management using contextvars.

This module provides thread-safe tenant context for the current request.
The tenant context is set by middleware and accessed throughout the request
lifecycle for automatic tenant filtering.
"""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

from starlette.responses import Response

# Context variable to store current tenant ID.
_current_tenant_id: ContextVar[UUID | None] = ContextVar(
    "current_tenant_id", default=None
)


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


class TenantContextMiddleware:
    """Middleware to extract and set tenant context from requests.

    Extracts tenant ID from JWT token or API key and sets the
    tenant context for the duration of the request.
    """

    async def __call__(self, request, call_next) -> Response:
        """Process request with tenant context.

        Extracts tenant ID from authentication credentials
        and sets it in the context for downstream handlers.
        """
        from app.core.logging import get_logger

        logger = get_logger("middleware.tenant")

        # Extract tenant ID from request (set by auth middleware).
        tenant_id = getattr(request.state, "tenant_id", None)

        with tenant_context(tenant_id):
            logger.debug(
                "tenant_context_set", tenant_id=str(tenant_id) if tenant_id else None
            )
            response = await call_next(request)

        return response  # type: ignore[no-any-return]
