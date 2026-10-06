"""Independent economic effects on PostgreSQL, before application projection."""

import json
from datetime import date, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    AssetPosition,
    AssetProduct,
    BankOperation,
    CreditCardBill,
    DecisionRun,
    EvidenceItem,
    Goal,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, BillReference, CashUse, ExecutionEffect
from app.domain.income_ledger import (
    IncomeFragment,
    IncomeLedger,
    IncomeOrigin,
    IncomeUse,
    location_id,
)
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.services.asset_allocation import preview_asset_allocation
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_bank import open_execution_anchors, process_operation
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_reservations import ResourceClaim, reserve_resources, resolve_resources
from app.services.execution_sources import execution_return_account, load_execution_quote
from app.services.income_ledger import reserve_income_for_action
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import BankRequest, open_simulated_bank, process_redemption
from app.tests.test_asset_allocation_service import authorization, purchase_action
from app.tests.test_boundary_service import boundary_engine, confirmed_policy, imported_proof
from app.tests.test_recovery_service import multiple_recovery_fixture, recovery_fixture
from app.tests.test_simulated_bank import bank_action
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def source_ledger(session: Session, *, available_cents: int = 0) -> EvidenceItem:
    """Explicit trusted fixture import; existing cash and original bank income never change."""
    origins: list[IncomeOrigin] = []
    fragments: list[IncomeFragment] = []
    for transaction in session.scalars(
        select(Transaction)
        .where(Transaction.direction == "CREDIT")
        .order_by(Transaction.occurred_at.desc())
    ):
        proof = session.get(EvidenceItem, transaction.evidence_id)
        if proof is None or proof.content.get("economic_role") != "INCOME":
            continue
        origins.append(
            IncomeOrigin(
                origin_transaction_id=transaction.id,
                origin_account_id=transaction.account_id,
                amount_cents=transaction.amount_cents,
                occurred_at=transaction.occurred_at,
                observed_at=transaction.observed_at,
                bank_evidence_id=proof.id,
                bank_evidence_hash=proof.content_hash,
            )
        )
        available = available_cents if len(origins) == 1 else 0
        fragments.append(
            IncomeFragment(
                fragment_id=location_id(transaction.id, transaction.account_id),
                origin_transaction_id=transaction.id,
                account_id=transaction.account_id,
                spent_cents=transaction.amount_cents - available,
                available_cents=available,
            )
        )
    ledger = IncomeLedger(
        user_id=DEMO_USER_ID,
        as_of=SEED_AS_OF,
        scope_account_ids=tuple(
            session.scalars(
                select(Account.id).where(Account.account_type == "CASH").order_by(Account.id)
            )
        ),
        origins=tuple(origins),
        fragments=tuple(fragments),
    )
    open_execution_anchors(session, DEMO_USER_ID, SEED_AS_OF, income_ledger=ledger)
    content = ledger.model_dump(mode="json")
    proof = EvidenceItem(
        id=uuid4(),
        user_id=DEMO_USER_ID,
        created_at=SEED_AS_OF,
        evidence_level="BANK_CONFIRMED",
        source_type="SIMULATED_NEW_FUNDS_LEDGER",
        source_ref="explicit-complete-income-fixture",
        content=content,
        content_hash=configuration_hash(content),
        valid_from=SEED_AS_OF,
        observed_at=SEED_AS_OF,
        status="VALID",
    )
    session.add(proof)
    session.flush()
    return proof


