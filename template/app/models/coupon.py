"""Coupon models: the global discount-code catalog and its redemptions.

`Coupon` is a global catalog, not tenant-scoped (`tenant_id` always
`NULL`) — a single coupon code can be restricted to specific plans
and/or specific tenants via its allow-lists, which is exactly why it
cannot be tenant-scoped via `BaseModel.tenant_id`: a coupon may target
*multiple* tenants at once. `CouponRedemption` records one tenant's use
of a coupon and *is* tenant-scoped in the normal way.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import BaseModel

if TYPE_CHECKING:
    pass


class CouponDiscountType(enum.StrEnum):
    """Kind of discount a `Coupon` applies."""

    PERCENT = "percent"
    FREE = "free"
    FIXED_AMOUNT = "fixed_amount"


class CouponDuration(enum.StrEnum):
    """How long a `Coupon`'s discount applies once redeemed."""

    ONCE = "once"
    REPEATING = "repeating"
    FOREVER = "forever"


class Coupon(BaseModel, table=True):
    """A time-limited discount code in the global coupon catalog.

    Attributes:
        id: UUID primary key
        code: Unique, normalized-uppercase redemption code
        stripe_coupon_id: Stripe `Coupon` object ID, created lazily in
            Stripe on first redemption and cached back onto this row
        stripe_promotion_code_id: Stripe `PromotionCode` object ID,
            same lazy-creation/caching as `stripe_coupon_id`
        discount_type: percent / free / fixed_amount
        percent_off: Percentage discount, set when `discount_type` is
            `percent`
        amount_off_cents: Fixed discount amount, set when
            `discount_type` is `fixed_amount`
        duration: once / repeating / forever
        duration_in_months: Set when `duration` is `repeating`
        free_days: Trial extension in days — handled as our own logic
            since Stripe coupons don't model this
        max_redemptions: Redemption cap, `NULL` = unlimited
        redemption_count: Running count of redemptions, row-locked and
            incremented at redemption time
        valid_from: Coupon becomes redeemable at this time
        valid_until: Coupon stops being redeemable after this time
        allowed_plan_ids: JSON array of plan UUIDs (stored as `str`,
            since the plain `JSON` column can't serialize `UUID`
            directly) this coupon applies to; empty/`NULL` = all plans
        allowed_tenant_ids: JSON array of tenant UUIDs (stored as
            `str`, same reason) allowed to redeem this coupon;
            empty/`NULL` = publicly redeemable — this is why `Coupon`
            cannot be tenant-scoped via `BaseModel.tenant_id`
        is_active: Whether the coupon can currently be redeemed
        tenant_id: Always `NULL` — coupons are a global catalog
    """

    __tablename__ = "coupons"  # pyright: ignore[reportAssignmentType]

    code: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=64,
    )

    stripe_coupon_id: str | None = Field(default=None, max_length=255)

    stripe_promotion_code_id: str | None = Field(default=None, max_length=255)

    discount_type: CouponDiscountType = Field(
        nullable=False,
        sa_type=SAEnum(
            CouponDiscountType,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    percent_off: int | None = Field(default=None)

    amount_off_cents: int | None = Field(default=None)

    duration: CouponDuration = Field(
        nullable=False,
        sa_type=SAEnum(
            CouponDuration,
            values_callable=lambda e: [m.value for m in e],
        ),  # type: ignore[call-overload]
    )

    duration_in_months: int | None = Field(default=None)

    free_days: int = Field(default=0, nullable=False)

    max_redemptions: int | None = Field(default=None)

    redemption_count: int = Field(default=0, nullable=False)

    valid_from: datetime | None = Field(default=None)

    valid_until: datetime | None = Field(default=None)

    allowed_plan_ids: list[str] | None = Field(default=None, sa_type=JSON)

    allowed_tenant_ids: list[str] | None = Field(default=None, sa_type=JSON)

    is_active: bool = Field(default=True, nullable=False)


class CouponRedemption(BaseModel, table=True):
    """One tenant's redemption of a `Coupon`.

    Tenant-scoped in the normal way (`BaseModel.tenant_id` = the
    redeeming tenant). At most one active redemption per
    `(coupon_id, tenant_id)`, enforced at the service layer.

    Attributes:
        id: UUID primary key
        coupon_id: FK to the redeemed `Coupon`
        subscription_id: FK to the `Subscription` this redemption is
            attached to, `NULL` until checkout completes
        redeemed_by_user_id: FK to the `User` who redeemed the coupon
        redeemed_at: When the redemption occurred
        tenant_id: The redeeming tenant
    """

    __tablename__ = "coupon_redemptions"  # pyright: ignore[reportAssignmentType]

    coupon_id: UUID = Field(nullable=False, foreign_key="coupons.id", index=True)

    subscription_id: UUID | None = Field(
        default=None, foreign_key="subscriptions.id", index=True
    )

    redeemed_by_user_id: UUID = Field(nullable=False, foreign_key="users.id")

    redeemed_at: datetime | None = Field(default=None)
