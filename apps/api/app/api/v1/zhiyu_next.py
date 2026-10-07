"""Closed extension adapters; all money, clocks, facts and receipts are server-owned."""

import os
from typing import Annotated, Any, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.v1.zhiyu import _state
from app.db.models import Account, ActionPlan, ExternalBankFact
from app.domain.external_bank_fact import validate_external_fact_original
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.audit_chain import audit_read_scope
from app.services.demo_console import _epoch
from app.services.execution import get_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.historical_read import historical_ledger_scope
from app.services.llm_provider import OpenAICompatibleProvider
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_agent import message
from app.services.zhiyu_model_settings import (
    ModelSettingsUpdate,
    load_model_settings,
    save_model_settings,
)
from app.services.zhiyu_orchestration import (
    agent_context,
    autonomy_state,
    confirm_authorization,
    control_autonomy,
    drain_once,
    operation,
    records,
    remember_operation,
    remember_rejection,
    replay_operation,
    serial_user,
)
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

EngineDependency = Annotated[Engine, Depends(get_engine)]


def isolated(request: Request, engine: EngineDependency) -> None:
    require_zhiyu_next_engine(engine, check_connection=True)
    if request.query_params:
        raise PolicyLifecycleError("UNSUPPORTED_QUERY", "接口不接受额外金融事实或时钟", 422)


