"""Direct semantic invariants, including verified empty actions and unknown evidence."""

from datetime import timedelta, timezone
from uuid import uuid4

import pytest
from app.domain.boundary_action_events import action_signature, event_kind
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import CashUse
from app.tests.test_execution_domain import A, context, purchase_example, transfer


def test_numeric_only_change_is_silent_and_original_hash_stays_unchanged() -> None:
    effect = transfer()
    original_hash = execution_effect_hash(effect)
    validation = revalidate_execution(effect, context())
    changed = validation.model_copy(
        update={
            "baseline_boundary": validation.baseline_boundary.model_copy(
                update={"safe_idle_cents": 777}
            )
        }
    )
    before = action_signature(effect, validation, "ASK_ONCE")
    assert event_kind(before, action_signature(effect, changed, "ASK_ONCE")) == "BoundaryObserved"
    assert execution_effect_hash(effect) == original_hash


def test_generated_ids_and_receipt_time_do_not_change_the_action() -> None:
    original = transfer()
    changed = original.model_copy(
        update={
            "operation_id": uuid4(),
            "business_key": "other-key",
            "expires_at": original.expires_at + timedelta(seconds=1),
        }
    )
    assert action_signature(
        original, revalidate_execution(original, context()), "ASK_ONCE"
    ) == action_signature(changed, revalidate_execution(changed, context()), "ASK_ONCE")


def test_amount_destination_and_authority_change_are_actual_action_changes() -> None:
    original = transfer()
    initial = action_signature(original, revalidate_execution(original, context()), "ASK_ONCE")
    changed = original.model_copy(
        update={"amount_cents": 400, "cash_uses": [CashUse(account_id=A, amount_cents=400)]}
    )
    assert (
        event_kind(
            initial, action_signature(changed, revalidate_execution(changed, context()), "ASK_ONCE")
        )
        == "BoundaryCrossed"
    )
    changed = original.model_copy(update={"destination_account_id": uuid4()})
    assert (
        action_signature(changed, revalidate_execution(changed, context()), "ASK_ONCE") != initial
    )
    assert (
        action_signature(original, revalidate_execution(original, context()), "ADVISE_ONLY")
        != initial
    )


def test_purchase_day_metadata_and_timezone_preserve_actual_economic_signature() -> None:
    effect, state = purchase_example()
    original_hash = execution_effect_hash(effect)
    validation = revalidate_execution(effect, state)
    assert validation.status == "READY"
    assert effect.purchase_exit is not None and effect.latest_arrival_at is not None
    alternate_zone = timezone(timedelta(hours=8))
    same_availability = effect.purchase_exit.principal_available_at.astimezone(alternate_zone)
    changed = effect.model_copy(
        update={
            "position_id": uuid4(),
            "purchase_exit": effect.purchase_exit.model_copy(
                update={
                    "earning_days": effect.purchase_exit.earning_days - 1,
                    "liquidity_days": effect.purchase_exit.liquidity_days + 1,
                    "principal_available_at": same_availability,
                }
            ),
            "latest_arrival_at": effect.latest_arrival_at.astimezone(alternate_zone),
        }
    )
    signature = action_signature(effect, validation, "AUTO_EXECUTE")
    assert signature == action_signature(changed, validation, "AUTO_EXECUTE")
    assert changed.latest_arrival_at is not None
    delayed = changed.model_copy(
        update={"latest_arrival_at": changed.latest_arrival_at + timedelta(minutes=1)}
    )
    assert signature != action_signature(delayed, validation, "AUTO_EXECUTE")
    assert execution_effect_hash(effect) == original_hash


def test_rejected_amounts_have_no_admissible_actions_but_unknown_is_not_empty() -> None:
    hashes = []
    for amount in (3000, 5000):
        effect = transfer().model_copy(
            update={
                "amount_cents": amount,
                "cash_uses": [CashUse(account_id=A, amount_cents=amount)],
            }
        )
        validation = revalidate_execution(effect, context())
        assert validation.status == "BLOCKED"
        hashes.append(action_signature(effect, validation, "BLOCKED"))
    assert len(set(hashes)) == 1
    effect = transfer()
    missing = revalidate_execution(effect, context()).model_copy(
        update={"status": "INSUFFICIENT_EVIDENCE"}
    )
    with pytest.raises(ValueError, match="Missing evidence"):
        action_signature(effect, missing, "BLOCKED")
