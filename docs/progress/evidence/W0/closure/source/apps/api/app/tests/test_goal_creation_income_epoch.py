"""A later zero goal keeps complete native income provenance without granting old income."""

import json
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.db.models import (
    Account,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.income_ledger import LEDGER_SOURCE
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import verify_audit_chain
from app.services.boundary import CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.goal_allocation import preview_goal_allocation
from app.services.goals import create_goal_projection
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import validate_bank_projection, validate_recovery_exposure
from app.tests.test_boundary_service import confirmed_policy, seed_legacy_income_fixture
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = SEED_AS_OF + timedelta(hours=9)
FINANCIAL_MODELS = (
    Account,
    AssetPosition,
    Goal,
    Transaction,
    BankOperation,
    SimulatedBankRedemption,
    SimulatedBankPosting,
    ActionResourceReservation,
    ExternalBankFact,
)


def _financial_rows(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    with Session(engine) as session:
        return {
            model.__tablename__: [
                dict(row)
                for row in session.execute(
                    select(model.__table__).where(model.user_id == DEMO_USER_ID).order_by(model.id)
                ).mappings()
            ]
            for model in FINANCIAL_MODELS
        }


def _goal_request(
    session: Session,
    name: str = "Later native zero goal",
    *,
    now: datetime = NOW,
    minimum_cents: int = 0,
) -> tuple[UUID, UUID, UUID]:
    account = session.scalars(
        select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
    ).one()
    policy_id, version_id = confirmed_policy(
        session,
        {
            "type": "goal_saving",
            "name": name,
            "target_cents": 100000,
            "deadline": "2027-10-01",
            "monthly_contribution": {
                "min_cents": minimum_cents,
                "target_cents": 10000,
                "max_cents": 10000,
            },
        },
        now,
    )
    return policy_id, version_id, account.id


def _income_and_goal_proofs(engine: Engine) -> list[dict[str, Any]]:
    with Session(engine) as session:
        return [
            dict(row)
            for row in session.execute(
                select(EvidenceItem.__table__)
                .where(
                    EvidenceItem.user_id == DEMO_USER_ID,
                    EvidenceItem.source_type.in_(
                        [LEDGER_SOURCE, OWNERSHIP_SOURCE, CONTRIBUTION_SOURCE]
                    ),
                )
                .order_by(EvidenceItem.id)
            ).mappings()
        ]


def _assert_old_financial_rows_unchanged(
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
    goal_id: UUID,
) -> None:
    for table, originals in before.items():
        indexed = {row["id"]: row for row in after[table]}
        assert all(indexed.get(row["id"]) == row for row in originals), table
        extras = [row for row in after[table] if row["id"] not in {r["id"] for r in originals}]
        if table == Goal.__tablename__:
            assert len(extras) == 1 and extras[0]["id"] == goal_id
            assert extras[0]["allocated_cents"] == 0
        elif table == SimulatedBankPosting.__tablename__:
            assert {row["ledger_key"] for row in extras} == {
                f"GOAL_CASH:{goal_id}",
                f"GOAL_PRINCIPAL:{goal_id}",
            }
            assert len(extras) == 2
            assert all(
                row["entry_kind"] == "OPENING"
                and row["delta_cents"] == 0
                and row["balance_before_cents"] == row["balance_after_cents"] == 0
                for row in extras
            )
        else:
            assert extras == [], table


def test_later_native_zero_goal_keeps_complete_income_without_authorizing_old_income(
    demo_engine: Engine,
) -> None:
    started = perf_counter()
    seed_demo(demo_engine)
    print(f"ACTUAL_PHASE native_seed {perf_counter() - started:.3f}s")
    with Session(demo_engine) as session, session.begin():
        original_state = read_income_state(session, DEMO_USER_ID, NOW)
        original = session.get(EvidenceItem, original_state.evidence_id)
        assert original is not None and original_state.ledger.as_of == SEED_AS_OF
        original_content = json.loads(json.dumps(original.content))
        original_hash, original_id = original.content_hash, original.id
        policy_id, version_id, cash_id = _goal_request(session)
    before = _financial_rows(demo_engine)
    started = perf_counter()
    with Session(demo_engine) as session, session.begin():
        created = create_goal_projection(session, DEMO_USER_ID, policy_id, version_id, cash_id, NOW)
        goal_id = created.goal.id
    print(f"ACTUAL_PHASE later_zero_goal {perf_counter() - started:.3f}s")
    before_reads = database_snapshot(demo_engine)
    started = perf_counter()
    with Session(demo_engine) as session:
        preview = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
        assert preview.allocation.status == "READY", preview.source_issues
        assert preview.source_issues == []
        assert preview.allocation.eligible_new_funds_cents == 0
        assert preview.allocation.suggested_cents == 0
        assert preview.allocation.lot_allocations == []
        state = read_income_state(session, DEMO_USER_ID, NOW)
        assert state.ledger.as_of == NOW
        assert state.ledger.origins == original_state.ledger.origins
        assert state.ledger.fragments == original_state.ledger.fragments
        assert state.ledger.reservations == original_state.ledger.reservations
        predecessor = session.get(EvidenceItem, original_id)
        successor = session.get(EvidenceItem, state.evidence_id)
        assert predecessor is not None and successor is not None
        assert predecessor.content == original_content and predecessor.content_hash == original_hash
        assert predecessor.status == "SUPERSEDED"
        expected = {**original_content, "as_of": NOW.isoformat()}
        digest = configuration_hash(expected)
        identity = uuid5(goal_id, f"evidence:{original_id}:{digest}")
        assert successor.content == expected and successor.content_hash == digest
        assert successor.id == identity and successor.source_ref == f"bank-projection:{identity}"
        assert successor.source_type == LEDGER_SOURCE and successor.supersedes_id == original_id
        assert successor.created_at == successor.observed_at == successor.valid_from == NOW
        assert successor.status == "VALID"
        validate_bank_projection(session, DEMO_USER_ID, NOW)
        validate_recovery_exposure(session, DEMO_USER_ID, NOW)
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    print(f"ACTUAL_PHASE complete_preview_and_verifiers {perf_counter() - started:.3f}s")
    assert database_snapshot(demo_engine) == before_reads
    _assert_old_financial_rows_unchanged(before, _financial_rows(demo_engine), goal_id)
    with Session(demo_engine) as session, session.begin():
        repeated = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash_id, NOW + timedelta(minutes=1)
        )
        assert repeated.goal.id == goal_id and repeated.goal.allocated_cents == 0
    with Session(demo_engine) as session:
        assert preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW) == preview
    assert database_snapshot(demo_engine) == before_reads


