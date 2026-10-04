"""Actual policy invalidation preserves bank effects and reconciles unsubmitted exposure."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    SimulatedBankPosting,
    Transaction,
)
from app.domain.income_ledger import LEDGER_SOURCE
from app.services import execution
from app.services.action_contracts import (
    ActionResponse,
    ConfirmActionRequest,
    GoalIntent,
    PrepareActionRequest,
)
from app.services.asset_exposure_import import load_all_asset_exposure
from app.services.audit_chain import verify_audit_chain
from app.services.boundary import load_boundary_context
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.execution_bank import BankOperationResult, process_operation
from app.services.external_bank_facts import ingest_external_fact
from app.services.goals import create_goal_projection
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import revoke_policy
from app.services.simulated_bank import validate_bank_projection
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_external_bank_facts import NOW, fact_request
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _prepared_goal(engine: Engine) -> tuple[UUID, UUID, UUID, ActionResponse]:
    seed_demo(engine)
    with Session(engine) as session, session.begin():
        account = session.scalars(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        ).one()
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "goal_saving",
                "name": "Actual policy invalidation",
                "target_cents": 100000,
                "deadline": "2027-10-01",
                "monthly_contribution": {"min_cents": 0, "target_cents": 10000, "max_cents": 10000},
            },
            SEED_AS_OF,
        )
        goal = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, account.id, SEED_AS_OF
        ).goal
        goal_id = goal.id
    salary = ingest_external_fact(engine, DEMO_USER_ID, fact_request(engine), NOW)
    assert salary.bank_status == "SETTLED" and salary.projection_status == "PROJECTED", salary
    prepared = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="real-lifecycle-goal",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        NOW,
    )
    assert prepared.effect.amount_cents == 10000 and prepared.effect.income_uses
    confirmed = confirm_action(
        engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        NOW,
    )
    assert confirmed.status == "AUTHORIZED"
    return policy_id, version_id, goal_id, confirmed


def _rows(engine: Engine, models: tuple[Any, ...]) -> str:
    with Session(engine) as session:
        rows = {
            model.__tablename__: [
                dict(row)
                for row in session.execute(
                    select(model.__table__).where(model.user_id == DEMO_USER_ID).order_by(model.id)
                ).mappings()
            ]
            for model in models
        }
    return json.dumps(rows, sort_keys=True, default=str)


def _current_income_original(engine: Engine) -> str:
    with Session(engine) as session:
        rows = list(
            session.execute(
                select(EvidenceItem.__table__)
                .where(
                    EvidenceItem.user_id == DEMO_USER_ID,
                    EvidenceItem.source_type == LEDGER_SOURCE,
                    EvidenceItem.status != "SUPERSEDED",
                )
                .order_by(EvidenceItem.id)
            ).mappings()
        )
    assert len(rows) == 1
    return json.dumps([dict(row) for row in rows], sort_keys=True, default=str)


def test_revoke_unsubmitted_real_goal_action_refreshes_complete_exposure_without_moving_money(
    demo_engine: Engine,
) -> None:
    policy_id, version_id, _, prepared = _prepared_goal(demo_engine)
    monetary_models = (
        Account,
        Goal,
        AssetPosition,
        Transaction,
        BankOperation,
        SimulatedBankPosting,
        ExternalBankFact,
    )
    before_money = _rows(demo_engine, monetary_models)
    with Session(demo_engine) as session:
        before_income = read_income_state(session, DEMO_USER_ID, NOW).ledger
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None
        before_request = json.dumps(action.request, sort_keys=True)
        before_hash = action.request_hash
        assert (
            session.scalar(
                select(ActionResourceReservation.id).where(
                    ActionResourceReservation.action_plan_id == prepared.action_id
                )
            )
            is None
        )
    with Session(demo_engine) as session, session.begin():
        result = revoke_policy(session, DEMO_USER_ID, policy_id, version_id, NOW)
    assert result.invalidated_action_ids == [prepared.action_id]
    assert result.inflight_action_ids == []
    assert _rows(demo_engine, monetary_models) == before_money
    with Session(demo_engine) as session:
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "INVALIDATED"
        assert json.dumps(action.request, sort_keys=True) == before_request
        assert action.request_hash == before_hash
        assert read_income_state(session, DEMO_USER_ID, NOW).ledger == before_income
        assert (
            session.scalar(
                select(BankOperation.id).where(BankOperation.action_plan_id == action.id)
            )
            is None
        )
        assert (
            session.scalar(
                select(ActionReceipt.id).where(ActionReceipt.action_plan_id == action.id)
            )
            is None
        )
        context = load_boundary_context(session, DEMO_USER_ID, NOW)
        exposures = load_all_asset_exposure(session, context)
        assert exposures is not None, context.sources.issues
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == DEMO_USER_ID,
                EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE",
                EvidenceItem.status == "VALID",
            )
        ).one()
        declaration = next(
            row for row in proof.content["settlements"] if row["action_id"] == str(action.id)
        )
        assert declaration["state"] == "NO_EFFECT"
        validate_bank_projection(session, DEMO_USER_ID, NOW)
        verification = verify_audit_chain(session, DEMO_USER_ID)
        assert verification.status == "VALID", verification
    unchanged = database_snapshot(demo_engine)
    with Session(demo_engine) as session, session.begin():
        replay = revoke_policy(session, DEMO_USER_ID, policy_id, version_id, NOW)
    assert replay == result and database_snapshot(demo_engine) == unchanged


def test_revoke_preserves_actual_settled_unknown_operation_and_reservations(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_id, version_id, goal_id, prepared = _prepared_goal(demo_engine)
    actual_bank = process_operation

    def lose_actual_bank_response(
        engine: Engine, user_id: UUID, action_id: UUID, now: datetime
    ) -> BankOperationResult:
        result = actual_bank(engine, user_id, action_id, now)
        assert result.status == "SETTLED"
        raise OSError("Actual bank committed; its response was lost")

    with monkeypatch.context() as patch:
        patch.setattr(execution, "process_operation", lose_actual_bank_response)
        with pytest.raises(OSError, match="Actual bank committed"):
            execute_action(demo_engine, DEMO_USER_ID, prepared.action_id, NOW)
    before_bank = _rows(demo_engine, (BankOperation, SimulatedBankPosting))
    before_claims = _rows(demo_engine, (ActionResourceReservation,))
    before_income = _current_income_original(demo_engine)
    with Session(demo_engine) as session:
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "UNKNOWN"
        original_request, original_hash, original_key = (
            json.dumps(action.request, sort_keys=True),
            action.request_hash,
            action.idempotency_key,
        )
        claims = session.scalars(
            select(ActionResourceReservation).where(
                ActionResourceReservation.action_plan_id == action.id
            )
        ).all()
        assert claims and all(row.status == "RESERVED" for row in claims)
        goal = session.get(Goal, goal_id)
        assert goal is not None and goal.allocated_cents == 0
    with Session(demo_engine) as session, session.begin():
        result = revoke_policy(session, DEMO_USER_ID, policy_id, version_id, NOW)
    assert result.invalidated_action_ids == []
    assert result.inflight_action_ids == [prepared.action_id]
    assert _rows(demo_engine, (BankOperation, SimulatedBankPosting)) == before_bank
    assert _rows(demo_engine, (ActionResourceReservation,)) == before_claims
    assert _current_income_original(demo_engine) == before_income
    with Session(demo_engine) as session:
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "UNKNOWN"
        assert (
            json.dumps(action.request, sort_keys=True),
            action.request_hash,
            action.idempotency_key,
        ) == (original_request, original_hash, original_key)
        goal = session.get(Goal, goal_id)
        assert goal is not None and goal.allocated_cents == 0
    repaired = execute_action(demo_engine, DEMO_USER_ID, prepared.action_id, NOW)
    assert repaired.status == "SUCCEEDED" and repaired.receipt is not None
    assert _rows(demo_engine, (BankOperation, SimulatedBankPosting)) == before_bank
    with Session(demo_engine) as session:
        goal = session.get(Goal, goal_id)
        assert goal is not None and goal.allocated_cents == prepared.effect.amount_cents
        claims = session.scalars(
            select(ActionResourceReservation).where(
                ActionResourceReservation.action_plan_id == prepared.action_id
            )
        ).all()
        assert claims and all(row.status == "CONSUMED" for row in claims)
        validate_bank_projection(session, DEMO_USER_ID, NOW)
        verification = verify_audit_chain(session, DEMO_USER_ID)
        assert verification.status == "VALID", verification
    unchanged = database_snapshot(demo_engine)
    assert execute_action(demo_engine, DEMO_USER_ID, prepared.action_id, NOW) == repaired
    assert database_snapshot(demo_engine) == unchanged
