"""Identity-only FULL action-set observations; no client financial inputs."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.global_boundary_postcommit import postcommit_global_observation
from app.api.v1.full_policies import require_no_query
from app.domain.full_action_set_boundary import GlobalBoundaryObserveRequest
from app.domain.full_action_set_boundary_full import (
    FullActionSetSnapshot,
    FullGlobalBoundaryObservation,
)
from app.services.full_action_set_boundary_full import (
    observe_full_global_boundary,
    read_current_full_action_set,
    read_full_global_boundary_observation,
)
from fastapi import APIRouter, Depends, Response
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/boundary/full-action-set",
    tags=["完整版有限全局动作边界"],
    dependencies=[Depends(require_no_query)],
    responses={409: {"model": ErrorEnvelope}},
)


@router.get(
    "/current",
    response_model=FullActionSetSnapshot,
    operation_id="read_actual_current_full_policy_action_set",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullActionSetSnapshot:
    return read_current_full_action_set(session, user.id, now)


@router.post(
    "/observe",
    response_model=FullGlobalBoundaryObservation,
    operation_id="observe_actual_global_full_policy_action_set",
)
def observe(
    body: GlobalBoundaryObserveRequest,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullGlobalBoundaryObservation:
    return postcommit_global_observation(
        engine, user.id, observe_full_global_boundary(engine, user.id, body, now), now, response
    )


@router.get(
    "/observations/{run_id}",
    response_model=FullGlobalBoundaryObservation,
    operation_id="read_original_global_full_policy_action_observation",
)
def original(
    run_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullGlobalBoundaryObservation:
    return read_full_global_boundary_observation(session, user.id, run_id, now)
