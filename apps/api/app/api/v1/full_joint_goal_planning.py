"""Clients select no facts, source identities, amounts, permissions or clocks."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_joint_goal_planning import (
    FullJointPlanningResponse,
    full_joint_goal_planning,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/planning", tags=["完整多目标规划"], responses={409: {"model": ErrorEnvelope}}
)


class FullJointPlanningQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "/full-current-goal-allocation",
    response_model=FullJointPlanningResponse,
    operation_id="read_full_current_joint_goal_allocation",
)
def read_full_current_joint_goal_allocation(
    query: Annotated[FullJointPlanningQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullJointPlanningResponse:
    return full_joint_goal_planning(session, user.id, now)
