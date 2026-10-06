"""Independent bank economic facts, on isolated PostgreSQL databases."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    AssetPosition,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import open_simulated_bank, process_redemption
from app.tests.test_asset_allocation_service import authorization, purchase_action
from app.tests.test_boundary_service import boundary_engine, confirmed_policy
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def test_opening_is_repeatable_and_bank_postings_are_immutable(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        cash = {
            row.id: row.balance_cents
            for row in session.scalars(select(Account))
            if row.account_type != "CREDIT_CARD"
        }
        positions = {row.id: row.principal_cents for row in session.scalars(select(AssetPosition))}
        first = open_simulated_bank(
            session, DEMO_USER_ID, SEED_AS_OF, cash_balances=cash, position_principals=positions
        )
        assert len(first) == 7
        assert first == open_simulated_bank(
            session, DEMO_USER_ID, SEED_AS_OF, cash_balances=cash, position_principals=positions
        )
    with Session(boundary_engine) as session, session.begin():
        posting = session.get(SimulatedBankPosting, first[0])
        assert posting is not None
        assert posting.entry_kind == "OPENING"
        assert posting.balance_before_cents == 0
        with pytest.raises(IntegrityError), session.begin_nested():
            posting.occurred_at += timedelta(seconds=1)
            session.flush()


def bank_action(engine: Engine, delay: int = 0) -> tuple[UUID, UUID, UUID, int]:
    with Session(engine) as session, session.begin():
        _, version_id = confirmed_policy(session, authorization())
        position = session.scalar(select(AssetPosition).order_by(AssetPosition.id))
        assert position is not None
        action = purchase_action(session, version_id, amount=position.principal_cents)
        action.action_type = "ASSET_REDEEM"
        destination_id = action.source_account_id
        action.source_account_id = position.account_id
        action.position_id = position.id
        action.product_id = position.product_id
        action.destination_account_id = None
        action.autonomy_level = "AUTO_EXECUTE"
        action.status = "SUBMITTED"
        action.request = {
            "bank_request": {
                "user_id": str(DEMO_USER_ID),
                "position_id": str(position.id),
                "position_account_id": str(position.account_id),
                "product_id": str(position.product_id),
                "goal_id": None,
                "destination_account_id": str(destination_id),
                "principal_cents": position.principal_cents,
                "requested_at": SEED_AS_OF.isoformat(),
                "available_at": (SEED_AS_OF + timedelta(days=delay)).isoformat(),
                "expires_at": (SEED_AS_OF + timedelta(minutes=15)).isoformat(),
            }
        }
        action.request_hash = configuration_hash(action.request)
        session.flush()
        return action.id, position.id, destination_id, position.principal_cents


def test_t0_bank_commits_two_conserved_legs_without_application_projection(
    boundary_engine: Engine,
) -> None:
    action_id, position_id, account_id, amount = bank_action(boundary_engine)
    with Session(boundary_engine) as session:
        account = session.get(Account, account_id)
        assert account is not None
        initial_cash = account.balance_cents
    result = process_redemption(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert result.status == "SETTLED"
    assert result == process_redemption(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.redemption_id == result.request_id
                )
            )
        )
        assert sorted(row.delta_cents for row in postings) == [-amount, amount]
        account = session.get(Account, account_id)
        assert account is not None and account.balance_cents == initial_cash
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.status == "HELD"


def test_t1_acceptance_never_adds_cash_and_settlement_is_once(boundary_engine: Engine) -> None:
    action_id, _, _, _ = bank_action(boundary_engine, 1)
    result = process_redemption(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert result.status == "ACCEPTED" and result.posting_ids == []
    settled = process_redemption(
        boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF + timedelta(days=1)
    )
    assert settled.status == "SETTLED" and len(settled.posting_ids) == 2


def test_bank_key_and_economic_request_cannot_change_after_acceptance(
    boundary_engine: Engine,
) -> None:
    action_id, _, _, _ = bank_action(boundary_engine, 1)
    result = process_redemption(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        row = session.get(SimulatedBankRedemption, result.request_id)
        assert row is not None
        with pytest.raises(IntegrityError), session.begin_nested():
            row.principal_cents += 1
            session.flush()
        action = session.get(ActionPlan, action_id)
        assert action is not None
        action.request = {
            **action.request,
            "bank_request": {
                **action.request["bank_request"],
                "available_at": (SEED_AS_OF + timedelta(days=2)).isoformat(),
            },
        }
        action.request_hash = configuration_hash(action.request)
    with pytest.raises(PolicyLifecycleError, match="cannot change economic content"):
        process_redemption(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)


def test_opening_replay_cannot_reset_a_changed_anchor(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        with pytest.raises(PolicyLifecycleError, match="cannot be replaced"):
            open_simulated_bank(
                session,
                DEMO_USER_ID,
                SEED_AS_OF,
                cash_balances={account.id: account.balance_cents + 1},
                position_principals={},
            )
