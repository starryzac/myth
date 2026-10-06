"""Prepare, explicitly confirm, execute, and read a single simulated economic action."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.services.action_contracts import (
    ActionIntent,
    ActionReceiptResponse,
    ActionResponse,
    ConfirmActionRequest,
    IntentModel,
    PrepareActionRequest,
)
from app.services.autonomy import AutonomyResponse, assess_action, assess_intent
from app.services.decision_trace import DecisionTraceResponse, get_action_trace
from app.services.execution import confirm_action, execute_action, get_action, prepare_action
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi import APIRouter, Depends, Query
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/actions",
    tags=["模拟资金动作"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class ActionQuery(IntentModel):
    pass


class ExecuteActionRequest(IntentModel):
    pass


class AssessActionRequest(IntentModel):
    intent: ActionIntent


@router.get(
    "/{action_id}/decision",
    response_model=DecisionTraceResponse,
    operation_id="get_action_decision",
)
def decision(
    action_id: UUID,
    query: Annotated[ActionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DecisionTraceResponse:
    return get_action_trace(session, user.id, action_id, now)


@router.post("/assess", response_model=AutonomyResponse, operation_id="assess_action_intent")
def assess(
    body: AssessActionRequest,
    query: Annotated[ActionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> AutonomyResponse:
    return assess_intent(session, user.id, body.intent, now)


@router.get(
    "/{action_id}/autonomy", response_model=AutonomyResponse, operation_id="assess_existing_action"
)
def autonomy(
    action_id: UUID,
    query: Annotated[ActionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> AutonomyResponse:
    return assess_action(session, user.id, action_id, now)


@router.post("/prepare", response_model=ActionResponse, operation_id="prepare_action")
def prepare(
    body: PrepareActionRequest,
    query: Annotated[ActionQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionResponse:
    return prepare_action(engine, user.id, body, now)


@router.get("/{action_id}", response_model=ActionResponse, operation_id="get_action")
def read(
    action_id: UUID,
    query: Annotated[ActionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionResponse:
    return get_action(session, user.id, action_id, now)


@router.post("/{action_id}/confirm", response_model=ActionResponse, operation_id="confirm_action")
def confirm(
    action_id: UUID,
    body: ConfirmActionRequest,
    query: Annotated[ActionQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionResponse:
    return confirm_action(engine, user.id, action_id, body, now)


@router.post("/{action_id}/execute", response_model=ActionResponse, operation_id="execute_action")
def execute(
    action_id: UUID,
    body: ExecuteActionRequest,
    query: Annotated[ActionQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionResponse:
    return execute_action(engine, user.id, action_id, now)


@router.get(
    "/{action_id}/receipt", response_model=ActionReceiptResponse, operation_id="get_action_receipt"
)
def receipt(
    action_id: UUID,
    query: Annotated[ActionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionReceiptResponse:
    response = get_action(session, user.id, action_id, now)
    if response.receipt is None:
        raise PolicyLifecycleError("RECEIPT_NOT_READY", "动作尚无已对账回执", 409)
    return response.receipt
