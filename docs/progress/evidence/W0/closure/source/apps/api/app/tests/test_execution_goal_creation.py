"""Normal public goal creation must initialize only zero independent ownership."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import Account, BankOperation, Goal, SimulatedBankPosting
from app.services.action_contracts import GoalIntent, PrepareActionRequest
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import execute_action, prepare_action
from app.services.execution_exposure import refresh_execution_exposure
from app.services.goals import create_goal_projection
from app.services.income_ledger import read_income_state
from app.tests.test_boundary_service import boundary_engine, confirmed_policy, snapshot
from app.tests.test_execution_bank import source_ledger
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def public_zero_goal(
    engine: Engine, *, import_income: bool = True
) -> tuple[UUID, UUID, UUID, UUID]:
    with Session(engine) as session, session.begin():
        cash = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "goal_saving",
                "name": "Public goal",
                "target_cents": 100000,
                "deadline": "2026-12-31",
                "monthly_contribution": {"min_cents": 0, "target_cents": 10000, "max_cents": 20000},
            },
            SEED_AS_OF - timedelta(days=40),
        )
        if import_income:
            # A complete trusted income import precedes the public goal creation call.
            # It does not open any goal ledger or change existing account balances.
            source_ledger(session, available_cents=100000)
        else:
            # Native 401 seed already has its own bound FIFO proof and bank LOT anchors.
            read_income_state(session, DEMO_USER_ID, SEED_AS_OF)
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
        goal = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash.id, SEED_AS_OF
        ).goal
        return goal.id, cash.id, policy_id, version_id


def test_public_goal_creation_can_allocate_without_test_only_goal_opening(
    boundary_engine: Engine,
) -> None:
    goal_id, cash_id, policy_id, version_id = public_zero_goal(boundary_engine)
    with Session(boundary_engine) as session:
        before_cash = {r.id: r.balance_cents for r in session.scalars(select(Account))}
        before_income = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
    action = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="public-goal-allocation",
            intent=GoalIntent(
                kind="allocate_goal",
                goal_id=goal_id,
            ),
        ),
        SEED_AS_OF,
    )
    result = execute_action(boundary_engine, DEMO_USER_ID, action.action_id, SEED_AS_OF)
    assert result.status == "SUCCEEDED" and result.effect.amount_cents == 10000
    with Session(boundary_engine) as session:
        goal = session.get(Goal, goal_id)
        operation = session.get(BankOperation, action.action_id)
        assert goal is not None and goal.allocated_cents == 10000 and goal.account_id == cash_id
        assert operation is not None and operation.status == "SETTLED"
        rows = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.ledger_key.in_(
                        [f"GOAL_CASH:{goal_id}", f"GOAL_PRINCIPAL:{goal_id}"]
                    )
                )
            )
        )
        openings = [r for r in rows if r.entry_kind == "OPENING"]
        assert len(openings) == 2 and all(r.balance_after_cents == 0 for r in openings)
        assert sum(r.delta_cents for r in rows) == 10000
        assert before_cash == {r.id: r.balance_cents for r in session.scalars(select(Account))}
        after_income = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
        assert after_income.origins == before_income.origins
        assert sum(f.assigned_cents for f in after_income.fragments) == 10000
    before_replay = snapshot(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        repeated = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash_id, SEED_AS_OF
        )
        assert repeated.goal.allocated_cents == 10000
    assert snapshot(boundary_engine) == before_replay


def test_repeated_goal_creation_never_bootstraps_missing_bank_truth_from_owned_projection(
    boundary_engine: Engine,
) -> None:
    goal_id, cash_id, policy_id, version_id = public_zero_goal(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        # Simulate an inconsistent imported projection, outside normal application writes.
        session.execute(
            delete(SimulatedBankPosting).where(
                SimulatedBankPosting.ledger_key.in_(
                    [f"GOAL_CASH:{goal_id}", f"GOAL_PRINCIPAL:{goal_id}"]
                )
            )
        )
        goal = session.get(Goal, goal_id)
        assert goal is not None
        goal.allocated_cents = 25000
    before = snapshot(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        result = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash_id, SEED_AS_OF
        )
        assert result.goal.allocated_cents == 25000
        assert (
            list(
                session.scalars(
                    select(SimulatedBankPosting).where(
                        SimulatedBankPosting.ledger_key.in_(
                            [f"GOAL_CASH:{goal_id}", f"GOAL_PRINCIPAL:{goal_id}"]
                        )
                    )
                )
            )
            == []
        )
    assert snapshot(boundary_engine) == before
