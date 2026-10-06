"""Explicitly save assessments and read immutable historical reasoning."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.domain.decision_trace_types import TraceExplanation
from app.services.action_contracts import IntentModel
from app.services.decision_assessment import SaveAssessmentRequest, save_assessment
from app.services.decision_trace import (
    DecisionTraceList,
    DecisionTraceResponse,
    get_decision_trace,
    list_decision_traces,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/decisions",
    tags=["历史决策轨迹"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class DecisionQuery(IntentModel):
    pass


class DecisionListQuery(IntentModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: str | None = None


@router.post(
    "/assess", response_model=DecisionTraceResponse, operation_id="save_decision_assessment"
)
def save(
    body: SaveAssessmentRequest,
    query: Annotated[DecisionQuery, Query()],
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> DecisionTraceResponse:
    return save_assessment(engine, user.id, body, now)


@router.get("", response_model=DecisionTraceList, operation_id="list_decisions")
def listing(
    query: Annotated[DecisionListQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
) -> DecisionTraceList:
    return list_decision_traces(session, user.id, limit=query.limit, cursor=query.cursor)


@router.get("/{run_id}", response_model=DecisionTraceResponse, operation_id="get_decision")
def read(
    run_id: UUID,
    query: Annotated[DecisionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DecisionTraceResponse:
    return get_decision_trace(session, user.id, run_id, now)


@router.get(
    "/{run_id}/explanation", response_model=TraceExplanation, operation_id="explain_decision"
)
def explanation(
    run_id: UUID,
    query: Annotated[DecisionQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> TraceExplanation:
    response = get_decision_trace(session, user.id, run_id, now)
    if response.explanation is None:
        raise PolicyLifecycleError(
            "TRACE_EXPLANATION_UNAVAILABLE", "历史输入不完整或算法版本不支持", 409
        )
    return response.explanation
