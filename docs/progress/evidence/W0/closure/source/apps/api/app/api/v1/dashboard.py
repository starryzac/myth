"""Read-only same-snapshot funds overview."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.dashboard import get_dashboard
from app.services.dashboard_types import DashboardResponse
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["模拟资金边界首页"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class DashboardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_limit: int = Field(default=20, ge=1, le=100)
    recovery_limit: int = Field(default=10, ge=1, le=100)


@router.get("", response_model=DashboardResponse, operation_id="dashboard_summary")
def dashboard_summary(
    query: Annotated[DashboardQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DashboardResponse:
    return get_dashboard(
        session,
        user.id,
        now,
        pending_limit=query.pending_limit,
        recovery_limit=query.recovery_limit,
    )
