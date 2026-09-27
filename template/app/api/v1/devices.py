"""Device registration routes for push notifications.

This module provides the endpoints an app calls to claim a push token
for the signed-in account and to give it up again on sign-out.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Response
from fastapi import status

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.repositories.device_token_repository import DeviceTokenRepository
from app.schemas.device import DeviceRegisterRequest
from app.schemas.device import DeviceResponse

router = APIRouter(prefix="/devices", tags=["Devices"])
logger = get_logger("api.devices")


@router.post(
    "",
    response_model=DeviceResponse,
    status_code=status.HTTP_200_OK,
    summary="Register this device for push notifications",
    description=(
        "Claims a push token for the signed-in account. Safe to call on "
        "every launch: an unchanged token is refreshed rather than "
        "duplicated, and a token last seen on another account is moved."
    ),
)
async def register_device(
    data: DeviceRegisterRequest,
    current_user: CurrentUser,
    session: SessionDependency,
) -> DeviceResponse:
    """Claim a push token for the calling account.

    Answers 200 rather than 201 because the client cannot tell whether
    its token is new, and a status that depends on something it does not
    know is a status it cannot act on.
    """
    devices = DeviceTokenRepository(session)
    device = await devices.register(
        user_id=current_user.id,
        token=data.token,
        platform=data.platform,
    )
    await session.commit()
    logger.info(
        "device_registered",
        user_id=str(current_user.id),
        platform=device.platform.value,
    )
    return DeviceResponse.model_validate(device, from_attributes=True)


@router.delete(
    "/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop sending push notifications to this device",
    description=(
        "Called on sign-out, so a shared device stops receiving the "
        "previous account's notifications."
    ),
)
async def forget_device(
    token: str,
    current_user: CurrentUser,
    session: SessionDependency,
) -> Response:
    """Give up a push token.

    Answers 204 whether or not a row was there. The client is telling us
    what it wants to be true rather than asking a question, and a device
    that was never registered is already in the state it is asking for.
    A 404 would only tempt a sign-out flow into treating a no-op as a
    failure it has to report. A token held by another account is the same
    no-op, which is also what stops this being a way to silence somebody
    else's phone.
    """
    devices = DeviceTokenRepository(session)
    forgotten = await devices.forget(token, user_id=current_user.id)
    await session.commit()
    if forgotten:
        logger.info("device_forgotten", user_id=str(current_user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
