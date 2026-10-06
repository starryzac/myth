"""Signed local USER declarations; amounts and clock are owned by the server."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.local_actor_sessions import LocalActorDependency
from app.domain.future_income_planning import (
    FutureIncomeCandidate,
    FutureIncomeCandidateRequest,
    FutureIncomeCommandLookup,
    FutureIncomeConfirmation,
    FutureIncomeConfirmationRequest,
    FutureIncomePlanningResponse,
    FutureIncomeSourceInventory,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.services import future_income_planning as service
from fastapi import APIRouter, Depends, HTTPException, Path


def require_planning_user(
    principal: LocalActorDependency, user: DemoUserDependency, now: ClockDependency
) -> LocalActorPrincipal:
    try:
        require_local_user(principal, user.id, now)
    except ValueError:
        raise HTTPException(403, "需要当前签名本地USER会话；声明不授予资金权限") from None
    return principal


PlanningUser = Annotated[LocalActorPrincipal, Depends(require_planning_user)]
router = APIRouter(
    prefix="/api/v1/planning/future-income",
    tags=["用户未来收入条件规划"],
    dependencies=[Depends(require_no_query), Depends(require_planning_user)],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)


@router.get(
    "/sources",
    response_model=FutureIncomeSourceInventory,
    operation_id="read_original_future_income_sources",
)
def sources(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FutureIncomeSourceInventory:
    return service.read_future_income_sources(session, user.id, now)


@router.get(
    "",
    response_model=FutureIncomePlanningResponse,
    operation_id="read_conditional_future_income_planning",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FutureIncomePlanningResponse:
    return service.read_future_income_planning(session, user.id, now)


@router.post(
    "/candidates",
    response_model=FutureIncomeCandidate,
    operation_id="create_original_future_income_candidate",
)
def candidate(
    body: FutureIncomeCandidateRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    principal: PlanningUser,
    now: ClockDependency,
) -> FutureIncomeCandidate:
    return service.create_future_income_candidate(session, user.id, body, principal, now)


@router.post(
    "/confirm",
    response_model=FutureIncomeConfirmation,
    operation_id="confirm_original_future_income_assumption",
)
def confirm(
    body: FutureIncomeConfirmationRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    principal: PlanningUser,
    now: ClockDependency,
) -> FutureIncomeConfirmation:
    return service.confirm_future_income_candidate(session, user.id, body, principal, now)


@router.get(
    "/commands/{epoch_id}/by-key/{idempotency_key}",
    response_model=FutureIncomeCommandLookup,
    operation_id="lookup_original_future_income_command",
)
def lookup(
    epoch_id: UUID,
    idempotency_key: Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FutureIncomeCommandLookup:
    return service.lookup_future_income_command(session, user.id, epoch_id, idempotency_key, now)
