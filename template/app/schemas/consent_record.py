"""Schemas for what an account has consented to."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.consent_record import MAXIMUM_KIND_LENGTH
from app.models.consent_record import MAXIMUM_VERSION_LENGTH
from app.schemas.base import BaseSchema


class ConsentRequest(BaseSchema):
    """Give or withdraw consent for one kind."""

    kind: str = Field(min_length=1, max_length=MAXIMUM_KIND_LENGTH)
    is_granted: bool
    document_version: str | None = Field(
        default=None,
        max_length=MAXIMUM_VERSION_LENGTH,
    )


class ConsentResponse(BaseSchema):
    """One answer, as it stands.

    Carries when it was given and which version of the text it was given
    for, because "has consented" without either is not something anybody
    can act on later.
    """

    kind: str
    is_granted: bool
    document_version: str | None = None
    recorded_at: datetime


class ConsentsResponse(BaseSchema):
    """Every question this account has answered, and its current answer.

    Only answered kinds appear. What an absent kind means is the
    application's rule, not this one's: terms usually gate sign up,
    marketing never gates anything.
    """

    consents: list[ConsentResponse]
