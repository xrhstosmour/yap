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

# Swagger UI (/try) and Redoc (/documentation) load their JS/CSS/fonts from
# this CDN. Every other response gets the stricter default with no CDN
# allowance, so `script-src`/`style-src` aren't opened up API-wide for the
# sake of two docs pages.
DOCS_PATHS: frozenset[str] = frozenset({"/try", "/documentation"})

_DEFAULT_CSP = (
    "default-src 'none'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)

_DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' cdn.jsdelivr.net; "
    "style-src 'self' cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "font-src 'self' data: cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)


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
            _DOCS_CSP if request.url.path in DOCS_PATHS else _DEFAULT_CSP
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        response.headers["X-Download-Options"] = "noopen"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        if (
            request.url.scheme == "https"
            or request.headers.get("X-Forwarded-Proto") == "https"
        ):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response
