"""Explicit goal projection and read-only allocation previews for the simulated user."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.goal_allocation import GoalAllocationResponse, preview_goal_allocation
from app.services.goals import GoalList, GoalResponse, create_goal_projection, list_goals
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/goals",
    tags=["目标储备"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class CreateGoalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: UUID
    expected_version_id: UUID
    account_id: UUID


class GoalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("", response_model=GoalResponse, operation_id="create_goal")
def create_goal(
    body: CreateGoalRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalResponse:
    return create_goal_projection(
        session, user.id, body.policy_id, body.expected_version_id, body.account_id, now
    )


@router.get("", response_model=GoalList, operation_id="list_goals")
def read_goals(
    query: Annotated[GoalQuery, Query()], session: SessionDependency, user: DemoUserDependency
) -> GoalList:
    return list_goals(session, user.id)


@router.get(
    "/{goal_id}/allocation-preview",
    response_model=GoalAllocationResponse,
    operation_id="preview_goal_allocation",
)
def read_allocation(
    goal_id: UUID,
    query: Annotated[GoalQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalAllocationResponse:
    return preview_goal_allocation(session, user.id, goal_id, now)
