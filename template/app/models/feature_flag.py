"""Feature flag model for dynamic feature toggling.

Provides a database-backed feature flag that can be toggled at runtime
without deployment, enabling gradual rollouts and operational controls.
"""

from __future__ import annotations

from sqlmodel import Field

from app.models.base import BaseModel


class FeatureFlag(BaseModel, table=True):
    """Database model for feature flags.

    Each flag has a unique name and a boolean state. Flags are the
    source of truth; they are synced to Redis on change for instant
    propagation across all application instances.

    Flags are deployment-wide, not per tenant. ``name`` is globally
    unique, ``feature_enabled()`` takes a bare name with no tenant, and
    both of its cache tiers are keyed on that name alone, so one tenant
    cannot hold a different state for a flag than another. The
    ``tenant_id`` column inherited from ``BaseModel`` is therefore not a
    scope here: ``FeatureFlagService`` writes every row under
    ``SYSTEM_TENANT_ID`` and reads them all back through
    ``system_context()``. Making flags genuinely per-tenant would need a
    ``UNIQUE(tenant_id, name)`` constraint, tenant-aware cache keys, and a
    tenant argument on ``feature_enabled()``.

    Attributes:
        id: UUID primary key
        name: Unique flag identifier (e.g. 'new_checkout_flow')
        state: Whether the feature is enabled
        description: Human-readable description of the feature
    """

    __tablename__ = "feature_flags"  # pyright: ignore[reportAssignmentType]

    name: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=255,
    )

    state: bool = Field(
        default=False,
        nullable=False,
    )

    description: str | None = Field(
        default=None,
        max_length=500,
    )
