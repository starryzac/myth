"""Predefined synthetic inputs; every financial result comes from its original engine."""

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.models import (
    Account,
    ActionPlan,
    AuditEvent,
    DecisionRun,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    PolicyProposal,
    PolicyVersion,
    User,
)
from app.domain.demo_identity import DEMO_USER_ID, DEMO_USER_REF
from app.domain.external_bank_fact_types import (
    ExternalFactRequest,
    ExternalFactResult,
    ExternalProjectionResult,
    ExternalSettlementResult,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.action_contracts import (
    ActionResponse,
    GoalIntent,
    PrepareActionRequest,
    PurchaseIntent,
    RedeemIntent,
)
from app.services.audit_chain import current_audit_epoch
from app.services.boundary import compute_user_boundary, load_boundary_context
from app.services.demo_console_types import (
    PRESET_VERSION,
    DemoCommandView,
    DemoEventPreset,
    DemoEventRequest,
    DemoPolicyChange,
    DemoPresets,
    DemoResetRequest,
    DemoResetResponse,
    DemoState,
    DemoTemplateView,
    EventKind,
    TemplateKind,
)
from app.services.demo_seed import seed_demo
from app.services.execution import execute_action, get_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact, verify_external_facts
from app.services.goal_allocation import preview_goal_allocation
from app.services.goals import create_goal_projection
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from app.services.recovery import get_recovery_run, run_recovery
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

TEMPLATE_KINDS: tuple[TemplateKind, ...] = ("CAR_GOAL", "LIQUID_ASSET", "FIXED_ASSET", "RENT")
EVENT_KINDS: tuple[EventKind, ...] = (
    "SALARY_RECEIVED",
    "CREATE_CAR_GOAL",
    "LARGE_CONSUMPTION",
    "AUTO_REDEEM",
    "FIXED_EARLY_WITHDRAWAL",
    "CHANGE_RENT",
)
_REQUIREMENTS: dict[EventKind, list[TemplateKind]] = {
    "SALARY_RECEIVED": ["CAR_GOAL", "LIQUID_ASSET"],
    "CREATE_CAR_GOAL": ["CAR_GOAL"],
    "LARGE_CONSUMPTION": [],
    "AUTO_REDEEM": [],
    "FIXED_EARLY_WITHDRAWAL": ["FIXED_ASSET"],
    "CHANGE_RENT": ["RENT"],
}


def _error(code: str, message: str, status: int = 409) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, status)


