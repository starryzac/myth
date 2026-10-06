"""Explicit user classification of actual original bank consumption, no money command."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.v1.full_policies import require_no_query
from app.domain.transaction_category import CategoryConfirmationRequest, CategoryKey
from app.services.transaction_category import (
    CategoryCommandResponse,
    CategoryReviewResponse,
    confirm_category,
    lookup_category_command,
    review_category,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/transactions",
    tags=["历史类别明确确认"],
    dependencies=[Depends(require_no_query)],
)


@router.get(
    "/{transaction_id}/category-review",
    response_model=CategoryReviewResponse,
    operation_id="review_original_transaction_category",
)
def review(
    transaction_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> CategoryReviewResponse:
    return review_category(session, user.id, transaction_id, now)


@router.post(
    "/{transaction_id}/category-confirmation",
    response_model=CategoryCommandResponse,
    operation_id="confirm_original_transaction_category",
)
def confirm(
    transaction_id: UUID,
    body: CategoryConfirmationRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> CategoryCommandResponse:
    return confirm_category(engine, user.id, transaction_id, body, now)


@router.get(
    "/{transaction_id}/category-confirmations/{epoch_id}/by-key/{key}",
    response_model=CategoryCommandResponse,
    operation_id="read_original_category_command",
)
def lookup(
    transaction_id: UUID,
    epoch_id: UUID,
    key: CategoryKey,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> CategoryCommandResponse:
    return lookup_category_command(session, user.id, transaction_id, epoch_id, key, now)
