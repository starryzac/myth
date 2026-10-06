"""Source-bound fixed joint child recognition and current range proof seams."""

import json
from datetime import datetime
from uuid import UUID, uuid5

from app.db.full_joint_goal_execution_models import FullJointGoalExecutionChild
from app.db.models import ActionPlan, BankOperation
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalPrepareRequest,
    FullDynamicGoalProof,
    derive_full_dynamic_goal_proof,
)
from app.domain.full_joint_goal_execution import (
    ALGORITHM,
    MARKER,
    FullJointChildPrepareRequest,
    FullJointFrozenPlan,
    FullJointWholeConsentContent,
    child_bank_key,
    require_fixed_child,
    verify_frozen_joint_plan,
    whole_consent_content,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch
from app.services.decision_recording import current_capture
from app.services.full_dynamic_goal_execution import read_full_dynamic_goal_inputs
from app.services.full_joint_goal_execution_store import (
    child_binding,
    error,
    read_joint_consent,
    read_joint_plan_original,
    verify_original_joint_child,
)
from app.services.full_policy_lifecycle import read_full_policy
from app.services.policy_lifecycle import _now
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def has_full_joint_goal_binding(session: Session, action: ActionPlan) -> bool:
    if MARKER in action.request or action.idempotency_key.startswith("joint-goal:"):
        return True
    # An indexed durable association also catches simultaneous marker/key
    # stripping. Older schemas have no such table; absence is not permission.
    if session.scalar(text("SELECT to_regclass('full_joint_goal_execution_children')")) is None:
        return False
    return (
        session.scalar(
            select(FullJointGoalExecutionChild.id).where(
                FullJointGoalExecutionChild.action_plan_id == action.id
            )
        )
        is not None
    )


def read_original_joint_child_request(action: ActionPlan) -> FullJointChildPrepareRequest:
    try:
        if configuration_hash(action.request) != action.request_hash:
            raise ValueError("Original complete Action.request hash differs")
        return FullJointChildPrepareRequest.model_validate_json(json.dumps(action.request[MARKER]))
    except (ValueError, KeyError, TypeError) as cause:
        raise error("JOINT_PERSISTED_CHILD_MARKER_REQUIRED") from cause


def _current(
    session: Session,
    user_id: UUID,
    plan: FullJointFrozenPlan,
    number: int,
    now: datetime,
    *,
    own_effect: ExecutionEffect | None = None,
    require_consent: bool,
) -> FullDynamicGoalProof:
    epoch = current_audit_epoch(session, user_id)
    if (
        epoch is None
        or epoch.status != "OPEN"
        or epoch.id != plan.epoch_id
        or not plan.prepared_at <= now < plan.expires_at
    ):
        raise error("JOINT_CURRENT_OPEN_EPOCH_AND_PLAN_WINDOW_REQUIRED")
    full = read_full_policy(session, user_id, plan.inputs.request.full_policy_id, now)
    if (
        full.current_version.version_id != plan.inputs.request.expected_full_policy_version_id
        or full.current_version.content_hash != plan.inputs.scope.current_version.content_hash
        or full.effective_status != "ACTIVE"
        or not full.planning_confirmation_valid
        or full.reference_validation != "CURRENT"
    ):
        raise error("JOINT_CURRENT_FULL_SCOPE_CHANGED")
    if require_consent and read_joint_consent(session, plan, now, current=True) is None:
        raise error("JOINT_EXACT_WHOLE_USER_CONSENT_REQUIRED")
    fixed = plan.children[number - 1]
    # Every goal version/model remains an actual dependency, even a zero result
    # or a previously settled child. Do not silently reduce the original scope.
    from app.services.full_goals import read_full_goal_model

    for goal in plan.allocation_input.goals:
        original = next(
            row.data
            for row in plan.inputs.joint.original_actual_input.dynamic_goals
            if row.data is not None and row.data.request.goal_id == goal.goal_id
        )
        model = read_full_goal_model(session, user_id, goal.goal_id, now)
        if (
            model.status != "VERIFIED"
            or model.evidence_id != original.model_evidence_id
            or model.evidence_hash != original.model_evidence_hash
            or model.base_policy_version_id != goal.effective_policy_version_id
        ):
            raise error("JOINT_COMPLETE_CURRENT_GOAL_VERSION_OR_MODEL_CHANGED")
    original = next(
        row.data
        for row in plan.inputs.joint.original_actual_input.dynamic_goals
        if row.data is not None and row.data.request.goal_id == fixed.goal_id
    )
    if fixed.command.effect.policy_version_id is None:
        raise error("JOINT_ORIGINAL_MVP_VERSION_REQUIRED")
    request = FullDynamicGoalPrepareRequest(
        goal_id=fixed.goal_id,
        expected_policy_version_id=fixed.command.effect.policy_version_id,
        expected_model_evidence_id=original.model_evidence_id,
        expected_model_evidence_hash=original.model_evidence_hash,
        expected_epoch_id=plan.epoch_id,
        idempotency_key=fixed.bank_idempotency_key,
    )
    data = read_full_dynamic_goal_inputs(session, user_id, request, now, own_effect=own_effect)
    # This new public joint protocol always needs a fresh explicit USER action
    # confirmation. Preserve every original financial fact and permission.
    data = data.model_copy(
        update={"context": data.context.model_copy(update={"requires_confirmation": True})}
    )
    proof = derive_full_dynamic_goal_proof(data, fixed.command.effect)
    if proof.status != "VERIFIED_RANGE":
        raise error("JOINT_FIXED_AMOUNT_ORIGINAL_SOURCES_NO_LONGER_VALID", ",".join(proof.reasons))
    return proof


def _fresh(
    engine: Engine,
    user_id: UUID,
    plan: FullJointFrozenPlan,
    number: int,
    now: datetime,
    *,
    own_effect: ExecutionEffect | None = None,
    require_consent: bool,
) -> FullDynamicGoalProof:
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with Session(bind=connection) as session, audit_read_scope(session):
            return _current(
                session,
                user_id,
                plan,
                number,
                now,
                own_effect=own_effect,
                require_consent=require_consent,
            )


def _capture(
    session: Session,
    plan: FullJointFrozenPlan,
    body: FullJointChildPrepareRequest,
    proof: FullDynamicGoalProof,
) -> None:
    capture = current_capture(session)
    if capture is not None:
        from app.services.decision_recording import capture_evidence

        capture.algorithms[MARKER] = ALGORITHM
        capture.inputs[MARKER] = {
            "plan": plan.model_dump(mode="json"),
            "child_request": body.model_dump(mode="json"),
            "current_dynamic_input": proof.inputs.model_dump(mode="json"),
            "current_dynamic_proof": proof.model_dump(mode="json"),
        }
        identities = {ref.evidence_id for ref in proof.inputs.source_refs} | {
            ref.evidence_id for ref in plan.inputs.joint.verified_source_refs
        }
        identities.update(plan.inputs.scope.current_version.evidence_ids)
        consent = read_joint_consent(session, plan, proof.as_of, current=False)
        if consent is not None:
            identities.add(consent.evidence_id)
        capture_evidence(session, plan.user_id, sorted(identities, key=str))


def produce_full_joint_goal_effect(
    session: Session,
    user_id: UUID,
    body: FullJointChildPrepareRequest,
    action_id: UUID,
    now: datetime,
) -> tuple[ExecutionEffect, FullDynamicGoalProof]:
    now = _now(now)
    _, plan, _, _ = read_joint_plan_original(session, user_id, body.plan_id, now)
    fixed = plan.children[body.child_number - 1]
    if (
        body != child_binding(plan, body.child_number)
        or action_id != fixed.action_id
        or fixed.bank_idempotency_key != child_bank_key(plan.plan_id, body.child_number)
    ):
        raise error("JOINT_ORIGINAL_PRIVATE_CHILD_BINDING_DIFFERS")
    bind = session.get_bind()
    proof = _fresh(
        bind if isinstance(bind, Engine) else bind.engine,
        user_id,
        plan,
        body.child_number,
        now,
        require_consent=False,
    )
    _capture(session, plan, body, proof)
    return fixed.command.effect, proof


def _ordered_current(
    session: Session, plan: FullJointFrozenPlan, number: int, now: datetime
) -> None:
    from app.domain.full_joint_goal_execution import FullJointGoalExecuteRequest

    states = []
    for child in plan.children:
        action = session.get(ActionPlan, child.action_id)
        if action is None:
            raise error("JOINT_ALL_CHILDREN_MATERIALIZED_BEFORE_EXECUTION")
        actual = verify_original_joint_child(session, plan, child.child_number, action, now)
        operations = list(
            session.scalars(select(BankOperation).where(BankOperation.action_plan_id == action.id))
        )
        if len(operations) > 1 or any(row.user_id != plan.user_id for row in operations):
            raise error("JOINT_COMPLETE_ORIGINAL_BANK_OPERATION_CONFLICT")
        if child.child_number < number:
            if (
                actual.status not in {"SUCCEEDED", "RECONCILED"}
                or actual.bank_status != "SETTLED"
                or actual.receipt is None
            ):
                raise error("JOINT_PRIOR_CHILD_UNRESOLVED")
            states.append("ORIGINAL_RECEIPT_VERIFIED")
        elif child.child_number > number:
            if (
                operations
                or actual.receipt is not None
                or actual.status not in {"PLANNED", "AUTHORIZED"}
            ):
                raise error("JOINT_LATER_CHILD_ALREADY_SUBMITTED_ORDER_VIOLATION")
            states.append("AUTHORIZED" if actual.status == "AUTHORIZED" else "PLANNED_UNRESERVED")
        else:
            if operations or actual.receipt is not None:
                raise error("JOINT_ORIGINAL_OPERATION_RECOVERY_ONLY")
            states.append(
                "AUTHORIZED"
                if actual.status == "AUTHORIZED"
                else "SUBMITTED"
                if actual.status == "SUBMITTED"
                else "PLANNED_UNRESERVED"
            )
    require_fixed_child(
        plan,
        FullJointGoalExecuteRequest(
            accepted=True,
            reviewed_plan_hash=plan.plan_hash,
            expected_epoch_id=plan.epoch_id,
            expected_child_number=number,
            expected_action_id=plan.children[number - 1].action_id,
        ),
        states,
    )


def recheck_full_joint_goal_proof(
    engine: Engine,
    locked_session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
    require_consent: bool = True,
) -> FullDynamicGoalProof:
    """Called under the original user lock before new claims or bank acceptance."""
    if locked_session.get_bind().engine is not engine or own_action_id not in {None, action.id}:
        raise error("JOINT_ENGINE_OR_OWN_CLAIM_IDENTITY_DIFFERS")
    body = read_original_joint_child_request(action)
    _, plan, _, _ = read_joint_plan_original(locked_session, action.user_id, body.plan_id, now)
    child = plan.children[body.child_number - 1]
    if (
        body != child_binding(plan, body.child_number)
        or action.id != child.action_id
        or command != child.command
    ):
        raise error("JOINT_FIXED_ORIGINAL_EFFECT_BINDING_DIFFERS")
    verify_original_joint_child(locked_session, plan, body.child_number, action, now)
    if require_consent:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as read, audit_read_scope(read):
                _ordered_current(read, plan, body.child_number, now)
    proof = _fresh(
        engine,
        action.user_id,
        plan,
        body.child_number,
        now,
        own_effect=command.effect if own_action_id is not None else None,
        require_consent=require_consent,
    )
    _capture(locked_session, plan, body, proof)
    return proof


def read_current_full_joint_goal_proof(
    session: Session,
    user_id: UUID,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
    *,
    own_action_id: UUID | None = None,
) -> FullDynamicGoalProof:
    body = read_original_joint_child_request(action)
    _, plan, _, _ = read_joint_plan_original(session, user_id, body.plan_id, now)
    if (
        body != child_binding(plan, body.child_number)
        or action.user_id != user_id
        or action.id != plan.children[body.child_number - 1].action_id
        or command != plan.children[body.child_number - 1].command
        or own_action_id not in {None, action.id}
    ):
        raise error("JOINT_READONLY_FIXED_BINDING_DIFFERS")
    return _current(
        session,
        user_id,
        plan,
        body.child_number,
        now,
        own_effect=command.effect if own_action_id is not None else None,
        require_consent=False,
    )


def read_frozen_full_joint_goal_proof(trace: object) -> FullDynamicGoalProof:
    """Root history delegate: exact new inputs/old effect math, never old relabel."""
    from app.domain.decision_trace import verify_trace
    from app.domain.decision_trace_types import DecisionTrace
    from app.domain.full_dynamic_goal_execution import FullDynamicGoalInput

    if not isinstance(trace, DecisionTrace):
        raise ValueError("JOINT_TYPED_TRACE_REQUIRED")
    verify_trace(trace)
    if trace.algorithm_versions.get(MARKER) != ALGORITHM:
        raise ValueError("JOINT_EXACT_NEW_ALGORITHM_REQUIRED")
    raw = trace.inputs["planning"][MARKER]
    plan = FullJointFrozenPlan.model_validate_json(json.dumps(raw["plan"]))
    verify_frozen_joint_plan(plan)
    body = FullJointChildPrepareRequest.model_validate_json(json.dumps(raw["child_request"]))
    data = FullDynamicGoalInput.model_validate_json(json.dumps(raw["current_dynamic_input"]))
    child = plan.children[body.child_number - 1]
    if (
        body != child_binding(plan, body.child_number)
        or (trace.user_id, trace.action_id) != (plan.user_id, child.action_id)
        or trace.as_of != data.context.snapshot.as_of
        or trace.inputs["effect"] != child.command.effect.model_dump(mode="json")
        or trace.inputs["execution_context"] != data.context.model_dump(mode="json")
        or trace.inputs["action_request"].get(MARKER) != body.model_dump(mode="json")
        or trace.inputs["action_request"].get("execution") != child.command.model_dump(mode="json")
        or not data.context.requires_confirmation
    ):
        raise ValueError("JOINT_FROZEN_TRACE_EFFECT_OR_COMPLETE_REQUEST_DIFFERS")
    originals = {row.id: row for row in trace.sources}
    policies = {row.id: row for row in trace.policies}
    if len(originals) != len(trace.sources) or len(policies) != len(trace.policies):
        raise ValueError("JOINT_FROZEN_SOURCE_DENOMINATOR_DUPLICATE")
    for ref in data.source_refs:
        source = originals.get(ref.evidence_id)
        if (
            source is None
            or source.user_id != trace.user_id
            or source.content_integrity != "VERIFIED"
            or source.content_hash != ref.content_hash
            or source.captured_content_hash != ref.content_hash
            or configuration_hash(source.content) != ref.content_hash
            or source.status_at_decision != "VALID"
            or source.observed_at > trace.as_of
            or source.valid_from > trace.as_of
            or source.valid_to is not None
            and trace.as_of >= source.valid_to
        ):
            raise ValueError("JOINT_FROZEN_CURRENT_ORIGINAL_SOURCES_MISSING_OR_DIRTY")
    model = originals[data.model_evidence_id]
    income = originals[data.income_evidence_id]
    from app.domain.full_dynamic_goal_execution import native_income_original_matches

    if (
        model.content != data.model_original
        or model.source_type != "FULL_GOAL_MODEL_V1"
        or model.evidence_level != "USER_CONFIRMED_POLICY"
        or model.source_ref != str(data.request.goal_id)
        or model.observed_at != datetime.fromisoformat(data.model_original["confirmed_at"])
        or income.source_type != "SIMULATED_NEW_FUNDS_LEDGER"
        or income.evidence_level != "BANK_CONFIRMED"
        or not native_income_original_matches(income.content, data.income)
    ):
        raise ValueError("JOINT_FROZEN_ACTUAL_MODEL_AND_INCOME_NOT_BOUND")
    for version in data.context.versions:
        policy_source = policies.get(version.version_id)
        if (
            policy_source is None
            or policy_source.user_id != trace.user_id
            or policy_source.policy_id != version.policy_id
            or policy_source.configuration != version.configuration
            or policy_source.configuration_hash != version.content_hash
            or policy_source.configuration_integrity != "VERIFIED"
            or policy_source.confirmed_at != version.confirmed_at
            or policy_source.valid_from != version.valid_from
            or policy_source.valid_to != version.valid_until
        ):
            raise ValueError("JOINT_FROZEN_CURRENT_MVP_VERSION_NOT_BOUND")
    proof = derive_full_dynamic_goal_proof(data, child.command.effect)
    if proof.status != "VERIFIED_RANGE" or raw["current_dynamic_proof"] != proof.model_dump(
        mode="json"
    ):
        raise ValueError("JOINT_FROZEN_FIXED_CURRENT_PROOF_DIFFERS")
    return proof


def verify_frozen_joint_execution_trace(
    trace: object,
) -> FullJointFrozenPlan | FullDynamicGoalProof:
    """Exact new-version delegate for Root decision/audit historical readers."""
    from app.domain.decision_trace import verify_trace
    from app.domain.decision_trace_types import DecisionTrace

    if not isinstance(trace, DecisionTrace) or trace.algorithm_versions.get(MARKER) != ALGORITHM:
        raise ValueError("JOINT_EXACT_TYPED_VERSION_REQUIRED")
    verify_trace(trace)
    if trace.phase != "EVALUATION":
        return read_frozen_full_joint_goal_proof(trace)
    if trace.action_id is not None:
        raise ValueError("JOINT_PLANNING_TRACE_CANNOT_CLAIM_AN_ACTION")
    plan = FullJointFrozenPlan.model_validate_json(
        json.dumps(trace.outcome["joint_execution_plan"])
    )
    verify_frozen_joint_plan(plan)
    if (
        trace.user_id != plan.user_id
        or trace.as_of < plan.prepared_at
        or trace.inputs.get("joint_execution_input") != plan.inputs.model_dump(mode="json")
    ):
        raise ValueError("JOINT_FROZEN_PLANNING_INPUT_OR_OWNER_DIFFERS")
    raw = {
        row["id"]: row
        for row in plan.inputs.joint.original_actual_input.base.original_inventory["evidence_items"]
    }
    ids = {str(row.evidence_id) for row in plan.inputs.joint.verified_source_refs}
    ids.update(
        row["confirmation"]["confirmation_evidence_id"]
        for row in plan.inputs.joint.original_actual_input.base.original_inventory[
            "full_policy_versions"
        ]
        if row["policy_id"] == str(plan.inputs.request.full_policy_id)
    )
    copied = {str(row.id): row for row in trace.sources}
    if len(copied) != len(trace.sources):
        raise ValueError("JOINT_PLANNING_SOURCE_COPIES_DUPLICATE")
    for identity in ids:
        original = raw[identity]
        source = copied.get(identity)
        if (
            source is None
            or source.user_id != plan.user_id
            or source.content_integrity != "VERIFIED"
            or source.content != original["content"]
            or source.content_hash != original["content_hash"]
            or source.captured_content_hash != source.content_hash
            or source.source_type != original["source_type"]
            or source.source_ref != original["source_ref"]
            or source.evidence_level != original["evidence_level"]
            or source.status_at_decision != original["status"]
            or source.observed_at != datetime.fromisoformat(original["observed_at"])
            or source.valid_from != datetime.fromisoformat(original["valid_from"])
            or source.valid_to
            != (
                None
                if original.get("valid_to") is None
                else datetime.fromisoformat(original["valid_to"])
            )
        ):
            raise ValueError("JOINT_COMPLETE_PLANNING_ORIGINAL_SOURCE_COPIES_REQUIRED")
    if "joint_confirmation" in trace.outcome:
        content = FullJointWholeConsentContent.model_validate_json(
            json.dumps(trace.outcome["joint_confirmation"])
        )
        expected = whole_consent_content(plan, content.original_request, content.actor, trace.as_of)
        identity = str(uuid5(plan.plan_id, "whole-confirmation:" + plan.plan_hash))
        proof = copied.get(identity)
        if (
            content != expected
            or trace.parent_run_id != uuid5(plan.plan_id, "planning-proof")
            or trace.inputs.get("joint_confirmation_request")
            != content.original_request.model_dump(mode="json")
            or proof is None
            or proof.user_id != plan.user_id
            or proof.content_integrity != "VERIFIED"
            or proof.source_type != "USER_JOINT_GOAL_CONFIRMATION"
            or proof.source_ref != str(plan.plan_id)
            or proof.evidence_level != "USER_CONFIRMED_ACTION"
            or proof.status_at_decision != "VALID"
            or proof.content != expected.model_dump(mode="json")
            or proof.content_hash != configuration_hash(proof.content)
            or proof.captured_content_hash != proof.content_hash
            or proof.observed_at != trace.as_of
            or proof.valid_from != trace.as_of
            or proof.valid_to != plan.expires_at
        ):
            raise ValueError("JOINT_WHOLE_CONSENT_ORIGINAL_ACTOR_SOURCE_DIFFERS")
    return plan
