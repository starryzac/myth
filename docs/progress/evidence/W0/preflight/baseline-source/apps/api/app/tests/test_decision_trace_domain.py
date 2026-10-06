"""Frozen decisions explain actual saved facts and detect bounded-content tampering."""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from app.domain.decision_trace import build_trace, explain_trace, verify_trace
from app.domain.decision_trace_types import (
    MAX_TRACE_BYTES,
    DecisionTrace,
    TraceCandidate,
    TraceConstraint,
    TraceEvidence,
    TracePolicy,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration

NOW = datetime(2026, 10, 4, 8, tzinfo=UTC)
USER, RUN, ACTION, EVIDENCE, POLICY, VERSION = (UUID(int=i) for i in range(1, 7))


def evidence() -> TraceEvidence:
    content = {"balance_cents": 1000, "account_id": "cash:one"}
    return TraceEvidence(
        id=EVIDENCE,
        user_id=USER,
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_BALANCE",
        source_ref="cash:one",
        content=content,
        content_hash=configuration_hash(content),
        captured_content_hash=configuration_hash(content),
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=NOW,
        valid_from=NOW,
    )


def policy() -> TracePolicy:
    configuration = {"amount_cents": 300, "kind": "saved-policy-fact"}
    return TracePolicy(
        id=VERSION,
        user_id=USER,
        policy_id=POLICY,
        version_number=1,
        configuration=configuration,
        configuration_hash=configuration_hash(configuration),
        captured_configuration_hash=configuration_hash(configuration),
        configuration_integrity="VERIFIED",
        status_at_decision="ACTIVE",
        confirmed_at=NOW,
        valid_from=NOW,
    )


def fields() -> dict[str, Any]:
    return {
        "run_id": RUN,
        "user_id": USER,
        "action_id": ACTION,
        "phase": "PREPARE",
        "as_of": NOW,
        "algorithm_versions": {"execution": "economic-effect-revalidation-v1"},
        "inputs": {
            "intent": {"kind": "transfer_internal", "amount_cents": 300},
            "context": {"user_id": str(USER), "cash_cents": 1000},
        },
        "sources": [evidence()],
        "policies": [policy()],
        "constraints": [
            TraceConstraint(
                constraint_key="cash-after-transfer",
                policy_version_id=VERSION,
                is_hard=True,
                satisfied=True,
                required_cents=300,
                available_cents=1000,
                calculation={"margin_cents": 700},
                reason_code="CASH_SUFFICIENT",
            )
        ],
        "candidates": [
            TraceCandidate(
                candidate_key="fixed-deposit:one",
                kind="ASSET",
                status="REJECTED",
                inputs={"product_version_number": 1},
                result={"maximum_cents": 0},
                reasons=["MATURITY_AFTER_OBLIGATION"],
            )
        ],
        "outcome": {
            "decision": {
                "level": "ASK_ONCE",
                "execution_eligible": False,
                "financial_evaluation": "VERIFIED",
                "confirmation_required": True,
                "confirmation_satisfied": False,
                "reasons": ["EXPLICIT_TRANSFER_CONFIRMATION_REQUIRED"],
            },
            "validation": {"status": "CONFIRMATION_REQUIRED"},
            "boundary": {"status": "READY", "safe_idle_cents": 700},
        },
    }


def test_saved_decision_round_trips_and_explains_exact_ask_source() -> None:
    trace = build_trace(**fields())
    loaded = DecisionTrace.model_validate_json(trace.model_dump_json())
    assert loaded == trace
    verify_trace(loaded)
    explanation = explain_trace(loaded)
    assert explanation.level == "ASK_ONCE"
    assert explanation.confirmation_required is True
    assert explanation.confirmation_satisfied is False
    assert explanation.audit_chain == "NOT_IMPLEMENTED"
    assert any("确认" in reason.text for reason in explanation.reasons)
    assert any("outcome.decision.reasons[0]" in r.references for r in explanation.reasons)
    assert any("candidates[0].reasons[0]" in r.references for r in explanation.reasons)


def test_confirmed_ask_is_not_relabelled_auto() -> None:
    data = fields()
    data["outcome"]["decision"].update(execution_eligible=True, confirmation_satisfied=True)
    explained = explain_trace(build_trace(**data))
    assert explained.level == "ASK_ONCE"
    assert explained.confirmation_satisfied is True


def test_hashes_and_explanation_are_stable_and_dictionary_order_independent() -> None:
    data = fields()
    first = build_trace(**data)
    data["inputs"] = dict(reversed(list(data["inputs"].items())))
    second = build_trace(**data)
    assert first.input_hash == second.input_hash
    assert first.trace_hash == second.trace_hash
    assert explain_trace(first) == explain_trace(second)


def test_outcome_changes_content_hash_without_changing_input_hash() -> None:
    data = fields()
    first = build_trace(**data)
    data["outcome"]["decision"]["confirmation_satisfied"] = True
    second = build_trace(**data)
    assert first.input_hash == second.input_hash
    assert first.trace_hash != second.trace_hash


@pytest.mark.parametrize("part", ["inputs", "outcome", "sources", "policies", "candidates"])
def test_single_field_tampering_fails_before_explanation(part: str) -> None:
    trace = build_trace(**fields())
    payload = json.loads(trace.model_dump_json())
    if part == "sources":
        payload[part][0]["content"]["balance_cents"] += 1
    elif part == "policies":
        payload[part][0]["configuration"]["amount_cents"] += 1
    elif part == "candidates":
        payload[part][0]["reasons"] = ["FORGED_SUCCESS"]
    else:
        payload[part]["forged"] = True
    with pytest.raises(ValueError):
        DecisionTrace.model_validate_json(json.dumps(payload))


def test_model_copy_cannot_bypass_hash_checks_and_explanation() -> None:
    trace = build_trace(**fields())
    forged = trace.model_copy(update={"trace_hash": "f" * 64})
    with pytest.raises(ValueError):
        verify_trace(forged)
    with pytest.raises(ValueError):
        explain_trace(forged)


@pytest.mark.parametrize("value", [True, 1.5, float("nan"), float("inf"), "300"])
def test_json_financial_amounts_reject_non_integer_values(value: Any) -> None:
    data = fields()
    data["inputs"]["intent"]["amount_cents"] = value
    with pytest.raises(ValueError):
        build_trace(**data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), {1: "bad"}])