def test_second_later_zero_goal_preserves_both_goals_and_all_income_economic_fields(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        policy_id, version_id, cash_id = _goal_request(session, "First zero goal")
        first = create_goal_projection(session, DEMO_USER_ID, policy_id, version_id, cash_id, NOW)
        first_id = first.goal.id
    old_proofs = [r for r in _income_and_goal_proofs(demo_engine) if r["status"] == "VALID"]
    assert len(old_proofs) == 3
    before = _financial_rows(demo_engine)
    later = NOW + timedelta(hours=1)
    with Session(demo_engine) as session, session.begin():
        policy_id, version_id, cash_id = _goal_request(session, "Second zero goal", now=later)
        second = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash_id, later
        )
        second_id = second.goal.id
    _assert_old_financial_rows_unchanged(before, _financial_rows(demo_engine), second_id)
    before_reads = database_snapshot(demo_engine)
    with Session(demo_engine) as session:
        for original in old_proofs:
            predecessor = session.get(EvidenceItem, original["id"])
            assert predecessor is not None and predecessor.status == "SUPERSEDED"
            assert all(
                getattr(predecessor, field) == value
                for field, value in original.items()
                if field != "status"
            )
            successor = session.scalars(
                select(EvidenceItem).where(EvidenceItem.supersedes_id == predecessor.id)
            ).one()
            expected = {**original["content"], "as_of": later.isoformat()}
            digest = configuration_hash(expected)
            assert successor.content == expected and successor.content_hash == digest
            assert successor.id == uuid5(second_id, f"evidence:{predecessor.id}:{digest}")
            assert successor.source_ref == f"bank-projection:{successor.id}"
            assert successor.observed_at == successor.valid_from == successor.created_at == later
            assert successor.status == "VALID"
        for goal_id in [first_id, second_id]:
            preview = preview_goal_allocation(session, DEMO_USER_ID, goal_id, later)
            assert preview.allocation.status == "READY", preview.source_issues
            assert preview.source_issues == []
            assert preview.allocation.eligible_new_funds_cents == 0
            assert preview.allocation.suggested_cents == 0
        assert read_income_state(session, DEMO_USER_ID, later).ledger.as_of == later
        validate_bank_projection(session, DEMO_USER_ID, later)
        validate_recovery_exposure(session, DEMO_USER_ID, later)
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    assert database_snapshot(demo_engine) == before_reads


