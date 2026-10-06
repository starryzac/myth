"""Strict acceptance and immutable bank-fact classification transition risks."""

import copy
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.db.models import EvidenceItem, Transaction
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.policy_configuration import configuration_hash
from app.domain.transaction_category import (
    CategoryConfirmationRequest,
    category_command,
    category_review_hash,
    verify_category_transition,
)
from app.services.audit_recording import audit_subject_data
from pydantic import ValidationError

USER, EPOCH, TX, BANK, DECL = (UUID(int=n) for n in range(1, 6))
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def originals() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    tx = Transaction(
        id=TX,
        user_id=USER,
        created_at=NOW,
        account_id=UUID(int=6),
        evidence_id=BANK,
        source_ref="original-bank-consumption",
        direction="DEBIT",
        amount_cents=18000,
        balance_after_cents=50000,
        occurred_at=NOW,
        observed_at=NOW,
        category="rent",
        category_confirmed=False,
        counterparty_ref="confirmed-landlord",
        is_one_off=False,
    )
    content = {
        "simulation": True,
        "transaction_id": str(TX),
        "account_id": str(tx.account_id),
        "direction": "DEBIT",
        "amount_cents": 18000,
        "balance_after_cents": 50000,
        "occurred_at": NOW.isoformat(),
        "counterparty_ref": tx.counterparty_ref,
        "economic_role": "CONSUMPTION",
    }
    bank = EvidenceItem(
        id=BANK,
        user_id=USER,
        created_at=NOW,
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_BANK_TRANSACTION",
        source_ref="independent-bank-original",
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
        observed_at=NOW,
        valid_from=NOW,
        valid_to=None,
    )
    before = audit_subject_data(tx)
    body = CategoryConfirmationRequest(
        category="rent",
        accepted=True,
        reviewed_transaction_hash=category_review_hash(EPOCH, before, bank_fact_snapshot(tx, bank)),
        reason="明确选择原消费类别",
        idempotency_key="original:category:1",
        expected_epoch_id=EPOCH,
    )
    command = category_command(USER, TX, body)
    receipt = {
        "transaction_id": str(TX),
        "before_category": "rent",
        "category": "rent",
        "before_confirmed": False,
        "confirmed": True,
        "bank_evidence_id": str(BANK),
        "bank_evidence_hash": bank.content_hash,
        "confirmed_at": NOW.isoformat(),
    }
    declared = {
        "simulation": True,
        "transaction_id": str(TX),
        "category": "rent",
        "confirmed": True,
        "actor": "synthetic_user",
        "basis": "explicit_user_category_selection",
        "original_command": command,
        "command_hash": configuration_hash(command),
        "original_receipt": receipt,
    }
    evidence = EvidenceItem(
        id=DECL,
        user_id=USER,
        created_at=NOW,
        evidence_level="USER_DECLARED",
        source_type="SIMULATED_USER_CATEGORY_CONFIRMATION",
        source_ref="explicit-category-command",
        content=declared,
        content_hash=configuration_hash(declared),
        status="VALID",
        observed_at=NOW,
        valid_from=NOW,
        valid_to=None,
    )
    tx.category_confirmed = True
    return before, audit_subject_data(tx), audit_subject_data(bank), audit_subject_data(evidence)


def verify(rows: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]) -> None:
    before, after, bank, declaration = rows
    verify_category_transition(
        user_id=USER,
        epoch_id=EPOCH,
        transaction_id=TX,
        before=before,
        after=after,
        bank=bank,
        declaration=declaration,
    )


def test_exact_first_category_confirmation_preserves_bank_originals() -> None:
    rows = originals()
    before = copy.deepcopy(rows)
    verify(rows)
    assert rows == before
    assert rows[0]["amount_cents"] == rows[1]["amount_cents"] == 18000


@pytest.mark.parametrize(
    "field,value",
    [
        ("accepted", False),
        ("accepted", 1),
        ("accepted", "true"),
        ("category", "INCOME"),
        ("reason", 5),
        ("idempotency_key", "a/b"),
        ("reviewed_transaction_hash", "fake"),
        ("amount_cents", 18000),
        ("bank_authority", True),
        ("user_id", str(USER)),
    ],
)
def test_request_never_accepts_inferred_acceptance_or_money_fields(field: str, value: Any) -> None:
    body = copy.deepcopy(originals()[3]["content"]["original_command"]["request"])
    body[field] = value
    with pytest.raises(ValidationError):
        CategoryConfirmationRequest.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_cents", 18001),
        ("balance_after_cents", 999999),
        ("account_id", str(UUID(int=7))),
        ("counterparty_ref", "different"),
        ("is_one_off", True),
        ("observed_at", "2026-10-05T00:00:00Z"),
    ],
)
def test_classification_cannot_change_economic_or_history_fields(field: str, value: Any) -> None:
    rows = originals()
    rows[1][field] = value
    with pytest.raises(ValueError):
        verify(rows)


@pytest.mark.parametrize(
    "case",
    [
        "different_owner",
        "bank_role",
        "receipt_amount_source",
        "old_already_confirmed",
        "declaration_hash",
        "wrong_review",
        "bank_amount",
    ],
)
def test_hash_recalculation_cannot_make_wrong_sources_into_valid_classification(case: str) -> None:
    before, after, bank, declaration = originals()
    if case == "different_owner":
        declaration["user_id"] = str(UUID(int=99))
    elif case == "bank_role":
        bank["content"]["economic_role"] = "INTERNAL_TRANSFER"
        bank["content_hash"] = configuration_hash(bank["content"])
    elif case == "receipt_amount_source":
        declaration["content"]["original_receipt"]["bank_evidence_id"] = str(UUID(int=99))
        declaration["content_hash"] = configuration_hash(declaration["content"])
    elif case == "old_already_confirmed":
        before["category_confirmed"] = True
    elif case == "declaration_hash":
        declaration["content_hash"] = "f" * 64
    elif case == "wrong_review":
        declaration["content"]["original_command"]["request"]["reviewed_transaction_hash"] = (
            "f" * 64
        )
        declaration["content"]["command_hash"] = configuration_hash(
            declaration["content"]["original_command"]
        )
        declaration["content_hash"] = configuration_hash(declaration["content"])
    else:
        bank["content"]["amount_cents"] += 1
        bank["content_hash"] = configuration_hash(bank["content"])
    with pytest.raises(ValueError):
        verify((before, after, bank, declaration))
