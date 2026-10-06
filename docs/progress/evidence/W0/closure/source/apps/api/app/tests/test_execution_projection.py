"""Independent bank effects are projected once, never inferred from an HTTP response."""

import json
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    CreditCardBill,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    Transaction,
)
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect, OccurrenceReference
from app.domain.income_ledger import LEDGER_SOURCE, IncomeUse
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE, SETTLEMENT_SOURCE
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_bank import open_execution_anchors, process_operation
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_projection import project_execution
from app.services.execution_reservations import ResourceClaim, reserve_resources
from app.services.goals import create_goal_projection
from app.services.income_ledger import read_income_state, reserve_income_for_action
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import open_simulated_bank
from app.tests.test_asset_allocation_service import purchase_action
from app.tests.test_boundary_service import confirmed_policy, imported_proof
from app.tests.test_execution_bank import boundary_engine as boundary_engine
from app.tests.test_execution_bank import (
    card_payment_command,
    purchase_command,
    redemption_command,
    source_ledger,
    transfer_action,
)
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

pytestmark = pytest.mark.integration


def test_multi_source_purchase_records_each_actual_debit_without_double_counting(
    boundary_engine: Engine,
) -> None:
    action_id, position_id = purchase_command(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, action_id)
        assert action is not None
        second = Account(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            external_ref=str(uuid4()),
            name="Explicit second opening",
            account_type="CASH",
            balance_cents=30000,
            observed_at=SEED_AS_OF,
            created_at=SEED_AS_OF,
        )
        session.add(second)
        session.flush()
        content = {
            "simulation": True,
            "user_id": str(DEMO_USER_ID),
            "account_id": str(second.id),
            "account_type": "CASH",
            "currency": "CNY",
            "balance_cents": 30000,
            "as_of": SEED_AS_OF.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_BALANCE",
                source_ref=str(second.id),
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
                observed_at=SEED_AS_OF,
                valid_from=SEED_AS_OF,
            )
        )
        open_simulated_bank(
            session,
            DEMO_USER_ID,
            SEED_AS_OF,
            cash_balances={second.id: 30000},
            position_principals={},
        )
        income = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == LEDGER_SOURCE, EvidenceItem.status == "VALID"
            )
        )
        assert income is not None
        # Explicit fixture import expands known CASH scope; no new income origin is invented.
        income.content = {
            **income.content,
            "scope_account_ids": sorted([*income.content["scope_account_ids"], str(second.id)]),
        }
        income.content_hash = configuration_hash(income.content)
        old = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        effect = old.effect.model_copy(
            update={
                "cash_uses": [
                    CashUse(account_id=action.source_account_id, amount_cents=20000),
                    CashUse(account_id=second.id, amount_cents=30000),
                ]
            }
        )
        action.request = {
            **action.request,
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json"),
            "income_evidence": {"id": str(income.id), "hash": income.content_hash},
        }
        action.request_hash = configuration_hash(action.request)
        session.execute(
            delete(ActionResourceReservation).where(
                ActionResourceReservation.action_plan_id == action.id
            )
        )
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="CASH",
                    resource_key=str(use.account_id),
                    amount_cents=use.amount_cents,
                    capacity_cents=use.amount_cents,
                )
                for use in effect.cash_uses
            ]
            + [
                ResourceClaim(
                    resource_kind="BUSINESS",
                    resource_key=effect.business_key,
                    amount_cents=1,
                    capacity_cents=1,
                )
            ],
            SEED_AS_OF,
        )
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, action.id)
        expected = {action.source_account_id: 20000, second.id: 30000}
        return_account = effect.return_account_id
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        receipt = project_execution(session, operation, SEED_AS_OF)
        assert receipt is not None and receipt.executed_cents == 50000
        purchases = list(
            session.scalars(
                select(Transaction).where(
                    Transaction.source_ref.like(f"bank-operation:{action_id}:%")
                )
            )
        )
        assert {t.account_id: t.amount_cents for t in purchases} == expected
        assert len(purchases) == 2 and sum(t.amount_cents for t in purchases) == 50000
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position_id),
                EvidenceItem.status == "VALID",
            )
        )
        assert proof is not None and set(proof.content["purchase_transaction_ids"]) == {
            str(t.id) for t in purchases
        }
        assert proof.content["return_account_id"] == str(return_account)


