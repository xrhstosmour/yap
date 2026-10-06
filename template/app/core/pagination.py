"""Pagination header utilities.

Two schemes live here. Offset pagination, `build_pagination_headers` and
`PaginatedResponse`, carries `X-Total-Count` and the full set of RFC 5988
relations, and suits a list that is not being written to while it is read.

Keyset pagination, `encode_cursor` through `CursorResponse`, suits one that
is. An offset page re-serves a row and drops another whenever a row is
inserted above the window, and its total has to materialise the whole
matching set before the page can be honoured. A cursor names a row instead
of a position, so neither happens.
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode
from base64 import urlsafe_b64encode
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_PAGE_SIZE = 100


def build_pagination_headers(
    request: Request,
    total: int,
    skip: int,
    limit: int,
) -> dict[str, str]:
    """Build `X-Total-Count` and `Link` headers for a paginated response.

    Constructs the `Link` header according to RFC 5988 with `first`,
    `last`, and optional `prev` / `next` relations. All URLs
    preserve the original query parameters and only override `skip`
    and `limit`.

    Args:
        request: The current HTTP request (used to build relative URLs).
        total: Total number of items across all pages.
        skip: Offset used for the current page.
        limit: Page size used for the current page.

    Returns:
        Dict with `X-Total-Count` and (when applicable) `Link` headers.
    """
    headers: dict[str, str] = {"X-Total-Count": str(total)}

    if total == 0:
        return headers

    limit = min(limit, MAX_PAGE_SIZE)
    skip = max(0, skip)

    # Use path-only URLs to prevent Host-header injection in the Link header.
    base_path = request.url.path

    # Preserve all existing query parameters including multi-value ones;
    # override skip/limit.
    base_params = [
        (k, v)
        for k, v in request.query_params.multi_items()
        if k not in ("skip", "limit")
    ]

    def _url(page_skip: int) -> str:
        parameters = base_params + [("skip", str(page_skip)), ("limit", str(limit))]
        return f"{base_path}?{urlencode(parameters)}"

    last_skip = max(0, ((total - 1) // limit) * limit) if total > 0 else 0

    # Clamp an out-of-range skip so prev/next links stay within valid bounds.
    effective_skip = min(skip, last_skip)

    links: list[str] = [
        f'<{_url(0)}>; rel="first"',
        f'<{_url(last_skip)}>; rel="last"',
    ]

    if effective_skip > 0:
        prev_skip = max(0, effective_skip - limit)
        links.append(f'<{_url(prev_skip)}>; rel="prev"')

    if effective_skip + limit < total:
        links.append(f'<{_url(effective_skip + limit)}>; rel="next"')

    headers["Link"] = ", ".join(links)
    return headers


class PaginatedResponse(JSONResponse):
    """JSON response with pagination headers.

    Wraps a paginated response and automatically adds `X-Total-Count`
    and RFC 5988 `Link` headers. Use as the return value from list
    endpoints instead of manually injecting `response.headers`.

    Example::

        return PaginatedResponse(
            content=UserListResponse(...).model_dump(),
            total=total,
            skip=parameters.skip,
            limit=parameters.limit,
            request=request,
        )
    """

    def __init__(
        self,
        content: Any,  # noqa: ANN401
        total: int,
        skip: int,
        limit: int,
        request: Request,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        pagination_headers = build_pagination_headers(request, total, skip, limit)
        headers = dict(kwargs.pop("headers", {}))
        headers.update(pagination_headers)
        super().__init__(content=jsonable_encoder(content), headers=headers, **kwargs)


PAGINATION_HEADERS_SPEC = {
    200: {
        "headers": {
            "X-Total-Count": {
                "schema": {"type": "integer"},
                "description": "Total number of items matching the query",
            },
            "Link": {
                "schema": {"type": "string"},
                "description": "RFC 5988 pagination links (first, last, prev, next)",
            },
        }
    }
}


def encode_cursor(values: Sequence[Any]) -> str:
    """Pack a sort key into an opaque cursor.

    The cursor is opaque on purpose. A client that parses it starts
    depending on the sort key, and then the key cannot change without
    breaking installed clients.

    Args:
        values: The sort key, in the same order the query sorts by.

    Returns:
        A URL-safe string naming exactly one row.
    """
    payload = json.dumps(
        [_as_primitive(value) for value in values], separators=(",", ":")
    )
    return urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[Any]:
    """Unpack a cursor produced by `encode_cursor`.

    Args:
        cursor: The opaque cursor from a previous page.

    Returns:
        The sort key values, in order.

    Raises:
        ValueError: If the cursor is not one this application issued.
    """
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        values = json.loads(urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("malformed cursor") from error
    if not isinstance(values, list):
        raise ValueError("malformed cursor")
    return values


def build_cursor_headers(
    request: Request,
    next_cursor: str | None,
    limit: int,
) -> dict[str, str]:
    """Build the `Link` header for a keyset-paginated response.

    There is no `X-Total-Count` and no `last` relation. Both need a count of
    the whole matching set, which is the work keyset pagination exists to
    avoid, and a total is stale by the time it is read anyway.

    Args:
        request: The current request, used to build a relative URL.
        next_cursor: The cursor for the following page, or None at the end.
        limit: The page size used for this page.

    Returns:
        A `Link` header when another page exists, otherwise no headers.
    """
    if next_cursor is None:
        return {}

    limit = min(limit, MAX_PAGE_SIZE)
    base_parameters = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in ("cursor", "limit")
    ]
    parameters = base_parameters + [("cursor", next_cursor), ("limit", str(limit))]
    # Path-only, so the Link header cannot carry an injected Host.
    url = f"{request.url.path}?{urlencode(parameters)}"
    return {"Link": f'<{url}>; rel="next"'}


class CursorResponse(JSONResponse):
    """JSON response with a keyset pagination `Link` header."""

    def __init__(
        self,
        content: Any,  # noqa: ANN401
        next_cursor: str | None,
        limit: int,
        request: Request,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        headers = dict(kwargs.pop("headers", {}))
        headers.update(build_cursor_headers(request, next_cursor, limit))
        super().__init__(content=jsonable_encoder(content), headers=headers, **kwargs)


CURSOR_HEADERS_SPEC = {
    200: {
        "headers": {
            "Link": {
                "schema": {"type": "string"},
                "description": "RFC 5988 `next` link, absent on the last page",
            },
        }
    }
}


def _as_primitive(value: Any) -> Any:  # noqa: ANN401
    """Render a sort key component as something JSON can carry.

    Args:
        value: One component of a sort key.

    Returns:
        The value as a JSON primitive.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value
