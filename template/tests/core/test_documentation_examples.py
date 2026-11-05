"""Tests that the interactive docs open on something a new reader can run.

The docs page is the first screen of an API with no frontend. An example full
of `"string"` or one that the endpoint refuses made the first click a failure,
and every persona of a first-run audit stopped on it.
"""

from __future__ import annotations

import pytest

from app.core.settings import settings
from app.main import create_app


class TestDocumentationDescription:
    """The text the docs page opens with."""

    def test_it_says_where_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Register, authorize, then try the rest."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")

        info = create_app().openapi()["info"]

        assert "Getting started" in info["description"]
        assert "/auth/register" in info["description"]
        assert "Authorize" in info["description"]

    def test_no_link_is_invented_when_none_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A link to a page nobody wrote is worse than none."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")
        monkeypatch.setattr(settings, "TERMS_OF_SERVICE_URL", "")
        monkeypatch.setattr(settings, "PRIVACY_POLICY_URL", "")

        info = create_app().openapi()["info"]

        assert "termsOfService" not in info
        assert "Privacy policy" not in info["description"]

    def test_configured_links_are_shown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Terms in the spec, both links in the text."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")
        monkeypatch.setattr(
            settings, "TERMS_OF_SERVICE_URL", "https://example.com/terms"
        )
        monkeypatch.setattr(
            settings, "PRIVACY_POLICY_URL", "https://example.com/privacy"
        )

        info = create_app().openapi()["info"]

        assert info["termsOfService"] == "https://example.com/terms"
        assert "(https://example.com/privacy)" in info["description"]