router = APIRouter(
    prefix="/api/v1/zhiyu-next", tags=["知余扩展版"], dependencies=[Depends(isolated)]
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientRequest(RequestModel):
    client_request_id: UUID


class AgentRequest(ClientRequest):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    engine: Literal["llm", "rules"] = "llm"
    parent_request_id: UUID | None = None


class ConfirmRequest(ClientRequest):
    expected_epoch_id: UUID
    proposal_id: UUID
    reviewed_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit consent required")
        return value


class ResumeRequest(ClientRequest):
    authorization_id: UUID
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit consent required")
        return value


class IncomeRequest(ClientRequest):
    expected_epoch_id: UUID
    event: Literal["PAYROLL_A", "PAYROLL_B"]


CAPABILITIES = [
    {
        "id": "single_goal",
        "title": "单目标持续安排",
        "status": "AVAILABLE",
        "reason": "开发验证范围，当前只开放无费用、无损失的已确认单目标",
    },
    {
        "id": "model",
        "title": "实际模型接入",
        "status": "AVAILABLE",
        "reason": "需在模型设置中配置服务并验证真实连通",
    },
    *[
        {"id": name, "title": title, "status": "NOT_OPEN", "reason": "尚未完成本扩展版实际验收"}
        for name, title in [
            ("ask_once_execution", "逐笔新授权"),
            ("twelve_policies", "十二策略"),
            ("multi_goal", "多目标与年度"),
            ("assets", "资产闭环"),
            ("minimax", "目标与资产必要问题"),
        ]
    ],
]


@router.get("/environment")
def environment(
    session: SessionDependency, user: DemoUserDependency, engine: EngineDependency
) -> dict[str, Any]:
    return {
        "simulation": True,
        "variant": "zhiyu-next",
        "environment_id": require_zhiyu_next_engine(engine),
        "epoch_id": str(_epoch(session, user.id)),
        "round_id": os.environ["ZHIYU_NEXT_ROUND"],
    }


@router.get("/state")
def state(
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        base = _state(session, user.id, engine, now).model_dump(mode="json")
        original_ids = {action["action_id"] for action in base["actions"]}
        for action in session.scalars(
            select(ActionPlan).where(ActionPlan.user_id == user.id).order_by(ActionPlan.created_at)
        ):
            if str(action.id) not in original_ids:
                actual = get_action(session, user.id, action.id, now)
                base["actions"].append(actual.model_dump(mode="json"))
                base["activity"].append(
                    {
                        "id": str(action.id),
                        "at": action.created_at.isoformat(),
                        "intent": "为目标自动安排",
                        "authorization": "已确认的持续授权",
                        "decision": "已完成" if actual.receipt else "正在核实原安排",
                        "amount_cents": actual.effect.amount_cents,
                        "status": actual.status,
                        "action_id": str(action.id),
                        "goal_id": str(action.goal_id),
                    }
                )
        income_facts = list(
            session.scalars(
                select(ExternalBankFact).where(
                    ExternalBankFact.user_id == user.id,
                    ExternalBankFact.kind == "INCOME",
                    ExternalBankFact.bank_status == "SETTLED",
                    ExternalBankFact.projection_status == "PROJECTED",
                )
            )
        )
        base["income_received"] = bool(income_facts)
        activity_ids = {entry["id"] for entry in base["activity"]}
        for fact in income_facts:
            if str(fact.id) in activity_ids:
                continue
            validate_external_fact_original(
                {column.name: getattr(fact, column.name) for column in fact.__table__.columns}
            )
            base["activity"].append(
                {
                    "id": str(fact.id),
                    "at": fact.created_at.isoformat(),
                    "intent": "实际模拟收入到账",
                    "authorization": "已核实的模拟银行事实",
                    "decision": "实际到账资金已进入当前规划，后续安排受现行规则限制",
                    "amount_cents": fact.amount_cents,
                    "status": "SETTLED",
                }
            )
        for control in records(session, user.id, "CONTROL"):
            paused = control.content["payload"]["state"] == "PAUSED"
            base["activity"].append(
                {
                    "id": str(control.id),
                    "at": control.created_at.isoformat(),
                    "intent": "暂停自动安排" if paused else "开启自动安排",
                    "authorization": "用户明确确认的持续授权控制",
                    "decision": "停止接受新安排，原已提交安排继续核对"
                    if paused
                    else "只在当前有效授权和保护规则内处理实际到账资金",
                    "amount_cents": None,
                    "status": "PAUSED" if paused else "ACTIVE",
                }
            )
        base["activity"].sort(key=lambda item: (item["at"], item["id"]))
        base.update(
            variant="zhiyu-next",
            autonomy=autonomy_state(session, user.id, now),
            capabilities=CAPABILITIES,
        )
        return base


@router.get("/model-settings")
def model_settings() -> Any:
    return {"simulation": True, **load_model_settings().public_status().model_dump(mode="json")}


@router.put("/model-settings")
def update_model_settings(body: ModelSettingsUpdate) -> Any:
    return {"simulation": True, **save_model_settings(body).public_status().model_dump(mode="json")}


@router.post("/model-settings/test")
def test_model_settings(body: RequestModel) -> Any:
    result = OpenAICompatibleProvider(load_model_settings()).test_connection()
    return {"simulation": True, **result.model_dump(mode="json")}


@router.post("/agent/messages")
def agent_message(
    body: AgentRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    original = {
        "kind": "AGENT",
        "text": body.text,
        "engine": body.engine,
        "parent_request_id": str(body.parent_request_id) if body.parent_request_id else None,
    }
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            previous = replay_operation(session, user.id, body.client_request_id, original)
            if previous is not None:
                return previous
            source, turns = agent_context(session, user.id, body.parent_request_id, body.text)
            result = message(session, user.id, source, body.engine, now)
            result["simulation"] = True
            result["client_request_id"] = str(body.client_request_id)
            remember_operation(
                session,
                user.id,
                body.client_request_id,
                original,
                result,
                now,
                context={"source_text": source, "turn_count": turns},
            )
            return result
    except PolicyLifecycleError as error:
        # Agent compilation has no financial side effects, and its whole
        # transaction rolled back. Retain a definitive, static rejection.
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.post("/authorizations/confirm")
def confirm(
    body: ConfirmRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    try:
        result = confirm_authorization(
            engine,
            user.id,
            body.proposal_id,
            body.reviewed_hash,
            body.client_request_id,
            body.expected_epoch_id,
            now,
        )
    except PolicyLifecycleError as error:
        original = {
            "kind": "CONFIRM",
            "proposal_id": str(body.proposal_id),
            "reviewed_hash": body.reviewed_hash,
            "expected_epoch_id": str(body.expected_epoch_id),
            "accepted": True,
        }
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise
    return {"simulation": True, **result}


@router.post("/autonomy/pause")
def pause(
    body: ClientRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    try:
        result = control_autonomy(engine, user.id, "PAUSED", body.client_request_id, now)
    except PolicyLifecycleError as error:
        remember_rejection(
            engine,
            user.id,
            body.client_request_id,
            {"kind": "PAUSED", "authorization_id": None},
            error,
            now,
        )
        raise
    return {"simulation": True, **result}


@router.post("/autonomy/resume")
def resume(
    body: ResumeRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    try:
        result = control_autonomy(
            engine, user.id, "ACTIVE", body.client_request_id, now, body.authorization_id
        )
    except PolicyLifecycleError as error:
        remember_rejection(
            engine,
            user.id,
            body.client_request_id,
            {"kind": "ACTIVE", "authorization_id": str(body.authorization_id)},
            error,
            now,
        )
        raise
    return {"simulation": True, **result}


@router.get("/operations/{client_request_id}")
def read_operation(
    client_request_id: UUID, session: SessionDependency, user: DemoUserDependency
) -> dict[str, Any]:
    result = operation(session, user.id, client_request_id)
    if result is None:
        raise PolicyLifecycleError("NOT_FOUND", "原请求尚未有可核实结果，请保留原定位", 404)
    return result


@router.post("/demo/income")
def income(
    body: IncomeRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    original = {
        "kind": "INCOME",
        "event": body.event,
        "expected_epoch_id": str(body.expected_epoch_id),
    }
    with serial_user(engine, user.id):
        with Session(engine) as session:
            epoch_id = _epoch(session, user.id, body.expected_epoch_id)
            previous = replay_operation(session, user.id, body.client_request_id, original)
            if previous is not None:
                return {"simulation": True, **previous}
            key = f"zhiyu-next:{epoch_id}:{body.event}"
            fact = session.scalar(
                select(ExternalBankFact).where(
                    ExternalBankFact.user_id == user.id,
                    ExternalBankFact.idempotency_key == key,
                )
            )
            if fact is not None:
                request = validate_external_fact_original(
                    {c.name: getattr(fact, c.name) for c in fact.__table__.columns}
                )
            else:
                account = session.scalar(
                    select(Account)
                    .where(
                        Account.user_id == user.id,
                        Account.account_type == "CASH",
                    )
                    .order_by(Account.id)
                    .limit(1)
                )
                if account is None:
                    raise PolicyLifecycleError("NOT_FOUND", "模拟来源账户未初始化", 404)
                request = ExternalFactRequest(
                    user_id=user.id,
                    idempotency_key=key,
                    external_ref=key,
                    kind="INCOME",
                    account_id=account.id,
                    amount_cents=200_000,
                    counterparty_ref="payroll",
                    occurred_at=now,
                )
        ingest_external_fact(engine, user.id, request, now)
        result = drain_once(engine, user.id, now)
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            previous = replay_operation(session, user.id, body.client_request_id, original)
            if previous is not None:
                return {"simulation": True, **previous}
            remember_operation(session, user.id, body.client_request_id, original, result, now)
        return {"simulation": True, **result}
