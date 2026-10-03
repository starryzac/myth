"""Strict contracts for a single-product, zero-fee simulated allocation preview."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel, BoundaryResult
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

Days = Annotated[StrictInt, Field(ge=0, le=3660)]
BasisPoints = Annotated[StrictInt, Field(ge=0, le=10000)]


class YieldRule(BoundaryModel):
    protocol: Literal["simple-annual-yield-v1"]
    basis: Literal["ACT_365"]
    annual_yield_bps: BasisPoints
    simulation: StrictBool
    fee_cents: MoneyCents
    purchase_fee_bps: BasisPoints
    redemption_fee_bps: BasisPoints
    accrual: Literal["UNTIL_MATURITY", "UNTIL_REDEMPTION_REQUEST"]

    @model_validator(mode="after")
    def simulated_zero_fee_only(self) -> Self:
        if not self.simulation or any(
            (self.fee_cents, self.purchase_fee_bps, self.redemption_fee_bps)
        ):
            raise ValueError("Only explicit simulated zero-fee yields are supported")
        return self


class PrincipalTerms(BoundaryModel):
    day_basis: Literal["CALENDAR"]
    guaranteed: StrictBool
    settlement_delay_days: Days
    principal_return_bps: Annotated[StrictInt, Field(ge=10000, le=10000)]
    rollover: StrictBool
    auto_rollover: StrictBool
    yield_rule: YieldRule

    @model_validator(mode="after")
    def full_principal_without_rollover(self) -> Self:
        if not self.guaranteed or self.rollover or self.auto_rollover:
            raise ValueError("Explicit guaranteed principal and no rollover are required")
        return self


class FixedPrincipalTerms(PrincipalTerms):
    protocol: Literal["fixed-principal-return-v1"]
    kind: Literal["RETURN_TO_CASH"] = "RETURN_TO_CASH"
    term_days: Annotated[StrictInt, Field(gt=0, le=3660)]


class PlannedPrincipalTerms(PrincipalTerms):
    protocol: Literal["planned-principal-return-v1"]
    kind: Literal["REDEEM_ON_REQUEST"] = "REDEEM_ON_REQUEST"


class AssetProductTerms(BoundaryModel):
    product_id: UUID
    product_code: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    version_number: Annotated[StrictInt, Field(gt=0)]
    asset_class: str
    risk_level: Annotated[StrictInt, Field(ge=0, le=5)]
    principal_fluctuation: StrictBool
    minimum_purchase_cents: MoneyCents
    lock_days: Days
    redemption_delay_days: Days
    annual_yield_bps: BasisPoints
    early_withdrawal_loss_bps: BasisPoints
    auto_purchase_allowed: StrictBool
    auto_redeem_allowed: StrictBool
    created_at: datetime
    effective_from: datetime
    effective_until: datetime | None = None
    maturity_rule: dict[str, Any]
    terms_digest: str

    @model_validator(mode="after")
    def canonical_terms(self) -> Self:
        if configuration_hash(self.maturity_rule) != self.terms_digest:
            raise ValueError("Product terms digest must match its full maturity_rule JSON")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("Product effective window is reversed")
        return self


class PlannedExit(BoundaryModel):
    kind: Literal["FIXED_MATURITY", "PLANNED_REDEMPTION"]
    request_at: datetime | None = None
    principal_available_at: datetime
    earning_days: Days
    liquidity_days: Days
    terms_digest: str


class AssetCashUse(BoundaryModel):
    account_id: UUID
    amount_cents: MoneyCents


class AssetCandidate(BoundaryModel):
    product_id: UUID
    product_code: str
    version_number: StrictInt
    asset_class: str
    status: Literal["FEASIBLE", "REJECTED"]
    financial_cap_cents: MoneyCents | None = None
    max_allocatable_cents: MoneyCents | None = None
    net_simulated_yield_cents: MoneyCents | None = None
    exit_plan: PlannedExit | None = None
    candidate_boundary_hash: str | None = None
    reasons: list[str] = Field(default_factory=list)


class AssetAllocationResult(BoundaryModel):
    algorithm_version: str
    policy_id: UUID
    policy_version_id: UUID
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUID | None = None
    status: Literal["READY", "INSUFFICIENT_EVIDENCE", "LIQUIDITY_RISK", "INACTIVE_POLICY"]
    financial_only: Literal[True] = True
    preview_only: Literal[True] = True
    selected_asset_class: str | None = None
    selected_product_id: UUID | None = None
    suggested_cents: MoneyCents | None = None
    retained_cash_cents: MoneyCents | None = None
    comparison_days: Days | None = None
    scope_cash_cents: MoneyCents | None = None
    remaining_managed_cents: MoneyCents | None = None
    net_simulated_yield_cents: MoneyCents | None = None
    source_cash_uses: list[AssetCashUse] = Field(default_factory=list)
    candidates: list[AssetCandidate] = Field(default_factory=list)
    baseline_boundary: BoundaryResult
    reservation_adjusted_boundary: BoundaryResult | None = None
    candidate_boundary: BoundaryResult | None = None
    selection_hash: str
    reasons: list[str] = Field(default_factory=list)
