"""Source-verified current-month goal pacing, with no client financial overrides."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.dynamic_goal_reserve import DynamicGoalReserveResponse, read_dynamic_goal_reserve
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/goals",
    tags=["动态目标储备"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class DynamicReserveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "/{goal_id}/dynamic-reserve",
    response_model=DynamicGoalReserveResponse,
    operation_id="read_dynamic_goal_reserve",
)
def read_reserve(
    goal_id: UUID,
    query: Annotated[DynamicReserveQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DynamicGoalReserveResponse:
    return read_dynamic_goal_reserve(session, user.id, goal_id, now)
