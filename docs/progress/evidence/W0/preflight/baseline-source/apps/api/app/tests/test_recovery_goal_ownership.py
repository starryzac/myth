"""Independent PostgreSQL acceptance: natural maturity preserves the original goal."""

from datetime import timedelta
from uuid import UUID

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    DecisionRun,
    EvidenceItem,
    Goal,
    Policy,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.boundary import CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE, load_boundary_context
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.policy_lifecycle import change_policy, revoke_policy
from app.services.recovery import preview_recovery, run_recovery
from app.tests.test_asset_allocation_service import authorization, exposure_statement
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import confirmed_policy, goal_fixture, snapshot
from app.tests.test_recovery_service import mature_recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _proof(session: Session, source: str, key: str, identifier: UUID) -> EvidenceItem:
    proof = session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.user_id == DEMO_USER_ID,
            EvidenceItem.source_type == source,
            EvidenceItem.content[key].as_string() == str(identifier),
            EvidenceItem.status == "VALID",
        )
    )
    assert proof is not None
    return proof


def _mature_goal_fixture(engine: Engine) -> tuple[UUID, UUID, UUID, int]:
    _, position_id, principal = mature_recovery_fixture(engine)
    with Session(engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True)
        goal = session.get(Goal, goal_id)
        position = session.get(AssetPosition, position_id)
        assert goal is not None and goal.account_id is not None and position is not None
        asset_policy_id, asset_version_id = confirmed_policy(
            session,
            authorization(scope="goal", goal_id=str(goal_id)),
            SEED_AS_OF - timedelta(days=40),
        )
        goal_version = session.get(PolicyVersion, goal.policy_version_id)
        assert goal_version is not None
        goal_configuration = {
            **goal_version.configuration,
            "asset_policy_id": str(asset_policy_id),
        }
        change_policy(
            session,
            DEMO_USER_ID,
            goal.policy_id,
            goal_version.id,
            goal_configuration,
            configuration_hash(validate_configuration(goal_configuration)),
            True,
            "Bind the imported goal-owned original purchase",
            "goal-recovery-asset-reference",
            SEED_AS_OF,
        )

        # Rebuild the original, independently confirmed purchase binding, including its
        # actual goal funding account, transaction, receipt-linked action and position.
        position.goal_id = goal_id
        position.policy_version_id = asset_version_id
        position_proof = _proof(session, "SIMULATED_BANK_POSITION", "position_id", position_id)
        purchase = session.get(ActionPlan, UUID(position_proof.content["purchase_action_id"]))
        transaction = session.get(
            Transaction, UUID(position_proof.content["purchase_transaction_id"])
        )
        assert purchase is not None and transaction is not None
        purchase.policy_version_id = asset_version_id
        purchase.goal_id = goal_id
        purchase.source_account_id = goal.account_id
        purchase.request = {
            **purchase.request,
            "policy_version_id": str(asset_version_id),
            "goal_id": str(goal_id),
            "source_account_id": str(goal.account_id),
            "product_id": str(position.product_id),
        }
        purchase.request_hash = configuration_hash(purchase.request)
        decision = session.get(DecisionRun, purchase.decision_run_id)
        assert decision is not None
        decision.policy_version_ids = [str(asset_version_id)]
        transaction.account_id = goal.account_id
        transaction.balance_after_cents = 160000
        transaction_proof = session.get(EvidenceItem, transaction.evidence_id)
        assert transaction_proof is not None
        transaction_proof.content = {
            **transaction_proof.content,
            "account_id": str(goal.account_id),
            "balance_after_cents": 160000,
        }
        transaction_proof.content_hash = configuration_hash(transaction_proof.content)
        position_proof.content = {
            **position_proof.content,
            "policy_version_id": str(asset_version_id),
            "goal_id": str(goal_id),
        }
        position_proof.content_hash = configuration_hash(position_proof.content)
        goal.allocated_cents = 160000 + principal
        ownership = _proof(session, OWNERSHIP_SOURCE, "goal_id", goal_id)
        ownership.content = {
            **ownership.content,
            "allocated_cents": goal.allocated_cents,
            "principal_owned_cents": principal,
            "position_ids": [str(position_id)],
        }
        ownership.content_hash = configuration_hash(ownership.content)
        revoke_policy(session, DEMO_USER_ID, asset_policy_id, asset_version_id, now=SEED_AS_OF)
        old_exposure = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE",
                EvidenceItem.status == "VALID",
            )
        )
        assert old_exposure is not None
        exposure_statement(session, old_exposure.content["settlements"])
        return goal_id, asset_policy_id, position_id, principal


