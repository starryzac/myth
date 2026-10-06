"""Strict identities for metadata observations; no client financial worlds."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.global_boundary_postcommit import postcommit_global_observation
from app.api.v1.full_policies import require_no_query
from app.domain.full_action_set_boundary import (
    ActionSetSnapshot,
    GlobalBoundaryObservation,
    GlobalBoundaryObserveRequest,
)
from app.services.full_action_set_boundary import (
    observe_global_boundary,
    read_current_action_set,
    read_global_boundary_observation,
)
from fastapi import APIRouter, Depends, Response
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/boundary/action-set",
    tags=["有限全局动作边界"],
    dependencies=[Depends(require_no_query)],
    responses={409: {"model": ErrorEnvelope}},
)


@router.get(
    "/current",
    response_model=ActionSetSnapshot,
    operation_id="read_actual_current_policy_action_set",
)
def current(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> ActionSetSnapshot:
    return read_current_action_set(session, user.id, now)


@router.post(
    "/observe",
    response_model=GlobalBoundaryObservation,
    operation_id="observe_actual_global_policy_action_set",
)
def observe(
    body: GlobalBoundaryObserveRequest,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> GlobalBoundaryObservation:
    return postcommit_global_observation(
        engine, user.id, observe_global_boundary(engine, user.id, body, now), now, response
    )


@router.get(
    "/observations/{run_id}",
    response_model=GlobalBoundaryObservation,
    operation_id="read_original_global_policy_action_observation",
)
def original(
    run_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> GlobalBoundaryObservation:
    return read_global_boundary_observation(session, user.id, run_id, now)
