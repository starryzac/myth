"""Immutable joint identity/consent readers; financial state is original rows."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.full_joint_goal_execution_models import (
    FullJointGoalExecutionChild,
    FullJointGoalExecutionConsent,
    FullJointGoalExecutionPlan,
)
from app.db.models import ActionPlan, AuditEpoch, EvidenceItem
from app.domain.boundary_types import BoundaryModel
from app.domain.full_joint_goal_execution import (
    ALGORITHM,
    MARKER,
    FullJointChildPrepareRequest,
    FullJointFrozenPlan,
    FullJointGoalConfirmRequest,
    FullJointGoalPrepareRequest,
    FullJointWholeConsentContent,
    Hash,
    verify_frozen_joint_plan,
    whole_consent_content,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionResponse
from app.services.audit_chain import row_copy
from app.services.decision_trace import get_decision_trace
from app.services.execution import get_action
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import select
from sqlalchemy.orm import Session


def error(code: str, message: str = "联合原件身份、来源或顺序未核实") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 404 if code == "NOT_FOUND" else 409)


def child_binding(plan: FullJointFrozenPlan, number: int) -> FullJointChildPrepareRequest:
    child = plan.children[number - 1]
    return FullJointChildPrepareRequest(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        parent_request_hash=configuration_hash(plan.inputs.request.model_dump(mode="json")),
        epoch_id=plan.epoch_id,
        child_number=number,
        action_id=child.action_id,
    )


def read_joint_plan_original(
    session: Session, user_id: UUID, plan_id: UUID, now: datetime
) -> tuple[
    FullJointGoalExecutionPlan, FullJointFrozenPlan, list[FullJointGoalExecutionChild], AuditEpoch
]:
    parent = session.get(FullJointGoalExecutionPlan, plan_id)
    if parent is None or parent.user_id != user_id:
        raise error("NOT_FOUND")
    try:
        plan = FullJointFrozenPlan.model_validate_json(json.dumps(parent.plan))
        verify_frozen_joint_plan(plan)
    except (ValueError, TypeError, KeyError, StopIteration) as cause:
        raise error("JOINT_FROZEN_PARENT_INVALID") from cause
    epoch = session.get(AuditEpoch, parent.epoch_id)
    body = plan.inputs.request
    if (
        epoch is None
        or epoch.user_id != user_id
        or epoch.status not in {"OPEN", "SEALED"}
        or (
            parent.id,
            parent.user_id,
            parent.epoch_id,
            parent.full_policy_id,
            parent.full_policy_version_id,
            parent.created_at,
            parent.expires_at,
            parent.plan_hash,
            parent.idempotency_key,
        )
        != (
            plan.plan_id,
            plan.user_id,
            plan.epoch_id,
            body.full_policy_id,
            body.expected_full_policy_version_id,
            plan.prepared_at,
            plan.expires_at,
            plan.plan_hash,
            body.idempotency_key,
        )
        or parent.created_at > now
        or parent.request != body.model_dump(mode="json")
        or parent.request_hash != configuration_hash(parent.request)
    ):
        raise error("JOINT_PARENT_OWNER_EPOCH_OR_BODY_DIFFERS")
    rows = list(
        session.scalars(
            select(FullJointGoalExecutionChild)
            .where(FullJointGoalExecutionChild.plan_id == plan_id)
            .order_by(FullJointGoalExecutionChild.child_number)
        )
    )
    if len(rows) != len(plan.children):
        raise error("JOINT_COMPLETE_PERSISTED_CHILD_DENOMINATOR_DIFFERS")
    for row, child in zip(rows, plan.children, strict=True):
        if (
            (
                row.user_id,
                row.epoch_id,
                row.child_number,
                row.goal_id,
                row.original_mvp_version_id,
                row.action_plan_id,
                row.bank_idempotency_key,
                row.created_at,
            )
            != (
                user_id,
                plan.epoch_id,
                child.child_number,
                child.goal_id,
                child.command.effect.policy_version_id,
                child.action_id,
                child.bank_idempotency_key,
                plan.prepared_at,
            )
            or row.command != child.command.model_dump(mode="json")
            or row.command_hash != configuration_hash(row.command)
        ):
            raise error("JOINT_PERSISTED_CHILD_IDENTITY_OR_COMMAND_DIFFERS")
    parent_trace = get_decision_trace(session, user_id, uuid5(plan_id, "planning-proof"), now)
    if (
        parent_trace.trace is None
        or parent_trace.completeness != "COMPLETE"
        or parent_trace.audit_chain_status != "VALID"
        or parent_trace.trace.algorithm_versions.get(MARKER) != ALGORITHM
        or parent_trace.trace.inputs.get("joint_execution_input")
        != plan.inputs.model_dump(mode="json")
        or parent_trace.trace.outcome.get("joint_execution_plan") != parent.plan
    ):
        raise error("JOINT_ORIGINAL_PLANNING_TRACE_OR_AUDIT_NOT_VERIFIED")
    return parent, plan, rows, epoch


def verify_original_joint_child(
    session: Session, plan: FullJointFrozenPlan, number: int, action: ActionPlan, now: datetime
) -> ActionResponse:
    child = plan.children[number - 1]
    binding = child_binding(plan, number)
    if (
        (
            action.id,
            action.user_id,
            action.action_type,
            action.amount_cents,
            action.goal_id,
            action.policy_version_id,
            action.idempotency_key,
        )
        != (
            child.action_id,
            plan.user_id,
            "ALLOCATE_GOAL",
            child.command.effect.amount_cents,
            child.goal_id,
            child.command.effect.policy_version_id,
            child.bank_idempotency_key,
        )
        or action.request_hash != configuration_hash(action.request)
        or action.request.get("execution") != child.command.model_dump(mode="json")
        or action.request.get(MARKER) != binding.model_dump(mode="json")
        or action.autonomy_level != "ASK_ONCE"
    ):
        raise error("JOINT_ORIGINAL_CHILD_ACTION_OR_MARKER_DIFFERS")
    saved = get_decision_trace(session, plan.user_id, action.decision_run_id, now)
    if (
        saved.trace is None
        or saved.completeness != "COMPLETE"
        or saved.audit_chain_status != "VALID"
        or saved.trace.action_id != action.id
        or saved.trace.phase != "PREPARE"
        or saved.trace.inputs.get("action_request") != action.request
        or saved.trace.algorithm_versions.get(MARKER) != ALGORITHM
    ):
        raise error("JOINT_ORIGINAL_CHILD_PREPARE_TRACE_MISSING_OR_DIRTY")
    return get_action(session, plan.user_id, action.id, now)


class FullJointConsentView(BoundaryModel):
    consent_id: UUID
    user_id: UUID
    plan_id: UUID
    epoch_id: UUID
    original_request: FullJointGoalConfirmRequest
    request_hash: Hash
    original_evidence: dict[str, Any]
    evidence_id: UUID
    evidence_hash: Hash
    current_evidence_verified: bool
    current_evidence_status: Literal[
        "CURRENT_EVIDENCE_MATCHED", "RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING"
    ]
    receipt_is_current_authority: Literal[False] = False


def read_joint_consent(
    session: Session, plan: FullJointFrozenPlan, now: datetime, *, current: bool
) -> FullJointConsentView | None:
    row = session.scalar(
        select(FullJointGoalExecutionConsent).where(
            FullJointGoalExecutionConsent.plan_id == plan.plan_id
        )
    )
    if row is None:
        return None
    body = FullJointGoalConfirmRequest.model_validate_json(json.dumps(row.request))
    original = row.original_evidence
    try:
        content = FullJointWholeConsentContent.model_validate_json(json.dumps(original["content"]))
        expected = whole_consent_content(plan, body, content.actor, row.created_at).model_dump(
            mode="json"
        )
    except (ValueError, KeyError, TypeError) as cause:
        raise error("JOINT_ORIGINAL_SIGNED_USER_CONSENT_REQUIRED") from cause
    if (
        (
            row.user_id,
            row.epoch_id,
            row.plan_hash,
            row.idempotency_key,
            body.expected_epoch_id,
            body.reviewed_plan_hash,
        )
        != (
            plan.user_id,
            plan.epoch_id,
            plan.plan_hash,
            body.idempotency_key,
            plan.epoch_id,
            plan.plan_hash,
        )
        or row.request_hash != configuration_hash(row.request)
        or not plan.prepared_at <= row.created_at < plan.expires_at
        or row.created_at > now
        or original.get("id") != str(row.evidence_id)
        or original.get("user_id") != str(plan.user_id)
        or original.get("content") != expected
        or original.get("content_hash") != row.evidence_hash
        or row.evidence_hash != configuration_hash(expected)
        or original.get("evidence_level") != "USER_CONFIRMED_ACTION"
        or original.get("source_type") != "USER_JOINT_GOAL_CONFIRMATION"
        or original.get("source_ref") != str(plan.plan_id)
    ):
        raise error("JOINT_WHOLE_ORIGINAL_CONSENT_DIFFERS")
    actual = session.get(EvidenceItem, row.evidence_id)
    matched = actual is not None and row_copy(actual) == original
    if current and (
        not matched
        or actual is None
        or actual.status != "VALID"
        or not actual.valid_from <= now < plan.expires_at
        or actual.valid_to != plan.expires_at
    ):
        raise error("JOINT_CURRENT_EXACT_WHOLE_CONFIRMATION_REQUIRED")
    saved = get_decision_trace(
        session, plan.user_id, uuid5(plan.plan_id, "whole-confirmation-proof"), now
    )
    if (
        saved.trace is None
        or saved.completeness != "COMPLETE"
        or saved.audit_chain_status != "VALID"
        or saved.trace.algorithm_versions.get(MARKER) != ALGORITHM
        or saved.trace.inputs.get("joint_confirmation_request") != row.request
        or saved.trace.outcome.get("joint_confirmation") != expected
    ):
        raise error("JOINT_ORIGINAL_WHOLE_CONFIRMATION_TRACE_MISSING_OR_DIRTY")
    return FullJointConsentView(
        consent_id=row.id,
        user_id=plan.user_id,
        plan_id=plan.plan_id,
        epoch_id=plan.epoch_id,
        original_request=body,
        request_hash=row.request_hash,
        original_evidence=original,
        evidence_id=row.evidence_id,
        evidence_hash=row.evidence_hash,
        current_evidence_verified=matched,
        current_evidence_status="CURRENT_EVIDENCE_MATCHED"
        if matched
        else "RETAINED_ORIGINAL_CURRENT_EVIDENCE_MISSING",
    )


class FullJointChildView(BoundaryModel):
    child_number: int
    action_id: UUID
    bank_idempotency_key: str
    state: Literal[
        "MISSING",
        "PLANNED_UNRESERVED",
        "AUTHORIZED",
        "SUBMITTED",
        "UNKNOWN",
        "ORIGINAL_RECEIPT_VERIFIED",
        "STOPPED",
    ]
    original_action: ActionResponse | None
    original_request_hash: Hash | None


class FullJointGoalExecutionResponse(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    current_authority_assessed: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    economic_experiment_verified: Literal[False] = False
    funds_reserved: Literal[False] = False
    reservation_scope: Literal["WHOLE_UNRESERVED_CHILDREN_USE_ORIGINAL_CLAIMS"] = (
        "WHOLE_UNRESERVED_CHILDREN_USE_ORIGINAL_CLAIMS"
    )
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    original_plan: FullJointFrozenPlan
    original_request_hash: Hash
    original_consent: FullJointConsentView | None
    children: list[FullJointChildView]
    state: Literal[
        "PARTIALLY_PREPARED",
        "PREPARED_UNRESERVED",
        "CONFIRMED_UNRESERVED",
        "PARTIALLY_SETTLED",
        "UNRESOLVED",
        "ORIGINAL_SERVICE_RECEIPTS_VERIFIED",
        "STOPPED",
        "RETAINED_HISTORY",
    ]


def read_full_joint_goal_execution(
    session: Session, user_id: UUID, plan_id: UUID, now: datetime
) -> FullJointGoalExecutionResponse:
    _read_snapshot(session)
    now = _now(now)
    parent, plan, _, epoch = read_joint_plan_original(session, user_id, plan_id, now)
    consent = read_joint_consent(session, plan, now, current=False)
    children = []
    for child in plan.children:
        action = session.get(ActionPlan, child.action_id)
        state: Literal[
            "MISSING",
            "PLANNED_UNRESERVED",
            "AUTHORIZED",
            "SUBMITTED",
            "UNKNOWN",
            "ORIGINAL_RECEIPT_VERIFIED",
            "STOPPED",
        ] = "MISSING"
        result = None
        if action is not None:
            result = verify_original_joint_child(session, plan, child.child_number, action, now)
            if (
                result.status in {"SUCCEEDED", "RECONCILED"}
                and result.bank_status == "SETTLED"
                and result.receipt is not None
            ):
                state = "ORIGINAL_RECEIPT_VERIFIED"
            elif result.status in {"SUBMITTED", "UNKNOWN"} or result.bank_status is not None:
                state = (
                    "SUBMITTED"
                    if result.status == "SUBMITTED" and result.bank_status is None
                    else "UNKNOWN"
                )
            elif result.status == "PLANNED":
                state = "PLANNED_UNRESERVED"
            elif result.status == "AUTHORIZED":
                state = "AUTHORIZED"
            else:
                state = "STOPPED"
        children.append(
            FullJointChildView(
                child_number=child.child_number,
                action_id=child.action_id,
                bank_idempotency_key=child.bank_idempotency_key,
                state=state,
                original_action=result,
                original_request_hash=action.request_hash if action is not None else None,
            )
        )
    states = {row.state for row in children}
    whole: Literal[
        "PARTIALLY_PREPARED",
        "PREPARED_UNRESERVED",
        "CONFIRMED_UNRESERVED",
        "PARTIALLY_SETTLED",
        "UNRESOLVED",
        "ORIGINAL_SERVICE_RECEIPTS_VERIFIED",
        "STOPPED",
        "RETAINED_HISTORY",
    ] = (
        "RETAINED_HISTORY"
        if epoch.status != "OPEN"
        else "UNRESOLVED"
        if states & {"SUBMITTED", "UNKNOWN"}
        else "STOPPED"
        if "STOPPED" in states
        else "PARTIALLY_PREPARED"
        if "MISSING" in states
        else "ORIGINAL_SERVICE_RECEIPTS_VERIFIED"
        if states == {"ORIGINAL_RECEIPT_VERIFIED"}
        else "PARTIALLY_SETTLED"
        if "ORIGINAL_RECEIPT_VERIFIED" in states
        else "CONFIRMED_UNRESERVED"
        if consent is not None and consent.current_evidence_verified
        else "PREPARED_UNRESERVED"
    )
    return FullJointGoalExecutionResponse(
        user_id=user_id,
        epoch_id=plan.epoch_id,
        as_of=now,
        original_plan=plan,
        original_request_hash=parent.request_hash,
        original_consent=consent,
        children=children,
        state=whole,
    )


class FullJointGoalLookup(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    command_kind: Literal["PREPARE", "CONFIRM"] | None
    original_request: FullJointGoalPrepareRequest | FullJointGoalConfirmRequest | None
    original_request_hash: Hash | None
    original: FullJointGoalExecutionResponse | None
    not_found_is_final: Literal[False] = False
    bank_authority: Literal[False] = False


def lookup_full_joint_goal_execution(
    session: Session, user_id: UUID, key: str, now: datetime
) -> FullJointGoalLookup:
    _read_snapshot(session)
    now = _now(now)
    parents = list(
        session.scalars(
            select(FullJointGoalExecutionPlan).where(
                FullJointGoalExecutionPlan.user_id == user_id,
                FullJointGoalExecutionPlan.idempotency_key == key,
            )
        )
    )
    consents = list(
        session.scalars(
            select(FullJointGoalExecutionConsent).where(
                FullJointGoalExecutionConsent.user_id == user_id,
                FullJointGoalExecutionConsent.idempotency_key == key,
            )
        )
    )
    if len(parents) + len(consents) > 1:
        raise error("JOINT_ORIGINAL_KEY_AMBIGUOUS")
    original = None
    body: FullJointGoalPrepareRequest | FullJointGoalConfirmRequest | None = None
    digest = None
    kind: Literal["PREPARE", "CONFIRM"] | None = None
    if parents:
        row = parents[0]
        original = read_full_joint_goal_execution(session, user_id, row.id, now)
        body = original.original_plan.inputs.request
        digest = row.request_hash
        kind = "PREPARE"
    elif consents:
        consent = consents[0]
        original = read_full_joint_goal_execution(session, user_id, consent.plan_id, now)
        if original.original_consent is None:
            raise error("JOINT_ORIGINAL_CONSENT_NOT_FOUND")
        body = original.original_consent.original_request
        digest = consent.request_hash
        kind = "CONFIRM"
    return FullJointGoalLookup(
        user_id=user_id,
        idempotency_key=key,
        status="RECORDED" if original is not None else "NOT_FOUND_NOT_FINAL",
        command_kind=kind,
        original_request=body,
        original_request_hash=digest,
        original=original,
    )
