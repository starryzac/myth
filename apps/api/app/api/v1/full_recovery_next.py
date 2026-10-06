"""Signed local USER selection of one server-derived original recovery position."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.full_recovery_execution import (
    EngineDependency,
    RecoveryPrincipalDependency,
    get_current_recovery_user,
)
from app.domain.full_recovery_next import FullRecoveryNextRequest
from app.services.action_contracts import ActionResponse
from app.services.full_recovery_next import (
    FullRecoveryNextLookup,
    FullRecoveryNextPreview,
    lookup_next_whole_recovery,
    prepare_next_whole_recovery,
    preview_next_whole_recovery,
)
from fastapi import APIRouter, Depends, Path

router = APIRouter(
    prefix="/api/v1/full-recovery-next-actions",
    tags=["完整版下一整仓原恢复"],
    dependencies=[Depends(require_no_query), Depends(get_current_recovery_user)],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)


@router.post(
    "/preview",
    response_model=FullRecoveryNextPreview,
    operation_id="preview_server_selected_next_whole_recovery",
)
def preview(
    body: FullRecoveryNextRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullRecoveryNextPreview:
    return preview_next_whole_recovery(session, user.id, body, now)


@router.post(
    "/prepare",
    response_model=ActionResponse,
    operation_id="prepare_server_selected_next_whole_recovery",
)
def prepare(
    body: FullRecoveryNextRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: RecoveryPrincipalDependency,
    now: ClockDependency,
) -> ActionResponse:
    return prepare_next_whole_recovery(engine, user.id, body, principal, now)


@router.get(
    "/by-key/{idempotency_key:path}",
    response_model=FullRecoveryNextLookup,
    operation_id="lookup_original_next_whole_recovery_root_key",
)
def by_key(
    idempotency_key: Annotated[str, Path(min_length=1, max_length=120, pattern=r"\S")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullRecoveryNextLookup:
    return lookup_next_whole_recovery(session, user.id, idempotency_key, now)