def _now(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("INVALID_CLOCK", "服务器时间必须带时区")
    return now.astimezone(UTC)


def _user(session: Session, user_id: UUID, *, lock: bool = False) -> None:
    if user_id != DEMO_USER_ID:
        raise _error("NOT_FOUND", "演示控制台仅用于保留的合成用户", 404)
    query = select(User).where(User.id == user_id)
    if lock:
        transaction_gate(session, user_id)
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None or row.external_ref != DEMO_USER_REF or not row.is_simulated:
        raise _error("NOT_FOUND", "合成演示用户不存在", 404)


def _epoch(session: Session, user_id: UUID, expected: UUID | None = None) -> UUID:
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN":
        raise _error("DEMO_RESET_REQUIRED", "请先明确重置演示初态")
    if expected is not None and epoch.id != expected:
        raise _error("STALE_DEMO_EPOCH", "演示轮次已经变化，请刷新控制台")
    return epoch.id


def _identity(epoch_id: UUID, kind: str, phase: str) -> UUID:
    return uuid5(epoch_id, f"{PRESET_VERSION}:{kind}:{phase}")


def _key(command_id: UUID, phase: str) -> str:
    return f"demo:{command_id}:{phase}"


def _source_ref(epoch_id: UUID, kind: str, phase: str) -> str:
    return f"{PRESET_VERSION}:{epoch_id}:{kind}:{phase}"


def _read_statement(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    kind: str,
    phase: str,
    now: datetime,
) -> EvidenceItem | None:
    row = session.get(EvidenceItem, _identity(epoch_id, kind, phase))
    if row is None:
        return None
    content = row.content
    required = {
        "simulation",
        "preset_version",
        "user_id",
        "epoch_id",
        "kind",
        "phase",
        "admitted_at",
        "inputs",
    }
    if (
        row.user_id != user_id
        or row.source_type != "DEMO_EVENT_INTENT"
        or row.evidence_level != "USER_DECLARED"
        or row.status != "VALID"
        or row.source_ref != _source_ref(epoch_id, kind, phase)
        or row.supersedes_id is not None
        or row.valid_to is not None
        or set(content) != required
        or content.get("simulation") is not True
        or content.get("preset_version") != PRESET_VERSION
        or content.get("user_id") != str(user_id)
        or content.get("epoch_id") != str(epoch_id)
        or content.get("kind") != kind
        or content.get("phase") != phase
        or content.get("admitted_at") != row.created_at.isoformat()
        or row.observed_at != row.created_at
        or row.valid_from != row.created_at
        or row.created_at > now
        or not isinstance(content.get("inputs"), dict)
        or configuration_hash(content) != row.content_hash
    ):
        raise _error("INVALID_DEMO_ORIGINAL", "原演示声明的身份、时钟或内容校验失败")
    return row


def _statement(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    kind: str,
    phase: str,
    inputs: dict[str, Any],
    now: datetime,
) -> EvidenceItem:
    previous = _read_statement(session, user_id, epoch_id, kind, phase, now)
    if previous is not None:
        return previous
    content = {
        "simulation": True,
        "preset_version": PRESET_VERSION,
        "user_id": str(user_id),
        "epoch_id": str(epoch_id),
        "kind": kind,
        "phase": phase,
        "admitted_at": now.isoformat(),
        "inputs": inputs,
    }
    row = EvidenceItem(
        id=_identity(epoch_id, kind, phase),
        user_id=user_id,
        created_at=now,
        evidence_level="USER_DECLARED",
        source_type="DEMO_EVENT_INTENT",
        source_ref=_source_ref(epoch_id, kind, phase),
        content=content,
        content_hash=configuration_hash(content),
        observed_at=now,
        valid_from=now,
        valid_to=None,
        supersedes_id=None,
        status="VALID",
    )
    session.add(row)
    session.flush()
    return row


def template_configuration(kind: TemplateKind) -> dict[str, Any]:
    """Complete fixed declaration shown before any proposal confirmation."""
    if kind == "CAR_GOAL":
        raw: dict[str, Any] = {
            "type": "goal_saving",
            "name": "演示：买车目标",
            "target_cents": 3_000_000,
            "deadline": "2027-10-31",
            "monthly_contribution": {
                "min_cents": 10_000,
                "target_cents": 200_000,
                "max_cents": 200_000,
            },
            "priority": {
                "importance": 60,
                "minimum_cents": 0,
                "reducible": True,
                "deferrable": True,
            },
        }
    elif kind == "RENT":
        raw = {
            "type": "recurring_obligation",
            "name": "演示：房租保护",
            "payee_id": "demo-landlord",
            "amount_rule": {"kind": "exact", "amount_cents": 150_000},
            "due_day": 15,
            "prepare_days_before": 5,
            "auto_execute": False,
            "priority": {
                "importance": 100,
                "minimum_cents": 150_000,
                "reducible": False,
                "deferrable": False,
            },
        }
    else:
        fixed = kind == "FIXED_ASSET"
        raw = {
            "type": "asset_authorization",
            "name": "演示：固定期限与损失需确认" if fixed else "演示：无损自动恢复",
            "scope": "general_idle_funds",
            "allowed_asset_classes": ["FIXED_DEPOSIT" if fixed else "CASH_MGMT_T0"],
            "max_auto_managed_cents": 50_000 if fixed else 250_000,
            "single_action_cap_cents": 50_000 if fixed else 250_000,
            "max_redemption_delay_days": 0,
            "max_lock_days": 30 if fixed else 0,
            "max_principal_risk_level": 0,
            "allow_auto_recovery_without_penalty": True,
            "allow_early_withdrawal_with_penalty": fixed,
        }
    return validate_configuration(raw)


def _template_view(
    session: Session,
    user_id: UUID,
    epoch_id: UUID | None,
    kind: TemplateKind,
    now: datetime,
) -> DemoTemplateView:
    config = template_configuration(kind)
    proposal = (
        session.get(PolicyProposal, _identity(epoch_id, kind, "proposal")) if epoch_id else None
    )
    evidence = (
        _read_statement(session, user_id, epoch_id, kind, "template", now) if epoch_id else None
    )
    if proposal is not None and (
        proposal.user_id != user_id
        or proposal.source_type != "DEMO_TEMPLATE"
        or proposal.compiler_version != PRESET_VERSION
        or proposal.idempotency_key != _source_ref(cast(UUID, epoch_id), kind, "proposal")
        or evidence is None
        or proposal.evidence_ids != [str(evidence.id)]
        or proposal.proposed_configuration != config
        or evidence.content["inputs"]
        != {"configuration": config, "configuration_hash": configuration_hash(config)}
    ):
        raise _error("INVALID_DEMO_ORIGINAL", "预定义提案与原声明不一致")
    if (proposal is None) != (evidence is None):
        raise _error("INVALID_DEMO_ORIGINAL", "预定义提案或原声明缺失")
    return DemoTemplateView(
        kind=kind,
        title=config["name"],
        configuration=config,
        configuration_hash=configuration_hash(config),
        proposal_id=proposal.id if proposal else None,
        evidence_id=evidence.id if evidence else None,
        confirmed_policy_id=proposal.confirmed_policy_id if proposal else None,
        status=proposal.status if proposal else "NOT_PREPARED",
    )


def get_demo_presets(session: Session, user_id: UUID, now: datetime) -> DemoPresets:
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    titles = [
        "工资到账",
        "新建买车目标",
        "用户大额消费",
        "自动赎回",
        "定存提前支取需确认",
        "修改房租策略",
    ]
    descriptions = [
        "注入2000元真实模拟工资；已确认目标与无损资产权限后才尝试实际分配和申购。",
        "先审核并确认完整目标模板，再建立零归属目标；不补造或移动已有本金。",
        "核验真实边界和可消费来源后，消费正边界余量再加300元；原银行事实与投影分别保留。",
        "仅按当前完整来源和原无损自动权限运行恢复计划。",
        "先真实购买授权定存，再取得原合同报价；有损支取必须另行明确确认原经济效果。",
        "房租从1500元改为1800元，展示完整变更后由用户明确确认；在途保持原身份。",
    ]
    return DemoPresets(
        templates=[
            _template_view(session, user_id, epoch.id if epoch else None, kind, _now(now))
            for kind in TEMPLATE_KINDS
        ],
        events=[
            DemoEventPreset(
                event_kind=kind,
                title=title,
                description=description,
                required_templates=_REQUIREMENTS[kind],
                amount_cents=200_000 if kind == "SALARY_RECEIVED" else None,
            )
            for kind, title, description in zip(EVENT_KINDS, titles, descriptions, strict=True)
        ]
        + [
            DemoEventPreset(
                event_kind="RESET",
                title="恢复演示初始状态",
                description="明确确认后封存原轮次并重置合成业务数据；永久审计历史保留。",
            )
        ],
    )


def prepare_demo_template(
    engine: Engine,
    user_id: UUID,
    kind: TemplateKind,
    expected_epoch_id: UUID,
    now: datetime,
) -> DemoTemplateView:
    now = _now(now)
    if kind not in TEMPLATE_KINDS:
        raise _error("NOT_FOUND", "预定义模板不存在", 404)
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        _user(session, user_id, lock=True)
        epoch_id = _epoch(session, user_id, expected_epoch_id)
        previous = _template_view(session, user_id, epoch_id, kind, now)
        if previous.proposal_id is not None:
            return previous
        config = template_configuration(kind)
        statement = _statement(
            session,
            user_id,
            epoch_id,
            kind,
            "template",
            {"configuration": config, "configuration_hash": configuration_hash(config)},
            now,
        )
        session.add(
            PolicyProposal(
                id=_identity(epoch_id, kind, "proposal"),
                user_id=user_id,
                created_at=now,
                source_type="DEMO_TEMPLATE",
                source_text=f"用户选择预定义合成模板：{config['name']}；完整配置须另行明确确认。",
                compiler_version=PRESET_VERSION,
                proposed_configuration=config,
                evidence_ids=[str(statement.id)],
                status="PROPOSED",
                confirmed_policy_id=None,
                idempotency_key=_source_ref(epoch_id, kind, "proposal"),
            )
        )
        session.flush()
        return _template_view(session, user_id, epoch_id, kind, now)


def _authority(
    session: Session, user_id: UUID, epoch_id: UUID, kind: TemplateKind, now: datetime
) -> PolicyVersion | None:
    view = _template_view(session, user_id, epoch_id, kind, now)
    if view.status != "CONFIRMED" or view.confirmed_policy_id is None:
        return None
    version = session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.user_id == user_id, PolicyVersion.policy_id == view.confirmed_policy_id
        )
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    return version if version and is_version_authorized(session, user_id, version.id, now) else None


