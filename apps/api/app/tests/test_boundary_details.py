"""Independent display facts and frozen financial-v1 compatibility."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from app.domain import boundary as boundary_domain
from app.domain.boundary import compute_boundary, compute_boundary_with_details
from app.domain.boundary_details_types import BoundaryDisplayDetails
from app.domain.boundary_types import BoundarySnapshot
from app.tests.boundary_display_cases import DisplayCase, cases, rent, snapshot

CASES = cases()


def _at(document: dict[str, Any], dotted_path: str) -> Any:
    value: Any = document
    for key in dotted_path.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_display_uses_independent_hand_worked_facts(case: DisplayCase) -> None:
    computation = compute_boundary_with_details(
        case.snapshot,
        case.policies,
        case.positions,
        case.products,
    )
    document = computation.model_dump(mode="json")
    for path, expected in case.expected.items():
        assert _at(document, path) == expected, path
    details = computation.details
    boundary = computation.boundary
    assert details.as_of == case.snapshot.as_of
    assert details.input_digest == case.snapshot.source_digest
    assert details.boundary_hash == boundary.boundary_hash
    assert BoundaryDisplayDetails.model_validate_json(details.model_dump_json()) == details
    if boundary.status == "INSUFFICIENT_EVIDENCE":
        assert details.next_obligations.items == []
        assert details.next_obligations.items_complete is False
        assert details.current_protection.value is None
        assert details.current_goal_ownership.allocated_cents is None
        assert details.blocking_constraints == boundary.blocking_constraints
        assert details.source_issues == case.snapshot.source_issues
    else:
        assert len(boundary.calculation_trace) == 273
        value = details.current_protection.value
        assert value is not None
        assert value.amounts_by_reason == boundary.calculation_trace[0].protected_cents_by_reason
        goals = details.current_goal_ownership
        assert goals.cash_owned_cents is not None and goals.unassigned_goal_cash_cents is not None
        assert (
            goals.cash_owned_cents + goals.unassigned_goal_cash_cents
            == value.amounts_by_reason["goal_cash"]
        )
        assert len(details.next_obligations.items) <= 20


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_complete_financial_v1_json_matches_pre_deployment_golden(case: DisplayCase) -> None:
    fixture = Path(__file__).with_name("fixtures") / "boundary_display_v1.json"
    goldens = json.loads(fixture.read_text(encoding="utf-8"))
    assert goldens["baseline_boundary_sha256"] == (
        "8cead09ab60c752d4454b9816e9e2d43ad7fec80670c084ec7426fae2bcd011d"
    )
    assert set(goldens["cases"]) == {item.name for item in CASES}
    expected = goldens["cases"][case.name]["result"]
    legacy_entry = compute_boundary(case.snapshot, case.policies, case.positions, case.products)
    new_entry = compute_boundary_with_details(
        case.snapshot,
        case.policies,
        case.positions,
        case.products,
    )
    assert legacy_entry.model_dump(mode="json") == expected
    assert new_entry.boundary.model_dump(mode="json") == expected


def test_b12_both_public_entries_call_one_core_and_do_not_mutate_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_core = boundary_domain._compute_boundary_core
    calls = 0

    def counting_core(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original_core(*args, **kwargs)

    monkeypatch.setattr(boundary_domain, "_compute_boundary_core", counting_core)
    case = next(item for item in CASES if item.name == "B02-exact-period-only")
    before = case.snapshot.model_dump(mode="json")
    compute_boundary(case.snapshot, case.policies, case.positions, case.products)
    assert calls == 1
    compute_boundary_with_details(case.snapshot, case.policies, case.positions, case.products)
    assert calls == 2
    assert case.snapshot.model_dump(mode="json") == before


def test_legacy_entry_does_not_construct_any_public_display_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_display(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("The financial v1 entry constructed a display-only DTO")

    monkeypatch.setattr(boundary_domain, "BoundaryDisplayDetails", fail_display)
    assert compute_boundary(snapshot(), [], [], []).safe_idle_cents == 20_000


@pytest.mark.parametrize("money", [True, 1.25, -1])
def test_invalid_money_still_fails_at_original_input_revalidation(money: Any) -> None:
    base = snapshot()
    bad_cash = base.cash_accounts[0].model_copy(update={"balance_cents": money})
    bad = base.model_copy(update={"cash_accounts": [bad_cash, base.cash_accounts[1]]})
    failures: list[tuple[type[Exception], str]] = []
    for entry in (compute_boundary, compute_boundary_with_details):
        with pytest.raises(ValueError) as error:
            entry(bad, [], [], [])
        failures.append((type(error.value), str(error.value)))
    assert failures[0] == failures[1]


def test_original_capacity_rejection_still_precedes_duplicate_validation() -> None:
    policies = [rent()] * 101
    for entry in (compute_boundary, compute_boundary_with_details):
        with pytest.raises(ValueError, match="Boundary capacity is 100 policies"):
            entry(snapshot(), policies, [], [])


def test_original_historical_month_budget_remains_120_and_rejects_121() -> None:
    assert len(boundary_domain._months(date(2017, 2, 1), date(2027, 1, 1))) == 120
    with pytest.raises(ValueError, match="At most 120 historical policy months are supported"):
        boundary_domain._months(date(2017, 1, 1), date(2027, 1, 1))


def test_original_financial_inputs_do_not_gain_display_fields() -> None:
    names = set(BoundarySnapshot.model_fields)
    assert names.isdisjoint({"next_obligations", "due_date", "boundary_details", "user_id"})


def test_goal_minimum_is_retained_after_deadline_until_original_valid_until() -> None:
    case = next(item for item in CASES if item.name == "B10-goal-contribution-actual")
    computation = compute_boundary_with_details(case.snapshot, case.policies, [], [])
    by_day = {
        point.day: point
        for point in computation.boundary.calculation_trace
        if point.phase == "BEFORE_PAYMENT"
    }
    assert by_day[30].protected_cents_by_reason["goal_minimum"] == 100_000
    assert by_day[41].protected_cents_by_reason["goal_minimum"] == 0
    assert by_day[41].protected_cents_by_reason["goal_cash"] == 100_000
    assert computation.details.current_goal_ownership.cash_owned_cents == 100_000