def transfer_action(engine: Engine, amount: int = 10000) -> tuple[UUID, UUID, UUID]:
    with Session(engine) as session, session.begin():
        _, version = confirmed_policy(session, authorization())
        action = purchase_action(session, version, amount=amount)
        target = Account(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            external_ref=str(uuid4()),
            name="Second CASH",
            account_type="CASH",
            balance_cents=0,
            observed_at=SEED_AS_OF,
            created_at=SEED_AS_OF,
        )
        session.add(target)
        session.flush()
        balance = {
            "simulation": True,
            "user_id": str(DEMO_USER_ID),
            "account_id": str(target.id),
            "account_type": "CASH",
            "balance_cents": 0,
            "currency": "CNY",
            "as_of": SEED_AS_OF.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_BALANCE",
                source_ref=str(target.id),
                content=balance,
                content_hash=configuration_hash(balance),
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
                status="VALID",
            )
        )
        open_simulated_bank(
            session, DEMO_USER_ID, SEED_AS_OF, cash_balances={target.id: 0}, position_principals={}
        )
        income = source_ledger(session)
        source = session.get(Account, action.source_account_id)
        assert source is not None
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key="transfer:" + str(uuid4()),
            action_type="TRANSFER_INTERNAL",
            amount_cents=amount,
            cash_uses=[CashUse(account_id=source.id, amount_cents=amount)],
            destination_account_id=target.id,
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        digest = configuration_hash(effect.model_dump(mode="json"))
        proof_id = uuid5(action.id, "confirmation:" + digest)
        content = {
            "simulation": True,
            "user_id": str(DEMO_USER_ID),
            "action_id": str(action.id),
            "effect_hash": digest,
            "accepted": True,
            "confirmed_at": SEED_AS_OF.isoformat(),
            "valid_until": effect.expires_at.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=proof_id,
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type="USER_ACTION_CONFIRMATION",
                source_ref=str(action.id),
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
                observed_at=SEED_AS_OF,
                valid_from=SEED_AS_OF,
                valid_to=effect.expires_at,
            )
        )
        action.action_type, action.status = "TRANSFER_INTERNAL", "SUBMITTED"
        action.policy_version_id, action.product_id = None, None
        action.destination_account_id = target.id
        action.request = {
            "execution": BankCommand(effect=effect, effect_hash=digest).model_dump(mode="json"),
            "confirmation_evidence_id": str(proof_id),
            "income_evidence": {"id": str(income.id), "hash": income.content_hash},
        }
        action.request_hash = configuration_hash(action.request)
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="CASH",
                    resource_key=str(source.id),
                    amount_cents=amount,
                    capacity_cents=source.balance_cents,
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
        return action.id, source.id, target.id


def test_internal_transfer_is_independent_conserved_and_replayable(boundary_engine: Engine) -> None:
    action_id, source_id, target_id = transfer_action(boundary_engine)
    with Session(boundary_engine) as session:
        before = {row.id: row.balance_cents for row in session.scalars(select(Account))}
    result = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert result.status == "SETTLED"
    assert result == process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        assert {row.id: row.balance_cents for row in session.scalars(select(Account))} == before
        operation = session.get(BankOperation, result.operation_id)
        assert operation is not None and operation.action_plan_id == action_id
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == result.operation_id
                )
            )
        )
        assert len(postings) == 2
        assert {row.account_id: row.delta_cents for row in postings} == {
            source_id: -10000,
            target_id: 10000,
        }


def test_changed_economic_content_cannot_reuse_the_same_key(boundary_engine: Engine) -> None:
    action_id, _, _ = transfer_action(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, action_id)
        assert action is not None
        payload = dict(action.request)
        command = BankCommand.model_validate_json(json.dumps(payload["execution"]))
        altered = command.effect.model_copy(update={"business_key": "different-business"})
        payload["execution"] = BankCommand(
            effect=altered, effect_hash=configuration_hash(altered.model_dump(mode="json"))
        ).model_dump(mode="json")
        action.request, action.request_hash = payload, configuration_hash(payload)
    with pytest.raises(PolicyLifecycleError):
        process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)


