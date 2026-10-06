"""One RR/RO actual recovery-family capture; never prepare, confirm or execute."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from app.db.models import User
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext
from app.domain.full_action_set_boundary import CandidateInput
from app.domain.full_action_set_recovery_producers import (
    ALGORITHM,
    MAX_PRODUCERS,
    RecoveryActionSetInput,
    RecoveryActionSetResult,
    RecoveryOriginalCommand,
    RecoveryProducerInput,
    derive_recovery_producers,
    recovery_action_ids,
    recovery_policy_ids,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_recovery_execution import (
    FullRecoveryPrepareRequest,
    build_full_recovery_effect,
    recovery_bank_key,
)
from app.domain.full_recovery_planning import FullRecoveryPlanningInput
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.services.action_contracts import RedeemIntent
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy import _basis, _capture_assessment, _facts
from app.services.autonomy_envelope import _snapshot
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.decision_trace import get_decision_trace
from app.services.execution_context import require_full_recovery_confirmation_context
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_action_set_boundary import _verify_original_source_copies
from app.services.full_action_set_boundary_actual import (
    ActualActionSetCapture,
    capture_actual_action_set,
)
from app.services.full_policy_lifecycle import FullPolicyView, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.full_recovery_execution import (
    read_full_recovery_execution_inputs,
    require_installed_full_recovery_guards,
)
from app.services.full_recovery_planning import (
    FullRecoveryPlanningResponse,
    _holdings,
    _linked_policy,
    read_full_recovery_planning,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RecoveryActionSetCapture:
    inputs: RecoveryActionSetInput
    result: RecoveryActionSetResult
    originals: DecisionCapture


def _planning_inputs(
    session: Session,
    user: UUID,
    full: FullPolicyView,
    response: FullRecoveryPlanningResponse,
    now: datetime,
) -> FullRecoveryPlanningInput:
    """Assemble original planner inputs and compare its actual public result in pure replay."""
    config = RecoveryPolicy.model_validate_json(json.dumps(full.current_version.configuration))
    if config.goal_id is not None:
        raise ValueError("RECOVERY_GOAL_EXECUTION_FAMILY_NOT_SUPPORTED")
    context, matched, exposures = load_verified_financial_context(session, user, now)
    if not matched or exposures is None or context.sources.issues:
        raise ValueError("RECOVERY_COMPLETE_CURRENT_FINANCIAL_SOURCE_NOT_VERIFIED")
    context.sources.used.update(full.current_version.evidence_ids)
    for issue in response.catalogue.issues:
        context.sources.issue("IMMUTABLE_CATALOGUE_NOT_PROVEN", issue, "原目录未证明")
    linked = _linked_policy(session, context, config)
    manual = {key for scope in exposures for key in scope.excluded_manual_position_ids}
    holdings, _ = _holdings(session, context, response.catalogue, manual)
    audit = current_epoch_audit(session, user, [])
    if audit.status != "VALID" or not audit.complete:
        raise ValueError("RECOVERY_COMPLETE_CURRENT_AUDIT_NOT_VERIFIED")
    context, issues, _ = finalize_financial_context(context, audit)
    if issues:
        raise ValueError("RECOVERY_ACTUAL_PLANNING_SOURCE_NOT_VERIFIED")
    capture_evidence(session, user, sorted(context.sources.used))
    return FullRecoveryPlanningInput(
        user_id=user,
        policy_id=full.policy_id,
        policy_version_id=full.current_version.version_id,
        configuration=config,
        planning_confirmation_valid=full.planning_confirmation_valid,
        confirmed_at=full.current_version.confirmed_at,
        valid_from=full.current_version.valid_from,
        valid_until=full.current_version.valid_until,
        linked_asset_policy=linked,
        snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
        boundary_versions=context.versions,
        positions=context.positions,
        boundary_products=context.products,
        holdings=holdings,
    )


def _candidate(
    session: Session,
    user: UUID,
    now: datetime,
    original: ActualActionSetCapture,
    item: RecoveryProducerInput,
) -> RecoveryProducerInput:
    full, response = item.full_policy, item.original_planning
    assert full is not None and response is not None and response.plan is not None
    plan = response.plan
    if len(plan.lossless_steps) != 1:
        return item
    selected = plan.lossless_steps[0]
    if selected.liquidity_rank != 0 or selected.goal_id is not None:
        return item
    require_installed_full_recovery_guards()
    body = FullRecoveryPrepareRequest(
        policy_id=full.policy_id,
        expected_version_id=full.current_version.version_id,
        expected_epoch_id=full.epoch_id,
        position_id=selected.position_id,
        idempotency_key="boundary-recovery:"
        + str(full.policy_id)
        + ":"
        + str(selected.position_id),
    )
    identity = uuid5(user, recovery_bank_key(body.idempotency_key))
    execution = read_full_recovery_execution_inputs(session, user, body, identity, now)
    effect, _ = build_full_recovery_effect(execution)
    basis = _basis(session, user, now)
    if basis.digest != original.inputs.base.financial_input_hash:
        raise ValueError("RECOVERY_SAME_INVOCATION_FINANCIAL_BASIS_DIFFERS")
    facts = _facts(
        session,
        user,
        RedeemIntent(kind="redeem_asset", position_id=selected.position_id),
        now,
        basis,
        effect=effect,
    )
    _capture_assessment(session, facts)
    capture = session.info.get(CAPTURE_KEY)
    if not isinstance(capture, DecisionCapture):
        raise ValueError("RECOVERY_ACTUAL_FINANCIAL_CAPTURE_MISSING")
    context = ExecutionContext.model_validate_json(json.dumps(capture.inputs["execution_context"]))
    context = require_full_recovery_confirmation_context(session, context)
    # No action_id or old confirmation enters this fresh producer.
    facts = facts.model_copy(
        update={"confirmation": None, "validation": revalidate_execution(effect, context)}
    )
    full_protection = compute_full_annual_protection(session, user, now)
    capture_evidence(session, user, full_protection.source_evidence_ids)
    issues = [row.code for row in full_protection.source_issues]
    if (full_protection.user_id, full_protection.as_of) != (user, now) or (
        not full_protection.projection.full_obligations_complete_within_registered_current_scope
    ):
        issues.append("RECOVERY_COMPLETE_CURRENT_FULL_PROTECTION_NOT_PROVEN")
    bounds, veto = None, None
    assert facts.validation is not None
    if facts.validation.status in {"READY", "CONFIRMATION_REQUIRED"}:
        if facts.validation.projected_snapshot is not None:
            projected = FullProtectionProjectionInput(
                snapshot=facts.validation.projected_snapshot,
                boundary_versions=context.versions,
                positions=facts.validation.projected_positions,
                boundary_products=context.boundary_products,
                policies=full_protection.full_policy_sources,
                reserved_cash_by_account=context.reserved_cash_by_account,
            )
            projection = project_full_protection(projected)
            if projection.source_account_checks:
                bounds = derive_full_account_debit_bounds(
                    effect, context, facts.validation, projected, projection
                )
        veto = validate_full_execution_protection(
            effect,
            context,
            facts.validation,
            full_protection.full_policy_sources,
            source_issues=tuple(issues),
            account_debit_bounds=bounds,
        )
    else:
        # Preserve a real original denial; FULL source verification is still mandatory.
        veto = validate_full_execution_protection(
            effect,
            context,
            facts.validation,
            full_protection.full_policy_sources,
            source_issues=tuple(issues),
        )
    return item.model_copy(
        update={
            "execution_inputs": execution,
            "candidate": CandidateInput(
                candidate_key="redemption:" + str(selected.position_id),
                facts=facts,
                execution_context=context,
                full_protection=veto,
                full_sources=full_protection.full_policy_sources,
                full_source_issues=issues,
                full_account_debit_bounds=bounds,
            ),
        }
    )


def capture_current_recovery_producers(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    original_actual_capture: ActualActionSetCapture | None = None,
) -> RecoveryActionSetCapture:
    """Single clean RR/RO; optional actual capture is only reused inside this invocation."""
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
        or (original.inputs.base.user_id, original.inputs.base.epoch_id, original.inputs.base.as_of)
        != (user_id, epoch.id, now)
    ):
        raise ValueError("RECOVERY_ACTUAL_OWNER_EPOCH_OR_CLOCK_DIFFERS")
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    capture.sources.update(original.originals.sources)
    capture.policies.update(original.originals.policies)
    producers: list[RecoveryProducerInput] = []
    commands: list[RecoveryOriginalCommand] = []
    reasons: list[str] = []
    expected = recovery_policy_ids(original.inputs)
    ids = recovery_action_ids(original.inputs)
    try:
        with session.no_autoflush:
            for identity in ids:
                command_item = RecoveryOriginalCommand(action_id=identity)
                try:
                    raw = next(
                        row
                        for row in original.inputs.base.original_inventory["action_plans"]
                        if row["id"] == str(identity)
                    )
                    recorded = get_decision_trace(
                        session, user_id, UUID(raw["decision_run_id"]), now
                    )
                    if (
                        recorded.completeness != "COMPLETE"
                        or recorded.audit_chain_status != "VALID"
                        or recorded.trace is None
                    ):
                        raise ValueError("RECOVERY_ORIGINAL_PREPARE_TRACE_OR_AUDIT_MISSING")
                    command_item = command_item.model_copy(update={"prepare_trace": recorded.trace})
                except (
                    PolicyLifecycleError,
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                ) as error:
                    command_item = command_item.model_copy(update={"missing_reasons": [str(error)]})
                commands.append(command_item)
            for identity in expected:
                item = RecoveryProducerInput(full_policy_id=identity)
                try:
                    if len(expected) > MAX_PRODUCERS:
                        raise ValueError("RECOVERY_CURRENT_PRODUCER_CAPACITY_EXCEEDED")
                    full = read_full_policy(session, user_id, identity, now)
                    item = item.model_copy(update={"full_policy": full})
                    capture_evidence(session, user_id, full.current_version.evidence_ids)
                    if full.status not in {"REVOKED", "SUSPENDED", "EXPIRED"} and not (
                        full.current_version.valid_until is not None
                        and now >= full.current_version.valid_until
                    ):
                        response = read_full_recovery_planning(session, user_id, identity, now)
                        item = item.model_copy(update={"original_planning": response})
                        planning_inputs = _planning_inputs(session, user_id, full, response, now)
                        item = item.model_copy(update={"planning_inputs": planning_inputs})
                        if response.plan is not None and response.plan.status not in {
                            "NO_RECOVERY_NEEDED",
                            "NOT_TRIGGERED",
                        }:
                            item = _candidate(session, user_id, now, original, item)
                except (
                    PolicyLifecycleError,
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                    OverflowError,
                ) as error:
                    item = item.model_copy(update={"missing_reasons": [str(error)]})
                producers.append(item)
            inputs = RecoveryActionSetInput(
                original_actual_input=original.inputs,
                expected_full_policy_ids=expected,
                original_position_ids=sorted(
                    (
                        UUID(row["id"])
                        for row in original.inputs.base.original_inventory["asset_positions"]
                    ),
                    key=str,
                ),
                original_action_ids=ids,
                original_commands=commands,
                producers=producers,
                source_reasons=reasons,
            )
            result = derive_recovery_producers(inputs)
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)
    return RecoveryActionSetCapture(inputs=inputs, result=result, originals=capture)


def read_current_recovery_producers(
    session: Session, user_id: UUID, now: datetime
) -> RecoveryActionSetResult:
    return capture_current_recovery_producers(session, user_id, now).result


def verify_recovery_source_copies(
    trace: DecisionTrace, data: RecoveryActionSetInput, result: RecoveryActionSetResult
) -> None:
    """For standalone or new composition: complete actual originals remain mandatory."""
    _verify_original_source_copies(
        trace, data.original_actual_input.base, result.recovery_family_complete
    )
    sources = {row.id: row for row in trace.sources}
    for item in data.producers:
        ids = set(item.original_planning.source_evidence_ids if item.original_planning else [])
        if item.full_policy is not None:
            ids.update(item.full_policy.current_version.evidence_ids)
        if item.execution_inputs is not None:
            ids.update(ref.evidence_id for ref in item.execution_inputs.source_refs)
        if item.candidate is not None and item.candidate.facts is not None:
            ids.update(item.candidate.facts.source_evidence_ids)
            ids.update(item.candidate.facts.authority.evidence_ids)
            for full_source in item.candidate.full_sources:
                ids.update(full_source.evidence_ids)
                for reference in full_source.protected_references:
                    ids.update(reference.evidence_ids)
        for identity in ids:
            source = sources.get(identity)
            if (
                source is None
                or source.user_id != result.user_id
                or source.content_integrity != "VERIFIED"
                or source.captured_content_hash != source.content_hash
            ):
                raise ValueError("RECOVERY_ORIGINAL_CURRENT_SOURCE_COPY_MISSING")


def verify_frozen_recovery_producers(trace: DecisionTrace) -> RecoveryActionSetResult:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("recovery_action_producers") != ALGORITHM
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
    ):
        raise ValueError("Not the exact recovery producer algorithm")
    inputs = RecoveryActionSetInput.model_validate_json(
        json.dumps(trace.inputs["recovery_action_set_input"])
    )
    result = derive_recovery_producers(inputs)
    verify_recovery_source_copies(trace, inputs, result)
    if (trace.user_id, trace.as_of) != (result.user_id, result.as_of) or trace.outcome != {
        "recovery_action_set_result": result.model_dump(mode="json")
    }:
        raise ValueError("Original recovery result or owner/clock differs")
    return result
