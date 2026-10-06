"""Durable application claims, separate from committed bank economic effects."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

from app.db.models import ActionPlan, ActionResourceReservation, BankOperation, User
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import ledger_heads
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select
from sqlalchemy.orm import Session

ResourceKind = Literal[
    "CASH", "INCOME", "GOAL_CASH", "MANAGED", "POSITION", "OBLIGATION", "BUSINESS"
]


class ResourceClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resource_kind: ResourceKind
    resource_key: str = Field(min_length=1, max_length=160)
    amount_cents: StrictInt = Field(gt=0)
    capacity_cents: StrictInt = Field(ge=0)


def reserve_resources(
    session: Session, user_id: UUID, action_id: UUID, claims: Sequence[ResourceClaim], now: datetime
) -> list[UUID]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("Claims require an aware server clock")
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    action = session.get(ActionPlan, action_id)
    if user is None or not user.is_simulated or action is None or action.user_id != user_id:
        raise _error("Unknown action claim owner")
    specs = {(item.resource_kind, item.resource_key): item for item in claims}
    if not claims or len(specs) != len(claims):
        raise _error("Claims must have unique nonempty resource identities")
    rows = list(
        session.scalars(
            select(ActionResourceReservation).where(ActionResourceReservation.user_id == user_id)
        )
    )
    existing = [item for item in rows if item.action_plan_id == action_id]
    if existing:
        if {(row.resource_kind, row.resource_key): row.amount_cents for row in existing} != {
            key: item.amount_cents for key, item in specs.items()
        }:
            raise _error("An action cannot change its reserved resources")
        return sorted(row.id for row in existing)
    if action.status not in {"PLANNED", "AUTHORIZED", "SUBMITTED"}:
        raise _error("An inactive action cannot reserve new resources")
    heads = ledger_heads(session, user_id)
    keys = {
        "CASH": "CASH:",
        "POSITION": "POSITION:",
        "GOAL_CASH": "GOAL_CASH:",
        "INCOME": "LOT_AVAILABLE:",
    }
    result: list[UUID] = []
    for key, item in sorted(specs.items()):
        cap = item.capacity_cents
        if item.resource_kind == "POSITION":
            closing = session.scalar(
                select(BankOperation.id).where(
                    BankOperation.user_id == user_id,
                    BankOperation.closing_position_id == UUID(item.resource_key),
                    BankOperation.action_plan_id != action_id,
                    BankOperation.status != "REJECTED",
                )
            )
            if closing is not None:
                raise _error("An accepted bank operation already occupies this principal")
        if item.resource_kind in keys:
            head = heads.get(keys[item.resource_kind] + item.resource_key)
            if head is None or head.occurred_at > now:
                raise _error("The independent bank lacks this resource anchor")
            cap = min(cap, head.balance_after_cents)
        if item.resource_kind == "BUSINESS":
            cap = min(cap, 1)
        occupied = sum(
            row.amount_cents
            for row in rows
            if (row.resource_kind, row.resource_key) == key
            and (
                row.status == "RESERVED"
                or (item.resource_kind == "BUSINESS" and row.status == "CONSUMED")
            )
        )
        if item.amount_cents > cap - occupied:
            raise _error("Another action or the bank balance exhausts this resource")
        identity = uuid5(action_id, f"resource:{item.resource_kind}:{item.resource_key}")
        session.add(
            ActionResourceReservation(
                id=identity,
                user_id=user_id,
                created_at=now,
                action_plan_id=action_id,
                resource_kind=item.resource_kind,
                resource_key=item.resource_key,
                amount_cents=item.amount_cents,
                status="RESERVED",
                resolved_at=None,
            )
        )
        result.append(identity)
    session.flush()
    return sorted(result)


def resolve_resources(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    outcome: Literal["CONSUMED", "RELEASED"],
    now: datetime,
) -> None:
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    action = session.get(ActionPlan, action_id)
    operation = session.scalar(
        select(BankOperation).where(
            BankOperation.action_plan_id == action_id, BankOperation.user_id == user_id
        )
    )
    if action is None or action.user_id != user_id:
        raise _error("Unknown action resource owner")
    if outcome == "CONSUMED":
        if operation is None or operation.status != "SETTLED":
            raise _error("Only independently settled economics consume a reservation")
    elif (operation is not None and operation.status != "REJECTED") or (
        operation is None and action.status not in {"CANCELLED", "INVALIDATED"}
    ):
        raise _error("Unresolved or accepted effects cannot release their resources")
    for row in session.scalars(
        select(ActionResourceReservation).where(
            ActionResourceReservation.action_plan_id == action_id,
            ActionResourceReservation.user_id == user_id,
        )
    ):
        if row.status == outcome:
            continue
        if row.status != "RESERVED":
            raise _error("A resolved reservation cannot change its outcome")
        row.status, row.resolved_at = outcome, now
    session.flush()


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("RESOURCE_CONFLICT", message, 409)