def _action(
    session: Session, user_id: UUID, command_id: UUID, phase: str, now: datetime
) -> ActionResponse | None:
    key = "action:" + configuration_hash({"key": _key(command_id, phase)})
    row = session.scalar(
        select(ActionPlan).where(ActionPlan.user_id == user_id, ActionPlan.idempotency_key == key)
    )
    return get_action(session, user_id, row.id, now) if row else None


def _fact(
    session: Session, user_id: UUID, command: EvidenceItem, now: datetime
) -> ExternalFactResult | None:
    stage = _read_statement(
        session, user_id, UUID(command.content["epoch_id"]), command.content["kind"], "bank", now
    )
    if stage is None:
        original = session.scalar(
            select(ExternalBankFact.id).where(
                ExternalBankFact.user_id == user_id,
                ExternalBankFact.idempotency_key == _key(command.id, "bank"),
            )
        )
        if original is not None:
            raise _error("INVALID_DEMO_ORIGINAL", "银行事实的原演示输入声明缺失")
        return None
    request = ExternalFactRequest.model_validate_json(
        json.dumps(stage.content["inputs"]["request"])
    )
    if (
        request.user_id != user_id
        or request.idempotency_key != _key(command.id, "bank")
        or request.external_ref != _key(command.id, "bank")
        or request.occurred_at != stage.created_at
    ):
        raise _error("INVALID_DEMO_ORIGINAL", "原预定义银行事实声明不一致")
    row = session.scalar(
        select(ExternalBankFact).where(
            ExternalBankFact.user_id == user_id,
            ExternalBankFact.idempotency_key == request.idempotency_key,
        )
    )
    if row is None:
        return None
    if row.request != request.model_dump(mode="json"):
        # Canonical bank request excludes first-key metadata; bind the original request below.
        from app.domain.external_bank_fact import external_json, semantic_request

        if row.request != external_json(semantic_request(request)):
            raise _error("INVALID_DEMO_ORIGINAL", "实际银行事实与原预定义声明不一致")
    verify_external_facts(session, user_id, now, require_projected=False)
    settlement = (
        ExternalSettlementResult.model_validate_json(row.bank_result_canonical_text)
        if row.bank_result_canonical_text
        else None
    )
    projection = (
        ExternalProjectionResult.model_validate_json(row.projection_result_canonical_text)
        if row.projection_result_canonical_text
        else None
    )
    return ExternalFactResult(
        external_fact_id=row.id,
        bank_status=cast(Any, row.bank_status),
        projection_status=cast(Any, row.projection_status),
        economic_posting_ids=settlement.economic_posting_ids if settlement else (),
        transaction_id=projection.transaction_id if projection else None,
    )


