"""One actual generated-PG source proof candidate, only root executes this node."""

from uuid import UUID

import pytest
from app.db.models import Account, Goal
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.goal_release_provenance import GoalCashSourceProof
from app.services.action_contracts import ConfirmActionRequest, GoalIntent, PrepareActionRequest
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.goal_release_provenance import read_goal_cash_source_proof
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def read_original_proof(
    engine: Engine, goal_id: UUID, epoch_id: UUID, version: UUID
) -> GoalCashSourceProof:
    before = physical_snapshot(engine)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                income_before = read_income_state(session, DEMO_USER_ID, NOW)
                result = read_goal_cash_source_proof(
                    session, DEMO_USER_ID, goal_id, epoch_id, version, NOW
                )
                income_after = read_income_state(session, DEMO_USER_ID, NOW)
                assert income_before == income_after
    assert physical_snapshot(engine) == before
    assert not result.bank_authority and not result.funds_released
    assert result.assigned_income_decrease_cents == result.available_income_increase_cents == 0
    assert result.principal_change_cents == result.other_goal_change_cents == 0
    return result


def test_actual_allocation_original_cash_sources_preserve_assigned_identity_and_zero_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_text, model = confirmed_existing_goal(client, engine)
    goal_id, epoch_id = UUID(goal_text), UUID(model["expected_epoch_id"])
    with Session(engine) as session:
        goal = session.get(Goal, goal_id)
        source_id = session.scalar(
            select(Account.id)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        assert goal is not None and source_id is not None
        version = goal.policy_version_id
    initial = read_original_proof(engine, goal_id, epoch_id, version)
    assert initial.state == "VERIFIED_CASH_ONLY" and initial.exactly_attributed_goal_cash_cents == 0
    income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=source_id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="FULL304_cash_provenance_income",
            idempotency_key="FULL304_cash_provenance_income",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    prepared = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="FULL304_cash_provenance_allocation",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        NOW,
    )
    assert prepared.effect.income_uses and prepared.effect.amount_cents > 0
    assert (
        sum(row.amount_cents for row in prepared.effect.income_uses) == prepared.effect.amount_cents
    )
    if prepared.autonomy_level == "ASK_ONCE":
        confirm_action(
            engine,
            DEMO_USER_ID,
            prepared.action_id,
            ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
            NOW,
        )
    executed = execute_action(engine, DEMO_USER_ID, prepared.action_id, NOW)
    assert executed.status == "SUCCEEDED" and executed.receipt is not None
    proof = read_original_proof(engine, goal_id, epoch_id, version)
    assert proof.state == "VERIFIED_CASH_ONLY"
    assert proof.exactly_attributed_goal_cash_cents == prepared.effect.amount_cents
    assert proof.original_goal_principal_cents == 0
    assert (
        sum(row.source_goal_cash_remaining_cents or 0 for row in proof.sources)
        == prepared.effect.amount_cents
    )
    source_amounts = {row.fragment_id: row.amount_cents for row in prepared.effect.income_uses}
    assert {
        row.fragment_id: row.source_goal_cash_remaining_cents
        for row in proof.sources
        if row.source_goal_cash_remaining_cents
    } == source_amounts
    assert any(
        ref.table == "action_receipts" and ref.row_id == executed.receipt.receipt_id
        for ref in proof.original_operation_refs
    )
    stable = physical_snapshot(engine)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session, pytest.raises(PolicyLifecycleError):
                read_goal_cash_source_proof(
                    session, DEMO_USER_ID, goal_id, UUID(int=999), version, NOW
                )
    assert physical_snapshot(engine) == stable
    # Generated test DB only: the exact bank and original evidence remain intact.
    with Session(engine) as session, session.begin():
        actual = session.get(Goal, goal_id)
        assert actual is not None
        actual.allocated_cents += 1
    rejected = read_original_proof(engine, goal_id, epoch_id, version)
    assert rejected.state == "UNKNOWN" and rejected.exactly_attributed_goal_cash_cents is None
    assert all(row.source_goal_cash_remaining_cents is None for row in rejected.sources)
