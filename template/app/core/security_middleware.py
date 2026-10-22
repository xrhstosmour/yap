"""Security middleware providing HTTP security headers and bot blocking.

Adds security headers (HSTS, X-Frame-Options, X-Content-Type-Options, etc.)
and blocks common AI bot/crawler user agents from scraping the API.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp
    from starlette.types import Message
    from starlette.types import Receive
    from starlette.types import Scope
    from starlette.types import Send

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

# Both docs pages ship an inline block that this policy has to allow, or
# the page renders blank: FastAPI's Swagger HTML bootstraps the UI from an
# inline `<script>` calling `SwaggerUIBundle(...)`, and its ReDoc HTML
# carries an inline `<style>`. Neither is under this project's control, so
# a hash would break on the next FastAPI upgrade. `'unsafe-inline'` is
# scoped to these two paths, and since PR #168 those paths only exist when
# ENVIRONMENT is `local`, so no deployed environment ever sends this header.
#
# The rest are third-party origins the two pages reference directly:
# fastapi.tiangolo.com for both favicons, and Google Fonts for ReDoc's
# stylesheet (fonts.googleapis.com) and the font files it in turn pulls
# (fonts.gstatic.com). ReDoc also builds its highlighting worker from a
# blob URL, hence `worker-src blob:`.
_DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net fonts.googleapis.com; "
    "img-src 'self' data: fastapi.tiangolo.com; "
    "font-src 'self' data: cdn.jsdelivr.net fonts.gstatic.com; "
    "worker-src blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'"
)


# Server-rendered HTML with one stylesheet and no JavaScript, so it needs
# `style-src 'self'` and nothing else. Its own policy rather than a wider
# default, which every API response would then carry.
ADMIN_PATH_PREFIX = "/admin"

_ADMIN_CSP = (
    "default-src 'none'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


def _policy_for(path: str) -> str:
    """The content security policy one path gets."""
    if path in DOCS_PATHS:
        return _DOCS_CSP
    if path == ADMIN_PATH_PREFIX or path.startswith(f"{ADMIN_PATH_PREFIX}/"):
        return _ADMIN_CSP
    return _DEFAULT_CSP


class SecurityHeadersMiddleware:
    """Adds security-focused HTTP headers and blocks AI bot crawlers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add security headers and check user agent.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel, whose response gets the headers.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        user_agent = request_headers.get("User-Agent", "")

        for pattern in AI_BOT_PATTERNS:
            if pattern.search(user_agent):
                response = PlainTextResponse("Forbidden", status_code=403)
                await response(scope, receive, send)
                return

        policy = _policy_for(scope["path"])
        is_https = (
            scope.get("scheme") == "https"
            or request_headers.get("X-Forwarded-Proto") == "https"
        )

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Content-Security-Policy"] = policy
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), interest-cohort=()"
                )
                headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
                headers["X-DNS-Prefetch-Control"] = "off"
                headers["X-Download-Options"] = "noopen"
                headers["X-Permitted-Cross-Domain-Policies"] = "none"
                if is_https:
                    headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains; preload"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
