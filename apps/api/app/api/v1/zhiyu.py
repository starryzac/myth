"""Thin server-owned demo presets over existing policy, ledger and execution services."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.models import Account, ActionPlan, EvidenceItem, ExternalBankFact, Goal, PolicyVersion
from app.domain.external_bank_fact import validate_external_fact_original
from app.domain.external_bank_fact_types import ExternalFactRequest, ExternalFactResult
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse, GoalIntent, PrepareActionRequest
from app.services.dashboard import get_dashboard
from app.services.dashboard_types import DashboardResponse
from app.services.demo_console import _epoch, _read_statement, _statement
from app.services.execution import execute_action, get_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.goals import GoalResponse, GoalView, create_goal_projection, list_goals
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import _fault
from app.zhiyu_isolation import require_zhiyu_engine
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

Scenario = Literal["SAFE", "REVOKED", "RESPONSE_LOSS"]
INCOME_CENTS = 200_000
PREFIX = "zhiyu-v1:"
EngineDependency = Annotated[Engine, Depends(get_engine)]


def isolated_request(request: Request, engine: EngineDependency) -> None:
    require_zhiyu_engine(engine, check_connection=True)
    if request.query_params:
        raise PolicyLifecycleError("UNSUPPORTED_QUERY", "演示接口不接受额外事实或时钟", 422)


router = APIRouter(
    prefix="/api/v1/zhiyu", tags=["知余模拟演示"], dependencies=[Depends(isolated_request)]
)


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Environment(DemoModel):
    simulation: Literal[True] = True
    environment_id: str
    epoch_id: UUID


class IntentPreset(DemoModel):
    id: Literal["EMERGENCY", "TRAVEL"]
    title: str
    text: str


class Presets(DemoModel):
    simulation: Literal[True] = True
    preset_version: Literal["zhiyu-v1"] = "zhiyu-v1"
    intents: list[IntentPreset]
    income_cents: int = INCOME_CENTS


class Activity(DemoModel):
    id: str
    at: datetime
    intent: str
    authorization: str
    decision: str
    amount_cents: int | None
    status: str
    action_id: UUID | None = None
    scenario: Scenario | None = None
    goal_id: UUID | None = None


class State(Environment):
    dashboard: DashboardResponse
    goals: list[GoalView]
    actions: list[ActionResponse]
    activity: list[Activity]
    income_received: bool


class GoalRequest(DemoModel):
    policy_id: UUID
    expected_version_id: UUID


class EpochRequest(DemoModel):
    expected_epoch_id: UUID


class PrepareRequest(EpochRequest):
    goal_id: UUID
    scenario: Scenario


class EmptyRequest(DemoModel):
    pass


def action_key(epoch_id: UUID, goal_id: UUID, scenario: Scenario) -> str:
    return f"{PREFIX}{epoch_id}:{goal_id}:{scenario}"


def _stored_key(key: str) -> str:
    return "action:" + configuration_hash({"key": key})


def _selected_goal(session: Session, user_id: UUID, goal_id: UUID) -> Goal:
    goal = session.get(Goal, goal_id)
    if goal is None or goal.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "本轮目标不存在", 404)
    return goal


def _registered_action(
    session: Session, user_id: UUID, action_id: UUID, epoch_id: UUID
) -> tuple[ActionPlan, Scenario]:
    row = session.get(ActionPlan, action_id)
    if row is None or row.user_id != user_id or row.goal_id is None:
        raise PolicyLifecycleError("NOT_FOUND", "本轮原操作不存在", 404)
    for scenario in ("SAFE", "REVOKED", "RESPONSE_LOSS"):
        if row.idempotency_key == _stored_key(action_key(epoch_id, row.goal_id, scenario)):
            return row, scenario
    raise PolicyLifecycleError("DEMO_ACTION_NOT_REGISTERED", "仅允许本轮服务端预置原操作", 403)


@router.get("/environment", response_model=Environment)
def environment(
    session: SessionDependency, user: DemoUserDependency, engine: EngineDependency
) -> Environment:
    return Environment(
        environment_id=require_zhiyu_engine(engine), epoch_id=_epoch(session, user.id)
    )


@router.get("/presets", response_model=Presets)
def presets() -> Presets:
    return Presets(
        intents=[
            IntentPreset(id="EMERGENCY", title="守住应急金", text="保留3000元应急金"),
            IntentPreset(
                id="TRAVEL",
                title="为旅行储备",
                text="旅行目标1.2万元，截止2027-06-30，每月固定储备1000元。",
            ),
        ]
    )


@router.post("/goal", response_model=GoalResponse)
def goal(
    body: GoalRequest, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> GoalResponse:
    _epoch(session, user.id)
    account = session.scalar(
        select(Account)
        .where(Account.user_id == user.id, Account.account_type == "GOAL")
        .order_by(Account.id)
        .limit(1)
    )
    if account is None:
        raise PolicyLifecycleError("NOT_FOUND", "模拟目标账户尚未初始化", 404)
    return create_goal_projection(
        session, user.id, body.policy_id, body.expected_version_id, account.id, now
    )


@router.post("/income", response_model=ExternalFactResult)
def income(
    body: EpochRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> ExternalFactResult:
    with audit_command_guard(engine, user.id):
        with Session(engine) as session, session.begin():
            transaction_gate(session, user.id)
            epoch_id = _epoch(session, user.id, body.expected_epoch_id)
            key = f"{PREFIX}{epoch_id}:income"
            previous = session.scalar(
                select(ExternalBankFact).where(
                    ExternalBankFact.user_id == user.id, ExternalBankFact.idempotency_key == key
                )
            )
            if previous is not None:
                request = validate_external_fact_original(
                    {
                        column.name: getattr(previous, column.name)
                        for column in previous.__table__.columns
                    }
                )
            else:
                account = session.scalar(
                    select(Account)
                    .where(Account.user_id == user.id, Account.account_type == "CASH")
                    .order_by(Account.id)
                    .limit(1)
                )
                if account is None:
                    raise PolicyLifecycleError("NOT_FOUND", "模拟活期账户尚未初始化", 404)
                request = ExternalFactRequest(
                    user_id=user.id,
                    idempotency_key=key,
                    external_ref=key,
                    kind="INCOME",
                    account_id=account.id,
                    amount_cents=INCOME_CENTS,
                    counterparty_ref="payroll",
                    occurred_at=now,
                )
        return ingest_external_fact(engine, user.id, request, now)


@router.post("/actions/prepare", response_model=ActionResponse)
def prepare(
    body: PrepareRequest, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> ActionResponse:
    with audit_command_guard(engine, user.id):
        with Session(engine) as session:
            epoch_id = _epoch(session, user.id, body.expected_epoch_id)
        try:
            with Session(engine) as session:
                _selected_goal(session, user.id, body.goal_id)
                unresolved = session.scalar(
                    select(ActionPlan.id)
                    .where(
                        ActionPlan.user_id == user.id,
                        ActionPlan.status.in_(["UNKNOWN", "SUBMITTED"]),
                        ActionPlan.idempotency_key
                        != _stored_key(action_key(epoch_id, body.goal_id, body.scenario)),
                    )
                    .limit(1)
                )
                if unresolved is not None:
                    raise PolicyLifecycleError(
                        "ORIGINAL_ACTION_UNRESOLVED",
                        "原操作结果待核实，请先用同一操作身份恢复",
                        409,
                    )
            return prepare_action(
                engine,
                user.id,
                PrepareActionRequest(
                    idempotency_key=action_key(epoch_id, body.goal_id, body.scenario),
                    intent=GoalIntent(kind="allocate_goal", goal_id=body.goal_id),
                ),
                now,
            )
        except PolicyLifecycleError as error:
            # Keep the real refusal and its original reason; this declaration grants no authority.
            with Session(engine) as session, session.begin():
                transaction_gate(session, user.id)
                _epoch(session, user.id, epoch_id)
                _statement(
                    session,
                    user.id,
                    epoch_id,
                    f"ZHIYU_REJECT_{body.scenario}_{body.goal_id}",
                    "command",
                    {
                        "goal_id": str(body.goal_id),
                        "scenario": body.scenario,
                        "code": error.code,
                        "message": error.message,
                    },
                    now,
                )
            raise


@router.post("/actions/{action_id}/execute", response_model=ActionResponse)
def execute(
    action_id: UUID,
    body: EmptyRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ActionResponse:
    with audit_command_guard(engine, user.id):
        with Session(engine) as session:
            epoch_id = _epoch(session, user.id)
            row, scenario = _registered_action(session, user.id, action_id, epoch_id)
            first_submission = row.status in {"PLANNED", "AUTHORIZED"}
        fault = "DROP_BANK_RESPONSE" if scenario == "RESPONSE_LOSS" and first_submission else "NONE"
        try:
            with _fault(fault, "EXECUTE_ACTION", engine, user.id):
                return execute_action(engine, user.id, action_id, now)
        except TimeoutError as error:
            if fault != "DROP_BANK_RESPONSE" or str(error) != "SIMULATED_BANK_RESPONSE_LOST":
                raise
            # The original engine has retained UNKNOWN after its real bank commit.
            # Read that actual state; do not infer success or submit another action.
            with Session(engine) as session:
                return get_action(session, user.id, action_id, now)


@router.get("/state", response_model=State)
def state(
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> State:
    with historical_ledger_scope(session):
        return _state(session, user.id, engine, now)


def _state(session: Session, user_id: UUID, engine: Engine, now: datetime) -> State:
    epoch_id = _epoch(session, user_id)
    goals = list_goals(session, user_id).items
    activity: list[Activity] = []
    for version in session.scalars(
        select(PolicyVersion)
        .where(PolicyVersion.user_id == user_id)
        .order_by(PolicyVersion.created_at)
    ):
        activity.append(
            Activity(
                id=str(version.id),
                at=version.created_at,
                intent=version.configuration.get("name")
                or version.configuration.get("type", "规则"),
                authorization="用户已确认的策略版本",
                decision=version.change_reason,
                amount_cents=None,
                status="CONFIRMED",
            )
        )
    fact = session.scalar(
        select(ExternalBankFact).where(
            ExternalBankFact.user_id == user_id,
            ExternalBankFact.idempotency_key == f"{PREFIX}{epoch_id}:income",
        )
    )
    if fact is not None:
        activity.append(
            Activity(
                id=str(fact.id),
                at=fact.created_at,
                intent="服务端固定收入事件",
                authorization="模拟银行实际收入事实",
                decision="收入到账后才可参与资金规划",
                amount_cents=fact.amount_cents,
                status=fact.bank_status,
            )
        )
    for goal_view in goals:
        original_goal = _selected_goal(session, user_id, goal_view.id)
        activity.append(
            Activity(
                id=str(goal_view.id),
                at=original_goal.created_at,
                intent=f"建立{goal_view.name}目标",
                authorization=str(goal_view.policy_version_id),
                decision="建立目标归属，不移动资金",
                amount_cents=0,
                status="COMPLETED",
            )
        )
    actions: list[ActionResponse] = []
    for row in session.scalars(
        select(ActionPlan).where(ActionPlan.user_id == user_id).order_by(ActionPlan.created_at)
    ):
        try:
            _, scenario = _registered_action(session, user_id, row.id, epoch_id)
        except PolicyLifecycleError:
            continue
        actual = get_action(session, user_id, row.id, now)
        actions.append(actual)
        activity.append(
            Activity(
                id=str(row.id),
                at=row.created_at,
                intent=f"目标储备 · {scenario}",
                authorization=str(actual.effect.policy_version_id),
                decision="；".join(actual.prepared_validation.reasons) or actual.autonomy_level,
                amount_cents=actual.effect.amount_cents,
                status=actual.status,
                action_id=actual.action_id,
                scenario=scenario,
                goal_id=row.goal_id,
            )
        )
    for declaration in session.scalars(
        select(EvidenceItem)
        .where(EvidenceItem.user_id == user_id, EvidenceItem.source_type == "DEMO_EVENT_INTENT")
        .order_by(EvidenceItem.created_at)
    ):
        kind = declaration.content.get("kind", "")
        if (
            not isinstance(kind, str)
            or not kind.startswith("ZHIYU_REJECT_")
            or declaration.content.get("epoch_id") != str(epoch_id)
        ):
            continue
        original = _read_statement(session, user_id, epoch_id, kind, "command", now)
        if original is None:
            continue
        inputs = original.content["inputs"]
        activity.append(
            Activity(
                id=str(original.id),
                at=original.created_at,
                intent="目标储备请求",
                authorization="按当前权限重新核验",
                decision=f"{inputs['code']}：{inputs['message']}",
                amount_cents=None,
                status="REJECTED",
                scenario=inputs["scenario"],
                goal_id=UUID(inputs["goal_id"]),
            )
        )
    return State(
        environment_id=require_zhiyu_engine(engine),
        epoch_id=epoch_id,
        dashboard=get_dashboard(session, user_id, now, pending_limit=10, recovery_limit=1),
        goals=goals,
        actions=actions,
        activity=sorted(activity, key=lambda item: (item.at, item.id), reverse=True),
        income_received=fact is not None
        and fact.bank_status == "SETTLED"
        and fact.projection_status == "PROJECTED",
    )
