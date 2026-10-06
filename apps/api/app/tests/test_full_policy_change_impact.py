"""Hand-worked change preview risks, not financial execution or FULL acceptance."""

from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

import pytest
from app.domain.boundary_types import (
    BoundaryPosition,
    BoundaryProduct,
    FixedReturnTerms,
    GoalOwnership,
)
from app.domain.full_policy_change_impact import FullPolicyImpactInput, project_full_policy_change
from app.domain.full_protection_projection import project_full_protection
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_lifecycle import canonical_candidate
from app.tests.test_full_projection import CASH, NOW, emergency
from app.tests.test_full_protection_projection import data, dated, periodic, source
from pydantic import ValidationError


def inputs(
    candidate: dict[str, Any],
    *,
    original: dict[str, Any] | None = None,
    periodic_case: bool = False,
) -> FullPolicyImpactInput:
    template: Literal["DatedExpensePolicy", "PeriodicTransferPolicy"] = (
        "PeriodicTransferPolicy" if periodic_case else "DatedExpensePolicy"
    )
    selected = source(template, original or (periodic() if periodic_case else dated()))
    current = data(selected)
    return FullPolicyImpactInput(
        as_of=NOW,
        timezone="Asia/Shanghai",
        selected_source=selected,
        candidate_configuration=canonical_candidate(template, candidate),
        candidate_valid_from=NOW,
        candidate_valid_until=None,
        candidate_references_verified=True,
        original=project_full_protection(current),
        cash_accounts=current.snapshot.cash_accounts,
        goals=[],
        products=[],
    )


def test_dated_replacement_changes_true_annual_margin_without_confirming_candidate() -> None:
    request = inputs(dated(amount=350))
    before_bytes = request.model_dump_json()
    result = project_full_policy_change(request)
    assert request.model_dump_json() == before_bytes
    assert result.status == "PROJECTED" and result.after is not None and result.before is not None
    assert len(result.after.calculation_trace) == 1098
    assert result.before.safe_idle_cents == 800 and result.after.safe_idle_cents == 650
    assert result.delta_minimum_margin_cents == result.delta_safe_idle_cents == -150
    assert result.after.calculation_trace[7 * 3].cash_cents == 1000
    assert result.after.calculation_trace[7 * 3 + 1].cash_cents == 650
    assert all(row.source_kind == "UNCONFIRMED_CANDIDATE" for row in result.candidate_commitments)
    assert result.grants_authority is False and result.future_income_cents == 0
    assert result.after.financial_capacity_is_authority is False
    assert request.original.full_annual_projection is not None
    assert result.before.boundary_hash == request.original.full_annual_projection.boundary_hash


def test_original_hard_floor_other_policy_reservation_and_goal_ownership_are_retained() -> None:
    selected = source("DatedExpensePolicy", dated())
    other = source("DatedExpensePolicy", dated(day=20, amount=100), identity=70)
    original = data(selected, other)
    owned = GoalOwnership(
        goal_id=UUID(int=90),
        policy_id=UUID(int=91),
        account_id=CASH,
        cash_owned_cents=15,
        principal_owned_cents=25,
        allocated_cents=40,
        evidence_ids=[UUID(int=92)],
    )
    original = original.model_copy(
        update={
            "snapshot": original.snapshot.model_copy(update={"goals": [owned]}),
            "positions": [
                BoundaryPosition(
                    position_id=UUID(int=93),
                    goal_id=owned.goal_id,
                    principal_cents=25,
                    status="HELD",
                )
            ],
            "boundary_versions": [emergency(50)],
            "reserved_cash_by_account": {CASH: 20},
        }
    )
    request = inputs(dated(amount=80)).model_copy(
        update={
            "selected_source": selected,
            "original": project_full_protection(original),
            "goals": [owned],
            "positions": original.positions,
        }
    )
    result = project_full_policy_change(request)
    assert result.after is not None and result.before is not None
    for before, after in zip(
        result.before.calculation_trace, result.after.calculation_trace, strict=True
    ):
        assert (
            after.protected_cents_by_reason["emergency"]
            == before.protected_cents_by_reason["emergency"]
        )
        assert after.protected_cents_by_reason["pending_cash_reservations"] == 20
        assert after.principal_position_ids == before.principal_position_ids
    assert any(
        ":DATED:" in identity and str(other.policy_id) in identity
        for identity in result.retained_original_occurrence_ids
    )
    assert result.goals[0].current_owned_cash_cents == 15
    assert result.goals[0].current_owned_principal_cents == 25
    assert result.goals[0].current_allocation_delta_cents == 0
    assert result.goals[0].future_allocation_cents is None
    assert result.positions[0].current_outstanding_principal_cents == 25
    assert result.positions[0].current_principal_delta_cents == 0
    assert result.positions[0].future_disposition_status == "UNKNOWN_NO_CANDIDATE_ACTION_GENERATION"


