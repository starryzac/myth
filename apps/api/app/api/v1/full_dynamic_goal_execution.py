"""Identity-only dynamic goal preview and original-pipeline prepare endpoints."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_dynamic_goal_execution import FullDynamicGoalPrepareRequest
from app.services.action_contracts import ActionResponse
from app.services.full_dynamic_goal_execution import (
    FullDynamicGoalLookup,
    FullDynamicGoalPreview,
    lookup_full_dynamic_goal_execution,
    prepare_full_dynamic_goal_execution,
    preview_full_dynamic_goal_execution,
)
from fastapi import APIRouter, Depends, Path
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/dynamic-goal-actions",
    tags=["原授权范围内动态目标储备"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.get(
    "/by-key/{idempotency_key}",
    response_model=FullDynamicGoalLookup,
    operation_id="lookup_original_dynamic_goal_execution_key",
)
def by_key(
    idempotency_key: Annotated[str, Path(min_length=1, max_length=120, pattern=r"\S")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullDynamicGoalLookup:
    return lookup_full_dynamic_goal_execution(session, user.id, idempotency_key, now)


@router.post(
    "/preview",
    response_model=FullDynamicGoalPreview,
    operation_id="preview_actual_dynamic_goal_execution_range",
)
def preview(
    body: FullDynamicGoalPrepareRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullDynamicGoalPreview:
    return preview_full_dynamic_goal_execution(session, user.id, body, now)


@router.post(
    "/prepare",
    response_model=ActionResponse,
    operation_id="prepare_original_dynamic_goal_allocation",
)
def prepare(
    body: FullDynamicGoalPrepareRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionResponse:
    return prepare_full_dynamic_goal_execution(engine, user.id, body, now)