def purchase_command(engine: Engine) -> tuple[UUID, UUID]:
    with Session(engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
        allocation = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        exits = {item.product_id: item.exit_plan for item in allocation.allocation.candidates}
        action = purchase_action(session, version_id, amount=50000)
        income = source_ledger(session)
        product = session.get(AssetProduct, action.product_id)
        assert product is not None
        exit_plan = exits[product.id]
        assert exit_plan is not None
        position_id = uuid4()
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key="purchase:" + str(action.id),
            action_type="PURCHASE_ASSET",
            amount_cents=50000,
            cash_uses=[CashUse(account_id=action.source_account_id, amount_cents=50000)],
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            product_id=product.id,
            product_version_number=product.version_number,
            terms_digest=configuration_hash(product.maturity_rule),
            position_id=position_id,
            position_account_id=action.source_account_id,
            return_account_id=action.source_account_id,
            purchase_exit=exit_plan,
            latest_arrival_at=exit_plan.principal_available_at + timedelta(minutes=15),
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        action.status, action.autonomy_level = "SUBMITTED", "AUTO_EXECUTE"
        action.request = {
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json"),
            "income_evidence": {"id": str(income.id), "hash": income.content_hash},
        }
        action.request_hash = configuration_hash(action.request)
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="CASH",
                    resource_key=str(action.source_account_id),
                    amount_cents=50000,
                    capacity_cents=1000000,
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
        return action.id, position_id


def test_purchase_bank_position_exists_before_application_projection(
    boundary_engine: Engine,
) -> None:
    action_id, position_id = purchase_command(boundary_engine)
    result = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert result.status == "SETTLED"
    with Session(boundary_engine) as session:
        assert session.get(AssetPosition, position_id) is None
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == result.operation_id
                )
            )
        )
        assert len(postings) == 2 and sum(row.delta_cents for row in postings) == 0
        assert {row.position_id for row in postings if row.delta_cents > 0} == {position_id}


def test_bank_rejects_current_policy_revocation_after_application_submission(
    boundary_engine: Engine,
) -> None:
    from app.services.policy_lifecycle import revoke_policy

    action_id, _ = purchase_command(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, action_id)
        assert action is not None
        version = session.get(PolicyVersion, action.policy_version_id)
        assert version is not None
        revoke_policy(session, DEMO_USER_ID, version.policy_id, version.id, SEED_AS_OF)
    with pytest.raises(PolicyLifecycleError):
        process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        assert session.scalar(select(BankOperation.id)) is None


def card_payment_command(engine: Engine) -> tuple[UUID, UUID]:
    with Session(engine) as session, session.begin():
        bill = session.scalar(select(CreditCardBill).where(CreditCardBill.status == "UNPAID"))
        assert bill is not None and bill.evidence_id is not None
        bill.due_date = date(2026, 10, 4)
        bill_proof = session.get(EvidenceItem, bill.evidence_id)
        assert bill_proof is not None
        bill_proof.content = {**bill_proof.content, "due_date": bill.due_date.isoformat()}
        bill_proof.content_hash = configuration_hash(bill_proof.content)
        payee = "credit-card:" + str(bill.account_id)
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": payee,
                "due_day": 4,
                "amount_rule": {"kind": "bill_balance", "account_id": str(bill.account_id)},
                "auto_execute": True,
            },
        )
        amount = bill.total_cents - bill.paid_cents
        income = source_ledger(session)
        action = purchase_action(session, version_id, amount=amount)
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key="bill:" + str(bill.id),
            action_type="PAY_RECURRING",
            amount_cents=amount,
            cash_uses=[CashUse(account_id=action.source_account_id, amount_cents=amount)],
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            liability=BillReference(bill_id=bill.id, evidence_ids=[bill.evidence_id]),
            payee_id=payee,
            payee_evidence_id=bill.evidence_id,
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        action.action_type, action.status, action.product_id = "PAY_RECURRING", "SUBMITTED", None
        action.request = {
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json"),
            "income_evidence": {"id": str(income.id), "hash": income.content_hash},
        }
        action.request_hash = configuration_hash(action.request)
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="CASH",
                    resource_key=str(action.source_account_id),
                    amount_cents=amount,
                    capacity_cents=1000000,
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
        action_id, bill_id = action.id, bill.id
    return action_id, bill_id


