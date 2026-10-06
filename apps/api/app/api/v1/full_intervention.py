"""Durable intervention receipts; no notification can answer or confirm an action."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_intervention import AcknowledgmentRequest, DeliveryRequest, ObserveRequest
from app.domain.question_workflow import CommandKey
from app.services import full_intervention as service
from app.services.full_intervention import (
    InterventionCommandLookup,
    InterventionCommandResponse,
    InterventionDeliveryResponse,
    InterventionList,
    InterventionView,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/interventions",
    tags=["持久介入通知"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.get("", response_model=InterventionList, operation_id="list_original_interventions")
def list_messages(
    request: Request,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=service.MAX_MESSAGES)] = 50,
) -> InterventionList:
    if set(request.query_params) - {"limit"} or len(request.query_params.getlist("limit")) > 1:
        raise HTTPException(status_code=422)
    return service.list_interventions(session, user.id, now, limit)


@router.post(
    "/observe",
    response_model=InterventionCommandResponse,
    operation_id="observe_original_intervention",
    dependencies=[Depends(require_no_query)],
)
def observe(
    body: ObserveRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> InterventionCommandResponse:
    return service.observe_intervention(engine, user.id, body, now)


@router.get(
    "/commands/{epoch_id}/by-key/{key}",
    response_model=InterventionCommandLookup,
    operation_id="read_original_intervention_command",
    dependencies=[Depends(require_no_query)],
)
def command(
    epoch_id: UUID,
    key: CommandKey,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> InterventionCommandLookup:
    return service.read_intervention_command(session, user.id, epoch_id, key, now)


@router.get(
    "/{message_id}",
    response_model=InterventionView,
    operation_id="read_original_intervention",
    dependencies=[Depends(require_no_query)],
)
def read(
    message_id: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> InterventionView:
    return service.read_intervention(session, user.id, message_id, now)


@router.post(
    "/{message_id}/deliveries",
    response_model=InterventionDeliveryResponse,
    operation_id="claim_original_intervention_once",
    dependencies=[Depends(require_no_query)],
)
def deliver(
    message_id: UUID,
    body: DeliveryRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> InterventionDeliveryResponse:
    return service.deliver_intervention(engine, user.id, message_id, body, now)


@router.post(
    "/{message_id}/acknowledgements",
    response_model=InterventionCommandResponse,
    operation_id="acknowledge_original_intervention",
    dependencies=[Depends(require_no_query)],
)
def acknowledge(
    message_id: UUID,
    body: AcknowledgmentRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> InterventionCommandResponse:
    return service.acknowledge_intervention(engine, user.id, message_id, body, now)
