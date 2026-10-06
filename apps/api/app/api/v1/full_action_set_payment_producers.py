"""Fixed-owner current producers only; no client finance, grants or observation writes."""

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.full_policies import require_no_query
from app.domain.full_action_set_payment_producers import PeriodicActionSetResult
from app.services.full_action_set_payment_producers import read_current_periodic_payment_producers
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/boundary/periodic-action-producers",
    tags=["实际周期动作来源"],
    dependencies=[Depends(require_no_query)],
)


@router.get(
    "/current",
    response_model=PeriodicActionSetResult,
    operation_id="read_current_actual_periodic_payment_producers_v1",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> PeriodicActionSetResult:
    return read_current_periodic_payment_producers(session, user.id, now)
