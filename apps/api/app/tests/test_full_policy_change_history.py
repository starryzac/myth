"""Synthetic raw-original risks, not PostgreSQL, browser, or financial acceptance."""

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary_types import (
    BoundaryPosition,
    BoundaryProduct,
    FixedReturnTerms,
    GoalOwnership,
)
from app.domain.full_future_dated_history import REFERENCE_KIND, validate_future_dated_history
from app.domain.full_policy_change_history import (
    FullPolicyHistoryImpactInput,
    project_full_policy_history_change,
)
from app.domain.full_policy_change_impact import (
    FullPolicyImpactInput,
    _original,
    _trace,
    project_full_policy_change,
)
from app.domain.full_policy_configuration import validate_full_configuration
from app.domain.full_protection_projection import project_full_protection
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_future_dated_history import (
    EPOCH,
    NOW,
    USER,
    current_source,
    originals,
    seal,
)
from app.tests.test_full_policy_change_impact import inputs
from app.tests.test_full_projection import CASH, emergency
from app.tests.test_full_protection_projection import data, dated, periodic, source
from pydantic import ValidationError


def fixture(*, amount_delta: int = 100) -> FullPolicyHistoryImpactInput:
    proof = originals()
    selected = current_source(proof).model_copy(
        update={
            "reference_snapshots": [
                {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
            ],
        }
    )
    current = data(selected, cash=100000)
    current = current.model_copy(
        update={"snapshot": current.snapshot.model_copy(update={"as_of": NOW})}
    )
    configuration = deepcopy(selected.configuration)
    configuration["amount"]["max_cents"] += amount_delta
    candidate = validate_full_configuration("DatedExpensePolicy", configuration)
    return FullPolicyHistoryImpactInput(
        user_id=USER,
        epoch_id=EPOCH,
        history_proof=proof,
        original_input=FullPolicyImpactInput(
            as_of=NOW,
            timezone="Asia/Shanghai",
            selected_source=selected,
            candidate_configuration=candidate,
            candidate_valid_from=NOW,
            candidate_valid_until=None,
            candidate_references_verified=True,
            original=project_full_protection(current),
            cash_accounts=current.snapshot.cash_accounts,
            goals=[],
            products=[],
        ),
    )


@pytest.mark.parametrize("delta", [100, -100])
def test_verified_history_repeated_change_uses_original_version_and_all_1098_points(
    delta: int,
) -> None:
    request = fixture(amount_delta=delta)
    original_bytes = request.model_dump_json()
    result = project_full_policy_history_change(request)
    assert request.model_dump_json() == original_bytes
    assert result.status == "PROJECTED" and result.before is not None and result.after is not None
    assert len(result.before.calculation_trace) == len(result.after.calculation_trace) == 1098
    assert result.before.safe_idle_cents == 66695
    assert result.after.safe_idle_cents == 66695 - delta
    assert result.delta_safe_idle_cents == result.delta_minimum_margin_cents == -delta
    before_payment = result.after.calculation_trace[2 * 3]
    after_payment = result.after.calculation_trace[2 * 3 + 1]
    assert before_payment.cash_cents == 100000
    assert after_payment.cash_cents == 66695 - delta
    assert after_payment.protected_cents_by_reason["full_dated_expense"] == 0
    assert result.future_income_cents == 0 and result.grants_authority is False
    assert result.history_proof is not None and result.history_proof.settlement_proven is False
    assert result.history_proof.unpaid_amount_proven is False
    assert result.candidate_commitments[0].source_kind == "UNCONFIRMED_CANDIDATE"
    assert result.input_hash == configuration_hash(request.model_dump(mode="json"))
    assert result.after.curve_hash == configuration_hash(
        {
            "protocol": "hypothetical-full-curve-history-v2",
            "input_hash": result.input_hash,
            "trace": [point.model_dump(mode="json") for point in result.after.calculation_trace],
        }
    )
    assert request.original_input.selected_source is not None
    assert request.original_input.selected_source.version_number == 2
    assert project_full_policy_change(request.original_input).status == "UNKNOWN"
    assert result == project_full_policy_history_change(request)


@pytest.mark.parametrize(
    "tamper", ["owner", "epoch", "count", "command", "evidence", "as_of", "missing"]
)
def test_unchanged_status_and_rehashed_outer_proof_cannot_hide_original_tamper(tamper: str) -> None:
    request = fixture()
    proof = request.history_proof
    assert proof is not None
    proof = proof.model_copy(deep=True)
    if tamper == "owner":
        proof = proof.model_copy(update={"user_id": UUID(int=9999)})
    elif tamper == "epoch":
        proof = proof.model_copy(update={"epoch_id": UUID(int=9999)})
    elif tamper == "count":
        proof = proof.model_copy(update={"actual_version_count": 3})
    elif tamper == "command":
        proof.commands[0]["request"]["body"]["accepted"] = False
    elif tamper == "evidence":
        proof.evidence_originals[0]["status"] = "SUPERSEDED"
    elif tamper == "as_of":
        proof = proof.model_copy(update={"as_of": NOW + timedelta(seconds=1)})
    proof = None if tamper == "missing" else seal(proof)
    result = project_full_policy_history_change(request.model_copy(update={"history_proof": proof}))
    assert result.status == "UNKNOWN" and result.after is None
    assert result.delta_safe_idle_cents is result.delta_minimum_margin_cents is None
    assert result.delta_max_allocatable_by_product is None
    assert "COMPLETE_CURRENT_FUTURE_DATED_HISTORY_NOT_VERIFIED" in result.reasons


@pytest.mark.parametrize(
    "case", ["claims", "cash", "duplicate_cash", "state", "trace", "safe_idle", "product", "floor"]
)
def test_known_history_cannot_hide_current_financial_denominator_or_metric_changes(
    case: str,
) -> None:
    request = fixture()
    original = request.original_input
    before = original.original.full_annual_projection
    assert before is not None
    if case == "claims":
        original = original.model_copy(update={"active_cash_claims_cents": 1})
    elif case == "cash":
        accounts = list(original.cash_accounts)
        accounts[0] = accounts[0].model_copy(update={"balance_cents": 100001})
        original = original.model_copy(update={"cash_accounts": accounts})
    elif case == "duplicate_cash":
        original = original.model_copy(
            update={"cash_accounts": original.cash_accounts + [original.cash_accounts[0]]}
        )
    elif case == "state":
        states = [
            original.original.policy_states[0].model_copy(update={"version_id": UUID(int=9999)})
        ]
        original = original.model_copy(
            update={"original": original.original.model_copy(update={"policy_states": states})}
        )
    elif case == "product":
        original = original.model_copy(
            update={
                "products": [
                    BoundaryProduct(
                        product_id=UUID(int=80),
                        version_number=1,
                        asset_class="FIXED_DEPOSIT",
                        terms_digest="a" * 64,
                        fixed_return=FixedReturnTerms(term_days=1, settlement_delay_days=0),
                    )
                ]
            }
        )
    else:
        update: dict[str, Any] = {}
        if case == "safe_idle":
            update["safe_idle_cents"] = (
                before.safe_idle_cents + 1 if before.safe_idle_cents is not None else 1
            )
        else:
            trace = list(before.calculation_trace)
            if case == "trace":
                trace[0] = trace[0].model_copy(update={"day": 1})
            else:
                trace[0] = trace[0].model_copy(
                    update={
                        "protected_cents_by_reason": {
                            **trace[0].protected_cents_by_reason,
                            "full_seasonal_adopted": 1,
                        }
                    }
                )
            update["calculation_trace"] = trace
        original = original.model_copy(
            update={
                "original": original.original.model_copy(
                    update={
                        "full_annual_projection": before.model_copy(update=update),
                    }
                )
            }
        )
    result = project_full_policy_history_change(
        request.model_copy(update={"original_input": original})
    )
    assert result.status == "UNKNOWN" and result.after is None and result.reasons
    assert result.delta_safe_idle_cents is None


def test_candidate_today_unsupported_periodic_and_old_v1_are_not_promoted() -> None:
    request = fixture()
    configuration = deepcopy(request.original_input.candidate_configuration)
    assert request.history_proof is not None
    configuration["window"]["start"] = request.history_proof.today.isoformat()
    configuration["window"]["end"] = request.history_proof.today.isoformat()
    result = project_full_policy_history_change(
        request.model_copy(
            update={
                "original_input": request.original_input.model_copy(
                    update={
                        "candidate_configuration": validate_full_configuration(
                            "DatedExpensePolicy", configuration
                        ),
                    }
                )
            }
        )
    )
    assert (
        result.status == "UNKNOWN"
        and "CANDIDATE_DATED_TODAY_OR_HISTORY_NOT_PROVEN" in result.reasons
    )
    old = inputs(dated())
    old_bytes, old_result = old.model_dump_json(), project_full_policy_change(old)
    history = project_full_policy_history_change(request.model_copy(update={"original_input": old}))
    assert history.status == "UNKNOWN" and old.model_dump_json() == old_bytes
    assert project_full_policy_change(old) == old_result
    periodic_source = source("PeriodicTransferPolicy", periodic())
    history = project_full_policy_history_change(
        request.model_copy(
            update={
                "original_input": request.original_input.model_copy(
                    update={"selected_source": periodic_source}
                )
            }
        )
    )
    assert history.status == "UNKNOWN" and history.after is None


def test_other_original_dated_commitments_and_cash_claims_remain_protected() -> None:
    request = fixture()
    selected = request.original_input.selected_source
    assert selected is not None
    other = source("DatedExpensePolicy", dated(day=20, amount=250), identity=9000)
    current = data(selected, other, cash=100000)
    owned = GoalOwnership(
        goal_id=UUID(int=9100),
        policy_id=UUID(int=9101),
        account_id=CASH,
        cash_owned_cents=15,
        principal_owned_cents=25,
        allocated_cents=40,
        evidence_ids=[UUID(int=9102)],
    )
    position = BoundaryPosition(
        position_id=UUID(int=9103),
        goal_id=owned.goal_id,
        principal_cents=25,
        status="HELD",
    )
    current = current.model_copy(
        update={
            "snapshot": current.snapshot.model_copy(update={"as_of": NOW, "goals": [owned]}),
            "boundary_versions": [emergency(500)],
            "positions": [position],
            "reserved_cash_by_account": {current.snapshot.cash_accounts[0].account_id: 71},
        }
    )
    original = request.original_input.model_copy(
        update={
            "original": project_full_protection(current),
            "active_cash_claims_cents": 71,
            "goals": [owned],
            "positions": [position],
        }
    )
    result = project_full_policy_history_change(
        request.model_copy(update={"original_input": original})
    )
    assert result.after is not None and result.before is not None
    assert result.delta_safe_idle_cents == -100
    assert len(result.retained_original_occurrence_ids) == 1
    assert str(other.policy_id) in result.retained_original_occurrence_ids[0]
    assert all(
        point.protected_cents_by_reason["pending_cash_reservations"] == 71
        for point in result.after.calculation_trace
    )
    assert result.goals[0].current_owned_cash_cents == 15
    assert result.goals[0].current_allocation_delta_cents == 0
    assert result.goals[0].future_allocation_cents is None
    assert result.positions[0].current_outstanding_principal_cents == 25
    assert result.positions[0].current_principal_delta_cents == 0
    assert all(
        point.protected_cents_by_reason["emergency"] == 500
        for point in result.after.calculation_trace
    )


@pytest.mark.parametrize("offset", [0, -1])
def test_valid_history_of_old_future_versions_does_not_prove_today_or_overdue_current_window(
    offset: int,
) -> None:
    request = fixture()
    proof = request.history_proof
    assert proof is not None
    proof = proof.model_copy(deep=True)
    current = proof.versions[-1]
    configuration = deepcopy(current["configuration"])
    due = proof.today + timedelta(days=offset)
    configuration["window"] = {
        "start": due.isoformat(),
        "end": (due + timedelta(days=1)).isoformat(),
    }
    configuration = validate_full_configuration("DatedExpensePolicy", configuration)
    current["configuration"], current["content_hash"] = (
        configuration,
        configuration_hash(configuration),
    )
    command = proof.commands[-1]
    command["request"]["body"]["configuration"] = deepcopy(configuration)
    command["request"]["body"]["reviewed_hash"] = current["content_hash"]
    command["request_hash"] = configuration_hash(command["request"])
    current["confirmation"]["reviewed_hash"] = current["content_hash"]
    current["confirmation"]["request_hash"] = command["request_hash"]
    evidence = proof.evidence_originals[-1]
    evidence["content"] = deepcopy(current["confirmation"])
    evidence["content_hash"] = configuration_hash(evidence["content"])
    command["result"]["configuration_hash"] = current["content_hash"]
    command["result_hash"] = configuration_hash(command["result"])
    proof = seal(proof.model_copy(update={"current_content_hash": current["content_hash"]}))
    assert validate_future_dated_history(proof)
    selected = current_source(proof).model_copy(
        update={
            "reference_snapshots": [
                {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
            ],
        }
    )
    financial = data(selected, cash=100000)
    financial = financial.model_copy(
        update={"snapshot": financial.snapshot.model_copy(update={"as_of": NOW})}
    )
    result = project_full_policy_history_change(
        request.model_copy(
            update={
                "history_proof": proof,
                "original_input": request.original_input.model_copy(
                    update={
                        "selected_source": selected,
                        "original": project_full_protection(financial),
                    }
                ),
            }
        )
    )
    assert result.status == "UNKNOWN" and result.after is None
    assert "CURRENT_DATED_TODAY_OR_OVERDUE_NOT_PROVEN" in result.reasons


def test_expired_clock_cannot_borrow_a_historical_current_source() -> None:
    request = fixture()
    proof = request.history_proof
    assert proof is not None
    # Complete original proof is bound to Oct6. A later request may not reuse it,
    # even if its caller presents the old stored ACTIVE representation.
    result = project_full_policy_history_change(
        request.model_copy(
            update={
                "original_input": request.original_input.model_copy(
                    update={"as_of": datetime(2026, 11, 1, tzinfo=NOW.tzinfo)}
                ),
            }
        )
    )
    assert result.status == "UNKNOWN" and result.after is None
    assert "COMPLETE_CURRENT_FUTURE_DATED_HISTORY_NOT_VERIFIED" in result.reasons


def test_product_caps_use_whole_original_terms_and_risk_can_lower_safe_idle_to_zero() -> None:
    request = fixture(amount_delta=80000)
    selected = request.original_input.selected_source
    assert selected is not None
    product = BoundaryProduct(
        product_id=UUID(int=80),
        version_number=1,
        asset_class="FIXED_DEPOSIT",
        terms_digest="a" * 64,
        fixed_return=FixedReturnTerms(term_days=2, settlement_delay_days=0),
    )
    current = data(selected, cash=100000)
    current = current.model_copy(
        update={
            "snapshot": current.snapshot.model_copy(update={"as_of": NOW}),
            "boundary_products": [product],
        }
    )
    original = request.original_input.model_copy(
        update={"original": project_full_protection(current), "products": [product]}
    )
    result = project_full_policy_history_change(
        request.model_copy(update={"original_input": original})
    )
    assert result.after is not None and result.after.status == "LIQUIDITY_RISK"
    assert result.after.minimum_margin_cents == -13305 and result.after.safe_idle_cents == 0
    assert result.after.max_allocatable_by_product == {str(product.product_id): 0}
    assert result.delta_max_allocatable_by_product == {str(product.product_id): -66695}


def test_strict_money_candidate_permission_and_false_proof_flags_rejected() -> None:
    request = fixture()
    assert request.history_proof is not None and validate_future_dated_history(
        request.history_proof
    )
    raw = request.model_dump()
    raw["original_input"]["candidate_configuration"]["amount"]["max_cents"] = True
    with pytest.raises(ValidationError):
        project_full_policy_history_change(FullPolicyHistoryImpactInput.model_validate(raw))
    extras: list[dict[str, Any]] = [
        {"bank_authority": True},
        {"client_clock": NOW},
        {"amount_cents": 1},
    ]
    for extra in extras:
        with pytest.raises(ValidationError):
            FullPolicyHistoryImpactInput.model_validate(request.model_dump() | extra)


@pytest.mark.parametrize("change", ["amount", "version", "evidence"])
def test_self_consistent_fake_curve_cannot_replace_verified_current_original_occurrence(
    change: str,
) -> None:
    request = fixture()
    original = request.original_input.original
    before = original.full_annual_projection
    assert before is not None
    occurrence = original.occurrences[0]
    if change == "amount":
        occurrence = occurrence.model_copy(update={"conservative_unpaid_cents": 1})
    elif change == "version":
        occurrence = occurrence.model_copy(update={"policy_version_id": UUID(int=9999)})
    else:
        occurrence = occurrence.model_copy(update={"evidence_ids": [UUID(int=9999)]})
    # Keep the counterfeit arithmetic internally consistent. The rejection
    # must therefore come from the independently verified current originals.
    trace = _trace(original.original_annual_projection, [_original(occurrence)], 0)
    minimum = min(point.margin_cents for point in trace)
    counterfeit = before.model_copy(
        update={
            "calculation_trace": trace,
            "minimum_margin_cents": minimum,
            "safe_idle_cents": max(0, minimum),
            "protected_cents_by_reason": trace[0].protected_cents_by_reason,
        }
    )
    result = project_full_policy_history_change(
        request.model_copy(
            update={
                "original_input": request.original_input.model_copy(
                    update={
                        "original": original.model_copy(
                            update={
                                "occurrences": [occurrence],
                                "full_annual_projection": counterfeit,
                            }
                        )
                    }
                ),
            }
        )
    )
    assert result.status == "UNKNOWN" and result.after is None
    assert "ORIGINAL_DATED_OCCURRENCE_DISAGREES_WITH_VERIFIED_CURRENT_VERSION" in result.reasons
