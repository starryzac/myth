"""Exact additional payment scope; a FULL planning confirmation is never a bank grant."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.execution_types import ExecutionEffect, OccurrenceReference
from app.domain.full_policy_configuration import PeriodicTransferPolicy
from app.domain.policy_configuration import (
    ExactAmount,
    RangeAmount,
    RecurringObligation,
    UUIDReference,
    configuration_hash,
)
from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

if TYPE_CHECKING:
    from app.domain.local_actor_session_types import LocalActorPrincipal

PROTOCOL = "full-payment-relation-v1"
SOURCE = "FULL_PAYMENT_RELATION"
NAMESPACE = UUID("d4d1c4a1-bc4b-5a12-b073-aafdfc133e8f")
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(min_length=1, max_length=140)]
Reason = Annotated[StrictStr, Field(min_length=1, max_length=1000)]
Period = Annotated[StrictStr, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


class PaymentScopeRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    full_policy_id: UUIDReference
    expected_full_version_id: UUIDReference
    original_policy_id: UUIDReference
    expected_original_version_id: UUIDReference


class PaymentStartRequest(PaymentScopeRequest):
    idempotency_key: Key

    @field_validator("idempotency_key")
    @classmethod
    def nonblank_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An original key must not be blank")
        return value


class PaymentConfirmRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_scope_hash: Hash
    accepted: StrictBool
    reason: Reason
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit_confirmation(self) -> Self:
        if self.accepted is not True or not self.reason.strip() or not self.idempotency_key.strip():
            raise ValueError("A payment relation requires explicit acceptance, reason and key")
        return self


class PaymentPrepareRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    period: Period
    idempotency_key: Key

    @field_validator("idempotency_key")
    @classmethod
    def nonblank_key(cls, value: str) -> str:
        return PaymentStartRequest.nonblank_key(value)

    @field_validator("period")
    @classmethod
    def real_calendar_month(cls, value: str) -> str:
        date.fromisoformat(value + "-01")
        return value


class PaymentActionConfirmation(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_effect_hash: Hash
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Only explicit original-effect consent confirms a payment")
        return value


class PaymentExecuteRequest(BoundaryModel):
    expected_epoch_id: UUIDReference


class PaymentRelationScope(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    full_policy_id: UUID
    full_version_id: UUID
    full_configuration_hash: Hash
    original_policy_id: UUID
    original_version_id: UUID
    original_configuration_hash: Hash
    payee_id: str
    payee_evidence_id: UUID
    payee_evidence_hash: Hash
    source_account_id: UUID
    source_account_identity_hash: Hash
    amount_rule: Annotated[ExactAmount | RangeAmount, Field(discriminator="kind")]
    due_day: Annotated[int, Field(strict=True, ge=1, le=31)]
    single_action_cap_cents: Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
    auto_execute: StrictBool
    timezone: Literal["UTC", "Asia/Shanghai"]
    valid_from: datetime
    valid_until: datetime
    full_planning_bank_authority: Literal[False] = False
    creates_original_mvp_permission: Literal[False] = False

    @model_validator(mode="after")
    def bounded_scope(self) -> Self:
        upper = (
            self.amount_rule.amount_cents
            if isinstance(self.amount_rule, ExactAmount)
            else self.amount_rule.max_cents
        )
        if (
            not self.payee_id.strip()
            or len(self.payee_id) > 160
            or self.valid_from.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.valid_from.utcoffset() is None
            or self.valid_until.utcoffset() is None
            or self.valid_from >= self.valid_until
            or upper > self.single_action_cap_cents
        ):
            raise ValueError("The exact payee, amount and finite current scope must be bounded")
        return self


def payment_command_identity(user_id: UUID, epoch_id: UUID, key: str) -> UUID:
    """START/CONFIRM keys share one epoch namespace; another kind cannot replace a key."""
    return uuid5(NAMESPACE, f"{user_id}:{epoch_id}:{key}")


def validate_payment_bridge(full: PeriodicTransferPolicy, original: RecurringObligation) -> None:
    """No invented original fields: source account and cap remain dedicated constraints."""
    if (
        full.payee_id != original.payee_id
        or full.amount_rule.model_dump(mode="json") != original.amount_rule.model_dump(mode="json")
        or full.due_day != original.due_day
        or full.prepare_days_before != original.prepare_days_before
        or full.auto_execute != original.auto_execute
    ):
        raise ValueError(
            "FULL and original recurring amount, payee, cadence or auto consent differ"
        )


def require_payment_principal(
    principal: LocalActorPrincipal | None,
    user_id: UUID,
    now: datetime,
    allowed_roles: set[str],
) -> None:
    """This object must come from the trusted signed-session dependency, never request JSON."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The actual server clock must be aware")
    if principal is None or (
        principal.user_id != user_id
        or principal.role not in allowed_roles
        or principal.authenticated is not True
        or principal.authentication_source != "LOCAL_SIGNED_SESSION"
        or principal.issued_at.tzinfo is None
        or principal.expires_at.tzinfo is None
        or not principal.issued_at <= now < principal.expires_at
    ):
        raise ValueError("A current matching server-authenticated payment principal is required")


