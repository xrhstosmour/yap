"""Audit log model for tracking changes and access.

This module defines the AuditLog model for security auditing,
compliance, and debugging. All significant actions should be logged.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from typing import Any
from uuid import UUID

from sqlalchemy import JSON
from sqlalchemy import Index
from sqlmodel import Field

from app.core.encryption import EncryptedString
from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class AuditAction(StrEnum):
    """Enumeration of auditable actions.

    Actions are categorized by type:
    - AUTH: Authentication and authorization events
    - CRUD: Data operations
    - ADMIN: Administrative actions
    - SYSTEM: System-level events
    """

    # Authentication.
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    TOKEN_REFRESH = "token_refresh"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    SESSION_REVOKE = "session_revoke"

    # User Management.
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_ACTIVATE = "user_activate"
    USER_DEACTIVATE = "user_deactivate"

    # API Keys.
    APIKEY_CREATE = "apikey_create"
    APIKEY_UPDATE = "apikey_update"
    APIKEY_DELETE = "apikey_delete"
    APIKEY_REVOKE = "apikey_revoke"

    # Tenant Management.
    TENANT_CREATE = "tenant_create"
    TENANT_UPDATE = "tenant_update"
    TENANT_DELETE = "tenant_delete"

    # Admin Actions.
    ROLE_CHANGE = "role_change"
    PERMISSION_CHANGE = "permission_change"
    SETTINGS_CHANGE = "settings_change"

    # System.
    EXPORT = "export"
    IMPORT = "import"
    BACKUP = "backup"
    RESTORE = "restore"

    # GDPR.
    ACCOUNT_DELETION = "account_deletion"
    DATA_EXPORT = "data_export"


class AuditLog(BaseModel, table=True):
    """Audit log entry for tracking changes and access.

    Records all significant actions in the system for:
    - Security monitoring and incident investigation
    - Compliance requirements (SOC2, GDPR, etc.)
    - User activity tracking
    - Debugging and troubleshooting

    Attributes:
        id: UUID primary key
        action: The action that was performed
        actor_type: Type of actor (user, api_key, system)
        actor_id: ID of the actor (user_id or api_key_id)
        actor_email: Email of the actor (for display purposes). Encrypted
            at rest (`EncryptedString`); reads/writes are transparent plain
            text. No search hash: nothing looks audit logs up by actor
            email, only `actor_id`, so unlike `User.email` there is no
            equality-lookup need to trade off against.
        resource_type: Type of resource affected
        resource_id: ID of the affected resource
        changes: Before/after values for changes
        metadata: Additional context (IP, user agent, etc.)
        status: Whether the action succeeded or failed
        error_message: Error message if action failed
        tenant_id: Tenant context for the action
        created_at: When the action occurred

    Note:
        Audit logs are append-only and should never be modified
        or deleted. They support soft delete for data residency
        compliance but the record should be preserved.
    """

    __tablename__ = "audit_logs"  # pyright: ignore[reportAssignmentType]
    # Compound indexes replace the single-column actor_id and tenant_id indexes.
    # PostgreSQL can use the leading column of a compound index for point lookups,
    # so the single-column indexes become redundant once the compounds exist.
    __table_args__ = (
        Index("ix_audit_logs_actor_id_created_at", "actor_id", "created_at"),
        Index("ix_audit_logs_tenant_id_created_at", "tenant_id", "created_at"),
        # Compound index for get_recent_failures() — WHERE status='failure'
        # ORDER BY created_at DESC. Eliminates the post-scan sort pass.
        Index("ix_audit_logs_status_created_at", "status", "created_at"),
    )

    action: str = Field(
        nullable=False,
        index=True,
        max_length=50,
    )

    # Actor information.
    actor_type: str = Field(
        nullable=False,
        max_length=20,
    )

    # Compound index (actor_id, created_at) covers single-column actor_id lookups.
    actor_id: str = Field(
        nullable=False,
        index=False,
        max_length=100,
    )

    actor_email: str | None = Field(
        default=None,
        max_length=512,
        sa_type=EncryptedString(512),  # type: ignore[call-overload]
    )

    # Resource information.
    resource_type: str | None = Field(
        default=None,
        index=True,
        max_length=50,
    )

    resource_id: str | None = Field(
        default=None,
        index=True,
        max_length=100,
    )

    # Change tracking.
    changes: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    # Context.
    extra_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSON,
        nullable=False,
    )

    # Result.
    status: str = Field(
        nullable=False,
        max_length=20,
    )

    error_message: str | None = Field(
        default=None,
        max_length=1000,
    )

    # Multi-tenancy.
    # Compound index (tenant_id, created_at) covers single-column tenant_id lookups.
    tenant_id: UUID = Field(
        nullable=False,
        index=False,
        foreign_key="tenants.id",
    )

    def __repr__(self) -> str:
        """String representation of audit log entry."""
        return f"<AuditLog {self.action} by {self.actor_type}:{self.actor_id}>"
