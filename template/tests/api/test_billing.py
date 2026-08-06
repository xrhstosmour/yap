"""Integration tests for the billing API routes."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from typing import Literal

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

from app.core.rate_limit import coupon_validate_rate_limiter
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> Literal["asyncio"]:
    return "asyncio"


@pytest.fixture
async def client() -> Generator[AsyncClient, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _tenant_user(session, *, is_tenant_owner: bool, slug: str) -> User:
    from app.services.auth_service import AuthService

    tenant = Tenant(name=f"Billing Test {slug}", slug=slug)
    session.add(tenant)
    await session.commit()

    user = User(
        email=f"{slug}@example.com",
        hashed_password="hash",
        tenant_id=tenant.id,
        is_tenant_owner=is_tenant_owner,
        is_active=True,
    )
    session.add(user)
    await session.commit()

    service = AuthService(session)
    tokens = service.create_tokens(user)
    return user, tokens.access_token  # type: ignore[return-value]


@pytest.mark.usefixtures("override_get_async_session")
async def test_checkout_forbidden_for_non_owner(client: AsyncClient, session) -> None:
    _, access_token = await _tenant_user(
        session, is_tenant_owner=False, slug="checkout-non-owner"
    )

    response = await client.post(
        "/api/v1/billing/checkout",
        json={
            "plan_id": "00000000-0000-0000-0000-000000000001",
            "success_url": "http://localhost:5173/success",
            "cancel_url": "http://localhost:5173/cancel",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 403


@pytest.mark.usefixtures("override_get_async_session")
async def test_cancel_forbidden_for_non_owner(client: AsyncClient, session) -> None:
    _, access_token = await _tenant_user(
        session, is_tenant_owner=False, slug="cancel-non-owner"
    )

    response = await client.post(
        "/api/v1/billing/cancel",
        json={"at_period_end": True},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 403


@pytest.mark.usefixtures("override_get_async_session")
async def test_checkout_not_found_for_owner_without_plan(
    client: AsyncClient, session
) -> None:
    """Owner passes the authorization gate; a nonexistent plan 404s downstream."""
    _, access_token = await _tenant_user(
        session, is_tenant_owner=True, slug="checkout-owner"
    )

    response = await client.post(
        "/api/v1/billing/checkout",
        json={
            "plan_id": "00000000-0000-0000-0000-000000000001",
            "success_url": "http://localhost:5173/success",
            "cancel_url": "http://localhost:5173/cancel",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 404


@pytest.mark.usefixtures("override_get_async_session")
async def test_checkout_rejects_untrusted_redirect_origin(
    client: AsyncClient, session
) -> None:
    """`success_url`/`cancel_url` must be rejected when their origin
    isn't in `settings.all_cors_origins`.

    Regression test: these were passed straight to Stripe with only a
    length check, which meant checkout could be turned into an open
    redirect to any attacker-controlled host.
    """
    _, access_token = await _tenant_user(
        session, is_tenant_owner=True, slug="checkout-untrusted-redirect"
    )

    response = await client.post(
        "/api/v1/billing/checkout",
        json={
            "plan_id": "00000000-0000-0000-0000-000000000001",
            "success_url": "https://attacker.example/success",
            "cancel_url": "http://localhost:5173/cancel",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("override_get_async_session")
async def test_get_subscription_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/v1/billing/subscription")
    assert response.status_code == 401


@pytest.mark.usefixtures("override_get_async_session")
async def test_get_subscription_none_when_no_subscription_row(
    client: AsyncClient, session
) -> None:
    _, access_token = await _tenant_user(
        session, is_tenant_owner=True, slug="no-subscription"
    )

    response = await client.get(
        "/api/v1/billing/subscription",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.usefixtures("override_get_async_session")
async def test_coupon_validate_rate_limit_enforced(
    client: AsyncClient, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The general `disable_rate_limit` autouse fixture patches the
    `check_user_rate_limit`/`check_api_key_rate_limit` dependencies used by
    `get_current_user`, not `check_coupon_validate_rate_limit` — this
    endpoint's own limiter is exercised directly here instead, against a
    real Redis (the `override_get_redis` autouse fixture only overrides
    the FastAPI-injected dependency, not this direct `get_redis()` call)."""
    from app.core.cache import close_redis
    from app.core.cache import init_redis

    await init_redis()
    try:
        _, access_token = await _tenant_user(
            session, is_tenant_owner=False, slug="coupon-rate-limit"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        body = {"code": "ANYCODE", "plan_id": "00000000-0000-0000-0000-000000000001"}

        monkeypatch.setattr(coupon_validate_rate_limiter, "limit", 1)

        first = await client.post(
            "/api/v1/billing/coupons/validate", json=body, headers=headers
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/v1/billing/coupons/validate", json=body, headers=headers
        )
        assert second.status_code == 429
    finally:
        await close_redis()
