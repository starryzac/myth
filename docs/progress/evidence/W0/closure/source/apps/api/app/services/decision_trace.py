"""Persist actual decision snapshots and read them without authorizing or moving funds."""

import base64
import binascii
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    BankOperation,
    DecisionConstraint,
    DecisionRun,
    EvidenceItem,
    Policy,
    PolicyVersion,
    SimulatedBankRedemption,
)
from app.domain.audit_chain_types import Status as AuditStatus
from app.domain.decision_trace import explain_trace, verify_trace
from app.domain.decision_trace_types import (
    DecisionTrace,
    TraceConstraint,
    TraceEvidence,
    TraceExplanation,
    TracePolicy,
)
from app.domain.execution import ACTION_PLAN_TYPES, execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.policy_configuration import configuration_hash
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

Completeness = Literal["COMPLETE", "LEGACY_PARTIAL", "UNSUPPORTED_VERSION"]


class TraceReferenceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entity_type: Literal["EVIDENCE", "POLICY"]
    entity_id: UUID
    status: Literal["UNCHANGED", "STATUS_CHANGED", "MISSING"]
    original_status: str
    current_status: str | None


class TraceActionLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: UUID
    decision_run_id: UUID
    status: str
    request_hash: str
    bank_operation_id: UUID | None = None
    bank_status: str | None = None
    receipt_id: UUID | None = None
    receipt_status: str | None = None


class DecisionTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    run_id: UUID
    as_of: datetime
    read_at: datetime
    completeness: Completeness
    trace: DecisionTrace | None = None
    explanation: TraceExplanation | None = None
    current_references: list[TraceReferenceStatus]
    actions: list[TraceActionLink]
    children: list[UUID]
    legacy_snapshot: dict[str, Any] | None = None
    legacy_result: dict[str, Any] | None = None
    audit_chain_status: AuditStatus = "LEGACY_UNAUDITED"


class DecisionTraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    as_of: datetime
    trigger_type: str
    status: str
    completeness: Completeness
    phase: str | None = None
    parent_run_id: UUID | None = None
    action_id: UUID | None = None


class DecisionTraceList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    items: list[DecisionTraceSummary]
    next_cursor: str | None = None


def _integrity(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("DECISION_TRACE_INTEGRITY_ERROR", message, 409)


def _not_found() -> PolicyLifecycleError:
    return PolicyLifecycleError("NOT_FOUND", "决策轨迹或关联对象不存在", 404)


def _clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "决策读取时钟必须带时区")
    return now.astimezone(UTC)


def evidence_copy(row: EvidenceItem) -> TraceEvidence:
    """Copy the exact object already used by the caller, including an invalid declaration."""
    actual = configuration_hash(row.content)
    return TraceEvidence.model_validate(
        {
            "id": row.id,
            "user_id": row.user_id,
            "evidence_level": row.evidence_level,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "content": row.content,
            "content_hash": row.content_hash,
            "captured_content_hash": actual,
            "content_integrity": "VERIFIED" if actual == row.content_hash else "INVALID",
            "status_at_decision": row.status,
            "observed_at": row.observed_at,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "supersedes_evidence_id": row.supersedes_id,
        }
    )


def policy_copy(row: PolicyVersion, *, status_at_decision: str = "UNKNOWN") -> TracePolicy:
    """Copy an immutable version; the optional lifecycle status comes from its owning policy."""
    actual = configuration_hash(row.configuration)
    return TracePolicy(
        id=row.id,
        user_id=row.user_id,
        policy_id=row.policy_id,
        version_number=row.version_number,
        configuration=row.configuration,
        configuration_hash=row.content_hash,
        captured_configuration_hash=actual,
        configuration_integrity="VERIFIED" if actual == row.content_hash else "INVALID",
        status_at_decision=status_at_decision,
        confirmed_at=row.confirmed_at,
        valid_from=row.valid_from,
        valid_to=row.valid_until,
    )


def _identities(values: Iterable[UUID], maximum: int) -> list[UUID]:
    identities = sorted(set(values))
    if len(identities) > maximum or any(not isinstance(key, UUID) for key in identities):
        raise _integrity("来源引用集合超出范围或身份类型无效")
    return identities