@pytest.mark.parametrize("range_rule", [False, True])
def test_recurring_projection_keeps_final_total_and_increments_paid_only_once(
    boundary_engine: Engine, range_rule: bool
) -> None:
    with Session(boundary_engine) as session, session.begin():
        rent = session.scalar(
            select(Transaction)
            .where(Transaction.category == "rent")
            .order_by(Transaction.occurred_at.desc())
        )
        assert (
            rent is not None and rent.counterparty_ref is not None and rent.evidence_id is not None
        )
        rule = (
            {"kind": "range", "min_cents": 100000, "max_cents": 200000}
            if range_rule
            else {"kind": "exact", "amount_cents": 150000}
        )
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": rent.counterparty_ref,
                "due_day": 4,
                "amount_rule": rule,
                "auto_execute": True,
            },
        )
        content = {
            "protocol": "recurring-settlement-v1",
            "policy_id": str(policy_id),
            "period": "2026-10",
            "payee_id": rent.counterparty_ref,
            "paid_cents": 25000,
            "complete": True,
            "as_of": SEED_AS_OF.isoformat(),
        }
        if range_rule:
            content["final_total_cents"] = 150000
        settlement = imported_proof(session, SETTLEMENT_SOURCE, content)
        income = source_ledger(session)
        action = purchase_action(session, version_id, amount=125000)
        action.action_type, action.product_id, action.status = "PAY_RECURRING", None, "SUBMITTED"
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key=f"occurrence:{policy_id}:2026-10",
            action_type="PAY_RECURRING",
            amount_cents=125000,
            cash_uses=[CashUse(account_id=action.source_account_id, amount_cents=125000)],
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            liability=OccurrenceReference(
                policy_id=policy_id,
                period="2026-10",
                final_total_cents=150000 if range_rule else None,
                evidence_ids=[settlement.id],
            ),
            payee_id=rent.counterparty_ref,
            payee_evidence_id=rent.evidence_id,
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        action.request = {
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json"),
            "income_evidence": {"id": str(income.id), "hash": income.content_hash},
        }
        if range_rule:
            confirmation_id = uuid5(action.id, "confirmation:" + execution_effect_hash(effect))
            confirmation = {
                "simulation": True,
                "user_id": str(DEMO_USER_ID),
                "action_id": str(action.id),
                "effect_hash": execution_effect_hash(effect),
                "accepted": True,
                "confirmed_at": SEED_AS_OF.isoformat(),
                "valid_until": effect.expires_at.isoformat(),
            }
            session.add(
                EvidenceItem(
                    id=confirmation_id,
                    user_id=DEMO_USER_ID,
                    created_at=SEED_AS_OF,
                    evidence_level="USER_CONFIRMED_ACTION",
                    source_type="USER_ACTION_CONFIRMATION",
                    source_ref=str(action.id),
                    content=confirmation,
                    content_hash=configuration_hash(confirmation),
                    status="VALID",
                    observed_at=SEED_AS_OF,
                    valid_from=SEED_AS_OF,
                    valid_to=effect.expires_at,
                )
            )
            action.request = {**action.request, "confirmation_evidence_id": str(confirmation_id)}
        action.request_hash = configuration_hash(action.request)
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="CASH",
                    resource_key=str(action.source_account_id),
                    amount_cents=125000,
                    capacity_cents=125000,
                ),
                ResourceClaim(
                    resource_kind="BUSINESS",
                    resource_key=effect.business_key,
                    amount_cents=1,
                    capacity_cents=1,
                ),
            ],
            SEED_AS_OF,
        )
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, action.id)
        action_id = action.id
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        receipt = project_execution(session, operation, SEED_AS_OF)
        assert receipt is not None and receipt.executed_cents == 125000
        current = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == SETTLEMENT_SOURCE, EvidenceItem.status == "VALID"
            )
        )
        assert current is not None and current.content["paid_cents"] == 150000
        assert current.content.get("final_total_cents") == (150000 if range_rule else None)
        again = project_execution(session, operation, SEED_AS_OF)
        assert again is not None and again.id == receipt.id
        assert current.content["paid_cents"] == 150000


