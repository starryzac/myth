"""Explicit candidates for the actual synthetic user, with original-key read recovery."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.services import user_policy_declaration as service
from fastapi import APIRouter, Depends, Path

router = APIRouter(
    prefix="/api/v1/policy-declarations",
    tags=["用户结构化策略候选"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post("", response_model=service.UserDeclarationRecord, operation_id="declare_user_policy")
def declare(
    body: service.UserDeclarationRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> service.UserDeclarationRecord:
    return service.declare_user_policy(session, user.id, body, now)


@router.get(
    "/{epoch_id}/by-key/{idempotency_key}",
    response_model=service.UserDeclarationLookup,
    operation_id="lookup_user_policy_declaration",
)
def lookup(
    epoch_id: UUID,
    idempotency_key: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> service.UserDeclarationLookup:
    return service.lookup_user_declaration(session, user.id, epoch_id, idempotency_key, now)
