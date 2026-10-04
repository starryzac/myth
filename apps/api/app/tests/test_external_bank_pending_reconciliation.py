"""Repair settled external facts in original cash order without resending either salary."""

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, ActionResourceReservation, ExternalBankFact, SimulatedBankPosting
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services import external_bank_facts
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.external_bank_facts import ingest_external_fact
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import ledger_heads, validate_bank_projection
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_external_bank_facts import NOW, fact_request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_two_pending_salaries_repair_in_cash_order_without_resending_bank_legs(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_demo(demo_engine)
    request_a = fact_request(demo_engine, amount=600000, ref="pending-salary-a", key="first-key-a")
    request_b = ExternalFactRequest.model_validate(
        {
            **fact_request(
                demo_engine, amount=700000, ref="pending-salary-b", key="first-key-b"
            ).model_dump(),
            "occurred_at": NOW + timedelta(minutes=1),
        }
    )
    with Session(demo_engine) as session:
        account = session.get(Account, request_a.account_id)
        assert account is not None
        cash_before, observation_before = account.balance_cents, account.observed_at
        income_before = read_income_state(session, DEMO_USER_ID, NOW).ledger

    def claims() -> str:
        with Session(demo_engine) as session:
            rows = [
                dict(row)
                for row in session.execute(
                    select(ActionResourceReservation.__table__)
                    .where(ActionResourceReservation.user_id == DEMO_USER_ID)
                    .order_by(ActionResourceReservation.id)
                ).mappings()
            ]
        return json.dumps(rows, sort_keys=True, default=str)

    claims_before = claims()
    project = external_bank_facts._project

    def interrupted_application(session: Session, fact: ExternalBankFact, now: datetime) -> None:
        raise PolicyLifecycleError(
            "SIMULATED_PROJECTION_INTERRUPTION", "First bank committed before application loss", 409
        )

    # Only the application stage is interrupted. Both actual bank transactions run unchanged.
    monkeypatch.setattr(external_bank_facts, "_project", interrupted_application)
    pending_a = ingest_external_fact(demo_engine, DEMO_USER_ID, request_a, NOW)
    assert pending_a.bank_status == "SETTLED" and pending_a.projection_status == "UNKNOWN"
    monkeypatch.setattr(external_bank_facts, "_project", project)
    pending_b = ingest_external_fact(
        demo_engine, DEMO_USER_ID, request_b, NOW + timedelta(minutes=1)
    )
    assert pending_b.bank_status == "SETTLED" and pending_b.projection_status == "UNKNOWN"
    assert pending_b.projection_error == "EXTERNAL_BANK_RECONCILIATION_REQUIRED"
    fact_ids = (pending_a.external_fact_id, pending_b.external_fact_id)

    def bank_originals() -> tuple[dict[UUID, tuple[Any, ...]], str]:
        with Session(demo_engine) as session:
            facts = {
                row.id: (
                    row.idempotency_key,
                    row.request_canonical_text,
                    row.request_hash,
                    row.observed_at,
                    row.accepted_at,
                    row.settled_at,
                    row.bank_result_canonical_text,
                    row.bank_result_hash,
                )
                for row in session.scalars(
                    select(ExternalBankFact).where(ExternalBankFact.id.in_(fact_ids))
                )
            }
            economic = [
                dict(row)
                for row in session.execute(
                    select(SimulatedBankPosting.__table__)
                    .where(
                        SimulatedBankPosting.external_fact_id.in_(fact_ids),
                        SimulatedBankPosting.ledger_dimension == "ECONOMIC",
                    )
                    .order_by(SimulatedBankPosting.id)
                ).mappings()
            ]
        assert set(facts) == set(fact_ids) and len(economic) == 4
        assert {row["id"] for row in economic} == {
            *pending_a.economic_posting_ids,
            *pending_b.economic_posting_ids,
        }
        return facts, json.dumps(economic, sort_keys=True, default=str)

    originals_before = bank_originals()
    assert originals_before[0][pending_a.external_fact_id][0] == request_a.idempotency_key
    assert originals_before[0][pending_b.external_fact_id][0] == request_b.idempotency_key
    with Session(demo_engine) as session:
        account = session.get(Account, request_a.account_id)
        assert account is not None
        assert (account.balance_cents, account.observed_at) == (cash_before, observation_before)
        assert (
            read_income_state(session, DEMO_USER_ID, NOW + timedelta(minutes=1)).ledger
            == income_before
        )
        assert (
            ledger_heads(session, DEMO_USER_ID)["CASH:" + str(account.id)].balance_after_cents
            == cash_before + request_a.amount_cents + request_b.amount_cents
        )
    assert claims() == claims_before

    still_pending_b = ingest_external_fact(
        demo_engine, DEMO_USER_ID, request_b, NOW + timedelta(minutes=2)
    )
    assert still_pending_b.projection_status == "UNKNOWN" and still_pending_b.transaction_id is None
    assert bank_originals() == originals_before and claims() == claims_before

    repaired_a = ingest_external_fact(
        demo_engine, DEMO_USER_ID, request_a, NOW + timedelta(minutes=3)
    )
    assert repaired_a.bank_status == "SETTLED" and repaired_a.projection_status == "PROJECTED", (
        repaired_a
    )
    assert repaired_a.transaction_id is not None
    assert repaired_a.economic_posting_ids == pending_a.economic_posting_ids
    with Session(demo_engine) as session:
        second = session.get(ExternalBankFact, pending_b.external_fact_id)
        assert second is not None and second.projection_status == "UNKNOWN"
        account = session.get(Account, request_a.account_id)
        assert account is not None and account.balance_cents == cash_before + request_a.amount_cents
        with pytest.raises(PolicyLifecycleError) as unresolved:
            validate_bank_projection(session, DEMO_USER_ID, NOW + timedelta(minutes=3))
        assert unresolved.value.code == "EXTERNAL_BANK_RECONCILIATION_REQUIRED"
        validate_bank_projection(
            session, DEMO_USER_ID, NOW + timedelta(minutes=3), allow_unprojected=True
        )
        partial = read_income_state(session, DEMO_USER_ID, NOW + timedelta(minutes=3)).ledger
        assert partial.reservations == income_before.reservations
        assert sum(row.assigned_cents for row in partial.fragments) == sum(
            row.assigned_cents for row in income_before.fragments
        )
        assert sum(row.reserved_cents for row in partial.fragments) == sum(
            row.reserved_cents for row in income_before.fragments
        )
    assert bank_originals() == originals_before and claims() == claims_before

    repaired_b = ingest_external_fact(
        demo_engine, DEMO_USER_ID, request_b, NOW + timedelta(minutes=4)
    )
    assert repaired_b.bank_status == "SETTLED" and repaired_b.projection_status == "PROJECTED", (
        repaired_b
    )
    assert repaired_b.transaction_id is not None
    assert repaired_b.economic_posting_ids == pending_b.economic_posting_ids
    with Session(demo_engine) as session:
        account = session.get(Account, request_a.account_id)
        assert account is not None
        assert (
            account.balance_cents == cash_before + request_a.amount_cents + request_b.amount_cents
        )
        after = read_income_state(session, DEMO_USER_ID, NOW + timedelta(minutes=4)).ledger
        origins = {row.origin_transaction_id: row for row in after.origins}
        assert set(origins) == {
            *(row.origin_transaction_id for row in income_before.origins),
            repaired_a.transaction_id,
            repaired_b.transaction_id,
        }
        assert all(origins[row.origin_transaction_id] == row for row in income_before.origins)
        assert origins[repaired_a.transaction_id].amount_cents == request_a.amount_cents
        assert origins[repaired_b.transaction_id].amount_cents == request_b.amount_cents
        assert origins[repaired_a.transaction_id].occurred_at == request_a.occurred_at
        assert origins[repaired_b.transaction_id].occurred_at == request_b.occurred_at
        assert after.reservations == income_before.reservations
        assert sum(row.available_cents for row in after.fragments) == (
            sum(row.available_cents for row in income_before.fragments)
            + request_a.amount_cents
            + request_b.amount_cents
        )
        before_fragments = {row.fragment_id: row for row in income_before.fragments}
        assert all(
            row == before_fragments[row.fragment_id]
            for row in after.fragments
            if row.fragment_id in before_fragments
        )
        validate_bank_projection(session, DEMO_USER_ID, NOW + timedelta(minutes=4))
        verification = verify_audit_chain(session, DEMO_USER_ID)
        assert verification.status == "VALID", verification
    assert bank_originals() == originals_before and claims() == claims_before
    unchanged = database_snapshot(demo_engine)
    assert (
        ingest_external_fact(demo_engine, DEMO_USER_ID, request_a, NOW + timedelta(minutes=5))
        == repaired_a
    )
    assert (
        ingest_external_fact(demo_engine, DEMO_USER_ID, request_b, NOW + timedelta(minutes=6))
        == repaired_b
    )
    assert database_snapshot(demo_engine) == unchanged
