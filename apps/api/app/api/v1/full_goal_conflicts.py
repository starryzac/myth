"""Read-only actual conflict diagnostics and explicit, non-authorizing proposals."""

import json
from typing import Annotated, Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_goal_conflicts import GoalRepairPreviewRequest, MonthlyMaxPlanningAdjustment
from app.services.full_goal_conflicts import (
    FullGoalConflictResponse,
    FullGoalRepairResponse,
    preview_full_goal_repairs,
    read_full_goal_conflicts,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(
    prefix="/api/v1/planning",
    tags=["多目标冲突与修复预览"],
    responses={409: {"model": ErrorEnvelope}},
)


class NoGoalConflictQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoalRepairPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_epoch_id: UUID
    reviewed_state_hash: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
    adjustments: Annotated[list[MonthlyMaxPlanningAdjustment], Field(min_length=1, max_length=8)]

    @field_validator("adjustments", mode="before")
    @classmethod
    def strict_json_adjustments(cls, value: Any) -> list[MonthlyMaxPlanningAdjustment]:
        if not isinstance(value, list):
            raise ValueError("Planning adjustments must be a finite JSON list")
        rows = []
        for row in value:
            if isinstance(row, MonthlyMaxPlanningAdjustment):
                rows.append(row)
            elif isinstance(row, dict):
                rows.append(
                    MonthlyMaxPlanningAdjustment.model_validate_json(
                        json.dumps(row, ensure_ascii=False, allow_nan=False)
                    )
                )
            else:
                raise ValueError("Each planning adjustment must be an exact JSON object")
        if len({row.goal_id for row in rows}) != len(rows):
            raise ValueError("Each goal has exactly one user-selected planning range")
        return rows

    def domain_request(self) -> GoalRepairPreviewRequest:
        return GoalRepairPreviewRequest(
            expected_epoch_id=self.expected_epoch_id,
            reviewed_state_hash=self.reviewed_state_hash,
            adjustments=self.adjustments,
        )


@router.get(
    "/full-goal-conflicts",
    response_model=FullGoalConflictResponse,
    operation_id="read_actual_full_goal_conflicts",
)
def read(
    query: Annotated[NoGoalConflictQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalConflictResponse:
    return read_full_goal_conflicts(session, user.id, now)


@router.post(
    "/full-goal-repairs/preview",
    response_model=FullGoalRepairResponse,
    operation_id="preview_actual_full_goal_repairs",
    dependencies=[Depends(require_no_query)],
)
def preview(
    body: GoalRepairPreviewBody,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalRepairResponse:
    return preview_full_goal_repairs(session, user.id, body.domain_request(), now)