def test_card_payment_records_independent_remaining_debt_and_paid_facts(
    boundary_engine: Engine,
) -> None:
    action_id, bill_id = card_payment_command(boundary_engine)
    with Session(boundary_engine) as session:
        bill = session.get(CreditCardBill, bill_id)
        assert bill is not None
        amount = bill.total_cents - bill.paid_cents
    result = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        rows = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == result.operation_id,
                    SimulatedBankPosting.ledger_dimension == "LIABILITY",
                )
            )
        )
        assert len(rows) == 2
        assert sorted(row.delta_cents for row in rows) == [-amount, amount]
        bill = session.get(CreditCardBill, bill_id)
        assert bill is not None and bill.paid_cents == 0


def goal_allocation_command(engine: Engine) -> tuple[UUID, UUID]:
    with Session(engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(
            session,
            {
                "type": "goal_saving",
                "target_cents": 1000000,
                "deadline": "2026-12-31",
                "monthly_contribution": {
                    "min_cents": 0,
                    "target_cents": 50000,
                    "max_cents": 100000,
                },
            },
            SEED_AS_OF - timedelta(days=40),
        )
        account = session.scalar(select(Account).where(Account.account_type == "GOAL"))
        assert account is not None
        goal = Goal(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            policy_id=policy_id,
            policy_version_id=version_id,
            account_id=account.id,
            name="Bank allocation fixture",
            target_cents=1000000,
            allocated_cents=160000,
            deadline=date(2026, 12, 31),
            monthly_min_cents=0,
            monthly_target_cents=50000,
            monthly_max_cents=100000,
            created_at=SEED_AS_OF,
        )
        session.add(goal)
        session.flush()
        imported_proof(
            session,
            "SIMULATED_GOAL_OWNERSHIP",
            {
                "protocol": "goal-ownership-v1",
                "goal_id": str(goal.id),
                "policy_id": str(policy_id),
                "account_id": str(account.id),
                "allocated_cents": 160000,
                "cash_owned_cents": 160000,
                "principal_owned_cents": 0,
                "position_ids": [],
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
        imported_proof(
            session,
            "SIMULATED_GOAL_MONTH_CONTRIBUTION",
            {
                "protocol": "goal-month-contribution-v1",
                "goal_id": str(goal.id),
                "period": "2026-10",
                "contributed_cents": 0,
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
        open_execution_anchors(
            session, DEMO_USER_ID, SEED_AS_OF, goal_balances={goal.id: (160000, 0)}
        )
        proof = source_ledger(session, available_cents=50000)
        ledger = IncomeLedger.model_validate_json(json.dumps(proof.content))
        fragment = next(row for row in ledger.fragments if row.available_cents)
        use = IncomeUse(
            fragment_id=fragment.fragment_id,
            origin_transaction_id=fragment.origin_transaction_id,
            account_id=fragment.account_id,
            amount_cents=50000,
        )
        action = purchase_action(session, version_id, amount=50000)
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key="goal:" + str(action.id),
            action_type="ALLOCATE_GOAL",
            amount_cents=50000,
            cash_uses=[CashUse(account_id=action.source_account_id, amount_cents=50000)],
            income_uses=[use],
            goal_id=goal.id,
            destination_account_id=goal.account_id,
            policy_id=policy_id,
            policy_version_id=version_id,
            policy_version_ids=[version_id],
            valid_from=SEED_AS_OF,
            expires_at=SEED_AS_OF + timedelta(minutes=15),
        )
        action.goal_id, action.destination_account_id = goal.id, goal.account_id
        action.action_type, action.status, action.product_id = "ALLOCATE_GOAL", "SUBMITTED", None
        action.request = {
            "execution": BankCommand(
                effect=effect, effect_hash=execution_effect_hash(effect)
            ).model_dump(mode="json"),
            "income_evidence": {"id": str(proof.id), "hash": proof.content_hash},
        }
        action.request_hash = configuration_hash(action.request)
        reserve_income_for_action(
            session, DEMO_USER_ID, action.id, [use], "ALLOCATE_GOAL", SEED_AS_OF
        )
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="CASH",
                    resource_key=str(action.source_account_id),
                    amount_cents=50000,
                    capacity_cents=50000,
                ),
                ResourceClaim(
                    resource_kind="INCOME",
                    resource_key=str(fragment.fragment_id),
                    amount_cents=50000,
                    capacity_cents=50000,
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
        action_id, goal_id = action.id, goal.id
    return action_id, goal_id


def test_goal_allocation_bank_changes_ownership_and_income_without_app_projection(
    boundary_engine: Engine,
) -> None:
    action_id, goal_id = goal_allocation_command(boundary_engine)
    result = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert result.status == "SETTLED"
    with Session(boundary_engine) as session:
        goal = session.get(Goal, goal_id)
        assert goal is not None and goal.allocated_cents == 160000
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == action_id)
            )
        )
        income = [row for row in postings if row.ledger_dimension == "INCOME_LOCATION"]
        assert len(income) == 2 and sum(row.delta_cents for row in income) == 0
        owned = [row for row in postings if row.ledger_dimension == "GOAL_OWNERSHIP"]
        assert len(owned) == 1 and owned[0].delta_cents == 50000


def redemption_command(engine: Engine, *, delay: int = 0, loss: int = 0) -> tuple[UUID, UUID, int]:
    policy_id, position_id, principal = recovery_fixture(engine, delay=delay)
    with Session(engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.policy_version_id is not None
        product = session.get(AssetProduct, position.product_id)
        assert product is not None
        if loss:
            # Build a distinct historical, explicitly permitted purchase fixture.
            # Do not mutate an immutable policy version or adopt a manual holding.
            policy_id, permitted_version_id = confirmed_policy(
                session,
                authorization(allow_early_withdrawal_with_penalty=True),
                SEED_AS_OF - timedelta(days=40),
            )
            proof = session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                    EvidenceItem.content["position_id"].as_string() == str(position_id),
                    EvidenceItem.status == "VALID",
                )
            )
            assert proof is not None
            purchase = session.get(ActionPlan, UUID(proof.content["purchase_action_id"]))
            assert purchase is not None
            run = session.get(DecisionRun, purchase.decision_run_id)
            assert run is not None
            purchase.policy_version_id = permitted_version_id
            run.policy_version_ids = [str(permitted_version_id)]
            position.policy_version_id = permitted_version_id
            proof.content = {**proof.content, "policy_version_id": str(permitted_version_id)}
            proof.content_hash = configuration_hash(proof.content)
            session.flush()
        destination = execution_return_account(session, DEMO_USER_ID, position, SEED_AS_OF)
        if loss:
            quote = RecoveryQuote(
                quote_id=uuid4(),
                user_id=DEMO_USER_ID,
                position_id=position.id,
                product_id=product.id,
                product_version_number=product.version_number,
                terms_digest=configuration_hash(product.maturity_rule),
                kind="EARLY_WITHDRAW",
                principal_cents=principal,
                fee_cents=100,
                loss_cents=loss,
                net_cents=principal - loss - 100,
                request_at=SEED_AS_OF,
                principal_available_at=SEED_AS_OF + timedelta(days=delay),
                expires_at=SEED_AS_OF + timedelta(minutes=15),
            )
            content = {
                "simulation": True,
                "protocol": "recovery-quote-v1",
                "user_id": str(DEMO_USER_ID),
                "position_id": str(position_id),
                "destination_account_id": str(destination),
                "goal_id": None,
                "quote": quote.model_dump(mode="json"),
            }
            session.add(
                EvidenceItem(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    created_at=SEED_AS_OF,
                    evidence_level="BANK_CONFIRMED",
                    source_type="SIMULATED_REDEMPTION_QUOTE",
                    source_ref=str(quote.quote_id),
                    content=content,
                    content_hash=configuration_hash(content),
                    valid_from=SEED_AS_OF,
                    observed_at=SEED_AS_OF,
                    status="VALID",
                )
            )
            session.flush()
        quote = load_execution_quote(session, DEMO_USER_ID, position_id, SEED_AS_OF)
        action = purchase_action(session, position.policy_version_id, amount=principal)
        effect = ExecutionEffect(
            operation_id=action.id,
            user_id=DEMO_USER_ID,
            business_key="close:" + str(position_id),
            action_type="REDEEM_ASSET",
            amount_cents=principal,
            destination_account_id=destination,
            policy_id=policy_id,
            policy_version_id=position.policy_version_id,
            policy_version_ids=[position.policy_version_id],
            product_id=product.id,
            product_version_number=product.version_number,
            terms_digest=configuration_hash(product.maturity_rule),
            position_id=position_id,
            position_account_id=position.account_id,
            original_policy_version_id=position.policy_version_id,
            fee_cents=quote.fee_cents,
            loss_cents=quote.loss_cents,
            net_cents=quote.net_cents,
            quote_id=quote.quote_id,
            settlement_delay_days=delay,
            latest_arrival_at=quote.principal_available_at,
            valid_from=SEED_AS_OF,
            expires_at=quote.expires_at,
        )
        digest = execution_effect_hash(effect)
        confirmation_id = uuid5(action.id, "confirmation:" + digest)
        if loss:
            content = {
                "simulation": True,
                "user_id": str(DEMO_USER_ID),
                "action_id": str(action.id),
                "effect_hash": digest,
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
                    content=content,
                    content_hash=configuration_hash(content),
                    valid_from=SEED_AS_OF,
                    valid_to=effect.expires_at,
                    observed_at=SEED_AS_OF,
                    status="VALID",
                )
            )
        action.action_type, action.status, action.autonomy_level = (
            "ASSET_REDEEM",
            "SUBMITTED",
            "ASK_ONCE" if loss else "AUTO_EXECUTE",
        )
        action.source_account_id = position.account_id
        action.destination_account_id = destination if destination != position.account_id else None
        action.position_id, action.product_id = position_id, product.id
        action.request = {
            "execution": BankCommand(effect=effect, effect_hash=digest).model_dump(mode="json"),
            "confirmation_evidence_id": str(confirmation_id),
        }
        action.request_hash = configuration_hash(action.request)
        reserve_resources(
            session,
            DEMO_USER_ID,
            action.id,
            [
                ResourceClaim(
                    resource_kind="POSITION",
                    resource_key=str(position_id),
                    amount_cents=principal,
                    capacity_cents=principal,
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
        return action.id, position_id, principal


def test_loss_confirmation_settles_net_fee_and_loss_once(boundary_engine: Engine) -> None:
    action_id, _, principal = redemption_command(boundary_engine, loss=1000)
    result = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert result.status == "SETTLED"
    assert result == process_operation(
        boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF + timedelta(minutes=1)
    )
    with Session(boundary_engine) as session:
        rows = list(
            session.scalars(
                select(SimulatedBankPosting).where(SimulatedBankPosting.operation_id == action_id)
            )
        )
        assert all(row.leg_ref is not None for row in rows)
        assert {
            row.leg_ref: row.delta_cents
            for row in rows
            if row.leg_ref is not None and not row.leg_ref.startswith("cash:")
        } == {"position": -principal, "fee": 100, "loss": 1000}
        assert sum(row.delta_cents for row in rows) == 0


def test_accepted_t1_operation_settles_original_effect_after_policy_revocation(
    boundary_engine: Engine,
) -> None:
    from app.services.policy_lifecycle import revoke_policy

    action_id, _, _ = redemption_command(boundary_engine, delay=1)
    accepted = process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    assert accepted.status == "ACCEPTED" and accepted.posting_ids == []
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, action_id)
        assert action is not None
        version = session.get(PolicyVersion, action.policy_version_id)
        assert version is not None
        revoke_policy(
            session, DEMO_USER_ID, version.policy_id, version.id, SEED_AS_OF + timedelta(minutes=1)
        )
    settled = process_operation(
        boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF + timedelta(days=1)
    )
    assert settled.status == "SETTLED" and len(settled.posting_ids) == 2


@pytest.mark.parametrize("entry", ["generic", "legacy"])
def test_late_t1_observation_keeps_promised_economic_time(
    boundary_engine: Engine, entry: str
) -> None:
    if entry == "generic":
        action_id, _, _ = redemption_command(boundary_engine, delay=1)
        process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    else:
        action_id, _, _, _ = bank_action(boundary_engine, delay=1)
        process_redemption(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    observed_at = SEED_AS_OF + timedelta(days=2)
    due = SEED_AS_OF + timedelta(days=1)
    if entry == "generic":
        process_operation(boundary_engine, DEMO_USER_ID, action_id, observed_at)
    else:
        process_redemption(boundary_engine, DEMO_USER_ID, action_id, observed_at)
    with Session(boundary_engine) as session:
        operation = session.scalars(
            select(BankOperation).where(BankOperation.action_plan_id == action_id)
        ).one()
        assert operation.status == "SETTLED" and operation.settled_at == due
        rows = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.operation_id == operation.id
                )
            )
        )
        assert len(rows) == 2
        assert all(r.occurred_at == due and r.created_at == observed_at for r in rows)
        if entry == "legacy":
            legacy = session.get(SimulatedBankRedemption, operation.legacy_redemption_id)
            assert legacy is not None and legacy.settled_at == due


