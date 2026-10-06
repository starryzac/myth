"""Internal, provenance-checked facts for autonomy; never a client authorization schema."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel, SourceIssue
from app.domain.execution_types import (
    ConfirmationGrant,
    Digest,
    ExecutionEffect,
    ExecutionValidation,
)
from pydantic import Field, model_validator

AutonomyLevel = Literal["AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"]
FinancialEvaluation = Literal["VERIFIED", "NOT_EVALUATED", "REJECTED"]
Reason = Annotated[str, Field(min_length=1, max_length=160)]
Identities = Annotated[list[UUID], Field(max_length=10000)]
Reasons = Annotated[list[Reason], Field(max_length=100)]


class EvidenceModel(BoundaryModel):
    @model_validator(mode="after")
    def unique_identities(self) -> Self:
        for name, value in self.__dict__.items():
            if name.endswith("_ids") and isinstance(value, list) and len(set(value)) != len(value):
                raise ValueError("Evidence and authority identities must be unique")
        return self


class AuthorityAssessment(EvidenceModel):
    """Adapter verdict backed by formal policy or exact user/account relationship evidence.

    AUTHORIZED is not a continuing policy grant: user-initiated transfers still require
    one-shot consent. OUTSIDE_AUTHORITY without an effect is only policy-change advice.
    """

    status: Literal["AUTHORIZED", "OUTSIDE_AUTHORITY", "STALE_VERSION", "MISSING_EVIDENCE"]
    policy_version_ids: Identities = Field(default_factory=list)
    evidence_ids: Identities = Field(default_factory=list)
    reasons: Reasons = Field(default_factory=list)


class PayeeAssessment(EvidenceModel):
    status: Literal[
        "NOT_APPLICABLE",
        "EXISTING_CONFIRMED",
        "USER_INITIATED_NEW",
        "AGENT_NEW",
        "MISSING_IDENTITY",
        "UNSUPPORTED",
    ] = "NOT_APPLICABLE"
    evidence_ids: Identities = Field(default_factory=list)


class AutonomyFacts(EvidenceModel):
    """Trusted internal facts, including actual execution-time financial validation.

    source_context_hash binds all verified bank/context facts, owner and supplied clock;
    it excludes the user amount preference and generated action identity. No public API
    may accept these facts, a caller-supplied authorization flag, or candidate worlds.
    """

    user_id: UUID
    as_of: datetime
    action_type: Annotated[str, Field(min_length=1, max_length=64)]
    initiation: Literal["USER_EXPLICIT", "CONFIRMED_POLICY", "AGENT_GENERATED"]
    authority: AuthorityAssessment
    effect: ExecutionEffect | None = None
    validation: ExecutionValidation | None = None
    payee: PayeeAssessment = Field(default_factory=PayeeAssessment)
    source_evidence_ids: Identities = Field(default_factory=list)
    source_issues: Annotated[list[SourceIssue], Field(max_length=1000)] = Field(
        default_factory=list
    )
    hard_block_reasons: Reasons = Field(default_factory=list)
    confirmation_reasons: Reasons = Field(default_factory=list)
    confirmation: ConfirmationGrant | None = None
    source_context_hash: Digest | None = None


class AutonomyWorld(BoundaryModel):
    candidate_key: Annotated[str, Field(min_length=1, max_length=80)]
    facts: AutonomyFacts


class FiniteUserVariable(EvidenceModel):
    """One complete evidence-bound amount preference; no arbitrary bank fact worlds.

    Bank facts and authority must be identical. Source account/origin sets, destination,
    ownership, product and exit terms stay fixed; only amount and source amounts vary.
    """

    variable_id: Annotated[str, Field(min_length=1, max_length=80)]
    field: Literal["amount_cents"] = "amount_cents"
    kind: Literal["USER_PREFERENCE", "BANK_FACT"]
    completeness: Literal["COMPLETE", "INCOMPLETE"]
    evidence_ids: Identities = Field(default_factory=list)
    source_context_hash: Digest
    worlds: Annotated[list[AutonomyWorld], Field(min_length=2, max_length=8)]


class AutonomyDecision(BoundaryModel):
    algorithm_version: str
    simulation: Literal[True] = True
    evaluation_only: Literal[True] = True
    level: AutonomyLevel
    execution_eligible: bool
    financial_evaluation: FinancialEvaluation
    reasons: list[str]
    confirmation_required: bool = False
    confirmation_satisfied: bool = False
    effect_hash: str | None = None
    economic_signature: str | None = None
    uncertainty_status: Literal["NONE", "STABLE", "DIVERGENT", "BLOCKED"] = "NONE"
    candidate_signatures: dict[str, str | None] = Field(default_factory=dict)
