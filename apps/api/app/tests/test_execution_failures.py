"""Fault injection across real commits and concurrent independent action keys."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from uuid import UUID

import app.services.execution as execution
import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    PolicyVersion,
)
from app.services.action_contracts import (
    ConfirmActionRequest,
    GoalIntent,
    PrepareActionRequest,
    TransferIntent,
)
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_bank import BankOperationResult, process_operation
from app.services.execution_projection import project_execution
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, revoke_policy
from app.tests.test_boundary_service import boundary_engine, snapshot
from app.tests.test_execution_projection import zero_goal_income_setup
from app.tests.test_execution_service import transfer_accounts
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def confirmed_transfer(
    engine: Engine, source: UUID, target: UUID, key: str, amount: int = 10000
) -> UUID:
    prepared = execution.prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key=key,
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=amount,
            ),
        ),
        SEED_AS_OF,
    )
    execution.confirm_action(
        engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    return prepared.action_id


@pytest.mark.parametrize("failure", ["lost_bank_response", "projection_failed"])
def test_retry_unknown_projects_original_committed_bank_effect_without_resubmitting_money(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    action_id = confirmed_transfer(boundary_engine, source, target, "fault-transfer")
    bank, project = process_operation, project_execution
    if failure == "lost_bank_response":

        def lost(engine: Engine, user: UUID, action: UUID, now: datetime) -> BankOperationResult:
            bank(engine, user, action, now)
            raise RuntimeError("injected response loss after actual bank commit")

        monkeypatch.setattr(execution, "process_operation", lost)
    else:

        def failed(
            session: Session, operation: BankOperation, now: datetime
        ) -> ActionReceipt | None:
            raise RuntimeError("injected projection transaction failure")

        monkeypatch.setattr(execution, "project_execution", failed)
    with pytest.raises(RuntimeError, match="injected"):
        execution.execute_action(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        action = session.get(ActionPlan, action_id)
        operation = session.get(BankOperation, action_id)
        assert action is not None and action.status == "UNKNOWN"
        assert operation is not None and operation.status == "SETTLED"
        assert all(
            row.status == "RESERVED" for row in session.scalars(select(ActionResourceReservation))
        )
    monkeypatch.setattr(execution, "process_operation", bank)
    monkeypatch.setattr(execution, "project_execution", project)
    retried = execution.execute_action(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert retried.status == "SUCCEEDED"
    done = snapshot(boundary_engine)
    assert (
        execution.execute_action(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF).receipt
        == retried.receipt
    )
    assert snapshot(boundary_engine) == done
    with Session(boundary_engine) as session:
        assert len(list(session.scalars(select(BankOperation)))) == 1


def test_two_different_keys_cannot_spend_the_same_reserved_source_cash(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session:
        account = session.get(Account, source)
        assert account is not None
        amount = account.balance_cents // 2 + 1
    first = confirmed_transfer(boundary_engine, source, target, "race-1", amount)
    second = confirmed_transfer(boundary_engine, source, target, "race-2", amount)
    entered, release = Event(), Event()
    bank = process_operation

    def paused(engine: Engine, user: UUID, action: UUID, now: datetime) -> BankOperationResult:
        if action == first:
            entered.set()
            assert release.wait(20), "test competitor did not release the actual bank phase"
        return bank(engine, user, action, now)

    monkeypatch.setattr(execution, "process_operation", paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(
            execution.execute_action, boundary_engine, DEMO_USER_ID, first, SEED_AS_OF
        )
        try:
            assert entered.wait(20)
            with pytest.raises(PolicyLifecycleError):
                execution.execute_action(boundary_engine, DEMO_USER_ID, second, SEED_AS_OF)
        finally:
            release.set()
        assert winner.result(timeout=20).status == "SUCCEEDED"
    with Session(boundary_engine) as session:
        operations = list(session.scalars(select(BankOperation)))
        assert len(operations) == 1 and operations[0].action_plan_id == first


def test_definite_bank_refusal_releases_only_independently_unaccepted_claims(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    goal_id, _ = zero_goal_income_setup(boundary_engine)
    prepared = execution.prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="revoke-at-bank",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        SEED_AS_OF,
    )
    bank = process_operation

    def revoke_before_bank(
        engine: Engine, user: UUID, action_id: UUID, now: datetime
    ) -> BankOperationResult:
        with Session(engine) as session, session.begin():
            action = session.get(ActionPlan, action_id)
            assert action is not None
            version = session.get(PolicyVersion, action.policy_version_id)
            assert version is not None
            revoke_policy(session, user, version.policy_id, version.id, now)
        return bank(engine, user, action_id, now)

    monkeypatch.setattr(execution, "process_operation", revoke_before_bank)
    with pytest.raises(PolicyLifecycleError):
        execution.execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        action = session.get(ActionPlan, prepared.action_id)
        assert action is not None and action.status == "INVALIDATED"
        assert session.get(BankOperation, prepared.action_id) is None
        assert all(
            r.status == "RELEASED" for r in session.scalars(select(ActionResourceReservation))
        )
        income = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
        assert sum(f.available_cents for f in income.fragments) == 100000
        assert all(r.state == "RELEASED" for r in income.reservations)
