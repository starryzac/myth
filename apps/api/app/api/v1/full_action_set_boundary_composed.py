"""Server-owned current producer composition, without financial or metadata writes."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.full_policies import require_no_query
from app.domain.full_action_set_boundary_composed import ComposedActionSetSnapshot
from app.services.full_action_set_boundary_composed import read_current_composed_action_set
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/boundary/composed-action-set",
    tags=["完整当前动作来源组合"],
    dependencies=[Depends(require_no_query)],
)


@router.get(
    "/current",
    response_model=ComposedActionSetSnapshot,
    operation_id="read_current_composed_actual_action_set_v3",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> ComposedActionSetSnapshot:
    return read_current_composed_action_set(session, user.id, now)
