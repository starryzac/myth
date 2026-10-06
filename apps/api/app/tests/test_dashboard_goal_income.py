"""A native new-income goal action remains readable before any financial execution."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.models import Account
from app.domain.demo_identity import DEMO_USER_ID
from app.services.action_contracts import GoalIntent, PrepareActionRequest
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.dashboard import get_dashboard
from app.services.demo_seed import seed_demo
from app.services.execution import prepare_action
from app.services.goals import create_goal_projection
from app.services.income_ledger import income_lots_for_action, read_income_state
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import ScenarioRPC
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 5, 4, 21, tzinfo=UTC)


def test_native_goal_income_prepared_action_dashboard_is_complete_and_read_only(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        policy, version = confirmed_policy(
            session,
            {
                "type": "goal_saving",
                "name": "Native new car goal",
                "target_cents": 3_000_000,
                "deadline": "2027-10-01",
                "monthly_contribution": {
                    "min_cents": 180_001,
                    "target_cents": 200_000,
                    "max_cents": 250_000,
                },
            },
            NOW,
        )
        account = session.scalars(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "GOAL")
        ).one()
        goal = create_goal_projection(session, DEMO_USER_ID, policy, version, account.id, NOW)
        goal_id = goal.goal.id
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = epoch.id
    runner = ScenarioRunner(demo_engine, DEMO_USER_ID)
    income = runner.rpc(
        ScenarioRPC(
            scenario_id="native-goal-dashboard",
            purpose="DEVELOPMENT",
            operation="ingest_goal_income",
            expected_epoch_id=epoch_id,
            goal_id=goal_id,
        ),
        NOW + timedelta(seconds=1),
    )
    assert income["result"]["bank_status"] == "SETTLED"
    assert income["result"]["projection_status"] == "PROJECTED"
    prepared = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key=str(uuid4()), intent=GoalIntent(kind="allocate_goal", goal_id=goal_id)
        ),
        NOW + timedelta(seconds=2),
    )
    assert prepared.status == "PLANNED" and prepared.effect.amount_cents == 200_000
    assert prepared.receipt is None
    action_id = prepared.action_id
    before = database_snapshot(demo_engine)
    with Session(demo_engine) as session, session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        clock = NOW + timedelta(seconds=3)
        ledger = read_income_state(session, DEMO_USER_ID, clock).ledger
        assert not any(item.action_id == action_id for item in ledger.reservations)
        assert income_lots_for_action(session, DEMO_USER_ID, clock, action_id=action_id) == (
            income_lots_for_action(session, DEMO_USER_ID, clock)
        )
        dashboard = get_dashboard(session, DEMO_USER_ID, NOW + timedelta(seconds=3))
        assert dashboard.pending_actions.total == 1
        assert dashboard.pending_actions.list_complete
        item = dashboard.pending_actions.items[0]
        assert item.action_id == action_id
        assert item.amount_cents == 200_000 and item.receipt_id is None
        assert item.current_decision is not None and item.current_decision.level == "AUTO_EXECUTE"
        assert item.bank_state_proven and item.audit_status == "VALID"
        assert verify_audit_chain(session, DEMO_USER_ID, epoch_id).status == "VALID"
    assert database_snapshot(demo_engine) == before
