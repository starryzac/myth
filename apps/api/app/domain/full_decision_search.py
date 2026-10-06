"""Exact persisted decision search; typed references never grant financial authority."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.policy_configuration import UUIDReference
from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[StrictInt, Field(ge=0)]
MAX_SCAN_ROWS = 1024
MAX_SCAN_BYTES = 64 * 1024 * 1024


class DecisionSearchQuery(BoundaryModel):
    action_id: UUIDReference | None = None
    action_key: Annotated[StrictStr, Field(min_length=1, max_length=160)] | None = None
    policy_version_id: UUIDReference | None = None
    epoch_id: UUIDReference | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 20
    offset: Annotated[StrictInt, Field(ge=0, le=100000)] = 0

    @field_validator("action_key")
    @classmethod
    def nonblank_key(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("An exact original action key is required")
        return value

    @model_validator(mode="after")
    def explicit_filter(self) -> Self:
        if self.action_id is not None and self.action_key is not None:
            raise ValueError("Choose one exact action identity or key")
        if self.action_id is None and self.action_key is None and self.policy_version_id is None:
            raise ValueError("An action or policy version filter is required")
        return self


class SearchReference(BoundaryModel):
    kind: Literal["ACTION", "MVP_POLICY_VERSION", "AUDIT_EPOCH"]
    identity: UUID
    pointer: str
    relation: Literal["PERSISTED_FOREIGN_KEY", "VERIFIED_TYPED_CAPTURE", "PERSISTED_AUDIT_LINK"]


class DecisionSearchItem(BoundaryModel):
    run_id: UUID
    as_of: datetime
    trigger_type: str
    record_status: str
    action_ids: list[UUID]
    epoch_ids: list[UUID]
    phase: str | None
    snapshot_hash: Hash
    trace_hash: Hash | None
    completeness: Literal["COMPLETE", "LEGACY_PARTIAL", "UNSUPPORTED_VERSION", "INVALID"]
    match_state: Literal["MATCHED", "UNVERIFIABLE"]
    references: list[SearchReference]
    issues: list[str]
    grants_authority: Literal[False] = False
    financial_success_inferred: Literal[False] = False


class DecisionSearchInventory(BoundaryModel):
    actual_owned_decision_count: Count
    known_decision_count: Count
    selected_scope_count: Count
    captured_scope_count: Count
    source_bytes: Count
    action_link_count: Count
    captured_action_link_count: Count
    audit_link_count: Count
    captured_audit_link_count: Count
    verified_typed_count: Count
    unverifiable_count: Count
    returned_count: Count
    row_limit: Literal[1024] = 1024
    byte_limit: Literal[67108864] = 67108864


class DecisionSearchResponse(BoundaryModel):
    schema_version: Literal["full-decision-search-v1"] = "full-decision-search-v1"
    simulation: Literal[True] = True
    user_id: UUID
    read_at: datetime
    business_known_at: datetime
    query: DecisionSearchQuery
    resolved_action_id: UUID | None
    version_family: Literal["NONE", "MVP", "FULL_UNSUPPORTED"]
    state: Literal["SEARCHED", "UNKNOWN"]
    scope: Literal["CURRENT_PERSISTED_DECISION_ROWS"] = "CURRENT_PERSISTED_DECISION_ROWS"
    inventory: DecisionSearchInventory
    source_hash: Hash | None
    verified_match_count: Count
    total_match_count: Count | None
    items: list[DecisionSearchItem]
    next_offset: Count | None
    issues: list[str]
    unsupported_families: list[str]
    absence_is_final: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_success_inferred: Literal[False] = False
    archived_records_searched: Literal[False] = False
    audit_chain_verified: Literal[False] = False


def typed_policy_references(trace: DecisionTrace) -> list[SearchReference]:
    """Read only the original schema's policy and constraint references, never arbitrary UUIDs."""
    verify_trace(trace)
    result = [
        SearchReference(
            kind="MVP_POLICY_VERSION",
            identity=policy.id,
            pointer=f"/decision_trace/policies/{index}/id",
            relation="VERIFIED_TYPED_CAPTURE",
        )
        for index, policy in enumerate(trace.policies)
    ]
    for index, constraint in enumerate(trace.constraints):
        if constraint.policy_version_id is not None:
            result.append(
                SearchReference(
                    kind="MVP_POLICY_VERSION",
                    identity=constraint.policy_version_id,
                    pointer=f"/decision_trace/constraints/{index}/policy_version_id",
                    relation="VERIFIED_TYPED_CAPTURE",
                )
            )
    return result
