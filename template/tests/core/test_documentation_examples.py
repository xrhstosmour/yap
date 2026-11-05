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

    def test_the_docs_page_is_set_up_to_keep_the_sign_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise the next call is a 401 with nothing to explain it."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")

        application = create_app()

        assert application.swagger_ui_parameters["persistAuthorization"] is True
        assert application.swagger_ui_parameters["docExpansion"] == "none"

    def test_the_authorize_dialog_says_which_fields_to_leave_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dialog asks for a client ID and secret nothing here reads."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")

        schemes = create_app().openapi()["components"]["securitySchemes"]

        assert "client_secret" in schemes["OAuth2PasswordBearer"]["description"]

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
