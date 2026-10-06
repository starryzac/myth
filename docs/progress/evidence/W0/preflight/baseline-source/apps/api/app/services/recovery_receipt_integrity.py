"""Read-only verification of the independently settled legacy recovery protocol."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    BankOperation,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
)
from app.domain.policy_configuration import configuration_hash
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import BankRequest, ledger_heads
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("BANK_RECONCILIATION_REQUIRED", message, 409)


def verify_recovery_receipt(
    session: Session, request: SimulatedBankRedemption, receipt: ActionReceipt, now: datetime
) -> None:
    """Verify historical facts without current authorization, projection or settlement."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("Historical recovery verification requires an aware server clock")
    with session.no_autoflush:
        _verify(session, request, receipt, now.astimezone(UTC))


def _verify(
    session: Session, request: SimulatedBankRedemption, receipt: ActionReceipt, now: datetime
) -> None:
    try:
        command = BankRequest.model_validate(request.request)
        action = session.get(ActionPlan, request.action_plan_id)
        if action is None:
            raise ValueError("Missing original action")
        action_command = BankRequest.model_validate(action.request["bank_request"])
    except (KeyError, TypeError, ValueError) as error:
        raise _error("The original recovery bank payload is unavailable or malformed") from error
    position = session.get(AssetPosition, command.position_id)
    destination = session.get(Account, command.destination_account_id)
    unified = session.get(BankOperation, request.id)
    receipts = list(
        session.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
    )
    expected_type = "ASSET_MATURITY" if command.kind == "MATURE" else "ASSET_REDEEM"
    if (
        request.id != uuid5(action.id, "simulated-bank-redemption")
        or request.user_id != command.user_id
        or request.user_id != action.user_id
        or request.request_hash != configuration_hash(request.request)
        or command != action_command
        or action.request_hash != configuration_hash(action.request)
        or request.request_hash != configuration_hash(action.request["bank_request"])
        or request.idempotency_key != action.idempotency_key
        or request.position_id != command.position_id
        or request.product_id != command.product_id
        or request.goal_id != command.goal_id
        or request.destination_account_id != command.destination_account_id
        or request.principal_cents != command.principal_cents
        or request.requested_at != command.requested_at
        or request.available_at != command.available_at
        or not command.requested_at <= request.created_at < command.expires_at
        or request.available_at < request.requested_at
        or request.status != "SETTLED"
        or request.settled_at != request.available_at
        or request.settled_at > now
        or position is None
        or position.user_id != request.user_id
        or position.account_id != command.position_account_id
        or position.product_id != command.product_id
        or position.goal_id != command.goal_id
        or position.policy_version_id != command.original_policy_version_id
        or position.principal_cents != command.principal_cents
        or destination is None
        or destination.user_id != request.user_id
        or destination.account_type not in {"CASH", "GOAL"}
        or action.action_type != expected_type
        or action.status not in {"SUCCEEDED", "RECONCILED"}
        or action.position_id != command.position_id
        or action.source_account_id != command.position_account_id
        or action.destination_account_id
        != (
            command.destination_account_id
            if command.destination_account_id != command.position_account_id
            else None
        )
        or action.product_id != command.product_id
        or action.goal_id != command.goal_id
        or action.amount_cents != command.principal_cents
        or action.expires_at != command.expires_at
        or unified is None
        or unified.user_id != request.user_id
        or unified.action_plan_id != action.id
        or unified.legacy_redemption_id != request.id
        or unified.closing_position_id != command.position_id
        or unified.operation_type != "LEGACY_REDEMPTION"
        or unified.business_key != f"close:{command.position_id}"
        or unified.request != request.request
        or unified.request_hash != request.request_hash
        or unified.idempotency_key != request.idempotency_key
        or unified.created_at != request.created_at
        or unified.requested_at != request.requested_at
        or unified.available_at != request.available_at
        or unified.settled_at != request.settled_at
        or unified.status != "SETTLED"
        or len(receipts) != 1
        or receipts[0].id != receipt.id
    ):
        raise _error("Recovery action, original request and independent bank identities disagree")
    if command.goal_id is not None:
        goal = session.get(Goal, command.goal_id)
        if (
            goal is None
            or goal.user_id != request.user_id
            or goal.account_id != command.destination_account_id
        ):
            raise _error("Recovery principal lost its original goal return account")
    legs = list(
        session.scalars(
            select(SimulatedBankPosting).where(
                or_(
                    SimulatedBankPosting.redemption_id == request.id,
                    SimulatedBankPosting.operation_id == request.id,
                )
            )
        )
    )
    specs = {
        "CASH_CREDIT": (
            f"CASH:{command.destination_account_id}",
            command.destination_account_id,
            None,
            command.principal_cents,
        ),
        "PRINCIPAL_DEBIT": (
            f"POSITION:{command.position_id}",
            None,
            command.position_id,
            -command.principal_cents,
        ),
    }
    if len(legs) != 2 or {leg.entry_kind for leg in legs} != set(specs):
        raise _error("Recovery settlement must have its complete two conserved posting legs")
    # Verify the independent chain; current application cash may have legitimately changed.
    ledger_heads(session, request.user_id)
    for leg in legs:
        key, account_id, position_id, delta = specs[leg.entry_kind]
        if (
            leg.id != uuid5(request.id, leg.entry_kind)
            or leg.user_id != request.user_id
            or leg.redemption_id != request.id
            or leg.operation_id != request.id
            or leg.leg_ref != leg.entry_kind
            or leg.ledger_dimension != "ECONOMIC"
            or leg.ledger_key != key
            or leg.account_id != account_id
            or leg.position_id != position_id
            or leg.delta_cents != delta
            or leg.occurred_at != request.settled_at
            or not leg.occurred_at <= leg.created_at <= now
            or leg.balance_after_cents != leg.balance_before_cents + delta
            or (leg.entry_kind == "PRINCIPAL_DEBIT" and leg.balance_after_cents != 0)
        ):
            raise _error("A recovery posting leg differs from its frozen bank economic fact")
    posting_ids = receipt.response.get("posting_ids")
    transaction_id = uuid5(request.id, "transaction")
    if (
        receipt.id != uuid5(request.id, "receipt")
        or receipt.user_id != request.user_id
        or receipt.action_plan_id != action.id
        or receipt.status != "SUCCEEDED"
        or receipt.attempt_number != 1
        or receipt.receipt_ref != f"bank:{request.id}"
        or receipt.executed_cents != command.principal_cents
        or receipt.fee_cents != 0
        or receipt.loss_cents != 0
        or set(receipt.response) != {"bank_request_id", "posting_ids", "transaction_id"}
        or receipt.response.get("bank_request_id") != str(request.id)
        or receipt.response.get("transaction_id") != str(transaction_id)
        or not isinstance(posting_ids, list)
        or any(not isinstance(key, str) for key in posting_ids)
        or len(posting_ids) != len(legs)
        or set(posting_ids) != {str(leg.id) for leg in legs}
        or receipt.occurred_at != request.settled_at
        or receipt.reconciled_at is None
        or not receipt.occurred_at <= receipt.reconciled_at <= now
        or receipt.created_at != receipt.reconciled_at
        or any(leg.created_at > receipt.reconciled_at for leg in legs)
    ):
        raise _error("Recovery receipt amount, complete posting set or observation times disagree")
    cash = next(leg for leg in legs if leg.entry_kind == "CASH_CREDIT")
    _verify_transaction(session, request, receipt, cash, transaction_id)


