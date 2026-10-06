"""FULL authoring contract: whole-family splits, deterministic truth, no frozen claim.

This validates actual supplied original JSON bytes, not whether a reviewer approved
them or a native executor supports them. Those missing gates remain explicit.
"""

import json
import math
import re
from hashlib import sha256
from typing import Annotated, Literal, Self

from app.domain.full_experiment_mechanisms import Digest, Identifier, Model, value_digest
from pydantic import Field, JsonValue, model_validator

TRUTH_DIMENSIONS = frozenset(
    {
        "mandatory_obligations",
        "user_authority",
        "adjustable_ranges",
        "product_attributes",
        "allowed_actions",
        "expected_autonomy",
        "expected_conflicts",
        "expected_recovery",
    }
)
ARMS = ("B0", "B1", "B2", "B3", "B4", "B5", "P")
ABLATIONS = (
    "EVIDENCE_LEVEL",
    "POLICY_VERSION",
    "DYNAMIC_LIVING_RESERVE",
    "MULTI_GOAL_CONSTRAINTS",
    "LIQUIDITY_FILTER",
    "MINIMUM_QUESTION",
    "SAFE_RECOVERY",
    "AUDIT_CHAIN",
)
FULL_METRIC_IDS = tuple(
    [f"S{i}" for i in range(1, 10)]
    + [f"E{i}" for i in range(1, 9)]
    + [f"F{i}" for i in range(1, 6)]
    + [f"A{i}" for i in range(1, 7)]
)
CATEGORIES = frozenset(
    {
        "NORMAL",
        "OBLIGATION_CONFLICT",
        "MULTI_GOAL_CONFLICT",
        "SEASONAL_RESERVE",
        "RENT_CHANGE",
        "GOAL_DEFERRAL",
        "CONSUMPTION",
        "T1_RECOVERY",
        "FIXED_LOCK",
        "EARLY_WITHDRAWAL_LOSS",
        "NEW_PAYEE",
        "AMBIGUITY",
        "VERSION_RACE",
        "DUPLICATE_ACTION",
        "UNKNOWN_EXECUTION",
        "AUDIT_TAMPER",
    }
)


def topology_digest(body: dict[str, JsonValue]) -> str:
    """A number/date change alone cannot move a complete flow to another split."""
    allowed = {
        "protocol",
        "profile",
        "purpose",
        "family_id",
        "scenario_id",
        "initial_state",
        "steps",
    }
    if set(body) - allowed or body.get("protocol") != "full-family-case-input-v1":
        raise ValueError("Closed FULL INPUT contract required; no result/permission injection")
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(row, dict) for row in steps):
        raise ValueError("Complete nonempty native service steps required")
    if not isinstance(body.get("initial_state"), dict):
        raise ValueError("Complete original isolated initial state required")
    for row in steps:
        if not isinstance(row, dict) or set(row) - {
            "step_id",
            "kind",
            "at",
            "inputs",
            "fault",
            "expected_error",
        }:
            raise ValueError("Closed original service step required")
        if not all(key in row for key in ("step_id", "kind", "at", "inputs")):
            raise ValueError("Complete original service step required")
        if not isinstance(row["inputs"], dict):
            raise ValueError("Original service inputs must be an object")
    identities: dict[str, str] = {}

    def normalize(value: JsonValue) -> JsonValue:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            return "$INTEGER"
        if isinstance(value, float):
            return "$FLOAT"
        if isinstance(value, str):
            if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", value):
                return identities.setdefault(value, f"$UUID{len(identities)}")
            if re.fullmatch(r"[0-9a-f]{64}", value):
                return "$SHA256"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", value):
                return "$DATE_TIME"
            return value
        if isinstance(value, list):
            return [normalize(row) for row in value]
        return {key: normalize(value[key]) for key in sorted(value)}

    selected = {
        key: row
        for key, row in body.items()
        if key not in {"family_id", "scenario_id", "purpose", "frozen_case_sha256"}
    }
    return value_digest(normalize(selected))


def original_json(raw: bytes) -> dict[str, JsonValue]:
    def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate original JSON key")
            result[key] = value
        return result

    def invalid(value: str) -> JsonValue:
        raise ValueError("Nonfinite original JSON: " + value)

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Nonfinite original JSON number")
        return result

    result: JsonValue = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=invalid,
        parse_float=finite_float,
    )
    if not isinstance(result, dict):
        raise ValueError("Complete original INPUT must be a JSON object")
    return result


class TruthDimension(Model):
    provenance: Literal["DETERMINISTIC_AUTHOR_DESCRIPTION", "INDEPENDENT_REPLAY_SPEC"]
    status: Literal["DEFINED", "MISSING"]
    value: JsonValue
    author_original_sha256: Digest
    # An expected truth is a preregistration, not a measured result.
    calculated_from_arm_output: Literal[False] = False

    @model_validator(mode="after")
    def no_placeholder(self) -> Self:
        if self.status == "DEFINED" and self.value is None:
            raise ValueError("A defined truth cannot be a null placeholder")
        if self.status == "MISSING" and self.value is not None:
            raise ValueError("A missing truth must remain null")
        return self


class CaseVariant(Model):
    variant_id: Identifier
    # Preserve the exact whole original, not a fabricated legacy V1 wrapper or subhash.
    native_input_utf8: Annotated[str, Field(min_length=2, max_length=16 * 1024 * 1024)]
    native_input_sha256: Digest
    native_schema_source_sha256: Digest
    native_adapter_status: Literal["NOT_REGISTERED", "PURE_SCHEMA_VALIDATED_NOT_RUN"]
    truth: dict[str, TruthDimension]

    @model_validator(mode="after")
    def whole_original(self) -> Self:
        raw = self.native_input_utf8.encode("utf-8")
        if sha256(raw).hexdigest() != self.native_input_sha256:
            raise ValueError("Whole original INPUT byte SHA differs")
        original_json(raw)
        if set(self.truth) != TRUTH_DIMENSIONS:
            raise ValueError("All original eight truth dimensions must remain registered")
        return self