@pytest.mark.parametrize("source_state", ["legacy_missing", "native_bad_hash"])
def test_zero_goal_never_bootstraps_or_repairs_missing_or_invalid_income(
    demo_engine: Engine, source_state: str
) -> None:
    if source_state == "legacy_missing":
        seed_legacy_income_fixture(demo_engine)
    else:
        seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        policy_id, version_id, cash_id = _goal_request(session)
        if source_state == "native_bad_hash":
            original = session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == DEMO_USER_ID,
                    EvidenceItem.source_type == LEDGER_SOURCE,
                    EvidenceItem.status == "VALID",
                )
            ).one()
            original.content_hash = "0" * 64
    before = database_snapshot(demo_engine)
    old_proofs = _income_and_goal_proofs(demo_engine)
    before_financial = _financial_rows(demo_engine)
    goal_id: UUID | None = None
    try:
        with Session(demo_engine) as session, session.begin():
            created = create_goal_projection(
                session, DEMO_USER_ID, policy_id, version_id, cash_id, NOW
            )
            goal_id = created.goal.id
    except PolicyLifecycleError:
        assert source_state == "native_bad_hash"
        assert database_snapshot(demo_engine) == before
    else:
        assert goal_id is not None
        _assert_old_financial_rows_unchanged(
            before_financial, _financial_rows(demo_engine), goal_id
        )
        before_reads = database_snapshot(demo_engine)
        with Session(demo_engine) as session:
            preview = preview_goal_allocation(session, DEMO_USER_ID, goal_id, NOW)
            assert preview.allocation.status == "INSUFFICIENT_EVIDENCE"
            assert preview.allocation.eligible_new_funds_cents is None
            assert preview.allocation.suggested_cents is None
            assert preview.allocation.lot_allocations == []
            assert any(issue.code.endswith("NEW_FUNDS_LEDGER") for issue in preview.source_issues)
        assert database_snapshot(demo_engine) == before_reads
    after_proofs = _income_and_goal_proofs(demo_engine)
    assert [r for r in after_proofs if r["source_type"] == LEDGER_SOURCE] == [
        r for r in old_proofs if r["source_type"] == LEDGER_SOURCE
    ]
    assert all(row in after_proofs for row in old_proofs)
    if source_state == "legacy_missing":
        assert goal_id is not None


def test_next_month_goal_creation_does_not_invent_current_period_or_rewrite_old_proofs(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        policy_id, version_id, cash_id = _goal_request(
            session, "October minimum goal", now=SEED_AS_OF, minimum_cents=1000
        )
        first = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash_id, SEED_AS_OF
        )
        first_id = first.goal.id
    old_proofs = _income_and_goal_proofs(demo_engine)
    before_financial = _financial_rows(demo_engine)
    later = NOW + timedelta(days=31)
    with Session(demo_engine) as session, session.begin():
        policy_id, version_id, cash_id = _goal_request(session, "November zero goal", now=later)
        second = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash_id, later
        )
        second_id = second.goal.id
    _assert_old_financial_rows_unchanged(before_financial, _financial_rows(demo_engine), second_id)
    after_proofs = _income_and_goal_proofs(demo_engine)
    assert all(row in after_proofs for row in old_proofs)
    assert [r for r in after_proofs if r["source_type"] == LEDGER_SOURCE] == [
        r for r in old_proofs if r["source_type"] == LEDGER_SOURCE
    ]
    assert not any(
        r["source_type"] == CONTRIBUTION_SOURCE
        and r["content"]["goal_id"] == str(first_id)
        and r["content"]["period"] == "2026-11"
        for r in after_proofs
    )
    before_reads = database_snapshot(demo_engine)
    with Session(demo_engine) as session:
        preview = preview_goal_allocation(session, DEMO_USER_ID, first_id, later)
        assert preview.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert any(issue.code == "INVALID_GOAL_MONTH_SOURCE" for issue in preview.source_issues)
    assert database_snapshot(demo_engine) == before_reads