def test_general_json_rejects_non_finite_and_non_json_values(value: Any) -> None:
    data = fields()
    data["outcome"]["other"] = value
    with pytest.raises(ValueError):
        build_trace(**data)


def test_unknown_fields_phase_and_naive_clock_are_rejected() -> None:
    for patch in (
        {"authorized": True},
        {"phase": "EXECUTE_REAL_BANK"},
        {"as_of": NOW.replace(tzinfo=None)},
        {"simulation": False},
        {"simulation": 1},
        {"input_hash": "0" * 64},
    ):
        with pytest.raises(ValueError):
            build_trace(**{**fields(), **patch})


@pytest.mark.parametrize("part", ["sources", "policies", "inputs"])
def test_cross_user_provenance_is_rejected(part: str) -> None:
    data = fields()
    if part == "inputs":
        data[part]["context"]["user_id"] = str(UUID(int=99))
    else:
        data[part][0] = data[part][0].model_copy(update={"user_id": UUID(int=99)})
    with pytest.raises(ValueError):
        build_trace(**data)


@pytest.mark.parametrize("part", ["sources", "policies", "constraints", "candidates"])
def test_duplicate_provenance_and_calculation_keys_are_rejected(part: str) -> None:
    data = fields()
    data[part].append(data[part][0])
    with pytest.raises(ValueError):
        build_trace(**data)


def test_missing_evidence_and_unknown_constraint_do_not_invent_safe_facts() -> None:
    data = fields()
    data["sources"] = []
    data["constraints"] = [
        TraceConstraint(
            constraint_key="unknown",
            is_hard=True,
            satisfied=None,
            required_cents=None,
            available_cents=-100,
            calculation={},
            reason_code="MISSING_BALANCE_EVIDENCE",
        )
    ]
    data["outcome"] = {
        "decision": {
            "level": "ADVISE_ONLY",
            "financial_evaluation": "NOT_EVALUATED",
            "reasons": ["OUTSIDE_AUTHORITY"],
        }
    }
    trace = build_trace(**data)
    explained = explain_trace(trace)
    assert trace.constraints[0].satisfied is None
    assert trace.constraints[0].available_cents == -100
    assert trace.sources == []
    assert any("未评估" in line for line in explained.summary)
    assert not any("700" in line or "安全" in line for line in explained.summary)


