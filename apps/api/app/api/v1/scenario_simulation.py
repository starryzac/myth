"""Bounded read-only scenario comparisons, never demo events, grants or receipts."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.scenario_simulation import ScenarioCompareRequest, ScenarioComparison
from app.services.scenario_simulation import (
    ScenarioContext,
    compare_current_scenario,
    read_scenario_context,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/scenario-simulation",
    tags=["只读场景反事实"],
    responses={409: {"model": ErrorEnvelope}},
)


class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "/context", response_model=ScenarioContext, operation_id="read_scenario_simulation_context"
)
def read_context(
    query: Annotated[NoParameters, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ScenarioContext:
    return read_scenario_context(session, user.id, now)


@router.post(
    "/compare", response_model=ScenarioComparison, operation_id="compare_readonly_scenario"
)
def compare(
    body: ScenarioCompareRequest,
    query: Annotated[NoParameters, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ScenarioComparison:
    return compare_current_scenario(session, user.id, now, body)
