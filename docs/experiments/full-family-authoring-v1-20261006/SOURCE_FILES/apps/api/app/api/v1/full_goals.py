"""Review both FULL goal planning and original execution-policy hashes."""

from typing import Annotated, Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_goals import (
    FullGoalConfirmationResponse,
    FullGoalModelResponse,
    FullGoalPreviewResponse,
    confirm_full_goal_model,
    preview_full_goal_model,
    read_full_goal_model,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

router = APIRouter(
    prefix="/api/v1/goals",
    tags=["完整目标模型"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class FullGoalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FullGoalPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version_id: UUID
    configuration: dict[str, Any]


class FullGoalConfirmationRequest(FullGoalPreviewRequest):
    expected_epoch_id: UUID
    reviewed_full_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reviewed_base_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    accepted: StrictBool
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=150)]

    @field_validator("accepted")
    @classmethod
    def explicit_acceptance(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Both goal model hashes require explicit confirmation")
        return value


@router.get(
    "/{goal_id}/full-model",
    response_model=FullGoalModelResponse,
    operation_id="read_full_goal_model",
)
def read_model(
    goal_id: UUID,
    query: Annotated[FullGoalQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalModelResponse:
    return read_full_goal_model(session, user.id, goal_id, now)


@router.post(
    "/{goal_id}/full-model/preview",
    response_model=FullGoalPreviewResponse,
    operation_id="preview_full_goal_model",
)
def preview_model(
    goal_id: UUID,
    body: FullGoalPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalPreviewResponse:
    return preview_full_goal_model(
        session, user.id, goal_id, body.expected_version_id, body.configuration, now
    )


@router.post(
    "/{goal_id}/full-model/confirm",
    response_model=FullGoalConfirmationResponse,
    operation_id="confirm_full_goal_model",
)
def confirm_model(
    goal_id: UUID,
    body: FullGoalConfirmationRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGoalConfirmationResponse:
    return confirm_full_goal_model(
        session,
        user.id,
        goal_id,
        body.expected_version_id,
        body.expected_epoch_id,
        body.configuration,
        body.reviewed_full_hash,
        body.reviewed_base_hash,
        body.accepted,
        body.reason,
        body.idempotency_key,
        now,
    )
