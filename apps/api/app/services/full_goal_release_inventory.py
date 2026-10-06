"""Fresh read-only release sources and lifetime policy occupancy; never a grant."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.db.models import ActionPlan, ActionReceipt, BankOperation, Goal, SimulatedBankPosting, User
from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_release_audit import read_frozen_goal_release_action
from app.domain.full_goal_release_execution import (
    Cents,
    GoalReleasePolicyUsage,
    GoalReleaseResidualResult,
    GoalReleaseSettlementOriginal,
    GoalReleaseUse,
    Hash,
    compute_release_policy_usage,
    replay_goal_release_residuals,
)
from app.domain.goal_release_provenance import GoalCashSourceProof, OriginalRowReference
from app.domain.income_ledger import IncomeLedger
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.full_goal_release_reader import (
    read_goal_release_bank_command,
    verify_goal_release_legs,
)
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.full_reconciliation import full_reconciliation
from app.services.goal_release_provenance import read_goal_cash_source_proof
from app.services.historical_read import historical_ledger_scope, verify_historical_ledger
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from app.services.simulated_bank import ledger_heads
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class ReleaseInventoryTable(BoundaryModel):
    table: str
    actual_count: Cents | None
    captured_count: Cents
    complete: bool
    original_refs: list[OriginalRowReference]


class GoalReleaseInventory(BoundaryModel):
    schema_version: Literal["goal-release-inventory-v1"] = "goal-release-inventory-v1"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    goal_id: UUID
    goal_policy_version_id: UUID
    full_policy_id: UUID
    state: Literal["VERIFIED", "UNKNOWN"]
    original_basis: GoalCashSourceProof
    residual: GoalReleaseResidualResult
    release_uses_available: list[GoalReleaseUse]
    policy_usage: GoalReleasePolicyUsage
    inventory: list[ReleaseInventoryTable]
    release_originals: list[GoalReleaseSettlementOriginal]
    financial_truth_verified: bool
    application_projection_matched: bool
    source_binding_hash: Hash
    reasons: list[str]
    bank_authority: Literal[False] = False
    funds_released: Literal[False] = False
    creates_new_income: Literal[False] = False
    changes_original_assigned_income: Literal[False] = False


def validate_retained_goal_cash_basis(
    basis: GoalCashSourceProof,
    original_rows: dict[tuple[str, UUID], dict[str, Any]],
    income: IncomeLedger,
    current_nonrelease_bank_ids: set[UUID],
) -> None:
    """Retained success is insufficient: recheck all its original bytes and denominators."""
    if basis.state != "VERIFIED_CASH_ONLY" or basis.reasons or income.user_id != basis.user_id:
        raise ValueError("Retained basis was not an actual complete cash-only source proof")
    if len(basis.sources) != len(
        income.fragments
    ) or basis.complete_original_income_fragment_count != len(income.fragments):
        raise ValueError("Original complete income denominator changed")
    assigned = {row.fragment_id: row.original_assigned_cents for row in basis.sources}
    if assigned != {row.fragment_id: row.assigned_cents for row in income.fragments}:
        raise ValueError(
            "Original assigned income changed; a retained basis cannot guess a new split"
        )
    origins = {row.origin_transaction_id: row for row in income.origins}
    fragments = {row.fragment_id: row for row in income.fragments}
    for source in basis.sources:
        origin, fragment = (
            origins.get(source.origin_transaction_id),
            fragments.get(source.fragment_id),
        )
        if (
            origin is None
            or fragment is None
            or (
                origin.origin_account_id,
                origin.bank_evidence_id,
                origin.bank_evidence_hash,
                fragment.origin_transaction_id,
                fragment.account_id,
            )
            != (
                source.origin_account_id,
                source.origin_bank_evidence_id,
                source.origin_bank_evidence_hash,
                source.origin_transaction_id,
                source.income_location_account_id,
            )
        ):
            raise ValueError("Retained income origin or location differs from the current original")
    refs = [*basis.original_operation_refs, *basis.original_goal_cash_posting_refs]
    for source in basis.sources:
        refs.extend(source.source_original_refs)
    for part in basis.original_allocation_slices:
        refs.extend(part.original_refs)
    for ref in refs:
        row = original_rows.get((ref.table, ref.row_id))
        if row is None or configuration_hash(row) != ref.row_hash:
            raise ValueError("Retained original allocation, bank, receipt or posting bytes differ")
    original_banks = {
        ref.row_id for ref in basis.original_operation_refs if ref.table == "bank_operations"
    }
    if original_banks != current_nonrelease_bank_ids:
        raise ValueError(
            "New non-release bank activity requires a newly proved complete source basis"
        )


def read_goal_release_inventory(
    session: Session,
    user_id: UUID,
    goal_id: UUID,
    expected_epoch_id: UUID,
    expected_goal_policy_version_id: UUID,
    full_policy_id: UUID,
    now: datetime,
) -> GoalReleaseInventory:
    _read_snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    goal = session.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
    epoch = current_audit_epoch(session, user_id)
    if user is None or not user.is_simulated or goal is None or goal.account_id is None:
        raise PolicyLifecycleError("NOT_FOUND", "当前模拟用户目标或原账户不存在", 404)
    if epoch is None or epoch.status != "OPEN" or epoch.id != expected_epoch_id:
        raise PolicyLifecycleError("STALE_AUDIT_EPOCH", "回拨库存需要当前开放epoch", 409)
    if goal.policy_version_id != expected_goal_policy_version_id:
        raise PolicyLifecycleError("STALE_GOAL_SOURCE", "原目标版本已变化", 409)
    with session.no_autoflush, historical_ledger_scope(session):
        return _read(session, user_id, goal, expected_epoch_id, full_policy_id, now)


def _read(
    session: Session, user_id: UUID, goal: Goal, epoch_id: UUID, full_policy_id: UUID, now: datetime
) -> GoalReleaseInventory:
    reasons: list[str] = []
    captured: dict[str, list[Any]] = {}
    inventory: list[ReleaseInventoryTable] = []
    originals_by_ref: dict[tuple[str, UUID], dict[str, Any]] = {}
    for model in (ActionPlan, BankOperation, ActionReceipt, SimulatedBankPosting):
        count = session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
        limit = 100000 if model is SimulatedBankPosting else 10000
        rows: list[Any] = list(
            session.scalars(
                select(model).where(model.user_id == user_id).order_by(model.id).limit(limit)
            )
        )
        complete = (
            type(count) is int
            and count == len(rows)
            and all(row.user_id == user_id for row in rows)
        )
        captured[model.__tablename__] = rows
        refs = []
        for row in rows:
            original = row_copy(row)
            originals_by_ref[(model.__tablename__, row.id)] = original
            refs.append(
                OriginalRowReference(
                    table=model.__tablename__, row_id=row.id, row_hash=configuration_hash(original)
                )
            )
        inventory.append(
            ReleaseInventoryTable(
                table=model.__tablename__,
                actual_count=count if type(count) is int else None,
                captured_count=len(rows),
                complete=complete,
                original_refs=refs,
            )
        )
        if not complete:
            reasons.append("CURRENT_" + model.__tablename__.upper() + "_INVENTORY_INCOMPLETE")
    actions = {row.id: row for row in captured[ActionPlan.__tablename__]}
    banks: list[BankOperation] = captured[BankOperation.__tablename__]
    release_banks = [
        row
        for row in banks
        if row.operation_type == "RELEASE_GOAL"
        or row.request.get("protocol") == "full-goal-release-bank-v1"
    ]
    retained: list[GoalCashSourceProof] = []
    for action in actions.values():
        if "goal_release_execution" not in action.request:
            if action.goal_id == goal.id and action.action_type in {
                "RELEASE_GOAL",
                "ASSET_PURCHASE",
                "ASSET_REDEEM",
            }:
                reasons.append("SOURCE_GOAL_LEGACY_RELEASE_OR_ASSET_SPLIT_NOT_SUPPORTED")
            continue
        try:
            command, base = read_frozen_goal_release_action(row_copy(action))
            if command.effect.source_goal_id == goal.id:
                retained.append(base)
        except (ValueError, TypeError, KeyError):
            reasons.append("CURRENT_RELEASE_ACTION_OR_RETAINED_BASIS_NOT_VERIFIED")
    current = read_goal_cash_source_proof(
        session, user_id, goal.id, epoch_id, goal.policy_version_id, now
    )
    basis = (
        current
        if current.state == "VERIFIED_CASH_ONLY"
        else (
            sorted(retained, key=lambda row: (row.as_of, row.source_binding_hash))[0]
            if retained
            else current
        )
    )
    if (
        basis.user_id,
        basis.epoch_id,
        basis.goal_id,
        basis.goal_account_id,
        basis.goal_policy_version_id,
    ) != (user_id, epoch_id, goal.id, goal.account_id, goal.policy_version_id) or basis.as_of > now:
        reasons.append("RETAINED_BASIS_CURRENT_OWNER_EPOCH_GOAL_OR_VERSION_DIFFERS")
    if basis.state != "VERIFIED_CASH_ONLY":
        reasons.append("ORIGINAL_CASH_ONLY_BASIS_MISSING")
    income = None
    try:
        income = read_income_state(session, user_id, now)
        validate_retained_goal_cash_basis(
            basis,
            originals_by_ref,
            income.ledger,
            {row.id for row in banks if row not in release_banks},
        )
    except (PolicyLifecycleError, ValueError):
        reasons.append("CURRENT_COMPLETE_ORIGINAL_ALLOCATION_DENOMINATOR_NOT_VERIFIED")
    report = full_reconciliation(session, user_id, now)
    bank_verified = False
    heads: dict[str, SimulatedBankPosting] = {}
    try:
        verify_historical_ledger(session, user_id)
        heads = ledger_heads(session, user_id)
        bank_verified = (
            report.bank_ledger_verified
            and report.audit.status == "VALID"
            and all(row.complete for row in report.inventory)
        )
    except PolicyLifecycleError:
        reasons.append("CURRENT_COMPLETE_INDEPENDENT_BANK_CHAIN_NOT_VERIFIED")
    if not bank_verified:
        reasons.append("CURRENT_BANK_LEDGER_OR_EXACT_AUDIT_NOT_VERIFIED")
    original_releases = []
    for bank in release_banks:
        action = actions.get(bank.action_plan_id)
        try:
            if action is None:
                raise ValueError("Original action missing")
            command = read_goal_release_bank_command(session, bank, now)
            if bank.status == "SETTLED":
                verify_goal_release_legs(session, bank, now)
            posting_rows = [
                row
                for row in captured[SimulatedBankPosting.__tablename__]
                if row.operation_id == bank.id
            ]
            original_releases.append(_original(bank, command.model_dump(mode="json"), posting_rows))
        except (ValueError, TypeError, KeyError, PolicyLifecycleError):
            reasons.append("CURRENT_RELEASE_BANK_OR_EXACT_THREE_LEGS_NOT_VERIFIED")
    cash = heads.get(f"GOAL_CASH:{goal.id}")
    principal = heads.get(f"GOAL_PRINCIPAL:{goal.id}")
    for head in (cash, principal):
        if (
            head is None
            or head.user_id != user_id
            or head.account_id != goal.account_id
            or head.ledger_metadata != {"goal_id": str(goal.id), "account_id": str(goal.account_id)}
        ):
            reasons.append("CURRENT_GOAL_BANK_HEAD_IDENTITY_MISSING")
    binding = configuration_hash(
        {
            "user_id": str(user_id),
            "epoch_id": str(epoch_id),
            "as_of": now.isoformat(),
            "goal": row_copy(goal),
            "tables": {table: [row_copy(row) for row in rows] for table, rows in captured.items()},
            "inventory": [row.model_dump(mode="json") for row in inventory],
            "heads": {key: row_copy(row) for key, row in sorted(heads.items())},
            "income": income.ledger.model_dump(mode="json") if income else None,
            "income_evidence_id": str(income.evidence_id) if income else None,
            "income_evidence_hash": income.evidence_hash if income else None,
            "reconciliation_input_hash": report.input_hash,
        }
    )
    complete = all(row.complete for row in inventory) and not reasons
    residual = replay_goal_release_residuals(
        basis,
        original_releases,
        actual_release_operation_count=len(release_banks) if complete else None,
        whole_bank_source_verified=bank_verified and complete,
        current_goal_cash_cents=cash.balance_after_cents if cash else None,
        current_goal_principal_cents=principal.balance_after_cents if principal else None,
        current_assigned_by_fragment={
            row.fragment_id: row.assigned_cents for row in income.ledger.fragments
        }
        if income
        else {},
        current_source_binding_hash=binding,
        now=now,
    )
    usage = compute_release_policy_usage(
        user_id,
        full_policy_id,
        original_releases,
        actual_release_operation_count=len(release_banks) if complete else None,
        whole_bank_source_verified=bank_verified and complete,
        current_source_binding_hash=binding,
        now=now,
    )
    reasons.extend(residual.reasons)
    reasons.extend(usage.reasons)
    available = []
    parts = {
        (row.allocation_action_id, row.fragment_id): row for row in basis.original_allocation_slices
    }
    if not reasons:
        for remaining in residual.remaining:
            if remaining.cash_remaining_cents is not None and remaining.cash_remaining_cents > 0:
                source = parts[(remaining.allocation_action_id, remaining.fragment_id)]
                available.append(
                    GoalReleaseUse(
                        allocation_action_id=source.allocation_action_id,
                        original_policy_version_id=source.original_policy_version_id,
                        fragment_id=source.fragment_id,
                        origin_transaction_id=source.origin_transaction_id,
                        income_location_account_id=source.income_location_account_id,
                        allocation_effect_hash=source.allocation_effect_hash,
                        allocation_bank_request_hash=source.allocation_bank_request_hash,
                        allocation_action_request_hash=source.allocation_action_request_hash,
                        amount_cents=remaining.cash_remaining_cents,
                    )
                )
    return GoalReleaseInventory(
        user_id=user_id,
        epoch_id=epoch_id,
        as_of=now,
        goal_id=goal.id,
        goal_policy_version_id=goal.policy_version_id,
        full_policy_id=full_policy_id,
        state="VERIFIED" if not reasons else "UNKNOWN",
        original_basis=basis,
        residual=residual,
        release_uses_available=available,
        policy_usage=usage,
        inventory=inventory,
        release_originals=original_releases,
        financial_truth_verified=bank_verified and complete,
        application_projection_matched=report.current_application_projection_matched,
        source_binding_hash=binding,
        reasons=list(dict.fromkeys(reasons)),
    )


def _original(
    bank: BankOperation, command: dict[str, Any], rows: list[SimulatedBankPosting]
) -> GoalReleaseSettlementOriginal:
    return GoalReleaseSettlementOriginal.model_validate_json(
        json.dumps(
            {
                "command": command,
                "bank_operation_id": str(bank.id),
                "action_plan_id": str(bank.action_plan_id),
                "user_id": str(bank.user_id),
                "operation_type": bank.operation_type,
                "request_hash": bank.request_hash,
                "business_key": bank.business_key,
                "idempotency_key": bank.idempotency_key,
                "status": bank.status,
                "requested_at": bank.requested_at.isoformat(),
                "available_at": bank.available_at.isoformat(),
                "settled_at": bank.settled_at.isoformat() if bank.settled_at else None,
                "original_bank_row_hash": configuration_hash(row_copy(bank)),
                "postings": [
                    {
                        "posting_id": str(row.id),
                        "user_id": str(row.user_id),
                        "operation_id": str(row.operation_id),
                        "ledger_key": row.ledger_key,
                        "ledger_dimension": row.ledger_dimension,
                        "ledger_metadata": row.ledger_metadata,
                        "leg_ref": row.leg_ref,
                        "account_id": str(row.account_id),
                        "position_id": row.position_id,
                        "redemption_id": row.redemption_id,
                        "external_fact_id": row.external_fact_id,
                        "previous_posting_id": str(row.previous_posting_id),
                        "sequence_number": row.sequence_number,
                        "entry_kind": row.entry_kind,
                        "balance_before_cents": row.balance_before_cents,
                        "delta_cents": row.delta_cents,
                        "balance_after_cents": row.balance_after_cents,
                        "occurred_at": row.occurred_at.isoformat(),
                        "original_row_hash": configuration_hash(row_copy(row)),
                    }
                    for row in rows
                ],
            }
        )
    )
