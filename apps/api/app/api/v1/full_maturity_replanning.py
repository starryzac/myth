"""Only an original maturity identity and current policy identity enter RR/RO replanning."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.full_maturity_replanning import MaturityReplanningRequest
from app.services.full_maturity_replanning import (
    MaturityReplanningResponse,
    preview_maturity_replanning,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/full-maturity-replanning",
    tags=["到期当前策略重规划"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post(
    "/preview",
    response_model=MaturityReplanningResponse,
    operation_id="preview_current_maturity_replanning",
)
def preview(
    body: MaturityReplanningRequest,
    query: Annotated[NoParameters, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> MaturityReplanningResponse:
    return preview_maturity_replanning(session, user.id, body, now)
