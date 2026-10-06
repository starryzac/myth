"""Read the original dedicated release action, ledger legs and receipt without writes."""

from datetime import datetime
from uuid import uuid5

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
from app.domain.full_goal_release_audit import (
    frozen_goal_release_identity,
    read_frozen_goal_release_action,
    verify_frozen_goal_release_posting,
)
from app.domain.full_goal_release_execution import GoalReleaseBankCommand
from app.services.audit_chain import row_copy
from app.services.historical_read import verify_historical_ledger
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select
from sqlalchemy.orm import Session


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("GOAL_RELEASE_ORIGINAL_INTEGRITY_ERROR", message, 409)


def read_goal_release_command(action: ActionPlan) -> GoalReleaseBankCommand:
    try:
        command, _ = read_frozen_goal_release_action(row_copy(action))
        return command
    except (KeyError, TypeError, ValueError) as error:
        raise _error("专用回拨原动作、资金来源或摘要不一致") from error


def read_goal_release_bank_command(
    session: Session, operation: BankOperation, now: datetime
) -> GoalReleaseBankCommand:
    user = session.get(User, operation.user_id)
    action = session.get(ActionPlan, operation.action_plan_id)
    if user is None or not user.is_simulated or action is None or action.user_id != user.id:
        raise _error("专用回拨缺少原模拟动作所有者")
    try:
        frozen_goal_release_identity(row_copy(operation), row_copy(action), now)
    except (KeyError, TypeError, ValueError) as error:
        raise _error("独立银行回拨请求不能替换原动作") from error
    return read_goal_release_command(action)


def verify_goal_release_legs(
    session: Session, operation: BankOperation, now: datetime
) -> list[SimulatedBankPosting]:
    command = read_goal_release_bank_command(session, operation, now)
    verify_historical_ledger(session, operation.user_id)
    action = session.get(ActionPlan, operation.action_plan_id)
    assert action is not None
    rows = list(
        session.scalars(
            select(SimulatedBankPosting)
            .where(SimulatedBankPosting.operation_id == operation.id)
            .limit(4)
        )
    )
    try:
        originals = [row_copy(row) for row in rows]
        verify_frozen_settlement(row_copy(operation), row_copy(action), originals, observed_at=now)
        for original in originals:
            verify_frozen_goal_release_posting(command.effect, original)
    except (KeyError, TypeError, ValueError) as error:
        raise _error("专用回拨完整三条原腿、现金归属或守恒尚未验真") from error
    return rows


def verify_goal_release_receipt(
    session: Session, operation: BankOperation, receipt: ActionReceipt, now: datetime
) -> None:
    """Revoked current permission does not alter an already settled historical fact."""
    command = read_goal_release_bank_command(session, operation, now)
    rows = verify_goal_release_legs(session, operation, now)
    action = session.get(ActionPlan, operation.action_plan_id)
    assert action is not None
    effect = command.effect
    cash = [row for row in rows if row.ledger_dimension == "ECONOMIC"]
    ids = {uuid5(operation.id, "transaction:" + str(row.leg_ref)) for row in cash}
    body = receipt.response
    if (
        set(body) != {"bank_operation_id", "posting_ids", "transaction_ids"}
        or not isinstance(body.get("transaction_ids"), list)
        or len(body["transaction_ids"]) != 2
        or set(body["transaction_ids"]) != {str(identity) for identity in ids}
    ):
        raise _error("回拨回执需要原两条内部转账交易，不能重新标作收入")
    subjects: list[AuditSubject] = []
    for row in cash:
        transaction_id = uuid5(operation.id, "transaction:" + str(row.leg_ref))
        transaction = session.get(Transaction, transaction_id)
        proof = session.get(EvidenceItem, uuid5(transaction_id, "evidence"))
        expected_ref = f"bank-operation:{operation.id}:{row.leg_ref}"
        counterparty = f"goal-release:{effect.source_goal_id}"
        if transaction is None or proof is None or operation.settled_at is None:
            raise _error("回拨原交易或银行凭证缺失")
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
            raise _error("回拨现金原交易被改为新收入或已改变原绑定")
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
        raise _error("回拨原回执和真实银行三条腿不能按新授权重新解释") from error
