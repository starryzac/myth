"""Read original paired goal-transfer commands, bank legs and receipts without writes."""

import json
from datetime import datetime
from uuid import UUID, uuid5

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.domain.audit_chain import (
    build_subject,
    verify_frozen_projection,
    verify_frozen_settlement,
)
from app.domain.audit_chain_types import AuditSubject
from app.domain.full_goal_release_execution import GoalReleasePostingOriginal
from app.domain.full_goal_transfer import GoalTransferBankCommand, GoalTransferSettlementOriginal
from app.domain.full_goal_transfer_audit import (
    frozen_goal_transfer_identity,
    read_frozen_goal_transfer_action,
    verify_frozen_goal_transfer_posting,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.historical_read import verify_historical_ledger
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select
from sqlalchemy.orm import Session


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("GOAL_TRANSFER_ORIGINAL_INTEGRITY_ERROR", message, 409)


def read_goal_transfer_command(action: ActionPlan) -> GoalTransferBankCommand:
    try:
        command, _ = read_frozen_goal_transfer_action(row_copy(action))
        return command
    except (KeyError, TypeError, ValueError) as error:
        raise _error("专用目标间转移原动作、资金来源或摘要不一致") from error


def read_goal_transfer_bank_command(
    session: Session, operation: BankOperation, now: datetime
) -> GoalTransferBankCommand:
    user = session.get(User, operation.user_id)
    action = session.get(ActionPlan, operation.action_plan_id)
    if user is None or not user.is_simulated or action is None or action.user_id != user.id:
        raise _error("专用目标间转移缺少原模拟动作所有者")
    try:
        frozen_goal_transfer_identity(row_copy(operation), row_copy(action), now)
    except (KeyError, TypeError, ValueError) as error:
        raise _error("独立银行目标间转移请求不能替换原动作") from error
    return read_goal_transfer_command(action)


def verify_goal_transfer_legs(
    session: Session, operation: BankOperation, now: datetime
) -> list[SimulatedBankPosting]:
    command = read_goal_transfer_bank_command(session, operation, now)
    verify_historical_ledger(session, operation.user_id)
    action = session.get(ActionPlan, operation.action_plan_id)
    assert action is not None
    rows = list(
        session.scalars(
            select(SimulatedBankPosting)
            .where(SimulatedBankPosting.operation_id == operation.id)
            .limit(5)
        )
    )
    try:
        originals = [row_copy(row) for row in rows]
        verify_frozen_settlement(row_copy(operation), row_copy(action), originals, observed_at=now)
        for original in originals:
            verify_frozen_goal_transfer_posting(command.effect, original)
    except (KeyError, TypeError, ValueError) as error:
        raise _error("专用目标间转移完整四条原腿、现金归属或守恒尚未验真") from error
    return rows


def read_goal_transfer_bank_original(
    session: Session, operation: BankOperation, now: datetime
) -> GoalTransferSettlementOriginal:
    """Capture an authenticated closed original; pending/UNKNOWN never imply no effect."""
    command = read_goal_transfer_bank_command(session, operation, now)
    if (
        operation.status != "SETTLED"
        and session.scalar(
            select(SimulatedBankPosting.id)
            .where(SimulatedBankPosting.operation_id == operation.id)
            .limit(1)
        )
        is not None
    ):
        raise _error("未结算目标间转移存在原银行腿，必须保留未知状态核对")
    rows = (
        verify_goal_transfer_legs(session, operation, now) if operation.status == "SETTLED" else []
    )
    postings = []
    for row in rows:
        original = row_copy(row)
        postings.append(
            {
                **{
                    key: value
                    for key, value in original.items()
                    if key in GoalReleasePostingOriginal.model_fields
                },
                "posting_id": str(row.id),
                "original_row_hash": configuration_hash(original),
            }
        )
    try:
        return GoalTransferSettlementOriginal.model_validate_json(
            json.dumps(
                {
                    "command": command.model_dump(mode="json"),
                    "bank_operation_id": str(operation.id),
                    "action_plan_id": str(operation.action_plan_id),
                    "user_id": str(operation.user_id),
                    "request_hash": operation.request_hash,
                    "business_key": operation.business_key,
                    "idempotency_key": operation.idempotency_key,
                    "status": operation.status,
                    "requested_at": operation.requested_at.isoformat(),
                    "available_at": operation.available_at.isoformat(),
                    "settled_at": operation.settled_at.isoformat()
                    if operation.settled_at
                    else None,
                    "postings": postings,
                    "original_bank_row_hash": configuration_hash(row_copy(operation)),
                }
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _error("目标间转移原银行行不能缩减或改变状态后重放") from error


def verify_goal_transfer_receipt(
    session: Session, operation: BankOperation, receipt: ActionReceipt, now: datetime
) -> None:
    """Revoked current permission does not alter an already settled historical fact."""
    command = read_goal_transfer_bank_command(session, operation, now)
    rows = verify_goal_transfer_legs(session, operation, now)
    action = session.get(ActionPlan, operation.action_plan_id)
    assert action is not None
    effect = command.effect
    cash = [row for row in rows if row.ledger_dimension == "ECONOMIC"]
    ids = {uuid5(operation.id, "transaction:" + str(row.leg_ref)) for row in cash}
    body = receipt.response
    if (
        set(body)
        != {"bank_operation_id", "posting_ids", "transaction_ids", "goal_ownership_evidence_ids"}
        or not isinstance(body.get("transaction_ids"), list)
        or len(body["transaction_ids"]) != 2
        or set(body["transaction_ids"]) != {str(identity) for identity in ids}
    ):
        raise _error("目标间转移回执需要原两条内部转账交易，不能重新标作收入")
    subjects: list[AuditSubject] = []
    for row in cash:
        transaction_id = uuid5(operation.id, "transaction:" + str(row.leg_ref))
        transaction = session.get(Transaction, transaction_id)
        proof = session.get(EvidenceItem, uuid5(transaction_id, "evidence"))
        expected_ref = f"bank-operation:{operation.id}:{row.leg_ref}"
        counterparty = f"goal-transfer:{effect.source_goal_id}:{effect.target_goal_id}"
        if transaction is None or proof is None or operation.settled_at is None:
            raise _error("目标间转移原交易或银行凭证缺失")
        expected_content = {
            "simulation": True,
            "user_id": str(operation.user_id),
            "transaction_id": str(transaction_id),
            "account_id": str(row.account_id),
            "direction": "DEBIT" if row.delta_cents < 0 else "CREDIT",
            "amount_cents": abs(row.delta_cents),
            "balance_after_cents": row.balance_after_cents,
            "occurred_at": row.occurred_at.isoformat(),
            "counterparty_ref": counterparty,
            "economic_role": "INTERNAL_TRANSFER",
            "bank_operation_id": str(operation.id),
            "bank_posting_id": str(row.id),
        }
        if (
            transaction.user_id != operation.user_id
            or proof.user_id != operation.user_id
            or transaction.evidence_id != proof.id
            or transaction.source_ref != expected_ref
            or proof.source_ref != expected_ref
            or transaction.counterparty_ref != counterparty
            or transaction.category != "internal_transfer"
            or transaction.created_at != receipt.reconciled_at
            or transaction.observed_at != receipt.reconciled_at
            or proof.created_at != receipt.reconciled_at
            or proof.observed_at != receipt.reconciled_at
            or proof.valid_from != operation.settled_at
            or proof.content != expected_content
        ):
            raise _error("目标间转移现金原交易被改为新收入或已改变原绑定")
        subjects.extend(
            build_subject(
                user_id=effect.user_id,
                epoch_id=effect.epoch_id,
                kind=kind,
                id=original.id,
                data=row_copy(original),
            )
            for kind, original in (("TRANSACTION", transaction), ("EVIDENCE", proof))
        )
    ownership_ids = body.get("goal_ownership_evidence_ids")
    if type(ownership_ids) is not list or len(ownership_ids) != 2 or len(set(ownership_ids)) != 2:
        raise _error("目标间转移需要两个原归属证据")
    for identity in ownership_ids:
        proof = session.get(EvidenceItem, UUID(identity))
        if proof is None or proof.user_id != operation.user_id:
            raise _error("目标间转移原归属证据缺失")
        subjects.append(
            build_subject(
                user_id=effect.user_id,
                epoch_id=effect.epoch_id,
                kind="EVIDENCE",
                id=proof.id,
                data=row_copy(proof),
            )
        )
    try:
        verify_frozen_projection(
            row_copy(operation),
            row_copy(action),
            row_copy(receipt),
            [row_copy(row) for row in rows],
            observed_at=now,
            subjects=subjects,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _error("目标间转移原回执和真实银行四条腿不能按新授权重新解释") from error
