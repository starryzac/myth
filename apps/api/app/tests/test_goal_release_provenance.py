"""Synthetic original-shape unit risks only, never a financial experiment."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.asset_allocation_types import PlannedExit
from app.domain.audit_chain_types import AuditVerification
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.full_reconciliation import (
    FullReconciliationReport,
    ReconciliationAction,
    ReconciliationGoal,
    ReconciliationInventory,
    ReconciliationPosting,
    compare_amount,
    head_reference,
)
from app.domain.goal_release_provenance import (
    AllocationOriginal,
    GoalCashPostingOriginal,
    GoalCashProvenanceInput,
    OriginalRowReference,
    prove_goal_cash_sources,
)
from app.domain.income_ledger import (
    IncomeFragment,
    IncomeLedger,
    IncomeOrigin,
    IncomeReservation,
    IncomeUse,
    location_id,
)
from app.domain.policy_configuration import configuration_hash

USER, GOAL, ACCOUNT, EPOCH, VERSION = (UUID(int=n) for n in range(8100, 8105))
ORIGIN, OTHER_GOAL = UUID(int=8105), UUID(int=8106)
NOW = datetime(2026, 10, 1, 3, tzinfo=UTC)
TABLES = (
    "accounts",
    "asset_positions",
    "goals",
    "action_plans",
    "bank_operations",
    "simulated_bank_redemptions",
    "action_receipts",
    "simulated_bank_postings",
)


def original(action_id: UUID, goal_id: UUID, cents: int) -> AllocationOriginal:
    use = IncomeUse(
        fragment_id=location_id(ORIGIN, ACCOUNT),
        origin_transaction_id=ORIGIN,
        account_id=ACCOUNT,
        amount_cents=cents,
    )
    effect = ExecutionEffect(
        operation_id=action_id,
        user_id=USER,
        business_key="synthetic:" + str(action_id),
        action_type="ALLOCATE_GOAL",
        amount_cents=cents,
        cash_uses=[CashUse(account_id=ACCOUNT, amount_cents=cents)],
        income_uses=[use],
        destination_account_id=ACCOUNT,
        goal_id=goal_id,
        policy_id=UUID(int=8110),
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
    bank = command.model_dump(mode="json")
    action = {"execution": bank}
    return AllocationOriginal(
        command=command,
        action_id=action_id,
        action_goal_id=goal_id,
        action_type="ALLOCATE_GOAL",
        action_request=action,
        action_request_hash=configuration_hash(action),
        bank_id=action_id,
        bank_request=bank,
        bank_request_hash=configuration_hash(bank),
        bank_idempotency_key="action:" + str(action_id),
        legacy_redemption_id=None,
        original_refs=[
            OriginalRowReference(
                table="bank_operations", row_id=action_id, row_hash=configuration_hash(bank)
            ),
            OriginalRowReference(
                table="action_plans", row_id=action_id, row_hash=configuration_hash(action)
            ),
            OriginalRowReference(
                table="action_receipts", row_id=UUID(int=action_id.int + 100), row_hash="9" * 64
            ),
        ],
    )


def actual(row: AllocationOriginal) -> ReconciliationAction:
    assert row.command is not None
    return ReconciliationAction(
        action_id=row.action_id,
        action_type=row.action_type,
        original_action_status="SUCCEEDED",
        original_idempotency_key=row.bank_idempotency_key,
        original_request_hash=row.action_request_hash,
        effect_hash=row.command.effect_hash,
        expected_amount_cents=row.command.effect.amount_cents,
        expected_fee_cents=0,
        expected_loss_cents=0,
        actual_executed_cents=row.command.effect.amount_cents,
        actual_fee_cents=0,
        actual_loss_cents=0,
        bank_operation_ids=[row.bank_id],
        bank_statuses=["SETTLED"],
        bank_request_hashes=[row.bank_request_hash],
        posting_ids=[],
        receipt_ids=[UUID(int=row.action_id.int + 100)],
        receipt_statuses=["SUCCEEDED"],
        complete_settlement_legs_verified=True,
        service_receipt_verified=True,
        state="SERVICE_RECEIPT_VERIFIED",
        read_original_action_path=f"/actions/{row.action_id}",
        issues=[],
    )


def fixture() -> GoalCashProvenanceInput:
    originals = [original(UUID(int=8111), GOAL, 300), original(UUID(int=8112), OTHER_GOAL, 200)]
    source = originals[0]
    assert source.command is not None
    income = IncomeLedger(
        user_id=USER,
        as_of=NOW,
        scope_account_ids=(ACCOUNT,),
        origins=(
            IncomeOrigin(
                origin_transaction_id=ORIGIN,
                origin_account_id=ACCOUNT,
                amount_cents=1000,
                occurred_at=NOW,
                observed_at=NOW,
                bank_evidence_id=UUID(int=8113),
                bank_evidence_hash="1" * 64,
            ),
        ),
        fragments=(
            IncomeFragment(
                fragment_id=location_id(ORIGIN, ACCOUNT),
                origin_transaction_id=ORIGIN,
                account_id=ACCOUNT,
                assigned_cents=500,
                available_cents=500,
            ),
        ),
        reservations=tuple(
            IncomeReservation(
                action_id=row.action_id,
                operation="ALLOCATE_GOAL",
                uses=tuple(row.command.effect.income_uses),
                state="COMMITTED",
            )
            for row in originals
            if row.command
        ),
    )
    history = [
        GoalCashPostingOriginal(
            posting_id=UUID(int=8120 + n),
            operation_id=None if n == 0 else source.action_id,
            account_id=ACCOUNT,
            ledger_metadata={"goal_id": str(GOAL), "account_id": str(ACCOUNT)},
            entry_kind="OPENING" if n == 0 else "SETTLEMENT",
            sequence_number=n + 1,
            balance_before_cents=0,
            delta_cents=0 if n == 0 else 300,
            balance_after_cents=0 if n == 0 else 300,
            source_ref=OriginalRowReference(
                table="simulated_bank_postings", row_id=UUID(int=8120 + n), row_hash="2" * 64
            ),
        )
        for n in range(2)
    ]
    head = head_reference(history[-1].posting_id, f"GOAL_CASH:{GOAL}", 2, NOW)
    report = FullReconciliationReport(
        user_id=USER,
        as_of=NOW,
        state="MATCHED",
        manual_review_required=False,
        bank_ledger_verified=True,
        current_application_projection_matched=True,
        pending_application_projection_explained=False,
        audit=AuditVerification(
            user_id=USER,
            epoch_id=EPOCH,
            status="VALID",
            chain_status="VALID",
            reference_status="VALID",
            checkpoint_status="NOT_REQUESTED",
            actual_count=1,
            expected_count=1,
            actual_tail_id=UUID(int=8124),
            actual_tail_hash="3" * 64,
            expected_tail_id=UUID(int=8124),
            expected_tail_hash="3" * 64,
            verified_through_sequence=1,
        ),
        inventory=[
            ReconciliationInventory(
                table=table,
                actual_count={
                    "bank_operations": 2,
                    "action_plans": 2,
                    "action_receipts": 2,
                    "goals": 2,
                    "simulated_bank_postings": 2,
                }.get(table, 0),
                captured_count={
                    "bank_operations": 2,
                    "action_plans": 2,
                    "action_receipts": 2,
                    "goals": 2,
                    "simulated_bank_postings": 2,
                }.get(table, 0),
                complete=True,
            )
            for table in TABLES
        ],
        account_cash=[],
        position_principals=[],
        goal_ownership=[
            ReconciliationGoal(
                goal_id=GOAL,
                account_id=ACCOUNT,
                allocated_cents=300,
                position_ids=[],
                ownership_evidence_id=UUID(int=8122),
                ownership_evidence_hash="4" * 64,
                current_ownership_proof_verified=True,
                cash=compare_amount(GOAL, "GOAL_CASH", 300, 300, head),
                principal=compare_amount(GOAL, "GOAL_PRINCIPAL", 0, 0, head),
            ),
            ReconciliationGoal(
                goal_id=OTHER_GOAL,
                account_id=ACCOUNT,
                allocated_cents=200,
                position_ids=[],
                ownership_evidence_id=UUID(int=8127),
                ownership_evidence_hash="4" * 64,
                current_ownership_proof_verified=True,
                cash=compare_amount(
                    OTHER_GOAL,
                    "GOAL_CASH",
                    200,
                    200,
                    head_reference(UUID(int=8128), f"GOAL_CASH:{OTHER_GOAL}", 2, NOW),
                ),
                principal=compare_amount(
                    OTHER_GOAL,
                    "GOAL_PRINCIPAL",
                    0,
                    0,
                    head_reference(UUID(int=8129), f"GOAL_PRINCIPAL:{OTHER_GOAL}", 1, NOW),
                ),
            ),
        ],
        actions=[actual(row) for row in originals],
        bank_postings=[
            ReconciliationPosting(
                posting_id=row.posting_id,
                operation_id=row.operation_id,
                redemption_id=None,
                ledger_key=f"GOAL_CASH:{GOAL}",
                ledger_dimension="GOAL_OWNERSHIP",
                leg_ref=None if row.entry_kind == "OPENING" else "goal_cash",
                sequence_number=row.sequence_number,
                balance_before_cents=row.balance_before_cents,
                delta_cents=row.delta_cents,
                balance_after_cents=row.balance_after_cents,
                occurred_at=NOW,
            )
            for row in history
        ],
        issues=[],
        uncovered=[],
        input_hash="5" * 64,
        limitations=[],
    )
    return GoalCashProvenanceInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        goal_id=GOAL,
        goal_account_id=ACCOUNT,
        goal_policy_version_id=VERSION,
        income=income,
        income_evidence_id=UUID(int=8123),
        income_evidence_hash="6" * 64,
        reconciliation=report,
        originals=originals,
        goal_cash_history=history,
        original_inventory_complete=True,
        original_binding_hash="7" * 64,
        source_issues=[],
    )


def test_cash_only_exact_sources_keep_all_original_buckets_and_other_goal_denominator() -> None:
    data = fixture()
    before = data.model_dump_json()
    result = prove_goal_cash_sources(data)
    assert result.state == "VERIFIED_CASH_ONLY" and result.exactly_attributed_goal_cash_cents == 300
    source = result.sources[0]
    assert (
        source.original_assigned_cents,
        source.source_goal_cash_remaining_cents,
        source.other_goal_allocation_cents,
        source.original_available_cents,
    ) == (500, 300, 200, 500)
    assert result.assigned_income_decrease_cents == result.available_income_increase_cents == 0
    assert not result.funds_released and not result.bank_authority
    assert result.principal_change_cents == result.other_goal_change_cents == 0
    assert data.model_dump_json() == before
    assert len(result.original_allocation_slices) == 1
    original_slice = result.original_allocation_slices[0]
    assert original_slice.original_allocated_cents == original_slice.cash_remaining_cents == 300
    assert original_slice.allocation_action_id == data.originals[0].action_id
    assert original_slice.fragment_id == source.fragment_id


@pytest.mark.parametrize(
    "change",
    [
        "missing_receipt",
        "unverified_legs",
        "missing_other_goal",
        "missing_committed",
        "assigned_denominator",
        "nonzero_opening",
        "missing_posting",
        "wrong_owner",
        "wrong_epoch",
        "pending_projection",
        "empty_inventory",
        "inventory_count",
        "original_hash",
        "source_account",
        "clock",
        "bank_key",
        "unrecorded_principal_split",
    ],
)
def test_missing_or_ambiguous_originals_keep_cash_residual_null(change: str) -> None:
    data = fixture()
    assert data.income is not None
    ledger = data.income
    report = data.reconciliation
    if change in {"missing_receipt", "unverified_legs", "bank_key"}:
        changes: dict[str, dict[str, Any]] = {
            "missing_receipt": {"receipt_ids": [], "service_receipt_verified": False},
            "unverified_legs": {"complete_settlement_legs_verified": False},
            "bank_key": {"original_idempotency_key": "not-original-bank-key"},
        }
        updates = changes[change]
        data = data.model_copy(
            update={
                "reconciliation": report.model_copy(
                    update={
                        "actions": [report.actions[0].model_copy(update=updates), report.actions[1]]
                    }
                )
            }
        )
    elif change == "missing_other_goal":
        data = data.model_copy(update={"originals": data.originals[:1]})
    elif change == "missing_committed":
        data = data.model_copy(update={"income": ledger.model_copy(update={"reservations": ()})})
    elif change == "assigned_denominator":
        fragment = ledger.fragments[0].model_copy(
            update={"assigned_cents": 501, "available_cents": 499}
        )
        data = data.model_copy(
            update={"income": ledger.model_copy(update={"fragments": (fragment,)})}
        )
    elif change in {"nonzero_opening", "source_account"}:
        row = data.goal_cash_history[0].model_copy(
            update={"delta_cents": 1}
            if change == "nonzero_opening"
            else {"account_id": UUID(int=8199)}
        )
        data = data.model_copy(update={"goal_cash_history": [row, *data.goal_cash_history[1:]]})
    elif change == "missing_posting":
        data = data.model_copy(update={"goal_cash_history": data.goal_cash_history[:1]})
    elif change in {"wrong_owner", "clock", "pending_projection", "empty_inventory", "wrong_epoch"}:
        changes = {
            "wrong_owner": {"user_id": UUID(int=8199)},
            "clock": {"as_of": NOW + timedelta(seconds=1)},
            "pending_projection": {"current_application_projection_matched": False},
            "empty_inventory": {"inventory": []},
            "wrong_epoch": {"audit": report.audit.model_copy(update={"epoch_id": UUID(int=8199)})},
        }
        updates = changes[change]
        data = data.model_copy(update={"reconciliation": report.model_copy(update=updates)})
    elif change == "inventory_count":
        data = data.model_copy(update={"original_inventory_complete": False})
    elif change == "original_hash":
        data = data.model_copy(
            update={
                "originals": [
                    data.originals[0].model_copy(update={"action_request_hash": "8" * 64}),
                    data.originals[1],
                ]
            }
        )
    else:
        data = data.model_copy(
            update={
                "originals": [
                    *data.originals,
                    data.originals[0].model_copy(
                        update={
                            "bank_id": UUID(int=8198),
                            "action_id": UUID(int=8198),
                            "command": None,
                            "action_type": "ASSET_PURCHASE",
                        }
                    ),
                ]
            }
        )
    result = prove_goal_cash_sources(data)
    assert result.state == "UNKNOWN" and result.exactly_attributed_goal_cash_cents is None
    assert result.reasons and all(
        row.source_goal_cash_remaining_cents is None for row in result.sources
    )
    assert not result.funds_released and result.assigned_income_decrease_cents == 0


def test_missing_income_is_unknown_and_not_a_fake_zero_complete_denominator() -> None:
    result = prove_goal_cash_sources(fixture().model_copy(update={"income": None}))
    assert result.state == "UNKNOWN" and result.complete_original_income_fragment_count is None
    assert result.sources == [] and result.exactly_attributed_goal_cash_cents is None


def test_valid_original_purchase_keeps_aggregate_cash_known_but_source_cash_split_unknown() -> None:
    data = fixture()
    action_id = UUID(int=8190)
    effect = ExecutionEffect(
        operation_id=action_id,
        user_id=USER,
        business_key="synthetic:goal-purchase",
        action_type="PURCHASE_ASSET",
        amount_cents=100,
        cash_uses=[CashUse(account_id=ACCOUNT, amount_cents=100)],
        goal_id=GOAL,
        policy_id=UUID(int=8110),
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        product_id=UUID(int=8191),
        product_version_number=1,
        terms_digest="a" * 64,
        position_id=UUID(int=8192),
        position_account_id=UUID(int=8193),
        return_account_id=ACCOUNT,
        latest_arrival_at=NOW + timedelta(days=1),
        purchase_exit=PlannedExit(
            kind="FIXED_MATURITY",
            principal_available_at=NOW + timedelta(days=1),
            earning_days=1,
            liquidity_days=1,
            terms_digest="a" * 64,
        ),
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    command = BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
    bank = command.model_dump(mode="json")
    request = {"execution": bank}
    purchase = AllocationOriginal(
        command=command,
        action_id=action_id,
        action_goal_id=GOAL,
        action_type="ASSET_PURCHASE",
        action_request=request,
        action_request_hash=configuration_hash(request),
        bank_id=action_id,
        bank_request=bank,
        bank_request_hash=configuration_hash(bank),
        bank_idempotency_key="action:purchase",
        legacy_redemption_id=None,
        original_refs=[
            OriginalRowReference(table="action_plans", row_id=action_id, row_hash="a" * 64),
            OriginalRowReference(table="bank_operations", row_id=action_id, row_hash="b" * 64),
            OriginalRowReference(
                table="action_receipts", row_id=UUID(int=action_id.int + 100), row_hash="c" * 64
            ),
        ],
    )
    history = GoalCashPostingOriginal(
        posting_id=UUID(int=8194),
        operation_id=action_id,
        account_id=ACCOUNT,
        ledger_metadata={"goal_id": str(GOAL), "account_id": str(ACCOUNT)},
        entry_kind="SETTLEMENT",
        sequence_number=3,
        balance_before_cents=300,
        delta_cents=-100,
        balance_after_cents=200,
        source_ref=OriginalRowReference(
            table="simulated_bank_postings", row_id=UUID(int=8194), row_hash="d" * 64
        ),
    )
    bank_posting = ReconciliationPosting(
        posting_id=history.posting_id,
        operation_id=action_id,
        redemption_id=None,
        ledger_key=f"GOAL_CASH:{GOAL}",
        ledger_dimension="GOAL_OWNERSHIP",
        leg_ref="goal_cash",
        sequence_number=3,
        balance_before_cents=300,
        delta_cents=-100,
        balance_after_cents=200,
        occurred_at=NOW,
    )
    goal = data.reconciliation.goal_ownership[0]
    assert goal.cash.bank_head is not None
    updated_goal = goal.model_copy(
        update={
            "cash": compare_amount(GOAL, "GOAL_CASH", 200, 200, goal.cash.bank_head),
            "principal": compare_amount(GOAL, "GOAL_PRINCIPAL", 100, 100, goal.cash.bank_head),
        }
    )
    inventory = [
        row.model_copy(update={"actual_count": 3, "captured_count": 3})
        if row.table
        in {"action_plans", "bank_operations", "action_receipts", "simulated_bank_postings"}
        else row
        for row in data.reconciliation.inventory
    ]
    report = data.reconciliation.model_copy(
        update={
            "actions": [*data.reconciliation.actions, actual(purchase)],
            "bank_postings": [*data.reconciliation.bank_postings, bank_posting],
            "goal_ownership": [updated_goal, *data.reconciliation.goal_ownership[1:]],
            "inventory": inventory,
        }
    )
    result = prove_goal_cash_sources(
        data.model_copy(
            update={
                "reconciliation": report,
                "originals": [*data.originals, purchase],
                "goal_cash_history": [*data.goal_cash_history, history],
            }
        )
    )
    assert result.original_goal_cash_cents == 200 and result.original_goal_principal_cents == 100
    assert result.state == "UNKNOWN" and result.exactly_attributed_goal_cash_cents is None
    assert "GOAL_CASH_PRINCIPAL_OR_LEGACY_SOURCE_SPLIT_NOT_RECORDED" in result.reasons
    assert (
        result.complete_original_bank_operation_count == 3
        and result.sources[0].original_assigned_cents == 500
    )
    assert result.sources[0].source_goal_cash_remaining_cents is None
    assert result.principal_change_cents == result.available_income_increase_cents == 0
