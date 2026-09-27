"""Claiming a handle, in the one place that decides what a handle is.

Three steps that belong together: look the current holder up, refuse if
it is somebody else, then write the handle and its normalized form. A
project that grows a second way to edit a profile will otherwise grow a
second copy of them, and the half that drifts quietly is the check.
"""

from __future__ import annotations

from uuid import UUID

from app.core.normalization import normalize_name
from app.repositories.user_repository import UserRepository


class UsernameTakenError(Exception):
    """Raised when somebody else already holds the requested handle."""


async def resolve_username_change(
    users: UserRepository,
    user_id: UUID,
    username: str,
) -> dict[str, str]:
    """Return the columns that claim `username` for `user_id`.

    The conflict is checked here rather than left to the unique
    constraint, so a taken handle comes back as a clear error instead of
    an integrity failure surfacing as a 500.

    Args:
        users: Repository to look the current holder up through.
        user_id: The account claiming the handle.
        username: The requested handle, trimmed here rather than by the
            caller.

    Returns:
        The handle and its normalized form, ready to merge into an update.

    Raises:
        UsernameTakenError: If another account already holds it.
    """
    handle = username.strip()
    holder = await users.get_by_username(handle)
    if holder is not None and holder.id != user_id:
        message = "That username is taken"
        raise UsernameTakenError(message)
    return {"username": handle, "username_normalized": normalize_name(handle)}