def _verify_transaction(
    session: Session,
    request: SimulatedBankRedemption,
    receipt: ActionReceipt,
    cash: SimulatedBankPosting,
    transaction_id: UUID,
) -> None:
    transaction = session.get(Transaction, transaction_id)
    proof_id = uuid5(request.id, "transaction-evidence")
    proof = session.get(EvidenceItem, proof_id)
    source_ref = f"bank-redemption:{request.id}:principal"
    payload = {
        "simulation": True,
        "user_id": str(request.user_id),
        "transaction_id": str(transaction_id),
        "account_id": str(request.destination_account_id),
        "direction": "CREDIT",
        "amount_cents": request.principal_cents,
        "balance_after_cents": cash.balance_after_cents,
        "occurred_at": cash.occurred_at.isoformat(),
        "counterparty_ref": f"position:{request.position_id}",
        "economic_role": "PRINCIPAL_RETURN",
        "bank_request_id": str(request.id),
        "bank_posting_id": str(cash.id),
    }
    if (
        transaction is None
        or transaction.user_id != request.user_id
        or transaction.account_id != request.destination_account_id
        or transaction.evidence_id != proof_id
        or transaction.source_ref != source_ref
        or transaction.direction != "CREDIT"
        or transaction.amount_cents != request.principal_cents
        or transaction.balance_after_cents != cash.balance_after_cents
        or transaction.counterparty_ref != f"position:{request.position_id}"
        or transaction.occurred_at != cash.occurred_at
        or transaction.observed_at != receipt.reconciled_at
        or transaction.created_at != receipt.reconciled_at
        or proof is None
        or proof.user_id != request.user_id
        or proof.source_type != "SIMULATED_BANK_TRANSACTION"
        or proof.evidence_level != "BANK_CONFIRMED"
        or proof.source_ref != source_ref
        or proof.content != payload
        or proof.content_hash != configuration_hash(payload)
        or proof.valid_from != cash.occurred_at
        or proof.observed_at != receipt.reconciled_at
        or proof.created_at != receipt.reconciled_at
    ):
        raise _error("The original recovery cash transaction or principal-return evidence changed")
