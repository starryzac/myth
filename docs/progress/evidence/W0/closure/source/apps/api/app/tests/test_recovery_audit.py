"""Independent failure-window and economic-once recovery checks on real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event, Lock
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    DecisionRun,
    EvidenceItem,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.policy_configuration import configuration_hash
from app.services import recovery as recovery_service
from app.services.asset_allocation import preview_asset_allocation
from app.services.boundary import compute_user_boundary
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.goal_allocation import LEDGER_SOURCE, preview_goal_allocation
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery import RecoveryRunResponse, preview_recovery, run_recovery
from app.services.recovery_projection import project_request as apply_bank_projection
from app.services.simulated_bank import BankResult
from app.services.simulated_bank import process_redemption as bank_process_redemption
from app.tests.test_asset_allocation_service import asset_policy
from app.tests.test_boundary_service import boundary_engine as boundary_engine
from app.tests.test_boundary_service import goal_fixture, snapshot
from app.tests.test_recovery_service import multiple_recovery_fixture, recovery_fixture
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class InjectedResponseLoss(RuntimeError):
    pass


class InjectedProjectionFailure(RuntimeError):
    pass


def target_cash(engine: Engine, position_id: UUID) -> tuple[UUID, int]:
    with Session(engine) as session:
        preview = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        assert len(preview.plan.steps) == 1
        step = preview.plan.steps[0]
        assert step.position_id == position_id
        account = session.get(Account, step.destination_account_id)
        assert account is not None
        return account.id, account.balance_cents


def assert_economic_once(
    engine: Engine,
    position_id: UUID,
    account_id: UUID,
    initial_cash: int,
    principal: int,
    *,
    projected: bool,
) -> None:
    """Literal principal/cash truth from independent rows, never a computed expected receipt."""
    with Session(engine) as session:
        requests = list(
            session.scalars(
                select(SimulatedBankRedemption).where(
                    SimulatedBankRedemption.user_id == DEMO_USER_ID,
                    SimulatedBankRedemption.position_id == position_id,
                )
            )
        )
        assert len(requests) == 1 and requests[0].status == "SETTLED"
        request = requests[0]
        legs = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.redemption_id == request.id,
                )
            )
        )
        assert len(legs) == 2
        assert {(leg.entry_kind, leg.delta_cents) for leg in legs} == {
            ("CASH_CREDIT", principal),
            ("PRINCIPAL_DEBIT", -principal),
        }
        credit = next(leg for leg in legs if leg.entry_kind == "CASH_CREDIT")
        debit = next(leg for leg in legs if leg.entry_kind == "PRINCIPAL_DEBIT")
        assert credit.account_id == account_id
        assert credit.balance_before_cents == initial_cash
        assert credit.balance_after_cents == initial_cash + principal
        assert debit.position_id == position_id
        assert debit.balance_before_cents == principal and debit.balance_after_cents == 0
        assert sum(leg.delta_cents for leg in legs) == 0
        action_ids = list(
            session.scalars(
                select(ActionPlan.id).where(
                    ActionPlan.user_id == DEMO_USER_ID,
                    ActionPlan.position_id == position_id,
                    ActionPlan.action_type == "ASSET_REDEEM",
                )
            )
        )
        receipts = list(
            session.scalars(
                select(ActionReceipt).where(
                    ActionReceipt.action_plan_id.in_(action_ids),
                )
            )
        )
        account, position = (
            session.get(Account, account_id),
            session.get(AssetPosition, position_id),
        )
        assert account is not None and position is not None
        if not projected:
            assert receipts == []
            assert account.balance_cents == initial_cash
            assert position.status != "REDEEMED"
            return
        assert len(receipts) == 1 and receipts[0].executed_cents == principal
        assert receipts[0].fee_cents == receipts[0].loss_cents == 0
        assert set(receipts[0].response["posting_ids"]) == {str(leg.id) for leg in legs}
        assert account.balance_cents == initial_cash + principal
        assert position.status == "REDEEMED"
        transactions = list(
            session.scalars(
                select(Transaction)
                .join(
                    EvidenceItem,
                    Transaction.evidence_id == EvidenceItem.id,
                )
                .where(
                    Transaction.user_id == DEMO_USER_ID,
                    EvidenceItem.content["bank_request_id"].as_string() == str(request.id),
                )
            )
        )
        assert len(transactions) == 1
        assert transactions[0].amount_cents == principal
        assert transactions[0].direction == "CREDIT"
        bank = session.get(EvidenceItem, transactions[0].evidence_id)
        assert bank is not None and bank.content["economic_role"] == "PRINCIPAL_RETURN"


def test_committed_bank_response_loss_retries_projection_without_second_effect(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    account_id, initial_cash = target_cash(boundary_engine, position_id)
    original = bank_process_redemption

    def lose_response(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> BankResult:
        original(engine, user_id, action_id, now)
        raise InjectedResponseLoss("The bank committed; its response was lost")

    monkeypatch.setattr(recovery_service, "process_redemption", lose_response)
    try:
        first = run_recovery(boundary_engine, DEMO_USER_ID, "audit-bank-response-loss", SEED_AS_OF)
    except InjectedResponseLoss:
        pass
    else:
        assert first.status != "RECOVERED"
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=False
    )
    monkeypatch.setattr(recovery_service, "process_redemption", original)
    second = run_recovery(boundary_engine, DEMO_USER_ID, "audit-bank-response-loss", SEED_AS_OF)
    assert second.status == "RECOVERED"
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=True
    )
    third = run_recovery(boundary_engine, DEMO_USER_ID, "audit-bank-response-loss", SEED_AS_OF)
    assert third.run_id == second.run_id
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=True
    )


def test_projection_transaction_failure_preserves_bank_fact_and_retries_once(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    account_id, initial_cash = target_cash(boundary_engine, position_id)
    original = apply_bank_projection

    def fail_projection(*args: object, **kwargs: object) -> object:
        raise InjectedProjectionFailure("The application projection transaction failed")

    monkeypatch.setattr(recovery_service, "project_request", fail_projection)
    try:
        first = run_recovery(boundary_engine, DEMO_USER_ID, "audit-projection-failure", SEED_AS_OF)
    except InjectedProjectionFailure:
        pass
    else:
        assert first.status != "RECOVERED"
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=False
    )
    monkeypatch.setattr(recovery_service, "project_request", original)
    second = run_recovery(boundary_engine, DEMO_USER_ID, "audit-projection-failure", SEED_AS_OF)
    assert second.status == "RECOVERED"
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=True
    )


def test_different_idempotency_keys_competing_for_one_position_have_one_bank_effect(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    account_id, initial_cash = target_cash(boundary_engine, position_id)
    entered_bank, release_bank = Event(), Event()
    entry_lock = Lock()
    calls = 0

    def pause_first_bank_call(
        engine: Engine, user_id: UUID, action_id: UUID, now: datetime
    ) -> BankResult:
        nonlocal calls
        with entry_lock:
            calls += 1
            first = calls == 1
        if first:
            entered_bank.set()
            assert release_bank.wait(timeout=30), "The audit must release its bank gate"
        return bank_process_redemption(engine, user_id, action_id, now)

    monkeypatch.setattr(recovery_service, "process_redemption", pause_first_bank_call)

    def compete(key: str) -> RecoveryRunResponse | PolicyLifecycleError:
        try:
            return run_recovery(boundary_engine, DEMO_USER_ID, key, SEED_AS_OF)
        except PolicyLifecycleError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(compete, "audit-competing-first")
        try:
            assert entered_bank.wait(timeout=15), "Phase 1 did not reach the bank gate"
            # Phase 1 has committed its SUBMITTED action; the independent bank has not run.
            second_future = pool.submit(compete, "audit-competing-second")
            second = second_future.result(timeout=20)
            with Session(boundary_engine) as session:
                actions = list(
                    session.scalars(
                        select(ActionPlan).where(
                            ActionPlan.user_id == DEMO_USER_ID,
                            ActionPlan.position_id == position_id,
                            ActionPlan.action_type == "ASSET_REDEEM",
                        )
                    )
                )
                assert len(actions) == 1, "A reserved position must not get a second action"
        finally:
            release_bank.set()
        results = [first_future.result(timeout=20), second]
    assert any(
        isinstance(result, RecoveryRunResponse) and result.status == "RECOVERED"
        for result in results
    )
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=True
    )


def test_bank_commit_cannot_launder_rebound_position_identity_during_projection(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, position_id, principal = recovery_fixture(boundary_engine)
    account_id, initial_cash = target_cash(boundary_engine, position_id)

    def fail_projection(*args: object, **kwargs: object) -> object:
        raise InjectedProjectionFailure("Pause after the independent bank commit")

    monkeypatch.setattr(recovery_service, "project_request", fail_projection)
    with pytest.raises(InjectedProjectionFailure):
        run_recovery(boundary_engine, DEMO_USER_ID, "audit-position-rebound", SEED_AS_OF)
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=False
    )
    monkeypatch.setattr(recovery_service, "project_request", apply_bank_projection)
    with Session(boundary_engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None
        original_account_id = position.account_id
        rebound_account = session.scalar(
            select(Account).where(
                Account.user_id == DEMO_USER_ID,
                Account.id != original_account_id,
                Account.account_type != "CREDIT_CARD",
            )
        )
        assert rebound_account is not None
        position.account_id = rebound_account.id
    before_rejected_retry = snapshot(boundary_engine)
    with pytest.raises(PolicyLifecycleError) as rejected:
        run_recovery(boundary_engine, DEMO_USER_ID, "audit-position-rebound", SEED_AS_OF)
    assert rejected.value.code == "BANK_RECONCILIATION_REQUIRED"
    assert rejected.value.status_code == 409
    assert snapshot(boundary_engine) == before_rejected_retry
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=False
    )
    with Session(boundary_engine) as session, session.begin():
        position = session.get(AssetPosition, position_id)
        assert position is not None
        position.account_id = original_account_id
    completed = run_recovery(boundary_engine, DEMO_USER_ID, "audit-position-rebound", SEED_AS_OF)
    assert completed.status == "RECOVERED"
    assert_economic_once(
        boundary_engine, position_id, account_id, initial_cash, principal, projected=True
    )


def test_later_bank_rejection_cannot_strand_an_earlier_committed_batch_effect(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, first_principal, second_principal = multiple_recovery_fixture(boundary_engine)
    assert (first_principal, second_principal) == (250000, 150000)
    with Session(boundary_engine) as session:
        preview = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        assert len(preview.plan.steps) == 2
        amounts = {
            position.id: position.principal_cents
            for position in session.scalars(
                select(AssetPosition).where(
                    AssetPosition.id.in_([step.position_id for step in preview.plan.steps])
                )
            )
        }
        assert sorted(amounts.values()) == [150000, 250000]
        assert all(
            step.quote.principal_cents == amounts[step.position_id] for step in preview.plan.steps
        )
        account_ids = {step.destination_account_id for step in preview.plan.steps}
        assert len(account_ids) == 1
        account_id = next(iter(account_ids))
        cash = session.get(Account, account_id)
        assert cash is not None
        initial_cash = cash.balance_cents
    accepted_action: UUID | None = None

    def reject_other_bank_call(
        engine: Engine, user_id: UUID, action_id: UUID, now: datetime
    ) -> BankResult:
        nonlocal accepted_action
        if accepted_action is None:
            accepted_action = action_id
        if action_id != accepted_action:
            raise PolicyLifecycleError(
                "SIMULATED_BANK_REJECTED", "Injected later-bank rejection", 409
            )
        return bank_process_redemption(engine, user_id, action_id, now)

    monkeypatch.setattr(recovery_service, "process_redemption", reject_other_bank_call)
    for _ in range(2):
        try:
            partial = run_recovery(boundary_engine, DEMO_USER_ID, "audit-partial-batch", SEED_AS_OF)
        except PolicyLifecycleError as error:
            assert error.code == "SIMULATED_BANK_REJECTED"
        else:
            assert partial.status != "RECOVERED"
        with Session(boundary_engine) as session:
            requests = list(session.scalars(select(SimulatedBankRedemption)))
            assert len(requests) == 1 and requests[0].status == "SETTLED"
            committed = requests[0]
            receipts = list(
                session.scalars(
                    select(ActionReceipt).where(
                        ActionReceipt.action_plan_id == committed.action_plan_id
                    )
                )
            )
            assert len(receipts) == 1, (
                "A later rejection cannot prevent an earlier bank effect's projection"
            )
            committed_action = session.get(ActionPlan, committed.action_plan_id)
            assert committed_action is not None and committed_action.decision_run_id is not None
            run = session.get(DecisionRun, committed_action.decision_run_id)
            assert run is not None and run.status != "SUCCEEDED" and run.completed_at is None
            assert receipts[0].executed_cents == amounts[committed.position_id]
            cash = session.get(Account, account_id)
            position = session.get(AssetPosition, committed.position_id)
            assert cash is not None and position is not None
            assert cash.balance_cents == initial_cash + amounts[committed.position_id]
            assert position.status == "REDEEMED"
            assert (
                len(
                    list(
                        session.scalars(
                            select(SimulatedBankPosting).where(
                                SimulatedBankPosting.redemption_id == committed.id
                            )
                        )
                    )
                )
                == 2
            )
    monkeypatch.setattr(recovery_service, "process_redemption", bank_process_redemption)
    final = run_recovery(boundary_engine, DEMO_USER_ID, "audit-partial-batch", SEED_AS_OF)
    assert final.status == "RECOVERED"
    with Session(boundary_engine) as session:
        cash = session.get(Account, account_id)
        assert cash is not None and cash.balance_cents == initial_cash + 400000
        requests = list(session.scalars(select(SimulatedBankRedemption)))
        assert len(requests) == 2 and all(request.status == "SETTLED" for request in requests)
        receipts = list(
            session.scalars(
                select(ActionReceipt).where(
                    ActionReceipt.action_plan_id.in_(
                        [request.action_plan_id for request in requests]
                    )
                )
            )
        )
        assert len(receipts) == 2 and sum(receipt.executed_cents for receipt in receipts) == 400000
        postings = list(
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.redemption_id.in_([request.id for request in requests])
                )
            )
        )
        assert len(postings) == 4
        assert sum(leg.delta_cents for leg in postings if leg.entry_kind == "CASH_CREDIT") == 400000
        assert (
            sum(leg.delta_cents for leg in postings if leg.entry_kind == "PRINCIPAL_DEBIT")
            == -400000
        )


def refresh_v2_manifest(session: Session) -> None:
    """Rehash application evidence while leaving independent bank postings untouched."""
    proof = session.scalar(
        select(EvidenceItem).where(
            EvidenceItem.user_id == DEMO_USER_ID,
            EvidenceItem.source_type == EXPOSURE_SOURCE,
            EvidenceItem.status == "VALID",
        )
    )
    assert proof is not None
    session.flush()
    proof.content = asset_exposure_snapshot(
        DEMO_USER_ID,
        SEED_AS_OF,
        accounts=session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID)),
        positions=session.scalars(
            select(AssetPosition).where(AssetPosition.user_id == DEMO_USER_ID)
        ),
        actions=session.scalars(select(ActionPlan).where(ActionPlan.user_id == DEMO_USER_ID)),
        receipts=session.scalars(
            select(ActionReceipt).where(ActionReceipt.user_id == DEMO_USER_ID)
        ),
        evidence=session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID)),
        settlements=[],
        bank_requests=session.scalars(
            select(SimulatedBankRedemption).where(
                SimulatedBankRedemption.user_id == DEMO_USER_ID,
            )
        ),
        bank_postings=session.scalars(
            select(SimulatedBankPosting).where(
                SimulatedBankPosting.user_id == DEMO_USER_ID,
            )
        ),
    )
    proof.content_hash = configuration_hash(proof.content)
    assert proof.content["protocol"] == "asset-exposure-v2"


def exhausted_income_statement(session: Session) -> None:
    """Complete old-source zero residuals; this creates no transaction or bank balance."""
    lots: list[dict[str, Any]] = []
    for transaction in session.scalars(
        select(Transaction).where(
            Transaction.user_id == DEMO_USER_ID,
            Transaction.direction == "CREDIT",
        )
    ):
        evidence = session.get(EvidenceItem, transaction.evidence_id)
        assert evidence is not None
        if evidence.content.get("economic_role") != "INCOME":
            continue
        lots.append(
            {
                "transaction_id": str(transaction.id),
                "account_id": str(transaction.account_id),
                "bank_evidence_id": str(evidence.id),
                "bank_evidence_hash": evidence.content_hash,
                "original_cents": transaction.amount_cents,
                "prior_unspent_cents": 0,
                "spent_cents": transaction.amount_cents,
                "assigned_cents": 0,
                "reserved_cents": 0,
                "available_cents": 0,
            }
        )
    content = {
        "simulation": True,
        "protocol": "new-funds-ledger-v1",
        "user_id": str(DEMO_USER_ID),
        "complete": True,
        "as_of": SEED_AS_OF.isoformat(),
        "scope_account_ids": [
            str(identity)
            for identity in session.scalars(
                select(Account.id)
                .where(
                    Account.user_id == DEMO_USER_ID,
                    Account.account_type == "CASH",
                )
                .order_by(Account.id)
            )
        ],
        "lots": sorted(lots, key=lambda lot: lot["transaction_id"]),
    }
    identity = uuid4()
    session.add(
        EvidenceItem(
            id=identity,
            user_id=DEMO_USER_ID,
            source_type=LEDGER_SOURCE,
            source_ref=f"audit-old-income:{identity}",
            evidence_level="BANK_CONFIRMED",
            content=content,
            content_hash=configuration_hash(content),
            status="VALID",
            created_at=SEED_AS_OF,
            valid_from=SEED_AS_OF,
            observed_at=SEED_AS_OF,
        )
    )


def test_rehashed_application_cash_tampering_closes_all_four_v2_consumers(
    boundary_engine: Engine,
) -> None:
    with Session(boundary_engine) as session, session.begin():
        goal_id = goal_fixture(session, with_proofs=True)
        policy_id = asset_policy(session)
        exhausted_income_statement(session)
        refresh_v2_manifest(session)
    with Session(boundary_engine) as session:
        assert compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF).boundary.status == "READY"
        goal_before = preview_goal_allocation(session, DEMO_USER_ID, goal_id, SEED_AS_OF)
        assert goal_before.allocation.status == "MINIMUM_SHORTFALL"
        assert goal_before.allocation.suggested_cents == 0
        assert goal_before.source_issues == []
        assert (
            preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF).allocation.status
            == "READY"
        )
        assert (
            preview_recovery(session, DEMO_USER_ID, SEED_AS_OF).plan.status == "NO_RECOVERY_NEEDED"
        )
        postings_before = [
            row.id
            for row in session.scalars(
                select(SimulatedBankPosting).order_by(SimulatedBankPosting.id)
            )
        ]
    with Session(boundary_engine) as session, session.begin():
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        cash.balance_cents += 1
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                EvidenceItem.status == "VALID",
                EvidenceItem.content["account_id"].as_string() == str(cash.id),
            )
        )
        assert proof is not None
        proof.content = {**proof.content, "balance_cents": cash.balance_cents}
        proof.content_hash = configuration_hash(proof.content)
        refresh_v2_manifest(session)
    unchanged = snapshot(boundary_engine)
    with Session(boundary_engine) as session:
        boundary = compute_user_boundary(session, DEMO_USER_ID, SEED_AS_OF)
        goal = preview_goal_allocation(session, DEMO_USER_ID, goal_id, SEED_AS_OF)
        asset = preview_asset_allocation(session, DEMO_USER_ID, policy_id, SEED_AS_OF)
        recovery = preview_recovery(session, DEMO_USER_ID, SEED_AS_OF)
        assert boundary.boundary.status == "INSUFFICIENT_EVIDENCE"
        assert boundary.boundary.safe_idle_cents is None
        assert goal.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert goal.allocation.suggested_cents is None
        assert asset.allocation.status == "INSUFFICIENT_EVIDENCE"
        assert asset.allocation.suggested_cents is None
        assert recovery.plan.status == "INSUFFICIENT_EVIDENCE"
        assert recovery.plan.steps == []
        assert [
            row.id
            for row in session.scalars(
                select(SimulatedBankPosting).order_by(SimulatedBankPosting.id)
            )
        ] == postings_before
    assert snapshot(boundary_engine) == unchanged
