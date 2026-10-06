"""Protection consumer risks; synthetic math, not a financial acceptance result."""

from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.tests.test_execution_domain import NOW, A
from app.tests.test_full_protection_projection import periodic, source
from app.tests.test_full_registered_account_debits import original_inputs


def test_explicit_exact_account_bounds_can_pass_without_adding_original_authority() -> None:
    effect, context, validation, inputs, projection = original_inputs()
    proof = derive_full_account_debit_bounds(effect, context, validation, inputs, projection)
    checked = validate_full_execution_protection(
        effect, context, validation, inputs.policies, account_debit_bounds=proof
    )
    assert checked.status == "PASSED"
    assert checked.grants_authority is False
    assert validation.status == "CONFIRMATION_REQUIRED"
    assert checked.projection_input_hash == proof.projection_input_hash


def test_positive_total_cash_cannot_borrow_another_accounts_money_for_periodic_debits() -> None:
    values = original_inputs(
        policies=[source("PeriodicTransferPolicy", periodic(amount=60, due_day=5), now=NOW)]
    )
    effect, context, validation, inputs, projection = values
    assert projection.full_annual_projection is not None
    assert projection.full_annual_projection.minimum_margin_cents is not None
    assert projection.full_annual_projection.minimum_margin_cents > 0
    proof = derive_full_account_debit_bounds(*values)
    assert proof.status == "BLOCKED"
    checked = validate_full_execution_protection(
        effect, context, validation, inputs.policies, account_debit_bounds=proof
    )
    assert checked.status == "BLOCKED"
    assert checked.reasons == ["FULL_REGISTERED_ACCOUNT_CASH_BOUND_NEGATIVE"]


def test_current_changed_reservation_rejects_an_original_account_bound_proof() -> None:
    values = original_inputs()
    effect, context, validation, inputs, _ = values
    proof = derive_full_account_debit_bounds(*values)
    changed = context.model_copy(update={"reserved_cash_by_account": {A: 1}})
    checked = validate_full_execution_protection(
        effect, changed, validation, inputs.policies, account_debit_bounds=proof
    )
    assert checked.status == "UNKNOWN"
    assert checked.reasons == ["FULL_FUTURE_ACCOUNT_DEBIT_PROOF_NOT_BOUND"]


def test_a_relabelled_proof_never_becomes_a_protection_pass() -> None:
    values = original_inputs()
    effect, context, validation, inputs, _ = values
    proof = derive_full_account_debit_bounds(*values)
    changed = proof.model_copy(update={"proof_hash": "f" * 64})
    checked = validate_full_execution_protection(
        effect, context, validation, inputs.policies, account_debit_bounds=changed
    )
    assert checked.status == "UNKNOWN"
    assert checked.reasons == ["FULL_FUTURE_ACCOUNT_DEBIT_PROOF_NOT_BOUND"]
