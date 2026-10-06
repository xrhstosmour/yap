"""Tests the keyset pagination helpers.

Offset pagination on a list that is written to while it is read re-serves a
row and drops another every time a row is inserted above the window. These
helpers exist so a page names a row instead of a position, and the tests
below pin the three properties that make that work: a cursor round-trips, it
stays opaque, and a client cannot forge one into something the decoder
accepts as a sort key.
"""

from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import UTC
from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from starlette.requests import Request

from app.core.pagination import CURSOR_HEADERS_SPEC
from app.core.pagination import CursorResponse
from app.core.pagination import build_cursor_headers
from app.core.pagination import decode_cursor
from app.core.pagination import encode_cursor

MOMENT = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
IDENTIFIER = UUID("00000000-0000-4000-8000-000000000001")


def mock_request(
    path: str = "/api/v1/items",
    query_params: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock request with a known path and query parameters.

    Args:
        path: The request path.
        query_params: The query parameters to expose.

    Returns:
        A mock standing in for a Starlette request.
    """
    mock = MagicMock(spec=Request)
    mock.url.path = path
    mock.query_params.multi_items.return_value = list((query_params or {}).items())
    return mock


def _link(headers: dict[str, str]) -> str:
    """Pull the URL out of a `Link` header.

    Args:
        headers: The headers to read.

    Returns:
        The URL, without its angle brackets.
    """
    return headers["Link"].split("; ")[0][1:-1]


class TestCursorRoundTrip:
    """A cursor has to come back as the key that went in."""

    def test_a_timestamp_and_identifier_round_trip(self) -> None:
        """The usual key: a sort column plus the tie-breaking id."""
        assert decode_cursor(encode_cursor([MOMENT, IDENTIFIER])) == [
            MOMENT.isoformat(),
            str(IDENTIFIER),
        ]

    def test_order_is_preserved(self) -> None:
        """The key is positional, so a reordering would silently mispage."""
        assert decode_cursor(encode_cursor(["first", "second"])) == ["first", "second"]

    def test_a_boolean_component_survives(self) -> None:
        """A grouped sort, friends first, carries a boolean ahead of the date."""
        assert decode_cursor(encode_cursor([True, MOMENT, IDENTIFIER]))[0] is True

    def test_the_cursor_is_url_safe(self) -> None:
        """It travels in a query string, so it cannot need escaping."""
        cursor = encode_cursor([MOMENT, IDENTIFIER])
        assert "=" not in cursor
        assert "+" not in cursor
        assert "/" not in cursor

    def test_the_cursor_does_not_spell_out_the_key(self) -> None:
        """Opaque on purpose, so the key can change without breaking clients."""
        assert str(IDENTIFIER) not in encode_cursor([MOMENT, IDENTIFIER])


class TestCursorRejection:
    """A cursor arrives from the client, so it is an untrusted input."""

    @pytest.mark.parametrize(
        "cursor",
        [
            "not base64 at all!",
            "",
            encode_cursor([MOMENT])[:-4],
        ],
    )
    def test_a_malformed_cursor_is_refused(self, cursor: str) -> None:
        """Refusing beats returning page one, which reads as data loss."""
        with pytest.raises(ValueError, match="malformed cursor"):
            decode_cursor(cursor)

    def test_valid_json_that_is_not_a_key_is_refused(self) -> None:
        """Valid base64 holding valid JSON is still not a sort key.

        Built deliberately rather than by corrupting a real cursor, so the
        test cannot pass merely because the bytes stopped being base64.
        """
        forged = urlsafe_b64encode(b'{"limit": 1000}').decode().rstrip("=")
        with pytest.raises(ValueError, match="malformed cursor"):
            decode_cursor(forged)


class TestCursorHeaders:
    """The `Link` header is how a client finds the next page."""

    def test_the_last_page_carries_no_link(self) -> None:
        """A `next` link at the end would loop a client forever."""
        assert build_cursor_headers(mock_request(), None, 20) == {}

    def test_no_total_count_is_promised(self) -> None:
        """A total is the work this scheme exists to avoid."""
        headers = build_cursor_headers(mock_request(), encode_cursor([MOMENT]), 20)
        assert "X-Total-Count" not in headers
        assert "last" not in headers["Link"]

    def test_the_link_carries_the_cursor_and_limit(self) -> None:
        """Following the link has to reproduce the same page size."""
        cursor = encode_cursor([MOMENT, IDENTIFIER])
        url = _link(build_cursor_headers(mock_request(), cursor, 20))
        assert f"cursor={cursor}" in url
        assert "limit=20" in url

    def test_other_query_parameters_survive(self) -> None:
        """A filtered feed has to stay filtered on page two."""
        url = _link(
            build_cursor_headers(
                mock_request(query_params={"only_reviews": "true", "cursor": "stale"}),
                encode_cursor([MOMENT]),
                20,
            )
        )
        assert "only_reviews=true" in url
        assert "cursor=stale" not in url

    def test_the_url_is_path_only(self) -> None:
        """A Host read from the request would carry an injected one."""
        url = _link(build_cursor_headers(mock_request(), encode_cursor([MOMENT]), 20))
        assert url.startswith("/api/v1/items?")

    def test_the_page_size_is_capped(self) -> None:
        """The link must not invite a page the endpoint would refuse."""
        url = _link(build_cursor_headers(mock_request(), encode_cursor([MOMENT]), 5000))
        assert "limit=100" in url


class TestCursorResponse:
    """The wrapper is what endpoints actually return."""

    def test_the_header_is_attached(self) -> None:
        """An endpoint should not have to set the header itself."""
        cursor = encode_cursor([MOMENT, IDENTIFIER])
        response = CursorResponse(
            content={"data": []}, next_cursor=cursor, limit=20, request=mock_request()
        )
        assert cursor in response.headers["Link"]

    def test_the_last_page_has_no_link_header(self) -> None:
        """Absence is how a client knows it has reached the end."""
        response = CursorResponse(
            content={"data": []}, next_cursor=None, limit=20, request=mock_request()
        )
        assert "Link" not in response.headers

    def test_the_specification_documents_only_next(self) -> None:
        """The OpenAPI spec should not promise headers that never arrive."""
        headers = CURSOR_HEADERS_SPEC[200]["headers"]
        assert set(headers) == {"Link"}
