"""Immutable economic effects shared by revalidation and the independent simulated bank."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.asset_allocation_types import AssetProductTerms, PlannedExit
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundaryResult,
    BoundarySnapshot,
    SourceIssue,
)
from app.domain.goal_allocation import IncomeLot
from app.domain.income_ledger import IncomeUse, location_id
from app.domain.policy_configuration import MoneyCents
from app.domain.recovery_types import RecoveryQuote
from pydantic import Field, StrictInt, model_validator

PositiveMoney = Annotated[MoneyCents, Field(gt=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ActionType = Literal[
    "TRANSFER_INTERNAL", "PAY_RECURRING", "ALLOCATE_GOAL", "PURCHASE_ASSET", "REDEEM_ASSET"
]


class CashUse(BoundaryModel):
    account_id: UUID
    amount_cents: PositiveMoney


class BillReference(BoundaryModel):
    kind: Literal["bill"] = "bill"
    bill_id: UUID
    evidence_ids: list[UUID]


class OccurrenceReference(BoundaryModel):
    kind: Literal["occurrence"] = "occurrence"
    policy_id: UUID
    period: Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
    final_total_cents: MoneyCents | None = None
    evidence_ids: list[UUID]


class ExecutionEffect(BoundaryModel):
    """An agreed economic command; no query clock or mutable financial snapshot hash."""

    simulation: Literal[True] = True
    operation_id: UUID
    user_id: UUID
    business_key: Annotated[str, Field(min_length=1, max_length=160)]
    action_type: ActionType
    amount_cents: PositiveMoney
    cash_uses: Annotated[list[CashUse], Field(max_length=100)] = Field(default_factory=list)
    income_uses: Annotated[list[IncomeUse], Field(max_length=10000)] = Field(default_factory=list)
    destination_account_id: UUID | None = None
    goal_id: UUID | None = None
    policy_id: UUID | None = None
    policy_version_id: UUID | None = None
    policy_version_ids: Annotated[list[UUID], Field(max_length=100)] = Field(default_factory=list)
    liability: (
        Annotated[BillReference | OccurrenceReference, Field(discriminator="kind")] | None
    ) = None
    payee_id: str | None = None
    payee_evidence_id: UUID | None = None
    product_id: UUID | None = None
    product_version_number: Annotated[StrictInt, Field(gt=0)] | None = None
    terms_digest: Digest | None = None
    position_id: UUID | None = None
    position_account_id: UUID | None = None
    return_account_id: UUID | None = None
    purchase_exit: PlannedExit | None = None
    original_policy_version_id: UUID | None = None
    fee_cents: MoneyCents = 0
    loss_cents: MoneyCents = 0
    net_cents: MoneyCents | None = None
    quote_id: UUID | None = None
    settlement_delay_days: Annotated[StrictInt, Field(ge=0, le=3660)] = 0
    latest_arrival_at: datetime | None = None
    valid_from: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def economic_shape(self) -> Self:
        if self.simulation is not True:
            raise ValueError("Only simulated effects are supported")
        if self.expires_at <= self.valid_from:
            raise ValueError("Effect validity must be nonempty")
        if len({u.account_id for u in self.cash_uses}) != len(self.cash_uses):
            raise ValueError("Cash source accounts must be unique")
        if len({(u.origin_transaction_id, u.account_id) for u in self.income_uses}) != len(
            self.income_uses
        ):
            raise ValueError("Income origin locations must be unique")
        if len(set(self.policy_version_ids)) != len(self.policy_version_ids):
            raise ValueError("Authority version identities must be unique")
        if self.action_type == "TRANSFER_INTERNAL" and (
            self.policy_id is not None
            or self.policy_version_id is not None
            or self.policy_version_ids
        ):
            raise ValueError("Internal transfers use exact one-shot consent, not a guessed policy")
        if self.action_type not in {"PURCHASE_ASSET", "REDEEM_ASSET"} and (
            self.latest_arrival_at is not None or self.settlement_delay_days
        ):
            raise ValueError("Only assets have deferred principal settlement")
        if self.action_type == "PURCHASE_ASSET" and self.original_policy_version_id is not None:
            raise ValueError("A new purchase cannot claim an existing position authority")
        if self.action_type == "REDEEM_ASSET" and self.income_uses:
            raise ValueError("Principal return cannot consume or invent income origins")
        funding = {u.account_id: u.amount_cents for u in self.cash_uses}
        if self.action_type == "PURCHASE_ASSET":
            if self.return_account_id is None or self.return_account_id not in funding:
                raise ValueError("Purchase must bind one funded account as its return destination")
            if (
                self.purchase_exit is None
                or self.latest_arrival_at is None
                or self.purchase_exit.terms_digest != self.terms_digest
                or not self.valid_from
                <= self.purchase_exit.principal_available_at
                <= self.latest_arrival_at
                or (self.purchase_exit.kind == "FIXED_MATURITY")
                != (self.purchase_exit.request_at is None)
                or (
                    self.purchase_exit.request_at is not None
                    and not self.valid_from
                    <= self.purchase_exit.request_at
                    <= self.purchase_exit.principal_available_at
                )
            ):
                raise ValueError(
                    "Purchase requires its exact term-bound exit plan and arrival limit"
                )
        elif self.return_account_id is not None or self.purchase_exit is not None:
            raise ValueError("Only purchase records a future principal return account and plan")
        uses: dict[UUID, int] = {}
        for use in self.income_uses:
            if use.fragment_id != location_id(use.origin_transaction_id, use.account_id):
                raise ValueError("Income use must bind the exact origin location")
            uses[use.account_id] = uses.get(use.account_id, 0) + use.amount_cents
        if any(amount > funding.get(identity, 0) for identity, amount in uses.items()):
            raise ValueError("Income uses cannot exceed cash debited from their location")
        if (
            self.action_type != "REDEEM_ASSET"
            and sum(u.amount_cents for u in self.cash_uses) != self.amount_cents
        ):
            raise ValueError("Cash funding must equal the complete economic amount")
        if self.action_type == "REDEEM_ASSET":
            if (
                self.cash_uses
                or self.net_cents is None
                or self.net_cents + self.fee_cents + self.loss_cents != self.amount_cents
            ):
                raise ValueError("Redemption uses conserved net principal, fee and loss")
            if self.quote_id is None or self.latest_arrival_at is None:
                raise ValueError("Redemption requires a bound quote and arrival upper bound")
        elif (
            self.fee_cents
            or self.loss_cents
            or self.net_cents is not None
            or self.quote_id is not None
        ):
            raise ValueError("Only redemption may carry explicit costs and a net quote")
        if self.action_type in {"PURCHASE_ASSET", "REDEEM_ASSET"}:
            if any(
                v is None
                for v in (
                    self.product_id,
                    self.product_version_number,
                    self.terms_digest,
                    self.position_id,
                    self.position_account_id,
                )
            ):
                raise ValueError("Asset effects bind exact product, terms and position identities")
        elif any(
            v is not None
            for v in (
                self.product_id,
                self.product_version_number,
                self.terms_digest,
                self.position_id,
                self.position_account_id,
                self.original_policy_version_id,
            )
        ):
            raise ValueError("Non-asset effects cannot carry asset identities")
        if self.action_type == "PAY_RECURRING":
            if self.liability is None or not self.payee_id or self.payee_evidence_id is None:
                raise ValueError("Payment requires one liability and a confirmed payee binding")
        elif (
            self.liability is not None
            or self.payee_id is not None
            or self.payee_evidence_id is not None
        ):
            raise ValueError("Only payment may bind a liability and payee")
        if (
            self.action_type in {"TRANSFER_INTERNAL", "ALLOCATE_GOAL", "REDEEM_ASSET"}
            and self.destination_account_id is None
        ):
            raise ValueError("This action requires an exact destination")
        if (
            self.action_type in {"PAY_RECURRING", "PURCHASE_ASSET"}
            and self.destination_account_id is not None
        ):
            raise ValueError(
                "External payment and purchase do not credit an internal cash destination"
            )
        if self.action_type in {"TRANSFER_INTERNAL", "PAY_RECURRING"} and self.goal_id is not None:
            raise ValueError("General cash actions cannot silently change goal ownership")
        if self.action_type == "ALLOCATE_GOAL" and self.goal_id is None:
            raise ValueError("Goal allocation requires a goal")
        if self.action_type != "TRANSFER_INTERNAL" and (
            self.policy_id is None or self.policy_version_id is None
        ):
            raise ValueError("Policy-linked effects require exact policy and version")
        if (
            self.policy_version_id is not None
            and self.policy_version_id not in self.policy_version_ids
        ):
            raise ValueError("Selected version must appear in the complete authority references")
        if self.latest_arrival_at is not None and self.latest_arrival_at < self.valid_from:
            raise ValueError("Arrival upper bound cannot precede effect validity")
        return self


class BankCommand(BoundaryModel):
    effect: ExecutionEffect
    effect_hash: Digest

    @model_validator(mode="after")
    def matches_effect(self) -> Self:
        from app.domain.execution import execution_effect_hash

        if execution_effect_hash(self.effect) != self.effect_hash:
            raise ValueError("Bank command hash does not bind its complete economic effect")
        return self


class ConfirmationGrant(BoundaryModel):
    """A verified one-shot confirmation fact; its provenance is checked by the adapter."""

    user_id: UUID
    operation_id: UUID
    effect_hash: Digest
    evidence_id: UUID
    confirmed_at: datetime
    expires_at: datetime


class ExecutionContext(BoundaryModel):
    user_id: UUID
    snapshot: BoundarySnapshot
    versions: list[BoundaryPolicyVersion]
    positions: list[BoundaryPosition]
    boundary_products: list[BoundaryProduct]
    products: list[AssetProductTerms] = Field(default_factory=list)
    lots: list[IncomeLot] = Field(default_factory=list)
    exposure: AssetExposure | None = None
    redemption_quote: RecoveryQuote | None = None
    requires_confirmation: bool = False
    reserved_cash_by_account: dict[UUID, MoneyCents] = Field(default_factory=dict)
    reserved_goal_cash_by_goal: dict[UUID, MoneyCents] = Field(default_factory=dict)
    reserved_position_ids: list[UUID] = Field(default_factory=list)
    source_issues: list[SourceIssue] = Field(default_factory=list)


class ExecutionValidation(BoundaryModel):
    simulation: Literal[True] = True
    financial_only: Literal[True] = True
    status: Literal["READY", "CONFIRMATION_REQUIRED", "BLOCKED", "INSUFFICIENT_EVIDENCE"]
    effect_hash: Digest
    baseline_boundary: BoundaryResult
    projected_boundary: BoundaryResult | None = None
    reservation_adjusted_baseline: BoundaryResult | None = None
    reservation_adjusted_boundary: BoundaryResult | None = None
    projected_snapshot: BoundarySnapshot | None = None
    projected_positions: list[BoundaryPosition] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
