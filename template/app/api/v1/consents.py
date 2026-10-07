"""Routes for what an account has consented to.

This module provides the endpoints an app calls to record a consent answer
and to read back what stands, plus the full history a subject access
request asks for.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import status

from app.core.logging import get_logger
from app.dependencies import CurrentUser
from app.dependencies import SessionDependency
from app.repositories.consent_record_repository import ConsentRecordRepository
from app.schemas.consent_record import ConsentRequest
from app.schemas.consent_record import ConsentResponse
from app.schemas.consent_record import ConsentsResponse

router = APIRouter(prefix="/consents", tags=["Consents"])
logger = get_logger("api.consents")


def _rendered(records: list) -> list[ConsentResponse]:  # noqa: ANN001
    """Shape stored answers into responses."""
    return [
        ConsentResponse(
            kind=record.kind,
            is_granted=record.is_granted,
            document_version=record.document_version,
            recorded_at=record.created_at,
        )
        for record in records
    ]


@router.get(
    "",
    response_model=ConsentsResponse,
    status_code=status.HTTP_200_OK,
    summary="What this account has consented to",
    description=(
        "The answer that stands for each question this account has "
        "answered. A question never put, or never answered, is absent."
    ),
)
async def read_consents(
    current_user: CurrentUser,
    session: SessionDependency,
) -> ConsentsResponse:
    """Return the calling account's current answers."""
    repository = ConsentRecordRepository(session)
    current = await repository.current_for(current_user.id)
    return ConsentsResponse(consents=_rendered(list(current.values())))


@router.get(
    "/history",
    response_model=ConsentsResponse,
    status_code=status.HTTP_200_OK,
    summary="Every consent answer this account has given",
    description=(
        "The full history, oldest first, including withdrawals and "
        "re-consents. This is what a subject access request asks for."
    ),
)
async def read_consent_history(
    current_user: CurrentUser,
    session: SessionDependency,
    kind: str | None = None,
) -> ConsentsResponse:
    """Return every answer, not only the ones that still stand."""
    repository = ConsentRecordRepository(session)
    return ConsentsResponse(
        consents=_rendered(await repository.history_for(current_user.id, kind))
    )


@router.put(
    "",
    response_model=ConsentsResponse,
    status_code=status.HTTP_200_OK,
    summary="Give or withdraw one consent",
    description=(
        "Appends the answer and returns everything that now stands, so one "
        "screen can be redrawn from one response. Previous answers are "
        "kept: a withdrawal is a new record, never an edit."
    ),
)
async def set_consent(
    data: ConsentRequest,
    current_user: CurrentUser,
    session: SessionDependency,
) -> ConsentsResponse:
    """Append one answer and return the current state.

    A put rather than a patch: one kind is the whole resource being named.
    It is deliberately not idempotent in storage, because sending it twice
    means the question was put twice and answered twice, and that is the
    thing worth keeping.

    The kind is not checked against a list, for the same reason a
    notification kind is not: what an application needs consent for is its
    own vocabulary.
    """
    repository = ConsentRecordRepository(session)
    await repository.record(
        current_user.id,
        data.kind,
        data.is_granted,
        data.document_version,
    )
    await session.commit()
    logger.info(
        "consent_recorded",
        user_id=str(current_user.id),
        kind=data.kind,
        is_granted=data.is_granted,
        document_version=data.document_version,
    )
    current = await repository.current_for(current_user.id)
    return ConsentsResponse(consents=_rendered(list(current.values())))
