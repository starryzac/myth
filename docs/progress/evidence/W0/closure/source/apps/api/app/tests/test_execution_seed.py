"""The execution schema resets only the reserved demo tenant without inventing income scope."""

from uuid import uuid4

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.income_ledger import LEDGER_SOURCE
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_execution_seed_is_repeatable_without_fabricated_income_qualification(
    demo_engine: Engine,
) -> None:
    first = seed_demo(demo_engine)
    assert first.seed_version == "mvp-301-v6"
    assert len(first.counts) == 20
    assert first.counts["bank_operations"] == first.counts["action_resource_reservations"] == 0
    assert first.counts["simulated_bank_postings"] == 7
    with Session(demo_engine) as session:
        assert (
            session.scalar(select(EvidenceItem).where(EvidenceItem.source_type == LEDGER_SOURCE))
            is None
        )
    before = database_snapshot(demo_engine)
    assert seed_demo(demo_engine) == first
    assert database_snapshot(demo_engine) == before


def test_reset_removes_execution_dependents_before_their_original_action(
    demo_engine: Engine,
) -> None:
    first = seed_demo(demo_engine)
    before = database_snapshot(demo_engine)
    with Session(demo_engine) as session, session.begin():
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        position = session.scalar(select(AssetPosition).order_by(AssetPosition.id))
        assert cash is not None and position is not None
        run = DecisionRun(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            idempotency_key="execution-reset-run",
            trigger_type="RESET_TEST",
            algorithm_version="fixture",
            as_of=SEED_AS_OF,
            input_snapshot={},
            snapshot_hash=configuration_hash({}),
        )
        session.add(run)
        session.flush()
        action = ActionPlan(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            decision_run_id=run.id,
            source_account_id=cash.id,
            position_id=position.id,
            product_id=position.product_id,
            action_type="ASSET_REDEEM",
            amount_cents=100,
            idempotency_key="execution-reset-action",
            status="SUBMITTED",
            request={},
            request_hash=configuration_hash({}),
        )
        session.add(action)
        session.flush()
        redemption = SimulatedBankRedemption(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            action_plan_id=action.id,
            position_id=position.id,
            destination_account_id=cash.id,
            product_id=position.product_id,
            principal_cents=100,
            idempotency_key="legacy-reset",
            request={},
            request_hash=configuration_hash({}),
            requested_at=SEED_AS_OF,
            available_at=SEED_AS_OF,
            status="ACCEPTED",
        )
        session.add(redemption)
        session.flush()
        operation = BankOperation(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            action_plan_id=action.id,
            legacy_redemption_id=redemption.id,
            operation_type="REDEEM_ASSET",
            business_key="reset-business",
            idempotency_key="execution-reset-bank",
            request={},
            request_hash=configuration_hash({}),
            requested_at=SEED_AS_OF,
            available_at=SEED_AS_OF,
            settled_at=SEED_AS_OF,
            status="SETTLED",
        )
        session.add(operation)
        session.flush()
        session.add(
            ActionResourceReservation(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                action_plan_id=action.id,
                resource_kind="POSITION",
                resource_key=str(position.id),
                amount_cents=100,
                status="RESERVED",
            )
        )
        for key, delta in ((f"CASH:{cash.id}", 100), (f"POSITION:{position.id}", -100)):
            opening = session.scalar(
                select(SimulatedBankPosting).where(SimulatedBankPosting.ledger_key == key)
            )
            assert opening is not None
            session.add(
                SimulatedBankPosting(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    ledger_key=key,
                    ledger_dimension="ECONOMIC",
                    ledger_metadata={},
                    account_id=opening.account_id,
                    position_id=opening.position_id,
                    operation_id=operation.id,
                    leg_ref=key,
                    previous_posting_id=opening.id,
                    sequence_number=2,
                    entry_kind="OPERATION",
                    balance_before_cents=opening.balance_after_cents,
                    delta_cents=delta,
                    balance_after_cents=opening.balance_after_cents + delta,
                    occurred_at=SEED_AS_OF,
                )
            )
    assert seed_demo(demo_engine) == first
    assert database_snapshot(demo_engine) == before
