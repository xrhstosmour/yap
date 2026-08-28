"""Integration tests for File API endpoints."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from httpx import ASGITransport
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.main import app
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService


@pytest.fixture(name="client")
def client_fixture() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestFileUpload:
    """Tests for POST /api/v1/files/upload."""

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_upload_requires_auth(self, client: AsyncClient) -> None:
        """Uploading without auth should return 401."""
        response = await client.post("/api/v1/files/upload")
        assert response.status_code == 401

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_upload_success(
        self,
        client: AsyncClient,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Uploading a file should return 201 with metadata."""
        import hashlib

        auth_service = AuthService(cast(AsyncSession, session))
        user = await auth_service.register(
            RegisterRequest(email="file-upload@testapp.com", password="password123")
        )
        token = create_access_token(subject=user.id)

        # Mock blob storage to avoid S3 credentials.
        async def _fake_upload(content, mimetype, uploaded_by, bucket=None):
            content_hash = hashlib.sha256(content).hexdigest()
            return f"uploads/{uploaded_by}/{content_hash}", content_hash

        monkeypatch.setattr(
            "app.services.file_service.upload_file",
            _fake_upload,
        )

        content = b"hello world"
        files = {"file": ("test.txt", content, "text/plain")}
        response = await client.post(
            "/api/v1/files/upload",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["filename"] == "test.txt"
        assert data["mimetype"] == "text/plain"
        assert data["size"] == len(content)
        assert "id" in data


class TestFileNotFoundReturnsCleanly:
    """Regression tests: a missing or unowned file_id must return 404,
    not crash. get_file_url, get_file_metadata, and delete_file all call
    FileService methods that raise FileServiceError, not ValueError, on a
    missing/unowned file. Catching only ValueError let FileServiceError
    propagate uncaught, an unhandled 500 instead of the documented 404.
    """

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_get_file_url_returns_404_for_unknown_file(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        auth_service = AuthService(cast(AsyncSession, session))
        user = await auth_service.register(
            RegisterRequest(email="file-url-404@testapp.com", password="password123")
        )
        token = create_access_token(subject=user.id)

        response = await client.get(
            f"/api/v1/files/{uuid4()}/url",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_get_file_metadata_returns_404_for_unknown_file(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        auth_service = AuthService(cast(AsyncSession, session))
        user = await auth_service.register(
            RegisterRequest(
                email="file-metadata-404@testapp.com", password="password123"
            )
        )
        token = create_access_token(subject=user.id)

        response = await client.get(
            f"/api/v1/files/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404

    @pytest.mark.usefixtures("override_get_async_session")
    async def test_delete_file_returns_404_for_unknown_file(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        auth_service = AuthService(cast(AsyncSession, session))
        user = await auth_service.register(
            RegisterRequest(email="file-delete-404@testapp.com", password="password123")
        )
        token = create_access_token(subject=user.id)

        response = await client.delete(
            f"/api/v1/files/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
