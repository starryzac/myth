"""Current native allocation provenance; old action source hashes are never rebased.

This pure read proof verifies closed supplied records. Only actual owned adapters
can authenticate Bank originals, current income evidence and the complete audit.
"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_release_execution import (
    GoalReleaseSettlementOriginal,
    GoalReleaseUse,
    Hash,
    validate_goal_release_settlement,
)
from app.domain.full_goal_transfer import (
    GoalTransferEffect,
    GoalTransferSettlementOriginal,
    validate_goal_transfer_bank_original,
)
from app.domain.full_goal_transfer_provenance import (
    GoalTransferOwnershipResult,
    ReceivedGoalTransferLot,
)
from app.domain.full_reconciliation import FullReconciliationReport
from app.domain.goal_release_provenance import (
    AllocationOriginal,
    OriginalRowReference,
    _same_uses,
    _verified_original,
)
from app.domain.income_ledger import IncomeLedger
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, model_validator


class GoalTransferHistoricalSource(BoundaryModel):
    operation_id: UUID
    source_goal_id: UUID
    original_action_request_hash: Hash
    source_provenance_hash: Hash
    source_protocol: Literal[
        "PROTECTED_CASH_V1", "GOAL_TRANSFER_BASIS_V1", "GOAL_TRANSFER_CURRENT_V1"
    ]


class GoalTransferCurrentInput(BoundaryModel):
    protocol: Literal["goal-transfer-current-provenance-v1"] = "goal-transfer-current-provenance-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    source_goal_id: UUID
    target_goal_id: UUID
    current_income: IncomeLedger
    income_evidence_id: UUID
    income_evidence_hash: Hash
    income_evidence_row: dict[str, Any]
    reconciliation: FullReconciliationReport
    originals: Annotated[list[AllocationOriginal], Field(max_length=10000)]
    releases: Annotated[list[GoalReleaseSettlementOriginal], Field(max_length=10000)]
    transfers: Annotated[list[GoalTransferSettlementOriginal], Field(max_length=10000)]
    historical_sources: Annotated[list[GoalTransferHistoricalSource], Field(max_length=20000)]
    original_row_refs: Annotated[list[OriginalRowReference], Field(max_length=130000)]


class GoalTransferCurrentProof(GoalTransferCurrentInput):
    proof_hash: Hash
    scope_input_hash: Hash
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def frozen_current_proof(self) -> Self:
        data = GoalTransferCurrentInput.model_validate(
            self.model_dump(exclude={"proof_hash", "scope_input_hash", "bank_authority"})
        )
        if self.proof_hash != configuration_hash(
            data.model_dump(mode="json")
        ) or self.scope_input_hash != current_scope_input_hash(data):
            raise ValueError("Current provenance proof or reviewed economic source hash differs")
        replay_current_goal_transfer(data)
        return self


def current_scope_input_hash(data: GoalTransferCurrentInput) -> str:
    """Fixed semantic payload excludes observation clocks/audit sequence, not money facts."""
    income = data.current_income.model_dump(mode="json", exclude={"as_of"})
    report = data.reconciliation
    return configuration_hash(
        {
            "protocol": data.protocol,
            "user_id": str(data.user_id),
            "epoch_id": str(data.epoch_id),
            "source_goal_id": str(data.source_goal_id),
            "target_goal_id": str(data.target_goal_id),
            "income": income,
            "income_evidence_hash": configuration_hash(income),
            "originals": [row.model_dump(mode="json") for row in data.originals],
            "releases": [row.model_dump(mode="json") for row in data.releases],
            "transfers": [row.model_dump(mode="json") for row in data.transfers],
            "historical_sources": [row.model_dump(mode="json") for row in data.historical_sources],
            "goals": [row.model_dump(mode="json") for row in report.goal_ownership],
            "account_cash": [row.model_dump(mode="json") for row in report.account_cash],
            "position_principals": [
                row.model_dump(mode="json") for row in report.position_principals
            ],
            "bank_postings": [row.model_dump(mode="json") for row in report.bank_postings],
        }
    )


def make_current_goal_transfer_proof(data: GoalTransferCurrentInput) -> GoalTransferCurrentProof:
    return GoalTransferCurrentProof.model_validate(
        data.model_dump()
        | {
            "proof_hash": configuration_hash(data.model_dump(mode="json")),
            "scope_input_hash": current_scope_input_hash(data),
        }
    )


def replay_current_goal_transfer(data: GoalTransferCurrentInput) -> GoalTransferOwnershipResult:
    report, income, now = data.reconciliation, data.current_income, data.as_of
    evidence = data.income_evidence_row
    evidence_content = evidence.get("content")
    if not isinstance(evidence_content, dict):
        raise ValueError("Current income evidence content is absent")
    if (
        evidence.get("id") != str(data.income_evidence_id)
        or evidence.get("user_id") != str(data.user_id)
        or evidence.get("source_type") != "SIMULATED_NEW_FUNDS_LEDGER"
        or evidence.get("evidence_level") != "BANK_CONFIRMED"
        or evidence.get("status") != "VALID"
        or evidence.get("content_hash") != data.income_evidence_hash
        or configuration_hash(evidence_content) != data.income_evidence_hash
    ):
        raise ValueError(
            "Actual current income evidence owner, source and original content hash differ"
        )
    original_income = IncomeLedger.model_validate_json(json.dumps(evidence["content"]))
    if (
        original_income.model_dump(exclude={"as_of"}) != income.model_dump(exclude={"as_of"})
        or original_income.as_of > now
    ):
        raise ValueError(
            "Current income origins and money fields differ from their actual raw evidence"
        )

    inventories = {row.table: row for row in report.inventory}
    expected_tables = {
        "accounts",
        "asset_positions",
        "goals",
        "action_plans",
        "bank_operations",
        "simulated_bank_redemptions",
        "action_receipts",
        "simulated_bank_postings",
    }
    if (
        report.user_id != data.user_id
        or report.as_of != now
        or income.user_id != data.user_id
        or income.as_of > now
        or data.source_goal_id == data.target_goal_id
        or report.state != "MATCHED"
        or report.issues
        or not report.bank_ledger_verified
        or not report.current_application_projection_matched
        or report.audit.status != "VALID"
        or report.audit.chain_status != "VALID"
        or report.audit.reference_status != "VALID"
        or report.audit.errors
        or (report.audit.user_id, report.audit.epoch_id) != (data.user_id, data.epoch_id)
        or set(inventories) != expected_tables
        or len(inventories) != len(report.inventory)
        or not all(
            row.complete and row.actual_count == row.captured_count for row in report.inventory
        )
    ):
        raise ValueError(
            "Complete current owned Bank/application/income/audit originals are required"
        )
    originals = {row.bank_id: row for row in data.originals}
    actions = {row.action_id: row for row in report.actions}
    goals = {row.goal_id: row for row in report.goal_ownership}
    if (
        len(originals) != len(data.originals)
        or len(actions) != len(report.actions)
        or len(goals) != len(report.goal_ownership)
        or inventories["action_plans"].actual_count != len(actions)
        or inventories["goals"].actual_count != len(goals)
        or inventories["simulated_bank_postings"].actual_count != len(report.bank_postings)
    ):
        raise ValueError("Current original Bank/action/goal/posting denominator is incomplete")
    special: list[GoalReleaseSettlementOriginal | GoalTransferSettlementOriginal] = [
        *data.releases,
        *data.transfers,
    ]
    special_ids = {row.bank_operation_id for row in special}
    history = {row.operation_id: row for row in data.historical_sources}
    expected_banks = {identity for row in report.actions for identity in row.bank_operation_ids}
    if (
        len(special_ids) != len(special)
        or len(history) != len(data.historical_sources)
        or set(history) != special_ids
        or special_ids & set(originals)
        or special_ids | set(originals) != expected_banks
        or inventories["bank_operations"].actual_count != len(expected_banks)
    ):
        raise ValueError(
            "All current ordinary and dedicated Bank/source-hash originals "
            "remain present exactly once"
        )
    refs = {(row.table, row.row_id): row.row_hash for row in data.original_row_refs}
    if len(refs) != len(data.original_row_refs):
        raise ValueError("Current original row references repeat")
    for table in ("action_plans", "action_receipts", "bank_operations", "simulated_bank_postings"):
        if sum(kind == table for kind, _ in refs) != inventories[table].actual_count:
            raise ValueError("Current complete raw row-reference denominator differs")
    expected_ref_ids = {
        "bank_operations": expected_banks,
        "action_plans": set(actions),
        "action_receipts": {identity for row in report.actions for identity in row.receipt_ids},
        "simulated_bank_postings": {row.posting_id for row in report.bank_postings},
    }
    if set(table for table, _ in refs) - set(expected_ref_ids) or any(
        {identity for kind, identity in refs if kind == table} != identities
        for table, identities in expected_ref_ids.items()
    ):
        raise ValueError("Complete current raw row-reference IDs differ from actual reconciliation")
    participating = {data.source_goal_id, data.target_goal_id}
    incoming: set[UUID] = set()
    outgoing: set[UUID] = set()
    for original in special:
        source = original.command.effect.source_goal_id
        participating.add(source)
        if isinstance(original, GoalTransferSettlementOriginal):
            validate_goal_transfer_bank_original(original, now)
            transfer_effect = original.command.effect
            participating.add(transfer_effect.target_goal_id)
            incoming.add(transfer_effect.target_goal_id)
            outgoing.add(source)
            protocol = (
                "GOAL_TRANSFER_CURRENT_V1"
                if getattr(transfer_effect.scope, "source_provenance_version", None)
                else "GOAL_TRANSFER_BASIS_V1"
            )
        else:
            validate_goal_release_settlement(original, now)
            protocol = "PROTECTED_CASH_V1"
        if refs.get(
            ("bank_operations", original.bank_operation_id)
        ) != original.original_bank_row_hash or any(
            refs.get(("simulated_bank_postings", row.posting_id)) != row.original_row_hash
            for row in original.postings
        ):
            raise ValueError(
                "Dedicated Bank or posting raw hash differs from the complete current originals"
            )
        reference = history[original.bank_operation_id]
        actual = actions.get(original.action_plan_id)
        if (
            original.user_id != data.user_id
            or original.command.effect.epoch_id != data.epoch_id
            or original.status != "SETTLED"
            or actual is None
            or actual.state != "SERVICE_RECEIPT_VERIFIED"
            or not actual.complete_settlement_legs_verified
            or not actual.service_receipt_verified
            or actual.issues
            or actual.bank_operation_ids != [original.bank_operation_id]
            or actual.bank_request_hashes != [original.request_hash]
            or actual.effect_hash != original.command.effect_hash
            or len(actual.receipt_ids) != 1
            or actual.actual_executed_cents != original.command.effect.amount_cents
            or reference.source_goal_id != source
            or reference.source_provenance_hash != original.command.effect.source_provenance_hash
            or reference.original_action_request_hash != actual.original_request_hash
            or reference.source_protocol != protocol
        ):
            raise ValueError(
                "Historical source hash must match that exact original action and settled receipt"
            )
    if incoming & outgoing or data.source_goal_id in incoming:
        raise ValueError("Received lots cannot become a later source in the single-hop protocol")
    if not participating <= set(goals):
        raise ValueError("A historical participating goal was omitted")
    for identity in participating:
        participating_goal = goals[identity]
        if (
            participating_goal.account_id is None
            or not participating_goal.current_ownership_proof_verified
            or participating_goal.cash.state != "MATCHED"
            or participating_goal.principal.state != "MATCHED"
            or participating_goal.cash.bank_cents is None
            or participating_goal.principal.bank_cents != 0
            or participating_goal.position_ids
        ):
            raise ValueError(
                "Participating goals require verified cash-only current ownership "
                "without asset split"
            )
    fragments = {row.fragment_id: row for row in income.fragments}
    origins = {row.origin_transaction_id: row for row in income.origins}
    reserved = {row.action_id: row for row in income.reservations}
    assigned: dict[UUID, int] = {}
    allocations: dict[tuple[UUID, UUID], GoalReleaseUse] = {}
    allocation_goals: dict[tuple[UUID, UUID], UUID] = {}
    verified_allocations: set[UUID] = set()
    cash = {identity: 0 for identity in participating}
    for native_original in data.originals:
        native_command = native_original.command
        actual = actions.get(native_original.action_id)
        if native_command is None:
            if (
                native_original.bank_id not in special_ids
                and native_original.action_goal_id in participating
            ):
                raise ValueError(
                    "A participating goal has an unexplained legacy cash/principal split"
                )
            continue
        native_effect = native_command.effect
        if any(
            refs.get((row.table, row.row_id)) != row.row_hash
            for row in native_original.original_refs
        ):
            raise ValueError(
                "An ordinary native original reference differs from the complete current rows"
            )
        if (
            actual is None
            or native_effect.user_id != data.user_id
            or native_effect.operation_id != native_original.bank_id
            or native_original.bank_id != native_original.action_id
            or native_original.bank_request != native_command.model_dump(mode="json")
            or configuration_hash(native_original.bank_request) != native_original.bank_request_hash
            or configuration_hash(native_original.action_request)
            != native_original.action_request_hash
            or native_original.action_request.get("execution") != native_original.bank_request
            or actual.original_request_hash != native_original.action_request_hash
            or actual.effect_hash != native_command.effect_hash
            or actual.bank_operation_ids != [native_original.bank_id]
            or actual.state not in {"SERVICE_RECEIPT_VERIFIED", "BANK_REJECTION_VERIFIED"}
            or actual.issues
        ):
            raise ValueError(
                "A current ordinary Bank original has no complete immutable native explanation"
            )
        if native_effect.action_type != "ALLOCATE_GOAL":
            if native_effect.goal_id in participating and (
                actual is None or actual.state != "BANK_REJECTION_VERIFIED"
            ):
                raise ValueError(
                    "Goal asset cash/principal movements lack an original allocation split"
                )
            continue
        if actual is not None and actual.state == "BANK_REJECTION_VERIFIED":
            continue
        goal = goals.get(native_effect.goal_id) if native_effect.goal_id else None
        reservation = reserved.get(native_original.action_id)
        if (
            not _verified_original(native_original, actual)
            or native_effect.user_id != data.user_id
            or native_effect.goal_id is None
            or native_effect.policy_version_id is None
            or goal is None
            or not goal.current_ownership_proof_verified
            or native_effect.destination_account_id != goal.account_id
            or native_effect.fee_cents != 0
            or native_effect.loss_cents != 0
            or sum(row.amount_cents for row in native_effect.income_uses)
            != native_effect.amount_cents
            or reservation is None
            or reservation.operation != "ALLOCATE_GOAL"
            or reservation.state != "COMMITTED"
            or not _same_uses(reservation.uses, native_effect.income_uses)
        ):
            raise ValueError(
                "Each current assigned amount requires its exact committed native "
                "ALLOCATION/Bank/receipt"
            )
        verified_allocations.add(native_original.action_id)
        for use in native_effect.income_uses:
            fragment, origin = (
                fragments.get(use.fragment_id),
                origins.get(use.origin_transaction_id),
            )
            if (
                fragment is None
                or origin is None
                or (fragment.origin_transaction_id, fragment.account_id)
                != (use.origin_transaction_id, use.account_id)
            ):
                raise ValueError("Native allocation changed its original income origin/location")
            allocation_key = native_original.action_id, use.fragment_id
            if allocation_key in allocations:
                raise ValueError("Original allocation slice is duplicated")
            row = GoalReleaseUse(
                allocation_action_id=native_original.action_id,
                original_policy_version_id=native_effect.policy_version_id,
                fragment_id=use.fragment_id,
                origin_transaction_id=use.origin_transaction_id,
                income_location_account_id=use.account_id,
                amount_cents=use.amount_cents,
                allocation_effect_hash=native_command.effect_hash,
                allocation_bank_request_hash=native_original.bank_request_hash,
                allocation_action_request_hash=native_original.action_request_hash,
            )
            allocations[allocation_key], allocation_goals[allocation_key] = (
                row,
                native_effect.goal_id,
            )
            assigned[use.fragment_id] = assigned.get(use.fragment_id, 0) + use.amount_cents
            if native_effect.goal_id in cash:
                cash[native_effect.goal_id] += use.amount_cents
    if (
        any(
            row.operation == "ALLOCATE_GOAL"
            and row.state == "COMMITTED"
            and row.action_id not in verified_allocations
            for row in income.reservations
        )
        or any(row.assigned_cents != assigned.get(row.fragment_id, 0) for row in income.fragments)
        or set(assigned) - set(fragments)
    ):
        raise ValueError(
            "Complete current assigned-fragment denominator does not equal "
            "all verified native allocations"
        )
    consumed: dict[tuple[UUID, UUID], int] = {}
    received: list[ReceivedGoalTransferLot] = []
    for original in special:
        uses = (
            original.command.effect.source_uses
            if isinstance(original, GoalTransferSettlementOriginal)
            else original.command.effect.release_uses
        )
        source_goal_id = original.command.effect.source_goal_id
        for historical_use in uses:
            use_key = historical_use.allocation_action_id, historical_use.fragment_id
            original_slice = allocations.get(use_key)
            if (
                original_slice is None
                or allocation_goals[use_key] != source_goal_id
                or any(
                    getattr(original_slice, field) != getattr(historical_use, field)
                    for field in GoalReleaseUse.model_fields
                    if field != "amount_cents"
                )
            ):
                raise ValueError(
                    "Historical transfer/release cannot launder a changed original allocation slice"
                )
            consumed[use_key] = consumed.get(use_key, 0) + historical_use.amount_cents
            if consumed[use_key] > original_slice.amount_cents:
                raise ValueError(
                    "New income or allocation cannot repay an old over-consumed source slice"
                )
            cash[source_goal_id] -= historical_use.amount_cents
        if isinstance(original, GoalTransferSettlementOriginal):
            received_effect = original.command.effect
            cash[received_effect.target_goal_id] += received_effect.amount_cents
            received.append(
                ReceivedGoalTransferLot(
                    transfer_operation_id=original.bank_operation_id,
                    source_goal_id=received_effect.source_goal_id,
                    target_goal_id=received_effect.target_goal_id,
                    target_account_id=received_effect.destination_account_id,
                    amount_cents=received_effect.amount_cents,
                    original_allocation_uses=received_effect.source_uses,
                )
            )
    if any(cash[identity] != goals[identity].cash.bank_cents for identity in participating):
        raise ValueError(
            "Current GOAL_CASH is not explained by exact original allocations "
            "and all dedicated effects"
        )
    remaining: dict[UUID, list[GoalReleaseUse]] = {identity: [] for identity in participating}
    for key, row in sorted(allocations.items()):
        remaining_goal_id = allocation_goals[key]
        amount = row.amount_cents - consumed.get(key, 0)
        if remaining_goal_id in remaining and remaining_goal_id not in incoming and amount > 0:
            remaining[remaining_goal_id].append(row.model_copy(update={"amount_cents": amount}))
    return GoalTransferOwnershipResult(
        goal_cash_cents=cash,
        remaining_original_uses=remaining,
        received_lots=received,
        input_hash=configuration_hash(data.model_dump(mode="json")),
    )


def validate_current_goal_transfer_effect(
    proof: GoalTransferCurrentProof, effect: GoalTransferEffect
) -> None:
    ownership = replay_current_goal_transfer(proof)
    if (
        (proof.user_id, proof.epoch_id, proof.source_goal_id, proof.target_goal_id)
        != (effect.user_id, effect.epoch_id, effect.source_goal_id, effect.target_goal_id)
        or proof.proof_hash != effect.source_provenance_hash
        or proof.as_of != effect.valid_from
        or getattr(effect.scope, "current_provenance_hash", None) != proof.scope_input_hash
    ):
        raise ValueError(
            "New paired effect must bind its own current proof and reviewed source hash"
        )
    available = {
        (row.allocation_action_id, row.fragment_id): row
        for row in ownership.remaining_original_uses[effect.source_goal_id]
    }
    for use in effect.source_uses:
        row = available.get((use.allocation_action_id, use.fragment_id))
        if (
            row is None
            or use.amount_cents > row.amount_cents
            or any(
                getattr(use, field) != getattr(row, field)
                for field in GoalReleaseUse.model_fields
                if field != "amount_cents"
            )
        ):
            raise ValueError(
                "New paired effect does not preserve its exact available original allocation uses"
            )