def test_new_bank_mutation_waits_for_earlier_due_accepted_economics(
    boundary_engine: Engine,
) -> None:
    multiple_recovery_fixture(boundary_engine, second_delay=1)
    with Session(boundary_engine) as session:
        positions = list(
            session.scalars(
                select(AssetPosition)
                .where(AssetPosition.policy_version_id.is_not(None))
                .order_by(AssetPosition.id)
            )
        )
        assert len(positions) == 2
        first_id, second_id = positions[0].id, positions[1].id
    first_action = legacy_competitor(boundary_engine, first_id)
    accepted = process_redemption(boundary_engine, DEMO_USER_ID, first_action, SEED_AS_OF)
    assert accepted.status == "ACCEPTED"
    second_action = legacy_competitor(boundary_engine, second_id)
    late = SEED_AS_OF + timedelta(days=2)
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, second_action)
        assert action is not None
        command = BankRequest.model_validate(action.request["bank_request"])
        action.request = {
            "bank_request": command.model_copy(
                update={
                    "requested_at": late,
                    "available_at": late,
                    "expires_at": late + timedelta(minutes=15),
                }
            ).model_dump(mode="json")
        }
        action.request_hash = configuration_hash(action.request)
    with pytest.raises(PolicyLifecycleError, match="Earlier accepted bank settlement"):
        process_redemption(boundary_engine, DEMO_USER_ID, second_action, late)
    with Session(boundary_engine) as session:
        assert (
            session.scalar(
                select(BankOperation).where(BankOperation.action_plan_id == second_action)
            )
            is None
        )
        assert (
            list(
                session.scalars(
                    select(SimulatedBankPosting).where(SimulatedBankPosting.entry_kind != "OPENING")
                )
            )
            == []
        )
    settled = process_redemption(boundary_engine, DEMO_USER_ID, first_action, late)
    assert settled.status == "SETTLED"
    next_settled = process_redemption(boundary_engine, DEMO_USER_ID, second_action, late)
    assert next_settled.status == "SETTLED"


