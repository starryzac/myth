"""Canonical simulated bank-history snapshots; editable classifications are not bank facts."""

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from app.domain.policy_configuration import configuration_hash

COVERAGE_PROTOCOL = "transaction-history-coverage-v1"
COVERAGE_SOURCE_TYPE = "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
ECONOMIC_ROLES = frozenset(
    {
        "CONSUMPTION",
        "INCOME",
        "OPENING",
        "INTERNAL_TRANSFER",
        "ASSET_PURCHASE",
        "CREDIT_CARD_PAYMENT",
        "PRINCIPAL_RETURN",
    }
)


class BankTransaction(Protocol):
    id: UUID
    user_id: UUID
    account_id: UUID
    evidence_id: UUID | None
    source_ref: str
    direction: str
    amount_cents: int
    balance_after_cents: int | None
    occurred_at: datetime
    observed_at: datetime
    counterparty_ref: str | None


class BankEvidence(Protocol):
    id: UUID
    user_id: UUID
    evidence_level: str
    source_type: str
    source_ref: str
    content: dict[str, Any]
    content_hash: str
    valid_from: datetime
    valid_to: datetime | None
    observed_at: datetime
    status: str


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Bank history timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def bank_fact_snapshot(transaction: BankTransaction, evidence: BankEvidence) -> dict[str, Any]:
    """Bind a transaction to its bank evidence without reading its editable category or flags."""
    if transaction.evidence_id != evidence.id or transaction.user_id != evidence.user_id:
        raise ValueError("Bank transaction evidence identity does not match")
    role = evidence.content.get("economic_role")
    if not isinstance(role, str) or role not in ECONOMIC_ROLES:
        raise ValueError("A bank-provided economic role is required")
    if (
        evidence.evidence_level != "BANK_CONFIRMED"
        or evidence.source_type != "SIMULATED_BANK_TRANSACTION"
        or evidence.content.get("simulation") is not True
        or configuration_hash(evidence.content) != evidence.content_hash
    ):
        raise ValueError("Valid simulated bank transaction evidence is required")
    payload = {
        "transaction_id": str(transaction.id),
        "account_id": str(transaction.account_id),
        "direction": transaction.direction,
        "amount_cents": transaction.amount_cents,
        "balance_after_cents": transaction.balance_after_cents,
        "occurred_at": _timestamp(transaction.occurred_at),
        "counterparty_ref": transaction.counterparty_ref,
    }
    if any(key not in evidence.content for key in payload) or configuration_hash(
        {key: evidence.content.get(key) for key in payload}
    ) != configuration_hash(payload):
        raise ValueError("Bank transaction evidence payload does not match")
    return {
        **payload,
        "user_id": str(transaction.user_id),
        "source_ref": transaction.source_ref,
        "observed_at": _timestamp(transaction.observed_at),
        "economic_role": role,
        "evidence_id": str(evidence.id),
        "evidence_level": evidence.evidence_level,
        "evidence_source_type": evidence.source_type,
        "evidence_source_ref": evidence.source_ref,
        "evidence_hash": evidence.content_hash,
        "evidence_observed_at": _timestamp(evidence.observed_at),
        "evidence_valid_from": _timestamp(evidence.valid_from),
        "evidence_valid_to": _timestamp(evidence.valid_to) if evidence.valid_to else None,
        "evidence_status": evidence.status,
    }


def account_history_manifest(
    account_id: UUID,
    transactions: Iterable[BankTransaction],
    evidence_by_id: Mapping[UUID, BankEvidence],
) -> dict[str, Any]:
    """Summarize all supplied rows of one account, including an explicit zero-row digest."""
    rows = sorted(
        (row for row in transactions if row.account_id == account_id), key=lambda row: row.id
    )
    if len({row.id for row in rows}) != len(rows):
        raise ValueError("Duplicate transaction identity in history coverage")
    snapshots = []
    for row in rows:
        evidence = evidence_by_id.get(row.evidence_id) if row.evidence_id is not None else None
        if evidence is None:
            raise ValueError("Bank history evidence is missing")
        snapshots.append(bank_fact_snapshot(row, evidence))
    return {
        "account_id": str(account_id),
        "transaction_count": len(snapshots),
        "bank_fact_digest": configuration_hash({"transactions": snapshots}),
    }


def build_history_coverage(
    user_id: UUID,
    timezone: str,
    period_start: date,
    period_end: date,
    account_ids: Iterable[UUID],
    transactions: Iterable[BankTransaction],
    evidence_by_id: Mapping[UUID, BankEvidence],
) -> dict[str, Any]:
    """Build the importer's closed-date coverage declaration; callers supply its known time."""
    if period_end < period_start or timezone not in {"Asia/Shanghai", "UTC"}:
        raise ValueError("Invalid history coverage date interval or timezone")
    zone = UTC if timezone == "UTC" else _SHANGHAI
    ids = list(account_ids)
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("History coverage requires a nonempty unique account scope")
    rows = list(transactions)
    if any(
        row.user_id != user_id
        or row.account_id not in ids
        or not period_start
        <= datetime.fromisoformat(_timestamp(row.occurred_at)).astimezone(zone).date()
        <= period_end
        for row in rows
    ):
        raise ValueError("A bank fact is outside the declared user, account or calendar scope")
    return {
        "simulation": True,
        "protocol": COVERAGE_PROTOCOL,
        "user_id": str(user_id),
        "timezone": timezone,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "scope_account_ids": sorted(str(identifier) for identifier in ids),
        "accounts": [
            account_history_manifest(identifier, rows, evidence_by_id) for identifier in sorted(ids)
        ],
    }


_SHANGHAI = timezone(timedelta(hours=8))
