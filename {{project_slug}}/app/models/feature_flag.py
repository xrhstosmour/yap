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

    Attributes:
        id: UUID primary key
        name: Unique flag identifier (e.g. 'new_checkout_flow')
        state: Whether the feature is enabled
        description: Human-readable description of the feature
        tenant_id: Optional tenant scope (null = global flag)
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