def test_unknown_reason_is_preserved_and_never_given_an_invented_formula() -> None:
    data = fields()
    data["outcome"]["decision"]["reasons"] = ["FUTURE_REASON_X"]
    explained = explain_trace(build_trace(**data))
    reason = next(r for r in explained.reasons if r.code == "FUTURE_REASON_X")
    assert "FUTURE_REASON_X" in reason.text
    assert "未提供" in reason.text


def test_deferred_recovery_separates_actual_and_projected_boundaries() -> None:
    data = fields()
    data["phase"] = "RECOVERY_PLAN"
    data["outcome"] = {
        "actual_boundary": {"status": "LIQUIDITY_RISK", "minimum_margin_cents": -100},
        "projected_boundary": {"status": "READY", "minimum_margin_cents": 200},
        "reasons": ["WAITING_FOR_PRINCIPAL"],
    }
    explained = explain_trace(build_trace(**data))
    assert any("实际" in line and "LIQUIDITY_RISK" in line for line in explained.summary)
    assert any("预计" in line and "READY" in line for line in explained.summary)
    assert not any("已恢复" in line for line in explained.summary)


def test_resource_bounds_reject_deep_or_oversized_trace() -> None:
    data = fields()
    value: dict[str, Any] = {}
    for _ in range(34):
        value = {"child": value}
    data["inputs"] = value
    with pytest.raises(ValueError):
        build_trace(**data)
    data["inputs"] = {"huge": "x" * (MAX_TRACE_BYTES + 1)}
    with pytest.raises(ValueError):
        build_trace(**data)


def test_hashes_snapshot_nested_input_instead_of_aliasing_the_callers_dictionary() -> None:
    data = fields()
    trace = build_trace(**data)
    data["inputs"]["intent"]["amount_cents"] = 999
    assert trace.inputs["intent"]["amount_cents"] == 300
    verify_trace(trace)


def test_equal_aware_instants_are_canonical_utc_and_source_windows_remain_frozen() -> None:
    data = fields()
    data["as_of"] = NOW.astimezone(timezone(timedelta(hours=8)))
    trace = build_trace(**data)
    assert trace.as_of.tzinfo == UTC
    assert trace.as_of == NOW
    assert trace.sources[0].status_at_decision == "VALID"


def test_invalid_claimed_source_and_policy_hashes_are_preserved_in_blocked_trace() -> None:
    data = fields()
    data["sources"][0] = data["sources"][0].model_copy(
        update={"content_hash": "f" * 64, "content_integrity": "INVALID"}
    )
    data["policies"][0] = data["policies"][0].model_copy(
        update={"configuration_hash": "f" * 64, "configuration_integrity": "INVALID"}
    )
    data["outcome"]["decision"].update(
        level="BLOCKED", execution_eligible=False, financial_evaluation="REJECTED"
    )
    trace = build_trace(**data)
    verify_trace(trace)
    assert trace.sources[0].content_hash == "f" * 64
    assert trace.sources[0].content_integrity == "INVALID"
    assert trace.policies[0].configuration_integrity == "INVALID"
    explained = explain_trace(trace)
    assert any("来源" in line and "未通过" in line for line in explained.summary)
    assert any("策略" in line and "未通过" in line for line in explained.summary)


def test_forged_integrity_labels_or_capture_hashes_cannot_hide_invalid_sources() -> None:
    for patch in (
        {"content_hash": "f" * 64},
        {"captured_content_hash": "f" * 64},
        {"content_integrity": "INVALID"},
    ):
        data = fields()
        data["sources"][0] = data["sources"][0].model_copy(update=patch)
        with pytest.raises(ValueError):
            build_trace(**data)


def test_fifo_list_order_is_preserved_in_trace_and_changes_input_hash() -> None:
    data = fields()
    data["inputs"]["lots"] = [{"origin": "first"}, {"origin": "second"}]
    first = build_trace(**data)
    data["inputs"]["lots"].reverse()
    second = build_trace(**data)
    assert first.inputs["lots"][0]["origin"] == "first"
    assert first.input_hash != second.input_hash