def test_today_period_is_frozen_and_future_months_use_candidate_upper_bound_and_clamp() -> None:
    # October 5 is the original unpaid date; it cannot be silently moved to Oct31.
    request = inputs(
        periodic(amount=20, due_day=31), original=periodic(amount=10, due_day=5), periodic_case=True
    )
    result = project_full_policy_change(request)
    assert result.after is not None
    assert len(result.retained_original_occurrence_ids) == 1
    assert result.candidate_commitments[0].due_date == date(2026, 11, 30)
    assert date(2027, 2, 28) in {row.due_date for row in result.candidate_commitments}
    assert (
        result.after.calculation_trace[0].protected_cents_by_reason["full_periodic_transfer"] == 230
    )
    assert result.after.calculation_trace[1].cash_cents == 990
    assert result.delta_minimum_margin_cents == -100


def test_candidate_future_window_and_end_are_respected_without_future_income() -> None:
    request = inputs(periodic(amount=20, due_day=31), periodic_case=True).model_copy(
        update={
            "candidate_valid_from": datetime(2026, 11, 1, tzinfo=UTC),
            "candidate_valid_until": datetime(2027, 1, 1, tzinfo=UTC),
        }
    )
    result = project_full_policy_change(request)
    assert [row.due_date for row in result.candidate_commitments] == [
        date(2026, 11, 30),
        date(2026, 12, 31),
    ]
    assert result.future_income_cents == 0


@pytest.mark.parametrize("issue", ["bank", "income", "audit", "claims"])
def test_unknown_source_never_returns_zero_delta(issue: str) -> None:
    result = project_full_policy_change(
        inputs(dated()).model_copy(update={"source_issues": [issue]})
    )
    assert result.status == "UNKNOWN" and result.after is None
    assert result.delta_safe_idle_cents is None and result.delta_max_allocatable_by_product is None


def test_old_version_unpaid_unknown_is_retained() -> None:
    request = inputs(dated(amount=100))
    selected = request.selected_source
    assert selected is not None
    selected = selected.model_copy(update={"version_number": 2})
    unknown = project_full_protection(data(selected))
    result = project_full_policy_change(
        request.model_copy(update={"selected_source": selected, "original": unknown})
    )
    assert result.status == "UNKNOWN" and result.before is None and result.after is None


@pytest.mark.parametrize(
    "change", [{"candidate_references_verified": False}, {"selected_source": None}]
)
def test_missing_supported_current_reference_has_no_candidate_curve(change: dict[str, Any]) -> None:
    result = project_full_policy_change(inputs(dated()).model_copy(update=change))
    assert result.status == "UNKNOWN" and result.after is None


def test_today_dated_candidate_is_unknown_not_an_invented_new_unpaid_event() -> None:
    result = project_full_policy_change(inputs(dated(day=0)))
    assert result.status == "UNKNOWN" and result.delta_minimum_margin_cents is None


