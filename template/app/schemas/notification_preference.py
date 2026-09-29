"""Schemas for which kinds of push an account wants."""

from __future__ import annotations

from pydantic import Field

from app.models.notification_preference import MAXIMUM_KIND_LENGTH
from app.schemas.base import BaseSchema


class NotificationPreferenceRequest(BaseSchema):
    """Turn one kind of push on or off."""

    kind: str = Field(min_length=1, max_length=MAXIMUM_KIND_LENGTH)
    is_enabled: bool


class NotificationPreferencesResponse(BaseSchema):
    """Every answer this account has given.

    Only kinds that were answered appear. A client reads an absent kind
    as on, which is what the server does at send time, so the two cannot
    disagree about a kind nobody has touched.
    """

    preferences: dict[str, bool]
