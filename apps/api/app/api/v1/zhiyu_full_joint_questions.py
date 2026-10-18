"""Closed fixed-rule joint observation routes; no preference answer or financial consent."""

from typing import Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.api.v1.zhiyu_questions import owned_read, require_question_user
from app.domain.full_joint_question_contract import PROTOCOL
from app.services.audit_chain import audit_read_scope
from app.services.full_joint_question_workflow import (
    JointQuestionCloseRequest,
    JointQuestionRefreshRequest,
    JointQuestionStartRequest,
    mutate,
    read_command,
    read_view,
)
from fastapi import APIRouter, Depends, Request

router = APIRouter(
    prefix="/api/v1/zhiyu-next/questions/full-joint",
    tags=["完整联合目标的固定规则零问题观察"],
    dependencies=[Depends(isolated)],
)


def result_identity(engine: EngineDependency, user: DemoUserDependency) -> dict[str, Any]:
    with owned_read(engine) as session:
        return {
            **identity(session, user.id, engine),
            "current_read_required": True,
            "receipt_is_current_authority": False,
            "bank_authority": False,
            "execution_eligible": False,
            "old_confirmation_inherited": False,
        }


@router.get("/schema")
def schema() -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "supported_input": "REGISTERED_CONFIGURATION",
        "candidate_count": 1,
        "preference_question_count": 0,
        "divergent_candidate_production": "NOT_IMPLEMENTED",
        "client_amounts_priorities_sources_or_clocks_allowed": False,
        "bank_authority": False,
        "requires_separate_whole_financial_confirmation": True,
        "limitations": [
            "仅使用全部目标当前已确认规则，不把偏好声明转换为资金授权。",
            "缺少合法原生多候选生产合同，歧义偏好一次一问尚未开放。",
        ],
    }


@router.post("/sessions")
def start(
    body: JointQuestionStartRequest,
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


@router.post("/sessions/{session_id}/refresh")
def refresh(
    session_id: UUID,
    body: JointQuestionRefreshRequest,
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
    body: JointQuestionCloseRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}
