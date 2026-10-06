"""Synthetic domain/metadata doubles only; no PG, no bank execution, no research."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import AuditEpoch, EvidenceItem, Transaction
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundarySnapshot, CashFact
from app.domain.future_income_planning import (
    FutureIncomeCandidateRequest,
    FutureIncomeConfirmationRequest,
    FutureIncomeSourceInventory,
    PlanningIncomeSource,
    assumption_for_source,
    conditional_schedule,
    source_hash,
)
from app.domain.income_ledger import IncomeOrigin
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services import future_income_planning as service
from app.services.audit_chain import row_copy
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import ValidationError
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 6, 1, tzinfo=UTC)
USER, EPOCH, ORIGIN, ACCOUNT, BANK, LEDGER = (UUID(int=n) for n in range(1, 7))
ACTOR = LocalActorPrincipal(
    user_id=USER,
    role="USER",
    session_id=UUID(int=7),
    issued_at=NOW - timedelta(minutes=1),
    expires_at=NOW + timedelta(minutes=14),
)


class ReadDouble:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = {(type(row), row.id): row for row in rows}

    def get(self, kind: type[Any], identifier: UUID) -> Any:
        return self.rows.get((kind, identifier))


def originals() -> tuple[PlanningIncomeSource, EvidenceItem, ReadDouble]:
    occurred = datetime(2026, 8, 31, 23, tzinfo=UTC)
    origin = IncomeOrigin(
        origin_transaction_id=ORIGIN,
        origin_account_id=ACCOUNT,
        amount_cents=231007,
        occurred_at=occurred,
        observed_at=occurred,
        bank_evidence_id=BANK,
        bank_evidence_hash="0" * 64,
    )
    bank_content = {
        "simulation": True,
        "user_id": str(USER),
        "economic_role": "INCOME",
        "transaction_id": str(ORIGIN),
        "account_id": str(ACCOUNT),
        "direction": "CREDIT",
        "amount_cents": 231007,
        "balance_after_cents": 231007,
        "occurred_at": occurred.isoformat(),
        "counterparty_ref": "synthetic-origin",
    }
    bank_row = EvidenceItem(
        id=BANK,
        user_id=USER,
        created_at=occurred,
        observed_at=occurred,
        valid_from=occurred,
        valid_to=None,
        supersedes_id=None,
        status="VALID",
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_BANK_TRANSACTION",
        source_ref="synthetic-bank-original",
        content=bank_content,
        content_hash=configuration_hash(bank_content),
    )
    origin = origin.model_copy(update={"bank_evidence_hash": bank_row.content_hash})
    ledger_content = {
        "simulation": True,
        "protocol": "new-funds-ledger-v2",
        "complete": True,
        "user_id": str(USER),
        "origins": [origin.model_dump(mode="json")],
    }
    ledger_row = EvidenceItem(
        id=LEDGER,
        user_id=USER,
        created_at=occurred,
        observed_at=occurred,
        valid_from=occurred,
        valid_to=None,
        supersedes_id=None,
        status="VALID",
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_NEW_FUNDS_LEDGER",
        source_ref="synthetic-ledger-original",
        content=ledger_content,
        content_hash=configuration_hash(ledger_content),
    )
    source = PlanningIncomeSource(
        user_id=USER,
        origin=origin,
        origin_hash=source_hash(origin),
        ledger_evidence_id=LEDGER,
        ledger_evidence_hash=ledger_row.content_hash,
    )
    transaction = Transaction(
        id=ORIGIN,
        user_id=USER,
        created_at=occurred,
        account_id=ACCOUNT,
        evidence_id=BANK,
        source_ref="synthetic-bank-original",
        direction="CREDIT",
        amount_cents=231007,
        balance_after_cents=231007,
        category="income",
        counterparty_ref="synthetic-origin",
        is_one_off=False,
        category_confirmed=False,
        occurred_at=occurred,
        observed_at=occurred,
    )
    epoch = AuditEpoch(id=EPOCH, user_id=USER, opened_at=NOW - timedelta(days=1))
    body = FutureIncomeCandidateRequest(
        expected_epoch_id=EPOCH,
        origin_transaction_id=ORIGIN,
        expected_origin_hash=source.origin_hash,
        idempotency_key="synthetic.candidate.1",
    )
    candidate_id, reference = service.identity(USER, EPOCH, body.idempotency_key)
    assumption = assumption_for_source(source, EPOCH, NOW, "UTC")
    payload = service.CandidatePayload(
        candidate_id=candidate_id,
        user_id=USER,
        epoch_id=EPOCH,
        admitted_at=NOW,
        confirmation_deadline=NOW + timedelta(minutes=15),
        actor=ACTOR,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        source=source,
        assumption=assumption,
        candidate_hash=configuration_hash(assumption.model_dump(mode="json")),
    )
    content = payload.model_dump(mode="json")
    candidate = EvidenceItem(
        id=candidate_id,
        user_id=USER,
        created_at=NOW,
        observed_at=NOW,
        valid_from=NOW,
        valid_to=None,
        supersedes_id=None,
        status="VALID",
        evidence_level="USER_DECLARED",
        source_type=service.CANDIDATE_SOURCE,
        source_ref=reference,
        content=content,
        content_hash=configuration_hash(content),
    )
    return source, candidate, ReadDouble([bank_row, ledger_row, transaction, epoch, candidate])


def confirmation(candidate: EvidenceItem) -> EvidenceItem:
    body = FutureIncomeConfirmationRequest(
        expected_epoch_id=EPOCH,
        candidate_id=candidate.id,
        reviewed_candidate_hash=candidate.content["candidate_hash"],
        accepted=True,
        idempotency_key="synthetic.confirm.1",
    )
    identifier, reference = service.identity(USER, EPOCH, body.idempotency_key)
    payload = service.ConfirmationPayload(
        confirmation_id=identifier,
        user_id=USER,
        epoch_id=EPOCH,
        candidate_id=candidate.id,
        candidate_hash=body.reviewed_candidate_hash,
        confirmed_at=NOW,
        actor=ACTOR,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        original_candidate_snapshot=row_copy(candidate),
    )
    content = payload.model_dump(mode="json")
    return EvidenceItem(
        id=identifier,
        user_id=USER,
        created_at=NOW,
        observed_at=NOW,
        valid_from=NOW,
        valid_to=None,
        supersedes_id=None,
        status="VALID",
        evidence_level="USER_DECLARED",
        source_type=service.CONFIRM_SOURCE,
        source_ref=reference,
        content=content,
        content_hash=configuration_hash(content),
    )


def test_original_metadata_is_not_authority_and_confirmation_preserves_each_row() -> None:
    _, row, double = originals()
    before = row_copy(row)
    candidate = service._candidate(cast(Session, double), row, USER, EPOCH, NOW)
    assert candidate.state == "REQUIRES_EXPLICIT_CONFIRMATION"
    confirm = confirmation(row)
    original, receipt = service._confirmation(cast(Session, double), confirm, USER, EPOCH, NOW)
    assert original.state == "USER_CONFIRMED" and receipt.original_request.accepted is True
    assert not receipt.grants_authority and not receipt.confirms_financial_action
    assert row_copy(row) == before
    assert (
        candidate.source.origin.amount_cents
        == original.assumption.conditional_amount_cents
        == 231007
    )
    assert original.assumption.monthly_local_day == 31


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "SUPERSEDED"),
        ("status", "CONFLICTED"),
        ("user_id", UUID(int=90)),
        ("source_type", "SIMULATED_BANK_TRANSACTION"),
        ("source_ref", "other-key"),
        ("observed_at", NOW + timedelta(seconds=1)),
        ("content_hash", "f" * 64),
    ],
)
def test_candidate_rejects_tampered_original_without_repair(field: str, value: Any) -> None:
    _, row, double = originals()
    setattr(row, field, value)
    retained = row_copy(row)
    with pytest.raises(PolicyLifecycleError, match="无法核验"):
        service._candidate(cast(Session, double), row, USER, EPOCH, NOW)
    assert row_copy(row) == retained


@pytest.mark.parametrize(
    "field", ["amount_cents", "now", "role", "result", "receipt", "conditional_amount_cents"]
)
def test_request_rejects_client_money_time_authority_and_outcome(field: str) -> None:
    with pytest.raises(ValidationError):
        FutureIncomeCandidateRequest.model_validate(
            {
                "expected_epoch_id": str(EPOCH),
                "origin_transaction_id": str(ORIGIN),
                "expected_origin_hash": "0" * 64,
                "idempotency_key": "native.ref",
                field: 70000,
            }
        )


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_confirmation_requires_actual_boolean_acceptance(accepted: Any) -> None:
    with pytest.raises(ValidationError):
        FutureIncomeConfirmationRequest.model_validate(
            {
                "expected_epoch_id": str(EPOCH),
                "candidate_id": str(ORIGIN),
                "reviewed_candidate_hash": "0" * 64,
                "idempotency_key": "confirm.native",
                "accepted": accepted,
            }
        )


def test_complete_365_dates_short_months_and_unknown_are_separate() -> None:
    source, row, _ = originals()
    assumption = assumption_for_source(source, EPOCH, NOW, "UTC")
    schedule = conditional_schedule(NOW, "UTC", [(row.id, assumption)])
    assert len(schedule) == 365
    funded = [d for d in schedule if d.conditional_income_cents]
    assert len(funded) == 12
    assert next(d for d in funded if d.date.month == 2).date.day == 28
    assert all(d.conditional_income_cents == 231007 for d in funded)
    assert all(not d.is_settled_cash and not d.availability_within_day_known for d in funded)
    assert all(d.conditional_income_cents is None for d in conditional_schedule(NOW, "UTC", None))
    with pytest.raises(ValueError, match="timezone"):
        conditional_schedule(NOW, "Asia/Shanghai", [(row.id, assumption)])


def test_repetition_amount_changes_never_change_original_execution_snapshot() -> None:
    source, row, _ = originals()
    snapshot = BoundarySnapshot(
        as_of=NOW,
        timezone="UTC",
        cash_accounts=[
            CashFact(account_id=ACCOUNT, account_type="CASH", balance_cents=200000, observed_at=NOW)
        ],
    )
    original = snapshot.model_dump(mode="json")
    boundary = compute_boundary(snapshot, [], [], [])
    assumption = assumption_for_source(source, EPOCH, NOW, "UTC")
    totals = []
    for amount in [1, 900007, 71000333]:
        changed = assumption.model_copy(update={"conditional_amount_cents": amount})
        totals.append(
            sum(
                d.conditional_income_cents or 0
                for d in conditional_schedule(NOW, "UTC", [(row.id, changed)])
            )
        )
        assert compute_boundary(snapshot, [], [], []) == boundary
        assert snapshot.model_dump(mode="json") == original
    assert len(set(totals)) == 3


def test_unregistered_and_conflicting_metadata_never_fill_zero_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, row, double = originals()
    inventory = FutureIncomeSourceInventory(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        timezone="UTC",
        status="VERIFIED_ORIGINAL_INCOME_SOURCES",
        original_origin_count=1,
        captured_origin_count=1,
        complete=True,
        sources=[source],
        issues=[],
    )
    monkeypatch.setattr(service, "readonly", lambda _: None)
    monkeypatch.setattr(service, "_sources", lambda *_: inventory)
    monkeypatch.setattr(service, "_records", lambda *_: [row])
    unknown = service.read_future_income_planning(cast(Session, double), USER, NOW)
    assert unknown.status == "NO_CONFIRMED_REGISTERED_SOURCE"
    assert unknown.total_conditional_income_cents is None
    assert all(d.conditional_income_cents is None for d in unknown.daily_schedule)
    confirmed = confirmation(row)
    monkeypatch.setattr(service, "_records", lambda *_: [row, confirmed])
    current = service.read_future_income_planning(cast(Session, double), USER, NOW)
    assert (
        current.status == "CONDITIONAL_PLANNING"
        and current.total_conditional_income_cents == 231007 * 12
    )
    corrupted = deepcopy(confirmed.content)
    corrupted["original_request"]["reviewed_candidate_hash"] = "f" * 64
    confirmed.content = corrupted
    retained = row_copy(confirmed)
    response = service.read_future_income_planning(cast(Session, double), USER, NOW)
    assert response.status == "UNKNOWN" and response.total_conditional_income_cents is None
    assert all(d.conditional_income_cents is None for d in response.daily_schedule)
    assert row_copy(confirmed) == retained and response.original_execution_view_changed is False


def test_expired_source_is_retained_but_not_reconfirmed() -> None:
    _, row, double = originals()
    source = service._candidate(
        cast(Session, double), row, USER, EPOCH, NOW + timedelta(minutes=15)
    )
    assert source.state == "EXPIRED" and source.original_evidence["id"] == str(row.id)


def test_original_bank_source_hash_and_role_cannot_be_replaced_by_category() -> None:
    _, row, double = originals()
    transaction = double.get(Transaction, ORIGIN)
    transaction.category = "salary"
    transaction.category_confirmed = True
    assert (
        service._candidate(
            cast(Session, double), row, USER, EPOCH, NOW
        ).assumption.monthly_local_day
        == 31
    )
    evidence = double.get(EvidenceItem, BANK)
    evidence.content = {**evidence.content, "economic_role": "PRINCIPAL_RETURN"}
    evidence.content_hash = configuration_hash(evidence.content)
    with pytest.raises(PolicyLifecycleError):
        service._candidate(cast(Session, double), row, USER, EPOCH, NOW)


def test_duplicate_confirmed_source_is_unknown_and_retains_both_originals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, row, double = originals()
    first = confirmation(row)
    second = confirmation(row)
    content = deepcopy(second.content)
    key = "synthetic.confirm.duplicate"
    second.id, second.source_ref = service.identity(USER, EPOCH, key)
    content["confirmation_id"] = str(second.id)
    content["original_request"]["idempotency_key"] = key
    content["request_hash"] = configuration_hash(content["original_request"])
    second.content, second.content_hash = content, configuration_hash(content)
    inventory = FutureIncomeSourceInventory(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        timezone="UTC",
        status="VERIFIED_ORIGINAL_INCOME_SOURCES",
        original_origin_count=1,
        captured_origin_count=1,
        complete=True,
        sources=[source],
        issues=[],
    )
    monkeypatch.setattr(service, "readonly", lambda _: None)
    monkeypatch.setattr(service, "_sources", lambda *_: inventory)
    monkeypatch.setattr(service, "_records", lambda *_: [row, first, second])
    response = service.read_future_income_planning(cast(Session, double), USER, NOW)
    assert response.status == "UNKNOWN" and response.total_conditional_income_cents is None
    assert response.plans[0].state == "CONFLICTED"
    assert {item["id"] for item in response.plans[0].original_metadata} == {
        str(row.id),
        str(first.id),
        str(second.id),
    }
