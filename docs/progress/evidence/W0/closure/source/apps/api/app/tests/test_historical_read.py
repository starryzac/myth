"""Independent PostgreSQL boundaries for invocation-local historical ledger verification."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import SimulatedBankPosting, User
from app.domain.bank_posting_codec import POSTING_V2_FIELDS
from app.services import simulated_bank
from app.services.audit_chain import get_audit_head
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.historical_read import historical_ledger_scope, verify_historical_ledger
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_api_audit import prepared_transfer
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def ledger_engine(demo_engine: Engine) -> Engine:
    seed_demo(demo_engine)
    return demo_engine


@pytest.fixture
def ledger_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Session, UUID]]:
    calls: list[tuple[Session, UUID]] = []
    original = simulated_bank.ledger_heads

    def observe(
        session: Session,
        user_id: UUID,
        *,
        _verified_posting: Callable[[SimulatedBankPosting], None] | None = None,
    ) -> dict[str, SimulatedBankPosting]:
        calls.append((session, user_id))
        if _verified_posting is None:
            return original(session, user_id)
        return original(session, user_id, _verified_posting=_verified_posting)

    monkeypatch.setattr(simulated_bank, "ledger_heads", observe)
    return calls


def readonly_transaction(session: Session) -> None:
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    assert session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
    assert session.scalar(text("SHOW transaction_read_only")) == "on"


def verify_twice(session: Session) -> None:
    verify_historical_ledger(session, DEMO_USER_ID)
    verify_historical_ledger(session, DEMO_USER_ID)


def test_one_rr_readonly_scope_verifies_once_and_separate_scope_verifies_again(
    ledger_engine: Engine, ledger_calls: list[tuple[Session, UUID]]
) -> None:
    before = all_tables(ledger_engine)
    with Session(ledger_engine) as session:
        readonly_transaction(session)
        with historical_ledger_scope(session):
            verify_twice(session)
            assert len(ledger_calls) == 1
        with historical_ledger_scope(session):
            verify_twice(session)
            assert len(ledger_calls) == 2
        # Ending the request scope also prevents reuse in its still-open transaction.
        verify_twice(session)
        assert len(ledger_calls) == 4
    assert all_tables(ledger_engine) == before


def test_read_committed_and_write_transactions_never_reuse_ledger_verification(
    ledger_engine: Engine, ledger_calls: list[tuple[Session, UUID]]
) -> None:
    before = all_tables(ledger_engine)
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE",
    ):
        with Session(ledger_engine) as session:
            session.execute(text(statement))
            previous = len(ledger_calls)
            with historical_ledger_scope(session):
                verify_twice(session)
            assert len(ledger_calls) == previous + 2, statement
    assert all_tables(ledger_engine) == before


def test_pending_new_dirty_and_deleted_objects_invalidate_reuse_without_flushing(
    ledger_engine: Engine, ledger_calls: list[tuple[Session, UUID]]
) -> None:
    before = all_tables(ledger_engine)
    with Session(ledger_engine, autoflush=False) as session:
        readonly_transaction(session)
        user = session.get(User, DEMO_USER_ID)
        assert user is not None
        for state in ("new", "dirty", "deleted"):
            previous = len(ledger_calls)
            pending = User(id=uuid4(), external_ref=str(uuid4()), display_name="unflushed")
            with historical_ledger_scope(session):
                verify_twice(session)
                assert len(ledger_calls) == previous + 1
                if state == "new":
                    session.add(pending)
                    assert session.new
                elif state == "dirty":
                    user.display_name = "unflushed changed name"
                    assert session.dirty
                else:
                    session.delete(user)
                    assert session.deleted
                verify_twice(session)
                assert len(ledger_calls) == previous + 3, state
                if state == "new":
                    assert pending in session.new
                    session.expunge(pending)
                elif state == "dirty":
                    assert user in session.dirty
                    session.expire(user)
                else:
                    assert user in session.deleted
                    session.add(user)
                assert not (session.new or session.dirty or session.deleted)
                verify_twice(session)
                assert len(ledger_calls) == previous + 4, state
    assert all_tables(ledger_engine) == before


def test_session_transaction_and_nested_boundaries_never_reuse_another_snapshot(
    ledger_engine: Engine, ledger_calls: list[tuple[Session, UUID]]
) -> None:
    before = all_tables(ledger_engine)
    with Session(ledger_engine) as session:
        readonly_transaction(session)
        with historical_ledger_scope(session):
            verify_twice(session)
            assert len(ledger_calls) == 1
            with Session(ledger_engine) as other:
                readonly_transaction(other)
                verify_twice(other)
                assert len(ledger_calls) == 3
            verify_twice(session)
            assert len(ledger_calls) == 4
            with session.begin_nested():
                verify_twice(session)
                assert len(ledger_calls) == 6
            verify_twice(session)
            assert len(ledger_calls) == 7
            session.rollback()
            readonly_transaction(session)
            verify_twice(session)
            assert len(ledger_calls) == 9
    assert all_tables(ledger_engine) == before


def test_exception_closes_scope_and_cannot_leave_a_verified_request_context(
    ledger_engine: Engine, ledger_calls: list[tuple[Session, UUID]]
) -> None:
    before = all_tables(ledger_engine)
    with Session(ledger_engine) as session:
        readonly_transaction(session)
        with pytest.raises(RuntimeError, match="injected request failure"):
            with historical_ledger_scope(session):
                verify_twice(session)
                raise RuntimeError("injected request failure")
        assert len(ledger_calls) == 1
        verify_twice(session)
        assert len(ledger_calls) == 3
    assert all_tables(ledger_engine) == before


def posting_copy(row: SimulatedBankPosting) -> dict[str, Any]:
    return {name: getattr(row, name) for name in POSTING_V2_FIELDS}


def test_next_request_rejects_nonhead_tampering_even_when_all_ledger_heads_are_unchanged(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    prepared = prepared_transfer(client, engine)
    action_id = prepared["action_id"]
    confirmed = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        json={"effect_hash": prepared["effect_hash"], "accepted": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    completed = client.post(f"/api/v1/actions/{action_id}/execute", json={})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "SUCCEEDED"
    with Session(engine) as session:
        readonly_transaction(session)
        heads = simulated_bank.ledger_heads(session, DEMO_USER_ID)
        original_heads = {row.id: posting_copy(row) for row in heads.values()}
        source_key = "CASH:" + prepared["effect"]["cash_uses"][0]["account_id"]
        source_head = heads[source_key]
        assert source_head.sequence_number > 1 and source_head.previous_posting_id is not None
        predecessor_id = source_head.previous_posting_id
        audit_head = get_audit_head(session, DEMO_USER_ID)
        with historical_ledger_scope(session):
            verify_twice(session)
    # Explicit administrator fault injection is confined to this generated test database.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE simulated_bank_postings DISABLE TRIGGER simulated_bank_postings_immutable"
        )
        changed = connection.execute(
            text(
                "UPDATE simulated_bank_postings "
                "SET delta_cents=delta_cents+1, balance_after_cents=balance_after_cents+1 "
                "WHERE id=:id"
            ),
            {"id": predecessor_id},
        )
        assert changed.rowcount == 1
        connection.exec_driver_sql(
            "ALTER TABLE simulated_bank_postings ENABLE TRIGGER simulated_bank_postings_immutable"
        )
    corrupted = all_tables(engine)
    with Session(engine) as session:
        readonly_transaction(session)
        for identity, original in original_heads.items():
            row = session.get(SimulatedBankPosting, identity)
            assert row is not None and posting_copy(row) == original
        assert get_audit_head(session, DEMO_USER_ID) == audit_head
        with historical_ledger_scope(session):
            with pytest.raises(PolicyLifecycleError, match="economic chain is inconsistent"):
                verify_historical_ledger(session, DEMO_USER_ID)
    # A real subsequent public read must also reject the unchanged-head forgery without repair.
    response = client.get(f"/api/v1/actions/{action_id}/receipt")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BANK_RECONCILIATION_REQUIRED"
    assert all_tables(engine) == corrupted


def test_in_place_nonhead_metadata_mutation_invalidates_same_request_ledger_result(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    prepared = prepared_transfer(client, engine)
    action_id = prepared["action_id"]
    confirmed = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        json={"effect_hash": prepared["effect_hash"], "accepted": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    completed = client.post(f"/api/v1/actions/{action_id}/execute", json={})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "SUCCEEDED"
    before = all_tables(engine)
    with Session(engine, autoflush=False) as session:
        readonly_transaction(session)
        heads = simulated_bank.ledger_heads(session, DEMO_USER_ID)
        source_key = "CASH:" + prepared["effect"]["cash_uses"][0]["account_id"]
        head = heads[source_key]
        assert head.previous_posting_id is not None
        predecessor = session.get(SimulatedBankPosting, head.previous_posting_id)
        assert predecessor is not None and predecessor.ledger_metadata == head.ledger_metadata
        with historical_ledger_scope(session):
            verify_twice(session)
            # SQLAlchemy's plain JSONB mapping does not mark nested/in-place edits dirty.
            predecessor.ledger_metadata["in_place_original_tampering"] = True
            assert not (session.new or session.dirty or session.deleted)
            with pytest.raises(PolicyLifecycleError, match="economic chain is inconsistent"):
                verify_historical_ledger(session, DEMO_USER_ID)
    assert all_tables(engine) == before
