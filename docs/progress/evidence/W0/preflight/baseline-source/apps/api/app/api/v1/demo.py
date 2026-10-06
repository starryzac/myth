"""The console accepts only versioned predefined synthetic inputs."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import (
    ClockDependency,
    DemoUserDependency,
    SessionDependency,
    get_engine,
)
from app.api.errors import ErrorEnvelope
from app.services.demo_console import (
    get_demo_command,
    get_demo_presets,
    get_demo_state,
    prepare_demo_template,
    reset_demo,
    run_demo_event,
)
from app.services.demo_console_types import (
    DemoCommandView,
    DemoEventRequest,
    DemoPresets,
    DemoResetRequest,
    DemoResetResponse,
    DemoState,
    DemoTemplateRequest,
    DemoTemplateView,
    TemplateKind,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/demo",
    tags=["合成演示控制台"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)
EngineDependency = Annotated[Engine, Depends(get_engine)]


@router.get("/presets", response_model=DemoPresets, operation_id="demo_presets")
def presets(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> DemoPresets:
    return get_demo_presets(session, user.id, now)


@router.get("/state", response_model=DemoState, operation_id="demo_state")
def state(session: SessionDependency, user: DemoUserDependency, now: ClockDependency) -> DemoState:
    return get_demo_state(session, user.id, now)


@router.post(
    "/templates/{kind}/prepare",
    response_model=DemoTemplateView,
    operation_id="prepare_demo_template",
)
def template(
    kind: TemplateKind,
    body: DemoTemplateRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DemoTemplateView:
    return prepare_demo_template(engine, user.id, kind, body.expected_epoch_id, now)


@router.post("/events", response_model=DemoCommandView, operation_id="run_demo_event")
def event(
    body: DemoEventRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> DemoCommandView:
    return run_demo_event(engine, user.id, body, now)


@router.get("/commands/{command_id}", response_model=DemoCommandView, operation_id="demo_command")
def command(
    command_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> DemoCommandView:
    return get_demo_command(session, user.id, command_id, now)


@router.post("/reset", response_model=DemoResetResponse, operation_id="reset_demo")
def reset(
    body: DemoResetRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> DemoResetResponse:
    return reset_demo(engine, user.id, body, now)
