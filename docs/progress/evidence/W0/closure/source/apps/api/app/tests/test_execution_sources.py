"""Execution targets must exist as bank facts, independently of policy strings."""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.db.models import Account, CreditCardBill, EvidenceItem, Transaction
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution_sources import load_execution_quote, resolve_payee_binding
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_recovery_service import recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_known_payee_uses_the_original_bank_counterparty_without_granting_authority(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        proof_id = resolve_payee_binding(
            session, DEMO_USER_ID, "synthetic-landlord-001", SEED_AS_OF
        )
        proof = session.get(EvidenceItem, proof_id)
        transaction = session.scalars(
            select(Transaction).where(Transaction.evidence_id == proof_id)
        ).one()
        assert proof is not None and proof.evidence_level == "BANK_CONFIRMED"
        assert proof.content["economic_role"] == "CONSUMPTION"
        assert transaction.counterparty_ref == "synthetic-landlord-001"
        assert transaction.direction == "DEBIT"


def test_unknown_foreign_income_and_internal_counterparties_are_not_payment_targets(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        for payee in ("new-person", "synthetic-employer-001", "synthetic-account:goal"):
            with pytest.raises(PolicyLifecycleError, match="收款"):
                resolve_payee_binding(session, DEMO_USER_ID, payee, SEED_AS_OF)
        with pytest.raises(PolicyLifecycleError, match="收款"):
            resolve_payee_binding(session, uuid4(), "synthetic-landlord-001", SEED_AS_OF)


def test_card_payee_is_bound_to_the_users_exact_bill_and_card_account(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        account = session.scalars(
            select(Account).where(Account.account_type == "CREDIT_CARD")
        ).one()
        bill = session.scalars(
            select(CreditCardBill).where(CreditCardBill.status == "UNPAID")
        ).one()
        assert (
            resolve_payee_binding(
                session, DEMO_USER_ID, f"credit-card:{account.id}", SEED_AS_OF, bill_id=bill.id
            )
            == bill.evidence_id
        )
        with pytest.raises(PolicyLifecycleError, match="收款"):
            resolve_payee_binding(
                session, DEMO_USER_ID, f"credit-card:{uuid4()}", SEED_AS_OF, bill_id=bill.id
            )


def test_contract_quote_remains_bound_to_original_time_during_confirmation(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    _, position_id, principal = recovery_fixture(demo_engine, delay=1)
    with Session(demo_engine) as session:
        first = load_execution_quote(session, DEMO_USER_ID, position_id, SEED_AS_OF)
        later = load_execution_quote(
            session,
            DEMO_USER_ID,
            position_id,
            SEED_AS_OF + timedelta(minutes=2),
            requested_at=SEED_AS_OF,
        )
        assert later == first
        assert first.principal_cents == first.net_cents == principal
        assert first.principal_available_at == SEED_AS_OF + timedelta(days=1)
        with pytest.raises(PolicyLifecycleError):
            load_execution_quote(
                session, DEMO_USER_ID, position_id, first.expires_at, requested_at=SEED_AS_OF
            )
        with pytest.raises(PolicyLifecycleError):
            load_execution_quote(session, uuid4(), position_id, SEED_AS_OF)


def test_explicit_cost_quote_preserves_price_and_rejects_rehashed_destination(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    _, position_id, principal = recovery_fixture(demo_engine)
    from app.services.recovery import preview_recovery

    with Session(demo_engine) as session, session.begin():
        action = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF).plan.steps[0]
        quote = action.quote.model_copy(
            update={"fee_cents": 100, "loss_cents": 200, "net_cents": principal - 300}
        )
        payload = {
            "simulation": True,
            "protocol": "recovery-quote-v1",
            "user_id": str(DEMO_USER_ID),
            "position_id": str(position_id),
            "destination_account_id": str(action.destination_account_id),
            "goal_id": None,
            "quote": quote.model_dump(mode="json"),
        }
        proof = EvidenceItem(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            created_at=SEED_AS_OF,
            evidence_level="BANK_CONFIRMED",
            source_type="SIMULATED_REDEMPTION_QUOTE",
            source_ref="301-price",
            content=payload,
            content_hash=configuration_hash(payload),
            valid_from=SEED_AS_OF,
            observed_at=SEED_AS_OF,
            status="VALID",
        )
        session.add(proof)
        session.flush()
        read = load_execution_quote(
            session,
            DEMO_USER_ID,
            position_id,
            SEED_AS_OF + timedelta(minutes=1),
            requested_at=SEED_AS_OF,
        )
        assert read.quote_id == quote.quote_id and read.net_cents == principal - 300
        assert read.evidence_ids == [proof.id]
        proof.content = {**payload, "destination_account_id": str(uuid4())}
        proof.content_hash = configuration_hash(proof.content)
        session.flush()
        with pytest.raises(PolicyLifecycleError):
            load_execution_quote(session, DEMO_USER_ID, position_id, SEED_AS_OF)
