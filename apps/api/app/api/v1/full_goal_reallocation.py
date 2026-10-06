"""Only identity-bound, read-only emergency reallocation preview."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_goal_reallocation import ReallocationPreviewRequest
from app.services.full_goal_reallocation import (
    FullGoalReallocationPreview,
    preview_goal_reallocation,
)
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/goal-reallocation",
    tags=["目标现金紧急回拨预览"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/preview",
    response_model=FullGoalReallocationPreview,
    operation_id="preview_full_goal_emergency_reallocation",
)
def preview_current_reallocation(
    request: ReallocationPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalReallocationPreview:
    return preview_goal_reallocation(session, user.id, request, now)
