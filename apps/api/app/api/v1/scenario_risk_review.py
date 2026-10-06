"""Readonly independent scenario hypotheses; no authorization or bank command."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.scenario_risk_review import ScenarioRiskRequest
from app.services.scenario_risk_review import (
    ScenarioRiskComparison,
    ScenarioRiskContext,
    compare_scenario_risk,
    read_scenario_risk_context,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/scenario-risk-review",
    tags=["只读假设分项复核"],
    responses={409: {"model": ErrorEnvelope}},
)


class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "/context", response_model=ScenarioRiskContext, operation_id="read_scenario_risk_review_context"
)
def context(
    query: Annotated[NoParameters, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ScenarioRiskContext:
    return read_scenario_risk_context(session, user.id, now)


@router.post(
    "/compare", response_model=ScenarioRiskComparison, operation_id="compare_scenario_risk_review"
)
def compare(
    body: ScenarioRiskRequest,
    query: Annotated[NoParameters, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ScenarioRiskComparison:
    return compare_scenario_risk(session, user.id, now, body)
