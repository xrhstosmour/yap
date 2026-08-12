"""Models package - SQLModel database models.

This package contains all SQLModel models for the application.
Import models from here for consistency.
"""

from app.models.api_key import APIKey
from app.models.audit_log import AuditAction
from app.models.audit_log import AuditLog
from app.models.base import BaseModel
from app.models.base import TenantBase
from app.models.feature_flag import FeatureFlag
from app.models.file import File
from app.models.graveyard import Graveyard
from app.models.oauth_account import OAuthAccount
from app.models.oauth_account import OAuthProvider
from app.models.outbox import Outbox
from app.models.outbox import OutboxEvent
from app.models.tenant import Tenant
from app.models.totp_recovery_code import TotpRecoveryCode
from app.models.user import User
from app.models.user import UserRole
from app.models.webauthn_credential import WebAuthnCredential

__all__ = [
    "APIKey",
    "AuditAction",
    "AuditLog",
    "BaseModel",
    "FeatureFlag",
    "File",
    "Graveyard",
    "OAuthAccount",
    "OAuthProvider",
    "Outbox",
    "OutboxEvent",
    "Tenant",
    "TenantBase",
    "TotpRecoveryCode",
    "User",
    "UserRole",
    "WebAuthnCredential",
]
