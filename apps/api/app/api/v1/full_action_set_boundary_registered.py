"""New complete-family composition is a current read, without any execution grant."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.full_policies import require_no_query
from app.domain.full_action_set_boundary_registered import RegisteredActionSetSnapshot
from app.services.full_action_set_boundary_registered import read_current_registered_action_set
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/boundary/registered-action-set",
    tags=["完整版已登记实际动作来源"],
    dependencies=[Depends(require_no_query)],
)


@router.get(
    "/current",
    response_model=RegisteredActionSetSnapshot,
    operation_id="read_current_registered_action_set_v5",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> RegisteredActionSetSnapshot:
    return read_current_registered_action_set(session, user.id, now)
