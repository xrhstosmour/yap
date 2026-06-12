"""FastAPI dependencies for dependency injection.

This module provides reusable dependencies for authentication,
database sessions, and common operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import check_api_key_rate_limit
from app.core.rate_limit import check_user_rate_limit
from app.core.security import decode_token
from app.core.tenant import set_current_tenant_id
from app.database import get_async_session
from app.models.api_key import APIKey
from app.models.user import User
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.user_repository import UserRepository

if TYPE_CHECKING:
    pass

logger = get_logger("deps")

# OAuth2 scheme for JWT Bearer tokens.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# Type aliases for dependency injection.
SessionDep = Annotated[AsyncSession, Depends(get_async_session)]
TokenDep = Annotated[str, Depends(oauth2_scheme)]


async def get_current_user(
    session: SessionDep,
    token: TokenDep,
    request: Request,
) -> User:
    """Get the current authenticated user from JWT token.

    Extracts and validates the JWT token, using embedded claims
    for tenant context before performing a single DB lookup.

    Args:
        session: Database session
        token: JWT access token
        request: FastAPI request object

    Returns:
        Authenticated User

    Raises:
        HTTPException: If token is invalid or user not found
    """
    try:
        payload = decode_token(token)

        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid token type",
            )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid token",
            )

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )

    user_repository = UserRepository(session)
    user = await user_repository.get(UUID(user_id))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    # Reject access tokens issued before the last password change or reset.
    token_version = payload.get("token_version")
    if token_version is not None and int(token_version) != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )

    if user.tenant_id:
        set_current_tenant_id(user.tenant_id)
        request.state.tenant_id = user.tenant_id

    request.state.user_id = user.id
    request.state.user = user

    await check_user_rate_limit(str(user.id))

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_superuser(current_user: CurrentUser) -> User:
    """Get the current user if they are a superuser.

    Args:
        current_user: Current authenticated user

    Returns:
        User if superuser

    Raises:
        HTTPException: If user is not a superuser
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return current_user


SuperuserUser = Annotated[User, Depends(get_current_superuser)]


async def get_api_key_auth(
    session: SessionDep,
    request: Request,
) -> APIKey | None:
    """Authenticate request using API key.

    Checks for X-API-Key header and validates the key.
    If valid, sets the tenant context and checks rate limits.

    Args:
        session: Database session
        request: FastAPI request object

    Returns:
        APIKey if valid, None otherwise
    """
    api_key_header = request.headers.get("X-API-Key")

    if not api_key_header:
        return None

    # Parse key (format: key_id:key_secret).
    if ":" not in api_key_header:
        return None

    key_id, key_secret = api_key_header.split(":", 1)

    # Verify key.
    apikey_repository = APIKeyRepository(session)
    api_key = await apikey_repository.verify_key(key_id, key_secret)

    if not api_key:
        return None

    # Set tenant context.
    set_current_tenant_id(api_key.tenant_id)
    request.state.tenant_id = api_key.tenant_id
    request.state.api_key = api_key

    # Check rate limit.
    await check_api_key_rate_limit(api_key.key_id)

    return api_key


APIKeyAuth = Annotated[APIKey | None, Depends(get_api_key_auth)]


async def get_optional_current_user(
    session: SessionDep,
    request: Request,
) -> User | None:
    """Try to authenticate via JWT Bearer token, returning None on failure.

    Unlike get_current_user, this does not raise HTTPException on missing
    or invalid auth, making it suitable for endpoints that accept multiple
    auth methods.

    Args:
        session: Database session
        request: FastAPI request object

    Returns:
        Authenticated User or None
    """
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[len("Bearer ") :]

    try:
        payload = decode_token(token)

        if payload.get("type") != "access":
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

    except Exception:
        return None

    user_repository = UserRepository(session)
    user = await user_repository.get(UUID(user_id))

    if not user or not user.is_active:
        return None

    # Reject access tokens issued before the last password change or reset.
    token_version = payload.get("token_version")
    if token_version is not None and int(token_version) != user.token_version:
        return None

    if user.tenant_id:
        set_current_tenant_id(user.tenant_id)
        request.state.tenant_id = user.tenant_id

    request.state.user_id = user.id
    request.state.user = user

    await check_user_rate_limit(str(user.id))

    return user


OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]


async def get_any_auth(
    current_user: OptionalCurrentUser,
    api_key: APIKeyAuth,
) -> User | APIKey | None:
    """Get either user or API key authentication.

    Tries both JWT and API key authentication. Returns the first valid
    credential found, or None if neither is present.

    Returns:
        User, APIKey, or None based on what was provided
    """
    return current_user or api_key


AnyAuth = Annotated[User | APIKey | None, Depends(get_any_auth)]
