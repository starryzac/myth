"""Literal adopted floors and full consumers; synthetic data, not financial acceptance."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.boundary_types import BoundarySnapshot, CashFact
from app.domain.execution import revalidate_execution
from app.domain.execution_types import CashUse, ExecutionContext, ExecutionEffect
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_joint_goal_planning import bind_full_joint_input
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_seasonal_protection import (
    ALGORITHM,
    FLOOR,
    derive_seasonal_protection_bindings,
)
from app.domain.multi_goal_allocation import (
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
)
from app.tests.test_full_seasonal_protection_bindings import bound_source

A, B = UUID(int=601), UUID(int=602)


def projection_input(*, adopted: bool = True) -> FullProtectionProjectionInput:
    source, proof = bound_source()
    if not adopted:
        source = source.model_copy(update={"reference_snapshots": []})
    return FullProtectionProjectionInput(
        snapshot=BoundarySnapshot(
            as_of=proof.as_of,
            timezone="Asia/Shanghai",
            cash_accounts=[
                CashFact(
                    account_id=A, account_type="CASH", balance_cents=5000, observed_at=proof.as_of
                )
            ],
        ),
        boundary_versions=[],
        positions=[],
        boundary_products=[],
        policies=[source],
    )


def test_explicit_v3_floor_1098_points_releases_without_payment_and_preserves_old_curves() -> None:
    inputs = projection_input()
    before = inputs.model_dump_json()
    result = project_full_protection(inputs)
    original = project_full_protection(projection_input(adopted=False))
    assert result.algorithm_version == ALGORITHM and result.seasonal_status == "ADOPTED_PROTECTED"
    assert result.seasonal_adopted_adjustment_cents == 1800
    curve = result.full_annual_projection
    assert curve is not None and curve.minimum_margin_cents == 3200
    assert len(curve.calculation_trace) == 1098 and result.occurrences == []
    assert result.original_execution_view == original.original_execution_view
    assert result.original_annual_projection == original.original_annual_projection
    _, proof = bound_source()
    assert proof.original is not None
    for point in curve.calculation_trace:
        expected = 1800 if point.date <= proof.original.scope.protection_end else 0
        assert point.cash_cents == 5000 and point.protected_cents_by_reason[FLOOR] == expected
        assert point.margin_cents == 5000 - expected
    assert original.algorithm_version == "registered-full-protection-v1"
    assert original.seasonal_adopted_adjustment_cents is None
    assert original.seasonal_status == "ADVICE_ONLY_NO_ADOPTED_AMOUNT"
    assert original.full_annual_projection is not None
    assert all(
        FLOOR not in point.protected_cents_by_reason
        for point in original.full_annual_projection.calculation_trace
    )
    assert inputs.model_dump_json() == before


def test_unknown_adoption_has_null_curve_and_amount_never_advice_or_zero() -> None:
    inputs = projection_input()
    inputs.policies[0].reference_snapshots[0]["proof"]["status"] = "UNKNOWN"
    result = project_full_protection(inputs)
    assert result.algorithm_version == ALGORITHM and result.status == "UNKNOWN"
    assert result.seasonal_status == "ADOPTED_AMOUNT_UNKNOWN"
    assert (
        result.seasonal_adopted_adjustment_cents is None and result.full_annual_projection is None
    )


def test_fresh_joint_consumer_binds_each_floor_and_refuses_missing_or_tampered_proof() -> None:
    inputs = projection_input()
    result = project_full_protection(inputs)
    source, proof = bound_source()
    evidence = derive_seasonal_protection_bindings(
        [source], proof.as_of, "Asia/Shanghai"
    ).evidence()
    refs = [
        SourceReference(user_id=proof.user_id, evidence_id=key, content_hash=value)
        for key, value in evidence.items()
    ]
    points = [
        HardProtectionPoint(
            date=row.date,
            cash_cents=row.cash_cents,
            obligation_floor_cents=0,
            living_floor_cents=0,
            emergency_floor_cents=0,
            owned_goal_cash_cents=0,
            other_protection_floor_cents=0,
            source_refs=refs,
        )
        for row in result.original_annual_projection.calculation_trace
    ]
    original = MultiGoalAllocationInput(
        user_id=proof.user_id,
        as_of=proof.as_of,
        timezone="Asia/Shanghai",
        income_lots=[],
        goals=[],
        hard_protection_points=points,
    )
    bound = bind_full_joint_input(original, result, refs, seasonal_proof_sources=inputs.policies)
    assert bound.status == "VERIFIED", bound.reasons
    assert bound.bound_point_count == 1098
    assert bound.candidate.hard_protection_points[0].other_protection_floor_cents == 1800
    assert bound.candidate.hard_protection_points[-1].other_protection_floor_cents == 0
    missing = bind_full_joint_input(original, result, refs)
    assert (
        missing.status == "UNKNOWN"
        and "SEASONAL_COMPLETE_CURRENT_PROOF_NOT_BOUND" in missing.reasons
    )
    assert result.full_annual_projection is not None
    result.full_annual_projection.calculation_trace[0].protected_cents_by_reason[FLOOR] = 0
    changed = bind_full_joint_input(original, result, refs, seasonal_proof_sources=inputs.policies)
    assert (
        changed.status == "UNKNOWN"
        and "FULL_CONDITIONAL_CASH_OR_FLOOR_BINDING_MISMATCH" in changed.reasons
    )


@pytest.mark.parametrize("adopted", [False, True])
def test_original_transfer_permission_cannot_override_an_adopted_floor(adopted: bool) -> None:
    source, proof = bound_source()
    if not adopted:
        source = source.model_copy(update={"reference_snapshots": []})
    original = ExecutionContext(
        user_id=proof.user_id,
        snapshot=BoundarySnapshot(
            as_of=proof.as_of,
            timezone="Asia/Shanghai",
            cash_accounts=[
                CashFact(
                    account_id=A, account_type="CASH", balance_cents=1000, observed_at=proof.as_of
                ),
                CashFact(
                    account_id=B, account_type="CASH", balance_cents=200, observed_at=proof.as_of
                ),
            ],
        ),
        versions=[],
        positions=[],
        boundary_products=[],
    )
    effect = ExecutionEffect(
        operation_id=UUID(int=603),
        user_id=proof.user_id,
        business_key="synthetic-transfer",
        action_type="TRANSFER_INTERNAL",
        amount_cents=300,
        cash_uses=[CashUse(account_id=A, amount_cents=300)],
        destination_account_id=B,
        valid_from=proof.as_of,
        expires_at=proof.as_of + timedelta(minutes=15),
    )
    validation = revalidate_execution(effect, original)
    assert validation.status == "CONFIRMATION_REQUIRED"
    protected = validate_full_execution_protection(effect, original, validation, [source])
    assert protected.status == ("BLOCKED" if adopted else "PASSED")
    assert protected.minimum_projected_margin_cents == (-600 if adopted else 1200)
    assert protected.grants_authority is False and validation.status == "CONFIRMATION_REQUIRED"
