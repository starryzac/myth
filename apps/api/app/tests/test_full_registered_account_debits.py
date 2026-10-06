"""Hand-worked synthetic bounds only; no bank/permission/financial runtime proof."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_types import BoundaryPosition, GoalOwnership
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext, ExecutionEffect, ExecutionValidation
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    FullProtectionProjectionResult,
    project_full_protection,
)
from app.domain.full_registered_account_debits import (
    FullAccountDebitBoundsProof,
    derive_full_account_debit_bounds,
    validate_full_account_debit_bounds_proof,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.tests.test_execution_domain import NOW, A, B, context, recurring, transfer
from app.tests.test_full_protection_projection import dated, periodic, source
from pydantic import ValidationError

Inputs = tuple[
    ExecutionEffect,
    ExecutionContext,
    ExecutionValidation,
    FullProtectionProjectionInput,
    FullProtectionProjectionResult,
]


def original_inputs(
    *,
    original: ExecutionContext | None = None,
    policies: list[FullProtectionPolicySource] | None = None,
) -> Inputs:
    effect = transfer()
    current = original or context()
    validation = revalidate_execution(effect, current)
    assert (
        validation.status == "CONFIRMATION_REQUIRED" and validation.projected_snapshot is not None
    )
    inputs = FullProtectionProjectionInput(
        snapshot=validation.projected_snapshot,
        boundary_versions=current.versions,
        positions=validation.projected_positions,
        boundary_products=current.boundary_products,
        reserved_cash_by_account=current.reserved_cash_by_account,
        policies=policies
        if policies is not None
        else [
            source(
                "PeriodicTransferPolicy",
                periodic(amount=10, due_day=5),
                now=NOW,
            )
        ],
    )
    return effect, current, validation, inputs, project_full_protection(inputs)


def test_every_periodic_account_gets_all_full_and_mvp_outflows_without_borrowing_other_cash() -> (
    None
):
    mvp = recurring(due_day=5)
    config = validate_configuration(
        mvp.configuration | {"amount_rule": {"kind": "exact", "amount_cents": 20}}
    )
    mvp = mvp.model_copy(
        update={"configuration": config, "content_hash": configuration_hash(config)}
    )
    # The original helper is a ten-day permit; this yearly synthetic scenario
    # explicitly declares a persistent original policy instead of guessing it.
    mvp = mvp.model_copy(update={"valid_until": None})
    original = context().model_copy(update={"versions": [mvp]})
    policies = [
        source("PeriodicTransferPolicy", periodic(amount=10, due_day=5), now=NOW),
        source(
            "PeriodicTransferPolicy",
            periodic(amount=10, due_day=5) | {"source_account_id": str(B)},
            now=NOW,
            identity=70,
        ),
        source("DatedExpensePolicy", dated(amount=60), now=NOW, identity=90),
    ]
    values = original_inputs(original=original, policies=policies)
    proof = derive_full_account_debit_bounds(*values)
    assert proof.status == "BLOCKED" and proof.registered_source_account_ids == [A, B]
    # Each month: two FULL10 + MVP20; twelve months, plus one dated60 = 540.
    assert proof.maximum_cumulative_registered_outflows_cents == 540
    first, second = proof.accounts
    assert (first.actual_projected_cash_cents, second.actual_projected_cash_cents) == (700, 500)
    assert first.minimum_account_cash_lower_bound_cents == 160 and first.status == "PASSED"
    assert second.minimum_account_cash_lower_bound_cents == -40 and second.status == "BLOCKED"
    assert all(len(row.daily_bounds) == 366 for row in proof.accounts)
    assert proof.observed_full_phase_count == proof.expected_phase_count == 1098
    assert not proof.account_allocation_is_exact and not proof.grants_authority
    assert proof.borrowed_other_account_cash_cents == 0
    assert validate_full_account_debit_bounds_proof(proof, *values) == proof
    assert FullAccountDebitBoundsProof.model_validate_json(proof.model_dump_json()) == proof


def test_goal_cash_and_maximum_overlapping_other_claims_are_preserved() -> None:
    original = context()
    goal = GoalOwnership(
        goal_id=UUID(int=110),
        policy_id=UUID(int=111),
        account_id=A,
        cash_owned_cents=300,
        principal_owned_cents=0,
        allocated_cents=300,
    )
    original = original.model_copy(
        update={
            "snapshot": original.snapshot.model_copy(update={"goals": [goal]}),
            "reserved_cash_by_account": {A: 100},
            "exposure": AssetExposure(
                as_of=NOW,
                scope="general_idle_funds",
                managed_principal_cents=0,
                pending_purchase_cents=0,
                reserved_cash_by_account={A: 200},
            ),
        }
    )
    proof = derive_full_account_debit_bounds(*original_inputs(original=original))
    assert proof.status == "PASSED"
    row = proof.accounts[0]
    assert (row.actual_projected_cash_cents, row.goal_owned_cash_cents, row.other_claims_cents) == (
        700,
        300,
        200,
    )
    assert row.initial_free_cash_cents == 200 and row.minimum_account_cash_lower_bound_cents == 80


def test_large_future_principal_cannot_make_account_lower_bound_pass() -> None:
    original = context()
    original = original.model_copy(
        update={
            "positions": [
                BoundaryPosition(
                    position_id=UUID(int=120),
                    principal_cents=1000000,
                    status="HELD",
                    principal_available_at=NOW + timedelta(days=1),
                )
            ]
        }
    )
    protected = source("PeriodicTransferPolicy", periodic(amount=60, due_day=5), now=NOW)
    values = original_inputs(original=original, policies=[protected])
    assert values[-1].full_annual_projection is not None
    assert values[-1].full_annual_projection.calculation_trace[-1].cash_cents > 100000
    proof = derive_full_account_debit_bounds(*values)
    assert proof.status == "BLOCKED"
    assert proof.accounts[0].minimum_account_cash_lower_bound_cents == 700 - 12 * 60 == -20
    assert proof.future_principal_credited_cents == proof.future_income_credited_cents == 0


@pytest.mark.parametrize(
    "risk",
    [
        "missing-phase",
        "wrong-phase",
        "wrong-day",
        "wrong-hash",
        "owner",
        "missing-source-check",
        "missing-account",
        "unmapped-other-goal",
        "full-coverage",
        "claims",
        "cash-delta",
        "principal-credit",
        "input-other-snapshot",
        "wrong-full-body",
        "source-issue",
    ],
)
def test_incomplete_or_mismatched_originals_remain_unknown_with_null_bounds(risk: str) -> None:
    effect, original, validation, inputs, full = original_inputs()
    curve = full.full_annual_projection
    assert curve is not None and validation.projected_snapshot is not None
    if risk == "missing-phase":
        full = full.model_copy(
            update={
                "full_annual_projection": curve.model_copy(
                    update={"calculation_trace": curve.calculation_trace[:-1]}
                )
            }
        )
    elif risk in {"wrong-phase", "wrong-day", "cash-delta", "principal-credit"}:
        trace = list(curve.calculation_trace)
        index = 1 if risk != "principal-credit" else 2
        point = trace[index]
        update = (
            {"phase": "BEFORE_PAYMENT"}
            if risk == "wrong-phase"
            else {"day": 99}
            if risk == "wrong-day"
            else {"cash_cents": point.cash_cents + 1, "margin_cents": point.margin_cents + 1}
        )
        trace[index] = point.model_copy(update=update)
        full = full.model_copy(
            update={"full_annual_projection": curve.model_copy(update={"calculation_trace": trace})}
        )
    elif risk == "wrong-hash":
        full = full.model_copy(update={"input_hash": "f" * 64})
    elif risk == "owner":
        original = original.model_copy(update={"user_id": UUID(int=200)})
    elif risk == "missing-source-check":
        full = full.model_copy(update={"source_account_checks": []})
    elif risk == "missing-account":
        projected = validation.projected_snapshot.model_copy(
            update={"cash_accounts": [validation.projected_snapshot.cash_accounts[1]]}
        )
        validation = validation.model_copy(update={"projected_snapshot": projected})
        inputs = inputs.model_copy(update={"snapshot": projected})
        full = full.model_copy(
            update={"input_hash": configuration_hash(inputs.model_dump(mode="json"))}
        )
    elif risk == "unmapped-other-goal":
        assert validation.projected_snapshot is not None
        projected = validation.projected_snapshot.model_copy(
            update={
                "goals": [
                    GoalOwnership(
                        goal_id=UUID(int=210),
                        policy_id=UUID(int=211),
                        account_id=None,
                        cash_owned_cents=1,
                        principal_owned_cents=0,
                        allocated_cents=1,
                    )
                ]
            }
        )
        validation = validation.model_copy(update={"projected_snapshot": projected})
        inputs = inputs.model_copy(update={"snapshot": projected})
        full = project_full_protection(inputs)
    elif risk == "full-coverage":
        full = full.model_copy(
            update={"full_obligations_complete_within_registered_current_scope": False}
        )
    elif risk == "claims":
        original = original.model_copy(update={"reserved_goal_cash_by_goal": {UUID(int=222): 1}})
    elif risk == "input-other-snapshot":
        inputs = inputs.model_copy(update={"snapshot": original.snapshot})
    elif risk == "wrong-full-body":
        full = full.model_copy(
            update={
                "source_account_checks": [
                    full.source_account_checks[0].model_copy(update={"actual_cash_cents": 999999})
                ]
            }
        )
    else:
        inputs = inputs.model_copy(update={"full_source_issues": ["MISSING_REAL_SOURCE"]})
        full = project_full_protection(inputs)
    proof = derive_full_account_debit_bounds(effect, original, validation, inputs, full)
    assert proof.status == "UNKNOWN" and proof.reasons
    assert proof.maximum_cumulative_registered_outflows_cents is None
    assert all(row.minimum_account_cash_lower_bound_cents is None for row in proof.accounts)
    assert not proof.grants_authority


def test_relabelled_proof_or_changed_context_is_not_accepted_by_exact_validator() -> None:
    values = original_inputs()
    proof = derive_full_account_debit_bounds(*values)
    assert proof.status == "PASSED"
    with pytest.raises(ValidationError, match="proof hash"):
        validate_full_account_debit_bounds_proof(
            proof.model_copy(update={"context_hash": "a" * 64}), *values
        )
    changed = values[1].model_copy(update={"reserved_cash_by_account": {A: 1}})
    with pytest.raises(ValueError, match="same-invocation"):
        validate_full_account_debit_bounds_proof(proof, values[0], changed, *values[2:])
