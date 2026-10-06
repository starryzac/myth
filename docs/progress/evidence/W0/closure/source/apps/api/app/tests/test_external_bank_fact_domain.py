"""External facts conserve actual cash and preserve every protected income component."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.external_bank_fact import (
    add_income_origin,
    apply_consumption,
    clearing_key,
    economic_legs,
    fact_id,
    plan_consumption,
    semantic_hash,
)
from app.domain.external_bank_fact_types import ExternalCashAttribution, ExternalFactRequest
from app.domain.income_ledger import IncomeFragment, IncomeLedger, IncomeOrigin, location_id

NOW = datetime(2026, 10, 4, tzinfo=UTC)
USER, ACCOUNT, ORIGIN = (UUID(int=value) for value in (1, 2, 3))


def request(**updates: object) -> ExternalFactRequest:
    return ExternalFactRequest.model_validate(
        {
            "user_id": USER,
            "idempotency_key": "first-key",
            "external_ref": "salary-2026-10",
            "kind": "CONSUMPTION",
            "account_id": ACCOUNT,
            "amount_cents": 800,
            "counterparty_ref": "merchant",
            "occurred_at": NOW,
            **updates,
        }
    )


def ledger() -> IncomeLedger:
    return IncomeLedger(
        user_id=USER,
        as_of=NOW,
        scope_account_ids=(ACCOUNT,),
        origins=(
            IncomeOrigin(
                origin_transaction_id=ORIGIN,
                origin_account_id=ACCOUNT,
                amount_cents=1000,
                occurred_at=NOW - timedelta(days=1),
                observed_at=NOW,
                bank_evidence_id=UUID(int=4),
                bank_evidence_hash="a" * 64,
            ),
        ),
        fragments=(
            IncomeFragment(
                fragment_id=location_id(ORIGIN, ACCOUNT),
                origin_transaction_id=ORIGIN,
                account_id=ACCOUNT,
                available_cents=700,
                spent_cents=100,
                assigned_cents=100,
                reserved_cents=100,
                legacy_reserved_cents=100,
            ),
        ),
    )


def test_fixed_reference_identity_does_not_turn_a_different_key_into_an_alias() -> None:
    original = request()
    alias = request(idempotency_key="another-key")
    assert original.idempotency_key != alias.idempotency_key
    assert fact_id(original) == fact_id(alias)
    assert semantic_hash(original) == semantic_hash(alias)
    assert fact_id(request(user_id=UUID(int=9))) != fact_id(original)
    assert semantic_hash(request(amount_cents=801)) != semantic_hash(original)
    assert clearing_key(original) == "CLEARING:bounded-funds-external-v1:merchant"
    assert sum(delta for _, _, delta in economic_legs(original)) == 0


def test_available_fifo_then_proven_untracked_cash_preserves_claims() -> None:
    before = ledger()
    attribution = ExternalCashAttribution(
        account_id=ACCOUNT,
        bank_balance_before_cents=1000,
        goal_cash_owned_cents=0,
        non_income_claim_cents=100,
    )
    planned = plan_consumption(request(), before, attribution)
    assert planned.status == "READY" and planned.untracked_spent_cents == 100
    assert [use.amount_cents for use in planned.uses] == [700]
    after = apply_consumption(before, planned, NOW + timedelta(minutes=1))
    assert after.origins == before.origins and after.reservations == before.reservations
    assert after.fragments[0].available_cents == 0 and after.fragments[0].spent_cents == 800
    assert after.fragments[0].reserved_cents == after.fragments[0].legacy_reserved_cents == 100
    assert after.fragments[0].assigned_cents == before.fragments[0].assigned_cents
    blocked = plan_consumption(request(amount_cents=801), before, attribution)
    assert blocked.status == "UNRECONCILED" and blocked.uses == ()
    with pytest.raises(ValueError):
        apply_consumption(before, blocked, NOW)
    assert before == ledger()


def test_new_salary_origin_comes_from_its_actual_transaction_and_never_rewrites_old_lots() -> None:
    before = ledger()
    origin = IncomeOrigin(
        origin_transaction_id=UUID(int=12),
        origin_account_id=ACCOUNT,
        amount_cents=1200000,
        occurred_at=NOW,
        observed_at=NOW,
        bank_evidence_id=UUID(int=13),
        bank_evidence_hash="b" * 64,
    )
    after = add_income_origin(before, origin, NOW)
    assert before.origins[0] in after.origins and before.fragments[0] in after.fragments
    new = next(
        row for row in after.fragments if row.origin_transaction_id == origin.origin_transaction_id
    )
    assert new.available_cents == origin.amount_cents and new.spent_cents == new.reserved_cents == 0
    assert add_income_origin(after, origin, NOW) == after
    with pytest.raises(ValueError):
        add_income_origin(after, origin.model_copy(update={"amount_cents": 1}), NOW)


@pytest.mark.parametrize("value", [True, 1.0, 0, -1, 2**63])
def test_external_bank_money_is_strict_positive_integer_cents(value: object) -> None:
    with pytest.raises(ValueError):
        request(amount_cents=value)
