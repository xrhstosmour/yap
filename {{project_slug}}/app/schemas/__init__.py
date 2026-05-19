"""Schemas package - Pydantic validation schemas.

This package contains all Pydantic schemas for request/response
validation. Import from here for consistency.
"""

from app.schemas.api_key import API_KEY_SCOPES
from app.schemas.api_key import APIKeyBase
from app.schemas.api_key import APIKeyCreate
from app.schemas.api_key import APIKeyCreateResponse
from app.schemas.api_key import APIKeyListParams
from app.schemas.api_key import APIKeyListResponse
from app.schemas.api_key import APIKeyResponse
from app.schemas.api_key import APIKeyUpdate
from app.schemas.auth import LoginRequest
from app.schemas.auth import PasswordChangeRequest
from app.schemas.auth import PasswordResetConfirmRequest
from app.schemas.auth import PasswordResetRequest
from app.schemas.auth import RefreshTokenRequest
from app.schemas.auth import RegisterRequest
from app.schemas.auth import TokenResponse
from app.schemas.base import BaseSchema
from app.schemas.base import ErrorDetail
from app.schemas.base import ErrorResponse
from app.schemas.base import HealthResponse
from app.schemas.base import MessageResponse
from app.schemas.base import PaginatedResponse
from app.schemas.base import PaginationParams
from app.schemas.feature_flag import FeatureFlagCreate
from app.schemas.feature_flag import FeatureFlagListResponse
from app.schemas.feature_flag import FeatureFlagResponse
from app.schemas.feature_flag import FeatureFlagToggle
from app.schemas.feature_flag import FeatureFlagUpdate
from app.schemas.user import UserBase
from app.schemas.user import UserCreate
from app.schemas.user import UserListParams
from app.schemas.user import UserListResponse
from app.schemas.user import UserResponse
from app.schemas.user import UserUpdate
from app.schemas.user import UserUpdateMe

__all__ = [
    # Base.
    "BaseSchema",
    "PaginationParams",
    "PaginatedResponse",
    "MessageResponse",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    # Auth.
    "LoginRequest",
    "RegisterRequest",
    "TokenResponse",
    "RefreshTokenRequest",
    "PasswordChangeRequest",
    "PasswordResetRequest",
    "PasswordResetConfirmRequest",
    # User.
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserUpdateMe",
    "UserResponse",
    "UserListResponse",
    "UserListParams",
    # API Key.
    "API_KEY_SCOPES",
    "APIKeyBase",
    "APIKeyCreate",
    "APIKeyUpdate",
    "APIKeyResponse",
    "APIKeyCreateResponse",
    "APIKeyListResponse",
    "APIKeyListParams",
    # Feature Flags.
    "FeatureFlagCreate",
    "FeatureFlagUpdate",
    "FeatureFlagToggle",
    "FeatureFlagResponse",
    "FeatureFlagListResponse",
]
