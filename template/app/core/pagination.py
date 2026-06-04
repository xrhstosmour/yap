"""Pagination header utilities.

This module provides helpers for building standard HTTP pagination
headers (``X-Total-Count`` and RFC 5988 ``Link``) that complement
the JSON pagination body already returned by list endpoints.
"""

from __future__ import annotations

from urllib.parse import urlencode

from starlette.requests import Request


def build_pagination_headers(
    request: Request,
    total: int,
    skip: int,
    limit: int,
) -> dict[str, str]:
    """Build ``X-Total-Count`` and ``Link`` headers for a paginated response.

    Constructs the ``Link`` header according to RFC 5988 with ``first``,
    ``last``, and optional ``prev`` / ``next`` relations. All URLs
    preserve the original query parameters and only override ``skip``
    and ``limit``.

    Args:
        request: The current HTTP request (used to build relative URLs).
        total: Total number of items across all pages.
        skip: Offset used for the current page.
        limit: Page size used for the current page.

    Returns:
        Dict with ``X-Total-Count`` and (when applicable) ``Link`` headers.
    """
    headers: dict[str, str] = {"X-Total-Count": str(total)}

    if total == 0:
        return headers

    # Use path-only URLs to prevent Host-header injection in the Link header.
    base_path = request.url.path

    # Preserve all existing query params including multi-value ones; override skip/limit.
    base_params = [
        (k, v)
        for k, v in request.query_params.multi_items()
        if k not in ("skip", "limit")
    ]

    def _url(page_skip: int) -> str:
        params = base_params + [("skip", str(page_skip)), ("limit", str(limit))]
        return f"{base_path}?{urlencode(params)}"

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
