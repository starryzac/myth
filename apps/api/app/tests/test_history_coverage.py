"""The pure import protocol binds bank facts while leaving user annotations editable."""

import hashlib
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from app.domain.history_coverage import (
    account_history_manifest,
    bank_fact_snapshot,
    build_history_coverage,
)

USER = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT = UUID("00000000-0000-0000-0000-000000000002")
OTHER_ACCOUNT = UUID("00000000-0000-0000-0000-000000000003")
TRANSACTION = UUID("00000000-0000-0000-0000-000000000004")
EVIDENCE = UUID("00000000-0000-0000-0000-000000000005")
WHEN = datetime(2026, 8, 4, 16, tzinfo=UTC)


def fixture() -> tuple[SimpleNamespace, SimpleNamespace]:
    content = {
        "simulation": True,
        "transaction_id": str(TRANSACTION),
        "account_id": str(ACCOUNT),
        "direction": "DEBIT",
        "amount_cents": 125,
        "balance_after_cents": 875,
        "occurred_at": "2026-08-04T16:00:00+00:00",
        "counterparty_ref": "store",
        "economic_role": "CONSUMPTION",
    }
    digest = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    transaction = SimpleNamespace(
        id=TRANSACTION,
        user_id=USER,
        account_id=ACCOUNT,
        evidence_id=EVIDENCE,
        source_ref="ledger:1",
        direction="DEBIT",
        amount_cents=125,
        balance_after_cents=875,
        occurred_at=WHEN,
        observed_at=WHEN,
        counterparty_ref="store",
        category="food",
        category_confirmed=True,
        is_one_off=False,
    )
    evidence = SimpleNamespace(
        id=EVIDENCE,
        user_id=USER,
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_BANK_TRANSACTION",
        source_ref="bank:1",
        content=content,
        content_hash=digest,
        observed_at=WHEN,
        valid_from=WHEN,
        valid_to=None,
        status="VALID",
    )
    return transaction, evidence


def test_closed_local_date_and_zero_transaction_account_are_explicit() -> None:
    transaction, evidence = fixture()
    result = build_history_coverage(
        USER,
        "Asia/Shanghai",
        date(2026, 8, 5),
        date(2026, 8, 5),
        [OTHER_ACCOUNT, ACCOUNT],
        [transaction],
        {EVIDENCE: evidence},
    )
    assert result["scope_account_ids"] == [str(ACCOUNT), str(OTHER_ACCOUNT)]
    assert result["accounts"][0]["transaction_count"] == 1
    # Literal SHA-256 of the public empty-account snapshot {"transactions":[]}.
    assert result["accounts"][1] == {
        "account_id": str(OTHER_ACCOUNT),
        "transaction_count": 0,
        "bank_fact_digest": "50aa01eac0d44824862af3c008bc2baee05a0b866ddab1145b5eb84b09d99f77",
    }
    assert result["period_start"] == result["period_end"] == "2026-08-05"


@pytest.mark.parametrize(
    "change", ["foreign_user", "account_outside", "date_outside", "missing_evidence", "duplicate"]
)
def test_invalid_import_scope_is_rejected(change: str) -> None:
    transaction, evidence = fixture()
    rows = [transaction]
    sources: dict[UUID, Any] = {EVIDENCE: evidence}
    if change == "foreign_user":
        transaction.user_id = OTHER_ACCOUNT
    elif change == "account_outside":
        transaction.account_id = OTHER_ACCOUNT
    elif change == "date_outside":
        transaction.occurred_at = datetime(2026, 8, 4, 15, 59, 59, tzinfo=UTC)
    elif change == "missing_evidence":
        sources = {}
    else:
        rows.append(transaction)
    with pytest.raises(ValueError):
        build_history_coverage(
            USER, "Asia/Shanghai", date(2026, 8, 5), date(2026, 8, 5), [ACCOUNT], rows, sources
        )


@pytest.mark.parametrize(
    "change", ["bad_hash", "role_missing", "wrong_fact", "other_user", "naive_clock"]
)
def test_bank_payload_cannot_be_replaced_by_editable_classification(change: str) -> None:
    transaction, evidence = fixture()
    transaction.category = "asset_purchase"
    assert bank_fact_snapshot(transaction, evidence)["economic_role"] == "CONSUMPTION"
    if change == "bad_hash":
        evidence.content_hash = "a" * 64
    elif change == "role_missing":
        del evidence.content["economic_role"]
    elif change == "wrong_fact":
        transaction.amount_cents += 1
    elif change == "other_user":
        evidence.user_id = OTHER_ACCOUNT
    else:
        transaction.observed_at = WHEN.replace(tzinfo=None)
    with pytest.raises(ValueError):
        account_history_manifest(ACCOUNT, [transaction], {EVIDENCE: evidence})