def _view(session: Session, user_id: UUID, command: EvidenceItem, now: datetime) -> DemoCommandView:
    epoch_id, kind = UUID(command.content["epoch_id"]), cast(EventKind, command.content["kind"])
    base: dict[str, Any] = {
        "command_id": command.id,
        "epoch_id": epoch_id,
        "event_kind": kind,
        "admitted_at": command.created_at,
        "status": "PENDING",
        "message": "原事件尚待继续",
    }
    templates = [_template_view(session, user_id, epoch_id, k, now) for k in _REQUIREMENTS[kind]]
    base["proposal_ids"] = [v.proposal_id for v in templates if v.proposal_id]
    phases = {
        "SALARY_RECEIVED": ("goal", "liquid"),
        "FIXED_EARLY_WITHDRAWAL": ("fixed", "early"),
    }.get(kind, ())
    actions = [a for p in phases if (a := _action(session, user_id, command.id, p, now))]
    base["actions"] = actions
    fact = (
        _fact(session, user_id, command, now)
        if kind in {"SALARY_RECEIVED", "LARGE_CONSUMPTION"}
        else None
    )
    base["fact"] = fact
    goal_template = _template_view(session, user_id, epoch_id, "CAR_GOAL", now)
    goal = (
        session.scalar(
            select(Goal).where(
                Goal.user_id == user_id, Goal.policy_id == goal_template.confirmed_policy_id
            )
        )
        if goal_template.confirmed_policy_id
        else None
    )
    base["goal_id"] = goal.id if goal else None
    if kind == "AUTO_REDEEM":
        key = "recovery:" + configuration_hash({"key": _key(command.id, "recovery")})
        run = session.scalar(
            select(DecisionRun).where(
                DecisionRun.user_id == user_id, DecisionRun.idempotency_key == key
            )
        )
        if run:
            recovery = get_recovery_run(session, user_id, run.id, now)
            base["recovery"] = recovery
            status = "BLOCKED"
            if recovery.status in {"RECONCILIATION_REQUIRED", "UNKNOWN"}:
                status = "UNKNOWN"
            elif recovery.status == "PENDING_SETTLEMENT":
                status = "PENDING"
            elif (
                recovery.status in {"RECOVERED", "NO_RECOVERY_NEEDED"}
                and recovery.actual_boundary.status == "READY"
            ):
                status = "COMPLETED"
            base.update(
                status=status,
                message="原恢复计划与回执已读取；结果见实际计划",
            )
    elif kind == "CHANGE_RENT":
        change = _read_statement(session, user_id, epoch_id, kind, "change", now)
        if change:
            request = DemoPolicyChange.model_validate_json(json.dumps(change.content["inputs"]))
            versions = session.scalars(
                select(PolicyVersion).where(
                    PolicyVersion.user_id == user_id, PolicyVersion.policy_id == request.policy_id
                )
            ).all()
            matches = [
                v
                for v in versions
                if v.confirmation.get("request_key") == "change:" + request.idempotency_key
            ]
            if matches:
                if len(matches) != 1 or matches[0].configuration != request.configuration:
                    raise _error("INVALID_DEMO_ORIGINAL", "原房租变更回执与声明不一致")
                base.update(status="COMPLETED", message="原房租版本变更已完成")
            else:
                base.update(
                    status="WAITING_POLICY_CHANGE",
                    message="请审核完整房租变更并明确确认",
                    policy_change=request,
                )
    elif kind == "CREATE_CAR_GOAL" and goal:
        base.update(status="COMPLETED", message="实际零归属目标已经建立")
    elif fact and (fact.bank_status != "SETTLED" or fact.projection_status != "PROJECTED"):
        base.update(status="UNKNOWN", message="保留原银行身份与结果，应用投影尚待核验")
    elif any(a.status == "UNKNOWN" or a.bank_status == "UNKNOWN" for a in actions):
        base.update(status="UNKNOWN", message="原动作结果未知，保留原键与占用")
    elif any(a.status in {"INVALIDATED", "CANCELLED", "EXPIRED", "FAILED"} for a in actions):
        base.update(status="BLOCKED", message="原动作已失效，不能替换原身份或复用旧确认")
    elif any(a.autonomy_level == "ASK_ONCE" and a.status == "PLANNED" for a in actions):
        base.update(
            status="WAITING_ACTION_CONFIRMATION", message="请明确确认原动作的本金、净额、费用与损失"
        )
    elif (kind == "LARGE_CONSUMPTION" and fact) or (
        len(actions) == 2
        and all(a.receipt is not None for a in actions)
        and (kind == "FIXED_EARLY_WITHDRAWAL" or fact is not None)
    ):
        base.update(status="COMPLETED", message="原银行事实及实际动作回执已经完成")
    elif any(_authority(session, user_id, epoch_id, k, now) is None for k in _REQUIREMENTS[kind]):
        base.update(status="WAITING_TEMPLATE", message="请先准备并明确确认所需完整策略模板")
    return DemoCommandView(**base)


