"""Actual 606 read-only adapters; callers never supply a financial candidate."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models import User
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution_types import ExecutionContext
from app.domain.full_action_set_boundary import CandidateInput
from app.domain.full_action_set_payment_producers import (
    ALGORITHM,
    PeriodicActionSetInput,
    PeriodicActionSetResult,
    PeriodicOriginalCommand,
    PeriodicPaymentProducerInput,
    confirmation_ids,
    derive_periodic_payment_producers,
    periodic_policy_ids,
    relation_rows,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.services.action_contracts import PaymentIntent
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy import _basis, _capture_assessment, _facts
from app.services.autonomy_envelope import _snapshot
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.decision_trace import get_decision_trace
from app.services.full_action_set_boundary import _verify_original_source_copies
from app.services.full_action_set_boundary_actual import (
    ActualActionSetCapture,
    capture_actual_action_set,
)
from app.services.full_payment_permissions import (
    PaymentCommandOriginal,
    PaymentCommandReceipt,
    _original,
    verified_periodic_transfer_projection_binding,
)
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PeriodicActionSetCapture:
    inputs: PeriodicActionSetInput
    result: PeriodicActionSetResult
    originals: DecisionCapture


def _command(session: Session, user: UUID, command: UUID, now: datetime) -> PeriodicOriginalCommand:
    receipt = _original(session, user, command, now, replay=True)
    original = get_decision_trace(session, user, command, now)
    if (
        receipt is None
        or original.trace is None
        or original.completeness != "COMPLETE"
        or original.audit_chain_status != "VALID"
    ):
        raise ValueError("PERIODIC_ORIGINAL_COMMAND_TRACE_OR_AUDIT_MISSING")
    return PeriodicOriginalCommand(receipt=receipt, trace=original.trace)


def _candidate(
    session: Session,
    user: UUID,
    now: datetime,
    original: ActualActionSetCapture,
    receipt: PaymentCommandReceipt,
) -> CandidateInput:
    scope = receipt.original.scope
    basis = _basis(session, user, now)
    if basis.digest != original.inputs.base.financial_input_hash:
        raise ValueError("PERIODIC_SAME_SNAPSHOT_FINANCIAL_BASIS_DIFFERS")
    period = now.astimezone(ZoneInfo(scope.timezone)).strftime("%Y-%m")
    facts = _facts(
        session,
        user,
        PaymentIntent(kind="pay_recurring", policy_id=scope.original_policy_id, period=period),
        now,
        basis,
    )
    _capture_assessment(session, facts)
    capture = session.info.get(CAPTURE_KEY)
    if not isinstance(capture, DecisionCapture):
        raise ValueError("PERIODIC_ORIGINAL_INPUT_CAPTURE_MISSING")
    raw_context = capture.inputs.get("execution_context") if facts.effect is not None else None
    context = (
        ExecutionContext.model_validate_json(json.dumps(raw_context))
        if raw_context is not None
        else None
    )
    full = compute_full_annual_protection(session, user, now)
    capture_evidence(session, user, full.source_evidence_ids)
    issues = [row.code for row in full.source_issues]
    if (full.user_id, full.as_of) != (
        user,
        now,
    ) or not full.projection.full_obligations_complete_within_registered_current_scope:
        issues.append("PERIODIC_COMPLETE_CURRENT_FULL_PROTECTION_NOT_PROVEN")
    bounds, veto = None, None
    if (
        facts.effect is not None
        and facts.validation is not None
        and context is not None
        and facts.validation.status in {"READY", "CONFIRMATION_REQUIRED"}
    ):
        if facts.validation.projected_snapshot is not None:
            projected_input = FullProtectionProjectionInput(
                snapshot=facts.validation.projected_snapshot,
                boundary_versions=context.versions,
                positions=facts.validation.projected_positions,
                boundary_products=context.boundary_products,
                policies=full.full_policy_sources,
                reserved_cash_by_account=context.reserved_cash_by_account,
            )
            projection = project_full_protection(projected_input)
            if projection.source_account_checks:
                bounds = derive_full_account_debit_bounds(
                    facts.effect, context, facts.validation, projected_input, projection
                )
        veto = validate_full_execution_protection(
            facts.effect,
            context,
            facts.validation,
            full.full_policy_sources,
            source_issues=tuple(issues),
            account_debit_bounds=bounds,
        )
    return CandidateInput(
        candidate_key="payment:" + str(scope.original_policy_id),
        facts=facts,
        execution_context=context,
        full_protection=veto,
        full_sources=full.full_policy_sources,
        full_source_issues=issues,
        full_account_debit_bounds=bounds,
    )


def capture_current_periodic_payment_producers(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    original_actual_capture: ActualActionSetCapture | None = None,
) -> PeriodicActionSetCapture:
    """Clean single RR/READ ONLY invocation; optional base is invocation-local only."""
    _snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    epoch = current_audit_epoch(session, user_id)
    original = original_actual_capture or capture_actual_action_set(session, user_id, now)
    if (
        epoch is None
        or epoch.status != "OPEN"
        or epoch.id != original.inputs.base.epoch_id
        or (original.inputs.base.user_id, original.inputs.base.as_of) != (user_id, now)
    ):
        raise ValueError("PERIODIC_ORIGINAL_OWNER_OR_CLOCK_DIFFERS")
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    capture.sources.update(original.originals.sources)
    capture.policies.update(original.originals.policies)
    expected = periodic_policy_ids(original.inputs)
    rows = relation_rows(original.inputs)
    reasons: list[str] = []
    producers: list[PeriodicPaymentProducerInput] = []
    try:
        for row in rows:
            try:
                parsed = PaymentCommandOriginal.model_validate_json(json.dumps(row["content"]))
                if parsed.user_id != user_id:
                    raise ValueError("Relation owner differs")
            except (ValueError, TypeError, KeyError) as error:
                reasons.append(
                    "PERIODIC_COMPLETE_RELATION_ROW_INVALID:" + row["id"] + ":" + str(error)
                )
        for identity in expected:
            item = PeriodicPaymentProducerInput(
                candidate_key="full-periodic:" + str(identity), full_policy_id=identity
            )
            try:
                full = read_full_policy(session, user_id, identity, now)
                ids = confirmation_ids(original.inputs, full)
                item = item.model_copy(
                    update={"full_policy": full, "confirmation_evidence_ids": ids}
                )
                commands: dict[UUID, PeriodicOriginalCommand] = {}
                for evidence_id in ids:
                    raw = next(row for row in rows if row["id"] == str(evidence_id))
                    parsed = PaymentCommandOriginal.model_validate_json(json.dumps(raw["content"]))
                    command = _command(session, user_id, parsed.command_id, now)
                    commands[parsed.command_id] = command
                    if parsed.start_command_id is None:
                        raise ValueError("PERIODIC_CONFIRMATION_START_MISSING")
                    commands[parsed.start_command_id] = _command(
                        session, user_id, parsed.start_command_id, now
                    )
                item = item.model_copy(update={"commands": list(commands.values())})
                binding = verified_periodic_transfer_projection_binding(session, user_id, full, now)
                item = item.model_copy(update={"relation_binding": binding})
                if binding.status == "VERIFIED_CURRENT_RELATION":
                    selected = (
                        commands.get(binding.relation_command_id)
                        if binding.relation_command_id is not None
                        else None
                    )
                    if selected is None:
                        raise ValueError("PERIODIC_SELECTED_ORIGINAL_RELATION_MISSING")
                    item = item.model_copy(
                        update={
                            "candidate": _candidate(
                                session, user_id, now, original, selected.receipt
                            )
                        }
                    )
                    capture_evidence(session, user_id, binding.source_evidence_ids)
                for command in commands.values():
                    capture.sources.update({row.id: row for row in command.trace.sources})
                    capture.policies.update({row.id: row for row in command.trace.policies})
            except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
                item = item.model_copy(
                    update={"missing_reasons": [getattr(error, "code", str(error))]}
                )
            producers.append(item)
        data = PeriodicActionSetInput(
            original_actual_input=original.inputs,
            expected_full_policy_ids=expected,
            relation_source_count=len(rows),
            relation_source_ids=sorted((UUID(row["id"]) for row in rows), key=str),
            producers=producers,
            source_reasons=reasons,
        )
        return PeriodicActionSetCapture(data, derive_periodic_payment_producers(data), capture)
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)


def read_current_periodic_payment_producers(
    session: Session, user_id: UUID, now: datetime
) -> PeriodicActionSetResult:
    return capture_current_periodic_payment_producers(session, user_id, now).result


def verify_frozen_periodic_payment_producers(trace: DecisionTrace) -> PeriodicActionSetResult:
    """Exact standalone frozen family computation, also callable by a new global verifier."""
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("periodic_action_producers") != ALGORITHM
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
    ):
        raise ValueError("Not the exact periodic producer algorithm")
    data = PeriodicActionSetInput.model_validate_json(
        json.dumps(trace.inputs["periodic_action_set_input"])
    )
    result = derive_periodic_payment_producers(data)
    _verify_original_source_copies(
        trace, data.original_actual_input.base, result.periodic_family_complete
    )
    if (trace.user_id, trace.as_of) != (result.user_id, result.as_of) or trace.outcome != {
        "periodic_action_set_result": result.model_dump(mode="json")
    }:
        raise ValueError("Original periodic producer result or owner/clock differs")
    sources = {row.id: row for row in trace.sources}
    for producer in data.producers:
        for command in producer.commands:
            for source in command.trace.sources:
                if sources.get(source.id) != source:
                    raise ValueError("Complete original periodic source copy is missing")
        candidate = producer.candidate
        if candidate is not None and candidate.facts is not None:
            for identity in (
                candidate.facts.source_evidence_ids + candidate.facts.authority.evidence_ids
            ):
                current_source = sources.get(identity)
                if (
                    current_source is None
                    or current_source.user_id != result.user_id
                    or current_source.content_integrity != "VERIFIED"
                ):
                    raise ValueError("Actual periodic financial/permission source copy is missing")
    return result
