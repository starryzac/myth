"""Current original recovery impacts; a query only tightens a deadline."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_recovery_planning import (
    FullRecoveryPlanningResponse,
    read_full_recovery_planning,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, field_validator

router = APIRouter(
    prefix="/api/v1/full-policies",
    tags=["完整恢复规划"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class RecoveryPlanningQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planning_deadline_at: datetime | None = None

    @field_validator("planning_deadline_at")
    @classmethod
    def aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("A planning deadline must be timezone-aware")
        return value


@router.get(
    "/{policy_id}/recovery-planning",
    response_model=FullRecoveryPlanningResponse,
    operation_id="read_full_recovery_planning",
)
def read_planning(
    policy_id: UUID,
    query: Annotated[RecoveryPlanningQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullRecoveryPlanningResponse:
    return read_full_recovery_planning(session, user.id, policy_id, now, query.planning_deadline_at)
