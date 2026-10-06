"""Actual adapter routing with explicit ORM doubles; no DB/financial measurement."""

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import ActionPlan, ActionReceipt, BankOperation, Goal, SimulatedBankPosting, User
from app.services import goal_release_provenance as service
from app.services.income_ledger import IncomeLedgerState
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_goal_release_provenance import (
    ACCOUNT,
    EPOCH,
    GOAL,
    NOW,
    USER,
    VERSION,
    fixture,
)
from sqlalchemy.orm import Session


class ReadSession:
    no_autoflush = nullcontext()

    def __init__(self) -> None:
        data = fixture()
        self.user = User(id=USER, is_simulated=True)
        self.goal = Goal(
            id=GOAL,
            user_id=USER,
            account_id=ACCOUNT,
            policy_version_id=VERSION,
            policy_id=UUID(int=8300),
            allocated_cents=300,
            created_at=NOW,
        )
        self.rows: dict[str, list[Any]] = {
            "action_plans": [],
            "bank_operations": [],
            "action_receipts": [],
            "simulated_bank_postings": [],
        }
        self.count_overrides: dict[str, int] = {}
        self.reads = 0
        for row in data.originals:
            action = ActionPlan(
                id=row.action_id,
                user_id=USER,
                goal_id=row.action_goal_id,
                created_at=NOW,
                action_type=row.action_type,
                status="SUCCEEDED",
                request=row.action_request,
                request_hash=row.action_request_hash,
                idempotency_key=row.bank_idempotency_key,
            )
            bank = BankOperation(
                id=row.bank_id,
                user_id=USER,
                action_plan_id=row.action_id,
                created_at=NOW,
                legacy_redemption_id=None,
                request=row.bank_request,
                request_hash=row.bank_request_hash,
                idempotency_key=row.bank_idempotency_key,
            )
            receipt = ActionReceipt(
                id=UUID(int=row.action_id.int + 100),
                user_id=USER,
                action_plan_id=row.action_id,
                created_at=NOW,
                response={},
                status="SUCCEEDED",
            )
            self.rows["action_plans"].append(action)
            self.rows["bank_operations"].append(bank)
            self.rows["action_receipts"].append(receipt)
        for posting in data.goal_cash_history:
            self.rows["simulated_bank_postings"].append(
                SimulatedBankPosting(
                    id=posting.posting_id,
                    user_id=USER,
                    account_id=ACCOUNT,
                    created_at=NOW,
                    operation_id=posting.operation_id,
                    leg_ref=None if posting.entry_kind == "OPENING" else "goal_cash",
                    previous_posting_id=None
                    if posting.entry_kind == "OPENING"
                    else data.goal_cash_history[0].posting_id,
                    ledger_dimension="GOAL_OWNERSHIP",
                    occurred_at=NOW,
                    ledger_key=f"GOAL_CASH:{GOAL}",
                    ledger_metadata=posting.ledger_metadata,
                    entry_kind=posting.entry_kind,
                    sequence_number=posting.sequence_number,
                    balance_before_cents=posting.balance_before_cents,
                    delta_cents=posting.delta_cents,
                    balance_after_cents=posting.balance_after_cents,
                )
            )

    def scalar(self, statement: Any) -> Any:
        self.reads += 1
        entity = statement.column_descriptions[0].get("entity")
        if entity is User:
            return self.user
        if entity is Goal:
            return self.goal
        table = statement.get_final_froms()[0].name
        return self.count_overrides.get(table, len(self.rows[table]))

    def scalars(self, statement: Any) -> list[Any]:
        self.reads += 1
        entity = statement.column_descriptions[0]["entity"]
        return self.rows[entity.__tablename__]


def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[ReadSession, list[str]]:
    session = ReadSession()
    data = fixture()
    assert data.income is not None and data.income_evidence_id is not None
    assert data.income_evidence_hash is not None
    calls: list[str] = []

    def rr(session: Session) -> None:
        calls.append("actual_read_snapshot_gate")

    monkeypatch.setattr(service, "_read_snapshot", rr)
    monkeypatch.setattr(service, "historical_ledger_scope", lambda *args: nullcontext())
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *args: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    monkeypatch.setattr(service, "full_reconciliation", lambda *args: data.reconciliation)
    monkeypatch.setattr(
        service,
        "read_income_state",
        lambda *args: IncomeLedgerState(
            ledger=data.income,
            evidence_id=data.income_evidence_id,
            evidence_hash=data.income_evidence_hash,
        ),
    )
    return session, calls


def read(session: ReadSession) -> Any:
    return service.read_goal_cash_source_proof(
        cast(Session, session), USER, GOAL, EPOCH, VERSION, NOW
    )


def test_adapter_keeps_original_reference_hashes_and_repeats_current_reads_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, calls = setup(monkeypatch)
    first = read(session)
    assert first.state == "VERIFIED_CASH_ONLY" and first.exactly_attributed_goal_cash_cents == 300
    assert (
        len(first.original_operation_refs) == 6 and len(first.original_goal_cash_posting_refs) == 2
    )
    reads = session.reads
    session.rows["action_plans"][0].request_hash = "8" * 64
    second = read(session)
    assert second.state == "UNKNOWN" and second.exactly_attributed_goal_cash_cents is None
    assert second.source_binding_hash != first.source_binding_hash and session.reads == 2 * reads
    assert calls == ["actual_read_snapshot_gate"] * 2
    assert first.assigned_income_decrease_cents == second.available_income_increase_cents == 0


@pytest.mark.parametrize("wrong", ["epoch", "version", "owner"])
def test_current_owner_epoch_goal_version_gates_reject_before_source_use(
    monkeypatch: pytest.MonkeyPatch,
    wrong: str,
) -> None:
    session, _ = setup(monkeypatch)
    if wrong == "epoch":
        monkeypatch.setattr(
            service,
            "current_audit_epoch",
            lambda *args: SimpleNamespace(id=UUID(int=8399), status="OPEN"),
        )
    elif wrong == "version":
        session.goal.policy_version_id = UUID(int=8399)
    else:
        session.user.is_simulated = False
    with pytest.raises(PolicyLifecycleError):
        read(session)
    assert session.reads == 2


def test_incomplete_original_bank_denominator_stays_unknown_with_actual_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup(monkeypatch)
    session.count_overrides["bank_operations"] = 3
    result = read(session)
    assert result.state == "UNKNOWN" and result.complete_original_bank_operation_count is None
    assert result.captured_original_bank_operation_count == 2
    assert "ORIGINAL_BANK_OPERATIONS_INVENTORY_INCOMPLETE" in result.reasons


def test_income_verifier_failure_remains_original_code_not_a_fake_empty_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup(monkeypatch)

    def fail(*args: Any) -> None:
        raise PolicyLifecycleError("INVALID_NEW_FUNDS_LEDGER", "actual verifier refused", 409)

    monkeypatch.setattr(service, "read_income_state", fail)
    result = read(session)
    assert result.state == "UNKNOWN" and result.complete_original_income_fragment_count is None
    assert "INVALID_NEW_FUNDS_LEDGER" in result.reasons
    assert not result.funds_released and result.assigned_income_decrease_cents == 0