def capture_sources(
    session: Session, user_id: UUID, used_ids: Iterable[UUID]
) -> list[TraceEvidence]:
    identities = _identities(used_ids, 10000)
    if not identities:
        return []
    with session.no_autoflush:
        rows = session.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.user_id == user_id, EvidenceItem.id.in_(identities))
            .order_by(EvidenceItem.id)
        ).all()
        if len(rows) != len(identities):
            raise _not_found()
        try:
            return [evidence_copy(row) for row in rows]
        except (ValueError, TypeError) as error:
            raise _integrity("实际证据不能冻结为严格轨迹输入") from error


def capture_available_sources(
    session: Session, user_id: UUID, used_ids: Iterable[UUID]
) -> tuple[list[TraceEvidence], list[UUID]]:
    """Explicit recording only: return absent IDs without inventing evidence or hiding tenants."""
    identities = _identities(used_ids, 10000)
    if not identities:
        return [], []
    with session.no_autoflush:
        rows = session.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.user_id == user_id, EvidenceItem.id.in_(identities))
            .order_by(EvidenceItem.id)
        ).all()
        missing = sorted(set(identities) - {row.id for row in rows})
        if (
            missing
            and session.scalar(select(EvidenceItem.id).where(EvidenceItem.id.in_(missing)))
            is not None
        ):
            raise _not_found()
        try:
            return [evidence_copy(row) for row in rows], missing
        except (ValueError, TypeError) as error:
            raise _integrity("实际证据不能冻结为严格轨迹输入") from error


def capture_policies(
    session: Session, user_id: UUID, version_ids: Iterable[UUID]
) -> list[TracePolicy]:
    identities = _identities(version_ids, 1000)
    if not identities:
        return []
    with session.no_autoflush:
        rows = session.execute(
            select(PolicyVersion, Policy.status)
            .join(Policy, and_(Policy.id == PolicyVersion.policy_id, Policy.user_id == user_id))
            .where(PolicyVersion.user_id == user_id, PolicyVersion.id.in_(identities))
            .order_by(PolicyVersion.id)
        ).all()
        if len(rows) != len(identities):
            raise _not_found()
        try:
            return [policy_copy(row, status_at_decision=status) for row, status in rows]
        except (ValueError, TypeError) as error:
            raise _integrity("实际策略不能冻结为严格轨迹输入") from error


def _same_content(first: TraceEvidence | TracePolicy, second: TraceEvidence | TracePolicy) -> bool:
    return first.model_dump(mode="json", exclude={"status_at_decision"}) == second.model_dump(
        mode="json", exclude={"status_at_decision"}
    )


@dataclass
class _ReferenceReads:
    """Private detached originals for one audit invocation in a stable read snapshot."""

    evidence: dict[UUID, TraceEvidence | None] = field(default_factory=dict)
    policies: dict[UUID, TracePolicy | None] = field(default_factory=dict)

    def load(self, session: Session, trace: DecisionTrace) -> None:
        missing = {source.id for source in trace.sources} - self.evidence.keys()
        if missing:
            rows = session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == trace.user_id, EvidenceItem.id.in_(missing)
                )
            )
            self.evidence.update(dict.fromkeys(missing))
            try:
                for row in rows:
                    self.evidence[row.id] = evidence_copy(row)
            except (ValueError, TypeError) as error:
                raise _integrity("当前原始证据无法核验") from error
        missing = {version.id for version in trace.policies} - self.policies.keys()
        if missing:
            versions = list(
                session.scalars(
                    select(PolicyVersion).where(
                        PolicyVersion.user_id == trace.user_id, PolicyVersion.id.in_(missing)
                    )
                )
            )
            policies = {
                row.id: row.status
                for row in session.scalars(
                    select(Policy).where(
                        Policy.user_id == trace.user_id,
                        Policy.id.in_({version.policy_id for version in versions}),
                    )
                )
            }
            self.policies.update(dict.fromkeys(missing))
            try:
                for version in versions:
                    if version.policy_id in policies:
                        self.policies[version.id] = policy_copy(
                            version, status_at_decision=policies[version.policy_id]
                        )
            except (ValueError, TypeError) as error:
                raise _integrity("当前原始策略无法核验") from error


