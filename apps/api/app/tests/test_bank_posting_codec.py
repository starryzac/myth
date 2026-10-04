"""Original posting layouts preserve old bytes while binding every new economic origin."""

from typing import Any
from uuid import UUID

import pytest
from app.domain.audit_chain import build_subject, subject_canonical_text, subject_hash
from app.domain.bank_posting_codec import (
    bank_posting_data,
    bank_posting_snapshot_version,
    validate_live_posting,
    validate_posting_original,
)

USER = UUID("00000000-0000-0000-0000-000000000001")
EPOCH = UUID("00000000-0000-0000-0000-000000000002")
IDENTITY = UUID("00000000-0000-0000-0000-000000000003")
ACCOUNT = "00000000-0000-0000-0000-000000000004"
OPERATION = "00000000-0000-0000-0000-000000000005"
FACT = "00000000-0000-0000-0000-000000000006"


def original() -> dict[str, Any]:
    return {
        "id": str(IDENTITY),
        "user_id": str(USER),
        "created_at": "2026-10-04T00:00:00.000000Z",
        "ledger_key": "CASH:" + ACCOUNT,
        "ledger_dimension": "ECONOMIC",
        "ledger_metadata": {},
        "account_id": ACCOUNT,
        "position_id": None,
        "redemption_id": None,
        "operation_id": OPERATION,
        "leg_ref": "cash:" + ACCOUNT,
        "previous_posting_id": "00000000-0000-0000-0000-000000000007",
        "sequence_number": 2,
        "entry_kind": "DEBIT",
        "balance_before_cents": 1000,
        "delta_cents": -100,
        "balance_after_cents": 900,
        "occurred_at": "2026-10-04T00:00:00.000000Z",
    }


def test_old_live_null_extension_preserves_complete_original_and_subject_hash() -> None:
    old = original()
    live = {**old, "external_fact_id": None}
    encoded = bank_posting_data(live)
    assert encoded == old
    assert len(encoded) == 18
    before = build_subject(user_id=USER, epoch_id=EPOCH, kind="BANK_POSTING", id=IDENTITY, data=old)
    after = build_subject(
        user_id=USER, epoch_id=EPOCH, kind="BANK_POSTING", id=IDENTITY, data=encoded
    )
    assert subject_canonical_text(after) == subject_canonical_text(before)
    assert subject_hash(after) == subject_hash(before)
    validate_live_posting(old, 1, live)


@pytest.mark.parametrize(
    "mutation",
    [
        {"external_fact_id": FACT},
        {"operation_id": None, "external_fact_id": FACT},
        {"ledger_metadata": {"hidden": "changed"}},
        {"delta_cents": -200, "balance_after_cents": 800},
        {"unexpected_future_column": None},
    ],
)
def test_legacy_original_does_not_hide_origin_or_other_full_live_changes(
    mutation: dict[str, Any],
) -> None:
    old = original()
    with pytest.raises(ValueError):
        validate_live_posting(old, 1, {**old, "external_fact_id": None, **mutation})


def test_external_and_clearing_opening_are_explicit_full_v2() -> None:
    external = {**original(), "operation_id": None, "external_fact_id": FACT}
    assert bank_posting_snapshot_version(external) == 2
    assert bank_posting_data(external) == external
    validate_posting_original(external, 2)
    with pytest.raises(ValueError):
        validate_posting_original(external, 1)
    clear = {
        **external,
        "ledger_key": "CLEARING:bounded-funds-external-v1:payroll",
        "account_id": None,
        "external_fact_id": None,
        "entry_kind": "OPENING",
        "leg_ref": None,
        "previous_posting_id": None,
        "sequence_number": 1,
        "balance_before_cents": 0,
        "delta_cents": 100000000,
        "balance_after_cents": 100000000,
    }
    assert bank_posting_snapshot_version(clear) == 2
    assert bank_posting_data(clear) == clear
    with pytest.raises(ValueError):
        validate_posting_original({k: v for k, v in clear.items() if k != "external_fact_id"}, 1)


def test_new_income_zero_opening_keeps_v1_and_cannot_borrow_external_origin() -> None:
    opening = {
        **original(),
        "operation_id": None,
        "external_fact_id": None,
        "ledger_key": "LOT_AVAILABLE:00000000-0000-0000-0000-000000000008",
        "ledger_dimension": "INCOME_LOCATION",
        "entry_kind": "OPENING",
        "leg_ref": None,
        "previous_posting_id": None,
        "sequence_number": 1,
        "balance_before_cents": 0,
        "delta_cents": 0,
        "balance_after_cents": 0,
    }
    encoded = bank_posting_data(opening)
    assert bank_posting_snapshot_version(encoded) == 1
    assert "external_fact_id" not in encoded
    with pytest.raises(ValueError):
        bank_posting_data({**opening, "external_fact_id": FACT})
