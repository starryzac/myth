"""Evaluation-only five-set interface; all facts and authorization are server-derived."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.services.autonomy_envelope import (
    EnvelopeRequest,
    EnvelopeResponse,
    assess_envelope_action,
    assess_envelope_intent,
)
from fastapi import APIRouter, Depends

router = APIRouter(
    prefix="/api/v1/autonomy-envelope",
    tags=["五集合自主包络"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/assess", response_model=EnvelopeResponse, operation_id="assess_autonomy_envelope_intent"
)
def assess(
    body: EnvelopeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> EnvelopeResponse:
    return assess_envelope_intent(session, user.id, body.intent, now)


@router.get(
    "/actions/{action_id}",
    response_model=EnvelopeResponse,
    operation_id="assess_autonomy_envelope_action",
)
def assess_action(
    action_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> EnvelopeResponse:
    return assess_envelope_action(session, user.id, action_id, now)
