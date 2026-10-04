"""Actual prepared/confirmed/executed application transactions on PostgreSQL."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionResourceReservation,
    BankOperation,
    EvidenceItem,
    Goal,
)
from app.domain.execution_types import OccurrenceReference
from app.domain.income_ledger import LEDGER_SOURCE
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ConfirmActionRequest,
    GoalIntent,
    PaymentIntent,
    PrepareActionRequest,
    PurchaseIntent,
    RedeemIntent,
    TransferIntent,
)
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution import confirm_action, execute_action, get_action, prepare_action
from app.services.execution_exposure import refresh_execution_exposure
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import open_simulated_bank
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_boundary_service import (
    boundary_engine,
    confirmed_policy,
    imported_proof,
    snapshot,
)
from app.tests.test_execution_bank import source_ledger
from app.tests.test_execution_projection import zero_goal_income_setup
from app.tests.test_recovery_service import recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def transfer_accounts(engine: Engine) -> tuple[UUID, UUID]:
    """Explicit trusted synthetic fixture import, independent of normal execution."""
    with Session(engine) as session, session.begin():
        source = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        current_income = session.scalar(
            select(EvidenceItem.id).where(
                EvidenceItem.user_id == DEMO_USER_ID,
                EvidenceItem.source_type == LEDGER_SOURCE,
                EvidenceItem.status != "SUPERSEDED",
            )
        )
        # Validate the complete original CASH scope before adding the actual zero-balance account.
        income_before = (
            read_income_state(session, DEMO_USER_ID, SEED_AS_OF)
            if current_income is not None
            else None
        )
        target = Account(
            id=uuid4(),
            user_id=DEMO_USER_ID,
            created_at=SEED_AS_OF,
            external_ref="301-second-cash",
            name="模拟第二活期",
            account_type="CASH",
            bank_code="ICBC",
            currency="CNY",
            balance_cents=0,
            observed_at=SEED_AS_OF,
        )
        session.add(target)
        session.flush()
        content = {
            "simulation": True,
            "user_id": str(DEMO_USER_ID),
            "account_id": str(target.id),
            "account_type": "CASH",
            "currency": "CNY",
            "balance_cents": 0,
            "as_of": SEED_AS_OF.isoformat(),
        }
        session.add(
            EvidenceItem(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                evidence_level="BANK_CONFIRMED",
                source_type="SIMULATED_BANK_BALANCE",
                source_ref="301-cash-import",
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
            )
        )
        open_simulated_bank(
            session, DEMO_USER_ID, SEED_AS_OF, cash_balances={target.id: 0}, position_principals={}
        )
        if income_before is not None:
            previous_income = session.get(EvidenceItem, income_before.evidence_id)
            assert previous_income is not None and previous_income.status == "VALID"
            original_content = deepcopy(previous_income.content)
            original_hash = previous_income.content_hash
            scope = tuple(sorted((*income_before.ledger.scope_account_ids, target.id)))
            successor_content = {
                **deepcopy(original_content),
                "scope_account_ids": [str(identity) for identity in scope],
            }
            # Trusted fixture import: only scope changes; no action or bank fact is invented.
            previous_income.status = "SUPERSEDED"
            session.add(
                EvidenceItem(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    created_at=SEED_AS_OF,
                    evidence_level="BANK_CONFIRMED",
                    source_type=LEDGER_SOURCE,
                    source_ref="explicit-transfer-cash-scope-fixture",
                    content=successor_content,
                    content_hash=configuration_hash(successor_content),
                    status="VALID",
                    valid_from=SEED_AS_OF,
                    observed_at=SEED_AS_OF,
                    supersedes_id=previous_income.id,
                )
            )
            session.flush()
            inherited = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
            assert inherited == income_before.ledger.model_copy(update={"scope_account_ids": scope})
            assert previous_income.content == original_content
            assert previous_income.content_hash == original_hash
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
        return source.id, target.id


def test_prepare_transfer_is_reviewable_idempotent_and_does_not_move_or_reserve_cash(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    request = PrepareActionRequest(
        idempotency_key="transfer-first",
        intent=TransferIntent(
            kind="transfer_internal",
            source_account_id=source,
            destination_account_id=target,
            amount_cents=10000,
        ),
    )
    with Session(boundary_engine) as session:
        before_cash = {row.id: row.balance_cents for row in session.scalars(select(Account))}
    first = prepare_action(boundary_engine, DEMO_USER_ID, request, SEED_AS_OF)
    assert first.status == "PLANNED" and first.autonomy_level == "ASK_ONCE"
    assert first.prepared_validation.status == "CONFIRMATION_REQUIRED"
    second = prepare_action(
        boundary_engine, DEMO_USER_ID, request, SEED_AS_OF + timedelta(minutes=1)
    )
    assert second.action_id == first.action_id and second.effect_hash == first.effect_hash
    assert second.effect == first.effect and first.receipt is None
    with Session(boundary_engine) as session:
        assert len(list(session.scalars(select(ActionPlan)))) == 1
        assert list(session.scalars(select(ActionResourceReservation))) == []
        assert list(session.scalars(select(BankOperation))) == []
    with Session(boundary_engine) as session:
        assert before_cash == {
            row.id: row.balance_cents for row in session.scalars(select(Account))
        }
    altered = request.model_copy(
        update={"intent": request.intent.model_copy(update={"amount_cents": 20000})}
    )
    with pytest.raises(PolicyLifecycleError, match="幂等"):
        prepare_action(boundary_engine, DEMO_USER_ID, altered, SEED_AS_OF)


def test_confirmation_binds_exact_consequences_without_cash_effect(boundary_engine: Engine) -> None:
    source, target = transfer_accounts(boundary_engine)
    request = PrepareActionRequest(
        idempotency_key="confirm-transfer",
        intent=TransferIntent(
            kind="transfer_internal",
            source_account_id=source,
            destination_account_id=target,
            amount_cents=10000,
        ),
    )
    action = prepare_action(boundary_engine, DEMO_USER_ID, request, SEED_AS_OF)
    before = snapshot(boundary_engine)
    with pytest.raises(PolicyLifecycleError):
        confirm_action(
            boundary_engine,
            DEMO_USER_ID,
            action.action_id,
            ConfirmActionRequest(effect_hash="f" * 64, accepted=True),
            SEED_AS_OF,
        )
    assert snapshot(boundary_engine) == before
    body = ConfirmActionRequest(effect_hash=action.effect_hash, accepted=True)
    result = confirm_action(
        boundary_engine, DEMO_USER_ID, action.action_id, body, SEED_AS_OF + timedelta(minutes=1)
    )
    assert result.status == "AUTHORIZED" and result.effect_hash == action.effect_hash
    replay = confirm_action(
        boundary_engine, DEMO_USER_ID, action.action_id, body, SEED_AS_OF + timedelta(minutes=2)
    )
    assert replay.effect == result.effect
    with Session(boundary_engine) as session:
        assert (
            len(
                list(
                    session.scalars(
                        select(EvidenceItem).where(
                            EvidenceItem.source_type == "USER_ACTION_CONFIRMATION"
                        )
                    )
                )
            )
            == 1
        )
        assert not list(session.scalars(select(BankOperation)))
        assert not list(session.scalars(select(ActionResourceReservation)))


def test_confirmed_transfer_commits_and_replays_one_independent_economic_effect(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    request = PrepareActionRequest(
        idempotency_key="execute-transfer",
        intent=TransferIntent(
            kind="transfer_internal",
            source_account_id=source,
            destination_account_id=target,
            amount_cents=10000,
        ),
    )
    prepared = prepare_action(boundary_engine, DEMO_USER_ID, request, SEED_AS_OF)
    with Session(boundary_engine) as session:
        before_cash = {row.id: row.balance_cents for row in session.scalars(select(Account))}
    before = snapshot(boundary_engine)
    with pytest.raises(PolicyLifecycleError):
        execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert snapshot(boundary_engine) == before
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    result = execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert result.status == "SUCCEEDED" and result.bank_status == "SETTLED"
    assert result.receipt is not None and result.receipt.executed_cents == 10000
    done = snapshot(boundary_engine)
    again = execute_action(
        boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF + timedelta(days=1)
    )
    assert again.receipt == result.receipt and snapshot(boundary_engine) == done
    with Session(boundary_engine) as session:
        source_account, target_account = session.get(Account, source), session.get(Account, target)
        assert source_account is not None and target_account is not None
        assert source_account.balance_cents == before_cash[source] - 10000
        assert target_account.balance_cents == before_cash[target] + 10000
        read = get_action(session, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
        assert read.receipt == result.receipt
        assert len(list(session.scalars(select(BankOperation)))) == 1
    assert snapshot(boundary_engine) == done


def test_prepare_purchase_uses_server_selected_product_and_amount(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, authorization())
    result = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="buy-first",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
        ),
        SEED_AS_OF,
    )
    assert (
        result.effect.action_type == "PURCHASE_ASSET"
        and result.effect.policy_version_id == version_id
    )
    assert result.prepared_validation.status == "READY" and result.effect.amount_cents > 0
    assert result.effect.position_id is not None


def test_prepare_recurring_pays_only_exact_unpaid_bank_occurrence(boundary_engine: Engine) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "synthetic-landlord-001",
                "due_day": 4,
                "auto_execute": True,
                "amount_rule": {"kind": "exact", "amount_cents": 180000},
            },
        )
        imported_proof(
            session,
            "SIMULATED_RECURRING_SETTLEMENT",
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": str(policy_id),
                "period": "2026-10",
                "paid_cents": 10000,
                "payee_id": "synthetic-landlord-001",
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
    result = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="pay-rent",
            intent=PaymentIntent(kind="pay_recurring", policy_id=policy_id, period="2026-10"),
        ),
        SEED_AS_OF,
    )
    assert result.effect.amount_cents == 170000
    assert result.effect.business_key == f"recurring:{policy_id}:2026-10"
    assert result.prepared_validation.status == "READY"


def test_prepare_redemption_keeps_original_acquisition_and_stable_quote(
    boundary_engine: Engine,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    result = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="redeem-first",
            intent=RedeemIntent(kind="redeem_asset", position_id=position_id),
        ),
        SEED_AS_OF,
    )
    assert result.effect.amount_cents == principal and result.effect.net_cents == principal
    assert (
        result.effect.original_policy_version_id is not None and result.effect.quote_id is not None
    )
    assert result.effect.latest_arrival_at == result.effect.expires_at


def test_range_payment_uses_bank_final_amount_instead_of_the_protection_max(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(
            session,
            {
                "type": "recurring_obligation",
                "payee_id": "synthetic-utilities-001",
                "due_day": 4,
                "auto_execute": True,
                "amount_rule": {"kind": "range", "min_cents": 5000, "max_cents": 10000},
            },
        )
        imported_proof(
            session,
            "SIMULATED_RECURRING_SETTLEMENT",
            {
                "protocol": "recurring-settlement-v1",
                "policy_id": str(policy_id),
                "period": "2026-10",
                "paid_cents": 1000,
                "final_total_cents": 8000,
                "payee_id": "synthetic-utilities-001",
                "complete": True,
                "as_of": SEED_AS_OF.isoformat(),
            },
        )
    result = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="pay-final-range",
            intent=PaymentIntent(kind="pay_recurring", policy_id=policy_id, period="2026-10"),
        ),
        SEED_AS_OF,
    )
    assert result.effect.amount_cents == 7000
    assert isinstance(result.effect.liability, OccurrenceReference)
    assert result.effect.liability.final_total_cents == 8000
    assert result.prepared_validation.projected_snapshot is not None
    assert (
        result.prepared_validation.projected_snapshot.occurrence_settlements[0].paid_cents == 8000
    )


def test_goal_prepare_execute_consumes_real_income_once_and_preserves_same_account_cash(
    boundary_engine: Engine,
) -> None:
    goal_id, account_id = zero_goal_income_setup(boundary_engine)
    with Session(boundary_engine) as session:
        account = session.get(Account, account_id)
        assert account is not None
        before = account.balance_cents
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="goal-actual", intent=GoalIntent(kind="allocate_goal", goal_id=goal_id)
        ),
        SEED_AS_OF,
    )
    assert prepared.effect.amount_cents == 10000 and len(prepared.effect.income_uses) == 1
    result = execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF)
    assert result.status == "SUCCEEDED"
    with Session(boundary_engine) as session:
        account, goal = session.get(Account, account_id), session.get(Goal, goal_id)
        assert account is not None and goal is not None
        assert account.balance_cents == before and goal.allocated_cents == 10000
        income = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
        assert sum(f.assigned_cents for f in income.fragments) == 10000
        assert sum(f.available_cents for f in income.fragments) == 90000
    done = snapshot(boundary_engine)
    assert (
        execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, SEED_AS_OF).receipt
        == result.receipt
    )
    assert snapshot(boundary_engine) == done


def test_transfer_moves_only_consumed_income_fragment_and_keeps_original_origin(
    boundary_engine: Engine,
) -> None:
    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session, session.begin():
        source_ledger(session, available_cents=100000)
        refresh_execution_exposure(session, DEMO_USER_ID, SEED_AS_OF, uuid4())
        before = read_income_state(session, DEMO_USER_ID, SEED_AS_OF).ledger
        account = session.get(Account, source)
        assert account is not None
        amount = account.balance_cents - 50000
    prepared = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="income-transfer",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=amount,
            ),
        ),
        SEED_AS_OF,
    )
    assert sum(u.amount_cents for u in prepared.effect.income_uses) == 50000
    later = SEED_AS_OF + timedelta(minutes=2)
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        prepared.action_id,
        ConfirmActionRequest(effect_hash=prepared.effect_hash, accepted=True),
        later,
    )
    result = execute_action(boundary_engine, DEMO_USER_ID, prepared.action_id, later)
    assert result.status == "SUCCEEDED"
    with Session(boundary_engine) as session:
        after = read_income_state(session, DEMO_USER_ID, later).ledger
        assert after.origins == before.origins
        assert sum(f.available_cents for f in after.fragments if f.account_id == source) == 50000
        assert sum(f.available_cents for f in after.fragments if f.account_id == target) == 50000


@pytest.mark.parametrize("redemption_entry", ["execution", "recovery"])
def test_multi_account_purchase_and_redemption_preserve_each_real_funding_leg(
    boundary_engine: Engine,
    redemption_entry: str,
) -> None:
    from app.db.models import AssetPosition, Transaction
    from app.services.asset_exposure_import import load_asset_exposure
    from app.services.boundary import load_boundary_context
    from app.services.execution_sources import execution_return_account

    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session:
        account = session.get(Account, source)
        assert account is not None
        half = account.balance_cents // 2
    transfer = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="split-funding",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=half,
            ),
        ),
        SEED_AS_OF,
    )
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        transfer.action_id,
        ConfirmActionRequest(effect_hash=transfer.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    execute_action(boundary_engine, DEMO_USER_ID, transfer.action_id, SEED_AS_OF)
    with Session(boundary_engine) as session, session.begin():
        policy_id, _ = confirmed_policy(
            session,
            authorization(
                allowed_asset_classes=["CASH_MGMT_T0"],
                max_auto_managed_cents=2000000,
                single_action_cap_cents=2000000,
            ),
        )
    purchase = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="multi-purchase",
            intent=PurchaseIntent(kind="purchase_asset", policy_id=policy_id),
        ),
        SEED_AS_OF,
    )
    assert len(purchase.effect.cash_uses) == 2 and purchase.effect.amount_cents == 2000000
    bought = execute_action(boundary_engine, DEMO_USER_ID, purchase.action_id, SEED_AS_OF)
    assert bought.status == "SUCCEEDED" and bought.receipt is not None
    with Session(boundary_engine) as session, session.begin():
        position = session.get(AssetPosition, bought.effect.position_id)
        assert position is not None and position.principal_cents == 2000000
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_POSITION",
                EvidenceItem.content["position_id"].as_string() == str(position.id),
                EvidenceItem.status == "VALID",
            )
        ).one()
        transactions = [
            session.get(Transaction, UUID(key)) for key in proof.content["purchase_transaction_ids"]
        ]
        assert len(transactions) == 2 and all(row is not None for row in transactions)
        assert sum(row.amount_cents for row in transactions if row is not None) == 2000000
        assert all(row.amount_cents < 2000000 for row in transactions if row is not None)
        assert (
            execution_return_account(session, DEMO_USER_ID, position, SEED_AS_OF)
            == bought.effect.return_account_id
        )
        context = load_boundary_context(session, DEMO_USER_ID, SEED_AS_OF)
        exposure = load_asset_exposure(session, context, {"scope": "general_idle_funds"})
        assert context.sources.issues == [] and exposure.managed_principal_cents == 2000000
        confirmed_policy(session, {"type": "emergency_buffer", "amount_cents": 1500000})
    assert bought.effect.position_id is not None
    if redemption_entry == "recovery":
        from app.services.recovery import preview_recovery, run_recovery

        recovered = run_recovery(boundary_engine, DEMO_USER_ID, "mixed-legacy-return", SEED_AS_OF)
        assert recovered.status == "RECOVERED" and len(recovered.actions) == 1
        assert recovered.actions[0].receipt_id is not None
        with Session(boundary_engine) as session:
            preview = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
            assert preview.source_issues == []
            assert preview.plan.status == "NO_RECOVERY_NEEDED"
        return
    redemption = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="multi-return",
            intent=RedeemIntent(kind="redeem_asset", position_id=bought.effect.position_id),
        ),
        SEED_AS_OF,
    )
    assert redemption.effect.destination_account_id == bought.effect.return_account_id
    returned = execute_action(boundary_engine, DEMO_USER_ID, redemption.action_id, SEED_AS_OF)
    assert returned.status == "SUCCEEDED" and returned.receipt is not None
    assert returned.receipt.executed_cents == 2000000