def verify_payment_effect(
    scope: PaymentRelationScope,
    effect: ExecutionEffect,
    now: datetime,
    *,
    require_current: bool = True,
    verified_payee_evidence_ids: set[UUID] | None = None,
) -> None:
    """Keep original PAY_RECURRING identity, real monthly liability and exact cash scope."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The actual payment clock must be aware")
    ref = effect.liability
    if (
        effect.action_type != "PAY_RECURRING"
        or effect.user_id != scope.user_id
        or effect.policy_id != scope.original_policy_id
        or effect.policy_version_id != scope.original_version_id
        or effect.policy_version_ids != [scope.original_version_id]
        or effect.payee_id != scope.payee_id
        or effect.payee_evidence_id
        not in (verified_payee_evidence_ids or {scope.payee_evidence_id})
        or not isinstance(ref, OccurrenceReference)
        or ref.policy_id != scope.original_policy_id
        or effect.amount_cents > scope.single_action_cap_cents
        or effect.amount_cents <= 0
        or effect.fee_cents != 0
        or effect.loss_cents != 0
        or len(effect.cash_uses) != 1
        or effect.cash_uses[0].account_id != scope.source_account_id
        or effect.cash_uses[0].amount_cents != effect.amount_cents
        or not scope.valid_from <= effect.valid_from < scope.valid_until
        or require_current
        and not scope.valid_from <= now < scope.valid_until
    ):
        raise ValueError("The real original payment does not fit the separately confirmed scope")
    first = date.fromisoformat(ref.period + "-01")
    due = first.replace(day=min(scope.due_day, monthrange(first.year, first.month)[1]))
    local = now.astimezone(ZoneInfo(scope.timezone)).date()
    start = scope.valid_from.astimezone(ZoneInfo(scope.timezone)).date()
    due_at = datetime.combine(due, time.min, ZoneInfo(scope.timezone))
    upper = (
        scope.amount_rule.amount_cents
        if isinstance(scope.amount_rule, ExactAmount)
        else scope.amount_rule.max_cents
    )
    # MVP checks actual unpaid/final total and duplicate monthly business key again.
    if due < start or due_at >= scope.valid_until or due > local or effect.amount_cents > upper:
        raise ValueError("This natural-month occurrence is not due inside the agreed window")
    if effect.business_key != f"recurring:{scope.original_policy_id}:{ref.period}":
        raise ValueError("A payment cannot replace its original natural-month business identity")


def payment_scope_hash(scope: PaymentRelationScope) -> str:
    return configuration_hash(scope.model_dump(mode="json"))


def account_payment_identity(account: dict[str, Any]) -> dict[str, Any]:
    return {
        key: account[key]
        for key in ("id", "user_id", "account_type", "currency", "external_ref", "bank_code")
    }


def verify_original_payment_references(
    references: list[dict[str, Any]],
    account: dict[str, Any],
    payee: dict[str, Any],
    payee_id: str,
) -> None:
    """Narrow immutable references only; newer same-payee transactions are not new grants."""
    if len(references) != 2 or {row.get("role") for row in references} != {
        "source_account",
        "payee_source",
    }:
        raise ValueError("Periodic payment original reference inventory must be exactly complete")
    source = next(row for row in references if row["role"] == "source_account")
    recipient = next(row for row in references if row["role"] == "payee_source")
    source_binding = {
        "id": account["id"],
        "owner": account["user_id"],
        "type": account["account_type"],
    }
    payee_binding = {"id": payee["id"], "hash": payee["content_hash"], "payee_id": payee_id}
    if (
        source.get("kind") != "ACCOUNT"
        or source.get("id") != account["id"]
        or source.get("binding_hash") != configuration_hash(source_binding)
        or account_payment_identity(source["snapshot"]) != account_payment_identity(account)
        or recipient.get("kind") != "EVIDENCE"
        or recipient.get("id") != payee["id"]
        or recipient.get("binding_hash") != configuration_hash(payee_binding)
        or recipient.get("snapshot") != payee
        or payee.get("user_id") != account["user_id"]
        or payee.get("status") != "VALID"
        or payee.get("evidence_level") != "BANK_CONFIRMED"
        or configuration_hash(payee["content"]) != payee["content_hash"]
    ):
        raise ValueError(
            "Original account/payee reference identity, complete snapshot or hash changed"
        )


def payment_prepare_key(authorization_id: UUID, key: str) -> str:
    return "full-payment:" + configuration_hash(
        {"authorization_id": str(authorization_id), "key": key}
    )


def original_payment_action_key(authorization_id: UUID, key: str) -> str:
    return "action:" + configuration_hash({"key": payment_prepare_key(authorization_id, key)})


def clock_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A payment clock must be aware")
    return value.astimezone(UTC)
