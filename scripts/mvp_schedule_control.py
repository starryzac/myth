"""Conditional dispatch of frozen nodes; original services retain every money gate."""

from __future__ import annotations

from typing import Any

from scripts.mvp_authored_schedule import CONDITIONS, ROLES, ScheduleRefused, check

ARMS = {"B0", "B1", "B2", "B3", "P"}
DISPATCH = "DISPATCH_ORIGINAL_SERVICE"


def required_sources(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        if "$ref" in value:
            reference = value["$ref"]
            check(
                set(value) == {"$ref"}
                and isinstance(reference, dict)
                and set(reference) == {"step_id", "pointer"}
                and isinstance(reference["step_id"], str),
                "ONLY_ORIGINAL_BACKWARD_OBJECT_REFERENCES",
            )
            result.add(reference["step_id"])
        else:
            for child in value.values():
                result.update(required_sources(child))
    elif isinstance(value, list):
        for child in value:
            result.update(required_sources(child))
    return result


def response(previous: dict[str, Any], identity: str) -> dict[str, Any] | None:
    row = previous.get(identity)
    if not isinstance(row, dict) or "error" in row or "skip" in row:
        return None
    value = row.get("result")
    return value if isinstance(value, dict) else None


def decision(status: str, reason: str, sources: set[str]) -> dict[str, Any]:
    return {
        "protocol": "bounded-funds-schedule-dispatch-decision-v1",
        "status": status,
        "reason": reason,
        "required_original_step_ids": sorted(sources),
        "opportunity_denominator_retained": True,
        "financial_permission_verified": False,
        "financial_effect_evidence": False,
        "original_service_authorization_still_required": True,
    }


def plan_dispatch(
    step: dict[str, Any],
    node: dict[str, Any],
    arm: str,
    previous: dict[str, Any],
    *,
    clarification_observed: bool = False,
) -> dict[str, Any]:
    """A branch choice grants no permission; callers must bind all original inputs first."""
    check(arm in ARMS, "UNREGISTERED_ARM")
    check(
        isinstance(node, dict)
        and node.get("role") in ROLES
        and node.get("step_id") == step.get("step_id")
        and node.get("condition") in CONDITIONS,
        "UNREGISTERED_OR_REBOUND_CONTROL_NODE",
    )
    declared = node.get("requires_original_success")
    check(
        isinstance(declared, list) and all(type(key) is str for key in declared), "BAD_DEPENDENCIES"
    )
    assert isinstance(declared, list)
    dependencies = set(declared) | required_sources(step.get("inputs"))
    missing = {key for key in dependencies if response(previous, key) is None}
    if missing:
        return decision("DEPENDENT_ORIGINAL_UNAVAILABLE", "No result may be invented", missing)
    role, kind = node["role"], step.get("kind")
    if role == "REGISTERED_SYNTHETIC_ACTOR_CLARIFICATION":
        raise ScheduleRefused("ACTOR_NODE_IS_NOT_A_NATIVE_SERVICE_STEP")
    if (
        node["condition"]
        == "ACTUAL_UNRESOLVED_DTO_REJECTION_AND_ORIGINAL_ACCOUNT_ACTOR_CLARIFICATION"
    ):
        if clarification_observed is not True:
            return decision(
                "CLARIFICATION_ORIGINAL_REQUIRED", "No actual actor clarification", dependencies
            )
    if role == "ARM_DECISION_OPPORTUNITY":
        intent = step.get("inputs", {}).get("intent", {})
        if arm in {"B1", "B2"} and intent.get("kind") != "purchase_asset":
            return decision(
                "SKIP_BY_REGISTERED_MECHANISM_UNSUPPORTED_INTENT",
                "Frozen B1/B2 baseline supports GENERAL purchase only",
                dependencies,
            )
    if role == "ARM_RECOVERY_OPPORTUNITY":
        if arm in {"B0", "B1", "B2"}:
            return decision(
                "SKIP_BY_REGISTERED_MECHANISM_NO_PROACTIVE_RECOVERY",
                "Frozen baseline does not discover an Agent recovery",
                dependencies,
            )
        if arm == "B3":
            return decision(
                "NOT_IMPLEMENTED",
                "B3 recovery needs an actual before-bank per-action confirmation seam",
                dependencies,
            )
    if role in {"CONDITIONAL_RUNTIME_ACTOR", "ARM_EXECUTION"}:
        check(kind in {"CONFIRM_ACTION", "EXECUTE_ACTION"}, "WRONG_CONDITIONAL_SERVICE_KIND")
        candidates = [
            response(previous, key)
            for key in dependencies
            if previous[key].get("kind") == "PREPARE_ACTION"
        ]
        candidates = [value for value in candidates if value is not None]
        check(len(candidates) == 1, "ONE_ACTUAL_ORIGINAL_PREPARE_RESPONSE_REQUIRED")
        prepared = candidates[0]
        assert prepared is not None
        autonomy = prepared.get("autonomy_level")
        check(
            autonomy in {"AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"},
            "UNSUPPORTED_ORIGINAL_AUTONOMY_RESPONSE",
        )
        if prepared.get("status") not in {
            "PLANNED",
            "AUTHORIZED",
            "SUBMITTED",
            "UNKNOWN",
            "SUCCEEDED",
        }:
            return decision(
                "NO_SELECTED_ORIGINAL_CANDIDATE", "Original candidate was not ready", dependencies
            )
        if autonomy in {"ADVISE_ONLY", "BLOCKED"}:
            return decision(
                "ORIGINAL_CANDIDATE_NOT_EXECUTABLE",
                "Original candidate is not executable",
                dependencies,
            )
        requires_confirmation = autonomy == "ASK_ONCE" or arm in {"B0", "B3"}
        if role == "CONDITIONAL_RUNTIME_ACTOR" and not requires_confirmation:
            return decision(
                "ACTOR_NOT_REQUIRED_BY_REGISTERED_MECHANISM",
                "P/B1/B2 automatic branch",
                dependencies,
            )
        if role == "ARM_EXECUTION" and requires_confirmation:
            matches = [
                row["result"]
                for row in previous.values()
                if isinstance(row, dict)
                and row.get("kind") == "CONFIRM_ACTION"
                and isinstance(row.get("result"), dict)
                and "error" not in row
                and "skip" not in row
                and row["result"].get("action_id") == prepared.get("action_id")
                and row["result"].get("effect_hash") == prepared.get("effect_hash")
            ]
            if len(matches) != 1:
                return decision(
                    "EXACT_ORIGINAL_CONFIRMATION_REQUIRED",
                    "Dispatch cannot supply or reuse another action's confirmation",
                    dependencies,
                )
    return decision(
        DISPATCH, "Dispatch only; original frozen service applies its actual guards", dependencies
    )
