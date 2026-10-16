"""Tests for the files that associate the native apps with the domain."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.core.settings import settings
from app.main import app

FINGERPRINT = ":".join(f"{byte:02X}" for byte in range(32))


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as one:
        yield one


class TestUnconfigured:
    """Nothing is claimed for apps nobody named."""

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "path",
        ["/.well-known/apple-app-site-association", "/.well-known/assetlinks.json"],
    )
    async def test_it_answers_not_found(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        monkeypatch.setattr(settings, "APPLE_APP_IDS", [])
        monkeypatch.setattr(settings, "ANDROID_APP_PACKAGE", "")
        monkeypatch.setattr(settings, "ANDROID_CERT_FINGERPRINTS", [])

        assert (await client.get(path)).status_code == 404


class TestConfigured:
    """The shapes each platform's fetcher expects."""

    @pytest.mark.anyio
    async def test_ios_gets_its_passkey_delegation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "APPLE_APP_IDS", ["ABCDE12345.com.example.app"])

        response = await client.get("/.well-known/apple-app-site-association")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {
            "webcredentials": {"apps": ["ABCDE12345.com.example.app"]}
        }

    @pytest.mark.anyio
    async def test_android_gets_its_passkey_delegation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "ANDROID_APP_PACKAGE", "com.example.app")
        monkeypatch.setattr(settings, "ANDROID_CERT_FINGERPRINTS", [FINGERPRINT])

        response = await client.get("/.well-known/assetlinks.json")

        assert response.status_code == 200
        statement = response.json()[0]
        assert statement["relation"] == ["delegate_permission/common.get_login_creds"]
        assert statement["target"] == {
            "namespace": "android_app",
            "package_name": "com.example.app",
            "sha256_cert_fingerprints": [FINGERPRINT],
        }


class TestLinks:
    """Links open the apps only on the paths named."""

    @pytest.mark.anyio
    async def test_ios_claims_the_link_paths(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "APPLE_APP_IDS", ["ABCDE12345.com.example.app"])
        monkeypatch.setattr(settings, "APP_LINK_PATHS", ["/auth/*"])

        response = await client.get("/.well-known/apple-app-site-association")

        assert response.json()["applinks"] == {
            "details": [
                {
                    "appIDs": ["ABCDE12345.com.example.app"],
                    "components": [{"/": "/auth/*"}],
                }
            ]
        }

    @pytest.mark.anyio
    async def test_ios_claims_no_links_without_paths(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "APPLE_APP_IDS", ["ABCDE12345.com.example.app"])
        monkeypatch.setattr(settings, "APP_LINK_PATHS", [])

        response = await client.get("/.well-known/apple-app-site-association")

        assert "applinks" not in response.json()

    @pytest.mark.anyio
    async def test_android_handles_links_with_paths(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "ANDROID_APP_PACKAGE", "com.example.app")
        monkeypatch.setattr(settings, "ANDROID_CERT_FINGERPRINTS", [FINGERPRINT])
        monkeypatch.setattr(settings, "APP_LINK_PATHS", ["/auth/*"])

        response = await client.get("/.well-known/assetlinks.json")

        assert response.json()[0]["relation"] == [
            "delegate_permission/common.get_login_creds",
            "delegate_permission/common.handle_all_urls",
        ]
