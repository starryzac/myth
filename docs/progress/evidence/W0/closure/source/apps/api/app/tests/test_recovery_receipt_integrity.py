"""Real legacy recovery receipt verification, including immutable historical legs."""

from datetime import timedelta
from uuid import UUID, uuid5

import pytest
from app.db.models import (
    ActionReceipt,
    EvidenceItem,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery import run_recovery
from app.services.recovery_receipt_integrity import verify_recovery_receipt
from app.tests.test_boundary_service import boundary_engine, snapshot
from app.tests.test_recovery_service import mature_recovery_fixture, recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def settled(engine: Engine, *, mature: bool = False) -> tuple[UUID, UUID]:
    if mature:
        mature_recovery_fixture(engine)
    else:
        recovery_fixture(engine)
    result = run_recovery(engine, DEMO_USER_ID, "receipt-audit", SEED_AS_OF)
    assert len(result.actions) == 1
    with Session(engine) as session:
        receipt = session.scalars(
            select(ActionReceipt).where(ActionReceipt.action_plan_id == result.actions[0].action_id)
        ).one()
        request = session.scalars(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.action_plan_id == receipt.action_plan_id
            )
        ).one()
        return request.id, receipt.id


@pytest.mark.parametrize("mature", [False, True])
def test_real_recovery_and_contract_receipts_verify_without_writes(
    boundary_engine: Engine, mature: bool
) -> None:
    request_id, receipt_id = settled(boundary_engine, mature=mature)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        request = session.get(SimulatedBankRedemption, request_id)
        receipt = session.get(ActionReceipt, receipt_id)
        assert request is not None and receipt is not None
        verify_recovery_receipt(session, request, receipt, SEED_AS_OF)
        verify_recovery_receipt(session, request, receipt, SEED_AS_OF)
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize(
    "fault",
    [
        "executed",
        "fee",
        "loss",
        "posting_missing",
        "posting_duplicate",
        "posting_unrelated",
        "occurred",
        "future_observed",
        "transaction_amount",
        "evidence_rehash",
    ],
)
def test_corrupt_original_recovery_facts_fail_readonly_verification(
    boundary_engine: Engine, fault: str
) -> None:
    request_id, receipt_id = settled(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        receipt = session.get(ActionReceipt, receipt_id)
        request = session.get(SimulatedBankRedemption, request_id)
        assert receipt is not None and request is not None
        if fault == "executed":
            receipt.executed_cents += 1
        elif fault == "fee":
            receipt.fee_cents = 1
        elif fault == "loss":
            receipt.loss_cents = 1
        elif fault.startswith("posting_"):
            ids = receipt.response["posting_ids"]
            if fault == "posting_missing":
                ids = ids[:1]
            elif fault == "posting_duplicate":
                ids = ids + ids[:1]
            else:
                ids = [ids[0], str(uuid5(request_id, "unrelated"))]
            receipt.response = {**receipt.response, "posting_ids": ids}
        elif fault == "occurred":
            receipt.occurred_at += timedelta(seconds=1)
        elif fault == "future_observed":
            receipt.reconciled_at = SEED_AS_OF + timedelta(seconds=1)
        elif fault == "transaction_amount":
            transaction = session.get(Transaction, UUID(receipt.response["transaction_id"]))
            assert transaction is not None
            transaction.amount_cents += 1
        elif fault == "evidence_rehash":
            proof = session.get(EvidenceItem, uuid5(request_id, "transaction-evidence"))
            assert proof is not None
            proof.content = {**proof.content, "economic_role": "INCOME"}
            proof.content_hash = configuration_hash(proof.content)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        request = session.get(SimulatedBankRedemption, request_id)
        receipt = session.get(ActionReceipt, receipt_id)
        assert request is not None and receipt is not None
        with pytest.raises(PolicyLifecycleError) as raised:
            verify_recovery_receipt(session, request, receipt, SEED_AS_OF)
        assert raised.value.code == "BANK_RECONCILIATION_REQUIRED"
        assert raised.value.status_code == 409
    assert snapshot(boundary_engine) == before


@pytest.mark.parametrize("fault", ["request_rehash", "posting_leg"])
def test_unflushed_immutable_bank_objects_are_checked_without_autoflush(
    boundary_engine: Engine, fault: str
) -> None:
    request_id, receipt_id = settled(boundary_engine)
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        request = session.get(SimulatedBankRedemption, request_id)
        receipt = session.get(ActionReceipt, receipt_id)
        assert request is not None and receipt is not None
        if fault == "request_rehash":
            request.request = {**request.request, "principal_cents": request.principal_cents + 1}
            request.request_hash = configuration_hash(request.request)
        else:
            posting = session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.redemption_id == request_id,
                    SimulatedBankPosting.entry_kind == "CASH_CREDIT",
                )
            ).one()
            posting.leg_ref = "fabricated-cash-leg"
        # The database forbids these UPDATEs. Check caller-supplied dirty ORM facts
        # without disabling the guard or accidentally flushing from a read function.
        with pytest.raises(PolicyLifecycleError) as raised:
            verify_recovery_receipt(session, request, receipt, SEED_AS_OF)
        assert raised.value.code == "BANK_RECONCILIATION_REQUIRED"
    assert snapshot(boundary_engine) == before


def test_later_supersession_and_late_observation_preserve_original_receipt(
    boundary_engine: Engine,
) -> None:
    request_id, receipt_id = settled(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        proof = session.get(EvidenceItem, uuid5(request_id, "transaction-evidence"))
        assert proof is not None
        proof.status = "SUPERSEDED"
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        request = session.get(SimulatedBankRedemption, request_id)
        receipt = session.get(ActionReceipt, receipt_id)
        assert request is not None and receipt is not None
        verify_recovery_receipt(session, request, receipt, SEED_AS_OF + timedelta(days=1))
    assert snapshot(boundary_engine) == before


def test_t1_late_reconciliation_keeps_economic_and_observation_clocks_separate(
    boundary_engine: Engine,
) -> None:
    recovery_fixture(boundary_engine, delay=1)
    pending = run_recovery(boundary_engine, DEMO_USER_ID, "late-receipt", SEED_AS_OF)
    assert pending.actions[0].receipt_id is None
    observed = SEED_AS_OF + timedelta(days=2)
    done = run_recovery(boundary_engine, DEMO_USER_ID, "late-receipt", observed)
    assert done.actions[0].receipt_id is not None
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        receipt = session.get(ActionReceipt, done.actions[0].receipt_id)
        request = session.scalars(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.action_plan_id == done.actions[0].action_id
            )
        ).one()
        assert receipt is not None
        assert receipt.occurred_at == SEED_AS_OF + timedelta(days=1)
        assert receipt.reconciled_at == observed
        verify_recovery_receipt(session, request, receipt, observed)
        with pytest.raises(PolicyLifecycleError):
            verify_recovery_receipt(session, request, receipt, receipt.occurred_at)
    assert snapshot(boundary_engine) == before
