"""Caps on how many WebSockets one worker process holds open.

A socket costs a task and a file descriptor for as long as it lives, and any
signed-in user can mint tickets, so without a cap one account can fill a
worker. The counts are per process, not per deployment: with several
workers the real ceiling is the cap times the number of workers.

Platform admin metrics sockets have a small cap of their own and do not count
against the total, so ordinary users filling the total cannot lock the admins
out of the one stream they use to see what is wrong.
"""

from __future__ import annotations

from uuid import UUID

from app.core.settings import settings


class ConnectionLimiter:
    """Counts open sockets per user and in total for this process."""

    def __init__(self) -> None:
        """Start with nothing open."""
        self._per_user: dict[UUID, int] = {}
        self._total = 0
        self._admin_total = 0

    def acquire(self, user_id: UUID, *, admin: bool = False) -> bool:
        """Take a slot for a new socket, if the caps allow it.

        Args:
            user_id: The user opening the socket.
            admin: Whether this is a platform admin metrics socket, which
                counts against `WS_MAX_ADMIN_CONNECTIONS` instead of the total.

        Returns:
            True when a slot was taken, which `release` must give back with
            the same `admin` value.
        """
        if admin:
            if self._admin_total >= settings.WS_MAX_ADMIN_CONNECTIONS:
                return False
        elif self._total >= settings.WS_MAX_CONNECTIONS_TOTAL:
            return False
        if self._per_user.get(user_id, 0) >= settings.WS_MAX_CONNECTIONS_PER_USER:
            return False
        self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
        if admin:
            self._admin_total += 1
        else:
            self._total += 1
        return True

    def release(self, user_id: UUID, *, admin: bool = False) -> None:
        """Give back a slot taken by `acquire`.

        Args:
            user_id: The user whose socket closed.
            admin: The value passed to `acquire`.
        """
        remaining = self._per_user.get(user_id, 0) - 1
        if remaining > 0:
            self._per_user[user_id] = remaining
        else:
            self._per_user.pop(user_id, None)
        if admin:
            self._admin_total = max(self._admin_total - 1, 0)
        else:
            self._total = max(self._total - 1, 0)


connection_limiter = ConnectionLimiter()
