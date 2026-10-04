"""Evidence-bound whole-position recovery contracts; no bank effects occur here."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary_types import (
    BoundaryModel,
    BoundaryPoint,
    BoundaryPolicyVersion,
    BoundaryResult,
    SourcedFact,
)
from app.domain.policy_configuration import MoneyCents
from pydantic import Field, StrictInt, model_validator


class RecoveryAuthorization(BoundaryPolicyVersion):
    user_id: UUID
    policy_status: Literal[
        "PROPOSED", "CONFIRMED", "ACTIVE", "SUSPENDED", "REVOKED", "EXPIRED", "SUPERSEDED"
    ]
    latest_version_id: UUID


class RecoveryQuote(SourcedFact):
    quote_id: UUID
    user_id: UUID
    position_id: UUID
    product_id: UUID
    product_version_number: Annotated[StrictInt, Field(gt=0)]
    terms_digest: str
    kind: Literal["REDEEM", "EARLY_WITHDRAW", "MATURE"]
    principal_cents: Annotated[StrictInt, Field(gt=0, le=9223372036854775807)]
    fee_cents: MoneyCents
    loss_cents: MoneyCents
    net_cents: MoneyCents
    request_at: datetime
    principal_available_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def conserved_quote(self) -> Self:
        if self.net_cents + self.fee_cents + self.loss_cents != self.principal_cents:
            raise ValueError("Quoted principal must equal net cash plus explicit fee and loss")
        if self.principal_available_at < self.request_at or self.expires_at <= self.request_at:
            raise ValueError("Recovery quote times must be ordered")
        return self


class RecoveryPosition(SourcedFact):
    position_id: UUID
    account_id: UUID
    destination_account_id: UUID
    goal_id: UUID | None = None
    purchased_at: datetime
    acquisition: Literal["AUTHORIZED_PURCHASE", "MANUAL", "UNKNOWN"]
    product: AssetProductTerms
    original_authorization: BoundaryPolicyVersion | None = None
    reserved_principal_cents: MoneyCents = 0
    quote: RecoveryQuote | None = None


class RecoveryAction(BoundaryModel):
    user_id: UUID
    position_id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    goal_id: UUID | None = None
    product_id: UUID
    product_version_number: StrictInt
    terms_digest: str
    original_policy_version_id: UUID
    current_policy_id: UUID
    current_policy_version_id: UUID
    current_authorization_hash: str
    quote: RecoveryQuote
    autonomy_level: Literal["AUTO_EXECUTE", "ASK_ONCE"]
    baseline_boundary_hash: str
    plan_inputs_hash: str
    expires_at: datetime
    request_hash: str


class RecoveryCandidate(BoundaryModel):
    position_id: UUID
    decision: Literal["AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"]
    reasons: list[str] = Field(default_factory=list)
    action: RecoveryAction | None = None
    projected_boundary: BoundaryResult | None = None


class RecoveryPlan(BoundaryModel):
    algorithm_version: str
    user_id: UUID
    as_of: datetime
    simulation: Literal[True] = True
    preview_only: Literal[True] = True
    status: Literal[
        "NO_RECOVERY_NEEDED",
        "AUTO_RECOVERY_AVAILABLE",
        "PARTIAL_RECOVERY_AVAILABLE",
        "ASK_ONCE",
        "ADVISE_ONLY",
        "NO_SAFE_RECOVERY",
        "INSUFFICIENT_EVIDENCE",
    ]
    actual_boundary: BoundaryResult
    projected_boundary: BoundaryResult | None = None
    steps: list[RecoveryAction] = Field(default_factory=list)
    candidates: list[RecoveryCandidate] = Field(default_factory=list)
    uncovered_checkpoints: list[BoundaryPoint] = Field(default_factory=list)
    first_sustained_safe_point: BoundaryPoint | None = None
    plan_hash: str
    reasons: list[str] = Field(default_factory=list)
