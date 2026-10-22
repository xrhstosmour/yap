"""Refuse request bodies over a size limit before they are read.

The file service stops an upload past `STORAGE_MAX_FILE_SIZE`, but only
after Starlette has spooled the whole multipart body to a temporary file,
and a JSON body is parsed into memory whole. Nothing in front of the app
capped either, so one request could fill the disk or the memory. This
answers 413 from the declared `Content-Length` without reading anything,
and stops a body sent without one as soon as it passes the limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException

if TYPE_CHECKING:
    from starlette.types import ASGIApp
    from starlette.types import Message
    from starlette.types import Receive
    from starlette.types import Scope
    from starlette.types import Send

TOO_LARGE = "Request body is too large."


class RequestBodyLimitMiddleware:
    """Answer 413 to a request whose body is larger than `maximum` bytes."""

    def __init__(self, app: ASGIApp, maximum: int) -> None:
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.maximum:
            await self._refuse(scope, receive, send)
            return

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum:
                    # An `HTTPException`, so a route reading its body turns
                    # it into the 413 through the usual handler.
                    raise HTTPException(status_code=413, detail=TOO_LARGE)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except HTTPException as error:
            # Raised outside a route, by a middleware reading the body.
            if error.status_code != 413 or response_started:
                raise
            await self._refuse(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": TOO_LARGE},
            status_code=413,
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)
