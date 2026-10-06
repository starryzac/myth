"""Explicit authored-input to Case V2 conversion; no execution or oracle values.

The complete prior author object is an immutable separately bound original. A
native step list does not authorize unconditional execution of its branches.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.mvp_native_schema import BINDING_METHOD, canonical, strict_json

AUTHOR_PROTOCOL = "bounded-funds-authored-schedule-draft-v2"
PROTOCOL = "bounded-funds-authored-case-schedule-v1"
METHOD = "EXPLICIT_AUTHOR_V2_TO_CASE_V2_WITH_BOUND_CONTROL_SCHEDULE_V1"
ROLES = {
    "ARM_DECISION_OPPORTUNITY",
    "ARM_EXECUTION",
    "ARM_RECOVERY_OPPORTUNITY",
    "COMMON_EXTERNAL_BANK_FACT",
    "COMMON_GOAL_INITIALIZATION",
    "COMMON_INITIAL_ACTOR_CONFIRMATION",
    "COMMON_TRUSTED_BANK_INITIAL_OBSERVATION",
    "COMMON_USER_DECLARATION",
    "CONDITIONAL_RUNTIME_ACTOR",
    "EXPECTED_DTO_REJECTION_PENDING",
    "ORIGINAL_IDENTITY_REPLAY",
    "ORIGINAL_REJECTED_ATTEMPT",
    "ORIGINAL_SERVER_QUOTE",
    "READ_ONLY",
    "REGISTERED_SYNTHETIC_ACTOR_CLARIFICATION",
}
CONDITIONS = {
    "ALWAYS",
    "ACTUAL_CANDIDATE_SELECTED_AND_ORIGINAL_AUTHORITY_READY",
    "ACTUAL_PLANNED_ASK_ONCE_OR_B3_OR_REGISTERED_MANUAL_SELECTION",
    "ACTUAL_UNRESOLVED_DTO_REJECTION_AND_ORIGINAL_ACCOUNT_ACTOR_CLARIFICATION",
    "REGISTERED_ARM_SUPPORTS_DISCOVERY_OR_ORIGINAL_PENDING_RECONCILIATION",
}


class ScheduleRefused(ValueError):
    pass


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise ScheduleRefused(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    check(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "MISSING_SHA")
    return str(value)


def authored_original(root: Path, ref: dict[str, str]) -> tuple[bytes, dict[str, Any]]:
    check(set(ref) == {"path", "sha256"}, "EXACT_AUTHOR_DESCRIPTOR_REQUIRED")
    relative = Path(ref["path"])
    path = (root / relative).resolve()
    check(
        not relative.is_absolute()
        and path.is_relative_to(root.resolve())
        and path.is_file()
        and not path.is_symlink(),
        "AUTHOR_ORIGINAL_OUTSIDE_ROOT",
    )
    raw = path.read_bytes()
    check(sha(raw) == digest(ref["sha256"]), "AUTHOR_ORIGINAL_BYTE_DRIFT")
    author = strict_json(raw)
    check(isinstance(author, dict), "AUTHOR_OBJECT_REQUIRED")
    return raw, author


def compile_case(
    author_raw: bytes,
    author_ref: dict[str, str],
    *,
    seed_sha256: str,
    source_sha256: str,
    design_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Deterministic explicit conversion; actual root/source/freeze checks remain mandatory."""
    check(set(author_ref) == {"path", "sha256"}, "EXACT_AUTHOR_DESCRIPTOR_REQUIRED")
    check(sha(author_raw) == digest(author_ref["sha256"]), "AUTHOR_ORIGINAL_BYTE_DRIFT")
    author = strict_json(author_raw)
    check(isinstance(author, dict) and author.get("protocol") == AUTHOR_PROTOCOL, "AUTHOR_PROTOCOL")
    check(
        author.get("intended_purpose") == "MVP_FROZEN"
        and author.get("status") == "NOT_FROZEN_NOT_RUN",
        "CANNOT_UPGRADE_DEVELOPMENT_OR_RESULTS",
    )
    execution = copy.deepcopy(author.get("execution_input"))
    control = copy.deepcopy(author.get("control_schedule"))
    check(isinstance(execution, dict) and isinstance(control, dict), "MISSING_FULL_AUTHOR_INPUT")
    check(
        execution.get("purpose") == "MVP_FROZEN"
        and execution.get("frozen_case_sha256") is None
        and control.get("direct_unconditional_scenario_run_allowed") is False,
        "AUTHOR_ALREADY_EXECUTED_OR_UNCONDITIONAL",
    )
    steps = execution.get("steps")
    nodes = control.get("nodes")
    check(isinstance(steps, list) and bool(steps) and isinstance(nodes, list), "MISSING_STEPS")
    identifiers = [step.get("step_id") for step in steps if isinstance(step, dict)]
    check(len(identifiers) == len(steps) == len(set(identifiers)), "STEP_IDENTITY_DRIFT")
    native_nodes: list[dict[str, Any]] = []
    clarifications: list[dict[str, Any]] = []
    for node in nodes:
        check(isinstance(node, dict) and node.get("role") in ROLES, "UNSUPPORTED_CONTROL_ROLE")
        if node["role"] == "REGISTERED_SYNTHETIC_ACTOR_CLARIFICATION":
            check(
                node.get("not_nlu_result") is True
                and node.get("actual_actor_log") is None
                and node.get("before_step_id") in identifiers,
                "CLARIFICATION_CANNOT_ASSERT_AN_ACTUAL_ACTOR_OR_NLU_RESULT",
            )
            clarifications.append(node)
            continue
        check(node.get("condition") in CONDITIONS, "UNSUPPORTED_CONTROL_CONDITION")
        check(isinstance(node.get("requires_original_success"), list), "MISSING_DEPENDENCIES")
        native_nodes.append(node)
    check(
        [node.get("step_id") for node in native_nodes] == identifiers,
        "CONTROL_MUST_COVER_EXACT_NATIVE_STEPS_IN_ORDER",
    )
    previous: set[str] = set()
    for node in native_nodes:
        check(
            all(source in previous for source in node["requires_original_success"]),
            "CONTROL_REFERENCE_IS_NOT_STRICTLY_BACKWARD",
        )
        previous.add(node["step_id"])
    check(execution.get("family_id") == author.get("family_id"), "FAMILY_DRIFT")
    initial = execution.get("initial_state")
    check(
        isinstance(initial, dict)
        and initial.get("mode") == "SEED_NEW"
        and initial.get("expected_epoch_id") is None
        and initial.get("seed_version") == "mvp-301-v6",
        "AUTHOR_INITIALIZATION_DRIFT",
    )
    # The trusted driver first initializes the new database and records its real
    # epoch. EXISTING then prevents the original runtime from seeding it again.
    initial["mode"] = "EXISTING"
    execution["scenario_id"] = author["case_id"]
    origin = {
        "method": METHOD,
        "complete_authored_input_ref": copy.deepcopy(author_ref),
        "prior_native_fragment_sha256": sha(canonical(author["execution_input"])),
        "conversions": [
            "SCENARIO_ID_BINDS_ORIGINAL_CASE_ID",
            "SEED_NEW_IS_TRUSTED_DRIVER_INITIALIZATION_BEFORE_EXISTING_RUNTIME",
        ],
        "conditional_control_required": True,
        "human_research": "NOT_STARTED",
        "financial_effect_evidence": False,
    }
    case = {
        "protocol": "bounded-funds-case-input-v2",
        "case_id": author["case_id"],
        "family_id": author["family_id"],
        "intended_purpose": "MVP_FROZEN",
        "seed_version": "mvp-301-v6",
        "seed_sha256": digest(seed_sha256),
        "source_sha256": digest(source_sha256),
        "design_sha256": digest(design_sha256),
        "execution_input": execution,
        "execution_binding": {"protocol": BINDING_METHOD},
        "data_origin": origin,
    }
    schedule = {
        "protocol": PROTOCOL,
        "method": METHOD,
        "case_id": author["case_id"],
        "family_id": author["family_id"],
        "purpose": "MVP_FROZEN",
        "complete_authored_input_ref": copy.deepcopy(author_ref),
        "case_input_sha256": sha(canonical(case)),
        "control_schedule": control,
        "arm_rule_author_inputs": copy.deepcopy(author["arm_rule_author_inputs"]),
        "oracle_denominator_author_draft": copy.deepcopy(author["oracle_registration_draft"]),
        "status": "REGISTERED_CONVERSION_NOT_FROZEN_NOT_EXECUTED",
        "financial_effect_evidence": False,
        "all_unattempted_opportunities_retained": True,
        "b3_recovery_confirmation": "REQUIRES_ACTUAL_SERVICE_SEAM_NOT_IMPLEMENTED",
    }
    return case, schedule


def load_compile(
    root: Path, author_ref: dict[str, str], bindings: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    check(set(bindings) == {"seed_sha256", "source_sha256", "design_sha256"}, "EXACT_CASE_BINDINGS")
    raw, _ = authored_original(root, author_ref)
    case, schedule = compile_case(raw, author_ref, **bindings)
    path = (root / author_ref["path"]).resolve()
    check(path.read_bytes() == raw, "AUTHOR_CHANGED_DURING_THIS_INVOCATION")
    return case, schedule


def write_original(path: Path, value: dict[str, Any]) -> dict[str, str]:
    """No overwrite: the actual output bytes, rather than a label, determine its SHA."""
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": str(path), "sha256": sha(raw)}


def summarize(case: dict[str, Any], schedule: dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": case["case_id"],
            "input_sha256": sha(canonical(case)),
            "schedule_sha256": sha(canonical(schedule)),
            "status": schedule["status"],
            "financial_effect_evidence": False,
        },
        sort_keys=True,
    )
