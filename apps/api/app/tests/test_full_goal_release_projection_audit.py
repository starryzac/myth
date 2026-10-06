"""Synthetic direct historical-integrity risks, never actual financial acceptance."""

from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.audit_chain import AuditContractError, build_subject, verify_frozen_projection
from app.domain.full_goal_release_audit import read_frozen_goal_release_action
from app.domain.full_goal_release_execution import GoalReleaseEffect
from app.domain.full_goal_release_projection_audit import verify_frozen_goal_release_projection
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_goal_release_audit import settled_originals
from app.tests.test_goal_release_provenance import NOW

Originals = dict[tuple[str, str], dict[str, Any]]


def fixture() -> tuple[
    GoalReleaseEffect, dict[str, Any], dict[str, Any], list[dict[str, Any]], Originals
]:
    action, operation, rows = settled_originals()
    effect = read_frozen_goal_release_action(action)[0].effect
    reconciled = NOW + timedelta(seconds=1)
    originals: Originals = {}
    transaction_ids = []
    for row in rows:
        if row["ledger_dimension"] != "ECONOMIC":
            continue
        identity = uuid5(effect.operation_id, "transaction:" + row["leg_ref"])
        proof_id = uuid5(identity, "evidence")
        source_ref = f"bank-operation:{effect.operation_id}:{row['leg_ref']}"
        counterparty = f"goal-release:{effect.source_goal_id}"
        direction = "DEBIT" if row["delta_cents"] < 0 else "CREDIT"
        transaction = {
            "id": str(identity),
            "user_id": str(effect.user_id),
            "account_id": row["account_id"],
            "evidence_id": str(proof_id),
            "source_ref": source_ref,
            "counterparty_ref": counterparty,
            "direction": direction,
            "amount_cents": abs(row["delta_cents"]),
            "balance_after_cents": row["balance_after_cents"],
            "category": "internal_transfer",
            "category_confirmed": False,
            "is_one_off": False,
            "created_at": reconciled.isoformat(),
            "observed_at": reconciled.isoformat(),
            "occurred_at": NOW.isoformat(),
        }
        content = {
            "simulation": True,
            "user_id": str(effect.user_id),
            "transaction_id": str(identity),
            "account_id": row["account_id"],
            "direction": direction,
            "amount_cents": abs(row["delta_cents"]),
            "balance_after_cents": row["balance_after_cents"],
            "occurred_at": NOW.isoformat(),
            "counterparty_ref": counterparty,
            "economic_role": "INTERNAL_TRANSFER",
            "bank_operation_id": str(effect.operation_id),
            "bank_posting_id": row["id"],
        }
        proof = {
            "id": str(proof_id),
            "user_id": str(effect.user_id),
            "source_type": "SIMULATED_BANK_TRANSACTION",
            "source_ref": source_ref,
            "evidence_level": "BANK_CONFIRMED",
            "content": content,
            "content_hash": configuration_hash(content),
            "created_at": reconciled.isoformat(),
            "observed_at": reconciled.isoformat(),
            "valid_from": NOW.isoformat(),
        }
        originals[("TRANSACTION", str(identity))] = transaction
        originals[("EVIDENCE", str(proof_id))] = proof
        transaction_ids.append(str(identity))
    receipt = {
        "id": str(uuid5(effect.operation_id, "receipt")),
        "user_id": str(effect.user_id),
        "action_plan_id": str(effect.operation_id),
        "reconciled_at": reconciled.isoformat(),
        "response": {
            "bank_operation_id": str(effect.operation_id),
            "posting_ids": [row["id"] for row in rows],
            "transaction_ids": transaction_ids,
        },
    }
    return effect, operation, receipt, rows, originals