def get_demo_command(
    session: Session, user_id: UUID, command_id: UUID, now: datetime
) -> DemoCommandView:
    now, epoch_id = _now(now), _epoch(session, user_id)
    for kind in EVENT_KINDS:
        if _identity(epoch_id, kind, "command") == command_id:
            row = _read_statement(session, user_id, epoch_id, kind, "command", now)
            if row is not None:
                return _view(session, user_id, row, now)
    raise _error("NOT_FOUND", "当前轮次的演示命令不存在", 404)


def get_demo_state(session: Session, user_id: UUID, now: datetime) -> DemoState:
    now = _now(now)
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    epoch_id = epoch.id if epoch and epoch.status == "OPEN" else None
    commands = []
    if epoch_id:
        for kind in EVENT_KINDS:
            command = _read_statement(session, user_id, epoch_id, kind, "command", now)
            if command:
                commands.append(_view(session, user_id, command, now))
    return DemoState(
        epoch_id=epoch_id,
        available=epoch_id is not None,
        reason=None if epoch_id else "请明确重置合成演示初态",
        templates=[_template_view(session, user_id, epoch_id, k, now) for k in TEMPLATE_KINDS],
        commands=commands,
    )


def _advance_action(
    engine: Engine, user_id: UUID, command_id: UUID, phase: str, intent: Any, now: datetime
) -> ActionResponse:
    action = prepare_action(
        engine,
        user_id,
        PrepareActionRequest(idempotency_key=_key(command_id, phase), intent=intent),
        now,
    )
    if action.receipt is None and (
        action.autonomy_level == "AUTO_EXECUTE" or action.status == "AUTHORIZED"
    ):
        return execute_action(engine, user_id, action.action_id, now)
    return action


