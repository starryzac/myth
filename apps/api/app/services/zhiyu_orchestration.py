"""Bounded, durable single-goal orchestration over the original execution engine.

Control and operation records are immutable EvidenceItems. No new bank executor,
user impersonation or cross-request authority cache is introduced.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import Account, ActionPlan, AuditEvent, EvidenceItem, ExternalBankFact, Goal
from app.domain.external_bank_fact import validate_external_fact_original
from app.domain.policy_compiler import CompileContext, compile_policy
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.action_contracts import ActionResponse, GoalIntent, PrepareActionRequest
from app.services.demo_console import _epoch
from app.services.execution import execute_action, get_action, prepare_action
from app.services.goal_allocation import preview_goal_allocation
from app.services.goals import create_goal_projection
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    confirm_proposal,
    is_version_authorized,
)
from app.services.zhiyu_autonomy_validity import grant_dates_active
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MARKER = "zhiyu-next-v1"
KINDS = {"AUTH", "CONTROL", "OPERATION", "EVENT", "RESULT", "BINDING"}
_held: ContextVar[frozenset[tuple[str, UUID]]] = ContextVar("zhiyu_next_gates", default=frozenset())


@contextmanager
def serial_user(engine: Engine, user_id: UUID) -> Iterator[None]:
    """Cross-process gate shared by pause, acceptance and recovery in this database."""
    require_zhiyu_next_engine(engine)
    identity = (str(engine.url.database), user_id)
    if identity in _held.get():
        yield
        return
    key = int.from_bytes(uuid5(user_id, MARKER).bytes[:8], "big", signed=True)
    with audit_command_guard(engine, user_id), engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        connection.commit()
        token = _held.set(_held.get() | {identity})
        try:
            yield
        finally:
            _held.reset(token)
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()


def _audit_healthy(session: Session, user_id: UUID) -> None:
    from app.services.audit_chain import verify_audit_chain

    epoch_id = _epoch(session, user_id)
    audit = verify_audit_chain(session, user_id, epoch_id)
    if audit.status != "VALID":
        raise PolicyLifecycleError("AUTONOMY_AUDIT_INVALID", "自动安排原件或审计无法核验", 409)


def _valid(session: Session, row: EvidenceItem, user_id: UUID, epoch_id: UUID) -> dict[str, Any]:
    data = row.content
    kind = row.source_type.removeprefix("ZHIYU_NEXT_")
    key = data.get("key")
    registered = session.scalar(
        select(AuditEvent).where(
            AuditEvent.user_id == user_id,
            AuditEvent.epoch_id == epoch_id,
            AuditEvent.event_type == "EXTENSION_RECORD_CREATED",
            AuditEvent.aggregate_id == row.id,
        )
    )
    if (
        kind not in KINDS
        or not isinstance(key, str)
        or row.id != uuid5(epoch_id, f"{MARKER}:{kind}:{key}")
        or row.source_ref
        != (f"{session.get_bind().engine.url.database}:{kind}:{configuration_hash({'key': key})}")
        or row.evidence_level
        != ("USER_CONFIRMED_POLICY" if kind in {"AUTH", "CONTROL"} else "USER_DECLARED")
        or registered is None
        or not any(
            anchor.get("kind") == "EVIDENCE_CONTENT"
            and anchor.get("reference_id") == str(row.id)
            and anchor.get("digest") == row.content_hash
            for anchor in registered.payload.get("anchors", [])
        )
        or row.user_id != user_id
        or row.status != "VALID"
        or configuration_hash(data) != row.content_hash
        or data.get("environment_id") != row_source_environment(row)
        or data.get("user_id") != str(user_id)
        or data.get("epoch_id") != str(epoch_id)
        or data.get("protocol") != MARKER
    ):
        raise PolicyLifecycleError("INVALID_AUTONOMY_RECORD", "自动安排原记录无法核验", 409)
    payload = data.get("payload", {})
    if kind in {"AUTH", "CONTROL"} and (
        payload.get("actor") != "USER" or payload.get("accepted") is not True
    ):
        raise PolicyLifecycleError("INVALID_AUTONOMY_RECORD", "持续授权未绑定真实用户确认", 409)
    if kind == "CONTROL" and (
        payload.get("state") not in {"ACTIVE", "PAUSED"}
        or type(payload.get("revision")) is not int
        or payload["revision"] < 1
    ):
        raise PolicyLifecycleError("INVALID_AUTONOMY_RECORD", "持续授权控制版本无法核验", 409)
    return data


def row_source_environment(row: EvidenceItem) -> str:
    # The name is deliberately embedded in the immutable source reference too.
    return row.source_ref.split(":", 1)[0]


def records(session: Session, user_id: UUID, kind: str) -> list[EvidenceItem]:
    assert kind in KINDS
    epoch_id = _epoch(session, user_id)
    result = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type == "ZHIYU_NEXT_" + kind,
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
        )
    )
    for row in result:
        _valid(session, row, user_id, epoch_id)
        if row_source_environment(row) != session.get_bind().engine.url.database:
            raise PolicyLifecycleError("ENVIRONMENT_MISMATCH", "自动安排环境不匹配", 409)
    return result


def store(
    session: Session,
    user_id: UUID,
    kind: str,
    key: str,
    payload: dict[str, Any],
    now: datetime,
    *,
    confirmed: bool = False,
) -> EvidenceItem:
    assert kind in KINDS
    epoch_id = _epoch(session, user_id)
    environment = session.get_bind().engine.url.database
    identity = uuid5(epoch_id, f"{MARKER}:{kind}:{key}")
    old = session.get(EvidenceItem, identity)
    if old is not None:
        original = _valid(session, old, user_id, epoch_id)
        if original["payload"] != payload:
            raise PolicyLifecycleError("REQUEST_CONFLICT", "同一原请求内容发生变化", 409)
        return old
    content = {
        "protocol": MARKER,
        "simulation": True,
        "environment_id": environment,
        "user_id": str(user_id),
        "epoch_id": str(epoch_id),
        "key": key,
        "payload": payload,
    }
    row = EvidenceItem(
        id=identity,
        user_id=user_id,
        created_at=now,
        observed_at=now,
        valid_from=now,
        evidence_level="USER_CONFIRMED_POLICY" if confirmed else "USER_DECLARED",
        source_type="ZHIYU_NEXT_" + kind,
        source_ref=f"{environment}:{kind}:{configuration_hash({'key': key})}",
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
    )
    session.add(row)
    session.flush()
    from app.services.audit_recording import _record

    _record(
        session,
        user_id=user_id,
        event_type="EXTENSION_RECORD_CREATED",
        aggregate_type="EVIDENCE",
        aggregate_id=row.id,
        correlation_id=row.id,
        correlation_kind="EVIDENCE",
        occurred_at=now,
        observed_at=now,
        subjects=[("EVIDENCE", row.id, "AFTER", None)],
        fact_key=f"EXTENSION_RECORD_CREATED:{row.id}",
        anchor_specs=[("EVIDENCE_CONTENT", row.id, row.content_hash, "configuration-sha256-v1")],
    )
    return row


def operation(session: Session, user_id: UUID, request_id: UUID) -> dict[str, Any] | None:
    epoch_id = _epoch(session, user_id)
    row = session.get(EvidenceItem, uuid5(epoch_id, f"{MARKER}:OPERATION:{request_id}"))
    if row is None:
        return None
    _audit_healthy(session, user_id)
    original = _valid(session, row, user_id, epoch_id)
    return {
        "simulation": True,
        "client_request_id": str(request_id),
        "status": original["payload"].get("operation_status", "COMPLETED"),
        "environment_id": original["environment_id"],
        "epoch_id": original["epoch_id"],
        "result": original["payload"]["result"],
    }


def remember_operation(
    session: Session,
    user_id: UUID,
    request_id: UUID,
    request: dict[str, Any],
    result: dict[str, Any],
    now: datetime,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"request": request, "result": result}
    if context is not None:
        payload["server_context"] = context
    store(session, user_id, "OPERATION", str(request_id), payload, now)


def agent_context(
    session: Session, user_id: UUID, parent_request_id: UUID | None, text_input: str
) -> tuple[str, int]:
    """Join a clarification only to this owner's audited, incomplete original message."""
    if parent_request_id is None:
        return text_input, 1
    parent = operation(session, user_id, parent_request_id)
    if parent is None or parent["status"] != "COMPLETED":
        raise PolicyLifecycleError("AGENT_CONTEXT_NOT_FOUND", "原对话尚无法核实，请保留原定位", 409)
    epoch_id = _epoch(session, user_id)
    row = session.get(EvidenceItem, uuid5(epoch_id, f"{MARKER}:OPERATION:{parent_request_id}"))
    assert row is not None
    payload = row.content["payload"]
    candidate = parent["result"].get("candidate")
    if (
        payload["request"].get("kind") != "AGENT"
        or not isinstance(candidate, dict)
        or candidate.get("can_confirm") is not False
    ):
        raise PolicyLifecycleError(
            "AGENT_CONTEXT_CLOSED", "请发起新需求；该候选已可审阅或对话已结束", 409
        )
    saved = payload.get("server_context", {})
    prior = saved.get("source_text", payload["request"].get("text"))
    count = saved.get("turn_count", 1)
    if not isinstance(prior, str) or type(count) is not int or not 1 <= count < 8:
        raise PolicyLifecycleError(
            "AGENT_CONTEXT_INVALID", "原对话范围无法核实，请重新明确完整需求", 409
        )
    combined = prior + "\n" + text_input
    if len(combined) > 2000:
        raise PolicyLifecycleError("AGENT_CONTEXT_LIMIT", "对话已超过范围，请重新明确完整需求", 422)
    return combined, count + 1


