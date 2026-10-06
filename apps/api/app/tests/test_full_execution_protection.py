"""Hand-worked post-effect FULL floors; not bank observations or acceptance metrics."""

import pytest
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.full_execution_protection import validate_full_execution_protection
from app.tests.test_execution_domain import NOW, context, goal_example, grant, transfer
from app.tests.test_full_protection_projection import dated, periodic, source


def test_confirmed_full_dated_floor_vetoes_a_previously_safe_goal_allocation() -> None:
    effect, original = goal_example()
    validation = revalidate_execution(effect, original)
    assert validation.status == "READY"
    protected = source("DatedExpensePolicy", dated(amount=1100), now=NOW)
    checked = validate_full_execution_protection(effect, original, validation, [protected])
    assert checked.status == "BLOCKED"
    # 1,200 total cash - 500 owned by the goal - 1,100 declared expense.
    assert checked.minimum_projected_margin_cents == -400
    assert checked.original_effect_hash == execution_effect_hash(effect)
    assert checked.grants_authority is False
    assert validation.status == "READY"  # Original financial result/hash remain original.


def test_cash_preserving_transfer_keeps_full_floor_but_cannot_gain_confirmation() -> None:
    effect, original = transfer(), context()
    validation = revalidate_execution(effect, original)
    assert validation.status == "CONFIRMATION_REQUIRED"
    protected = source("DatedExpensePolicy", dated(amount=1000), now=NOW)
    checked = validate_full_execution_protection(effect, original, validation, [protected])
    assert checked.status == "PASSED" and checked.minimum_projected_margin_cents == 200
    assert checked.grants_authority is False and validation.status == "CONFIRMATION_REQUIRED"
    assert checked.current_policy_version_ids == [str(protected.version_id)]
    assert checked.source_evidence_ids == [str(protected.evidence_ids[0])]


def test_other_reserved_cash_remains_protected_in_full_post_effect_curve() -> None:
    effect, original = transfer(), context()
    original = original.model_copy(
        update={"reserved_cash_by_account": {original.snapshot.cash_accounts[0].account_id: 100}}
    )
    validation = revalidate_execution(effect, original, confirmation=grant(effect))
    assert validation.status == "READY"
    checked = validate_full_execution_protection(
        effect, original, validation, [source("DatedExpensePolicy", dated(amount=1150), now=NOW)]
    )
    assert checked.status == "BLOCKED" and checked.minimum_projected_margin_cents == -50


@pytest.mark.parametrize("mode", ["effect_hash", "user", "missing_post", "clock", "blocked"])
def test_unverified_original_projection_never_passes_full_check(mode: str) -> None:
    effect, original = transfer(), context()
    validation = revalidate_execution(effect, original)
    if mode == "effect_hash":
        validation = validation.model_copy(update={"effect_hash": "f" * 64})
    elif mode == "user":
        original = original.model_copy(
            update={"user_id": original.snapshot.cash_accounts[0].account_id}
        )
    elif mode == "missing_post":
        validation = validation.model_copy(update={"projected_snapshot": None})
    elif mode == "clock":
        assert validation.projected_snapshot is not None
        validation = validation.model_copy(
            update={
                "projected_snapshot": validation.projected_snapshot.model_copy(
                    update={"timezone": "Asia/Shanghai"}
                )
            }
        )
    else:
        validation = validation.model_copy(update={"status": "BLOCKED"})
    checked = validate_full_execution_protection(
        effect, original, validation, [source("DatedExpensePolicy", dated(), now=NOW)]
    )
    assert checked.status == "UNKNOWN" and checked.minimum_projected_margin_cents is None


def test_periodic_future_account_debits_are_not_assumed_from_current_sufficient_cash() -> None:
    effect, original = transfer(), context()
    checked = validate_full_execution_protection(
        effect,
        original,
        revalidate_execution(effect, original),
        [source("PeriodicTransferPolicy", periodic(), now=NOW)],
    )
    assert checked.status == "UNKNOWN"
    assert checked.reasons == ["FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN"]
    assert checked.minimum_projected_margin_cents is None


def test_current_source_failure_cannot_turn_an_empty_inventory_into_permission() -> None:
    effect, original = transfer(), context()
    checked = validate_full_execution_protection(
        effect,
        original,
        revalidate_execution(effect, original),
        [],
        source_issues=("ACTUAL_FULL_CONFIRMATION_HASH_CHANGED",),
    )
    assert checked.status == "UNKNOWN" and checked.grants_authority is False


def test_absent_additional_full_policy_does_not_rewrite_original_result_or_effect() -> None:
    effect, original = transfer(), context()
    validation = revalidate_execution(effect, original)
    before = validation.model_dump_json(), effect.model_dump_json(), original.model_dump_json()
    checked = validate_full_execution_protection(effect, original, validation, [])
    assert checked.status == "NO_ADDITIONAL_POLICY"
    assert checked.minimum_projected_margin_cents is None
    assert before == (
        validation.model_dump_json(),
        effect.model_dump_json(),
        original.model_dump_json(),
    )


def test_changed_confirmation_hash_is_unknown_not_a_released_obligation() -> None:
    effect, original = transfer(), context()
    protected = source("DatedExpensePolicy", dated(), now=NOW)
    protected.confirmation["reviewed_hash"] = "f" * 64
    checked = validate_full_execution_protection(
        effect, original, revalidate_execution(effect, original), [protected]
    )
    assert checked.status == "UNKNOWN" and checked.minimum_projected_margin_cents is None