class CaseFamily(Model):
    family_id: Identifier
    category: Literal[
        "NORMAL",
        "OBLIGATION_CONFLICT",
        "MULTI_GOAL_CONFLICT",
        "SEASONAL_RESERVE",
        "RENT_CHANGE",
        "GOAL_DEFERRAL",
        "CONSUMPTION",
        "T1_RECOVERY",
        "FIXED_LOCK",
        "EARLY_WITHDRAWAL_LOSS",
        "NEW_PAYEE",
        "AMBIGUITY",
        "VERSION_RACE",
        "DUPLICATE_ACTION",
        "UNKNOWN_EXECUTION",
        "AUDIT_TAMPER",
    ]
    split: Literal["DEVELOPMENT", "VALIDATION", "FROZEN"]
    # Authors register the mechanism topology before varying numbers or dates.
    mechanism_topology_sha256: Digest
    author_design_original_sha256: Digest
    variants: Annotated[list[CaseVariant], Field(min_length=2, max_length=100)]

    @model_validator(mode="after")
    def same_family(self) -> Self:
        if len({row.variant_id for row in self.variants}) != len(self.variants):
            raise ValueError("Repeated family variant identity")
        if len({row.native_input_sha256 for row in self.variants}) != len(self.variants):
            raise ValueError("Duplicated original INPUT is not a second variant")
        purpose = "FULL_FAMILY_FROZEN" if self.split == "FROZEN" else "DEVELOPMENT"
        for row in self.variants:
            body = original_json(row.native_input_utf8.encode("utf-8"))
            if body.get("family_id") != self.family_id or body.get("purpose") != purpose:
                raise ValueError("Original whole-family identity/purpose differs")
            if body.get("profile") != "FULL":
                raise ValueError("MVP inputs cannot be relabelled as FULL families")
            if topology_digest(body) != self.mechanism_topology_sha256:
                raise ValueError("Original complete flow differs from registered family topology")
        return self


class FullCaseDesign(Model):
    protocol: Literal["full-case-family-authoring-v1"] = "full-case-family-authoring-v1"
    families: Annotated[list[CaseFamily], Field(max_length=10000)]
    seed_original_sha256: Digest
    source_inventory_sha256: Digest
    independent_oracle_source_sha256: Digest
    status: Literal["NOT_FROZEN_NOT_RUN"] = "NOT_FROZEN_NOT_RUN"
    review_status: Literal["NOT_REVIEWED"] = "NOT_REVIEWED"

    @model_validator(mode="after")
    def no_numeric_leakage(self) -> Self:
        if len({row.family_id for row in self.families}) != len(self.families):
            raise ValueError("A whole family cannot occur in multiple splits")
        topology_splits: dict[str, str] = {}
        all_variants: set[str] = set()
        for family in self.families:
            prior = topology_splits.setdefault(family.mechanism_topology_sha256, family.split)
            if prior != family.split:
                raise ValueError("Numeric/date variants of one topology leaked across splits")
            for row in family.variants:
                if row.native_input_sha256 in all_variants:
                    raise ValueError("Original INPUT appears in more than one family")
                all_variants.add(row.native_input_sha256)
        return self


class DesignReadiness(Model):
    status: Literal["AUTHORING_INCOMPLETE", "READY_FOR_REVIEW_NOT_FROZEN"]
    actual_family_count: int
    actual_variant_count: int
    frozen_family_count: int
    missing_frozen_categories: list[str]
    missing_truth_units: list[str]
    unregistered_native_variants: list[str]
    original_input_sha256s: list[str]
    design_value_sha256: str
    required_arms: tuple[str, ...] = ARMS
    required_ablations: tuple[str, ...] = ABLATIONS
    execution_status: Literal["NOT_RUN"] = "NOT_RUN"
    metrics: dict[str, None]
    reviewer_approval: Literal[None] = None


def inspect_full_case_design(value: FullCaseDesign) -> DesignReadiness:
    """Count supplied originals. This never freezes, creates inputs, or synthesizes truth."""
    missing: list[str] = []
    native: list[str] = []
    originals: list[str] = []
    frozen = [family for family in value.families if family.split == "FROZEN"]
    topologies = {family.mechanism_topology_sha256 for family in frozen}
    categories = sorted(CATEGORIES - {family.category for family in frozen})
    for family in value.families:
        for variant in family.variants:
            originals.append(variant.native_input_sha256)
            if variant.native_adapter_status != "PURE_SCHEMA_VALIDATED_NOT_RUN":
                native.append(f"{family.family_id}/{variant.variant_id}")
            missing += [
                f"{family.family_id}/{variant.variant_id}/{name}"
                for name, truth in variant.truth.items()
                if truth.status == "MISSING"
            ]
    ready = (
        len(frozen) >= 50
        and len(topologies) >= 50
        and not categories
        and not missing
        and not native
    )
    return DesignReadiness(
        status="READY_FOR_REVIEW_NOT_FROZEN" if ready else "AUTHORING_INCOMPLETE",
        actual_family_count=len(value.families),
        actual_variant_count=len(originals),
        frozen_family_count=len(frozen),
        missing_frozen_categories=categories,
        missing_truth_units=missing,
        unregistered_native_variants=native,
        original_input_sha256s=originals,
        design_value_sha256=value_digest(value.model_dump(mode="json")),
        metrics=dict.fromkeys(FULL_METRIC_IDS),
    )
