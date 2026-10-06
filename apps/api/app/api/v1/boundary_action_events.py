"""Explicit original-run comparisons create records, never an executable command."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.services.boundary_action_events import (
    BoundaryObservationRequest,
    BoundaryObservationResponse,
    observe_boundary_actions,
)
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/boundary-events",
    tags=["原动作边界事件"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/observe",
    response_model=BoundaryObservationResponse,
    operation_id="observe_original_action_boundary",
)
def observe(
    body: BoundaryObservationRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> BoundaryObservationResponse:
    return observe_boundary_actions(session, user.id, body, now)