def _current_references(
    session: Session,
    trace: DecisionTrace,
    *,
    allow_missing: bool,
    _reads: _ReferenceReads | None = None,
) -> list[TraceReferenceStatus]:
    if _reads is not None:
        _reads.load(session, trace)
        references: list[TraceReferenceStatus] = []
        status: Literal["UNCHANGED", "STATUS_CHANGED", "MISSING"]
        originals: list[TraceEvidence | TracePolicy] = [*trace.sources, *trace.policies]
        for original in originals:
            source = isinstance(original, TraceEvidence)
            copied = (
                _reads.evidence.get(original.id) if source else _reads.policies.get(original.id)
            )
            if copied is None:
                if not allow_missing:
                    raise _not_found()
                current, status = None, "MISSING"
            else:
                if not _same_content(original, copied):
                    raise _integrity(
                        "当前原始证据已不同于当时冻结的来源"
                        if source
                        else "当前原始策略已不同于当时冻结的版本"
                    )
                current = copied.status_at_decision
                status = "UNCHANGED" if current == original.status_at_decision else "STATUS_CHANGED"
            references.append(
                TraceReferenceStatus(
                    entity_type="EVIDENCE" if source else "POLICY",
                    entity_id=original.id,
                    status=status,
                    original_status=original.status_at_decision,
                    current_status=current,
                )
            )
        return references
    return _uncached_current_references(session, trace, allow_missing=allow_missing)


def _uncached_current_references(
    session: Session, trace: DecisionTrace, *, allow_missing: bool
) -> list[TraceReferenceStatus]:
    references: list[TraceReferenceStatus] = []
    status: Literal["UNCHANGED", "STATUS_CHANGED", "MISSING"]
    for source in trace.sources:
        row = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.id == source.id, EvidenceItem.user_id == trace.user_id
            )
        )
        if row is None:
            if not allow_missing:
                raise _not_found()
            status, current = "MISSING", None
        else:
            try:
                copied = evidence_copy(row)
            except (ValueError, TypeError) as error:
                raise _integrity("当前原始证据无法核验") from error
            if not _same_content(source, copied):
                raise _integrity("当前原始证据已不同于当时冻结的来源")
            current = row.status
            status = "UNCHANGED" if current == source.status_at_decision else "STATUS_CHANGED"
        references.append(
            TraceReferenceStatus(
                entity_type="EVIDENCE",
                entity_id=source.id,
                status=status,
                original_status=source.status_at_decision,
                current_status=current,
            )
        )
    for version in trace.policies:
        policy_row = session.scalar(
            select(PolicyVersion).where(
                PolicyVersion.id == version.id, PolicyVersion.user_id == trace.user_id
            )
        )
        policy = session.scalar(
            select(Policy).where(Policy.id == version.policy_id, Policy.user_id == trace.user_id)
        )
        if policy_row is None or policy is None:
            if not allow_missing:
                raise _not_found()
            status, current = "MISSING", None
        else:
            try:
                copied_policy = policy_copy(policy_row, status_at_decision=policy.status)
            except (ValueError, TypeError) as error:
                raise _integrity("当前原始策略无法核验") from error
            if not _same_content(version, copied_policy):
                raise _integrity("当前原始策略已不同于当时冻结的版本")
            current = policy.status
            status = "UNCHANGED" if current == version.status_at_decision else "STATUS_CHANGED"
        references.append(
            TraceReferenceStatus(
                entity_type="POLICY",
                entity_id=version.id,
                status=status,
                original_status=version.status_at_decision,
                current_status=current,
            )
        )
    return references


def _relations(
    session: Session, user_id: UUID, parent: UUID | None, action: UUID | None, run_id: UUID
) -> None:
    ancestors = {run_id}
    cursor = parent
    while cursor is not None:
        if cursor in ancestors or len(ancestors) > 32:
            raise _integrity("决策父链存在循环或超出范围")
        row = session.scalar(
            select(DecisionRun).where(DecisionRun.id == cursor, DecisionRun.user_id == user_id)
        )
        if row is None:
            raise _not_found()
        ancestors.add(row.id)
        cursor = row.parent_run_id
    if action is not None:
        action_row = session.scalar(
            select(ActionPlan).where(ActionPlan.id == action, ActionPlan.user_id == user_id)
        )
        if action_row is None:
            raise _not_found()
        if action_row.decision_run_id not in ancestors:
            raise _integrity("动作不能重绑定到另一条决策父链")


def _constraint_copy(row: DecisionConstraint) -> TraceConstraint:
    return TraceConstraint(
        constraint_key=row.constraint_key,
        policy_version_id=row.policy_version_id,
        is_hard=row.is_hard,
        satisfied=row.satisfied,
        required_cents=row.required_cents,
        available_cents=row.available_cents,
        due_date=row.due_date,
        calculation=row.calculation,
        reason_code=row.reason_code,
    )


