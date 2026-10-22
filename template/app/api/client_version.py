"""Turn away client builds older than the oldest one the API still serves.

A mobile build lives in people's pockets for months after the API it was
written against has moved on. Rather than failing on whichever request
first changed shape, a build sends its own version in `X-Client-Version`
and is answered 426 when it is below `MINIMUM_CLIENT_VERSION`, which the
client turns into an "update the app" screen.

A request with no header, or one that does not parse, is let through: a
browser, a script or a build from before the header existed cannot be
told apart from each other, and refusing all of them would lock out more
than it protects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

if TYPE_CHECKING:
    from starlette.types import ASGIApp
    from starlette.types import Receive
    from starlette.types import Scope
    from starlette.types import Send

CLIENT_VERSION_HEADER = "X-Client-Version"

# `major.minor.patch`, with an optional `+build` or `-pre` suffix that is
# ignored, which is the shape both store version strings and `pubspec`
# versions take.
_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[+-].*)?$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Read `major.minor.patch` out of `text`, or None if it is not one."""
    match = _VERSION_PATTERN.match(text.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


class ClientVersionMiddleware:
    """Answer 426 to a client that says it is older than `minimum`."""

    def __init__(self, app: ASGIApp, minimum: str, path_prefix: str) -> None:
        """Parse the minimum once, so a typo fails the start, not a request.

        Args:
            app: The application this wraps.
            minimum: The oldest version still served, empty to serve all.
            path_prefix: Only paths under it are checked, so health checks
                and the admin pages never answer 426.

        Raises:
            ValueError: If `minimum` is set and is not `major.minor.patch`.
        """
        self.app = app
        self.minimum_text = minimum.strip()
        self.minimum = parse_version(self.minimum_text) if self.minimum_text else None
        if self.minimum_text and self.minimum is None:
            message = f"MINIMUM_CLIENT_VERSION is not major.minor.patch: {minimum!r}"
            raise ValueError(message)
        self.path_prefix = path_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse an old client before any work is done for it."""
        if (
            scope["type"] == "http"
            and self.minimum is not None
            and scope["path"].startswith(self.path_prefix)
        ):
            sent = parse_version(Headers(scope=scope).get(CLIENT_VERSION_HEADER, ""))
            if sent is not None and sent < self.minimum:
                response = JSONResponse(
                    status_code=426,
                    content={
                        "detail": "This version of the app is no longer supported.",
                        "minimum_client_version": self.minimum_text,
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
