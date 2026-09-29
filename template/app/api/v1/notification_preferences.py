"""Routes for which kinds of push an account wants.

This module provides the endpoints an app calls to read and change the
kinds of notification the signed-in account receives.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import status

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.repositories.notification_preference_repository import (
    NotificationPreferenceRepository,
)
from app.schemas.notification_preference import NotificationPreferenceRequest
from app.schemas.notification_preference import NotificationPreferencesResponse

router = APIRouter(
    prefix="/notification-preferences",
    tags=["Notification preferences"],
)
logger = get_logger("api.notification_preferences")


@router.get(
    "",
    response_model=NotificationPreferencesResponse,
    status_code=status.HTTP_200_OK,
    summary="Which kinds of push this account wants",
    description=(
        "Returns only the kinds the account has answered. Anything absent "
        "is on, which is what the server assumes when it sends."
    ),
)
async def read_notification_preferences(
    current_user: CurrentUser,
    session: SessionDependency,
) -> NotificationPreferencesResponse:
    """Return the calling account's answers."""
    repository = NotificationPreferenceRepository(session)
    return NotificationPreferencesResponse(
        preferences=await repository.for_user(current_user.id)
    )


@router.put(
    "",
    response_model=NotificationPreferencesResponse,
    status_code=status.HTTP_200_OK,
    summary="Turn one kind of push on or off",
    description=(
        "Records the answer and returns every answer this account has "
        "given, so one screen can be redrawn from one response."
    ),
)
async def set_notification_preference(
    data: NotificationPreferenceRequest,
    current_user: CurrentUser,
    session: SessionDependency,
) -> NotificationPreferencesResponse:
    """Record one answer and return them all.

    A put rather than a patch: one kind is the whole resource being
    named, and sending it twice with the same body leaves the same state.

    The kind is not checked against a list. What an application notifies
    about is its own vocabulary, and an unknown kind stored here simply
    never matches anything at send time, which is harmless.
    """
    repository = NotificationPreferenceRepository(session)
    await repository.record(current_user.id, data.kind, data.is_enabled)
    await session.commit()
    logger.info(
        "notification_preference_set",
        user_id=str(current_user.id),
        kind=data.kind,
        is_enabled=data.is_enabled,
    )
    return NotificationPreferencesResponse(
        preferences=await repository.for_user(current_user.id)
    )
