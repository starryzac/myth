"""Original FULL goal request recovery; this GET never submits a confirmation."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_goal_commands import FullGoalCommandLookup, lookup_full_goal_command
from fastapi import APIRouter, Depends, HTTPException, Path, Request


def require_no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/goals",
    tags=["完整目标原确认查询"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.get(
    "/{goal_id}/full-model/commands/by-key/{idempotency_key:path}",
    response_model=FullGoalCommandLookup,
    operation_id="lookup_original_full_goal_confirmation",
)
def lookup_original(
    goal_id: UUID,
    idempotency_key: Annotated[str, Path(min_length=1, max_length=150)],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalCommandLookup:
    return lookup_full_goal_command(session, user.id, goal_id, idempotency_key, now)