def test_transfer_projection_uses_bank_facts_once_and_preserves_income_origin(
    boundary_engine: Engine,
) -> None:
    action_id, source_id, destination_id = transfer_action(boundary_engine)
    with Session(boundary_engine) as session:
        before = {row.id: row.balance_cents for row in session.scalars(select(Account))}
        original = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == LEDGER_SOURCE, EvidenceItem.status == "VALID"
            )
        )
        assert original is not None
        origins = original.content["origins"]
        fragments = original.content["fragments"]
    result = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, result.operation_id)
        assert operation is not None
        receipt = project_execution(session, operation, SEED_AS_OF)
        assert receipt is not None
        receipt_id = receipt.id
        assert receipt.executed_cents == 10000
        assert receipt.response["bank_operation_id"] == str(operation.id)
        assert set(receipt.response["posting_ids"]) == {
            str(row.id)
            for row in session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == operation.id
                )
            )
        }
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, result.operation_id)
        assert operation is not None
        again = project_execution(session, operation, SEED_AS_OF)
        assert again is not None and again.id == receipt_id
        after = {row.id: row.balance_cents for row in session.scalars(select(Account))}
        assert after == {
            **before,
            source_id: before[source_id] - 10000,
            destination_id: before[destination_id] + 10000,
        }
        action = session.get(ActionPlan, action_id)
        assert action is not None and action.status == "SUCCEEDED"
        assert all(
            row.status == "CONSUMED"
            for row in session.scalars(
                select(ActionResourceReservation).where(
                    ActionResourceReservation.action_plan_id == action_id
                )
            )
        )
        transactions = list(
            session.scalars(
                select(Transaction).where(
                    Transaction.source_ref.like(f"bank-operation:{action_id}:%")
                )
            )
        )
        assert len(transactions) == 2
        assert {(row.account_id, row.direction, row.amount_cents) for row in transactions} == {
            (source_id, "DEBIT", 10000),
            (destination_id, "CREDIT", 10000),
        }
        for row in transactions:
            proof = session.get(EvidenceItem, row.evidence_id)
            assert proof is not None and proof.content["economic_role"] == "INTERNAL_TRANSFER"
        current = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == LEDGER_SOURCE, EvidenceItem.status == "VALID"
            )
        )
        assert (
            current is not None
            and current.content["origins"] == origins
            and current.content["fragments"] == fragments
        )
        assert (
            len(
                list(
                    session.scalars(
                        select(ActionReceipt).where(ActionReceipt.action_plan_id == action_id)
                    )
                )
            )
            == 1
        )


def test_purchase_projection_materializes_exact_bank_principal_and_purchase_link(
    boundary_engine: Engine,
) -> None:
    action_id, position_id = purchase_command(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        receipt = project_execution(session, operation, SEED_AS_OF)
        assert receipt is not None and receipt.executed_cents == 50000
        position = session.get(AssetPosition, position_id)
        assert (
            position is not None
            and position.principal_cents == 50000
            and position.accrued_yield_cents == 0
        )
        assert position.status == "HELD" and position.purchased_at == SEED_AS_OF
        transaction = session.get(Transaction, uuid5(action_id, "transaction:purchase"))
        assert (
            transaction is not None
            and transaction.amount_cents == 50000
            and transaction.direction == "DEBIT"
        )
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position_id),
                EvidenceItem.status == "VALID",
            )
        )
        assert (
            proof is not None and proof.content["acquisition_protocol"] == "execution-purchase-v1"
        )
        assert proof.content["purchase_exit_status"] == "UNSUBMITTED_PLAN"
        assert position.available_at is None
        assert proof.content["purchase_exit_plan"] == operation.request["effect"]["purchase_exit"]
        assert proof.content["purchase_transaction_ids"] == [str(transaction.id)]
        assert proof.content["purchase_action_id"] == str(action_id)
        assert proof.content["purchase_receipt_id"] == str(receipt.id)
        again = project_execution(session, operation, SEED_AS_OF)
        assert again is not None and again.id == receipt.id


