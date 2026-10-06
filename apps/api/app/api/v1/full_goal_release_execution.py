"""Finite dedicated release endpoints; all inputs are identities and original keys."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_goal_release_authorization import Key
from app.services.full_goal_release_execution import (
    GoalReleaseActionResponse,
    GoalReleaseCandidate,
    GoalReleaseExecuteRequest,
    GoalReleaseLookup,
    GoalReleasePrepareRequest,
    execute_goal_release_execution,
    prepare_goal_release_execution,
    preview_goal_release_execution,
    read_goal_release_by_key,
    read_goal_release_execution,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/goal-cash-releases",
    tags=["目标现金紧急回拨"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/preview",
    response_model=GoalReleaseCandidate,
    operation_id="preview_actual_dedicated_goal_cash_release",
)
def preview(
    body: GoalReleasePrepareRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalReleaseCandidate:
    return preview_goal_release_execution(session, user.id, body, now)


@router.post(
    "/prepare",
    response_model=GoalReleaseActionResponse,
    operation_id="prepare_dedicated_goal_cash_release",
)
def prepare(
    body: GoalReleasePrepareRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalReleaseActionResponse:
    return prepare_goal_release_execution(engine, user.id, body, now)


@router.get(
    "/actions/{action_id}",
    response_model=GoalReleaseActionResponse,
    operation_id="read_original_goal_cash_release",
)
def read(
    action_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> GoalReleaseActionResponse:
    return read_goal_release_execution(session, user.id, action_id, now)


@router.post(
    "/actions/{action_id}/execute",
    response_model=GoalReleaseActionResponse,
    operation_id="execute_original_goal_cash_release",
)
def execute(
    action_id: UUID,
    body: GoalReleaseExecuteRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalReleaseActionResponse:
    return execute_goal_release_execution(engine, user.id, action_id, body, now)


@router.get(
    "/commands/{epoch_id}/by-key/{key}",
    response_model=GoalReleaseLookup,
    operation_id="read_original_goal_cash_release_by_key",
)
def lookup(
    epoch_id: UUID,
    key: Key,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalReleaseLookup:
    return read_goal_release_by_key(session, user.id, epoch_id, key, now)
