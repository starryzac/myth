"""Explicit delivery of persisted simulated actions; no caller-supplied command payload."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.services.command_delivery import (
    DeliveryList,
    DeliveryView,
    deliver_command,
    enqueue_action,
    get_delivery,
    list_deliveries,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/delivery",
    tags=["持久模拟命令投递"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class EmptyDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliveryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliveryListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: Annotated[int, Field(ge=1, le=100)] = 50


@router.get("", response_model=DeliveryList, operation_id="list_command_deliveries")
def list_commands(
    query: Annotated[DeliveryListQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DeliveryList:
    return list_deliveries(session, user.id, now, limit=query.limit)


@router.get("/{outbox_id}", response_model=DeliveryView, operation_id="get_command_delivery")
def read_command(
    outbox_id: UUID,
    query: Annotated[DeliveryQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DeliveryView:
    return get_delivery(session, user.id, outbox_id, now)


@router.post(
    "/actions/{action_id}/enqueue",
    response_model=DeliveryView,
    operation_id="enqueue_original_action",
)
def enqueue_original(
    action_id: UUID,
    body: EmptyDeliveryRequest,
    query: Annotated[DeliveryQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> DeliveryView:
    return enqueue_action(engine, user.id, action_id, now)


@router.post(
    "/{outbox_id}/deliver", response_model=DeliveryView, operation_id="deliver_original_command"
)
def deliver_original(
    outbox_id: UUID,
    body: EmptyDeliveryRequest,
    query: Annotated[DeliveryQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> DeliveryView:
    return deliver_command(engine, user.id, outbox_id, now)
