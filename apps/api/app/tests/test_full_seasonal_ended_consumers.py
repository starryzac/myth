"""Explicit v4 curve/joint/execution consumers, synthetic originals only."""

from datetime import timedelta
from uuid import UUID

from app.domain.boundary_types import BoundarySnapshot, CashFact
from app.domain.execution import revalidate_execution
from app.domain.execution_types import CashUse, ExecutionContext, ExecutionEffect
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_joint_goal_planning import bind_full_joint_input
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_seasonal_current_protection import (
    ALGORITHM,
    derive_current_seasonal_protection_bindings,
)
from app.domain.full_seasonal_ended_adoption import REFERENCE_KIND, derive_ended_seasonal_adoption
from app.domain.multi_goal_allocation import (
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
)
from app.tests.test_full_seasonal_adoption import USER, proof_fixture
from app.tests.test_full_seasonal_ended_adoption import fixture

A, B = UUID(int=801), UUID(int=802)


def projection_input() -> FullProtectionProjectionInput:
    inputs = fixture()
    proof = derive_ended_seasonal_adoption(inputs)
    source, _ = proof_fixture()
    source = source.model_copy(
        update={
            "reference_snapshots": [
                {
                    "kind": REFERENCE_KIND,
                    "proof": proof.model_dump(mode="json"),
                }
            ]
        }
    )
    return FullProtectionProjectionInput(
        snapshot=BoundarySnapshot(
            as_of=inputs.as_of,
            timezone=inputs.timezone,
            cash_accounts=[
                CashFact(
                    account_id=A, account_type="CASH", balance_cents=5000, observed_at=inputs.as_of
                )
            ],
        ),
        boundary_versions=[],
        positions=[],
        boundary_products=[],
        policies=[source],
    )


def test_verified_ended_v4_1098_points_release_floor_and_preserve_old_original_curves() -> None:
    inputs = projection_input()
    before = inputs.model_dump_json()
    result = project_full_protection(inputs)
    ordinary = inputs.model_copy(
        update={"policies": [inputs.policies[0].model_copy(update={"reference_snapshots": []})]}
    )
    original = project_full_protection(ordinary)
    assert result.algorithm_version == ALGORITHM
    assert result.seasonal_status == "ADOPTED_FLOOR_RELEASED"
    assert result.seasonal_adopted_adjustment_cents == 0 and result.status == "READY"
    assert result.full_annual_projection is not None
    assert len(result.full_annual_projection.calculation_trace) == 1098
    assert result.original_execution_view == original.original_execution_view
    assert result.original_annual_projection == original.original_annual_projection
    assert original.algorithm_version == "registered-full-protection-v1"
    for point in result.full_annual_projection.calculation_trace:
        assert point.cash_cents == 5000 and point.margin_cents == 5000
        assert point.protected_cents_by_reason["full_seasonal_adopted"] == 0
    assert inputs.model_dump_json() == before and not result.bank_authority


def test_unproven_ended_original_keeps_all_curve_money_null() -> None:
    inputs = projection_input()
    inputs.policies[0].reference_snapshots[0]["proof"]["proof_hash"] = "0" * 64
    result = project_full_protection(inputs)
    assert result.algorithm_version == ALGORITHM and result.status == "UNKNOWN"
    assert (
        result.full_annual_projection is None and result.seasonal_adopted_adjustment_cents is None
    )
    assert result.seasonal_status == "ADOPTED_AMOUNT_UNKNOWN" and result.reasons


def test_ended_joint_binds_complete_proof_sources_and_each_zero_floor_without_cash_credit() -> None:
    inputs = projection_input()
    result = project_full_protection(inputs)
    bindings = derive_current_seasonal_protection_bindings(
        inputs.policies, inputs.snapshot.as_of, inputs.snapshot.timezone, expected_user_id=USER
    )
    refs = [
        SourceReference(user_id=USER, evidence_id=identity, content_hash=digest)
        for identity, digest in bindings.evidence().items()
    ]
    original = MultiGoalAllocationInput(
        user_id=USER,
        as_of=inputs.snapshot.as_of,
        timezone=inputs.snapshot.timezone,
        income_lots=[],
        goals=[],
        hard_protection_points=[
            HardProtectionPoint(
                date=point.date,
                cash_cents=point.cash_cents,
                obligation_floor_cents=0,
                living_floor_cents=0,
                emergency_floor_cents=0,
                owned_goal_cash_cents=0,
                other_protection_floor_cents=0,
                source_refs=refs,
            )
            for point in result.original_annual_projection.calculation_trace
        ],
    )
    bound = bind_full_joint_input(original, result, refs, seasonal_proof_sources=inputs.policies)
    assert bound.status == "VERIFIED", bound.reasons
    assert bound.bound_point_count == 1098
    assert all(
        point.cash_cents == 5000 and point.other_protection_floor_cents == 0
        for point in bound.candidate.hard_protection_points
    )
    missing = bind_full_joint_input(original, result, refs)
    assert (
        missing.status == "UNKNOWN"
        and missing.candidate.hard_protection_points == original.hard_protection_points
    )
    assert result.full_annual_projection is not None
    result.full_annual_projection.calculation_trace[0].protected_cents_by_reason[
        "full_seasonal_adopted"
    ] = 1
    tampered = bind_full_joint_input(original, result, refs, seasonal_proof_sources=inputs.policies)
    assert (
        tampered.status == "UNKNOWN"
        and tampered.candidate.hard_protection_points == original.hard_protection_points
    )


def test_ended_floor_execution_veto_never_bypasses_original_confirmation_or_failed_proof() -> None:
    inputs = projection_input()
    now = inputs.snapshot.as_of
    context = ExecutionContext(
        user_id=USER,
        snapshot=BoundarySnapshot(
            as_of=now,
            timezone=inputs.snapshot.timezone,
            cash_accounts=[
                CashFact(account_id=A, account_type="CASH", balance_cents=1000, observed_at=now),
                CashFact(account_id=B, account_type="CASH", balance_cents=200, observed_at=now),
            ],
        ),
        versions=[],
        positions=[],
        boundary_products=[],
    )
    effect = ExecutionEffect(
        operation_id=UUID(int=803),
        user_id=USER,
        business_key="synthetic-ended-transfer",
        action_type="TRANSFER_INTERNAL",
        amount_cents=300,
        cash_uses=[CashUse(account_id=A, amount_cents=300)],
        destination_account_id=B,
        valid_from=now,
        expires_at=now + timedelta(minutes=15),
    )
    validation = revalidate_execution(effect, context)
    assert validation.status == "CONFIRMATION_REQUIRED"
    result = validate_full_execution_protection(effect, context, validation, inputs.policies)
    assert result.status == "PASSED" and result.minimum_projected_margin_cents == 1200
    assert not result.grants_authority and validation.status == "CONFIRMATION_REQUIRED"
    inputs.policies[0].reference_snapshots[0]["proof"]["proof_hash"] = "0" * 64
    unknown = validate_full_execution_protection(effect, context, validation, inputs.policies)
    assert unknown.status == "UNKNOWN" and unknown.minimum_projected_margin_cents is None
