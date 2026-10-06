"""Finite planning preferences cannot submit bank facts, permission or a world result."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.services.finite_uncertainty import (
    FinitePlanningRequest,
    FinitePlanningResponse,
    analyze_finite_planning,
)
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/finite-planning",
    tags=["有限不确定性规划"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/analyze",
    response_model=FinitePlanningResponse,
    operation_id="analyze_original_finite_planning",
)
def analyze(
    body: FinitePlanningRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FinitePlanningResponse:
    return analyze_finite_planning(session, user.id, body, now)