def test_projection_refuses_cash_changed_after_bank_commit(boundary_engine: Engine) -> None:
    action_id, source_id, _ = transfer_action(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        source = session.get(Account, source_id)
        assert source is not None
        source.balance_cents += 1
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        with pytest.raises(PolicyLifecycleError, match="previous balance"):
            project_execution(session, operation, SEED_AS_OF)
        assert (
            session.scalar(select(ActionReceipt).where(ActionReceipt.action_plan_id == action_id))
            is None
        )


def test_projection_failure_rolls_back_earlier_cash_writes_even_when_caller_catches(
    boundary_engine: Engine,
) -> None:
    action_id, position_id = purchase_command(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, action_id)
        assert action is not None
        session.add(
            AssetPosition(
                id=position_id,
                user_id=DEMO_USER_ID,
                account_id=action.source_account_id,
                product_id=action.product_id,
                principal_cents=1,
                purchased_at=SEED_AS_OF,
                status="HELD",
            )
        )
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        before = {a.id: a.balance_cents for a in session.scalars(select(Account))}
        with pytest.raises(PolicyLifecycleError, match="overwrite"):
            project_execution(session, operation, SEED_AS_OF)
        assert {a.id: a.balance_cents for a in session.scalars(select(Account))} == before
        assert (
            session.scalar(select(ActionReceipt).where(ActionReceipt.action_plan_id == action_id))
            is None
        )


def zero_goal_income_setup(engine: Engine) -> tuple[UUID, UUID]:
    """Trusted zero-goal/source import only: no ActionPlan, reservation or payment."""
    with Session(engine) as session, session.begin():
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "goal_saving",
                "name": "New funds",
                "target_cents": 100000,
                "deadline": "2026-12-31",
                "monthly_contribution": {"min_cents": 0, "target_cents": 10000, "max_cents": 20000},
            },
            now=SEED_AS_OF - timedelta(days=40),
        )
        goal = create_goal_projection(
            session, DEMO_USER_ID, policy_id, version_id, cash.id, SEED_AS_OF
        ).goal
        open_execution_anchors(session, DEMO_USER_ID, SEED_AS_OF, goal_balances={goal.id: (0, 0)})
        source_ledger(session, available_cents=100000)
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, goal.id)
        return goal.id, cash.id


def goal_command(engine: Engine) -> tuple[UUID, UUID, UUID]:
    goal_id, cash_id = zero_goal_income_setup(engine)
    with Session(engine) as session, session.begin():
        goal, cash = session.get(Goal, goal_id), session.get(Account, cash_id)
        assert goal is not None and cash is not None
        policy_id, version_id = goal.policy_id, goal.policy_version_id
        state = read_income_state(session, DEMO_USER_ID, SEED_AS_OF)
        proof = session.get(EvidenceItem, state.evidence_id)
        assert proof is not None
        fragment = next(f for f in state.ledger.fragments if f.available_cents)
        use = IncomeUse(
            fragment_id=fragment.fragment_id,
            origin_transaction_id=fragment.origin_transaction_id,
            account_id=fragment.account_id,
            amount_cents=10000,
        )
        action = purchase_action(session, version_id, amount=10000)
        action.action_type, action.product_id, action.goal_id = "ALLOCATE_GOAL", None, goal.id
        action.status = "SUBMITTED"
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key=f"goal-allocation:{action.id}",
            action_type="ALLOCATE_GOAL",
            amount_cents=10000,
            cash_uses=[CashUse(account_id=cash.id, amount_cents=10000)],
            income_uses=[use],
            destination_account_id=cash.id,
            goal_id=goal.id,
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        action.request = {
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json"),
            "income_evidence": {"id": str(proof.id), "hash": proof.content_hash},
        }
        action.request_hash = configuration_hash(action.request)
        claims = [
            ResourceClaim(
                resource_kind="CASH",
                resource_key=str(cash.id),
                amount_cents=10000,
                capacity_cents=cash.balance_cents,
            ),
            ResourceClaim(
                resource_kind="INCOME",
                resource_key=str(fragment.fragment_id),
                amount_cents=10000,
                capacity_cents=100000,
            ),
            ResourceClaim(
                resource_kind="BUSINESS",
                resource_key=effect.business_key,
                amount_cents=1,
                capacity_cents=1,
            ),
        ]
        reserve_resources(session, DEMO_USER_ID, action.id, claims, SEED_AS_OF)
        reserve_income_for_action(
            session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", SEED_AS_OF
        )
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, action.id)
        return action.id, goal.id, cash.id


def test_same_account_goal_projection_assigns_origin_without_fabricating_cash_transfer(
    boundary_engine: Engine,
) -> None:
    action_id, goal_id, cash_id = goal_command(boundary_engine)
    with Session(boundary_engine) as session:
        cash = session.get(Account, cash_id)
        assert cash is not None
        balance = cash.balance_cents
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        receipt = project_execution(session, operation, SEED_AS_OF)
        assert receipt is not None and receipt.executed_cents == 10000
        goal, cash = session.get(Goal, goal_id), session.get(Account, cash_id)
        assert goal is not None and cash is not None
        assert goal.allocated_cents == 10000 and cash.balance_cents == balance
        assert not list(
            session.scalars(
                select(Transaction).where(
                    Transaction.source_ref.like(f"bank-operation:{action_id}:%")
                )
            )
        )
        ownership = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == OWNERSHIP_SOURCE, EvidenceItem.status == "VALID"
            )
        )
        month = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == CONTRIBUTION_SOURCE, EvidenceItem.status == "VALID"
            )
        )
        assert (
            ownership is not None
            and ownership.content["cash_owned_cents"] == 10000
            and ownership.content["principal_owned_cents"] == 0
        )
        assert month is not None and month.content["contributed_cents"] == 10000
        state = read_income_state(session, DEMO_USER_ID, SEED_AS_OF)
        assert sum(f.assigned_cents for f in state.ledger.fragments) == 10000
        assert sum(f.available_cents for f in state.ledger.fragments) == 90000


