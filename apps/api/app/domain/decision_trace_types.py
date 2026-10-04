"""Strict, bounded copies of the facts and results used by a simulated decision."""

import copy
import json
import math
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

MAX_TRACE_BYTES = 10 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 250000
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(min_length=1, max_length=160)]
Money = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
SignedMoney = Annotated[StrictInt, Field(ge=-(2**63), le=2**63 - 1)]


def _bounded_json_object(value: Any, *, trusted_money: bool) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("Trace JSON must be an object")
    nodes = 0

    def visit(child: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ValueError("Trace JSON exceeds its depth or node limit")
        if child is None or type(child) in (str, bool, int):
            return
        if type(child) is float:
            if not math.isfinite(child):
                raise ValueError("Trace JSON numbers must be finite")
            return
        if type(child) is dict:
            for key, item in child.items():
                if type(key) is not str:
                    raise ValueError("Trace JSON keys must be strings")
                if trusted_money and key == "amount_options_cents" and item is not None:
                    if (
                        type(item) is not list
                        or not 2 <= len(item) <= 8
                        or any(
                            type(amount) is not int or not 0 < amount <= 2**63 - 1
                            for amount in item
                        )
                    ):
                        raise ValueError("Trace amount options must be 2 to 8 integer cents")
                elif trusted_money and key.endswith("_cents") and item is not None:
                    if type(item) is not int or not -(2**63) <= item <= 2**63 - 1:
                        raise ValueError("Trace monetary values must be integer cents")
                visit(item, depth + 1)
            return
        if type(child) is list:
            for item in child:
                visit(item, depth + 1)
            return
        raise ValueError("Trace JSON permits only objects, lists and finite JSON scalar values")

    visit(value, 0)
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ValueError("Trace JSON cannot be canonically encoded") from error
    if len(encoded) > MAX_TRACE_BYTES:
        raise ValueError("Trace JSON exceeds the byte limit")
    return copy.deepcopy(value)


def _json_object(value: Any) -> dict[str, Any]:
    return _bounded_json_object(value, trusted_money=True)


def _raw_json_object(value: Any) -> dict[str, Any]:
    # Raw copies may contain the exact false claims that caused a BLOCKED decision.
    # Their hash integrity says nothing about their permission or financial validity.
    return _bounded_json_object(value, trusted_money=False)


JsonObject = Annotated[dict[str, Any], BeforeValidator(_json_object)]
RawJsonObject = Annotated[dict[str, Any], BeforeValidator(_raw_json_object)]


class TraceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    @field_validator("simulation", mode="before", check_fields=False)
    @classmethod
    def simulated_only(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("Trace simulation flag must be the boolean true")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def aware_utc(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Trace timestamps must be timezone-aware")
            return value.astimezone(UTC)
        return value


class TraceEvidence(TraceModel):
    id: UUID
    user_id: UUID
    evidence_level: Literal[
        "BANK_CONFIRMED",
        "BANK_OBSERVED",
        "USER_DECLARED",
        "MODEL_INFERRED",
        "USER_CONFIRMED_POLICY",
        "USER_CONFIRMED_ACTION",
    ]
    source_type: Annotated[str, Field(min_length=1, max_length=48)]
    source_ref: Label
    content: RawJsonObject
    content_hash: Digest
    captured_content_hash: Digest
    content_integrity: Literal["VERIFIED", "INVALID"]
    status_at_decision: Literal["VALID", "CONFLICTED", "UNKNOWN", "SUPERSEDED"]
    observed_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None
    supersedes_evidence_id: UUID | None = None

    @model_validator(mode="after")
    def source_window(self) -> Self:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("Evidence validity window is reversed")
        if self.supersedes_evidence_id == self.id:
            raise ValueError("Evidence cannot supersede itself")
        return self


class TracePolicy(TraceModel):
    id: UUID
    user_id: UUID
    policy_id: UUID
    version_number: Annotated[StrictInt, Field(gt=0)]
    configuration: RawJsonObject
    configuration_hash: Digest
    captured_configuration_hash: Digest
    configuration_integrity: Literal["VERIFIED", "INVALID"]
    status_at_decision: Label
    confirmed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def policy_window(self) -> Self:
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("Policy validity window is reversed")
        return self


class TraceConstraint(TraceModel):
    constraint_key: Annotated[str, Field(min_length=1, max_length=120)]
    policy_version_id: UUID | None = None
    is_hard: bool
    satisfied: bool | None = None
    required_cents: Money | None = None
    available_cents: SignedMoney | None = None
    due_date: date | None = None
    calculation: JsonObject = Field(default_factory=dict)
    reason_code: Annotated[str, Field(min_length=1, max_length=80)]


class TraceCandidate(TraceModel):
    candidate_key: Label
    kind: Annotated[str, Field(min_length=1, max_length=64)]
    status: Label
    inputs: JsonObject = Field(default_factory=dict)
    result: JsonObject = Field(default_factory=dict)
    reasons: Annotated[list[Label], Field(max_length=1000)] = Field(default_factory=list)


class _TraceContent(TraceModel):
    schema_version: Literal["decision-trace-v1"] = "decision-trace-v1"
    simulation: Literal[True] = True
    run_id: UUID
    user_id: UUID
    phase: Literal[
        "PREPARE",
        "CONFIRM",
        "RESERVE",
        "BANK_ACCEPT",
        "RECOVERY_PLAN",
        "CONTRACT_SETTLEMENT",
        "EVALUATION",
    ]
    as_of: datetime
    action_id: UUID | None = None
    parent_run_id: UUID | None = None
    algorithm_versions: dict[Label, Label]
    inputs: JsonObject
    sources: Annotated[list[TraceEvidence], Field(max_length=10000)] = Field(default_factory=list)
    policies: Annotated[list[TracePolicy], Field(max_length=1000)] = Field(default_factory=list)
    constraints: Annotated[list[TraceConstraint], Field(max_length=10000)] = Field(
        default_factory=list
    )
    candidates: Annotated[list[TraceCandidate], Field(max_length=1000)] = Field(
        default_factory=list
    )
    outcome: JsonObject

    @field_validator("algorithm_versions")
    @classmethod
    def bounded_algorithms(cls, value: dict[str, str]) -> dict[str, str]:
        if not 1 <= len(value) <= 64:
            raise ValueError("Trace must identify 1 to 64 actual algorithm versions")
        return value

    @model_validator(mode="after")
    def bounded_snapshot(self) -> Self:
        from app.domain.decision_trace import _validate_content

        _validate_content(self)
        return self


class DecisionTrace(_TraceContent):
    input_hash: Digest
    trace_hash: Digest

    @model_validator(mode="after")
    def matching_hashes(self) -> Self:
        from app.domain.decision_trace import _validate_hashes

        _validate_hashes(self)
        return self


class TraceReason(TraceModel):
    code: Label
    text: Annotated[str, Field(min_length=1, max_length=1000)]
    references: Annotated[list[Label], Field(min_length=1, max_length=100)]


class TraceExplanation(TraceModel):
    schema_version: Literal["decision-explanation-v1"] = "decision-explanation-v1"
    simulation: Literal[True] = True
    run_id: UUID
    user_id: UUID
    level: Label | None = None
    financial_evaluation: Label | None = None
    confirmation_required: bool | None = None
    confirmation_satisfied: bool | None = None
    summary: list[str]
    reasons: list[TraceReason]
    audit_chain: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
