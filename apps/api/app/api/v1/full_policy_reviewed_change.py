"""Explicit signed USER configuration changes after registered financial review."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.full_recovery_execution import EngineDependency, RecoveryPrincipalDependency
from app.domain.full_policy_change_multi import SourceKind
from app.domain.full_policy_reviewed_change import (
    ConfirmReviewedChangeRequest,
    ReviewedChangeRecord,
    ReviewRequest,
)
from app.services.full_policy_reviewed_change import (
    ReviewCommandLookup,
    ReviewCommitResponse,
    confirm_reviewed_change,
    lookup_reviewed_change,
    read_review,
    register_review,
)
from fastapi import APIRouter, Depends, Path

router = APIRouter(
    prefix="/api/v1/reviewed-policy-changes",
    tags=["复核财务差量后正式修改"],
    dependencies=[Depends(require_no_query)],
    responses={409: {"model": ErrorEnvelope}},
)


@router.post("/{source_kind}/{policy_id}/review", response_model=ReviewedChangeRecord)
def review(
    source_kind: SourceKind,
    policy_id: UUID,
    body: ReviewRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    principal: RecoveryPrincipalDependency,
) -> ReviewedChangeRecord:
    return register_review(engine, user.id, source_kind, policy_id, body, now, principal)


@router.post("/{source_kind}/{policy_id}/confirm", response_model=ReviewCommitResponse)
def confirm(
    source_kind: SourceKind,
    policy_id: UUID,
    body: ConfirmReviewedChangeRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    principal: RecoveryPrincipalDependency,
) -> ReviewCommitResponse:
    return confirm_reviewed_change(engine, user.id, source_kind, policy_id, body, now, principal)


@router.get("/reviews/{review_id}", response_model=ReviewedChangeRecord)
def original_review(
    review_id: UUID, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> ReviewedChangeRecord:
    return read_review(engine, user.id, review_id, now)


@router.get("/commands/by-key/{idempotency_key}", response_model=ReviewCommandLookup)
def command_by_key(
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    idempotency_key: str = Path(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$"),
) -> ReviewCommandLookup:
    return lookup_reviewed_change(engine, user.id, idempotency_key, now)
