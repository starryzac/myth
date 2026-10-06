"""Exact new trace/source protocol risks using declared synthetic originals only."""

from typing import Any

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence, TracePolicy
from app.domain.full_dynamic_goal_execution import (
    ALGORITHM,
    MARKER,
    build_full_dynamic_goal_effect,
    read_frozen_full_dynamic_goal_proof,
)
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_dynamic_goal_execution import (
    BANK,
    INCOME,
    MODEL,
    MONTH,
    NOW,
    OWN,
    USER,
    fixture,
)


def frozen_trace() -> DecisionTrace:
    data = fixture()
    contents: dict[Any, dict[str, Any]] = {
        MODEL: data.model_original,
        BANK: {"simulation": True, "synthetic_direct_original": str(BANK)},
        OWN: {"simulation": True, "synthetic_direct_original": str(OWN)},
        MONTH: {"simulation": True, "synthetic_direct_original": str(MONTH)},
    }
    income = data.income.model_copy(
        update={
            "origins": (
                data.income.origins[0].model_copy(
                    update={"bank_evidence_hash": configuration_hash(contents[BANK])}
                ),
            )
        }
    )
    contents[INCOME] = income.model_dump(mode="json")
    data = data.model_copy(
        update={
            "income": income,
            "income_evidence_hash": configuration_hash(contents[INCOME]),
            "source_refs": [
                row.model_copy(
                    update={"content_hash": configuration_hash(contents[row.evidence_id])}
                )
                for row in data.source_refs
            ],
        }
    )
    effect, proof = build_full_dynamic_goal_effect(data, OWN)
    marker = {
        "protocol": ALGORITHM,
        "user_id": str(USER),
        "epoch_id": str(data.epoch_id),
        "request": data.request.model_dump(mode="json"),
        "request_hash": configuration_hash(data.request.model_dump(mode="json")),
        "effect_hash": proof.effect_hash,
        "original_proof": proof.model_dump(mode="json"),
    }
    original_request = {
        MARKER: marker,
        "execution": {"effect": effect.model_dump(mode="json"), "effect_hash": proof.effect_hash},
    }
    sources = [
        TraceEvidence(
            id=key,
            user_id=USER,
            evidence_level="USER_CONFIRMED_POLICY" if key == MODEL else "BANK_CONFIRMED",
            source_type="FULL_GOAL_MODEL_V1"
            if key == MODEL
            else ("SIMULATED_NEW_FUNDS_LEDGER" if key == INCOME else "SYNTHETIC_DIRECT_ORIGINAL"),
            source_ref=str(data.request.goal_id) if key == MODEL else str(key),
            content=value,
            content_hash=configuration_hash(value),
            captured_content_hash=configuration_hash(value),
            content_integrity="VERIFIED",
            status_at_decision="VALID",
            observed_at=data.context.versions[0].confirmed_at if key == MODEL else NOW,
            valid_from=data.context.versions[0].confirmed_at if key == MODEL else NOW,
        )
        for key, value in contents.items()
    ]
    version = data.context.versions[0]
    policy = TracePolicy(
        id=version.version_id,
        user_id=USER,
        policy_id=version.policy_id,
        version_number=1,
        configuration=version.configuration,
        configuration_hash=version.content_hash,
        captured_configuration_hash=version.content_hash,
        configuration_integrity="VERIFIED",
        status_at_decision="ACTIVE",
        confirmed_at=version.confirmed_at,
        valid_from=version.valid_from,
        valid_to=version.valid_until,
    )
    return build_trace(
        run_id=MONTH,
        user_id=USER,
        action_id=effect.operation_id,
        phase="PREPARE",
        as_of=NOW,
        algorithm_versions={MARKER: ALGORITHM},
        inputs={
            "effect": effect.model_dump(mode="json"),
            "execution_context": data.context.model_dump(mode="json"),
            "action_request": original_request,
            "planning": {
                MARKER: {
                    "inputs": data.model_dump(mode="json"),
                    "proof": proof.model_dump(mode="json"),
                }
            },
        },
        sources=sources,
        policies=[policy],
        outcome={"synthetic_direct": True},
    )


def rebuild(trace: DecisionTrace, **changes: Any) -> DecisionTrace:
    body = trace.model_dump(exclude={"schema_version", "simulation", "input_hash", "trace_hash"})
    body.update(changes)
    return build_trace(**body)


def test_new_frozen_exact_originals_recompute_amount_with_complete_denominator() -> None:
    trace = frozen_trace()
    proof = read_frozen_full_dynamic_goal_proof(trace)
    assert proof.status == "VERIFIED_RANGE" and proof.dynamic_cap_cents == 150000
    assert proof.bank_authority is False
    assert proof.effect_hash == trace.inputs["action_request"][MARKER]["effect_hash"]


@pytest.mark.parametrize("denominator", ["sources", "policies"])
def test_removing_original_source_or_version_rejected_even_with_new_consistent_trace_hash(
    denominator: str,
) -> None:
    trace = frozen_trace()
    changed = rebuild(trace, **{denominator: []})
    with pytest.raises(ValueError, match="COMPLETE_FROZEN"):
        read_frozen_full_dynamic_goal_proof(changed)


def test_plausible_success_status_or_cap_not_a_substitute_for_original_source_binding() -> None:
    trace = frozen_trace()
    inputs = trace.model_dump(mode="json")["inputs"]
    inputs["planning"][MARKER]["proof"]["dynamic_cap_cents"] += 1
    with pytest.raises(ValueError, match="MATH_OR_ORIGINAL"):
        read_frozen_full_dynamic_goal_proof(
            rebuild(trace, inputs=inputs, outcome={"status": "SUCCEEDED"})
        )
    inputs = trace.model_dump(mode="json")["inputs"]
    inputs["action_request"][MARKER]["request"]["expected_epoch_id"] = str(MONTH)
    with pytest.raises(ValueError, match="REQUEST_BINDING"):
        read_frozen_full_dynamic_goal_proof(rebuild(trace, inputs=inputs))


def test_legacy_execution_trace_is_not_reinterpreted_as_dynamic() -> None:
    trace = rebuild(
        frozen_trace(), algorithm_versions={"execution": "economic-effect-revalidation-v1"}
    )
    with pytest.raises(ValueError, match="EXACT_DYNAMIC_ALGORITHM"):
        read_frozen_full_dynamic_goal_proof(trace)
