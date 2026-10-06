"""Explicit finite parameter selection; every endpoint is read-only."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_goal_adjustments import GoalAdjustmentRequest
from app.services.full_goal_adjustments import (
    GoalAdjustmentPreviewResponse,
    GoalAdjustmentReadResponse,
    preview_current_goal_adjustments,
    read_goal_adjustments,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/planning/full-goal-adjustments",
    tags=["明确目标参数调整"],
    responses={409: {"model": ErrorEnvelope}},
)


class NoAdjustmentQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "",
    response_model=GoalAdjustmentReadResponse,
    operation_id="read_current_goal_adjustment_originals",
)
def read(
    query: Annotated[NoAdjustmentQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalAdjustmentReadResponse:
    return read_goal_adjustments(session, user.id, now)


@router.post(
    "/preview",
    response_model=GoalAdjustmentPreviewResponse,
    operation_id="preview_selected_minimum_or_deadline_adjustments",
    dependencies=[Depends(require_no_query)],
)
def preview(
    body: GoalAdjustmentRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> GoalAdjustmentPreviewResponse:
    return preview_current_goal_adjustments(session, user.id, body, now)