def test_rehashed_original_curve_tamper_and_missing_phase_are_rejected() -> None:
    request = inputs(dated())
    original = request.original
    curve = original.full_annual_projection
    assert curve is not None
    changed = curve.calculation_trace[0].model_copy(update={"cash_cents": 2000})
    forged = curve.model_copy(
        update={
            "calculation_trace": [changed, *curve.calculation_trace[1:]],
            "boundary_hash": configuration_hash({"fake": 1}),
        }
    )
    with pytest.raises(ValueError, match="disagree"):
        project_full_policy_change(
            request.model_copy(
                update={"original": original.model_copy(update={"full_annual_projection": forged})}
            )
        )
    with pytest.raises(ValueError, match="phase denominator"):
        project_full_policy_change(
            request.model_copy(
                update={
                    "original": original.model_copy(
                        update={
                            "full_annual_projection": curve.model_copy(
                                update={"calculation_trace": curve.calculation_trace[:-1]}
                            )
                        }
                    )
                }
            )
        )


def test_fixed_return_phase_caps_retain_original_product_denominator() -> None:
    request = inputs(dated(day=2, amount=300))
    product = BoundaryProduct(
        product_id=UUID(int=80),
        version_number=1,
        asset_class="FIXED_DEPOSIT",
        terms_digest="a" * 64,
        fixed_return=FixedReturnTerms(term_days=2, settlement_delay_days=0),
    )
    source_row = request.selected_source
    assert source_row is not None
    original_data = data(source_row).model_copy(update={"boundary_products": [product]})
    request = request.model_copy(
        update={"original": project_full_protection(original_data), "products": [product]}
    )
    result = project_full_policy_change(request)
    assert result.after is not None
    assert result.after.max_allocatable_by_product[str(product.product_id)] == 700
    assert result.delta_max_allocatable_by_product == {str(product.product_id): -100}
    with pytest.raises(ValueError, match="product denominator"):
        project_full_policy_change(request.model_copy(update={"products": []}))


def test_malformed_candidate_money_and_permission_extra_rejected() -> None:
    request = inputs(dated())
    for config in [
        dated(amount=1) | {"actor": "USER"},
        dated() | {"amount": {"min_cents": 0, "target_cents": 1, "max_cents": True}},
    ]:
        with pytest.raises(ValidationError):
            project_full_policy_change(
                request.model_copy(update={"candidate_configuration": config})
            )
    with pytest.raises(ValidationError):
        FullPolicyImpactInput.model_validate(
            request.model_dump() | {"candidate_references_verified": 1}
        )


def test_candidate_account_shortfall_is_liquidity_risk_and_not_executable_capacity() -> None:
    result = project_full_policy_change(inputs(periodic(amount=100), periodic_case=True))
    assert result.after is not None and result.after.status == "LIQUIDITY_RISK"
    assert result.after.safe_idle_cents == 0 and result.after.minimum_margin_cents == -200
    assert result.after.financial_capacity_is_authority is False


@pytest.mark.parametrize("field", ["planning_confirmation_valid", "references_current"])
def test_candidate_does_not_inherit_an_unverified_current_source(field: str) -> None:
    request = inputs(dated())
    selected = request.selected_source
    assert selected is not None
    changed = selected.model_copy(update={field: False})
    result = project_full_policy_change(request.model_copy(update={"selected_source": changed}))
    assert result.status == "UNKNOWN" and result.after is None


def test_current_original_source_wrong_version_and_duplicate_account_are_rejected() -> None:
    request = inputs(dated())
    selected = request.selected_source
    assert selected is not None
    with pytest.raises(ValueError, match="exact original projected policy version"):
        project_full_policy_change(
            request.model_copy(
                update={
                    "selected_source": selected.model_copy(update={"version_id": UUID(int=123456)})
                }
            )
        )

    with pytest.raises(ValueError, match="unique nonnegative denominator"):
        project_full_policy_change(
            request.model_copy(
                update={"cash_accounts": request.cash_accounts + [request.cash_accounts[0]]}
            )
        )


def test_unrepresented_actual_cash_claim_does_not_enlarge_preview_capacity() -> None:
    result = project_full_policy_change(
        inputs(dated()).model_copy(update={"active_cash_claims_cents": 1})
    )
    assert result.status == "UNKNOWN" and result.after is None
    assert result.delta_safe_idle_cents is None
    assert "ORIGINAL_ACTIVE_CASH_CLAIM_FLOOR_NOT_COMPLETE" in result.reasons
