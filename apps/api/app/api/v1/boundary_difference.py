"""Compare actual persisted decisions without accepting client financial snapshots."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.boundary_difference import BoundaryDifference
from app.services.boundary_difference import BoundaryDifferenceRequest, compare_boundary_runs
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/boundary-differences",
    tags=["真实边界差分"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/compare", response_model=BoundaryDifference, operation_id="compare_original_boundary_runs"
)
def compare(
    body: BoundaryDifferenceRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> BoundaryDifference:
    return compare_boundary_runs(session, user.id, body, now)
