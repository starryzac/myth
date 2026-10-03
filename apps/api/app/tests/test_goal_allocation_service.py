"""Verified income-lot previews, using real PostgreSQL and immutable financial snapshots."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import Account, EvidenceItem, Goal, Transaction
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE, compute_user_boundary
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.goal_allocation import LEDGER_PROTOCOL, LEDGER_SOURCE, preview_goal_allocation
from app.services.policy_lifecycle import PolicyLifecycleError, suspend_policy
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import goal_fixture, snapshot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = SEED_AS_OF + timedelta(hours=1)


@pytest.fixture
def allocation_setup(boundary_engine: Engine) -> tuple[Engine, UUID]:
    with Session(boundary_engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True)
    return boundary_engine, goal_id


def test_missing_complete_income_ledger_does_not_invent_zero_new_funds(
    allocation_setup: tuple[Engine, UUID],
) -> None:
    engine, goal_id = allocation_setup
    before = snapshot(engine)
    with Session(engine) as session:
        result = preview_goal_allocation(session, DEMO_USER_ID, goal_id, SEED_AS_OF)
        assert result.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert result.allocation.suggested_cents is None
        assert any(item.code == "MISSING_NEW_FUNDS_LEDGER" for item in result.source_issues)
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    "case",
    [
        "incomplete",
        "bad_hash",
        "wrong_owner",
        "future_statement",
        "old_epoch",
        "duplicate",
        "missing_old_lot",
        "duplicate_origin",
        "wrong_account",
        "future_transaction",
        "bank_hash",
        "category_spoof",
        "original_amount",
        "prior_unspent",
        "unbalanced",
        "negative_reserved",
        "fake_bank_hash",
        "scope",
        "over_account_cash",
    ],
)
def test_only_complete_conserved_and_bound_lot_statements_are_usable(
    allocation_setup: tuple[Engine, UUID],
    case: str,
) -> None:
    engine, goal_id = allocation_setup
    with Session(engine) as session, session.begin():
        transaction_id, ledger_id = income_ledger(session)
        session.flush()
        ledger = session.get(EvidenceItem, ledger_id)
        transaction = session.get(Transaction, transaction_id)
        assert ledger is not None and transaction is not None
        bank = session.get(EvidenceItem, transaction.evidence_id)
        assert bank is not None
        content = {**ledger.content, "lots": [dict(item) for item in ledger.content["lots"]]}
        lot = next(
            item for item in content["lots"] if item["transaction_id"] == str(transaction_id)
        )
        if case == "incomplete":
            content["complete"] = False
        elif case == "bad_hash":
            content["as_of"] = SEED_AS_OF.isoformat()
        elif case == "wrong_owner":
            content["user_id"] = str(uuid4())
        elif case == "future_statement":
            ledger.observed_at = NOW + timedelta(seconds=1)
        elif case == "old_epoch":
            content["as_of"] = SEED_AS_OF.isoformat()
        elif case == "duplicate":
            session.add(
                EvidenceItem(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    evidence_level="BANK_CONFIRMED",
                    source_type=LEDGER_SOURCE,
                    source_ref="duplicate-ledger",
                    content=content,
                    content_hash=configuration_hash(content),
                    valid_from=NOW,
                    observed_at=NOW,
                    status="VALID",
                )
            )
        elif case == "missing_old_lot":
            content["lots"] = [lot]
        elif case == "duplicate_origin":
            content["lots"].append(dict(lot))
        elif case == "wrong_account":
            account = session.scalar(select(Account).where(Account.account_type == "GOAL"))
            assert account is not None
            lot["account_id"] = str(account.id)
        elif case == "future_transaction":
            transaction.occurred_at = NOW + timedelta(seconds=1)
        elif case == "bank_hash":
            bank.content = {**bank.content, "modified": True}
        elif case == "category_spoof":
            transaction.category = "salary"
            bank.content = {**bank.content, "economic_role": "INTERNAL_TRANSFER"}
            bank.content_hash = configuration_hash(bank.content)
            lot["bank_evidence_hash"] = bank.content_hash
        elif case == "original_amount":
            lot["original_cents"] += 1
            lot["spent_cents"] += 1
        elif case == "prior_unspent":
            lot["prior_unspent_cents"] += 1
        elif case == "unbalanced":
            lot["available_cents"] += 1
        elif case == "negative_reserved":
            lot["reserved_cents"] = -1
        elif case == "fake_bank_hash":
            lot["bank_evidence_hash"] = "a" * 64
        elif case == "scope":
            content["scope_account_ids"] = []
        else:
            cash = session.get(Account, transaction.account_id)
            assert cash is not None
            cash.balance_cents = 100000
            proof = session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                    EvidenceItem.content["account_id"].as_string() == str(cash.id),
                )
            )
            assert proof is not None
            proof.content = {**proof.content, "balance_cents": 100000}
            proof.content_hash = configuration_hash(proof.content)
        ledger.content = content
        if case != "bad_hash":
            ledger.content_hash = configuration_hash(content)
    before = snapshot(engine)
    with Session(engine) as session:
        result = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert result.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert result.allocation.suggested_cents is None
        assert any(
            item.code in {"INVALID_NEW_FUNDS_LEDGER", "CONFLICTING_NEW_FUNDS_LEDGER"}
            for item in result.source_issues
        )
    assert snapshot(engine) == before


def test_partial_consumption_retains_unspent_part_but_never_reuses_spent_amount(
    allocation_setup: tuple[Engine, UUID],
) -> None:
    engine, goal_id = allocation_setup
    with Session(engine) as session, session.begin():
        _, ledger_id = income_ledger(session, available=100000)
    with Session(engine) as session:
        first = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert first.allocation.suggested_cents == 100000
    with Session(engine) as session, session.begin():
        ledger = session.get(EvidenceItem, ledger_id)
        assert ledger is not None
        content = {**ledger.content, "lots": [dict(item) for item in ledger.content["lots"]]}
        fresh = next(item for item in content["lots"] if item["available_cents"] == 100000)
        fresh["spent_cents"] += 30000
        fresh["available_cents"] -= 30000
        fresh["prior_unspent_cents"] -= 30000
        ledger.content = content
        ledger.content_hash = configuration_hash(content)
    before = snapshot(engine)
    with Session(engine) as session:
        changed = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert changed.allocation.suggested_cents == 70000
        assert changed.allocation.allocation_hash != first.allocation.allocation_hash
    assert snapshot(engine) == before


def test_old_income_is_not_requalified_by_a_new_current_policy_version(
    allocation_setup: tuple[Engine, UUID],
) -> None:
    engine, goal_id = allocation_setup
    with Session(engine) as session, session.begin():
        _, ledger_id = income_ledger(session, available=0)
        session.flush()
        ledger = session.get(EvidenceItem, ledger_id)
        assert ledger is not None
        content = {**ledger.content, "lots": [dict(item) for item in ledger.content["lots"]]}
        old = next(item for item in content["lots"] if item["original_cents"] != 200000)
        old["available_cents"] = old["prior_unspent_cents"] = 100000
        old["spent_cents"] -= 100000
        ledger.content = content
        ledger.content_hash = configuration_hash(content)
    with Session(engine) as session:
        result = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert result.allocation.status == "MINIMUM_SHORTFALL"
        assert result.allocation.eligible_new_funds_cents == 0
        assert result.allocation.suggested_cents == 0


def test_inactive_and_other_user_goals_never_produce_candidates(
    allocation_setup: tuple[Engine, UUID],
) -> None:
    engine, goal_id = allocation_setup
    with Session(engine) as session, session.begin():
        income_ledger(session)
        goal = session.get(Goal, goal_id)
        assert goal is not None
        suspend_policy(session, DEMO_USER_ID, goal.policy_id, goal.policy_version_id, NOW)
    with Session(engine) as session:
        result = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert result.allocation.status == "INACTIVE_POLICY"
        assert result.allocation.suggested_cents is None
        with pytest.raises(PolicyLifecycleError) as error:
            preview_goal_allocation(session, uuid4(), goal_id, NOW)
        assert error.value.status_code == 404


def income_ledger(session: Session, *, available: int = 140000) -> tuple[UUID, UUID]:
    cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
    assert cash is not None
    transaction_id, bank_id = uuid4(), uuid4()
    cash.balance_cents += 200000
    cash.observed_at = NOW
    payload: dict[str, Any] = {
        "simulation": True,
        "transaction_id": str(transaction_id),
        "account_id": str(cash.id),
        "direction": "CREDIT",
        "amount_cents": 200000,
        "balance_after_cents": cash.balance_cents,
        "occurred_at": NOW.isoformat(),
        "counterparty_ref": "synthetic-employer",
        "economic_role": "INCOME",
    }
    bank = EvidenceItem(
        id=bank_id,
        user_id=DEMO_USER_ID,
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_BANK_TRANSACTION",
        source_ref=f"new-income:{transaction_id}",
        content=payload,
        content_hash=configuration_hash(payload),
        valid_from=NOW,
        observed_at=NOW,
        status="VALID",
    )
    session.add(bank)
    session.flush()
    session.add(
        Transaction(
            id=transaction_id,
            user_id=DEMO_USER_ID,
            account_id=cash.id,
            evidence_id=bank_id,
            source_ref=bank.source_ref,
            direction="CREDIT",
            amount_cents=200000,
            balance_after_cents=cash.balance_cents,
            category="salary",
            category_confirmed=False,
            counterparty_ref="synthetic-employer",
            occurred_at=NOW,
            observed_at=NOW,
        )
    )
    proof = session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
            EvidenceItem.content["account_id"].as_string() == str(cash.id),
        )
    )
    assert proof is not None
    proof.content = {**proof.content, "balance_cents": cash.balance_cents, "as_of": NOW.isoformat()}
    proof.content_hash = configuration_hash(proof.content)
    proof.observed_at = NOW
    for proof in session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.source_type.in_([OWNERSHIP_SOURCE, CONTRIBUTION_SOURCE])
        )
    ):
        proof.content = {**proof.content, "as_of": NOW.isoformat()}
        proof.content_hash = configuration_hash(proof.content)
        proof.observed_at = NOW
    session.flush()
    lots = []
    for transaction in session.scalars(
        select(Transaction).where(Transaction.direction == "CREDIT")
    ):
        origin = session.get(EvidenceItem, transaction.evidence_id)
        assert origin is not None
        if origin.content["economic_role"] != "INCOME":
            continue
        fresh = transaction.id == transaction_id
        lots.append(
            {
                "transaction_id": str(transaction.id),
                "account_id": str(transaction.account_id),
                "bank_evidence_id": str(origin.id),
                "bank_evidence_hash": origin.content_hash,
                "original_cents": transaction.amount_cents,
                "prior_unspent_cents": available + 30000 if fresh else 0,
                "spent_cents": 200000 - available - 40000 if fresh else transaction.amount_cents,
                "assigned_cents": 10000 if fresh else 0,
                "reserved_cents": 30000 if fresh else 0,
                "available_cents": available if fresh else 0,
            }
        )
    content = {
        "simulation": True,
        "protocol": LEDGER_PROTOCOL,
        "user_id": str(DEMO_USER_ID),
        "complete": True,
        "as_of": NOW.isoformat(),
        "scope_account_ids": [str(cash.id)],
        "lots": sorted(lots, key=lambda item: str(item["transaction_id"])),
    }
    ledger = EvidenceItem(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        evidence_level="BANK_CONFIRMED",
        source_type=LEDGER_SOURCE,
        source_ref="complete-income-ledger",
        content=content,
        content_hash=configuration_hash(content),
        valid_from=NOW,
        observed_at=NOW,
        status="VALID",
    )
    session.add(ledger)
    return transaction_id, ledger.id


def test_preview_uses_only_proved_remaining_lot_and_is_repeatable_without_writes(
    allocation_setup: tuple[Engine, UUID],
) -> None:
    engine, goal_id = allocation_setup
    with Session(engine) as session, session.begin():
        transaction_id, _ = income_ledger(session)
    before = snapshot(engine)
    with Session(engine) as session:
        result = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert result.allocation.status == "READY"
        assert result.allocation.eligible_new_funds_cents == 140000
        assert result.allocation.suggested_cents == 110000
        assert result.allocation.lot_allocations[0].origin_transaction_id == transaction_id
        assert result.allocation.lot_allocations[0].remaining_available_cents == 30000
        assert result == preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
    assert snapshot(engine) == before


@pytest.mark.parametrize("role", ["CONSUMPTION", "INTERNAL_TRANSFER"])
def test_known_cash_outflow_after_ledger_epoch_blocks_old_available_amount(
    allocation_setup: tuple[Engine, UUID],
    role: str,
) -> None:
    engine, goal_id = allocation_setup
    with Session(engine) as session, session.begin():
        income_ledger(session)
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        identifier, proof_id = uuid4(), uuid4()
        occurred, observed = NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)
        content = {
            "simulation": True,
            "transaction_id": str(identifier),
            "account_id": str(cash.id),
            "direction": "DEBIT",
            "amount_cents": 5000,
            "balance_after_cents": cash.balance_cents - 5000,
            "occurred_at": occurred.isoformat(),
            "counterparty_ref": "actual-outflow",
            "economic_role": role,
        }
        session.add(
            EvidenceItem(
                id=proof_id,
                user_id=DEMO_USER_ID,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_TRANSACTION",
                source_ref=f"outflow:{identifier}",
                content=content,
                content_hash=configuration_hash(content),
                valid_from=observed,
                observed_at=observed,
                status="VALID",
            )
        )
        session.flush()
        session.add(
            Transaction(
                id=identifier,
                user_id=DEMO_USER_ID,
                account_id=cash.id,
                evidence_id=proof_id,
                source_ref=f"outflow:{identifier}",
                direction="DEBIT",
                amount_cents=5000,
                balance_after_cents=cash.balance_cents - 5000,
                category="food",
                counterparty_ref="actual-outflow",
                occurred_at=occurred,
                observed_at=observed,
            )
        )
    before = snapshot(engine)
    with Session(engine) as session:
        result = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW + timedelta(seconds=3))
        assert result.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert result.allocation.suggested_cents is None
        assert any(item.code == "INVALID_NEW_FUNDS_LEDGER" for item in result.source_issues)
    assert snapshot(engine) == before


def test_inactive_future_goal_keeps_all_confirmed_protection_in_baseline(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True, starts_on="2026-11-01")
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        expected = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        preview = preview_goal_allocation(session, DEMO_USER_ID, goal_id, SEED_AS_OF)
        assert expected.boundary.safe_idle_cents == 2957400
        assert preview.allocation.baseline_boundary == expected.boundary
        assert preview.allocation.status == "INACTIVE_POLICY"
        assert preview.allocation.suggested_cents is None
    assert snapshot(boundary_engine) == before
