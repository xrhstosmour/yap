"""Tests for pagination header utilities.

Tests `build_pagination_headers` (RFC 5988 Link headers, X-Total-Count,
edge cases, clamping, and Host-header injection prevention) and the
`PaginatedResponse` wrapper class.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from starlette.requests import Request

from app.core.pagination import MAX_PAGE_SIZE
from app.core.pagination import PaginatedResponse
from app.core.pagination import build_pagination_headers

# Helpers


def mock_request(
    path: str = "/api/v1/items",
    query_params: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock Starlette Request with a known path and query parameters."""
    mock = MagicMock(spec=Request)
    mock.url.path = path
    mock.query_params.multi_items.return_value = [
        (k, v) for k, v in (query_params or {}).items()
    ]
    return mock


def _parse_link_header(header: str) -> dict[str, str]:
    """Parse an RFC 5988 Link header value into a dict of {rel: url}."""
    links: dict[str, str] = {}
    for part in header.split(", "):
        # Each part looks like: <$URL>; rel="$REL"
        url_part, rel_part = part.split("; ", 1)
        url = url_part[1:-1]  # strip angle brackets
        rel = rel_part.split("=")[1].strip('"')
        links[rel] = url
    return links


# build_pagination_headers


class TestBuildPaginationHeaders:
    """Tests for the `build_pagination_headers` function."""

    def test_zero_total_returns_only_x_total_count(self) -> None:
        """When total is 0, return only the X-Total-Count header, no Link."""
        request = mock_request()
        headers = build_pagination_headers(request, total=0, skip=0, limit=50)

        assert headers == {"X-Total-Count": "0"}

    def test_single_page_no_prev_next_links(self) -> None:
        """When total <= limit, only first/last links appear (no prev/next)."""
        request = mock_request()
        headers = build_pagination_headers(request, total=25, skip=0, limit=50)

        links = _parse_link_header(headers["Link"])
        assert "first" in links
        assert "last" in links
        assert "prev" not in links
        assert "next" not in links

    def test_multiple_pages_includes_prev_next(self) -> None:
        """Second page of a multi-page result should have both prev and next."""
        request = mock_request()
        headers = build_pagination_headers(request, total=150, skip=50, limit=50)

        links = _parse_link_header(headers["Link"])
        assert "prev" in links
        assert "next" in links

    def test_first_page_no_prev_link(self) -> None:
        """The very first page should not include a prev relation."""
        request = mock_request()
        headers = build_pagination_headers(request, total=100, skip=0, limit=25)

        links = _parse_link_header(headers["Link"])
        assert "prev" not in links
        assert "next" in links
        assert "first" in links
        assert "last" in links

    def test_last_page_no_next_link(self) -> None:
        """The last page should not include a next relation."""
        request = mock_request()
        headers = build_pagination_headers(request, total=100, skip=75, limit=25)

        links = _parse_link_header(headers["Link"])
        assert "prev" in links
        assert "next" not in links
        assert "first" in links
        assert "last" in links

    def test_negative_skip_clamped_to_zero(self) -> None:
        """Negative skip values are clamped to 0 before building links."""
        request = mock_request()
        headers = build_pagination_headers(request, total=100, skip=-5, limit=25)

        links = _parse_link_header(headers["Link"])
        # Treated as first page, no prev, but next exists.
        assert "prev" not in links
        assert "next" in links

    def test_oversized_limit_clamped_to_max_page_size(self) -> None:
        """Limit values greater than MAX_PAGE_SIZE are clamped down."""
        request = mock_request()
        headers = build_pagination_headers(request, total=200, skip=0, limit=999)

        links = _parse_link_header(headers["Link"])
        # last link should use the clamped limit = MAX_PAGE_SIZE
        last_url = links["last"]
        assert f"limit={MAX_PAGE_SIZE}" in last_url

    def test_urls_are_path_only_no_host_injection(self) -> None:
        """Link URLs must be path-only, no scheme or host to prevent injection."""
        request = mock_request(path="/api/v1/items")
        headers = build_pagination_headers(request, total=50, skip=0, limit=25)

        links = _parse_link_header(headers["Link"])
        for rel, url in links.items():
            assert url.startswith("/"), (
                f"Link rel={rel} URL should start with '/', got: {url}"
            )
            assert "://" not in url, (
                f"Link rel={rel} URL must not contain scheme/host, got: {url}"
            )

    def test_preserves_existing_query_params(self) -> None:
        """Query parameters other than skip/limit are preserved in link URLs."""
        request = mock_request(
            path="/api/v1/items",
            query_params={
                "category": "books",
                "sort": "title",
                "skip": "10",
                "limit": "20",
            },
        )

        headers = build_pagination_headers(request, total=100, skip=10, limit=20)
        links = _parse_link_header(headers["Link"])

        for url in links.values():
            assert "category=books" in url
            assert "sort=title" in url

    def test_skip_exceeds_total_clamped_to_last_page(self) -> None:
        """An out-of-range skip is clamped to the last page so links remain valid."""
        request = mock_request()
        headers = build_pagination_headers(request, total=60, skip=500, limit=20)

        links = _parse_link_header(headers["Link"])
        # Should behave like the last page: no next link, prev present.
        assert "next" not in links
        assert "prev" in links
        assert "last" in links

    def test_evenly_divisible_total_last_page_links(self) -> None:
        """Last page links are correct when total is evenly divisible by limit."""
        request = mock_request()
        # total=100, limit=25 → last_skip = ((100-1)//25)*25 = 75
        headers = build_pagination_headers(request, total=100, skip=75, limit=25)
        links = _parse_link_header(headers["Link"])
        assert "next" not in links
        # prev should link to skip=50 (since current skip is 75)
        prev_url = links["prev"]
        assert "skip=50" in prev_url