def test_real_living_reserve_quantile_configuration_is_preserved_and_hash_verified() -> None:
    data = fields()
    configuration = validate_configuration(
        {
            "type": "living_reserve",
            "horizon_days": 14,
            "method": {
                "name": "rolling_window_quantile",
                "lookback_days": 56,
                "quantile": 0.8,
                "essential_categories": ["food"],
                "exclude_one_off": True,
            },
            "extra_buffer_cents": 0,
        }
    )
    updated = data["policies"][0].model_dump(mode="python")
    updated.update(
        configuration=configuration,
        configuration_hash=configuration_hash(configuration),
        captured_configuration_hash=configuration_hash(configuration),
    )
    data["policies"] = [TracePolicy.model_validate(updated)]
    trace = build_trace(**data)
    loaded = DecisionTrace.model_validate_json(trace.model_dump_json())
    verify_trace(loaded)
    assert loaded.policies[0].configuration["method"]["quantile"] == 0.8
    assert loaded.policies[0].configuration_hash == configuration_hash(configuration)


def test_explanation_preserves_constraint_numbers_candidate_yields_and_original_versions() -> None:
    data = fields()
    data["candidates"] = [
        TraceCandidate(
            candidate_key="T1:one",
            kind="ASSET",
            status="FEASIBLE",
            inputs={"product_version_number": 3},
            result={
                "max_allocatable_cents": 700,
                "net_simulated_yield_cents": 12,
                "exit_plan": {"principal_available_at": "2026-10-05T08:00:00Z"},
            },
        )
    ]
    explained = explain_trace(build_trace(**data))
    assert any(str(VERSION) in line and "第 1 版" in line for line in explained.summary)
    constraint = next(r for r in explained.reasons if r.code == "CASH_SUFFICIENT")
    assert "300" in constraint.text and "1000" in constraint.text
    assert "constraints[0].available_cents" in constraint.references
    assert any("T1:one" in line and "700" in line and "12" in line for line in explained.summary)
    assert any("2026-10-05T08:00:00Z" in line for line in explained.summary)


def test_recorded_execution_level_and_original_contract_financial_status_are_explained() -> None:
    data = fields()
    data["outcome"] = {
        "autonomy_level": "ASK_ONCE",
        "validation": {"status": "READY"},
        "financial_evaluation": "NOT_EVALUATED",
        "new_authority": False,
    }
    explanation = explain_trace(build_trace(**data))
    assert explanation.level == "ASK_ONCE"
    assert explanation.financial_evaluation == "NOT_EVALUATED"


def test_bounded_user_amount_options_are_strict_integer_vectors() -> None:
    data = fields()
    data["inputs"]["amount_options_cents"] = [10000, 20000]
    trace = build_trace(**data)
    assert trace.inputs["amount_options_cents"] == [10000, 20000]
    verify_trace(trace)
    for bad in ([True, 20000], [10000, 20000.0], [10000, "20000"]):
        data["inputs"]["amount_options_cents"] = bad
        with pytest.raises(ValueError):
            build_trace(**data)


@pytest.mark.parametrize("part", ["sources", "policies"])
@pytest.mark.parametrize("amount", [True, 1.5, 100])
def test_raw_invalid_money_and_owner_claims_can_be_frozen_as_original_blocked_copies(
    part: str,
    amount: Any,
) -> None:
    data = fields()
    copy = data[part][0].model_dump(mode="python")
    body = "content" if part == "sources" else "configuration"
    claimed = "content_hash" if part == "sources" else "configuration_hash"
    captured = "captured_content_hash" if part == "sources" else "captured_configuration_hash"
    copy[body] = {"amount_cents": amount, "user_id": str(UUID(int=99))}
    copy[claimed] = copy[captured] = configuration_hash(copy[body])
    model = TraceEvidence if part == "sources" else TracePolicy
    data[part] = [model.model_validate(copy)]
    data["outcome"] = {
        "decision": {
            "level": "BLOCKED",
            "financial_evaluation": "REJECTED",
            "reasons": ["SOURCE_EVIDENCE_INCOMPLETE"],
        }
    }
    trace = build_trace(**data)
    verify_trace(trace)
    copied = getattr(trace, part)[0]
    assert copied.user_id == USER
    assert getattr(copied, body) == copy[body]
    assert explain_trace(trace).level == "BLOCKED"