def test_exact_two_internal_transfers_and_original_evidence_are_read_without_mutation() -> None:
    values = fixture()
    before = deepcopy(values)
    verify_frozen_goal_release_projection(*values)
    assert values == before
    assert all(
        proof["content"]["economic_role"] == "INTERNAL_TRANSFER"
        for (kind, _), proof in values[-1].items()
        if kind == "EVIDENCE"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("category", "income"),
        ("direction", "CREDIT"),
        ("id", str(UUID(int=999))),
        ("user_id", str(UUID(int=999))),
        ("account_id", str(UUID(int=999))),
        ("evidence_id", str(UUID(int=999))),
        ("source_ref", "external-fact:new-income"),
        ("counterparty_ref", "employer"),
        ("amount_cents", True),
        ("amount_cents", 100.0),
        ("amount_cents", 101),
        ("balance_after_cents", True),
        ("balance_after_cents", 201),
        ("occurred_at", (NOW + timedelta(seconds=1)).isoformat()),
        ("observed_at", NOW.isoformat()),
        ("created_at", NOW.isoformat()),
    ],
)
def test_transaction_category_uuid_money_clock_and_source_tamper_are_rejected(
    field: str, value: Any
) -> None:
    values = fixture()
    transaction = next(row for (kind, _), row in values[-1].items() if kind == "TRANSACTION")
    transaction[field] = value
    with pytest.raises(ValueError):
        verify_frozen_goal_release_projection(*values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("economic_role", "NEW_INCOME"),
        ("bank_operation_id", str(UUID(int=999))),
        ("bank_posting_id", str(UUID(int=999))),
        ("transaction_id", str(UUID(int=999))),
        ("account_id", str(UUID(int=999))),
        ("counterparty_ref", "employer"),
        ("amount_cents", True),
        ("amount_cents", 100.0),
        ("balance_after_cents", 200.0),
        ("simulation", 1),
        ("occurred_at", NOW.replace(tzinfo=None).isoformat()),
    ],
)
def test_rehashed_bank_content_cannot_relabel_original_cash_or_change_its_binding(
    field: str, value: Any
) -> None:
    values = fixture()
    proof = next(row for (kind, _), row in values[-1].items() if kind == "EVIDENCE")
    proof["content"][field] = value
    proof["content_hash"] = configuration_hash(proof["content"])
    with pytest.raises(ValueError):
        verify_frozen_goal_release_projection(*values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", str(UUID(int=999))),
        ("user_id", str(UUID(int=999))),
        ("source_ref", "external-fact:new-income"),
        ("source_type", "USER_DECLARED_TRANSACTION"),
        ("evidence_level", "USER_DECLARED"),
        ("valid_from", (NOW + timedelta(seconds=1)).isoformat()),
        ("observed_at", NOW.isoformat()),
        ("created_at", NOW.isoformat()),
        ("content_hash", "0" * 64),
    ],
)
def test_bank_evidence_original_metadata_tamper_is_rejected(field: str, value: Any) -> None:
    values = fixture()
    proof = next(row for (kind, _), row in values[-1].items() if kind == "EVIDENCE")
    proof[field] = value
    with pytest.raises(ValueError):
        verify_frozen_goal_release_projection(*values)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_transaction",
        "missing_evidence",
        "one_transaction",
        "duplicate_transaction",
        "extra_response_field",
        "extra_content_field",
        "wrong_operation",
        "wrong_posting",
        "missing_leg",
        "duplicate_leg",
        "wrong_receipt_owner",
        "naive_column_clock",
    ],
)
def test_full_transaction_denominator_response_and_original_leg_tamper_rejected(
    tamper: str,
) -> None:
    effect, operation, receipt, rows, originals = fixture()
    if tamper in {"missing_transaction", "missing_evidence"}:
        kind = "TRANSACTION" if tamper == "missing_transaction" else "EVIDENCE"
        originals.pop(next(key for key in originals if key[0] == kind))
    elif tamper == "one_transaction":
        receipt["response"]["transaction_ids"].pop()
    elif tamper == "duplicate_transaction":
        receipt["response"]["transaction_ids"][1] = receipt["response"]["transaction_ids"][0]
    elif tamper == "extra_response_field":
        receipt["response"]["transaction_id"] = receipt["response"]["transaction_ids"][0]
    elif tamper == "extra_content_field":
        proof = next(row for (kind, _), row in originals.items() if kind == "EVIDENCE")
        proof["content"]["income_origin_id"] = str(UUID(int=999))
        proof["content_hash"] = configuration_hash(proof["content"])
    elif tamper == "wrong_operation":
        operation["id"] = str(UUID(int=999))
    elif tamper == "wrong_posting":
        rows[0]["id"] = str(UUID(int=999))
        receipt["response"]["posting_ids"][0] = rows[0]["id"]
    elif tamper == "missing_leg":
        rows.pop()
    elif tamper == "duplicate_leg":
        rows[-1] = deepcopy(rows[0])
    elif tamper == "wrong_receipt_owner":
        receipt["user_id"] = str(UUID(int=999))
    else:
        operation["settled_at"] = NOW.replace(tzinfo=None).isoformat()
    with pytest.raises(ValueError):
        verify_frozen_goal_release_projection(effect, operation, receipt, rows, originals)


def common_projection_originals() -> tuple[
    GoalReleaseEffect,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    Originals,
]:
    effect, operation, receipt, rows, originals = fixture()
    action, _, _ = settled_originals()
    action["status"] = "SUCCEEDED"
    receipt.update(
        status="SUCCEEDED",
        attempt_number=1,
        receipt_ref=f"bank-operation:{effect.operation_id}",
        executed_cents=effect.amount_cents,
        fee_cents=0,
        loss_cents=0,
        occurred_at=operation["settled_at"],
        created_at=receipt["reconciled_at"],
    )
    return effect, operation, action, receipt, rows, originals


def test_common_frozen_projection_entry_reads_the_actual_new_helper() -> None:
    effect, operation, action, receipt, rows, originals = common_projection_originals()
    subjects = [
        build_subject(
            user_id=effect.user_id,
            epoch_id=effect.epoch_id,
            kind=kind,
            id=UUID(identity),
            data=data,
        )
        for (kind, identity), data in originals.items()
    ]
    before = deepcopy((operation, action, receipt, rows, subjects))
    verify_frozen_projection(
        operation,
        action,
        receipt,
        rows,
        observed_at=NOW + timedelta(seconds=2),
        subjects=subjects,
    )
    assert (operation, action, receipt, rows, subjects) == before


@pytest.mark.parametrize("tamper", ["category", "rehashed_role", "missing_second_transaction"])
def test_common_frozen_projection_rejects_relabelled_or_missing_originals(tamper: str) -> None:
    effect, operation, action, receipt, rows, originals = common_projection_originals()
    if tamper == "category":
        transaction = next(row for (kind, _), row in originals.items() if kind == "TRANSACTION")
        transaction["category"] = "income"
    elif tamper == "rehashed_role":
        proof = next(row for (kind, _), row in originals.items() if kind == "EVIDENCE")
        proof["content"]["economic_role"] = "NEW_INCOME"
        proof["content_hash"] = configuration_hash(proof["content"])
    else:
        originals.pop(next(key for key in originals if key[0] == "TRANSACTION"))
    subjects = [
        build_subject(
            user_id=effect.user_id,
            epoch_id=effect.epoch_id,
            kind=kind,
            id=UUID(identity),
            data=data,
        )
        for (kind, identity), data in originals.items()
    ]
    with pytest.raises(AuditContractError):
        verify_frozen_projection(
            operation,
            action,
            receipt,
            rows,
            observed_at=NOW + timedelta(seconds=2),
            subjects=subjects,
        )