def _verify_constraints(session: Session, trace: DecisionTrace) -> None:
    rows = session.scalars(
        select(DecisionConstraint).where(
            DecisionConstraint.decision_run_id == trace.run_id,
            DecisionConstraint.user_id == trace.user_id,
        )
    ).all()
    try:
        copied = [_constraint_copy(row) for row in rows]
        actual = {item.constraint_key: item for item in copied}
    except (ValueError, TypeError) as error:
        raise _integrity("约束投影字段无效") from error
    expected = {constraint.constraint_key: constraint for constraint in trace.constraints}
    if actual != expected or len(rows) != len(expected):
        raise _integrity("约束投影不再等于冻结的计算约束")


def record_trace(
    session: Session, trace: DecisionTrace, existing_run: DecisionRun | None = None
) -> DecisionRun:
    """Caller owns the transaction; frozen decisions never commit unrelated economic mutations."""
    from app.db.audit_guard import transaction_gate

    transaction_gate(session, trace.user_id)
    try:
        verify_trace(trace)
    except (ValueError, TypeError) as error:
        raise _integrity("决策轨迹内容校验失败") from error
    if existing_run is not None and (
        existing_run.id != trace.run_id or existing_run.user_id != trace.user_id
    ):
        raise _not_found()
    _relations(session, trace.user_id, trace.parent_run_id, trace.action_id, trace.run_id)
    _current_references(session, trace, allow_missing=False)
    for constraint in trace.constraints:
        if (
            constraint.policy_version_id is not None
            and session.scalar(
                select(PolicyVersion.id).where(
                    PolicyVersion.id == constraint.policy_version_id,
                    PolicyVersion.user_id == trace.user_id,
                )
            )
            is None
        ):
            raise _not_found()
    row = existing_run or session.scalar(
        select(DecisionRun).where(
            DecisionRun.id == trace.run_id, DecisionRun.user_id == trace.user_id
        )
    )
    payload = trace.model_dump(mode="json")
    prior = session.scalars(
        select(DecisionConstraint).where(
            DecisionConstraint.decision_run_id == trace.run_id,
            DecisionConstraint.user_id == trace.user_id,
        )
    ).all()
    if row is not None:
        if configuration_hash(row.input_snapshot) != row.snapshot_hash:
            raise _integrity("原决策输入摘要已改变")
        if "decision_trace" in row.input_snapshot:
            if row.input_snapshot["decision_trace"] != payload:
                raise PolicyLifecycleError(
                    "IDEMPOTENCY_CONFLICT", "同一决策身份不能改变冻结内容", 409
                )
            _stored_trace(session, row)
            return row
        if prior:
            raise _integrity("既有约束不能被新冻结内容替换")
        if row.as_of != trace.as_of:
            raise _integrity("追加轨迹不能更换原决策时钟")
        row.input_snapshot = {**row.input_snapshot, "decision_trace": payload}
        row.parent_run_id = trace.parent_run_id
        row.subject_action_plan_id = trace.action_id
        row.policy_version_ids = sorted(
            {str(item.id) for item in trace.policies} | set(row.policy_version_ids)
        )
        row.evidence_ids = sorted({str(item.id) for item in trace.sources} | set(row.evidence_ids))
    else:
        if session.get(DecisionRun, trace.run_id) is not None:
            raise _not_found()
        row = DecisionRun(
            id=trace.run_id,
            user_id=trace.user_id,
            created_at=trace.as_of,
            idempotency_key="trace:" + str(trace.run_id),
            trigger_type="TRACE_" + trace.phase,
            algorithm_version="decision-trace-v1",
            as_of=trace.as_of,
            parent_run_id=trace.parent_run_id,
            subject_action_plan_id=trace.action_id,
            input_snapshot={"decision_trace": payload},
            snapshot_hash="0" * 64,
            policy_version_ids=[str(item.id) for item in trace.policies],
            evidence_ids=[str(item.id) for item in trace.sources],
            result={},
            status="SUCCEEDED",
            completed_at=trace.as_of,
        )
        session.add(row)
    row.input_snapshot = {
        **row.input_snapshot,
        "decision_trace_indexes": {
            "policy_version_ids": row.policy_version_ids,
            "evidence_ids": row.evidence_ids,
        },
    }
    row.snapshot_hash = configuration_hash(row.input_snapshot)
    session.flush()
    for constraint in trace.constraints:
        session.add(
            DecisionConstraint(
                id=uuid5(row.id, "constraint:" + constraint.constraint_key),
                user_id=trace.user_id,
                created_at=trace.as_of,
                decision_run_id=row.id,
                **constraint.model_dump(),
            )
        )
    session.flush()
    from app.services.audit_recording import record_decision

    record_decision(session, trace)
    return row


