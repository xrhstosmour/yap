"""Core module initialization.

This module exports commonly used components from core submodules.
Import from here for convenience rather than individual submodules.
"""

from uuid import UUID

from app.core.cache import CacheService
from app.core.cache import get_cache
from app.core.cache import get_redis
from app.core.circuit_breaker import CircuitBreakerError
from app.core.circuit_breaker import CircuitBreakerService
from app.core.circuit_breaker import CircuitState
from app.core.circuit_breaker import circuit_breaker
from app.core.email import send_batch_emails
from app.core.email import send_email
from app.core.encryption import CryptoService
from app.core.encryption import crypto
from app.core.encryption import decrypt
from app.core.encryption import encrypt
from app.core.encryption import generate_key
from app.core.feature_flags import feature_disabled
from app.core.feature_flags import feature_enabled
from app.core.feature_flags import refresh_cache
from app.core.feature_flags import sync_to_redis
from app.core.logging import get_logger
from app.core.logging import setup_logging
from app.core.rate_limit import RateLimiter
from app.core.rate_limit import RateLimitExceeded
from app.core.rate_limit import check_api_key_rate_limit
from app.core.rate_limit import check_user_rate_limit
from app.core.security import create_access_token
from app.core.security import create_refresh_token
from app.core.security import decode_token
from app.core.security import generate_api_key
from app.core.security import generate_api_key_id
from app.core.security import generate_password_hash
from app.core.security import mask_api_key
from app.core.security import verify_password
from app.core.settings import settings
from app.core.tenant import get_current_tenant_id
from app.core.tenant import set_current_tenant_id
from app.core.tenant import tenant_context

SYSTEM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")

__all__ = [
    "settings",
    "get_logger",
    "setup_logging",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "generate_password_hash",
    "verify_password",
    "generate_api_key",
    "generate_api_key_id",
    "mask_api_key",
    "get_current_tenant_id",
    "set_current_tenant_id",
    "tenant_context",
    "RateLimitExceeded",
    "RateLimiter",
    "check_user_rate_limit",
    "check_api_key_rate_limit",
    "CacheService",
    "get_cache",
    "get_redis",
    "CircuitBreakerError",
    "CircuitBreakerService",
    "CircuitState",
    "circuit_breaker",
    "feature_enabled",
    "feature_disabled",
    "refresh_cache",
    "sync_to_redis",
    "send_email",
    "send_batch_emails",
    "CryptoService",
    "crypto",
    "decrypt",
    "encrypt",
    "generate_key",
    "SYSTEM_TENANT_ID",
]
