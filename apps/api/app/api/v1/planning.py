"""Source-verified annual planning; clients cannot supply money, authority or clocks."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_projection import AnnualProjectionResponse, compute_annual_projection
from app.services.multi_goal_planning import JointPlanningResponse, joint_goal_planning
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/planning",
    tags=["年度资金规划"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class AnnualPlanningQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get("/annual", response_model=AnnualProjectionResponse, operation_id="read_annual_planning")
def read_annual_planning(
    query: Annotated[AnnualPlanningQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> AnnualProjectionResponse:
    return compute_annual_projection(session, user.id, now)


@router.get(
    "/current-goal-allocation",
    response_model=JointPlanningResponse,
    operation_id="read_current_joint_goal_allocation",
)
def read_current_joint_goal_allocation(
    query: Annotated[AnnualPlanningQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> JointPlanningResponse:
    return joint_goal_planning(session, user.id, now)
