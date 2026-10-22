"""Tests for refusing request bodies over the size limit."""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterator

from fastapi import FastAPI
from fastapi import Request
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.body_limit import RequestBodyLimitMiddleware

MAXIMUM = 1024


def _client(*, body_reading_middleware: bool = False) -> tuple[TestClient, list[str]]:
    """A bare app with the limit, recording which routes ran."""
    app = FastAPI()
    reached: list[str] = []

    @app.post("/json")
    async def json_route(payload: dict) -> dict:
        reached.append("json")
        return {"keys": len(payload)}

    @app.post("/upload")
    async def upload_route(file: UploadFile) -> dict:
        reached.append("upload")
        return {"size": len(await file.read())}

    if body_reading_middleware:

        async def read_body(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            # Like the idempotency layer, which hashes the body first.
            await request.body()
            return await call_next(request)

        app.add_middleware(BaseHTTPMiddleware, dispatch=read_body)

    app.add_middleware(RequestBodyLimitMiddleware, maximum=MAXIMUM)
    return TestClient(app), reached


def _chunks(size: int) -> Iterator[bytes]:
    """A body sent without a `Content-Length`, in 256 byte pieces."""
    for _ in range(size // 256):
        yield b"x" * 256


class TestRequestBodyLimitMiddleware:
    """Bodies over the limit are refused before a route sees them."""

    def test_a_small_body_passes(self) -> None:
        client, reached = _client()

        response = client.post("/json", json={"name": "small"})

        assert response.status_code == 200
        assert reached == ["json"]

    def test_a_declared_length_over_the_limit_is_refused_unread(self) -> None:
        client, reached = _client()

        response = client.post(
            "/upload",
            files={"file": ("big.bin", b"x" * (MAXIMUM * 2), "image/png")},
        )

        assert response.status_code == 413
        assert reached == []

    def test_an_undeclared_body_is_stopped_at_the_limit(self) -> None:
        client, reached = _client()

        response = client.post(
            "/json",
            content=_chunks(MAXIMUM * 4),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413
        assert reached == []

    def test_a_middleware_reading_the_body_gets_the_413_too(self) -> None:
        client, reached = _client(body_reading_middleware=True)

        response = client.post(
            "/json",
            content=_chunks(MAXIMUM * 4),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 413
        assert reached == []
