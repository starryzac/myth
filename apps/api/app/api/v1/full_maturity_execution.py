"""Signed local USER asks for one actual matured whole-position return."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.full_recovery_execution import (
    RecoveryPrincipalDependency,
    get_current_recovery_user,
)
from app.domain.full_maturity_execution import (
    FullMaturityConfirmation,
    FullMaturityExecuteRequest,
    FullMaturityRequest,
)
from app.services.full_maturity_execution import (
    FullMaturityAction,
    FullMaturityLookup,
    FullMaturityPreview,
    confirm_maturity_execution,
    execute_maturity_execution,
    lookup_maturity_execution,
    prepare_maturity_execution,
    preview_maturity_execution,
)
from fastapi import APIRouter, Depends, Path
from sqlalchemy.engine import Engine

EngineDependency = Annotated[Engine, Depends(get_engine)]
router = APIRouter(
    prefix="/api/v1/full-maturity-actions",
    tags=["用户逐次确认原合同到期整仓"],
    dependencies=[Depends(require_no_query), Depends(get_current_recovery_user)],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409)},
)


@router.post(
    "/preview", response_model=FullMaturityPreview, operation_id="preview_user_whole_maturity"
)
def preview(
    body: FullMaturityRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullMaturityPreview:
    return preview_maturity_execution(session, user.id, body, now)


@router.post(
    "/prepare", response_model=FullMaturityAction, operation_id="prepare_user_whole_maturity"
)
def prepare(
    body: FullMaturityRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> FullMaturityAction:
    return prepare_maturity_execution(engine, user.id, body, principal, now)


@router.get(
    "/by-key/{idempotency_key:path}",
    response_model=FullMaturityLookup,
    operation_id="lookup_user_whole_maturity_original_key",
)
def by_key(
    idempotency_key: Annotated[str, Path(min_length=1, max_length=120, pattern=r"\S")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullMaturityLookup:
    return lookup_maturity_execution(session, user.id, idempotency_key, now)


@router.post(
    "/actions/{action_id}/confirm",
    response_model=FullMaturityAction,
    operation_id="confirm_user_whole_maturity",
)
def confirm(
    action_id: UUID,
    body: FullMaturityConfirmation,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> FullMaturityAction:
    return confirm_maturity_execution(engine, user.id, action_id, body, principal, now)


@router.post(
    "/actions/{action_id}/execute",
    response_model=FullMaturityAction,
    operation_id="execute_user_whole_maturity_original_key",
)
def execute(
    action_id: UUID,
    body: FullMaturityExecuteRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> FullMaturityAction:
    return execute_maturity_execution(engine, user.id, action_id, body, principal, now)
