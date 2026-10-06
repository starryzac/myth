"""Read-only planning constraints can narrow plans, never grant banking authority."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.full_asset_allocation import FullAssetPlanOptions
from app.services.full_asset_allocation import (
    FullAssetAllocationResponse,
    read_full_asset_allocation,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(
    prefix="/api/v1/full-policies",
    tags=["完整资产规划"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class PlanningConstraintsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planning_comparison_days: int = Field(default=90, ge=1, le=365)
    planning_max_components: int = Field(default=3, ge=1, le=4)
    planning_max_turnover_cents: int | None = Field(default=None, ge=0, le=9223372036854775807)
    planning_funds_use_date: date | None = None
    planning_mode: Literal["PORTFOLIO", "FIXED_LADDER"] = "PORTFOLIO"


@router.get(
    "/{policy_id}/asset-allocation",
    response_model=FullAssetAllocationResponse,
    operation_id="read_full_asset_allocation",
)
def read_allocation(
    policy_id: UUID,
    query: Annotated[PlanningConstraintsQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAssetAllocationResponse:
    return read_full_asset_allocation(
        session,
        user.id,
        policy_id,
        now,
        FullAssetPlanOptions(
            comparison_days=query.planning_comparison_days,
            max_components=query.planning_max_components,
            max_turnover_cents=query.planning_max_turnover_cents,
            funds_use_date=query.planning_funds_use_date,
            mode=query.planning_mode,
        ),
    )
