"""Bounded, versioned original simulated audit records and verification results."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, field_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(min_length=1, max_length=160)]
Count = Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
Sequence = Annotated[StrictInt, Field(ge=1, le=2**31 - 1)]
EVENT_TYPES = frozenset(
    {
        "EPOCH_STARTED",
        "EPOCH_SEALED",
        "DECISION_RECORDED",
        "ACTION_CREATED",
        "ACTION_STATE_CHANGED",
        "BANK_ACCEPTED",
        "BANK_SETTLED",
        "ACTION_PROJECTED",
        "RECOVERY_OBSERVED",
        "POLICY_VERSION_CONFIRMED",
        "POLICY_STATE_CHANGED",
        "GOAL_INITIALIZED",
    }
)
PUBLIC_SUBJECT_KINDS = frozenset(
    {
        "DECISION_RUN",
        "ACTION_PLAN",
        "ACTION_RECEIPT",
        "POLICY",
        "POLICY_VERSION",
        "EVIDENCE",
        "ACCOUNT",
        "GOAL",
        "ASSET_POSITION",
        "ASSET_PRODUCT",
        "BANK_OPERATION",
        "BANK_REDEMPTION",
        "BANK_POSTING",
        "RESOURCE_CLAIM",
        "TRANSACTION",
    }
)
SUBJECT_KINDS = PUBLIC_SUBJECT_KINDS | frozenset(
    {
        "USER",
        "TRANSACTION",
        "CREDIT_CARD_BILL",
        "POLICY_PROPOSAL",
        "DECISION_CONSTRAINT",
        "LEGACY_AUDIT_EVENT",
    }
)


def _json(value: Any, *, raw: bool) -> dict[str, Any]:
    from app.domain.audit_chain import canonical_bytes

    return cast(dict[str, Any], json.loads(canonical_bytes(value, raw=raw)))


TrustedJsonObject = Annotated[dict[str, Any], BeforeValidator(lambda v: _json(v, raw=False))]
RawJsonObject = Annotated[dict[str, Any], BeforeValidator(lambda v: _json(v, raw=True))]


class AuditModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    @field_validator("simulation", mode="before", check_fields=False)
    @classmethod
    def simulated(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("Audit simulation must be the boolean true")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def aware(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Audit timestamps must be timezone-aware")
            return value.astimezone(UTC)
        return value


class AuditSubject(AuditModel):
    schema_version: Literal["audit-subject-v1"] = "audit-subject-v1"
    canonical_version: Literal["audit-canonical-json-v1"] = "audit-canonical-json-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    kind: Label
    id: UUID
    scope: Literal["TENANT", "GLOBAL_CATALOG"] = "TENANT"
    snapshot_version: Sequence = 1
    data: RawJsonObject


class AuditReference(AuditModel):
    kind: Label
    id: UUID
    scope: Literal["TENANT", "GLOBAL_CATALOG"] = "TENANT"
    user_id: UUID | None
    role: Literal["BASIS", "BEFORE", "AFTER"] = "BASIS"
    snapshot_hash: Digest
    snapshot_version: Sequence = 1


class AuditAnchor(AuditModel):
    kind: Label
    reference_id: UUID
    snapshot_hash: Digest
    digest: Digest
    hash_algorithm: Label


class AuditChange(AuditModel):
    kind: Label
    id: UUID
    field: Label
    before: Any = None
    after: Any
    before_snapshot_hash: Digest | None = None
    after_snapshot_hash: Digest


class AuditFactContext(AuditModel):
    reason_code: Label | None = None
    cause_ref: Label | None = None
    details: TrustedJsonObject = Field(default_factory=dict)


class AuditLegacyOrigin(AuditModel):
    reason: Literal["UNRECORDED_BEFORE_ACTIVATION"]
    original_request_hash: Digest


class AuditHead(AuditModel):
    schema_version: Literal["audit-head-v1"] = "audit-head-v1"
    canonical_version: Literal["audit-canonical-json-v1"] = "audit-canonical-json-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    epoch_number: Sequence
    status: Literal["OPEN", "SEALED"] = "OPEN"
    event_count: Count
    last_sequence: Count
    last_event_id: UUID | None
    last_event_hash: Digest | None
    genesis_event_id: UUID | None
    genesis_event_hash: Digest | None
    previous_epoch_id: UUID | None = None
    previous_seal_hash: Digest | None = None


class AuditEpochTransition(AuditModel):
    kind: Literal["INIT", "RESET", "SEAL"]
    reset_key: Label | None = None
    reason: Label | None = None
    principal: Label | None = None
    previous_epoch_id: UUID | None = None
    previous_seal_hash: Digest | None = None
    seed_version: Label | None = None
    summary_version: Label | None = None
    dataset_hash: Digest | None = None
    archive_manifest_hash: Digest | None = None
    archive_record_counts: dict[Label, Count] = Field(default_factory=dict)
    pre_seal_head: AuditHead | None = None
    legacy_history: bool = False


class AuditObservation(AuditModel):
    kind: Literal["WAITING_PROJECTION", "PROJECTION_FAILED", "BANK_ERROR", "RUN_COMPLETED"]
    error_code: Label | None = None
    bank_status: Label | None = None
    run_id: UUID | None = None
    action_id: UUID | None = None
    request_id: UUID | None = None


class AuditPayload(AuditModel):
    fact_key: Label
    correlation_kind: Literal["EPOCH", "DECISION_RUN", "POLICY", "GOAL"]
    references: Annotated[list[AuditReference], Field(max_length=10000)] = Field(
        default_factory=list
    )
    anchors: Annotated[list[AuditAnchor], Field(max_length=10000)] = Field(default_factory=list)
    changes: Annotated[list[AuditChange], Field(max_length=10000)] = Field(default_factory=list)
    missing_evidence_ids: Annotated[list[UUID], Field(max_length=10000)] = Field(
        default_factory=list
    )
    legacy_origin: AuditLegacyOrigin | None = None
    epoch_transition: AuditEpochTransition | None = None
    observation: AuditObservation | None = None
    context: AuditFactContext = Field(default_factory=AuditFactContext)


class AuditIntent(AuditModel):
    user_id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=80)]
    aggregate_type: Annotated[str, Field(min_length=1, max_length=48)]
    aggregate_id: UUID
    correlation_id: UUID
    causation_id: UUID | None = None
    decision_run_id: UUID | None = None
    action_plan_id: UUID | None = None
    action_receipt_id: UUID | None = None
    idempotency_key: Label
    payload_version: Sequence = 1
    payload: AuditPayload
    occurred_at: datetime


class AuditEnvelope(AuditIntent):
    schema_version: Literal["audit-event-v1"] = "audit-event-v1"
    canonical_version: Literal["audit-canonical-json-v1"] = "audit-canonical-json-v1"
    simulation: Literal[True] = True
    id: UUID
    epoch_id: UUID
    sequence_number: Sequence
    previous_hash: Digest | None
    observed_at: datetime
    appended_at: datetime
    event_hash: Digest


class AuditEpochSeal(AuditModel):
    schema_version: Literal["audit-epoch-seal-v1"] = "audit-epoch-seal-v1"
    canonical_version: Literal["audit-canonical-json-v1"] = "audit-canonical-json-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    epoch_number: Sequence
    head: AuditHead
    previous_epoch_id: UUID | None = None
    previous_seal_hash: Digest | None = None
    archive_manifest_hash: Digest
    archive_record_counts: dict[Label, Count]
    seed_version: Label | None = None
    summary_version: Label | None = None
    dataset_hash: Digest | None = None
    reset_key: Label
    reason: Label
    principal: Label
    sealed_at: datetime
    seal_hash: Digest


class AuditCheckpoint(AuditModel):
    schema_version: Literal["audit-checkpoint-v1"] = "audit-checkpoint-v1"
    canonical_version: Literal["audit-canonical-json-v1"] = "audit-canonical-json-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    epoch_number: Sequence
    genesis_event_id: UUID
    genesis_event_hash: Digest
    expected_count: Sequence
    last_sequence: Sequence
    tail_id: UUID
    tail_hash: Digest
    previous_epoch_id: UUID | None = None
    previous_seal_hash: Digest | None = None
    captured_at: datetime
    checkpoint_hash: Digest


class AuditDiagnostic(AuditModel):
    code: Label
    sequence_number: Count | None = None
    event_id: UUID | None = None
    reference: Label | None = None
    message: Annotated[str, Field(min_length=1, max_length=1000)]


class ReferenceBundle(AuditModel):
    subjects: Annotated[list[AuditSubject], Field(max_length=100000)] = Field(default_factory=list)
    current_subjects: Annotated[list[AuditSubject], Field(max_length=100000)] = Field(
        default_factory=list
    )
    original_errors: Annotated[list[AuditDiagnostic], Field(max_length=1000)] = Field(
        default_factory=list
    )


Status = Literal[
    "VALID", "INTEGRITY_ERROR", "UNSUPPORTED_VERSION", "LEGACY_UNAUDITED", "INCOMPLETE"
]


class AuditVerification(AuditModel):
    schema_version: Literal["audit-verification-v1"] = "audit-verification-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID | None
    status: Status
    chain_status: Status
    reference_status: Status
    checkpoint_status: Literal["VERIFIED", "NOT_REQUESTED", "MISMATCH", "UNAVAILABLE"]
    actual_count: Count
    expected_count: Count
    actual_tail_id: UUID | None
    actual_tail_hash: Digest | None
    expected_tail_id: UUID | None
    expected_tail_hash: Digest | None
    verified_through_sequence: Count
    errors: list[AuditDiagnostic] = Field(default_factory=list)
    warnings: list[AuditDiagnostic] = Field(default_factory=list)
    errors_truncated: bool = False
