"""Publish and verify complete execution exposure; declarations alone grant no authority."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.execution import ACTION_PLAN_TYPES
from app.domain.execution_types import BankCommand
from app.domain.policy_configuration import configuration_hash
from app.services.recovery_projection import current_proof, replace_proof
from sqlalchemy import select
from sqlalchemy.orm import Session


def refresh_execution_exposure(
    session: Session,
    user_id: UUID,
    now: datetime,
    operation_id: UUID,
    *,
    declarations: dict[UUID, dict[str, Any]] | None = None,
) -> None:
    """Caller must hold the user lock and have verified facts before changing projections."""
    previous = current_proof(session, user_id, EXPOSURE_SOURCE)
    declared = {UUID(item["action_id"]): item for item in previous.content["settlements"]}
    session.flush()
    actions = list(session.scalars(select(ActionPlan).where(ActionPlan.user_id == user_id)))
    receipts = list(session.scalars(select(ActionReceipt).where(ActionReceipt.user_id == user_id)))
    for action in actions:
        if "execution" not in action.request:
            continue
        command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
        state = "PLANNED_UNRESERVED"
        record: dict[str, Any] = {"action_id": str(action.id)}
        if action.status in {"SUBMITTED", "UNKNOWN"}:
            state = "EXECUTION_RESERVED"
        elif action.status in {"SUCCEEDED", "RECONCILED"}:
            matches = [r for r in receipts if r.action_plan_id == action.id]
            if len(matches) != 1:
                raise ValueError("A settled action needs exactly one final receipt")
            state = "EXECUTION_SETTLED"
            record["receipt_id"] = str(matches[0].id)
            if command.effect.action_type == "PURCHASE_ASSET":
                state = "MATERIALIZED"
                record.update(
                    position_id=str(command.effect.position_id),
                    transaction_id=str(uuid5(action.id, "transaction:purchase")),
                )
        elif action.status in {"CANCELLED", "INVALIDATED"}:
            state = "NO_EFFECT"
        elif action.status not in {"PLANNED", "AUTHORIZED"}:
            raise ValueError("Uncertain failed actions cannot be asserted as no effect")
        record["state"] = state
        declared[action.id] = record
    declared.update(declarations or {})
    content = asset_exposure_snapshot(
        user_id,
        now,
        accounts=session.scalars(select(Account).where(Account.user_id == user_id)),
        positions=session.scalars(select(AssetPosition).where(AssetPosition.user_id == user_id)),
        actions=actions,
        receipts=receipts,
        evidence=session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == user_id)),
        settlements=list(declared.values()),
        bank_requests=session.scalars(
            select(SimulatedBankRedemption).where(SimulatedBankRedemption.user_id == user_id)
        ),
        bank_postings=session.scalars(
            select(SimulatedBankPosting).where(SimulatedBankPosting.user_id == user_id)
        ),
        bank_operations=session.scalars(
            select(BankOperation).where(BankOperation.user_id == user_id)
        ),
        resource_reservations=session.scalars(
            select(ActionResourceReservation).where(ActionResourceReservation.user_id == user_id)
        ),
    )
    replace_proof(session, previous, content, now, operation_id)


def validate_execution_declaration(
    action: ActionPlan,
    declaration: dict[str, Any],
    receipts: list[ActionReceipt],
    operations: list[BankOperation],
    reservations: list[ActionResourceReservation],
    postings: list[SimulatedBankPosting],
    epoch: datetime,
) -> BankCommand:
    command = BankCommand.model_validate_json(json.dumps(action.request["execution"]))
    effect = command.effect
    if (
        action.request_hash != configuration_hash(action.request)
        or effect.operation_id != action.id
        or effect.user_id != action.user_id
        or action.action_type != ACTION_PLAN_TYPES[effect.action_type]
        or action.amount_cents != effect.amount_cents
        or action.source_account_id
        != (effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id)
        or action.destination_account_id
        != (
            effect.destination_account_id
            if effect.destination_account_id != action.source_account_id
            else None
        )
        or action.goal_id != effect.goal_id
        or action.product_id != effect.product_id
    ):
        raise ValueError("Action projection differs from its immutable effect")
    own_ops = [r for r in operations if r.action_plan_id == action.id]
    own_claims = [r for r in reservations if r.action_plan_id == action.id]
    own_receipts = [r for r in receipts if r.action_plan_id == action.id]
    state = declaration["state"]
    if len(own_ops) > 1:
        raise ValueError("Duplicate independent operations")
    operation = own_ops[0] if own_ops else None
    if operation is not None and (
        operation.request != command.model_dump(mode="json")
        or operation.request_hash != configuration_hash(operation.request)
        or operation.user_id != action.user_id
        or operation.id != action.id
        or operation.idempotency_key != action.idempotency_key
        or operation.business_key != effect.business_key
        or operation.requested_at > epoch
    ):
        raise ValueError("Independent operation does not match the prepared effect")
    if state == "PLANNED_UNRESERVED":
        if action.status not in {"PLANNED", "AUTHORIZED"} or own_ops or own_claims or own_receipts:
            raise ValueError("A preview cannot hide a bank operation or resource claim")
        return command
    if state == "NO_EFFECT":
        if (
            action.status not in {"CANCELLED", "INVALIDATED"}
            or own_receipts
            or (operation is not None and operation.status != "REJECTED")
            or any(r.status != "RELEASED" for r in own_claims)
        ):
            raise ValueError("Only a confirmed absent economic effect releases resources")
        return command
    if state == "EXECUTION_RESERVED":
        required = {("CASH", str(u.account_id)): u.amount_cents for u in effect.cash_uses}
        required.update(
            {("INCOME", str(u.fragment_id)): u.amount_cents for u in effect.income_uses}
        )
        required[("BUSINESS", effect.business_key)] = 1
        if effect.action_type == "REDEEM_ASSET":
            required[("POSITION", str(effect.position_id))] = effect.amount_cents
        actual = {
            (r.resource_kind, r.resource_key): r.amount_cents
            for r in own_claims
            if r.status == "RESERVED"
        }
        if (
            action.status not in {"SUBMITTED", "UNKNOWN"}
            or own_receipts
            or any(actual.get(key) != value for key, value in required.items())
            or any(r.status != "RESERVED" for r in own_claims)
            or (operation is not None and operation.status != "ACCEPTED")
        ):
            raise ValueError("Execution has uncertain or already settled economics to reconcile")
        return command
    if state not in {"EXECUTION_SETTLED", "MATERIALIZED"}:
        raise ValueError("Unknown execution settlement")
    if (
        action.status not in {"SUCCEEDED", "RECONCILED"}
        or operation is None
        or operation.status != "SETTLED"
        or operation.settled_at is None
        or operation.settled_at > epoch
        or len(own_receipts) != 1
        or any(r.status != "CONSUMED" for r in own_claims)
    ):
        raise ValueError("Execution is not fully reconciled")
    receipt = own_receipts[0]
    legs = [p for p in postings if p.operation_id == operation.id]
    if (
        receipt.status != "SUCCEEDED"
        or receipt.executed_cents != effect.amount_cents
        or receipt.fee_cents != effect.fee_cents
        or receipt.loss_cents != effect.loss_cents
        or receipt.response.get("bank_operation_id") != str(operation.id)
        or set(receipt.response.get("posting_ids", [])) != {str(p.id) for p in legs}
        or declaration.get("receipt_id") != str(receipt.id)
        or not legs
    ):
        raise ValueError("Receipt does not bind the independently settled economic legs")
    return command
