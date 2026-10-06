"""Exact signed USER initiation and existing recurring bank execution, with no financial inputs."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.local_actor_sessions import LocalActorDependency
from app.domain.full_payment_permissions import (
    Key,
    PaymentActionConfirmation,
    PaymentConfirmRequest,
    PaymentExecuteRequest,
    PaymentPrepareRequest,
    PaymentScopeRequest,
    PaymentStartRequest,
)
from app.services.action_contracts import ActionResponse
from app.services.full_payment_permissions import (
    PaymentCommandLookup,
    PaymentCommandReceipt,
    PaymentConsentLookup,
    PaymentExecutor,
    PaymentPreparedLookup,
    PaymentScopePreview,
    confirm_full_payment_action,
    confirm_payment_relation,
    execute_full_payment,
    prepare_full_payment,
    preview_payment_relation,
    read_full_payment_action,
    read_payment_action_consent,
    read_payment_command,
    read_prepared_payment,
    start_payment_relation,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/full-payment-relations",
    tags=["固定收款关系"],
    dependencies=[Depends(require_no_query)],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
EngineDependency = Annotated[Engine, Depends(get_engine)]


def get_guarded_payment_executor() -> PaymentExecutor | None:
    """Root must wire original execute_action after its mandatory new-bank scope hook."""
    return None


ExecutorDependency = Annotated[PaymentExecutor | None, Depends(get_guarded_payment_executor)]


@router.post(
    "/preview", response_model=PaymentScopePreview, operation_id="preview_full_payment_relation"
)
def preview(
    body: PaymentScopeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> PaymentScopePreview:
    return preview_payment_relation(session, user.id, body, now)


@router.post(
    "/start", response_model=PaymentCommandReceipt, operation_id="start_user_full_payment_relation"
)
def start(
    body: PaymentStartRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: LocalActorDependency,
    now: ClockDependency,
) -> PaymentCommandReceipt:
    return start_payment_relation(engine, user.id, body, principal, now)


@router.post(
    "/starts/{start_command_id}/confirm",
    response_model=PaymentCommandReceipt,
    operation_id="confirm_user_full_payment_relation",
)
def confirm(
    start_command_id: UUID,
    body: PaymentConfirmRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: LocalActorDependency,
    now: ClockDependency,
) -> PaymentCommandReceipt:
    return confirm_payment_relation(engine, user.id, start_command_id, body, principal, now)


@router.get(
    "/commands/{epoch_id}/by-key/{key}",
    response_model=PaymentCommandLookup,
    operation_id="read_original_full_payment_command",
)
def lookup(
    epoch_id: UUID,
    key: Key,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> PaymentCommandLookup:
    return read_payment_command(session, user.id, epoch_id, key, now)


@router.post(
    "/authorizations/{authorization_id}/prepare",
    response_model=ActionResponse,
    operation_id="prepare_full_recurring_payment",
)
def prepare(
    authorization_id: UUID,
    body: PaymentPrepareRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: LocalActorDependency,
    now: ClockDependency,
) -> ActionResponse:
    return prepare_full_payment(engine, user.id, authorization_id, body, principal, now)


@router.get(
    "/authorizations/{authorization_id}/prepared/by-key/{key}",
    response_model=PaymentPreparedLookup,
    operation_id="read_original_full_payment_prepare",
)
def lookup_prepare(
    authorization_id: UUID,
    key: Key,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> PaymentPreparedLookup:
    return read_prepared_payment(session, user.id, authorization_id, key, now)


@router.get(
    "/actions/{action_id}",
    response_model=ActionResponse,
    operation_id="read_original_full_recurring_payment",
)
def read_action(
    action_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> ActionResponse:
    return read_full_payment_action(session, user.id, action_id, now)


@router.post(
    "/actions/{action_id}/confirm",
    response_model=ActionResponse,
    operation_id="confirm_user_full_recurring_payment",
)
def confirm_action(
    action_id: UUID,
    body: PaymentActionConfirmation,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: LocalActorDependency,
    now: ClockDependency,
) -> ActionResponse:
    return confirm_full_payment_action(engine, user.id, action_id, body, principal, now)


@router.post(
    "/actions/{action_id}/execute",
    response_model=ActionResponse,
    operation_id="execute_full_recurring_payment",
)
def execute(
    action_id: UUID,
    body: PaymentExecuteRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: LocalActorDependency,
    now: ClockDependency,
    executor: ExecutorDependency,
) -> ActionResponse:
    return execute_full_payment(
        engine, user.id, action_id, body, principal, now, guarded_executor=executor
    )


@router.get(
    "/actions/{action_id}/user-consent",
    response_model=PaymentConsentLookup,
    operation_id="read_signed_original_full_payment_action_consent",
)
def read_consent(
    action_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> PaymentConsentLookup:
    return read_payment_action_consent(session, user.id, action_id, now)
