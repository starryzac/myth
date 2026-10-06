"""Money-adjacent branch risks; these TOOL_ONLY responses prove no actual authority."""

import copy
from typing import Any

import pytest

from scripts.mvp_authored_schedule import ScheduleRefused
from scripts.mvp_schedule_control import DISPATCH, plan_dispatch


def fixture(
    arm: str = "P", autonomy: str = "AUTO_EXECUTE"
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    step = {
        "step_id": "execute",
        "kind": "EXECUTE_ACTION",
        "inputs": {"action_id": {"$ref": {"step_id": "prepare", "pointer": "/result/action_id"}}},
    }
    node = {
        "step_id": "execute",
        "role": "ARM_EXECUTION",
        "condition": "ACTUAL_CANDIDATE_SELECTED_AND_ORIGINAL_AUTHORITY_READY",
        "requires_original_success": ["prepare"],
    }
    previous = {
        "prepare": {
            "kind": "PREPARE_ACTION",
            "result": {
                "action_id": "TOOL_ONLY-a",
                "effect_hash": "a" * 64,
                "autonomy_level": autonomy,
                "status": "PLANNED",
            },
        }
    }
    return step, node, previous


@pytest.mark.parametrize("arm", ["B0", "B3"])
def test_every_manual_or_b3_action_requires_this_exact_original_confirmation(arm: str) -> None:
    step, node, previous = fixture(arm)
    assert (
        plan_dispatch(step, node, arm, previous)["status"] == "EXACT_ORIGINAL_CONFIRMATION_REQUIRED"
    )
    previous["confirm"] = {
        "kind": "CONFIRM_ACTION",
        "result": copy.deepcopy(previous["prepare"]["result"]),
    }
    actual = plan_dispatch(step, node, arm, previous)
    assert actual["status"] == DISPATCH and actual["financial_permission_verified"] is False
    assert actual["original_service_authorization_still_required"] is True


@pytest.mark.parametrize(
    "mutation", ["action", "effect", "failed", "skipped", "missing", "duplicate"]
)
def test_another_failed_or_duplicate_confirmation_cannot_enable_dispatch(mutation: str) -> None:
    step, node, previous = fixture(autonomy="ASK_ONCE")
    confirmation: dict[str, Any] = {
        "kind": "CONFIRM_ACTION",
        "result": copy.deepcopy(previous["prepare"]["result"]),
    }
    if mutation == "action":
        confirmation["result"]["action_id"] = "TOOL_ONLY-foreign"
    elif mutation == "effect":
        confirmation["result"]["effect_hash"] = "b" * 64
    elif mutation == "failed":
        confirmation["error"] = {"code": "REJECTED"}
    elif mutation == "skipped":
        confirmation["skip"] = {"status": "NOT_RUN"}
    elif mutation == "missing":
        confirmation.pop("result")
    previous["confirm"] = confirmation
    if mutation == "duplicate":
        previous["another"] = copy.deepcopy(confirmation)
    actual = plan_dispatch(step, node, "P", previous)
    assert actual["status"] == "EXACT_ORIGINAL_CONFIRMATION_REQUIRED"
    assert actual["opportunity_denominator_retained"] is True


@pytest.mark.parametrize("autonomy", ["ADVISE_ONLY", "BLOCKED"])
def test_original_nonexecutable_candidate_never_becomes_ready(autonomy: str) -> None:
    step, node, previous = fixture(autonomy=autonomy)
    assert plan_dispatch(step, node, "P", previous)["status"] == "ORIGINAL_CANDIDATE_NOT_EXECUTABLE"


@pytest.mark.parametrize("mutation", ["error", "skip", "missing", "nonobject"])
def test_missing_actual_result_does_not_invent_a_dependent_action(mutation: str) -> None:
    step, node, previous = fixture()
    if mutation == "error":
        previous["prepare"]["error"] = {"code": "EXPECTED_REFUSAL"}
    elif mutation == "skip":
        previous["prepare"]["skip"] = {"status": "UNSUPPORTED"}
    elif mutation == "missing":
        previous.clear()
    else:
        previous["prepare"]["result"] = None
    actual = plan_dispatch(step, node, "P", previous)
    assert actual["status"] == "DEPENDENT_ORIGINAL_UNAVAILABLE"
    assert actual["financial_effect_evidence"] is False


@pytest.mark.parametrize("arm", ["B1", "B2"])
def test_baseline_unsupported_intent_is_retained_without_cloning_p(arm: str) -> None:
    step = {
        "step_id": "goal",
        "kind": "PREPARE_ACTION",
        "inputs": {"intent": {"kind": "allocate_goal"}},
    }
    node = {
        "step_id": "goal",
        "role": "ARM_DECISION_OPPORTUNITY",
        "condition": "ALWAYS",
        "requires_original_success": [],
    }
    assert (
        plan_dispatch(step, node, arm, {})["status"]
        == "SKIP_BY_REGISTERED_MECHANISM_UNSUPPORTED_INTENT"
    )


@pytest.mark.parametrize("arm", ["B0", "B1", "B2", "B3", "P"])
def test_recovery_discovery_distinction_and_missing_b3_phase_are_explicit(arm: str) -> None:
    step = {
        "step_id": "recovery",
        "kind": "RUN_RECOVERY",
        "inputs": {"idempotency_key": "TOOL_ONLY"},
    }
    node = {
        "step_id": "recovery",
        "role": "ARM_RECOVERY_OPPORTUNITY",
        "condition": "REGISTERED_ARM_SUPPORTS_DISCOVERY_OR_ORIGINAL_PENDING_RECONCILIATION",
        "requires_original_success": [],
    }
    actual = plan_dispatch(step, node, arm, {})
    assert actual["status"] == (
        DISPATCH
        if arm == "P"
        else "NOT_IMPLEMENTED"
        if arm == "B3"
        else "SKIP_BY_REGISTERED_MECHANISM_NO_PROACTIVE_RECOVERY"
    )
    assert actual["opportunity_denominator_retained"] is True


def test_independent_readonly_observation_continues_after_actual_rejection() -> None:
    step = {"step_id": "final", "kind": "SNAPSHOT", "inputs": {}}
    node = {
        "step_id": "final",
        "role": "READ_ONLY",
        "condition": "ALWAYS",
        "requires_original_success": [],
    }
    assert (
        plan_dispatch(step, node, "P", {"failed": {"error": {"code": "REJECTED"}}})["status"]
        == DISPATCH
    )


def test_expected_refusal_attempt_is_dispatched_to_the_real_service() -> None:
    step, node, previous = fixture()
    node["role"] = "ORIGINAL_REJECTED_ATTEMPT"
    node["condition"] = "ALWAYS"
    previous["prepare"]["result"]["status"] = "INVALIDATED"
    assert plan_dispatch(step, node, "P", previous)["status"] == DISPATCH


def test_unresolved_destination_needs_actual_clarification_and_cannot_claim_nlu() -> None:
    step = {
        "step_id": "clarified",
        "kind": "PREPARE_ACTION",
        "inputs": {"intent": {"kind": "transfer_internal"}},
    }
    node = {
        "step_id": "clarified",
        "role": "ARM_DECISION_OPPORTUNITY",
        "condition": "ACTUAL_UNRESOLVED_DTO_REJECTION_AND_ORIGINAL_ACCOUNT_ACTOR_CLARIFICATION",
        "requires_original_success": [],
    }
    assert plan_dispatch(step, node, "P", {})["status"] == "CLARIFICATION_ORIGINAL_REQUIRED"
    # True is an outer source-bound actual observation, never public authority.
    actual = plan_dispatch(step, node, "P", {}, clarification_observed=True)
    assert actual["status"] == DISPATCH and actual["financial_permission_verified"] is False


def test_changed_node_identity_or_protocol_is_refused() -> None:
    step, node, previous = fixture()
    node["step_id"] = "foreign"
    with pytest.raises(ScheduleRefused):
        plan_dispatch(step, node, "P", previous)
