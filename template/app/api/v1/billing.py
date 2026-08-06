"""Billing API routes: checkout, portal, cancel, coupons, invoices, payments.

Mutation routes (checkout, portal, cancel) require `TenantOwnerUser`.
Read routes (subscription/invoices/payments/payment-methods) remain
`CurrentUser`-gated — any user in the tenant can see billing status and
history, only the owner can change it. This router is itself billing
management, so it is never gated behind `ActiveBilling` (`app.core.
billing_access`) — a lapsed tenant must still be able to reach
`/checkout`/`/cancel` to reactivate.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from fastapi.requests import Request

from app.core.logging import get_logger
from app.core.pagination import PAGINATION_HEADERS_SPEC
from app.core.pagination import PaginatedResponse
from app.core.payment_provider import PaymentProviderError
from app.core.providers.stripe_provider import StripeProvider
from app.core.rate_limit import check_coupon_validate_rate_limit
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.dependencies import TenantOwnerUser
from app.repositories.audit_repository import AuditLogRepository
from app.schemas.base import PaginationParameters
from app.schemas.billing import CancelSubscriptionRequest
from app.schemas.billing import CheckoutRequest
from app.schemas.billing import CheckoutResponse
from app.schemas.billing import CouponValidateRequest
from app.schemas.billing import CouponValidateResponse
from app.schemas.billing import InvoiceListResponse
from app.schemas.billing import InvoiceResponse
from app.schemas.billing import PaymentListResponse
from app.schemas.billing import PaymentMethodResponse
from app.schemas.billing import PaymentResponse
from app.schemas.billing import PortalRequest
from app.schemas.billing import PortalResponse
from app.schemas.billing import SubscriptionResponse
from app.services.billing_service import BillingService
from app.services.billing_service import CouponAlreadyRedeemedError
from app.services.billing_service import CouponExhaustedError
from app.services.billing_service import CouponExpiredError
from app.services.billing_service import CouponNotApplicableError
from app.services.billing_service import CouponNotFoundError
from app.services.billing_service import NoActiveSubscriptionError
from app.services.billing_service import PlanNotFoundError

router = APIRouter(prefix="/billing", tags=["Billing"])
logger = get_logger("api.billing")

_NO_TENANT = "User is not associated with a tenant"
_PAYMENT_PROVIDER_UNAVAILABLE = (
    "Payment provider is temporarily unavailable, please try again"
)


def _billing_service(session: SessionDependency) -> BillingService:
    return BillingService(
        session,
        StripeProvider(),
        audit_repository=AuditLogRepository(session),
    )


def _require_tenant_id(user_tenant_id) -> None:  # noqa: ANN001
    if user_tenant_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_NO_TENANT)


async def _check_coupon_validate_rate_limit(current_user: CurrentUser) -> None:
    """Resolve the rate-limit key from `CurrentUser` rather than a query param.

    `check_coupon_validate_rate_limit(user_id: str)` takes a plain
    `str`, which FastAPI would otherwise interpret as a required query
    parameter if wired directly into `dependencies=[Depends(...)]`.
    """
    await check_coupon_validate_rate_limit(str(current_user.id))


@router.post(
    "/checkout",
    summary="Start checkout",
    description="Start a Stripe Checkout session for a plan subscription. "
    "Tenant owner only.",
)
async def start_checkout(
    data: CheckoutRequest,
    current_user: TenantOwnerUser,
    session: SessionDependency,
) -> CheckoutResponse:
    """Start a Stripe Checkout session (tenant owner only)."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)

    try:
        checkout_session = await service.start_checkout(
            tenant_id=current_user.tenant_id,  # type: ignore[arg-type]
            plan_id=data.plan_id,
            user_id=current_user.id,
            user_email=current_user.email,
            success_url=data.success_url,
            cancel_url=data.cancel_url,
            coupon_code=data.coupon_code,
        )
        await session.commit()
    except PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NoActiveSubscriptionError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        CouponNotFoundError,
        CouponExpiredError,
        CouponExhaustedError,
        CouponNotApplicableError,
    ) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except CouponAlreadyRedeemedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except PaymentProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PAYMENT_PROVIDER_UNAVAILABLE,
        ) from e

    return CheckoutResponse(
        checkout_url=checkout_session.url,
        stripe_customer_id=checkout_session.stripe_customer_id,
    )


@router.post(
    "/portal",
    summary="Open billing portal",
    description="Create a Stripe Billing Portal session. Tenant owner only.",
)
async def open_billing_portal(
    data: PortalRequest,
    current_user: TenantOwnerUser,
    session: SessionDependency,
) -> PortalResponse:
    """Create a Stripe Billing Portal session (tenant owner only)."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)

    try:
        portal_url = await service.create_portal_session(
            tenant_id=current_user.tenant_id,  # type: ignore[arg-type]
            return_url=data.return_url,
        )
    except NoActiveSubscriptionError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PaymentProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PAYMENT_PROVIDER_UNAVAILABLE,
        ) from e

    return PortalResponse(portal_url=portal_url)


@router.post(
    "/cancel",
    summary="Cancel subscription",
    description="Cancel the tenant's subscription. Tenant owner only.",
)
async def cancel_subscription(
    data: CancelSubscriptionRequest,
    current_user: TenantOwnerUser,
    session: SessionDependency,
) -> SubscriptionResponse:
    """Cancel the tenant's subscription (tenant owner only)."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)

    try:
        subscription = await service.cancel_tenant_subscription(
            tenant_id=current_user.tenant_id,  # type: ignore[arg-type]
            actor_id=current_user.id,
            at_period_end=data.at_period_end,
        )
        await session.commit()
    except NoActiveSubscriptionError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PaymentProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PAYMENT_PROVIDER_UNAVAILABLE,
        ) from e

    return SubscriptionResponse.model_validate(subscription)


