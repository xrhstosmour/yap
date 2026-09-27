"""Schemas for registering a device to receive push notifications."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.device_token import DevicePlatform
from app.schemas.base import BaseSchema


class DeviceRegisterRequest(BaseSchema):
    """Request to claim a push token for the calling account."""

    token: str = Field(min_length=1, max_length=512)
    platform: DevicePlatform


class DeviceResponse(BaseSchema):
    """One registered install.

    The token itself is not echoed back. The client already has it, and
    the only thing returning it would add is another copy in a log.
    """

    id: UUID
    platform: DevicePlatform
    last_seen_at: datetime | None = None
    created_at: datetime