def _supported(trace: DecisionTrace) -> bool:
    from app.domain.asset_allocation import ALGORITHM_VERSION as asset
    from app.domain.autonomy import ALGORITHM_VERSION as autonomy
    from app.domain.boundary import ALGORITHM_VERSION as boundary
    from app.domain.execution import ALGORITHM_VERSION as execution
    from app.domain.goal_allocation import ALGORITHM_VERSION as goal
    from app.domain.recovery import ALGORITHM_VERSION as recovery

    supported = {
        asset,
        autonomy,
        boundary,
        execution,
        goal,
        recovery,
        "decision-trace-v1",
        "execution-bank-v1",
        "legacy-redemption-v1",
    }
    return all(version in supported for version in trace.algorithm_versions.values())


def _stored_trace(session: Session, row: DecisionRun) -> tuple[Completeness, DecisionTrace | None]:
    try:
        if configuration_hash(row.input_snapshot) != row.snapshot_hash:
            raise _integrity("原决策输入摘要不一致")
    except (ValueError, TypeError) as error:
        raise _integrity("原决策输入不是有效JSON") from error
    raw = row.input_snapshot.get("decision_trace")
    if raw is None:
        return "LEGACY_PARTIAL", None
    if not isinstance(raw, dict):
        raise _integrity("冻结轨迹不是JSON对象")
    if raw.get("schema_version") != "decision-trace-v1":
        return "UNSUPPORTED_VERSION", None
    try:
        trace = DecisionTrace.model_validate_json(json.dumps(raw))
        verify_trace(trace)
    except (ValueError, TypeError) as error:
        raise _integrity("冻结轨迹合同或内容摘要无效") from error
    if (
        trace.run_id != row.id
        or trace.user_id != row.user_id
        or trace.as_of != row.as_of
        or trace.parent_run_id != row.parent_run_id
        or trace.action_id != row.subject_action_plan_id
    ):
        raise _integrity("轨迹身份与原始记录关联不一致")
    _relations(session, row.user_id, row.parent_run_id, row.subject_action_plan_id, row.id)
    if row.input_snapshot.get("decision_trace_indexes") != {
        "policy_version_ids": row.policy_version_ids,
        "evidence_ids": row.evidence_ids,
    }:
        raise _integrity("决策来源索引已不同于录制时的冻结集合")
    if not {str(item.id) for item in trace.policies}.issubset(set(row.policy_version_ids)) or not {
        str(item.id) for item in trace.sources
    }.issubset(set(row.evidence_ids)):
        raise _integrity("决策记录来源索引与冻结内容不一致")
    _verify_constraints(session, trace)
    return ("COMPLETE" if _supported(trace) else "UNSUPPORTED_VERSION"), trace


