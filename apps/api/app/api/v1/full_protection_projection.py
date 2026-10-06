"""No-query annual FULL commitments; original execution inputs remain separate."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_protection_projection import (
    FullAnnualProtectionResponse,
    compute_full_annual_protection,
)
from fastapi import APIRouter, Depends, HTTPException, Request


def no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/planning",
    tags=["完整年度保护规划"],
    dependencies=[Depends(no_query)],
    responses={409: {"model": ErrorEnvelope}},
)


@router.get(
    "/full-annual",
    response_model=FullAnnualProtectionResponse,
    operation_id="full_annual_protection",
)
def read_full_annual(
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAnnualProtectionResponse:
    return compute_full_annual_protection(session, user.id, now)
