"""Read-only original goal-cash sources for a later dedicated release protocol."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.db.models import ActionPlan, ActionReceipt, BankOperation, Goal, SimulatedBankPosting, User
from app.domain.execution_types import BankCommand
from app.domain.goal_release_provenance import (
    AllocationOriginal,
    GoalCashPostingOriginal,
    GoalCashProvenanceInput,
    GoalCashSourceProof,
    OriginalRowReference,
    prove_goal_cash_sources,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.full_reconciliation import full_reconciliation
from app.services.historical_read import historical_ledger_scope
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def read_goal_cash_source_proof(
    session: Session,
    user_id: UUID,
    goal_id: UUID,
    expected_epoch_id: UUID,
    expected_goal_policy_version_id: UUID,
    now: datetime,
) -> GoalCashSourceProof:
    """Actual RRRO service facts only; no amount, income, authority, or write input."""
    _read_snapshot(session)
    now = _now(now)
    user = session.scalar(select(User).where(User.id == user_id))
    epoch = current_audit_epoch(session, user_id)
    goal = session.scalar(select(Goal).where(Goal.user_id == user_id, Goal.id == goal_id))
    if user is None or not user.is_simulated or goal is None:
        raise PolicyLifecycleError("NOT_FOUND", "当前模拟用户目标不存在", 404)
    if epoch is None or epoch.status != "OPEN" or epoch.id != expected_epoch_id:
        raise PolicyLifecycleError("STALE_AUDIT_EPOCH", "来源证明需要当前开放epoch", 409)
    if goal.policy_version_id != expected_goal_policy_version_id or goal.account_id is None:
        raise PolicyLifecycleError("STALE_GOAL_SOURCE", "原目标版本或账户已变化", 409)
    with session.no_autoflush, historical_ledger_scope(session):
        return _read(session, user_id, goal, expected_epoch_id, now)


def _read(
    session: Session, user_id: UUID, goal: Goal, epoch_id: UUID, now: datetime
) -> GoalCashSourceProof:
    report = full_reconciliation(session, user_id, now)
    issues: list[str] = []
    income = None
    evidence_id = None
    evidence_hash = None
    try:
        state = read_income_state(session, user_id, now)
        income, evidence_id, evidence_hash = state.ledger, state.evidence_id, state.evidence_hash
    except PolicyLifecycleError as error:
        issues.append(error.code)
    captured: dict[str, list[Any]] = {}
    inventory: dict[str, dict[str, Any]] = {}
    complete = True
    models: tuple[Any, ...] = (ActionPlan, BankOperation, ActionReceipt, SimulatedBankPosting)
    for model in models:
        count = session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )
        limit = 100000 if model is SimulatedBankPosting else 10000
        rows: list[Any] = list(
            session.scalars(
                select(model).where(model.user_id == user_id).order_by(model.id).limit(limit)
            )
        )
        captured[model.__tablename__] = rows
        original = next((row for row in report.inventory if row.table == model.__tablename__), None)
        valid = (
            type(count) is int
            and count == len(rows)
            and original is not None
            and original.complete
            and original.actual_count == count
            and all(row.user_id == user_id for row in rows)
        )
        inventory[model.__tablename__] = {
            "actual_count": count,
            "captured_count": len(rows),
            "complete": valid,
        }
        complete = complete and valid
        if not valid:
            issues.append("ORIGINAL_" + model.__tablename__.upper() + "_INVENTORY_INCOMPLETE")
    actions = {row.id: row for row in captured[ActionPlan.__tablename__]}
    receipts = {row.id: row for row in captured[ActionReceipt.__tablename__]}
    reconciled = {row.action_id: row for row in report.actions}
    originals = []
    for bank in captured[BankOperation.__tablename__]:
        action = actions.get(bank.action_plan_id)
        if action is None:
            complete = False
            issues.append("ORIGINAL_BANK_ACTION_MISSING")
            continue
        command = None
        if bank.legacy_redemption_id is None:
            try:
                command = BankCommand.model_validate_json(json.dumps(bank.request))
            except (ValueError, TypeError):
                issues.append("ORIGINAL_BANK_COMMAND_NOT_SUPPORTED_OR_INVALID")
        refs = [_ref(action), _ref(bank)]
        actual = reconciled.get(action.id)
        if actual:
            for identity in actual.receipt_ids:
                receipt = receipts.get(identity)
                if receipt is None:
                    complete = False
                    issues.append("ORIGINAL_RECEIPT_INVENTORY_MISMATCH")
                else:
                    refs.append(_ref(receipt))
        originals.append(
            AllocationOriginal(
                command=command,
                action_id=action.id,
                action_goal_id=action.goal_id,
                action_type=action.action_type,
                action_request=action.request,
                action_request_hash=action.request_hash,
                bank_id=bank.id,
                bank_request=bank.request,
                bank_request_hash=bank.request_hash,
                bank_idempotency_key=bank.idempotency_key,
                legacy_redemption_id=bank.legacy_redemption_id,
                original_refs=refs,
            )
        )
    history = [
        GoalCashPostingOriginal(
            posting_id=row.id,
            operation_id=row.operation_id,
            account_id=row.account_id,
            ledger_metadata=row.ledger_metadata,
            entry_kind=row.entry_kind,
            sequence_number=row.sequence_number,
            balance_before_cents=row.balance_before_cents,
            delta_cents=row.delta_cents,
            balance_after_cents=row.balance_after_cents,
            source_ref=_ref(row),
        )
        for row in captured[SimulatedBankPosting.__tablename__]
        if row.ledger_key == f"GOAL_CASH:{goal.id}"
    ]
    if goal.account_id is None:
        raise PolicyLifecycleError("STALE_GOAL_SOURCE", "原目标账户缺失", 409)
    return prove_goal_cash_sources(
        GoalCashProvenanceInput(
            user_id=user_id,
            epoch_id=epoch_id,
            as_of=now,
            goal_id=goal.id,
            goal_account_id=goal.account_id,
            goal_policy_version_id=goal.policy_version_id,
            income=income,
            income_evidence_id=evidence_id,
            income_evidence_hash=evidence_hash,
            reconciliation=report,
            originals=originals,
            goal_cash_history=history,
            original_inventory_complete=complete,
            original_binding_hash=configuration_hash(
                {
                    "user_id": str(user_id),
                    "epoch_id": str(epoch_id),
                    "as_of": now.isoformat(),
                    "goal": row_copy(goal),
                    "income_evidence_id": str(evidence_id),
                    "income_evidence_hash": evidence_hash,
                    "inventory": inventory,
                    "original_rows": {
                        table: [row_copy(row) for row in rows] for table, rows in captured.items()
                    },
                    "reconciliation_input_hash": report.input_hash,
                }
            ),
            source_issues=issues,
        )
    )


def _ref(row: Any) -> OriginalRowReference:
    return OriginalRowReference(
        table=row.__tablename__, row_id=row.id, row_hash=configuration_hash(row_copy(row))
    )
