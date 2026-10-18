"""Closed Next thin routes for explicitly typed whole-portfolio preference receipts."""

from typing import Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.api.v1.zhiyu_questions import owned_read, require_question_user
from app.services.audit_chain import audit_read_scope
from app.services.full_asset_mode_workflow import (
    ModeAnswerRequest,
    ModeCloseRequest,
    ModeRefreshRequest,
    ModeStartRequest,
    mutate,
    read_command,
    read_view,
)
from fastapi import APIRouter, Depends, Request

router = APIRouter(
    prefix="/api/v1/zhiyu-next/questions/full-assets",
    tags=["完整资产模式的一次一问"],
    dependencies=[Depends(isolated)],
)


def result_identity(engine: EngineDependency, user: DemoUserDependency) -> dict[str, Any]:
    with owned_read(engine) as session:
        return {
            **identity(session, user.id, engine),
            "current_read_required": True,
            "receipt_is_current_authority": False,
            "bank_authority": False,
            "old_confirmation_inherited": False,
        }


@router.post("/sessions")
def start(
    body: ModeStartRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, None, body, request.url.path, now, principal)
    return {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}


@router.get("/commands/{epoch_id}/by-key/{client_request_id}")
def command(
    epoch_id: UUID,
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return {
            **identity(session, user.id, engine),
            **read_command(session, user.id, epoch_id, client_request_id),
        }


@router.get("/sessions/{session_id}")
def read(
    session_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return {
            **identity(session, user.id, engine),
            **read_view(session, user.id, session_id, now),
        }


@router.post("/sessions/{session_id}/answers")
def answer(
    session_id: UUID,
    body: ModeAnswerRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}


@router.post("/sessions/{session_id}/refresh")
def refresh(
    session_id: UUID,
    body: ModeRefreshRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}


@router.post("/sessions/{session_id}/close")
def close(
    session_id: UUID,
    body: ModeCloseRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}