def remember_rejection(
    engine: Engine,
    user_id: UUID,
    request_id: UUID,
    request: dict[str, Any],
    error: PolicyLifecycleError,
    now: datetime,
) -> None:
    # Only transactionally rolled-back authorization/control commands use this.
    # In-flight financial commands must keep their original unresolved locator.
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        if operation(session, user_id, request_id) is not None:
            return
        _audit_healthy(session, user_id)
        store(
            session,
            user_id,
            "OPERATION",
            str(request_id),
            {
                "request": request,
                "operation_status": "REJECTED",
                "result": {
                    "code": error.code,
                    "message": error.message,
                    "status_code": error.status_code,
                },
            },
            now,
        )


def replay_operation(
    session: Session,
    user_id: UUID,
    request_id: UUID,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    original = operation(session, user_id, request_id)
    if original is None:
        return None
    epoch_id = _epoch(session, user_id)
    row = session.get(EvidenceItem, uuid5(epoch_id, f"{MARKER}:OPERATION:{request_id}"))
    assert row is not None
    if row.content["payload"]["request"] != request:
        raise PolicyLifecycleError("REQUEST_CONFLICT", "同一原请求内容发生变化", 409)
    if original["status"] == "REJECTED":
        failure = original["result"]
        raise PolicyLifecycleError(failure["code"], failure["message"], failure["status_code"])
    return dict(original["result"])


def autonomy_state(session: Session, user_id: UUID, now: datetime) -> dict[str, Any]:
    _audit_healthy(session, user_id)
    authorizations = records(session, user_id, "AUTH")
    controls = records(session, user_id, "CONTROL")
    if not authorizations or not controls:
        return {"state": "NOT_CONFIGURED", "pending_count": 0, "last_result": None}
    control = max(controls, key=lambda row: row.content["payload"]["revision"])
    control_data = control.content["payload"]
    auth = next(
        (row for row in authorizations if str(row.id) == control_data["authorization_id"]), None
    )
    if auth is None:
        raise PolicyLifecycleError("INVALID_AUTHORIZATION", "持续授权原件缺失", 409)
    grant = auth.content["payload"]
    state = control_data["state"]
    if state == "ACTIVE" and not _grant_authorized(session, user_id, grant, now):
        state = "PAUSED"
    pending = list(
        session.scalars(
            select(ActionPlan).where(
                ActionPlan.user_id == user_id,
                ActionPlan.status.in_(["UNKNOWN", "SUBMITTED"]),
            )
        )
    )
    results = records(session, user_id, "RESULT")
    return {
        "state": state,
        "authorization_id": str(auth.id),
        "goal_id": grant["goal_id"],
        "summary": grant["summary"],
        "pending_count": len(pending),
        "last_result": results[-1].content["payload"] if results else None,
    }


def _grant_authorized(
    session: Session, user_id: UUID, grant: dict[str, Any], now: datetime
) -> bool:
    goal = session.get(Goal, UUID(grant["goal_id"]))
    return (
        goal is not None
        and goal.user_id == user_id
        and str(goal.policy_id) == grant["policy_id"]
        and str(goal.policy_version_id) == grant["policy_version_id"]
        and grant_dates_active(
            {"deadline": goal.deadline.isoformat(), "valid_until": grant["valid_until"]}, now
        )
        and is_version_authorized(session, user_id, UUID(grant["policy_version_id"]), now)
    )


def _control(
    session: Session,
    user_id: UUID,
    auth_id: UUID,
    state: str,
    request_id: UUID,
    now: datetime,
) -> None:
    controls = records(session, user_id, "CONTROL")
    revision = 1 + max((r.content["payload"]["revision"] for r in controls), default=0)
    store(
        session,
        user_id,
        "CONTROL",
        str(request_id),
        {
            "authorization_id": str(auth_id),
            "state": state,
            "revision": revision,
            "actor": "USER",
            "accepted": True,
        },
        now,
        confirmed=True,
    )


def confirm_authorization(
    engine: Engine,
    user_id: UUID,
    proposal_id: UUID,
    reviewed_hash: str,
    request_id: UUID,
    expected_epoch_id: UUID,
    now: datetime,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    from app.db.models import PolicyProposal

    request = {
        "kind": "CONFIRM",
        "proposal_id": str(proposal_id),
        "reviewed_hash": reviewed_hash,
        "expected_epoch_id": str(expected_epoch_id),
        "accepted": True,
    }
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        _epoch(session, user_id, expected_epoch_id)
        previous = replay_operation(session, user_id, request_id, request)
        if previous is not None:
            return previous
        proposal = session.get(PolicyProposal, proposal_id)
        if proposal is None or proposal.user_id != user_id:
            raise PolicyLifecycleError("NOT_FOUND", "规则候选不存在", 404)
        config = validate_configuration(proposal.proposed_configuration)
        parsed = compile_policy(
            proposal.source_text,
            CompileContext(
                reference_date=now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                timezone="Asia/Shanghai",
            ),
        )
        if parsed.configuration is None or parsed.configuration != config:
            raise PolicyLifecycleError(
                "DECLARATION_REQUIRED",
                "候选金额、日期和范围需与明确原声明一致，请先补充需求",
                409,
            )
        confirmed = confirm_proposal(session, user_id, proposal_id, reviewed_hash, True, now)
        result: dict[str, Any] = {
            "policy_id": str(confirmed.policy_id),
            "current_version_id": str(confirmed.current_version_id),
            "status": "CONFIRMED",
            "summary": "规则已确认",
        }
        if config["type"] == "goal_saving":
            existing = autonomy_state(session, user_id, now)
            if existing["state"] == "ACTIVE":
                raise PolicyLifecycleError(
                    "SINGLE_GOAL_ONLY", "首版自动安排只支持一个目标，请先暂停原授权", 409
                )
            account = session.scalar(
                select(Account)
                .where(
                    Account.user_id == user_id,
                    Account.account_type == "GOAL",
                )
                .order_by(Account.id)
                .limit(1)
            )
            if account is None:
                raise PolicyLifecycleError("NOT_FOUND", "模拟目标账户未初始化", 404)
            goal = create_goal_projection(
                session,
                user_id,
                confirmed.policy_id,
                confirmed.current_version_id,
                account.id,
                now,
            ).goal
            sources = list(
                session.scalars(
                    select(Account.id)
                    .where(
                        Account.user_id == user_id,
                        Account.account_type == "CASH",
                    )
                    .order_by(Account.id)
                )
            )
            cap = config["monthly_contribution"]["max_cents"]
            summary = (
                f"实际收入到账后，为{goal.name}按当前规则自动安排，每月最多{cap / 100:,.2f}元；"
                "无费用、无损失，可随时暂停。"
            )
            grant = store(
                session,
                user_id,
                "AUTH",
                str(request_id),
                {
                    "actor": "USER",
                    "accepted": True,
                    "reviewed_hash": reviewed_hash,
                    "policy_id": str(confirmed.policy_id),
                    "policy_version_id": str(confirmed.current_version_id),
                    "goal_id": str(goal.id),
                    "source_account_ids": [str(identity) for identity in sources],
                    "destination_account_id": str(account.id),
                    "action_family": "ALLOCATE_GOAL",
                    "single_action_cap_cents": cap,
                    "period_cap_cents": cap,
                    "fee_cap_cents": 0,
                    "loss_cap_cents": 0,
                    "max_lock_days": 0,
                    "valid_until": config.get("valid_until") or config["deadline"],
                    "summary": summary,
                },
                now,
                confirmed=True,
            )
            _control(session, user_id, grant.id, "ACTIVE", request_id, now)
            result.update(
                authorization_id=str(grant.id),
                goal_id=str(goal.id),
                status="ACTIVE",
                summary=summary,
            )
        elif config["type"] != "emergency_buffer":
            raise PolicyLifecycleError("UNSUPPORTED_FAMILY", "此首版尚未开放该策略消费链", 409)
        remember_operation(session, user_id, request_id, request, result, now)
        return result


def control_autonomy(
    engine: Engine,
    user_id: UUID,
    state: str,
    request_id: UUID,
    now: datetime,
    authorization_id: UUID | None = None,
) -> dict[str, Any]:
    request = {
        "kind": state,
        "authorization_id": str(authorization_id) if authorization_id else None,
    }
    with serial_user(engine, user_id), Session(engine) as session, session.begin():
        previous = replay_operation(session, user_id, request_id, request)
        if previous is not None:
            return previous
        current = autonomy_state(session, user_id, now)
        identity = current.get("authorization_id")
        if identity is None or authorization_id is not None and str(authorization_id) != identity:
            raise PolicyLifecycleError("AUTHORIZATION_MISMATCH", "请查看当前持续授权", 409)
        if state == "ACTIVE":
            auth = session.get(EvidenceItem, UUID(identity))
            assert auth is not None
            if not _grant_authorized(session, user_id, auth.content["payload"], now):
                raise PolicyLifecycleError(
                    "POLICY_NOT_AUTHORIZED", "规则或持续授权已暂停、撤销或到期，需重新审阅", 409
                )
        _control(session, user_id, UUID(identity), state, request_id, now)
        result = autonomy_state(session, user_id, now)
        remember_operation(session, user_id, request_id, request, result, now)
        return result


def _covered(action: ActionResponse, grant: dict[str, Any]) -> bool:
    effect = action.effect
    return (
        action.autonomy_level == "AUTO_EXECUTE"
        and action.prepared_validation.status == "READY"
        and effect.action_type == grant["action_family"]
        and str(effect.goal_id) == grant["goal_id"]
        and str(effect.policy_version_id) == grant["policy_version_id"]
        and str(effect.destination_account_id) == grant["destination_account_id"]
        and effect.amount_cents <= grant["single_action_cap_cents"]
        and effect.fee_cents == effect.loss_cents == effect.settlement_delay_days == 0
        and all(str(use.account_id) in grant["source_account_ids"] for use in effect.cash_uses)
    )


def _execute_original(
    engine: Engine, user_id: UUID, action_id: UUID, now: datetime
) -> ActionResponse:
    try:
        return execute_action(engine, user_id, action_id, now)
    except TimeoutError:
        with Session(engine) as session:
            original = get_action(session, user_id, action_id, now)
            if original.status != "UNKNOWN":
                raise
            return original


def _result(actual: ActionResponse, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": actual.status,
        "action_id": str(actual.action_id),
        "amount_cents": actual.effect.amount_cents,
        "summary": f"已为目标安排{actual.receipt.executed_cents / 100:,.2f}元"
        if actual.receipt
        else "正在核实原安排",
        "trigger_event": source,
        "actual_income_uses": [use.model_dump(mode="json") for use in actual.effect.income_uses],
    }


def drain_once(engine: Engine, user_id: UUID, now: datetime) -> dict[str, Any]:
    """Resume accepted originals first, then consume at most one actual income event."""
    with serial_user(engine, user_id):
        with Session(engine) as session:
            _audit_healthy(session, user_id)
            bindings = records(session, user_id, "BINDING")
            completed = {row.content["key"] for row in records(session, user_id, "RESULT")}
            for binding in bindings:
                identity = UUID(binding.content["payload"]["action_id"])
                row = session.get(ActionPlan, identity)
                if row is None or row.user_id != user_id:
                    raise PolicyLifecycleError("MISSING_ORIGINAL_ACTION", "自动安排原动作缺失", 409)
                if row.status in {"UNKNOWN", "SUBMITTED"} or (
                    row.status in {"SUCCEEDED", "RECONCILED"}
                    and binding.content["key"] not in completed
                ):
                    # No new acceptance: original bank key and effect remain authoritative.
                    actual = _execute_original(engine, user_id, identity, now)
                    source = {
                        name: binding.content["payload"][name]
                        for name in (
                            "fact_id",
                            "fact_request_hash",
                            "authorization_id",
                            "authorization_hash",
                        )
                    }
                    recovered = _result(actual, source)
                    if actual.receipt:
                        with Session(engine) as writer, writer.begin():
                            store(writer, user_id, "RESULT", binding.content["key"], recovered, now)
                    return recovered
            current = autonomy_state(session, user_id, now)
            if current["state"] != "ACTIVE":
                return {"status": "PAUSED", "summary": "已暂停接受新安排"}
            if current["pending_count"]:
                return {"status": "UNKNOWN", "summary": "原安排仍待核实，暂停新的安排"}
            auth = session.get(EvidenceItem, UUID(current["authorization_id"]))
            assert auth is not None
            grant = auth.content["payload"]
            facts = list(
                session.scalars(
                    select(ExternalBankFact)
                    .where(
                        ExternalBankFact.user_id == user_id,
                        ExternalBankFact.kind == "INCOME",
                        ExternalBankFact.bank_status == "SETTLED",
                        ExternalBankFact.projection_status == "PROJECTED",
                        ExternalBankFact.occurred_at >= auth.created_at,
                        ExternalBankFact.occurred_at <= now,
                        ExternalBankFact.observed_at <= now,
                    )
                    .order_by(ExternalBankFact.occurred_at, ExternalBankFact.id)
                )
            )
            fact = next((f for f in facts if f"{auth.id}:{f.id}" not in completed), None)
            if fact is None:
                return {"status": "IDLE", "summary": "等待实际收入到账"}
            validate_external_fact_original(
                {c.name: getattr(fact, c.name) for c in fact.__table__.columns}
            )
            event_key = f"{auth.id}:{fact.id}"
            source = {
                "fact_id": str(fact.id),
                "fact_request_hash": fact.request_hash,
                "authorization_id": str(auth.id),
                "authorization_hash": auth.content_hash,
            }
            bank_key = f"{MARKER}:{event_key}"
            prepared = session.scalar(
                select(ActionPlan).where(
                    ActionPlan.user_id == user_id,
                    ActionPlan.idempotency_key == "action:" + configuration_hash({"key": bank_key}),
                )
            )
            # A crash after prepare must continue that original. Its reserved
            # income can make a fresh preview show zero and must not hide it.
            if prepared is None:
                preview = preview_goal_allocation(session, user_id, UUID(grant["goal_id"]), now)
                allocation = preview.allocation
            else:
                allocation = None
            if allocation is not None and (
                allocation.status != "READY" or not allocation.suggested_cents
            ):
                result: dict[str, Any] = {
                    "status": "NO_ACTION" if allocation.status == "READY" else "BLOCKED",
                    "summary": "本期已满足目标安排"
                    if allocation.status == "READY"
                    else "当前规则或资金证据尚不足，自动安排已停止",
                    "source": source,
                }
                with Session(engine) as writer, writer.begin():
                    store(writer, user_id, "RESULT", event_key, result, now)
                return result
        with Session(engine) as writer, writer.begin():
            store(writer, user_id, "EVENT", event_key, source, now)
        # Every phase is committed independently by the original service. This key
        # survives a crash between preparation, binding, submission and projection.
        action = prepare_action(
            engine,
            user_id,
            PrepareActionRequest(
                idempotency_key=bank_key,
                intent=GoalIntent(kind="allocate_goal", goal_id=UUID(grant["goal_id"])),
            ),
            now,
        )
        if not _covered(action, grant):
            raise PolicyLifecycleError(
                "AUTONOMY_NOT_COVERED", "这笔安排超出持续授权，需要重新审阅", 409
            )
        with Session(engine) as writer, writer.begin():
            store(
                writer,
                user_id,
                "BINDING",
                event_key,
                {
                    **source,
                    "action_id": str(action.action_id),
                    "effect_hash": action.effect_hash,
                    "actual_income_uses": [
                        use.model_dump(mode="json") for use in action.effect.income_uses
                    ],
                    "authority_source": "CONTINUOUS_USER_AUTHORIZATION",
                },
                now,
            )
        actual = _execute_original(engine, user_id, action.action_id, now)
        result = _result(actual, source)
        if actual.receipt is not None:
            with Session(engine) as writer, writer.begin():
                store(writer, user_id, "RESULT", event_key, result, now)
        return result