@pytest.mark.parametrize("field", ["source_account_id", "destination_account_id"])
def test_bank_rejects_action_account_projection_different_from_frozen_redemption(
    boundary_engine: Engine,
    field: str,
) -> None:
    action_id, position_id, _ = redemption_command(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        action = session.get(ActionPlan, action_id)
        position = session.get(AssetPosition, position_id)
        assert action is not None and position is not None
        if field == "source_account_id":
            alternate = session.scalar(select(Account).where(Account.account_type == "GOAL"))
            assert alternate is not None
            action.source_account_id = alternate.id
        else:
            action.destination_account_id = None
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, action_id)
    with pytest.raises(PolicyLifecycleError):
        process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session:
        assert session.get(BankOperation, action_id) is None


def legacy_competitor(engine: Engine, position_id: UUID) -> UUID:
    with Session(engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.policy_version_id is not None
        destination = execution_return_account(session, DEMO_USER_ID, position, SEED_AS_OF)
        action = purchase_action(
            session, position.policy_version_id, amount=position.principal_cents
        )
        action.action_type, action.status, action.autonomy_level = (
            "ASSET_REDEEM",
            "SUBMITTED",
            "AUTO_EXECUTE",
        )
        action.source_account_id = position.account_id
        action.product_id, action.position_id = position.product_id, position.id
        action.request = {
            "bank_request": BankRequest(
                user_id=DEMO_USER_ID,
                position_id=position.id,
                position_account_id=position.account_id,
                product_id=position.product_id,
                goal_id=position.goal_id,
                original_policy_version_id=position.policy_version_id,
                destination_account_id=destination,
                principal_cents=position.principal_cents,
                requested_at=SEED_AS_OF,
                available_at=SEED_AS_OF + timedelta(days=1),
                expires_at=SEED_AS_OF + timedelta(minutes=15),
            ).model_dump(mode="json")
        }
        action.request_hash = configuration_hash(action.request)
        return action.id


def test_legacy_entry_cannot_take_a_position_already_reserved_by_generic_action(
    boundary_engine: Engine,
) -> None:
    _, position_id, _ = redemption_command(boundary_engine, delay=1)
    competitor = legacy_competitor(boundary_engine, position_id)
    with pytest.raises(PolicyLifecycleError, match="reserved"):
        process_redemption(boundary_engine, DEMO_USER_ID, competitor, SEED_AS_OF)


def test_unified_close_survives_consumed_claims_and_blocks_legacy_duplicate(
    boundary_engine: Engine,
) -> None:
    action_id, position_id, _ = redemption_command(boundary_engine)
    process_operation(boundary_engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        resolve_resources(session, DEMO_USER_ID, action_id, "CONSUMED", SEED_AS_OF)
    competitor = legacy_competitor(boundary_engine, position_id)
    with pytest.raises(PolicyLifecycleError, match="closing operation"):
        process_redemption(boundary_engine, DEMO_USER_ID, competitor, SEED_AS_OF)


def test_generic_claim_cannot_reserve_legacy_accepted_principal(boundary_engine: Engine) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine, delay=1)
    accepted_action = legacy_competitor(boundary_engine, position_id)
    assert (
        process_redemption(boundary_engine, DEMO_USER_ID, accepted_action, SEED_AS_OF).status
        == "ACCEPTED"
    )
    with Session(boundary_engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.policy_version_id is not None
        action = purchase_action(session, position.policy_version_id)
        with pytest.raises(PolicyLifecycleError), session.begin_nested():
            reserve_resources(
                session,
                DEMO_USER_ID,
                action.id,
                [
                    ResourceClaim(
                        resource_kind="POSITION",
                        resource_key=str(position_id),
                        amount_cents=principal,
                        capacity_cents=principal,
                    )
                ],
                SEED_AS_OF,
            )