@router.post(
    "/coupons/validate",
    summary="Validate coupon",
    description="Validate a coupon code without redeeming it, so the "
    "frontend can show 'code applied' before checkout.",
    dependencies=[Depends(_check_coupon_validate_rate_limit)],
)
async def validate_coupon(
    data: CouponValidateRequest,
    current_user: CurrentUser,
    session: SessionDependency,
) -> CouponValidateResponse:
    """Validate a coupon code (tight rate limit — a code-guessing target)."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)

    try:
        coupon = await service.validate_coupon(
            data.code,
            current_user.tenant_id,  # type: ignore[arg-type]
            data.plan_id,
        )
    except (
        CouponNotFoundError,
        CouponExpiredError,
        CouponExhaustedError,
        CouponNotApplicableError,
        CouponAlreadyRedeemedError,
    ) as e:
        return CouponValidateResponse(valid=False, code=data.code, reason=str(e))

    return CouponValidateResponse(
        valid=True,
        code=coupon.code,
        discount_type=coupon.discount_type.value,
        percent_off=coupon.percent_off,
        amount_off_cents=coupon.amount_off_cents,
        free_days=coupon.free_days,
    )


@router.get(
    "/subscription",
    summary="Get subscription",
    description="Get the tenant's current subscription status.",
)
async def get_subscription(
    current_user: CurrentUser,
    session: SessionDependency,
) -> SubscriptionResponse | None:
    """Get the tenant's current subscription status."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)
    subscription = await service.get_subscription_for_tenant(
        current_user.tenant_id  # type: ignore[arg-type]
    )
    if subscription is None:
        return None
    return SubscriptionResponse.model_validate(subscription)


@router.get(
    "/invoices",
    response_model=InvoiceListResponse,
    responses=PAGINATION_HEADERS_SPEC,  # type: ignore[arg-type]
    summary="List invoices",
    description="List the tenant's invoices.",
)
async def list_invoices(
    parameters: Annotated[PaginationParameters, Depends()],
    current_user: CurrentUser,
    session: SessionDependency,
    request: Request,
) -> PaginatedResponse:
    """List the tenant's invoices."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)
    invoices, total = await service.list_invoices_for_tenant(
        current_user.tenant_id,  # type: ignore[arg-type]
        skip=parameters.skip,
        limit=parameters.limit,
    )

    page = (parameters.skip // parameters.limit) + 1 if parameters.limit > 0 else 1
    pages = (
        (total + parameters.limit - 1) // parameters.limit
        if parameters.limit > 0
        else 1
    )

    return PaginatedResponse(
        content=InvoiceListResponse(
            data=[InvoiceResponse.model_validate(i) for i in invoices],
            total=total,
            page=page,
            page_size=parameters.limit,
            pages=pages,
        ).model_dump(),
        total=total,
        skip=parameters.skip,
        limit=parameters.limit,
        request=request,
    )


@router.get(
    "/payments",
    response_model=PaymentListResponse,
    responses=PAGINATION_HEADERS_SPEC,  # type: ignore[arg-type]
    summary="List payments",
    description="List the tenant's payment history.",
)
async def list_payments(
    parameters: Annotated[PaginationParameters, Depends()],
    current_user: CurrentUser,
    session: SessionDependency,
    request: Request,
) -> PaginatedResponse:
    """List the tenant's payment history."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)
    payments, total = await service.list_payments_for_tenant(
        current_user.tenant_id,  # type: ignore[arg-type]
        skip=parameters.skip,
        limit=parameters.limit,
    )

    page = (parameters.skip // parameters.limit) + 1 if parameters.limit > 0 else 1
    pages = (
        (total + parameters.limit - 1) // parameters.limit
        if parameters.limit > 0
        else 1
    )

    return PaginatedResponse(
        content=PaymentListResponse(
            data=[PaymentResponse.model_validate(p) for p in payments],
            total=total,
            page=page,
            page_size=parameters.limit,
            pages=pages,
        ).model_dump(),
        total=total,
        skip=parameters.skip,
        limit=parameters.limit,
        request=request,
    )


@router.get(
    "/payment-methods",
    summary="List payment methods",
    description="List the tenant's saved payment methods.",
)
async def list_payment_methods(
    current_user: CurrentUser,
    session: SessionDependency,
) -> list[PaymentMethodResponse]:
    """List the tenant's saved payment methods."""
    _require_tenant_id(current_user.tenant_id)
    service = _billing_service(session)
    payment_methods = await service.list_payment_methods_for_tenant(
        current_user.tenant_id  # type: ignore[arg-type]
    )
    return [PaymentMethodResponse.model_validate(p) for p in payment_methods]
