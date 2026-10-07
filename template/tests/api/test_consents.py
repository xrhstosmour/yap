"""Tests for what an account has consented to.

The load-bearing claim is that the table is append only. What has to be
answerable later is what was agreed, when, and to which version of the
document, so a withdrawal has to be a new record rather than an edit of the
one that said yes. A suite that only checked the current answer would pass
against an implementation that overwrote its own evidence.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.main import app
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    """Async HTTP test client for the full app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _account(session, email: str, slug: str) -> tuple[User, str]:
    """Create a tenant, a user in it, and a bearer token for that user."""
    from app.services.auth_service import AuthService

    tenant = Tenant(name="Test", slug=slug)
    session.add(tenant)
    await session.flush()

    user = User(
        email=email,
        hashed_password="hash",
        tenant_id=tenant.id,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user, AuthService(session).create_tokens(user).access_token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _by_kind(body: dict) -> dict[str, dict]:
    return {one["kind"]: one for one in body["consents"]}


@pytest.mark.usefixtures("override_get_async_session")
class TestConsentsApi:
    """Tests for GET and PUT /consents."""

    @pytest.mark.anyio
    async def test_a_new_account_has_answered_nothing(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """An absent answer is not a granted one, and not a refused one
        either. What it means is the application's rule."""
        _, token = await _account(session, "consent-new@example.com", "con-new")

        response = await client.get("/api/v1/consents", headers=_headers(token))

        assert response.status_code == 200, response.text
        assert response.json()["consents"] == []

    @pytest.mark.anyio
    async def test_granting_one_returns_everything_that_stands(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """One response redraws the whole screen."""
        _, token = await _account(session, "consent-set@example.com", "con-set")

        await client.put(
            "/api/v1/consents",
            json={"kind": "terms", "is_granted": True, "document_version": "2026-01"},
            headers=_headers(token),
        )
        response = await client.put(
            "/api/v1/consents",
            json={"kind": "marketing_email", "is_granted": False},
            headers=_headers(token),
        )

        assert response.status_code == 200, response.text
        current = _by_kind(response.json())
        assert current["terms"]["is_granted"] is True
        assert current["terms"]["document_version"] == "2026-01"
        assert current["marketing_email"]["is_granted"] is False

    @pytest.mark.anyio
    async def test_a_refusal_is_stored_rather_than_left_absent(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """Saying no is an answer. Dropping it would make a refusal
        indistinguishable from never having been asked, which is the one
        thing a consent record exists to tell apart."""
        _, token = await _account(session, "consent-no@example.com", "con-no")

        await client.put(
            "/api/v1/consents",
            json={"kind": "marketing_email", "is_granted": False},
            headers=_headers(token),
        )
        response = await client.get("/api/v1/consents", headers=_headers(token))

        current = _by_kind(response.json())
        assert current["marketing_email"]["is_granted"] is False

    @pytest.mark.anyio
    async def test_the_newest_answer_is_the_one_that_stands(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        _, token = await _account(session, "consent-latest@example.com", "con-last")

        for granted in (True, False, True):
            await client.put(
                "/api/v1/consents",
                json={"kind": "marketing_email", "is_granted": granted},
                headers=_headers(token),
            )

        response = await client.get("/api/v1/consents", headers=_headers(token))

        current = _by_kind(response.json())
        assert len(response.json()["consents"]) == 1
        assert current["marketing_email"]["is_granted"] is True

    @pytest.mark.anyio
    async def test_a_withdrawal_keeps_the_record_of_the_grant(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """The whole point of the table. An implementation that updated one
        row in place would pass every test above and fail this one."""
        _, token = await _account(session, "consent-history@example.com", "con-hist")

        await client.put(
            "/api/v1/consents",
            json={"kind": "marketing_email", "is_granted": True},
            headers=_headers(token),
        )
        await client.put(
            "/api/v1/consents",
            json={"kind": "marketing_email", "is_granted": False},
            headers=_headers(token),
        )

        response = await client.get(
            "/api/v1/consents/history",
            headers=_headers(token),
        )

        assert response.status_code == 200, response.text
        answers = response.json()["consents"]
        assert [one["is_granted"] for one in answers] == [True, False]

    @pytest.mark.anyio
    async def test_the_history_can_be_narrowed_to_one_question(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        _, token = await _account(session, "consent-narrow@example.com", "con-narr")

        for kind in ("terms", "marketing_email"):
            await client.put(
                "/api/v1/consents",
                json={"kind": kind, "is_granted": True},
                headers=_headers(token),
            )

        response = await client.get(
            "/api/v1/consents/history",
            params={"kind": "terms"},
            headers=_headers(token),
        )

        assert [one["kind"] for one in response.json()["consents"]] == ["terms"]

    @pytest.mark.anyio
    async def test_a_new_document_version_does_not_inherit_the_old_consent(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        """A changed policy has to be agreed to again, and the record has to
        say which text was agreed, or re-consent is unprovable."""
        _, token = await _account(session, "consent-version@example.com", "con-vers")

        await client.put(
            "/api/v1/consents",
            json={"kind": "privacy", "is_granted": True, "document_version": "2026-01"},
            headers=_headers(token),
        )
        await client.put(
            "/api/v1/consents",
            json={"kind": "privacy", "is_granted": True, "document_version": "2026-06"},
            headers=_headers(token),
        )

        history = await client.get(
            "/api/v1/consents/history",
            params={"kind": "privacy"},
            headers=_headers(token),
        )
        current = await client.get("/api/v1/consents", headers=_headers(token))

        versions = [one["document_version"] for one in history.json()["consents"]]
        assert versions == ["2026-01", "2026-06"]
        assert _by_kind(current.json())["privacy"]["document_version"] == "2026-06"

    @pytest.mark.anyio
    async def test_one_account_never_reads_anothers_answers(
        self,
        client: AsyncClient,
        session,
    ) -> None:
        _, mine = await _account(session, "consent-mine@example.com", "con-mine")
        _, theirs = await _account(session, "consent-theirs@example.com", "con-thrs")

        await client.put(
            "/api/v1/consents",
            json={"kind": "terms", "is_granted": True},
            headers=_headers(theirs),
        )

        response = await client.get("/api/v1/consents", headers=_headers(mine))

        assert response.json()["consents"] == []

    @pytest.mark.anyio
    async def test_consents_need_a_signed_in_account(
        self,
        client: AsyncClient,
    ) -> None:
        response = await client.get("/api/v1/consents")

        assert response.status_code == 401
