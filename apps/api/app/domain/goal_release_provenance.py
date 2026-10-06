"""Original assigned income remains assigned; this proof never releases funds."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.execution_types import BankCommand
from app.domain.full_reconciliation import FullReconciliationReport, ReconciliationAction
from app.domain.income_ledger import IncomeLedger, IncomeUse
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt

Cents = Annotated[StrictInt, Field(ge=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OriginalRowReference(BoundaryModel):
    table: str
    row_id: UUID
    row_hash: Digest


class AllocationOriginal(BoundaryModel):
    command: BankCommand | None
    action_id: UUID
    action_goal_id: UUID | None
    action_type: str
    action_request: dict[str, Any]
    action_request_hash: Digest
    bank_id: UUID
    bank_request: dict[str, Any]
    bank_request_hash: Digest
    bank_idempotency_key: str
    legacy_redemption_id: UUID | None
    original_refs: list[OriginalRowReference]


class GoalCashPostingOriginal(BoundaryModel):
    posting_id: UUID
    operation_id: UUID | None
    account_id: UUID | None
    ledger_metadata: dict[str, Any]
    entry_kind: str
    sequence_number: Annotated[StrictInt, Field(gt=0)]
    balance_before_cents: Cents
    delta_cents: StrictInt
    balance_after_cents: Cents
    source_ref: OriginalRowReference


class GoalCashProvenanceInput(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    goal_id: UUID
    goal_account_id: UUID
    goal_policy_version_id: UUID
    income: IncomeLedger | None
    income_evidence_id: UUID | None
    income_evidence_hash: Digest | None
    reconciliation: FullReconciliationReport
    originals: list[AllocationOriginal]
    goal_cash_history: list[GoalCashPostingOriginal]
    original_inventory_complete: bool
    original_binding_hash: Digest
    source_issues: list[str]


class GoalIncomeSource(BoundaryModel):
    fragment_id: UUID
    origin_transaction_id: UUID
    income_location_account_id: UUID
    origin_account_id: UUID
    origin_bank_evidence_id: UUID
    origin_bank_evidence_hash: Digest
    original_assigned_cents: Cents
    verified_all_goal_allocation_cents: Cents
    source_goal_allocation_cents: Cents
    other_goal_allocation_cents: Cents
    source_goal_cash_remaining_cents: Cents | None
    original_available_cents: Cents
    original_reserved_cents: Cents
    original_spent_cents: Cents
    source_allocation_action_ids: list[UUID]
    source_original_refs: list[OriginalRowReference]


class GoalAllocationSourceSlice(BoundaryModel):
    allocation_action_id: UUID
    original_policy_version_id: UUID
    fragment_id: UUID
    origin_transaction_id: UUID
    income_location_account_id: UUID
    original_allocated_cents: Cents
    cash_remaining_cents: Cents | None
    allocation_effect_hash: Digest
    allocation_bank_request_hash: Digest
    allocation_action_request_hash: Digest
    original_refs: list[OriginalRowReference]


class GoalCashSourceProof(BoundaryModel):
    schema_version: Literal["goal-cash-source-proof-v1"] = "goal-cash-source-proof-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    goal_id: UUID
    goal_account_id: UUID
    goal_policy_version_id: UUID
    state: Literal["VERIFIED_CASH_ONLY", "UNKNOWN"]
    original_goal_cash_cents: Cents | None
    original_goal_principal_cents: Cents | None
    exactly_attributed_goal_cash_cents: Cents | None
    sources: list[GoalIncomeSource]
    original_allocation_slices: list[GoalAllocationSourceSlice]
    complete_original_income_fragment_count: Cents | None
    complete_original_bank_operation_count: Cents | None
    captured_original_bank_operation_count: Cents
    original_operation_refs: list[OriginalRowReference]
    original_goal_cash_posting_refs: list[OriginalRowReference]
    source_binding_hash: Digest
    reconciliation_input_hash: Digest
    income_evidence_id: UUID | None
    income_evidence_hash: Digest | None
    reasons: list[str]
    read_only: Literal[True] = True
    bank_authority: Literal[False] = False
    principal_change_cents: Literal[0] = 0
    other_goal_change_cents: Literal[0] = 0
    available_income_increase_cents: Literal[0] = 0
    assigned_income_decrease_cents: Literal[0] = 0
    funds_released: Literal[False] = False
    minimum_guarantee_or_grant_verified: Literal[False] = False
    usage_ledger_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"


def _same_uses(left: tuple[IncomeUse, ...], right: list[IncomeUse]) -> bool:
    return sorted(left, key=lambda row: row.fragment_id) == sorted(
        right, key=lambda row: row.fragment_id
    )


def _verified_original(row: AllocationOriginal, actual: ReconciliationAction | None) -> bool:
    if actual is None or row.command is None:
        return False
    command, effect = row.command, row.command.effect
    refs = {(ref.table, ref.row_id) for ref in row.original_refs}
    return (
        row.legacy_redemption_id is None
        and row.action_id == row.bank_id == effect.operation_id == actual.action_id
        and row.action_goal_id == effect.goal_id
        and row.action_type == actual.action_type == "ALLOCATE_GOAL"
        and row.action_request_hash == actual.original_request_hash
        and row.action_request_hash == configuration_hash(row.action_request)
        and row.bank_request_hash == configuration_hash(row.bank_request)
        and row.action_request.get("execution") == row.bank_request
        and row.bank_request == command.model_dump(mode="json")
        and row.bank_idempotency_key == actual.original_idempotency_key
        and actual.bank_operation_ids == [row.bank_id]
        and actual.bank_statuses == ["SETTLED"]
        and actual.bank_request_hashes == [row.bank_request_hash]
        and actual.effect_hash == command.effect_hash
        and actual.state == "SERVICE_RECEIPT_VERIFIED"
        and actual.complete_settlement_legs_verified
        and actual.service_receipt_verified
        and len(actual.receipt_ids) == 1
        and actual.receipt_statuses == ["SUCCEEDED"]
        and ("action_plans", row.action_id) in refs
        and ("bank_operations", row.bank_id) in refs
        and all(("action_receipts", identity) in refs for identity in actual.receipt_ids)
        and not actual.issues
        and effect.amount_cents == actual.actual_executed_cents
        and effect.fee_cents == actual.actual_fee_cents
        and effect.loss_cents == actual.actual_loss_cents
    )


def prove_goal_cash_sources(data: GoalCashProvenanceInput) -> GoalCashSourceProof:
    """Only an original cash-only history identifies the cash for each origin.

    Existing goal purchases/redemptions keep aggregate ownership, but record no
    assigned-fragment split between cash and principal. Inventing FIFO would add
    facts. Such histories remain UNKNOWN with their complete original denominator.
    """
    report, ledger = data.reconciliation, data.income
    reasons = list(data.source_issues)
    if report.user_id != data.user_id or report.as_of != data.as_of:
        reasons.append("RECONCILIATION_OWNER_OR_CLOCK_MISMATCH")
    if (
        not data.original_inventory_complete
        or {row.table for row in report.inventory}
        != {
            "accounts",
            "asset_positions",
            "goals",
            "action_plans",
            "bank_operations",
            "simulated_bank_redemptions",
            "action_receipts",
            "simulated_bank_postings",
        }
        or len(report.inventory) != 8
        or not all(row.complete for row in report.inventory)
        or not report.bank_ledger_verified
        or not report.current_application_projection_matched
        or report.audit.status != "VALID"
        or report.audit.chain_status != "VALID"
        or report.audit.reference_status != "VALID"
        or report.audit.errors
        or report.audit.user_id != data.user_id
        or report.audit.epoch_id != data.epoch_id
        or report.state != "MATCHED"
        or report.issues
    ):
        reasons.append("CURRENT_ORIGINAL_BANK_APPLICATION_AUDIT_NOT_VERIFIED")
    if ledger is None or data.income_evidence_id is None or data.income_evidence_hash is None:
        reasons.append("CURRENT_ORIGINAL_INCOME_LEDGER_MISSING")
    elif ledger.user_id != data.user_id or ledger.as_of > data.as_of:
        reasons.append("INCOME_OWNER_OR_CLOCK_MISMATCH")
    goal = next((row for row in report.goal_ownership if row.goal_id == data.goal_id), None)
    if (
        goal is None
        or goal.account_id != data.goal_account_id
        or not goal.current_ownership_proof_verified
        or goal.cash.state != "MATCHED"
        or goal.principal.state != "MATCHED"
    ):
        reasons.append("CURRENT_GOAL_OWNERSHIP_NOT_VERIFIED")
    cash = goal.cash.bank_cents if goal else None
    principal = goal.principal.bank_cents if goal else None
    actions = {row.action_id: row for row in report.actions}
    count_by_table = {row.table: row.actual_count for row in report.inventory}
    if (
        count_by_table.get("action_plans") != len(report.actions)
        or count_by_table.get("goals") != len(report.goal_ownership)
        or count_by_table.get("simulated_bank_postings") != len(report.bank_postings)
        or any(row.actual_count != row.captured_count for row in report.inventory if row.complete)
    ):
        reasons.append("RECONCILIATION_COMPLETE_ROW_DENOMINATOR_MISMATCH")
    original_by_action = {row.action_id: row for row in data.originals}
    if len(original_by_action) != len(data.originals) or len(actions) != len(report.actions):
        reasons.append("DUPLICATE_ORIGINAL_BANK_ACTION")
    bank_inventory = next((row for row in report.inventory if row.table == "bank_operations"), None)
    if bank_inventory is None or bank_inventory.actual_count != len(data.originals):
        reasons.append("ORIGINAL_BANK_OPERATION_INVENTORY_MISMATCH")
    expected_ops = {identity for row in report.actions for identity in row.bank_operation_ids}
    if {row.bank_id for row in data.originals} != expected_ops:
        reasons.append("ORIGINAL_BANK_OPERATION_INVENTORY_MISMATCH")
    all_by_fragment: dict[UUID, int] = {}
    source_by_fragment: dict[UUID, int] = {}
    source_records: dict[UUID, list[AllocationOriginal]] = {}
    verified_allocations: dict[UUID, AllocationOriginal] = {}
    for original in data.originals:
        command = original.command
        if command is None or command.effect.action_type != "ALLOCATE_GOAL":
            if original.action_goal_id == data.goal_id:
                reasons.append("GOAL_CASH_PRINCIPAL_OR_LEGACY_SOURCE_SPLIT_NOT_RECORDED")
            continue
        effect, actual = command.effect, actions.get(original.action_id)
        allocation_goal = next(
            (row for row in report.goal_ownership if row.goal_id == effect.goal_id), None
        )
        if actual is not None and actual.state in {
            "BANK_REJECTION_VERIFIED",
            "PREPARED_NO_BANK_OBSERVED",
        }:
            continue
        reservation = (
            next((row for row in ledger.reservations if row.action_id == original.action_id), None)
            if ledger
            else None
        )
        if (
            not _verified_original(original, actual)
            or effect.user_id != data.user_id
            or effect.goal_id is None
            or allocation_goal is None
            or not allocation_goal.current_ownership_proof_verified
            or effect.destination_account_id != allocation_goal.account_id
            or effect.fee_cents != 0
            or effect.loss_cents != 0
            or sum(use.amount_cents for use in effect.income_uses) != effect.amount_cents
            or reservation is None
            or reservation.operation != "ALLOCATE_GOAL"
            or reservation.state != "COMMITTED"
            or not _same_uses(reservation.uses, effect.income_uses)
        ):
            reasons.append("ORIGINAL_ALLOCATION_BANK_RECEIPT_OR_COMMITTED_USES_NOT_VERIFIED")
            continue
        if effect.goal_id == data.goal_id and effect.destination_account_id != data.goal_account_id:
            reasons.append("SOURCE_GOAL_ACCOUNT_CHANGED")
        verified_allocations[original.action_id] = original
        for use in effect.income_uses:
            all_by_fragment[use.fragment_id] = (
                all_by_fragment.get(use.fragment_id, 0) + use.amount_cents
            )
            if effect.goal_id == data.goal_id:
                source_by_fragment[use.fragment_id] = (
                    source_by_fragment.get(use.fragment_id, 0) + use.amount_cents
                )
                source_records.setdefault(use.fragment_id, []).append(original)
    if ledger:
        for reservation in ledger.reservations:
            if (
                reservation.operation == "ALLOCATE_GOAL"
                and reservation.state == "COMMITTED"
                and reservation.action_id not in verified_allocations
            ):
                reasons.append("COMMITTED_ALLOCATION_ORIGINAL_MISSING")
        if any(
            fragment.assigned_cents != all_by_fragment.get(fragment.fragment_id, 0)
            for fragment in ledger.fragments
        ) or set(all_by_fragment) - {row.fragment_id for row in ledger.fragments}:
            reasons.append("COMPLETE_ASSIGNED_FRAGMENT_DENOMINATOR_MISMATCH")
    history = sorted(data.goal_cash_history, key=lambda row: row.sequence_number)
    reported_history = [
        row for row in report.bank_postings if row.ledger_key == f"GOAL_CASH:{data.goal_id}"
    ]
    if {
        (
            row.posting_id,
            row.operation_id,
            row.sequence_number,
            row.balance_before_cents,
            row.delta_cents,
            row.balance_after_cents,
        )
        for row in history
    } != {
        (
            row.posting_id,
            row.operation_id,
            row.sequence_number,
            row.balance_before_cents,
            row.delta_cents,
            row.balance_after_cents,
        )
        for row in reported_history
    } or len(history) != len(reported_history):
        reasons.append("COMPLETE_ORIGINAL_GOAL_CASH_POSTINGS_MISMATCH")
    if (
        not history
        or len({row.posting_id for row in history}) != len(history)
        or [row.sequence_number for row in history] != list(range(1, len(history) + 1))
        or history[0].entry_kind != "OPENING"
        or history[0].operation_id is not None
        or history[0].balance_before_cents != 0
        or history[0].delta_cents != 0
    ):
        reasons.append("ZERO_ORIGINAL_GOAL_CASH_ANCHOR_OR_COMPLETE_HISTORY_MISSING")
    prior = 0
    for posting in history:
        if (
            posting.account_id != data.goal_account_id
            or posting.ledger_metadata.get("goal_id") != str(data.goal_id)
            or posting.ledger_metadata.get("account_id") != str(data.goal_account_id)
            or posting.balance_before_cents != prior
            or posting.balance_after_cents != prior + posting.delta_cents
            or posting.source_ref.table != "simulated_bank_postings"
            or posting.source_ref.row_id != posting.posting_id
        ):
            reasons.append("ORIGINAL_GOAL_CASH_HISTORY_IDENTITY_OR_CONTINUITY_MISMATCH")
        if posting.entry_kind != "OPENING":
            movement_original = (
                verified_allocations.get(posting.operation_id) if posting.operation_id else None
            )
            if (
                movement_original is None
                or movement_original.command is None
                or movement_original.command.effect.goal_id != data.goal_id
                or posting.delta_cents != movement_original.command.effect.amount_cents
            ):
                reasons.append("GOAL_CASH_MOVEMENT_HAS_NO_EXACT_ORIGINAL_ALLOCATION_SOURCE")
        prior = posting.balance_after_cents
    if cash is None or prior != cash or sum(source_by_fragment.values()) != cash:
        reasons.append("CURRENT_GOAL_CASH_SOURCE_TOTAL_MISMATCH")
    reasons = list(dict.fromkeys(reasons))
    verified = not reasons
    sources = []
    slices = []
    if ledger:
        origins = {row.origin_transaction_id: row for row in ledger.origins}
        for fragment in sorted(ledger.fragments, key=lambda row: row.fragment_id):
            origin = origins[fragment.origin_transaction_id]
            records = source_records.get(fragment.fragment_id, [])
            source_total = source_by_fragment.get(fragment.fragment_id, 0)
            total = all_by_fragment.get(fragment.fragment_id, 0)
            sources.append(
                GoalIncomeSource(
                    fragment_id=fragment.fragment_id,
                    origin_transaction_id=fragment.origin_transaction_id,
                    income_location_account_id=fragment.account_id,
                    origin_account_id=origin.origin_account_id,
                    origin_bank_evidence_id=origin.bank_evidence_id,
                    origin_bank_evidence_hash=origin.bank_evidence_hash,
                    original_assigned_cents=fragment.assigned_cents,
                    verified_all_goal_allocation_cents=total,
                    source_goal_allocation_cents=source_total,
                    other_goal_allocation_cents=total - source_total,
                    source_goal_cash_remaining_cents=source_total if verified else None,
                    original_available_cents=fragment.available_cents,
                    original_reserved_cents=fragment.reserved_cents,
                    original_spent_cents=fragment.spent_cents,
                    source_allocation_action_ids=sorted(row.action_id for row in records),
                    source_original_refs=[ref for row in records for ref in row.original_refs],
                )
            )
            for original in records:
                command = original.command
                assert command is not None and command.effect.policy_version_id is not None
                use = next(
                    row
                    for row in command.effect.income_uses
                    if row.fragment_id == fragment.fragment_id
                )
                slices.append(
                    GoalAllocationSourceSlice(
                        allocation_action_id=original.action_id,
                        original_policy_version_id=command.effect.policy_version_id,
                        fragment_id=use.fragment_id,
                        origin_transaction_id=use.origin_transaction_id,
                        income_location_account_id=use.account_id,
                        original_allocated_cents=use.amount_cents,
                        cash_remaining_cents=use.amount_cents if verified else None,
                        allocation_effect_hash=command.effect_hash,
                        allocation_bank_request_hash=original.bank_request_hash,
                        allocation_action_request_hash=original.action_request_hash,
                        original_refs=[
                            *original.original_refs,
                            *[
                                row.source_ref
                                for row in history
                                if row.operation_id == original.action_id
                            ],
                        ],
                    )
                )
    return GoalCashSourceProof(
        user_id=data.user_id,
        epoch_id=data.epoch_id,
        as_of=data.as_of,
        goal_id=data.goal_id,
        goal_account_id=data.goal_account_id,
        goal_policy_version_id=data.goal_policy_version_id,
        state="VERIFIED_CASH_ONLY" if verified else "UNKNOWN",
        original_goal_cash_cents=cash if cash is not None and cash >= 0 else None,
        original_goal_principal_cents=principal
        if principal is not None and principal >= 0
        else None,
        exactly_attributed_goal_cash_cents=cash if verified else None,
        sources=sources,
        original_allocation_slices=sorted(
            slices, key=lambda row: (row.allocation_action_id, row.fragment_id)
        ),
        complete_original_income_fragment_count=len(ledger.fragments) if ledger else None,
        complete_original_bank_operation_count=bank_inventory.actual_count
        if bank_inventory is not None
        and data.original_inventory_complete
        and bank_inventory.complete
        else None,
        captured_original_bank_operation_count=len(data.originals),
        original_operation_refs=[ref for row in data.originals for ref in row.original_refs],
        original_goal_cash_posting_refs=[row.source_ref for row in history],
        source_binding_hash=configuration_hash(data.model_dump(mode="json")),
        reconciliation_input_hash=report.input_hash,
        income_evidence_id=data.income_evidence_id,
        income_evidence_hash=data.income_evidence_hash,
        reasons=reasons,
    )
