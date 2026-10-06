"""Explicit v4 reads/observations; no notification, grant or financial writes."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.v1.full_policies import require_no_query
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_recovery_composed import (
    RecoveryComposedActionSetSnapshot,
    RecoveryComposedGlobalObservation,
)
from app.services.full_action_set_boundary_recovery_composed import (
    read_current_recovery_composed_action_set,
)
from app.services.full_action_set_recovery_observations import (
    observe_recovery_composed_action_set,
    read_recovery_composed_observation,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/boundary/recovery-composed-action-set",
    tags=["完整版周期与整仓恢复动作来源"],
    dependencies=[Depends(require_no_query)],
)


@router.get(
    "/current",
    response_model=RecoveryComposedActionSetSnapshot,
    operation_id="read_current_recovery_composed_actual_action_set_v4",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> RecoveryComposedActionSetSnapshot:
    return read_current_recovery_composed_action_set(session, user.id, now)


@router.post(
    "/observe",
    response_model=RecoveryComposedGlobalObservation,
    operation_id="record_original_recovery_composed_action_set_v4",
)
def observe(
    body: GlobalBoundaryObserveRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> RecoveryComposedGlobalObservation:
    return observe_recovery_composed_action_set(engine, user.id, body, now)


@router.get(
    "/observations/{run_id}",
    response_model=RecoveryComposedGlobalObservation,
    operation_id="read_original_recovery_composed_observation_v4",
)
def original(
    run_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> RecoveryComposedGlobalObservation:
    return read_recovery_composed_observation(session, user.id, run_id, now)