def _verify_action_origin(trace: DecisionTrace | None, action: ActionPlan) -> BankCommand | None:
    command = None
    if "execution" in action.request:
        try:
            command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        except (ValueError, TypeError) as error:
            raise _integrity("原动作缺少有效的经济后果绑定") from error
        effect = command.effect
        source = effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id
        destination = (
            effect.destination_account_id if effect.destination_account_id != source else None
        )
        position_matches = (
            action.position_id == effect.position_id
            if effect.action_type == "REDEEM_ASSET"
            else action.position_id in {None, effect.position_id}
            if effect.action_type == "PURCHASE_ASSET"
            else action.position_id is None
        )
        if (
            effect.user_id != action.user_id
            or effect.operation_id != action.id
            or action.action_type not in {effect.action_type, ACTION_PLAN_TYPES[effect.action_type]}
            or action.amount_cents != effect.amount_cents
            or action.policy_version_id != effect.policy_version_id
            or action.source_account_id != source
            or action.destination_account_id != destination
            or action.goal_id != effect.goal_id
            or action.product_id != effect.product_id
            or not position_matches
            or action.expires_at != effect.expires_at
        ):
            raise _integrity("动作身份已不同于原经济后果")
    if trace is None:
        return command
    original_effect = trace.inputs.get("effect")
    if original_effect is not None:
        try:
            effect = ExecutionEffect.model_validate_json(json.dumps(original_effect))
        except (ValueError, TypeError) as error:
            raise _integrity("冻结的原始经济后果无效") from error
        if (
            command is None
            or command.effect_hash != execution_effect_hash(effect)
            or (command.effect.model_dump(mode="json") != effect.model_dump(mode="json"))
        ):
            raise _integrity("当前动作经济后果已不同于录制时的原始请求")
    original_requests = trace.inputs.get("action_requests")
    original_request = trace.inputs.get("action_request")
    if isinstance(original_requests, dict):
        if str(action.id) not in original_requests:
            raise _integrity("当前动作不在原恢复请求集合中")
        original_request = original_requests[str(action.id)]
    if original_request is not None and (
        not isinstance(original_request, dict)
        or configuration_hash(original_request) != configuration_hash(action.request)
    ):
        raise _integrity("当前恢复请求已不同于录制时的原始请求")
    if trace.phase == "PREPARE" and "validation" in trace.outcome:
        prepared = action.request.get("prepared_validation")
        if not isinstance(prepared, dict) or configuration_hash(prepared) != configuration_hash(
            trace.outcome["validation"]
        ):
            raise _integrity("动作准备计算已不同于冻结的原始结果")
    return command


def _action_links(
    session: Session, row: DecisionRun, now: datetime, trace: DecisionTrace | None
) -> list[TraceActionLink]:
    query = select(ActionPlan).where(ActionPlan.user_id == row.user_id)
    if row.subject_action_plan_id is not None:
        query = query.where(ActionPlan.id == row.subject_action_plan_id)
    else:
        query = query.where(ActionPlan.decision_run_id == row.id)
    actions = session.scalars(query.order_by(ActionPlan.id)).all()
    if (
        trace is not None
        and isinstance(trace.inputs.get("action_requests"), dict)
        and (set(trace.inputs["action_requests"]) != {str(action.id) for action in actions})
    ):
        raise _integrity("当前动作集合已不同于原恢复计划")
    result: list[TraceActionLink] = []
    for action in actions:
        if configuration_hash(action.request) != action.request_hash:
            raise _integrity("动作请求载荷已改变")
        command = _verify_action_origin(trace, action)
        operation = session.scalar(
            select(BankOperation).where(
                BankOperation.user_id == row.user_id, BankOperation.action_plan_id == action.id
            )
        )
        receipts = session.scalars(
            select(ActionReceipt).where(
                ActionReceipt.user_id == row.user_id, ActionReceipt.action_plan_id == action.id
            )
        ).all()
        if len(receipts) > 1:
            raise _integrity("动作存在冲突回执")
        receipt = receipts[0] if receipts else None
        if (
            operation is not None
            and configuration_hash(operation.request) != operation.request_hash
        ):
            raise _integrity("独立银行请求载荷已改变")
        if (
            operation is not None
            and command is not None
            and (operation.request != command.model_dump(mode="json"))
        ):
            raise _integrity("独立银行操作已不同于原经济后果")
        if receipt is not None:
            if operation is None:
                raise _integrity("回执缺少独立银行操作")
            try:
                if operation.legacy_redemption_id is None:
                    from app.services.execution_projection import verify_execution_receipt

                    verify_execution_receipt(session, operation, receipt, now)
                else:
                    from app.services.recovery_receipt_integrity import verify_recovery_receipt

                    request = session.scalar(
                        select(SimulatedBankRedemption).where(
                            SimulatedBankRedemption.user_id == row.user_id,
                            SimulatedBankRedemption.id == operation.legacy_redemption_id,
                        )
                    )
                    if request is None:
                        raise _integrity("旧恢复回执缺少原始银行请求")
                    verify_recovery_receipt(session, request, receipt, now)
            except PolicyLifecycleError as error:
                raise _integrity("历史回执与原始银行经济事实不一致") from error
        elif action.status in {"SUCCEEDED", "RECONCILED"}:
            raise _integrity("已成功动作缺少原始回执")
        result.append(
            TraceActionLink(
                action_id=action.id,
                decision_run_id=action.decision_run_id,
                status=action.status,
                request_hash=action.request_hash,
                bank_operation_id=operation.id if operation else None,
                bank_status=operation.status if operation else None,
                receipt_id=receipt.id if receipt else None,
                receipt_status=receipt.status if receipt else None,
            )
        )
    return result


