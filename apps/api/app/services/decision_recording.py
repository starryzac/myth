"""Capture actual deterministic inputs inside their existing transaction boundaries."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.models import ActionPlan, DecisionRun, PolicyVersion
from app.domain.boundary_types import BoundaryResult
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import (
    TraceCandidate,
    TraceConstraint,
    TraceEvidence,
    TracePolicy,
)
from app.domain.execution_types import (
    ConfirmationGrant,
    ExecutionContext,
    ExecutionEffect,
    ExecutionValidation,
)
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryPlan, RecoveryPosition
from app.services.boundary import BoundaryContext
from app.services.decision_trace import (
    capture_available_sources,
    capture_policies,
    evidence_copy,
    record_trace,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

CAPTURE_KEY = "bounded_funds_decision_capture"


@dataclass
class DecisionCapture:
    inputs: dict[str, Any] = field(default_factory=dict)
    sources: dict[UUID, TraceEvidence] = field(default_factory=dict)
    policies: dict[UUID, TracePolicy] = field(default_factory=dict)
    candidates: list[TraceCandidate] = field(default_factory=list)
    algorithms: dict[str, str] = field(default_factory=dict)


def start_capture(session: Session) -> DecisionCapture:
    capture = DecisionCapture()
    session.info[CAPTURE_KEY] = capture
    return capture


def current_capture(session: Session) -> DecisionCapture | None:
    capture = session.info.get(CAPTURE_KEY)
    return capture if isinstance(capture, DecisionCapture) else None


def capture_boundary(session: Session, name: str, context: BoundaryContext) -> None:
    capture = current_capture(session)
    if capture is None:
        return
    capture.inputs[name] = {
        "snapshot": context.snapshot.model_dump(mode="json"),
        "versions": [v.model_dump(mode="json") for v in context.versions],
        "positions": [p.model_dump(mode="json") for p in context.positions],
        "products": [p.model_dump(mode="json") for p in context.products],
        "source_issues": [i.model_dump(mode="json") for i in context.sources.issues],
    }
    for identifier in context.sources.used:
        row = context.sources.evidence.get(identifier)
        if row is not None:
            capture.sources[row.id] = evidence_copy(row)
    capture_versions(session, context.sources.user_id, [v.version_id for v in context.versions])


def capture_versions(session: Session, user_id: UUID, identifiers: list[UUID]) -> None:
    capture = current_capture(session)
    if capture is None:
        return
    for item in capture_policies(session, user_id, set(identifiers)):
        capture.policies[item.id] = item
        row = session.get(PolicyVersion, item.id)
        assert row is not None
        capture_evidence(session, user_id, [UUID(key) for key in row.evidence_ids])


def capture_evidence(session: Session, user_id: UUID, identifiers: list[UUID]) -> None:
    capture = current_capture(session)
    if capture is None:
        return
    sources, missing = capture_available_sources(session, user_id, identifiers)
    for item in sources:
        capture.sources[item.id] = item
    if missing:
        capture.inputs["missing_evidence_references"] = sorted(
            set(capture.inputs.get("missing_evidence_references", []))
            | {str(identifier) for identifier in missing}
        )


def capture_execution_context(
    session: Session, base: BoundaryContext, effect: ExecutionEffect, context: ExecutionContext
) -> None:
    capture = current_capture(session)
    if capture is None:
        return
    capture_boundary(session, "execution_boundary", base)
    capture.inputs["execution_context"] = context.model_dump(mode="json")
    capture_versions(
        session,
        effect.user_id,
        effect.policy_version_ids
        + ([effect.original_policy_version_id] if effect.original_policy_version_id else []),
    )
    identifiers = {key for lot in context.lots for key in lot.evidence_ids}
    if effect.payee_evidence_id:
        identifiers.add(effect.payee_evidence_id)
    if effect.liability:
        identifiers.update(effect.liability.evidence_ids)
    # Sources consulted by the adapter have been copied before later epoch updates.
    capture_evidence(session, effect.user_id, sorted(identifiers))


def boundary_constraints(name: str, result: BoundaryResult) -> list[TraceConstraint]:
    return [
        TraceConstraint(
            constraint_key=f"{name}:{point.day}:{point.phase}",
            policy_version_id=None,
            is_hard=True,
            satisfied=point.margin_cents >= 0,
            required_cents=sum(point.protected_cents_by_reason.values()),
            available_cents=point.cash_cents,
            due_date=point.date,
            calculation=point.model_dump(mode="json"),
            reason_code="CASH_BOUNDARY_POINT",
        )
        for point in result.calculation_trace
    ]


def validation_constraints(validation: ExecutionValidation) -> list[TraceConstraint]:
    constraints: list[TraceConstraint] = []
    for name in (
        "baseline_boundary",
        "projected_boundary",
        "reservation_adjusted_baseline",
        "reservation_adjusted_boundary",
    ):
        result = getattr(validation, name)
        if result is not None:
            constraints.extend(boundary_constraints(name, result))
    for index, reason in enumerate(validation.reasons):
        constraints.append(
            TraceConstraint(
                constraint_key=f"execution_reason:{index}",
                policy_version_id=None,
                is_hard=True,
                satisfied=False if validation.status == "BLOCKED" else None,
                required_cents=None,
                available_cents=None,
                due_date=None,
                calculation={"status": validation.status},
                reason_code=reason,
            )
        )
    return constraints


def record_execution_trace(
    session: Session,
    effect: ExecutionEffect,
    validation: ExecutionValidation,
    now: datetime,
    phase: str,
    *,
    parent_run_id: UUID | None,
    autonomy_level: str,
    existing_run: DecisionRun | None = None,
    intent: dict[str, Any] | None = None,
    confirmation: ConfirmationGrant | None = None,
) -> DecisionRun:
    capture = current_capture(session)
    if capture is None or "execution_context" not in capture.inputs:
        raise ValueError("Decision recording requires the actual captured execution context")
    if confirmation is not None:
        capture_evidence(session, effect.user_id, [confirmation.evidence_id])
    event_digest = configuration_hash(
        {
            "captured_inputs": capture.inputs,
            "validation": validation.model_dump(mode="json"),
            "confirmation": confirmation.model_dump(mode="json") if confirmation else None,
        }
    )
    run_id = (
        existing_run.id
        if existing_run is not None
        else uuid5(effect.operation_id, f"decision-trace:{phase}:{now.isoformat()}:{event_digest}")
    )
    trace = build_trace(
        run_id=run_id,
        user_id=effect.user_id,
        action_id=effect.operation_id,
        parent_run_id=parent_run_id,
        phase=phase,
        as_of=now,
        algorithm_versions={
            **capture.algorithms,
            "execution": "economic-effect-revalidation-v1",
            "boundary": validation.baseline_boundary.algorithm_version,
        },
        inputs={
            "effect": effect.model_dump(mode="json"),
            "execution_context": capture.inputs["execution_context"],
            "planning": capture.inputs,
            "intent": intent,
            "confirmation": confirmation.model_dump(mode="json") if confirmation else None,
        },
        sources=list(capture.sources.values()),
        policies=list(capture.policies.values()),
        constraints=validation_constraints(validation),
        candidates=capture.candidates,
        outcome={
            "validation": validation.model_dump(mode="json"),
            "autonomy_level": autonomy_level,
            "decision_status": "COMPUTED",
        },
    )
    return record_trace(session, trace, existing_run=existing_run)


def record_recovery_plan(
    session: Session,
    run: DecisionRun,
    plan: RecoveryPlan,
    maturity_positions: list[RecoveryPosition],
    now: datetime,
) -> None:
    capture = current_capture(session)
    if capture is None:
        raise ValueError("Recovery must capture its actual planning inputs")
    capture.inputs["action_requests"] = {
        str(action.id): action.request
        for action in session.scalars(
            select(ActionPlan)
            .where(
                ActionPlan.user_id == run.user_id,
                ActionPlan.decision_run_id == run.id,
            )
            .order_by(ActionPlan.id)
        )
    }
    constraints = boundary_constraints("actual_boundary", plan.actual_boundary)
    if plan.projected_boundary is not None:
        constraints.extend(boundary_constraints("projected_boundary", plan.projected_boundary))
    trace = build_trace(
        run_id=run.id,
        user_id=run.user_id,
        action_id=None,
        parent_run_id=None,
        phase="RECOVERY_PLAN",
        as_of=now,
        algorithm_versions={
            "recovery": plan.algorithm_version,
            "boundary": plan.actual_boundary.algorithm_version,
        },
        inputs=capture.inputs,
        sources=list(capture.sources.values()),
        policies=list(capture.policies.values()),
        constraints=constraints,
        candidates=[
            TraceCandidate(
                candidate_key=str(c.position_id),
                kind="RECOVERY_POSITION",
                status=c.decision,
                inputs={"position_id": str(c.position_id)},
                result=c.model_dump(mode="json"),
                reasons=c.reasons,
            )
            for c in plan.candidates
        ],
        outcome={"recovery": plan.model_dump(mode="json"), "decision_status": "COMPUTED"},
    )
    record_trace(session, trace, existing_run=run)
    for item in maturity_positions:
        action_id = uuid5(run.id, f"mature:{item.position_id}")
        action = session.get(ActionPlan, action_id)
        assert action is not None
        maturity_trace = build_trace(
            run_id=uuid5(action_id, "contract-settlement-decision"),
            user_id=run.user_id,
            action_id=action_id,
            parent_run_id=run.id,
            phase="CONTRACT_SETTLEMENT",
            as_of=now,
            algorithm_versions={"recovery": plan.algorithm_version},
            inputs={
                "original_contract": item.model_dump(mode="json"),
                "bank_request": action.request["bank_request"],
                "action_request": action.request,
            },
            sources=list(capture.sources.values()),
            policies=list(capture.policies.values()),
            constraints=[],
            candidates=[],
            outcome={
                "settlement_kind": "ORIGINAL_CONTRACT",
                "new_authority": False,
                "autonomy_level": action.autonomy_level,
                "decision_status": "COMPUTED",
                "financial_evaluation": "NOT_EVALUATED",
                "reasons": ["ORIGINAL_CONTRACT_MATURITY"],
            },
        )
        record_trace(session, maturity_trace)


def record_recovery_bank_acceptance(
    session: Session,
    action: ActionPlan,
    now: datetime,
    validation_inputs: dict[str, Any],
    *,
    contract: bool,
) -> None:
    capture = start_capture(session)
    identifiers = [action.policy_version_id] if action.policy_version_id else []
    command = action.request["bank_request"]
    original = command.get("original_policy_version_id")
    if original:
        identifiers.append(UUID(original))
    capture_versions(session, action.user_id, identifiers)
    from app.services.boundary import load_boundary_context

    context = load_boundary_context(session, action.user_id, now)
    capture_boundary(session, "bank_projection_context", context)
    trace = build_trace(
        run_id=uuid5(action.id, "legacy-bank-accept-decision"),
        user_id=action.user_id,
        action_id=action.id,
        parent_run_id=action.decision_run_id,
        phase="BANK_ACCEPT",
        as_of=now,
        algorithm_versions={"recovery": "whole-position-recovery-v1"},
        inputs={
            "bank_request": command,
            "action_request": action.request,
            "validation_inputs": validation_inputs,
            **capture.inputs,
        },
        sources=list(capture.sources.values()),
        policies=list(capture.policies.values()),
        constraints=[],
        candidates=[],
        outcome={
            "autonomy_level": action.autonomy_level,
            "decision_status": "COMPUTED",
            "settlement_kind": "ORIGINAL_CONTRACT" if contract else "AUTHORIZED_REDEMPTION",
            "new_authority": False,
            "bank_validation_status": "READY",
            "financial_evaluation": "NOT_EVALUATED",
        },
    )
    record_trace(session, trace)