def test_projection_cannot_launder_rehashed_boolean_goal_money(boundary_engine: Engine) -> None:
    action_id, goal_id, _ = goal_command(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == OWNERSHIP_SOURCE,
                EvidenceItem.content["goal_id"].as_string() == str(goal_id),
                EvidenceItem.status == "VALID",
            )
        )
        assert proof is not None
        proof.content = {**proof.content, "principal_owned_cents": False}
        flag_modified(proof, "content")
        proof.content_hash = configuration_hash(proof.content)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        with pytest.raises(PolicyLifecycleError, match="Goal ownership"):
            project_execution(session, operation, SEED_AS_OF)


def test_card_projection_reduces_unpaid_debt_and_updates_its_bound_evidence(
    boundary_engine: Engine,
) -> None:
    action_id, bill_id = card_payment_command(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        receipt = project_execution(session, operation, SEED_AS_OF)
        bill = session.get(CreditCardBill, bill_id)
        assert bill is not None and receipt is not None
        assert bill.paid_cents == bill.total_cents == receipt.executed_cents
        assert bill.status == "PAID"
        proof = session.get(EvidenceItem, bill.evidence_id)
        assert (
            proof is not None
            and proof.status == "VALID"
            and proof.content["paid_cents"] == bill.total_cents
        )
        assert proof.content["status"] == "PAID"
        again = project_execution(session, operation, SEED_AS_OF)
        assert again is not None and again.id == receipt.id


@pytest.mark.parametrize("delay,loss", [(0, 0), (0, 1000), (1, 0)])
def test_redemption_projects_only_settled_net_principal_and_preserves_pending_claims(
    boundary_engine: Engine, delay: int, loss: int
) -> None:
    action_id, position_id, principal = redemption_command(boundary_engine, delay=delay, loss=loss)
    with Session(boundary_engine) as session:
        before = {a.id: a.balance_cents for a in session.scalars(select(Account))}
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    if delay:
        with Session(boundary_engine) as session, session.begin():
            operation = session.get(BankOperation, action_id)
            assert operation is not None and operation.status == "ACCEPTED"
            assert project_execution(session, operation, SEED_AS_OF) is None
            assert {a.id: a.balance_cents for a in session.scalars(select(Account))} == before
            assert all(
                r.status == "RESERVED"
                for r in session.scalars(
                    select(ActionResourceReservation).where(
                        ActionResourceReservation.action_plan_id == action_id
                    )
                )
            )
        process_operation(
            boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF + timedelta(days=delay)
        )
    now = SEED_AS_OF + timedelta(days=delay)
    with Session(boundary_engine) as session, session.begin():
        operation = session.get(BankOperation, action_id)
        assert operation is not None
        receipt = project_execution(session, operation, now)
        assert receipt is not None and receipt.executed_cents == principal
        assert receipt.loss_cents == loss and receipt.fee_cents == (100 if loss else 0)
        position = session.get(AssetPosition, position_id)
        assert (
            position is not None
            and position.status == "REDEEMED"
            and position.principal_cents == principal
        )
        after = sum(a.balance_cents for a in session.scalars(select(Account)))
        assert after - sum(before.values()) == principal - loss - (100 if loss else 0)
        transaction = session.scalar(
            select(Transaction).where(Transaction.source_ref.like(f"bank-operation:{action_id}:%"))
        )
        assert transaction is not None and transaction.amount_cents == principal - loss - (
            100 if loss else 0
        )
        proof = session.get(EvidenceItem, transaction.evidence_id)
        assert proof is not None and proof.content["economic_role"] == "PRINCIPAL_RETURN"
        assert (
            session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == LEDGER_SOURCE, EvidenceItem.status == "VALID"
                )
            )
            is None
        )
        again = project_execution(session, operation, now)
        assert again is not None and again.id == receipt.id
