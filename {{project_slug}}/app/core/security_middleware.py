"""Security middleware providing HTTP security headers and bot blocking.

Adds security headers (HSTS, X-Frame-Options, X-Content-Type-Options, etc.)
and blocks common AI bot/crawler user agents from scraping the API.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import PlainTextResponse

AI_BOT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"GPTBot",
        r"ChatGPT",
        r"anthropic",
        r"Claude-Web",
        r"ClaudeBot",
        r"CCBot",
        r"Google-Extended",
        r"FacebookBot",
        r"Bytespider",
        r"Amazonbot",
        r"Diffbot",
        r"ImagesiftBot",
        r"Omgilibot",
        r"PerplexityBot",
        r"YouBot",
        r"cohere",
        r"AhrefsBot",  # SEO crawler
        r"SemrushBot",  # SEO crawler
        r"DotBot",  # SEO crawler
        r"PetalBot",  # Huawei
        r"Barkrowler",
        r"BLEXBot",
        r"DataForSeoBot",
        r"Magpie-Crawler",
        r"TurnitinBot",
        r"trendictionbot",
    ]
]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security-focused HTTP headers and blocks AI bot crawlers."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Add security headers and check user agent.

        Args:
            request: Incoming request
            call_next: Next middleware or handler

        Returns:
            Response with security headers
        """
        user_agent = request.headers.get("User-Agent", "")

        for pattern in AI_BOT_PATTERNS:
            if pattern.search(user_agent):
                return PlainTextResponse(
                    "Forbidden",
                    status_code=403,
                )

        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["X-DNS-Prefetch-Control"] = "off"

        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )

        return response
