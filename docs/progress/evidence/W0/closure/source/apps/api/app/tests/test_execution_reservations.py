"""PostgreSQL claims preserve unresolved effects and reject competing cash use."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from app.db.models import Account, ActionPlan, ActionResourceReservation, BankOperation
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_reservations import ResourceClaim, reserve_resources, resolve_resources
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_asset_allocation_service import authorization, purchase_action
from app.tests.test_boundary_service import boundary_engine, confirmed_policy
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def test_cash_claim_replay_competition_and_unknown_preservation(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        _, version_id = confirmed_policy(session, authorization())
        first = purchase_action(session, version_id)
        second = purchase_action(session, version_id)
        account = session.get(Account, first.source_account_id)
        assert account is not None
        capacity = account.balance_cents
        claim = ResourceClaim(
            resource_kind="CASH",
            resource_key=str(account.id),
            amount_cents=capacity,
            capacity_cents=capacity,
        )
        ids = reserve_resources(session, DEMO_USER_ID, first.id, [claim], SEED_AS_OF)
        assert ids == reserve_resources(session, DEMO_USER_ID, first.id, [claim], SEED_AS_OF)
        with pytest.raises(PolicyLifecycleError), session.begin_nested():
            reserve_resources(session, DEMO_USER_ID, second.id, [claim], SEED_AS_OF)
        first.status = "UNKNOWN"
        session.flush()
        with pytest.raises(PolicyLifecycleError), session.begin_nested():
            resolve_resources(session, DEMO_USER_ID, first.id, "RELEASED", SEED_AS_OF)
        assert session.scalar(select(ActionResourceReservation.status)) == "RESERVED"
        first_id = first.id
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, first_id)
        assert action is not None
        action.status = "CANCELLED"
        resolve_resources(session, DEMO_USER_ID, first_id, "RELEASED", SEED_AS_OF)
        assert session.scalar(select(ActionResourceReservation.status)) == "RELEASED"


def test_two_concurrent_actions_cannot_reserve_the_same_cash_twice(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        _, version_id = confirmed_policy(session, authorization())
        actions = [purchase_action(session, version_id), purchase_action(session, version_id)]
        account = session.get(Account, actions[0].source_account_id)
        assert account is not None
        claim = ResourceClaim(
            resource_kind="CASH",
            resource_key=str(account.id),
            amount_cents=account.balance_cents,
            capacity_cents=account.balance_cents,
        )
        ids = [action.id for action in actions]

    def reserve(index: int) -> str:
        try:
            with Session(boundary_engine) as session, session.begin():
                reserve_resources(session, DEMO_USER_ID, ids[index], [claim], SEED_AS_OF)
            return "RESERVED"
        except PolicyLifecycleError as error:
            assert error.code == "RESOURCE_CONFLICT"
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == ["CONFLICT", "RESERVED"]
    with Session(boundary_engine) as session:
        rows = list(session.scalars(select(ActionResourceReservation)))
        assert len(rows) == 1 and rows[0].amount_cents == claim.amount_cents


def test_bank_definitive_rejection_is_terminal_after_resource_release(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        _, version = confirmed_policy(session, authorization())
        action = purchase_action(session, version)
        operation = BankOperation(
            id=action.id,
            user_id=DEMO_USER_ID,
            created_at=SEED_AS_OF,
            action_plan_id=action.id,
            legacy_redemption_id=None,
            closing_position_id=None,
            operation_type="PURCHASE_ASSET",
            business_key="rejected:" + str(action.id),
            idempotency_key=action.idempotency_key,
            request={},
            request_hash=configuration_hash({}),
            requested_at=SEED_AS_OF,
            available_at=SEED_AS_OF,
            settled_at=None,
            status="REJECTED",
        )
        session.add(operation)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            operation.status = "ACCEPTED"
            session.flush()
