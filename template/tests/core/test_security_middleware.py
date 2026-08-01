"""Unit tests for `SecurityHeadersMiddleware`."""

import re

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.core.security_middleware import AI_BOT_PATTERNS
from app.core.security_middleware import SecurityHeadersMiddleware


def _create_app() -> FastAPI:
    """Create a minimal FastAPI app with SecurityHeadersMiddleware."""
    app = FastAPI()

    @app.get("/")
    async def root() -> PlainTextResponse:
        return PlainTextResponse("OK")

    @app.get("/try")
    async def docs_swagger() -> PlainTextResponse:
        return PlainTextResponse("OK")

    @app.get("/documentation")
    async def docs_redoc() -> PlainTextResponse:
        return PlainTextResponse("OK")

    app.add_middleware(SecurityHeadersMiddleware)
    return app


class TestSecurityHeaders:
    """Tests that security headers are added to every response."""

    def test_content_type_options_header(self) -> None:
        """X-Content-Type-Options should be nosniff."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_frame_options_header(self) -> None:
        """X-Frame-Options should be DENY."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_content_security_policy_header(self) -> None:
        """CSP header for a non-docs route should not allow the jsdelivr CDN."""
        client = TestClient(_create_app())
        response = client.get("/")
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "img-src 'self' data:" in csp
        assert "font-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "script-src" not in csp
        assert "style-src" not in csp
        assert "cdn.jsdelivr.net" not in csp

    @pytest.mark.parametrize("path", ["/try", "/documentation"])
    def test_content_security_policy_header_on_docs_paths(self, path: str) -> None:
        """CSP header for the Swagger/Redoc doc pages should allow the jsdelivr CDN."""
        client = TestClient(_create_app())
        response = client.get(path)
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "script-src 'self' cdn.jsdelivr.net" in csp
        assert "style-src 'self' cdn.jsdelivr.net" in csp
        assert "font-src 'self' data: cdn.jsdelivr.net" in csp
        assert "frame-ancestors 'none'" in csp

    def test_referrer_policy_header(self) -> None:
        """Referrer-Policy should be strict-origin-when-cross-origin."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy_header(self) -> None:
        """Permissions-Policy should disable sensitive features."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["Permissions-Policy"] == (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )

    def test_robots_tag_header(self) -> None:
        """X-Robots-Tag should tell bots not to index."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"

    def test_dns_prefetch_control_header(self) -> None:
        """X-DNS-Prefetch-Control should be off."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["X-DNS-Prefetch-Control"] == "off"

    def test_download_options_header(self) -> None:
        """X-Download-Options should be noopen."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["X-Download-Options"] == "noopen"

    def test_permitted_cross_domain_policies_header(self) -> None:
        """X-Permitted-Cross-Domain-Policies should be none."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"


class TestHSTSHeader:
    """Tests for Strict-Transport-Security header behavior."""

    def test_hsts_added_when_scheme_is_https(self) -> None:
        """HSTS should be present when request scheme is https."""
        client = TestClient(_create_app(), base_url="https://testserver")
        response = client.get("/")
        assert "Strict-Transport-Security" in response.headers
        expected = "max-age=31536000; includeSubDomains; preload"
        assert response.headers["Strict-Transport-Security"] == expected

    def test_hsts_added_when_forwarded_proto_is_https(self) -> None:
        """HSTS should be present when X-Forwarded-Proto is https."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "Strict-Transport-Security" in response.headers
        expected = "max-age=31536000; includeSubDomains; preload"
        assert response.headers["Strict-Transport-Security"] == expected

    def test_hsts_not_added_for_plain_http(self) -> None:
        """HSTS should NOT be present for plain http with no X-Forwarded-Proto."""
        client = TestClient(_create_app())
        response = client.get("/")
        assert "Strict-Transport-Security" not in response.headers

    def test_hsts_not_added_for_http_forwarded_proto(self) -> None:
        """HSTS should NOT be present when X-Forwarded-Proto is http."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={"X-Forwarded-Proto": "http"},
        )
        assert "Strict-Transport-Security" not in response.headers


