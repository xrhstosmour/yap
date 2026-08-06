"""Models package - SQLModel database models.

This package contains all SQLModel models for the application.
Import models from here for consistency.
"""

from app.models.api_key import APIKey
from app.models.audit_log import AuditAction
from app.models.audit_log import AuditLog
from app.models.base import BaseModel
from app.models.base import TenantBase
from app.models.coupon import Coupon
from app.models.coupon import CouponRedemption
from app.models.feature_flag import FeatureFlag
from app.models.file import File
from app.models.invoice import Invoice
from app.models.invoice import InvoiceLineItem
from app.models.invoice import InvoiceSequence
from app.models.oauth_account import OAuthAccount
from app.models.oauth_account import OAuthProvider
from app.models.payment import Payment
from app.models.payment import PaymentMethod
from app.models.payment_event import PaymentEvent
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.subscription import SubscriptionStatus
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
    "Coupon",
    "CouponRedemption",
    "FeatureFlag",
    "File",
    "Invoice",
    "InvoiceLineItem",
    "InvoiceSequence",
    "OAuthAccount",
    "OAuthProvider",
    "Payment",
    "PaymentEvent",
    "PaymentMethod",
    "Plan",
    "Subscription",
    "SubscriptionStatus",
    "Tenant",
    "TenantBase",
    "TotpRecoveryCode",
    "User",
    "UserRole",
    "WebAuthnCredential",
]
