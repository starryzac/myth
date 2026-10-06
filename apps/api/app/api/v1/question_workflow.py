"""Persist original planning questions and exact answers; no permission or bank submissions."""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.question_workflow import CommandKey
from app.services.question_intervention_producer import produce_current_question_intervention
from app.services.question_workflow import (
    QuestionAnswerRequest,
    QuestionCloseRequest,
    QuestionCommandLookupResponse,
    QuestionRefreshRequest,
    QuestionStartRequest,
    QuestionWorkflowResponse,
    answer_question_session,
    close_question_session,
    read_question_command,
    read_question_session,
    read_question_start_command,
    start_question_session,
)
from fastapi import APIRouter, Depends, Response
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/finite-planning/sessions",
    tags=["持久一次一问"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


def _postcommit_question_observation(
    engine: Engine,
    user_id: UUID,
    result: QuestionWorkflowResponse,
    now: datetime,
    response: Response,
) -> QuestionWorkflowResponse:
    """Notification failure never replaces or rolls back an already committed receipt."""
    status, error_code = "SOURCE_UNVERIFIED", None
    try:
        observed = produce_current_question_intervention(
            engine, user_id, result.current_revision.session_id, now
        )
        status, error_code = observed.status, observed.error_code
    except Exception as error:
        # Do not log driver messages, request bodies, credentials or bank facts.
        error_code = type(error).__name__
    response.headers["X-Question-Intervention-Status"] = status
    logging.getLogger("bounded_funds.question_intervention").info(
        "question_postcommit_observation status=%s error_code=%s session_id=%s",
        status,
        error_code,
        result.current_revision.session_id,
    )
    return result


@router.post(
    "", response_model=QuestionWorkflowResponse, operation_id="start_original_question_session"
)
def start(
    body: QuestionStartRequest,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> QuestionWorkflowResponse:
    return _postcommit_question_observation(
        engine, user.id, start_question_session(engine, user.id, body, now), now, response
    )


@router.get(
    "/commands/{epoch_id}/by-start-key/{key}",
    response_model=QuestionCommandLookupResponse,
    operation_id="read_original_question_start_by_key",
)
def start_command(
    epoch_id: UUID,
    key: CommandKey,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> QuestionCommandLookupResponse:
    return read_question_start_command(session, user.id, epoch_id, key, now)


@router.get(
    "/{session_id}/commands/by-key/{key}",
    response_model=QuestionCommandLookupResponse,
    operation_id="read_original_question_command_by_key",
)
def command(
    session_id: UUID,
    key: CommandKey,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> QuestionCommandLookupResponse:
    return read_question_command(session, user.id, session_id, key, now)


@router.post(
    "/{session_id}/close",
    response_model=QuestionWorkflowResponse,
    operation_id="close_original_question_session",
)
def close(
    session_id: UUID,
    body: QuestionCloseRequest,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> QuestionWorkflowResponse:
    return _postcommit_question_observation(
        engine,
        user.id,
        close_question_session(engine, user.id, session_id, body, now),
        now,
        response,
    )


@router.get(
    "/{session_id}",
    response_model=QuestionWorkflowResponse,
    operation_id="read_original_question_session",
)
def read(
    session_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> QuestionWorkflowResponse:
    return read_question_session(session, user.id, session_id, now)


@router.post(
    "/{session_id}/answers",
    response_model=QuestionWorkflowResponse,
    operation_id="answer_original_pending_question",
)
def answer(
    session_id: UUID,
    body: QuestionAnswerRequest,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> QuestionWorkflowResponse:
    return _postcommit_question_observation(
        engine,
        user.id,
        answer_question_session(engine, user.id, session_id, body, now),
        now,
        response,
    )


@router.post(
    "/{session_id}/refresh",
    response_model=QuestionWorkflowResponse,
    operation_id="refresh_original_question_session",
)
def refresh(
    session_id: UUID,
    body: QuestionRefreshRequest,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> QuestionWorkflowResponse:
    return _postcommit_question_observation(
        engine,
        user.id,
        answer_question_session(engine, user.id, session_id, body, now),
        now,
        response,
    )