class TestAIBotBlocking:
    """Tests that known AI bot/crawler user agents are blocked with 403."""

    @pytest.mark.parametrize(
        "user_agent",
        [
            "GPTBot/1.0",
            "ChatGPT-User",
            "anthropic/1.0",
            "Claude-Web",
            "ClaudeBot/1.0",
            "CCBot/2.0",
            "Google-Extended",
            "FacebookBot",
            "Bytespider",
            "Amazonbot",
            "Diffbot",
            "ImagesiftBot",
            "Omgilibot",
            "PerplexityBot/1.0",
            "YouBot",
            "cohere/1.0",
            "AhrefsBot/7.0",
            "SemrushBot",
            "DotBot/1.2",
            "PetalBot",
            "Barkrowler",
            "BLEXBot/1.0",
            "DataForSeoBot/1.0",
            "Magpie-Crawler",
            "TurnitinBot",
            "trendictionbot",
        ],
    )
    def test_ai_bot_user_agent_blocked(self, user_agent: str) -> None:
        """Each known AI bot user agent should return 403 Forbidden."""
        client = TestClient(_create_app())
        response = client.get("/", headers={"User-Agent": user_agent})
        assert response.status_code == 403
        assert "Forbidden" in response.text

    def test_ai_bot_pattern_matches_substring(self) -> None:
        """Patterns should match even when the bot name is part of a longer UA."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; GPTBot/2.0; +https://openai.com/gptbot)"
            },
        )
        assert response.status_code == 403

    def test_ai_bot_case_insensitive(self) -> None:
        """Blocking should be case-insensitive."""
        client = TestClient(_create_app())
        response = client.get("/", headers={"User-Agent": "gptbot/1.0"})
        assert response.status_code == 403


class TestNormalUserAgents:
    """Tests that normal/regular user agents pass through."""

    def test_chrome_user_agent_passes(self) -> None:
        """Chrome browser user agent should return 200."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_firefox_user_agent_passes(self) -> None:
        """Firefox browser user agent should return 200."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) "
                    "Gecko/20100101 Firefox/120.0"
                )
            },
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_safari_user_agent_passes(self) -> None:
        """Safari browser user agent should return 200."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.1 Safari/605.1.15"
                )
            },
        )
        assert response.status_code == 200
        assert response.text == "OK"

    def test_curl_user_agent_passes(self) -> None:
        """curl user agent should return 200."""
        client = TestClient(_create_app())
        response = client.get("/", headers={"User-Agent": "curl/8.4.0"})
        assert response.status_code == 200
        assert response.text == "OK"

    def test_python_requests_user_agent_passes(self) -> None:
        """Python requests library user agent should return 200."""
        client = TestClient(_create_app())
        response = client.get("/", headers={"User-Agent": "python-requests/2.31.0"})
        assert response.status_code == 200
        assert response.text == "OK"

    def test_empty_user_agent_passes(self) -> None:
        """Request with no User-Agent header should return 200."""
        client = TestClient(_create_app())
        response = client.get("/")
        # TestClient may set a default; remove it explicitly
        client.headers.pop("User-Agent", None)
        # Use separate client without headers
        app = _create_app()
        fresh_client = TestClient(app)
        # Send request without User-Agent header
        response = fresh_client.get("/", headers={})
        assert response.status_code == 200
        assert response.text == "OK"

    def test_headers_still_applied_for_normal_agents(self) -> None:
        """Normal agents should still receive security headers in the response."""
        client = TestClient(_create_app())
        response = client.get(
            "/",
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
            },
        )
        # Googlebot is a search engine bot, not in AI_BOT_PATTERNS — it should pass
        assert response.status_code == 200
        # Security headers should still be present
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"


class TestAI_BOT_PATTERNS:
    """Tests that AI_BOT_PATTERNS list is well-formed."""

    def test_all_patterns_are_compiled_regex(self) -> None:
        """Every entry in AI_BOT_PATTERNS should be a compiled regex pattern."""
        for pattern in AI_BOT_PATTERNS:
            assert isinstance(pattern, re.Pattern)

    def test_all_patterns_are_case_insensitive(self) -> None:
        """Every compiled pattern should be case-insensitive."""
        for pattern in AI_BOT_PATTERNS:
            assert pattern.flags & re.IGNORECASE