def run_demo_event(
    engine: Engine, user_id: UUID, body: DemoEventRequest, now: datetime
) -> DemoCommandView:
    now = _now(now)
    with audit_command_guard(engine, user_id):
        with Session(engine) as session, session.begin():
            _user(session, user_id, lock=True)
            epoch_id = _epoch(session, user_id, body.expected_epoch_id)
            command = _statement(session, user_id, epoch_id, body.event_kind, "command", {}, now)
            command_id = command.id
            view = _view(session, user_id, command, now)
            if view.status == "COMPLETED":
                return view
            authorities = {
                k: _authority(session, user_id, epoch_id, k, now)
                for k in _REQUIREMENTS[body.event_kind]
            }
            if any(v is None for v in authorities.values()):
                return view
            car_authority = authorities.get("CAR_GOAL")
            goal = (
                session.scalar(
                    select(Goal).where(
                        Goal.user_id == user_id, Goal.policy_id == car_authority.policy_id
                    )
                )
                if car_authority is not None
                else None
            )
            if body.event_kind == "CREATE_CAR_GOAL":
                authority = authorities["CAR_GOAL"]
                assert authority is not None
                account = session.scalar(
                    select(Account)
                    .where(Account.user_id == user_id, Account.account_type == "GOAL")
                    .order_by(Account.id)
                    .limit(1)
                )
                if account is None:
                    raise _error("NOT_FOUND", "原合成目标账户不存在", 404)
                create_goal_projection(
                    session, user_id, authority.policy_id, authority.id, account.id, now
                )
            elif body.event_kind in {"SALARY_RECEIVED", "LARGE_CONSUMPTION"}:
                if body.event_kind == "SALARY_RECEIVED" and goal is None:
                    return view.model_copy(
                        update={"status": "BLOCKED", "message": "请先建立已确认的零归属目标"}
                    )
                stage = _read_statement(session, user_id, epoch_id, body.event_kind, "bank", now)
                if stage is None:
                    cash = session.scalar(
                        select(Account)
                        .where(Account.user_id == user_id, Account.account_type == "CASH")
                        .order_by(Account.id)
                        .limit(1)
                    )
                    if cash is None:
                        raise _error("NOT_FOUND", "原合成活期账户不存在", 404)
                    amount = 200_000
                    basis: dict[str, Any] = {"formula": "fixed-salary-200000-v1"}
                    if body.event_kind == "LARGE_CONSUMPTION":
                        boundary = compute_user_boundary(session, user_id, now)
                        margin = boundary.boundary.minimum_margin_cents
                        if (
                            boundary.source_issues
                            or boundary.boundary.status != "READY"
                            or margin is None
                            or margin <= 0
                        ):
                            return view.model_copy(
                                update={
                                    "status": "BLOCKED",
                                    "message": "真实边界或原资金来源不足，不能注入此预定义消费",
                                }
                            )
                        amount = margin + 30_000
                        basis = {
                            "formula": "verified-positive-minimum-margin-plus-30000-v1",
                            "minimum_margin_cents": margin,
                            "boundary_hash": boundary.boundary.boundary_hash,
                            "input_digest": boundary.input_digest,
                            "source_evidence_ids": [
                                str(identifier) for identifier in boundary.source_evidence_ids
                            ],
                        }
                    if amount <= 0:
                        return view.model_copy(
                            update={"status": "BLOCKED", "message": "无正消费金额可注入"}
                        )
                    request = ExternalFactRequest(
                        user_id=user_id,
                        idempotency_key=_key(command_id, "bank"),
                        external_ref=_key(command_id, "bank"),
                        kind="INCOME" if body.event_kind == "SALARY_RECEIVED" else "CONSUMPTION",
                        account_id=cash.id,
                        amount_cents=amount,
                        counterparty_ref="payroll"
                        if body.event_kind == "SALARY_RECEIVED"
                        else "merchant",
                        occurred_at=now,
                    )
                    if body.event_kind == "LARGE_CONSUMPTION":
                        from app.domain.external_bank_fact import plan_consumption
                        from app.services.external_bank_facts import _cash_attribution
                        from app.services.income_ledger import read_income_state

                        ledger = read_income_state(session, user_id, now).ledger
                        context = load_boundary_context(session, user_id, now)
                        attribution = _cash_attribution(
                            session, request, ledger, cash.balance_cents, now
                        )
                        plan = plan_consumption(request, ledger, attribution)
                        if plan.status != "READY" or context.sources.issues:
                            return view.model_copy(
                                update={
                                    "status": "BLOCKED",
                                    "message": "消费超过已证明可用现金，保持原目标与claims",
                                }
                            )
                    stage = _statement(
                        session,
                        user_id,
                        epoch_id,
                        body.event_kind,
                        "bank",
                        {"request": request.model_dump(mode="json"), "basis": basis},
                        now,
                    )
                bank_request = ExternalFactRequest.model_validate_json(
                    json.dumps(stage.content["inputs"]["request"])
                )
            elif body.event_kind == "CHANGE_RENT":
                version = authorities["RENT"]
                assert version is not None
                config = {
                    **version.configuration,
                    "amount_rule": {"kind": "exact", "amount_cents": 180_000},
                    "priority": {**version.configuration["priority"], "minimum_cents": 180_000},
                }
                change = DemoPolicyChange(
                    policy_id=version.policy_id,
                    expected_version_id=version.id,
                    configuration=validate_configuration(config),
                    reviewed_hash=configuration_hash(validate_configuration(config)),
                    reason="用户选择预定义房租调整：1500元改1800元",
                    idempotency_key=_key(command_id, "change"),
                )
                _statement(
                    session,
                    user_id,
                    epoch_id,
                    body.event_kind,
                    "change",
                    change.model_dump(mode="json"),
                    now,
                )
        try:
            if body.event_kind in {"SALARY_RECEIVED", "LARGE_CONSUMPTION"}:
                bank_result = ingest_external_fact(engine, user_id, bank_request, now)
                if (
                    body.event_kind == "SALARY_RECEIVED"
                    and bank_result.projection_status == "PROJECTED"
                ):
                    assert goal is not None
                    with Session(engine) as session:
                        previous = _action(session, user_id, command_id, "goal", now)
                        suggested = (
                            preview_goal_allocation(
                                session, user_id, goal.id, now
                            ).allocation.suggested_cents
                            if previous is None
                            else previous.effect.amount_cents
                        )
                    if suggested:
                        allocation = _advance_action(
                            engine,
                            user_id,
                            command_id,
                            "goal",
                            GoalIntent(kind="allocate_goal", goal_id=goal.id),
                            now,
                        )
                        if allocation.receipt is None:
                            with Session(engine) as session:
                                return get_demo_command(session, user_id, command_id, now)
                    liquid = authorities["LIQUID_ASSET"]
                    assert liquid is not None
                    _advance_action(
                        engine,
                        user_id,
                        command_id,
                        "liquid",
                        PurchaseIntent(kind="purchase_asset", policy_id=liquid.policy_id),
                        now,
                    )
            elif body.event_kind == "AUTO_REDEEM":
                run_recovery(engine, user_id, _key(command_id, "recovery"), now)
            elif body.event_kind == "FIXED_EARLY_WITHDRAWAL":
                fixed = authorities["FIXED_ASSET"]
                assert fixed is not None
                purchased = _advance_action(
                    engine,
                    user_id,
                    command_id,
                    "fixed",
                    PurchaseIntent(kind="purchase_asset", policy_id=fixed.policy_id),
                    now,
                )
                if purchased.receipt is not None:
                    with Session(engine) as session:
                        early = _action(session, user_id, command_id, "early", now)
                    if early is None:
                        from app.services.simulated_redemption_quote import issue_fixed_early_quote

                        assert purchased.effect.position_id is not None
                        issue_fixed_early_quote(engine, user_id, purchased.effect.position_id, now)
                    _advance_action(
                        engine,
                        user_id,
                        command_id,
                        "early",
                        RedeemIntent(
                            kind="redeem_asset",
                            position_id=cast(UUID, purchased.effect.position_id),
                        ),
                        now,
                    )
        except PolicyLifecycleError as error:
            with Session(engine) as session:
                current = get_demo_command(session, user_id, command_id, now)
            if current.status in {"UNKNOWN", "WAITING_ACTION_CONFIRMATION"}:
                return current
            return current.model_copy(
                update={"status": "BLOCKED", "message": error.code + "：" + error.message}
            )
        with Session(engine) as session:
            return get_demo_command(session, user_id, command_id, now)


def reset_demo(
    engine: Engine, user_id: UUID, body: DemoResetRequest, now: datetime
) -> DemoResetResponse:
    _now(now)
    # No shared command guard: seed_demo acquires the same reset gate exclusively.
    with Session(engine) as session:
        _user(session, user_id)
    summary = seed_demo(
        engine,
        reset_key=body.reset_key,
        reason="SYNTHETIC_DEMO_RESET",
        principal="demo-console",
        expected_epoch_id=body.expected_epoch_id,
        check_expected_epoch=True,
    )
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        current = current_audit_epoch(session, user_id)
        reset = session.scalar(
            select(AuditEvent).where(
                AuditEvent.user_id == user_id,
                AuditEvent.event_type == "EPOCH_STARTED",
                AuditEvent.payload["epoch_transition"]["reset_key"].astext == body.reset_key,
            )
        )
        if current is None or reset is None or reset.epoch_id is None:
            raise _error("INVALID_DEMO_ORIGINAL", "重置后的原轮次回执缺失")
        return DemoResetResponse(
            reset_key=body.reset_key,
            reset_epoch_id=reset.epoch_id,
            epoch_id=current.id,
            seed_summary=summary.model_dump(mode="json"),
        )
