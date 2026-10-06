"""Current local USER recovery through the unchanged original redemption pipeline."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.local_actor_sessions import LocalActorDependency
from app.domain.full_recovery_execution import (
    FullRecoveryConfirmation,
    FullRecoveryExecuteRequest,
    FullRecoveryPrepareRequest,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.services.action_contracts import ActionResponse
from app.services.full_recovery_execution import (
    FullRecoveryExecutionLookup,
    FullRecoveryExecutionPreview,
    confirm_full_recovery_action,
    execute_full_recovery_action,
    lookup_full_recovery_execution,
    prepare_full_recovery_execution,
    preview_full_recovery_execution,
)
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.engine import Engine


def get_current_recovery_user(
    principal: LocalActorDependency, user: DemoUserDependency, now: ClockDependency
) -> LocalActorPrincipal:
    try:
        require_local_user(principal, user.id, now)
    except ValueError:
        raise HTTPException(403, "需要当前本地USER会话；身份本身不授予资金权限") from None
    return principal


RecoveryPrincipalDependency = Annotated[LocalActorPrincipal, Depends(get_current_recovery_user)]
EngineDependency = Annotated[Engine, Depends(get_engine)]
router = APIRouter(
    prefix="/api/v1/full-recovery-actions",
    tags=["完整安全恢复原动作"],
    dependencies=[Depends(require_no_query), Depends(get_current_recovery_user)],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)


@router.post(
    "/preview",
    response_model=FullRecoveryExecutionPreview,
    operation_id="preview_original_full_recovery_execution",
)
def preview(
    body: FullRecoveryPrepareRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullRecoveryExecutionPreview:
    return preview_full_recovery_execution(session, user.id, body, now)


@router.post(
    "/prepare",
    response_model=ActionResponse,
    operation_id="prepare_original_full_recovery_execution",
)
def prepare(
    body: FullRecoveryPrepareRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> ActionResponse:
    return prepare_full_recovery_execution(engine, user.id, body, principal, now)


@router.get(
    "/by-key/{idempotency_key:path}",
    response_model=FullRecoveryExecutionLookup,
    operation_id="lookup_original_full_recovery_execution",
)
def by_key(
    idempotency_key: Annotated[str, Path(min_length=1, max_length=120, pattern=r"\S")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullRecoveryExecutionLookup:
    return lookup_full_recovery_execution(session, user.id, idempotency_key, now)


@router.post(
    "/actions/{action_id}/confirm",
    response_model=ActionResponse,
    operation_id="confirm_user_original_full_recovery_execution",
)
def confirm(
    action_id: UUID,
    body: FullRecoveryConfirmation,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> ActionResponse:
    return confirm_full_recovery_action(engine, user.id, action_id, body, principal, now)


@router.post(
    "/actions/{action_id}/execute",
    response_model=ActionResponse,
    operation_id="execute_original_full_recovery_execution",
)
def execute(
    action_id: UUID,
    body: FullRecoveryExecuteRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> ActionResponse:
    return execute_full_recovery_action(engine, user.id, action_id, body, principal, now)
