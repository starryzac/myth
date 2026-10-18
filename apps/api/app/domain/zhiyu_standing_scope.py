"""Closed extension delegation scope; no native bank permission is created here."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary_types import BoundaryModel
from app.domain.execution_types import ExecutionEffect
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt, model_validator

Family = Literal[
    "SINGLE_GOAL", "JOINT_GOALS", "ASSET_PORTFOLIO", "MATURITY_REINVESTMENT", "PERIODIC_PAYMENT"
]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Cents = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]


class StandingScope(BoundaryModel):
    protocol: Literal["zhiyu-next-standing-scope-v1"] = "zhiyu-next-standing-scope-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    family: Family
    policy_version_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    source_account_ids: Annotated[list[UUID], Field(min_length=1, max_length=64)]
    goal_ids: Annotated[list[UUID], Field(max_length=8)] = Field(default_factory=list)
    product_ids: Annotated[list[UUID], Field(max_length=64)] = Field(default_factory=list)
    payee_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    per_action_cap_cents: Cents
    per_event_cap_cents: Cents
    daily_cap_cents: Cents
    maximum_children: Annotated[StrictInt, Field(ge=1, le=8)]
    maximum_principal_unavailable_days: Annotated[StrictInt, Field(ge=0, le=3660)] = 0
    maximum_risk_level: Annotated[StrictInt, Field(ge=0, le=5)] = 0
    asset_classes: Annotated[list[str], Field(max_length=16)] = Field(default_factory=list)
    max_fee_cents: Literal[0] = 0
    max_loss_cents: Literal[0] = 0
    cross_goal_release: Literal[False] = False
    requires_verified_complete_worlds: Literal[True] = True
    requires_current_native_guard: Literal[True] = True
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def finite_and_specific(self) -> Self:
        for values in (
            self.policy_version_ids,
            self.source_account_ids,
            self.goal_ids,
            self.product_ids,
            self.asset_classes,
        ):
            if len(values) != len(set(values)):
                raise ValueError("Delegated identities must be unique")
        if (
            any(
                clock.tzinfo is None or clock.utcoffset() is None
                for clock in (self.issued_at, self.expires_at)
            )
            or not self.issued_at < self.expires_at
        ):
            raise ValueError("An explicit finite aware delegation window is required")
        if not self.per_action_cap_cents <= self.per_event_cap_cents <= self.daily_cap_cents:
            raise ValueError("Action, event and day limits must strengthen each other")
        if self.family in {"SINGLE_GOAL", "JOINT_GOALS"}:
            if not self.goal_ids or self.product_ids or self.payee_id is not None:
                raise ValueError("Goal scope may cover only explicit owned goal destinations")
            if self.family == "SINGLE_GOAL" and (
                len(self.goal_ids) != 1 or self.maximum_children != 1
            ):
                raise ValueError("Single-goal delegation cannot cover a joint plan")
        elif self.family in {"ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"}:
            if (
                not self.product_ids
                or not self.asset_classes
                or any(not value.strip() for value in self.asset_classes)
                or self.maximum_principal_unavailable_days < 1
                or self.payee_id is not None
                or self.maximum_children > 4
            ):
                raise ValueError(
                    "Asset delegation needs a finite product set and at most four batches"
                )
        elif (
            self.payee_id is None or self.goal_ids or self.product_ids or self.maximum_children != 1
        ):
            raise ValueError("Periodic payment is one fixed payee, never a new-payee permission")
        if self.family not in {"ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"} and (
            self.asset_classes or self.maximum_principal_unavailable_days or self.maximum_risk_level
        ):
            raise ValueError("Non-asset delegation cannot carry product permissions")
        return self


class SignedStandingGrant(BoundaryModel):
    protocol: Literal["zhiyu-next-signed-standing-grant-v1"] = "zhiyu-next-signed-standing-grant-v1"
    grant_id: UUID
    scope: StandingScope
    reviewed_scope_hash: Hash
    actor_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime
    accepted: Literal[True]
    native_bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_user_origin(self) -> Self:
        require_local_user(self.actor_at_confirmation, self.scope.user_id, self.confirmed_at)
        if (
            not self.scope.issued_at <= self.confirmed_at < self.scope.expires_at
            or self.reviewed_scope_hash != configuration_hash(self.scope.model_dump(mode="json"))
        ):
            raise ValueError("USER must review this exact finite scope")
        return self


def covered_effects(
    scope: StandingScope,
    effects: list[ExecutionEffect],
    *,
    user_id: UUID,
    epoch_id: UUID,
    current_version_ids: set[UUID],
    now: datetime,
    spent_today_cents: int,
    control_state: Literal["ACTIVE", "PAUSED", "REVOKED"],
    complete_worlds_verified: bool,
    native_current_scope_verified: bool,
    settled_maturity_original_verified: bool = False,
    current_product_terms: dict[UUID, AssetProductTerms] | None = None,
) -> list[str]:
    """Internal typed gate only; callers must reread original records under a user lock."""
    reasons: set[str] = set()
    if now.tzinfo is None or now.utcoffset() is None:
        return ["CURRENT_SERVER_CLOCK_REQUIRED"]
    if (scope.user_id, scope.epoch_id) != (user_id, epoch_id):
        reasons.add("DELEGATION_OWNER_OR_EPOCH_CHANGED")
    if not scope.issued_at <= now.astimezone(UTC) < scope.expires_at or control_state != "ACTIVE":
        reasons.add("DELEGATION_INACTIVE")
    if set(scope.policy_version_ids) != current_version_ids:
        reasons.add("DELEGATION_POLICY_VERSION_CHANGED")
    if complete_worlds_verified is not True or native_current_scope_verified is not True:
        reasons.add("CURRENT_COMPLETE_CONTEXT_NOT_VERIFIED")
    if scope.family == "MATURITY_REINVESTMENT" and settled_maturity_original_verified is not True:
        reasons.add("ACTUAL_MATURITY_RECEIPT_REQUIRED")
    if (
        not effects
        or len(effects) > scope.maximum_children
        or len({row.operation_id for row in effects}) != len(effects)
    ):
        reasons.add("WHOLE_EVENT_INVENTORY_INVALID")
    action_type = (
        "ALLOCATE_GOAL"
        if scope.family in {"SINGLE_GOAL", "JOINT_GOALS"}
        else "PAY_RECURRING"
        if scope.family == "PERIODIC_PAYMENT"
        else "PURCHASE_ASSET"
    )
    total = sum(row.amount_cents for row in effects)
    if (
        type(spent_today_cents) is not int
        or spent_today_cents < 0
        or total > scope.per_event_cap_cents
        or total + spent_today_cents > scope.daily_cap_cents
    ):
        reasons.add("EVENT_OR_DAILY_CAP_EXCEEDED")
    for effect in effects:
        if (
            not effect.cash_uses
            or any(use.account_id not in scope.source_account_ids for use in effect.cash_uses)
            or any(use.account_id not in scope.source_account_ids for use in effect.income_uses)
        ):
            reasons.add("FUNDING_ACCOUNT_NOT_COVERED")
        if (
            effect.user_id != scope.user_id
            or effect.action_type != action_type
            or not effect.valid_from <= now < effect.expires_at
        ):
            reasons.add("EFFECT_OWNER_TYPE_OR_WINDOW_CHANGED")
        versions = set(effect.policy_version_ids)
        if effect.policy_version_id is not None:
            versions.add(effect.policy_version_id)
        if not versions or not versions.issubset(current_version_ids):
            reasons.add("EFFECT_CURRENT_POLICY_NOT_COVERED")
        if (
            effect.amount_cents > scope.per_action_cap_cents
            or effect.fee_cents
            or effect.loss_cents
        ):
            reasons.add("AMOUNT_FEE_OR_LOSS_OUTSIDE_DELEGATION")
        if action_type == "ALLOCATE_GOAL" and effect.goal_id not in scope.goal_ids:
            reasons.add("GOAL_DESTINATION_NOT_COVERED")
        if action_type == "PURCHASE_ASSET" and (
            effect.product_id not in scope.product_ids
            or effect.goal_id is not None
            and effect.goal_id not in scope.goal_ids
        ):
            reasons.add("PRODUCT_OR_OWNERSHIP_NOT_COVERED")
        if action_type == "PURCHASE_ASSET":
            terms = (
                (current_product_terms or {}).get(effect.product_id) if effect.product_id else None
            )
            if (
                terms is None
                or terms.product_id != effect.product_id
                or terms.version_number != effect.product_version_number
                or terms.terms_digest != effect.terms_digest
                or terms.asset_class not in scope.asset_classes
                or terms.risk_level > scope.maximum_risk_level
                or terms.principal_fluctuation
                or not terms.auto_purchase_allowed
                or not terms.effective_from <= now
                or terms.effective_until is not None
                and now >= terms.effective_until
                or effect.latest_arrival_at is None
                or (effect.latest_arrival_at - effect.valid_from).total_seconds()
                > scope.maximum_principal_unavailable_days * 86400
            ):
                reasons.add("CURRENT_PRODUCT_OR_PRINCIPAL_WINDOW_NOT_COVERED")
        if action_type == "PAY_RECURRING" and effect.payee_id != scope.payee_id:
            reasons.add("PAYEE_NOT_COVERED")
    return sorted(reasons)
