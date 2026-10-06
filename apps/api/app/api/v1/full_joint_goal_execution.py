"""Identity-only readonly review of a server-derived whole goal allocation."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.full_recovery_execution import RecoveryPrincipalDependency
from app.domain.full_joint_goal_execution import (
    FullJointGoalConfirmRequest,
    FullJointGoalExecuteRequest,
    FullJointGoalPrepareRequest,
    FullJointGoalPreview,
)
from app.services.full_joint_goal_execution import preview_full_joint_goal_execution
from app.services.full_joint_goal_execution_dispatch import (
    confirm_full_joint_goal_execution,
    execute_full_joint_goal_child,
    prepare_full_joint_goal_execution,
)
from app.services.full_joint_goal_execution_store import (
    FullJointGoalExecutionResponse,
    FullJointGoalLookup,
    lookup_full_joint_goal_execution,
    read_full_joint_goal_execution,
)
from fastapi import APIRouter, Depends, Path
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/joint-goal-actions",
    tags=["完整联合目标固定批次"],
    dependencies=[Depends(require_no_query)],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)


@router.post(
    "/preview",
    response_model=FullJointGoalPreview,
    operation_id="preview_registered_joint_goal_fixed_plan",
)
def preview(
    body: FullJointGoalPrepareRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullJointGoalPreview:
    return preview_full_joint_goal_execution(session, user.id, body, now)


@router.get(
    "/by-key/{idempotency_key}",
    response_model=FullJointGoalLookup,
    operation_id="lookup_original_joint_goal_plan_key",
)
def by_key(
    idempotency_key: Annotated[
        str, Path(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
    ],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullJointGoalLookup:
    return lookup_full_joint_goal_execution(session, user.id, idempotency_key, now)


@router.get(
    "/{plan_id}",
    response_model=FullJointGoalExecutionResponse,
    operation_id="read_original_joint_goal_plan",
)
def original(
    plan_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullJointGoalExecutionResponse:
    return read_full_joint_goal_execution(session, user.id, plan_id, now)


@router.post(
    "/prepare",
    response_model=FullJointGoalExecutionResponse,
    operation_id="prepare_registered_joint_goal_plan",
)
def prepare(
    body: FullJointGoalPrepareRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> FullJointGoalExecutionResponse:
    return prepare_full_joint_goal_execution(engine, user.id, body, principal, now)


@router.post(
    "/{plan_id}/confirm",
    response_model=FullJointGoalExecutionResponse,
    operation_id="confirm_original_whole_joint_goal_plan",
)
def confirm(
    plan_id: UUID,
    body: FullJointGoalConfirmRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> FullJointGoalExecutionResponse:
    return confirm_full_joint_goal_execution(engine, user.id, plan_id, body, principal, now)


@router.post(
    "/{plan_id}/execute-child",
    response_model=FullJointGoalExecutionResponse,
    operation_id="execute_or_recover_fixed_original_joint_goal_child",
)
def execute_child(
    plan_id: UUID,
    body: FullJointGoalExecuteRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> FullJointGoalExecutionResponse:
    return execute_full_joint_goal_child(engine, user.id, plan_id, body, principal, now)
