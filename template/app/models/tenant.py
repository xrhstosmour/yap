"""Tenant model for multi-tenancy support.

This module defines the Tenant model which represents an organization
or customer in a multi-tenant architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from sqlalchemy import JSON
from sqlalchemy.orm import relationship
from sqlmodel import Field
from sqlmodel import Relationship

from app.models.base import BaseModel
from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.api_key import APIKey
    from app.models.user import User


class Tenant(TenantBase, BaseModel, table=True):
    """Tenant/organization model for multi-tenancy.

    Represents a tenant (organization) in the multi-tenant architecture.
    All user-scoped data is associated with a tenant.

    Attributes:
        id: UUID primary key
        name: Display name of the tenant
        slug: URL-safe identifier (unique)
        is_active: Whether the tenant is active
        settings: JSON blob for tenant-specific settings
        created_at: When the tenant was created
        updated_at: When the tenant was last modified
        deleted_at: Soft delete timestamp

    Relationships:
        users: Users belonging to this tenant
        api_keys: API keys belonging to this tenant
        audit_logs: Audit logs for this tenant
    """

    __tablename__ = "tenants"  # pyright: ignore[reportAssignmentType]

    # Tenant-specific settings.
    settings: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    # Relationships. Collections are `raise`, not `selectin`: a tenant's
    # child tables grow with the tenant, and `User.tenant` is eager, so
    # eager-loading these meant every authenticated request pulled every
    # API key in the tenant to serve one user. Ask for one explicitly where
    # it is genuinely needed:
    #
    #     select(Tenant).options(selectinload(Tenant.users))
    users: list[User] = Relationship(
        sa_relationship=relationship(
            "User",
            back_populates="tenant",
            lazy="raise",
        ),
    )

    api_keys: list[APIKey] = Relationship(
        sa_relationship=relationship(
            "APIKey",
            back_populates="tenant",
            lazy="raise",
        ),
    )
