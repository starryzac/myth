"""Exact historical release projection originals; no current permission or money grant.

The caller must retain the common complete bank settlement, receipt/hash/clock
and audit-subject checks. This additional new-protocol check binds the two cash
transactions to internal transfers, including their original bank evidence.
It does not read current policy, modify originals or reinterpret old protocols.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid5

from app.domain.full_goal_release_execution import GoalReleaseEffect
from app.domain.policy_configuration import configuration_hash


def _clock(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("A frozen release clock must be an original aware ISO string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("A frozen release clock must be timezone-aware")
    return result.astimezone(UTC)


def _money(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError("Frozen release values must remain strict integer cents: " + field)
    return value


def verify_frozen_goal_release_projection(
    effect: GoalReleaseEffect,
    operation: dict[str, Any],
    receipt: dict[str, Any],
    postings: Iterable[dict[str, Any]],
    originals: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Check the original two transactions/proofs after common frozen bank validation.

    ``originals`` is the common verifier's map of referenced audit-subject data.
    Additional account, goal and permission subjects may be present; only the
    exact two original cash transactions and their exact evidence IDs are read.
    No current authorization or current income projection supplies these facts.
    """
    identity, user = str(effect.operation_id), str(effect.user_id)
    if (
        operation.get("id") != identity
        or operation.get("user_id") != user
        or operation.get("operation_type") != "RELEASE_GOAL"
        or operation.get("status") != "SETTLED"
        or receipt.get("user_id") != user
        or receipt.get("action_plan_id") != identity
        or receipt.get("id") != str(uuid5(effect.operation_id, "receipt"))
    ):
        raise ValueError("Frozen release projection must retain its original bank owner/identity")
    settled, reconciled = _clock(operation["settled_at"]), _clock(receipt["reconciled_at"])
    rows = list(postings)
    cash = [row for row in rows if row.get("ledger_dimension") == "ECONOMIC"]
    expected_legs = {
        f"cash:{effect.source_account_id}": (str(effect.source_account_id), -effect.amount_cents),
        f"cash:{effect.destination_account_id}": (
            str(effect.destination_account_id),
            effect.amount_cents,
        ),
    }
    if (
        len(rows) != 3
        or len(cash) != 2
        or {row.get("leg_ref") for row in cash} != set(expected_legs)
    ):
        raise ValueError("Frozen release needs exactly its two economic cash legs")
    body = receipt.get("response")
    transaction_ids = {
        str(uuid5(effect.operation_id, "transaction:" + leg)) for leg in expected_legs
    }
    if (
        not isinstance(body, dict)
        or set(body) != {"bank_operation_id", "posting_ids", "transaction_ids"}
        or body["bank_operation_id"] != identity
        or type(body["transaction_ids"]) is not list
        or len(body["transaction_ids"]) != 2
        or any(type(value) is not str for value in body["transaction_ids"])
        or set(body["transaction_ids"]) != transaction_ids
        or type(body["posting_ids"]) is not list
        or len(body["posting_ids"]) != 3
        or any(type(value) is not str for value in body["posting_ids"])
        or set(body["posting_ids"]) != {row.get("id") for row in rows}
    ):
        raise ValueError("Frozen release receipt requires exact original cash transactions/legs")
    counterparty = f"goal-release:{effect.source_goal_id}"
    for row in cash:
        leg = row["leg_ref"]
        account, delta = expected_legs[leg]
        posting_id = str(uuid5(effect.operation_id, "posting:" + leg))
        if (
            row.get("id") != posting_id
            or row.get("operation_id") != identity
            or row.get("user_id") != user
            or row.get("account_id") != account
            or row.get("ledger_key") != "CASH:" + account
            or type(row.get("delta_cents")) is not int
            or row["delta_cents"] != delta
            or _clock(row["occurred_at"]) != settled
        ):
            raise ValueError("Frozen release cash leg differs from its exact original identity")
        balance = _money(row, "balance_after_cents")
        transaction_id = str(uuid5(effect.operation_id, "transaction:" + leg))
        proof_id = str(uuid5(uuid5(effect.operation_id, "transaction:" + leg), "evidence"))
        transaction = originals.get(("TRANSACTION", transaction_id))
        proof = originals.get(("EVIDENCE", proof_id))
        if transaction is None or proof is None:
            raise ValueError("Frozen release original transaction or bank evidence is missing")
        source_ref = f"bank-operation:{identity}:{leg}"
        direction = "DEBIT" if delta < 0 else "CREDIT"
        expected_content = {
            "simulation": True,
            "user_id": user,
            "transaction_id": transaction_id,
            "account_id": account,
            "direction": direction,
            "amount_cents": abs(delta),
            "balance_after_cents": balance,
            "occurred_at": settled.isoformat(),
            "counterparty_ref": counterparty,
            "economic_role": "INTERNAL_TRANSFER",
            "bank_operation_id": identity,
            "bank_posting_id": posting_id,
        }
        content = proof.get("content")
        if (
            transaction.get("id") != transaction_id
            or transaction.get("user_id") != user
            or transaction.get("account_id") != account
            or transaction.get("evidence_id") != proof_id
            or transaction.get("source_ref") != source_ref
            or transaction.get("counterparty_ref") != counterparty
            or transaction.get("category") != "internal_transfer"
            or transaction.get("direction") != direction
            or _money(transaction, "amount_cents") != abs(delta)
            or _money(transaction, "balance_after_cents") != balance
            or _clock(transaction["occurred_at"]) != settled
            or any(
                _clock(transaction[field]) != reconciled for field in ("created_at", "observed_at")
            )
            or proof.get("id") != proof_id
            or proof.get("user_id") != user
            or proof.get("source_ref") != source_ref
            or proof.get("source_type") != "SIMULATED_BANK_TRANSACTION"
            or proof.get("evidence_level") != "BANK_CONFIRMED"
            or _clock(proof["valid_from"]) != settled
            or any(_clock(proof[field]) != reconciled for field in ("created_at", "observed_at"))
            or not isinstance(content, dict)
            or content.get("simulation") is not True
            or _money(content, "amount_cents") != abs(delta)
            or _money(content, "balance_after_cents") != balance
            or content != expected_content
            or proof.get("content_hash") != configuration_hash(content)
        ):
            raise ValueError(
                "Frozen release original cash cannot be relabeled as income or rebound"
            )
