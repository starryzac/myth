"""Externally reviewed display capabilities; never financial or worker authority.

The trusted operator independently reads raw actual DB/API/simulated-Bank
originals before publishing this artifact and its separate launch pin. JSON
claims do not establish actuality. This reader only preserves that explicit
review, its immutable references, and the exact current consumer inventory.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from app.domain.boundary_types import BoundaryModel
from app.domain.zhiyu_standing_scope import Hash
from app.services.zhiyu_local_actor import ROOT
from app.workers.zhiyu_standing_worker import consumer_source_paths
from pydantic import Field, model_validator

Capability = Literal["JOINT_GOALS", "ASSETS"]
Scenario = Literal[
    "TWO_DIFFERENT_GOALS_UNKNOWN_ORIGINAL_RECOVERY",
    "EIGHT_DIFFERENT_GOALS_V4_ORIGINAL_RECEIPTS",
    "TWO_GOAL_CONFLICT_MINIMUM_REPAIR_NEW_VERSIONS",
    "THREE_TERM_LADDER_MATURITY_REINVESTMENT",
    "EXPLICIT_LOSS_UNKNOWN_ORIGINAL_RECOVERY",
    "CHANGED_QUOTE_REQUIRES_FRESH_USER_CONSENT",
]
REQUIRED_SCENARIOS: dict[Capability, set[str]] = {
    "JOINT_GOALS": {
        "TWO_DIFFERENT_GOALS_UNKNOWN_ORIGINAL_RECOVERY",
        "EIGHT_DIFFERENT_GOALS_V4_ORIGINAL_RECEIPTS",
        "TWO_GOAL_CONFLICT_MINIMUM_REPAIR_NEW_VERSIONS",
    },
    "ASSETS": {
        "THREE_TERM_LADDER_MATURITY_REINVESTMENT",
        "EXPLICIT_LOSS_UNKNOWN_ORIGINAL_RECOVERY",
        "CHANGED_QUOTE_REQUIRES_FRESH_USER_CONSENT",
    },
}
STATUSES: dict[Capability, tuple[str, str]] = {
    "JOINT_GOALS": (
        "DEVELOPMENT_PENDING_ACTUAL_JOINT_ACCEPTANCE",
        "VERIFIED_ACTUAL_JOINT_ACCEPTANCE",
    ),
    "ASSETS": (
        "DEVELOPMENT_PENDING_ACTUAL_ASSET_ACCEPTANCE",
        "VERIFIED_ACTUAL_ASSET_ACCEPTANCE",
    ),
}
MANIFEST_PATH = ".runtime/zhiyu-next-validation/reviewed-capabilities.json"
MAX_BYTES = 2 * 1024 * 1024
SourceMap = Annotated[dict[str, Hash], Field(min_length=1, max_length=20000)]


class ActualOriginalReference(BoundaryModel):
    path: Annotated[str, Field(min_length=1, max_length=500)]
    sha256: Hash
    terminal_status: Literal["PASSED", "PASSED_RETAINED_ORIGINAL_JOINT_CHAIN"]


class ReviewReference(BoundaryModel):
    path: Annotated[str, Field(min_length=1, max_length=500)]
    sha256: Hash


class ScenarioReview(BoundaryModel):
    scenario: Scenario
    command: ActualOriginalReference
    actual_originals: Annotated[list[ActualOriginalReference], Field(min_length=1, max_length=2)]
    independent_review: ReviewReference


class RootActualReview(BoundaryModel):
    protocol: Literal["zhiyu-next-root-capability-actual-review-v1"]
    status: Literal["VERIFIED_RAW_ACTUAL_ORIGINALS"]
    capability: Capability
    scenario: Scenario
    validation_kind: Literal["ACTUAL_OWNED_POSTGRES_API_SIMULATED_BANK"]
    command: ActualOriginalReference
    actual_originals: Annotated[list[ActualOriginalReference], Field(min_length=1, max_length=2)]
    current_consumer_sha256: SourceMap
    native_originals_independently_verified: Literal[True]
    physical_hashes_and_counts_independently_verified: Literal[True]
    historical_to_current_source_compatibility_independently_reviewed: Literal[True]
    real_bank: Literal[False]
    worker_permit_issued: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def exact_booleans(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            value.get(key) is not expected
            for key, expected in {
                "native_originals_independently_verified": True,
                "physical_hashes_and_counts_independently_verified": True,
                "historical_to_current_source_compatibility_independently_reviewed": True,
                "real_bank": False,
                "worker_permit_issued": False,
            }.items()
        ):
            raise ValueError("Review requires exact explicit booleans")
        return value


class CapabilityReview(BoundaryModel):
    capability: Capability
    reviews: Annotated[list[ScenarioReview], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def complete_scenarios(self) -> Self:
        if {review.scenario for review in self.reviews} != REQUIRED_SCENARIOS[self.capability]:
            raise ValueError("Every distinct actual capability scenario is required")
        return self


class ReviewedCapabilities(BoundaryModel):
    protocol: Literal["zhiyu-next-root-reviewed-capabilities-v1"]
    status: Literal["ROOT_INDEPENDENT_REVIEWED_ACTUAL"]
    scope: Literal["DISPLAY_ONLY_NOT_FINANCIAL_AUTHORITY"]
    financial_authority: Literal[False]
    worker_permit_issued: Literal[False]
    real_bank: Literal[False]
    current_consumer_sha256: SourceMap
    capabilities: Annotated[dict[Capability, CapabilityReview], Field(min_length=1, max_length=2)]

    @model_validator(mode="before")
    @classmethod
    def no_authority(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            value.get(key) is not False
            for key in ("financial_authority", "worker_permit_issued", "real_bank")
        ):
            raise ValueError("Display metadata has no authority or real Bank")
        return value

    @model_validator(mode="after")
    def exact_capability(self) -> Self:
        if any(name != review.capability for name, review in self.capabilities.items()):
            raise ValueError("Capability key and reviewed scenarios differ")
        return self


def _closed_path(root: Path, relative: str) -> Path:
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or Path(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("Only canonical checkout-relative originals are accepted")
    path = root / relative
    if (
        not path.is_file()
        or path.is_symlink()
        or path.is_junction()
        or path.resolve() != path.absolute()
        or not path.resolve().is_relative_to(root)
    ):
        raise ValueError("Original source or evidence escapes or redirects the checkout")
    return path


def _json(raw: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate review JSON member")
            result[key] = value
        return result

    def forbidden(value: str) -> Any:
        raise ValueError("Nonfinite review JSON is forbidden")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=forbidden)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid bounded review JSON") from error
    if not isinstance(result, dict):
        raise ValueError("Review must be a JSON object")
    return result


def _read_original(
    root: Path, reference: ReviewReference, *, native_round: bool = False
) -> dict[str, Any]:
    owned_round = native_round and re.fullmatch(
        r"\.runtime/zhiyu-next/\d{8}T\d{6}Z-[0-9a-f]{32}/x02-strategy-test\.json",
        reference.path,
    ) is not None
    if not reference.path.startswith(".runtime/zhiyu-next-validation/") and not owned_round:
        raise ValueError("Actual review originals require the declared validation directory")
    path = _closed_path(root, reference.path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Review original exceeds bounded capacity")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != reference.sha256:
        raise ValueError("Externally pinned actual review original changed")
    return _json(raw)


def require_reviewed_capability(
    capability: Capability,
    *,
    root: Path = ROOT,
    reviewed_manifest_sha256: str | None,
) -> CapabilityReview:
    """File/source-only check of an explicit independent operator review."""
    if (
        not isinstance(reviewed_manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", reviewed_manifest_sha256) is None
    ):
        raise ValueError("Explicit independently reviewed capability pin required")
    if root.absolute() != root.resolve() or root.is_symlink() or root.is_junction():
        raise ValueError("Exact nonredirected checkout required")
    data = _read_original(
        root, ReviewReference(path=MANIFEST_PATH, sha256=reviewed_manifest_sha256)
    )
    manifest = ReviewedCapabilities.model_validate_json(json.dumps(data))
    if set(manifest.current_consumer_sha256) != consumer_source_paths(root):
        raise ValueError("Exact complete current consumer roster is required")
    for relative, expected in manifest.current_consumer_sha256.items():
        source = _closed_path(root, relative)
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("A current consumer changed after the independent review")
    if capability not in manifest.capabilities:
        raise ValueError("Capability has no actual independent review")
    selected = manifest.capabilities[capability]
    for scenario in selected.reviews:
        review = RootActualReview.model_validate_json(
            json.dumps(_read_original(root, scenario.independent_review))
        )
        if (
            review.capability != capability
            or review.scenario != scenario.scenario
            or review.command != scenario.command
            or review.actual_originals != scenario.actual_originals
            or review.current_consumer_sha256 != manifest.current_consumer_sha256
        ):
            raise ValueError("Independent review, source and actual original references differ")
        for original in [scenario.command, *scenario.actual_originals]:
            actual = _read_original(
                root,
                ReviewReference(path=original.path, sha256=original.sha256),
                native_round=True,
            )
            if actual.get("status") != original.terminal_status:
                raise ValueError("Actual original has no matching genuine terminal status")
    return selected


def capability_validation_status(capability: Capability) -> str:
    """Closed by default; display metadata grants no financial permission."""
    try:
        require_reviewed_capability(
            capability,
            root=ROOT,
            reviewed_manifest_sha256=os.environ.get("ZHIYU_NEXT_REVIEWED_CAPABILITIES_SHA256"),
        )
    except (ValueError, OSError):
        return STATUSES[capability][0]
    return STATUSES[capability][1]