def get_decision_trace(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> DecisionTraceResponse:
    now = _clock(now)
    with session.no_autoflush:
        row = session.scalar(
            select(DecisionRun).where(DecisionRun.id == run_id, DecisionRun.user_id == user_id)
        )
        if row is None:
            raise _not_found()
        if now < row.as_of:
            raise PolicyLifecycleError("DECISION_NOT_YET_OCCURRED", "决策时钟晚于当前读取时钟", 409)
        completeness, trace = _stored_trace(session, row)
        references = _current_references(session, trace, allow_missing=True) if trace else []
        from app.services.audit_chain import get_decision_audit_status

        return DecisionTraceResponse(
            user_id=user_id,
            run_id=run_id,
            as_of=row.as_of,
            read_at=now,
            completeness=completeness,
            trace=trace,
            explanation=explain_trace(trace) if trace and completeness == "COMPLETE" else None,
            current_references=references,
            actions=_action_links(session, row, now, trace),
            children=list(
                session.scalars(
                    select(DecisionRun.id)
                    .where(DecisionRun.user_id == user_id, DecisionRun.parent_run_id == run_id)
                    .order_by(DecisionRun.as_of, DecisionRun.id)
                )
            ),
            legacy_snapshot=json.loads(json.dumps(row.input_snapshot)) if trace is None else None,
            legacy_result=json.loads(json.dumps(row.result)) if trace is None else None,
            audit_chain_status=get_decision_audit_status(session, user_id, run_id),
        )


def get_action_trace(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> DecisionTraceResponse:
    with session.no_autoflush:
        action = session.scalar(
            select(ActionPlan).where(ActionPlan.id == action_id, ActionPlan.user_id == user_id)
        )
        if action is None:
            raise _not_found()
        response = get_decision_trace(session, user_id, action.decision_run_id, now)
        if action_id not in {link.action_id for link in response.actions}:
            raise _integrity("动作已不同于该主决策记录中的原始关联")
        return response


def _cursor(row: DecisionRun) -> str:
    content = json.dumps(
        {"as_of": row.as_of.isoformat(), "id": str(row.id)}, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def _parse_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        if (
            not isinstance(cursor, str)
            or not 1 <= len(cursor) <= 256
            or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor)
        ):
            raise ValueError("Invalid cursor encoding")
        decoded = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        content = json.loads(decoded)
        if type(content) is not dict or set(content) != {"as_of", "id"}:
            raise ValueError("Invalid cursor fields")
        if type(content["as_of"]) is not str or type(content["id"]) is not str:
            raise ValueError("Invalid cursor identity")
        return _clock(datetime.fromisoformat(content["as_of"])), UUID(content["id"])
    except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise PolicyLifecycleError("INVALID_DECISION_CURSOR", "决策分页游标无效") from error


def list_decision_traces(
    session: Session, user_id: UUID, *, limit: int = 20, cursor: str | None = None
) -> DecisionTraceList:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise PolicyLifecycleError("INVALID_DECISION_LIMIT", "决策分页大小必须为1到100")
    query = select(DecisionRun).where(DecisionRun.user_id == user_id)
    if cursor is not None:
        as_of, identity = _parse_cursor(cursor)
        query = query.where(
            or_(
                DecisionRun.as_of < as_of,
                and_(DecisionRun.as_of == as_of, DecisionRun.id < identity),
            )
        )
    with session.no_autoflush:
        rows = session.scalars(
            query.order_by(DecisionRun.as_of.desc(), DecisionRun.id.desc()).limit(limit + 1)
        ).all()
        items = []
        for row in rows[:limit]:
            completeness, trace = _stored_trace(session, row)
            items.append(
                DecisionTraceSummary(
                    run_id=row.id,
                    as_of=row.as_of,
                    trigger_type=row.trigger_type,
                    status=row.status,
                    completeness=completeness,
                    phase=trace.phase if trace else None,
                    parent_run_id=row.parent_run_id,
                    action_id=row.subject_action_plan_id,
                )
            )
        return DecisionTraceList(
            user_id=user_id,
            items=items,
            next_cursor=_cursor(rows[limit - 1]) if len(rows) > limit else None,
        )