# PaginatedResponse


class TestPaginatedResponse:
    """Tests for the `PaginatedResponse` convenience class."""

    def test_sets_correct_status_code_and_headers(self) -> None:
        """PaginatedResponse should default to 200 and inject pagination headers."""
        request = mock_request()
        response = PaginatedResponse(
            content={"items": [1, 2, 3]},
            total=3,
            skip=0,
            limit=50,
            request=request,
        )

        assert response.status_code == 200
        assert response.headers["X-Total-Count"] == "3"
        assert "Link" in response.headers

    def test_merges_pagination_headers_with_custom_headers(self) -> None:
        """Custom headers passed via kwargs are merged with pagination headers."""
        request = mock_request()
        response = PaginatedResponse(
            content={"items": []},
            total=0,
            skip=0,
            limit=50,
            request=request,
            headers={"X-Custom": "custom-value"},
        )

        assert response.headers["X-Custom"] == "custom-value"
        assert response.headers["X-Total-Count"] == "0"

    def test_pagination_headers_override_duplicate_custom_headers(self) -> None:
        """Pagination headers take precedence over custom headers with the same name."""
        request = mock_request()
        response = PaginatedResponse(
            content={"items": [42]},
            total=1,
            skip=0,
            limit=50,
            request=request,
            headers={"X-Total-Count": "wrong-value"},
        )

        # Pagination headers should win: the correct total is 1.
        assert response.headers["X-Total-Count"] == "1"

    def test_status_code_can_be_overridden(self) -> None:
        """The status code can be set via kwargs (e.g., status_code=201)."""
        request = mock_request()
        response = PaginatedResponse(
            content={"items": []},
            total=0,
            skip=0,
            limit=50,
            request=request,
            status_code=201,
        )

        assert response.status_code == 201

    def test_response_body_is_json_serializable(self) -> None:
        """Content is passed through jsonable_encoder so it renders correctly."""
        from datetime import datetime

        request = mock_request()
        response = PaginatedResponse(
            content={"created_at": datetime(2025, 1, 1)},
            total=1,
            skip=0,
            limit=50,
            request=request,
        )

        # The body should decode as JSON without errors.
        import json

        body = response.body
        data = json.loads(body)
        assert "created_at" in data
