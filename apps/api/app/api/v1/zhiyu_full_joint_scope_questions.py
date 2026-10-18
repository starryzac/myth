"""One explicit registered-scope preference, never a financial command."""

from typing import Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_full_joint_questions import result_identity
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.api.v1.zhiyu_questions import require_question_user
from app.domain.full_joint_scope_questions import PROTOCOL
from app.services.audit_chain import audit_read_scope
from app.services.full_joint_scope_questions import (
    ScopeQuestionAnswerRequest,
    ScopeQuestionCloseRequest,
    ScopeQuestionRefreshRequest,
    ScopeQuestionStartRequest,
    mutate,
    read_command,
    read_view,
    require_wire_capacity,
)
from fastapi import APIRouter, Depends, Request

router = APIRouter(
    prefix="/api/v1/zhiyu-next/questions/full-joint-scopes",
    tags=["完整联合目标显式已确认规则范围选择"],
    dependencies=[Depends(isolated)],
)


def wire_response(value: dict[str, Any]) -> dict[str, Any]:
    require_wire_capacity(value, reserve_bytes=0)
    return value


@router.get("/schema")
def schema() -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "supported_input": "EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES",
        "finite_world_count": {"minimum": 2, "maximum": 2},
        "preference_variables": ["REGISTERED_SCOPE_CHOICE"],
        "simultaneous_pending_questions": 1,
        "unknown_worlds_are_questions": False,
        "automatically_discovered_rules_are_user_preferences": False,
        "client_amounts_priorities_sources_or_clocks_allowed": False,
        "bank_authority": False,
        "execution_eligible": False,
        "requires_separate_whole_financial_confirmation": True,
        "limitations": [
            "仅比较本人显式声明的当前已确认完整联合规则，不修改任何目标偏好。",
            "回答只选择当前来源下的规划范围，不生成金融准备、确认或银行动作。",
            "任一原生候选未知时不通过偏好回答猜测资金事实；需先核实来源。",
            "其它资产、期限或自然语言歧义候选仍未实现。",
            "本阶段恰好两条既有完整规则；完整原JSON恢复上限64MiB，更多备选需另行原件归档合同。",
        ],
    }


@router.post("/sessions")
def start(
    body: ScopeQuestionStartRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, None, body, request.url.path, now, principal)
    return wire_response(
        {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}
    )


@router.get("/commands/{epoch_id}/by-key/{client_request_id}")
def command(
    epoch_id: UUID,
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return wire_response({
            **identity(session, user.id, engine),
            **read_command(session, user.id, epoch_id, client_request_id),
        })


@router.get("/sessions/{session_id}")
def read(
    session_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return wire_response({
            **identity(session, user.id, engine),
            **read_view(session, user.id, session_id, now),
        })


@router.post("/sessions/{session_id}/answers")
def answer(
    session_id: UUID,
    body: ScopeQuestionAnswerRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return wire_response(
        {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}
    )


@router.post("/sessions/{session_id}/refresh")
def refresh(
    session_id: UUID,
    body: ScopeQuestionRefreshRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return wire_response(
        {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}
    )


@router.post("/sessions/{session_id}/close")
def close(
    session_id: UUID,
    body: ScopeQuestionCloseRequest,
    request: Request,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    receipt = mutate(engine, user.id, session_id, body, request.url.path, now, principal)
    return wire_response(
        {**result_identity(engine, user), "original_receipt": receipt.model_dump(mode="json")}
    )
