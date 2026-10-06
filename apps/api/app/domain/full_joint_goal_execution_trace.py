"""Pure checks for the new joint protocol; originals are never rewritten."""

import json
from datetime import datetime
from uuid import uuid5

from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ConfirmationGrant, ExecutionContext, ExecutionEffect
from app.domain.full_joint_goal_execution import (
    ALGORITHM,
    MARKER,
    FullJointFrozenPlan,
    FullJointGoalConfirmRequest,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash


def _whole_consent(trace: DecisionTrace, plan: FullJointFrozenPlan) -> None:
    identity = uuid5(plan.plan_id, "whole-confirmation:" + plan.plan_hash)
    source = next((row for row in trace.sources if row.id == identity), None)
    if source is None:
        raise ValueError("Exact original whole joint USER consent is missing")
    content = source.content
    body = FullJointGoalConfirmRequest.model_validate_json(json.dumps(content["original_request"]))
    actor = LocalActorPrincipal.model_validate_json(json.dumps(content["actor"]))
    confirmed = datetime.fromisoformat(content["confirmed_at"])
    require_local_user(actor, plan.user_id, confirmed)
    expected = {
        "protocol": "joint-goal-whole-consent-v2",
        "simulation": True,
        "user_id": str(plan.user_id),
        "plan_id": str(plan.plan_id),
        "epoch_id": str(plan.epoch_id),
        "plan_hash": plan.plan_hash,
        "accepted": True,
        "original_request": body.model_dump(mode="json"),
        "request_hash": configuration_hash(body.model_dump(mode="json")),
        "confirmed_at": content["confirmed_at"],
        "valid_until": content["valid_until"],
        "actor": actor.model_dump(mode="json"),
        "actor_session_id": str(actor.session_id),
        "actor_role": "USER",
        "authentication_source": "LOCAL_SIGNED_SESSION",
        "human_identity_verified": False,
    }
    if (
        content != expected
        or not body.accepted
        or body.expected_epoch_id != plan.epoch_id
        or body.reviewed_plan_hash != plan.plan_hash
        or not plan.prepared_at <= confirmed <= trace.as_of < plan.expires_at
        or datetime.fromisoformat(content["valid_until"]) != plan.expires_at
        or source.user_id != plan.user_id
        or source.source_type != "USER_JOINT_GOAL_CONFIRMATION"
        or source.source_ref != str(plan.plan_id)
        or source.evidence_level != "USER_CONFIRMED_ACTION"
        or source.status_at_decision != "VALID"
        or source.content_integrity != "VERIFIED"
        or source.content_hash != configuration_hash(expected)
        or source.captured_content_hash != source.content_hash
        or source.observed_at != confirmed
        or source.valid_from != confirmed
        or source.valid_to != plan.expires_at
    ):
        raise ValueError("Whole joint consent cannot be replaced by an actor label")


def verify_frozen_full_joint_goal_trace(trace: DecisionTrace) -> None:
    try:
        _verify(trace)
    except (KeyError, TypeError, StopIteration) as cause:
        raise ValueError("Complete closed original joint trace is required") from cause


def _verify(trace: DecisionTrace) -> None:
    # The delegate is mathematical only: it reads the passed complete originals
    # and does not call Session, a current-rule reader or an audit reader.
    from app.services.full_joint_goal_execution_guards import verify_frozen_joint_execution_trace

    if trace.algorithm_versions.get(MARKER) != ALGORITHM:
        raise ValueError("Exact new joint execution algorithm is required")
    proof = verify_frozen_joint_execution_trace(trace)
    if trace.phase == "EVALUATION":
        if not isinstance(proof, FullJointFrozenPlan):
            raise ValueError("Exact original joint planning identity/outcome is required")
        if set(trace.inputs) == {"joint_execution_input"}:
            if (
                trace.run_id != uuid5(proof.plan_id, "planning-proof")
                or trace.parent_run_id is not None
                or trace.as_of != proof.prepared_at
                or trace.outcome
                != {
                    "joint_execution_plan": proof.model_dump(mode="json"),
                    "decision_status": "COMPUTED",
                }
            ):
                raise ValueError("Exact original joint planning identity/outcome is required")
        else:
            _whole_consent(trace, proof)
            identity = uuid5(proof.plan_id, "whole-confirmation:" + proof.plan_hash)
            original = next(row for row in trace.sources if row.id == identity).content
            if (
                set(trace.inputs) != {"joint_execution_input", "joint_confirmation_request"}
                or trace.run_id != uuid5(proof.plan_id, "whole-confirmation-proof")
                or trace.parent_run_id != uuid5(proof.plan_id, "planning-proof")
                or trace.inputs["joint_confirmation_request"] != original["original_request"]
                or trace.outcome
                != {
                    "joint_execution_plan": proof.model_dump(mode="json"),
                    "joint_confirmation": original,
                    "decision_status": "COMPUTED",
                }
            ):
                raise ValueError("Exact whole joint consent trace cannot be rebound")
        return
    if isinstance(proof, FullJointFrozenPlan) or trace.phase not in {
        "PREPARE",
        "CONFIRM",
        "RESERVE",
        "BANK_ACCEPT",
    }:
        raise ValueError("Unsupported joint child execution phase")
    plan = FullJointFrozenPlan.model_validate_json(
        json.dumps(trace.inputs["planning"][MARKER]["plan"])
    )
    effect = ExecutionEffect.model_validate_json(json.dumps(trace.inputs["effect"]))
    context = ExecutionContext.model_validate_json(json.dumps(trace.inputs["execution_context"]))
    raw = trace.inputs.get("confirmation")
    grant = ConfirmationGrant.model_validate_json(json.dumps(raw)) if raw is not None else None
    validation = revalidate_execution(
        effect, context, confirmation=grant, full_dynamic_goal_proof=proof
    )
    if (
        not context.requires_confirmation
        or trace.outcome.get("autonomy_level") != "ASK_ONCE"
        or trace.outcome.get("decision_status") != "COMPUTED"
        or trace.outcome.get("validation") != validation.model_dump(mode="json")
    ):
        raise ValueError("Exact joint child decision cannot gain AUTO authority")
    if trace.phase == "PREPARE":
        if (
            grant is not None
            or validation.status != "CONFIRMATION_REQUIRED"
            or trace.run_id != uuid5(effect.operation_id, "decision")
            or trace.parent_run_id is not None
        ):
            raise ValueError("Joint prepare is an unconfirmed original ASK proposal")
        return
    _whole_consent(trace, plan)
    if (
        grant is None
        or validation.status != "READY"
        or trace.parent_run_id != uuid5(effect.operation_id, "decision")
    ):
        raise ValueError("Each joint child requires its original exact effect consent")
    source = next((row for row in trace.sources if row.id == grant.evidence_id), None)
    if source is None:
        raise ValueError("Original joint child USER evidence is missing")
    content = source.content
    if (
        source.id != uuid5(effect.operation_id, "confirmation:" + grant.effect_hash)
        or source.user_id != effect.user_id
        or source.evidence_level != "USER_CONFIRMED_ACTION"
        or source.source_type != "USER_ACTION_CONFIRMATION"
        or source.source_ref != str(effect.operation_id)
        or source.status_at_decision != "VALID"
        or source.content_integrity != "VERIFIED"
        or source.content_hash != configuration_hash(content)
        or source.captured_content_hash != source.content_hash
        or content.get("simulation") is not True
        or content.get("accepted") is not True
        or content.get("user_id") != str(effect.user_id)
        or content.get("action_id") != str(effect.operation_id)
        or content.get("effect_hash") != grant.effect_hash
        or datetime.fromisoformat(content["confirmed_at"]) != grant.confirmed_at
        or datetime.fromisoformat(content["valid_until"]) != grant.expires_at
        or source.observed_at != grant.confirmed_at
        or source.valid_from != grant.confirmed_at
        or source.valid_to != grant.expires_at
        or not grant.confirmed_at <= trace.as_of < grant.expires_at
    ):
        raise ValueError("Original joint child consent cannot be replaced or rebound")
