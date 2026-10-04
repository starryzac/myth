"""Principal settlement preserves a complete, already consumed/reserved income ledger."""

import json
from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import Account, EvidenceItem, Goal, Transaction
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import CONTRIBUTION_SOURCE
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.goal_allocation import (
    LEDGER_PROTOCOL,
    LEDGER_SOURCE,
    LedgerPayload,
    preview_goal_allocation,
)
from app.services.recovery import run_recovery
from app.tests.test_asset_allocation_service import exposure_statement
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import goal_fixture, snapshot
from app.tests.test_recovery_service import mature_recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_principal_return_preserves_nonempty_spent_assigned_reserved_and_available_income(
    boundary_engine: Engine,
) -> None:
    _, _, principal = mature_recovery_fixture(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True)
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        cash_id, cash_before = cash.id, cash.balance_cents
        lots: list[dict[str, Any]] = []
        for transaction in session.scalars(
            select(Transaction).where(Transaction.direction == "CREDIT").order_by(Transaction.id)
        ):
            origin = session.get(EvidenceItem, transaction.evidence_id)
            assert origin is not None
            if origin.content["economic_role"] != "INCOME":
                continue
            retained = not lots
            lots.append(
                {
                    "transaction_id": str(transaction.id),
                    "account_id": str(transaction.account_id),
                    "bank_evidence_id": str(origin.id),
                    "bank_evidence_hash": origin.content_hash,
                    "original_cents": transaction.amount_cents,
                    "prior_unspent_cents": 130000 if retained else 0,
                    "spent_cents": transaction.amount_cents - 140000
                    if retained
                    else transaction.amount_cents,
                    "assigned_cents": 10000 if retained else 0,
                    "reserved_cents": 30000 if retained else 0,
                    "available_cents": 100000 if retained else 0,
                }
            )
        assert lots and lots[0]["spent_cents"] > 0
        payload = {
            "simulation": True,
            "protocol": LEDGER_PROTOCOL,
            "user_id": str(DEMO_USER_ID),
            "complete": True,
            "as_of": SEED_AS_OF.isoformat(),
            "scope_account_ids": [str(cash_id)],
            "lots": lots,
        }
        ledger_id = uuid4()
        ledger = EvidenceItem(
            id=ledger_id,
            user_id=DEMO_USER_ID,
            evidence_level="BANK_CONFIRMED",
            source_type=LEDGER_SOURCE,
            source_ref="known-consumed-income-before-recovery",
            content=payload,
            content_hash=configuration_hash(payload),
            valid_from=SEED_AS_OF,
            observed_at=SEED_AS_OF,
            status="VALID",
        )
        session.add(ledger)
        exposure = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE")
        )
        assert exposure is not None
        exposure_statement(session, exposure.content["settlements"])
        original_payload = deepcopy(payload)
    now = SEED_AS_OF + timedelta(hours=1)
    result = run_recovery(boundary_engine, DEMO_USER_ID, "preserve-income-lots", now)
    assert result.status == "PARTIAL_RECOVERY"
    assert result.actual_boundary.minimum_margin_cents == -40000
    assert len(result.actions) == 1
    assert result.actions[0].bank_status == "SETTLED"
    assert result.actions[0].receipt_id is not None
    with Session(boundary_engine) as session:
        current = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == LEDGER_SOURCE, EvidenceItem.status == "VALID"
            )
        ).one()
        assert current.id != ledger_id and current.supersedes_id == ledger_id
        assert current.content == {**original_payload, "as_of": now.isoformat()}
        assert current.content_hash == configuration_hash(current.content)
        validated = LedgerPayload.model_validate_json(json.dumps(current.content))
        assert sum(lot.available_cents for lot in validated.lots) == 100000
        assert sum(lot.reserved_cents for lot in validated.lots) == 30000
        assert sum(lot.assigned_cents for lot in validated.lots) == 10000
        old = session.get(EvidenceItem, ledger_id)
        assert old is not None and old.status == "SUPERSEDED"
        assert old.content == original_payload
        cash_after = session.get(Account, cash_id)
        assert cash_after is not None and cash_after.balance_cents == cash_before + principal
        goal = session.get(Goal, goal_id)
        assert goal is not None and goal.allocated_cents == 160000
        contribution = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == CONTRIBUTION_SOURCE,
                EvidenceItem.status == "VALID",
            )
        ).one()
        assert contribution.content["contributed_cents"] == 40000
        assert contribution.content["as_of"] == now.isoformat()
        returned = session.scalars(
            select(Transaction).where(Transaction.category == "principal_return")
        ).one()
        assert returned.id not in {UUID(lot["transaction_id"]) for lot in current.content["lots"]}
        return_proof = session.get(EvidenceItem, returned.evidence_id)
        assert return_proof is not None
        assert return_proof.content["economic_role"] == "PRINCIPAL_RETURN"
        allocation = preview_goal_allocation(session, DEMO_USER_ID, goal_id, now)
        assert allocation.source_issues == []
    before_retry = snapshot(boundary_engine)
    run_recovery(boundary_engine, DEMO_USER_ID, "preserve-income-lots", now)
    assert snapshot(boundary_engine) == before_retry