def test_mature_goal_principal_returns_once_without_releasing_goal_cash_or_monthly_contribution(
    boundary_engine: Engine,
) -> None:
    goal_id, asset_policy_id, position_id, principal = _mature_goal_fixture(boundary_engine)
    assert principal == 250000
    with Session(boundary_engine) as session:
        goal = session.get(Goal, goal_id)
        policy = session.get(Policy, asset_policy_id)
        assert goal is not None and goal.account_id is not None and policy is not None
        assert policy.status == "REVOKED"
        assert goal.asset_policy_id == asset_policy_id
        destination = goal.account_id
        allocation_before = goal.allocated_cents
        month_before = dict(_proof(session, CONTRIBUTION_SOURCE, "goal_id", goal_id).content)
        balances_before = {
            row.id: row.balance_cents
            for row in session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID))
        }
        preview = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        # This is a complete original acquisition. Only the already-due bank event awaits
        # reconciliation; permission/ownership/exposure defects would invalidate this fixture.
        assert {issue.code for issue in preview.source_issues} == {
            "UNRECONCILED_POSITION_AVAILABILITY"
        }
        assert preview.plan.steps == []

    result = run_recovery(boundary_engine, DEMO_USER_ID, "goal-natural-maturity", SEED_AS_OF)
    assert result.status == "PARTIAL_RECOVERY"
    assert result.actual_boundary.status == "LIQUIDITY_RISK"
    # Seed general shortfall 30000 + Oct minimum (100000-40000) + Nov/Dec 100000 each.
    # The 250000 returned principal increases C and protected goal cash equally.
    assert result.actual_boundary.minimum_margin_cents == -290000
    assert result.actual_boundary.deficit_cents == 290000
    assert result.actual_boundary.safe_idle_cents == 0
    assert result.actual_boundary.protected_cents_by_reason["goal_cash"] == 410000
    assert result.actual_boundary.protected_cents_by_reason["goal_minimum"] == 260000
    assert len(result.actions) == 1 and result.actions[0].receipt_id is not None
    assert any(item.code == "PRINCIPAL_RECEIVED" for item in result.notifications)

    with Session(boundary_engine) as session:
        goal = session.get(Goal, goal_id)
        position = session.get(AssetPosition, position_id)
        assert goal is not None and position is not None
        assert goal.allocated_cents == allocation_before == 410000
        assert position.status == "REDEEMED" and position.goal_id == goal_id
        assert _proof(session, CONTRIBUTION_SOURCE, "goal_id", goal_id).content == month_before
        ownership = _proof(session, OWNERSHIP_SOURCE, "goal_id", goal_id).content
        assert ownership["cash_owned_cents"] == 410000
        assert ownership["principal_owned_cents"] == 0
        assert ownership["allocated_cents"] == 410000 and ownership["position_ids"] == []
        for account in session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID)):
            assert account.balance_cents == balances_before[account.id] + (
                principal if account.id == destination else 0
            )
        request = session.get(SimulatedBankRedemption, result.actions[0].bank_request_id)
        receipt = session.get(ActionReceipt, result.actions[0].receipt_id)
        assert request is not None and receipt is not None
        assert request.destination_account_id == destination
        assert request.goal_id == goal_id and request.position_id == position_id
        assert receipt.status == "SUCCEEDED" and receipt.executed_cents == principal
        legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.redemption_id == request.id)
            )
        )
        assert sorted(leg.delta_cents for leg in legs) == [-250000, 250000]
        assert {leg.entry_kind for leg in legs} == {"CASH_CREDIT", "PRINCIPAL_DEBIT"}
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        assert context.sources.issues == []
        owned = next(item for item in context.snapshot.goals if item.goal_id == goal_id)
        contribution = next(
            item for item in context.snapshot.goal_month_contributions if item.goal_id == goal_id
        )
        assert owned.cash_owned_cents == owned.allocated_cents == 410000
        assert owned.principal_owned_cents == 0 and contribution.contributed_cents == 40000
        after = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        assert after.source_issues == []
        assert after.plan.actual_boundary.minimum_margin_cents == -290000
        assert after.plan.steps == []

    persisted = snapshot(boundary_engine)
    replay = run_recovery(boundary_engine, DEMO_USER_ID, "goal-natural-maturity", SEED_AS_OF)
    assert replay.run_id == result.run_id
    assert replay.actual_boundary.minimum_margin_cents == -290000
    assert snapshot(boundary_engine) == persisted
