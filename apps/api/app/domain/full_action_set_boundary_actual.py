"""Actual physical inventory v2; old global v1 calculation contracts stay unchanged."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_action_set_asset_producers import (
    AssetProducerInput,
    asset_producer_view,
    expected_asset_producers,
)
from app.domain.full_action_set_boundary import (
    ActionSetInput,
    CandidateInput,
    CandidateView,
    GlobalBoundaryObserveRequest,
    Hash,
    _original_candidate_binding,
    candidate_view,
    unsupported_producers,
)
from app.domain.full_action_set_boundary_full import (
    DynamicGoalProducerInput,
    dynamic_candidate,
    dynamic_model_inventory,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from pydantic import Field, StrictBool, StrictInt

ALGORITHM: Literal["full-policy-action-set-boundary-actual-v2"] = (
    "full-policy-action-set-boundary-actual-v2"
)
SCOPE: Literal["POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2"] = (
    "POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2"
)
NAMESPACE = UUID("02747b78-37e7-5e3d-80a4-f4cf951d2042")
MAX_ROWS = 4096
MAX_CANDIDATES = 64
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
REQUIRED_TABLES = frozenset(
    {
        "users",
        "accounts",
        "policies",
        "policy_versions",
        "goals",
        "asset_positions",
        "asset_products",
        "action_plans",
        "action_receipts",
        "bank_operations",
        "simulated_bank_postings",
        "simulated_bank_redemptions",
        "external_bank_facts",
        "transactions",
        "credit_card_bills",
        "action_resource_reservations",
        "evidence_items",
        "full_policies",
        "full_policy_versions",
        "full_policy_commands",
        "product_catalog_versions",
        "full_asset_execution_portfolios",
        "full_asset_execution_batches",
        "full_asset_execution_consents",
    }
)
AUXILIARY_TABLES = frozenset({"command_outbox", "command_inbox", "command_delivery_attempts"})
GLOBAL_TABLES = frozenset({"asset_products", "product_catalog_versions"})


class ActualTableCoverage(BoundaryModel):
    table: str
    actual_count: Annotated[StrictInt, Field(ge=0)] | None
    captured_count: Annotated[StrictInt, Field(ge=0)]
    complete: StrictBool
    rows_hash: Hash


class ActualActionSetInput(BoundaryModel):
    protocol: Literal["actual-action-set-input-v2"] = "actual-action-set-input-v2"
    base: ActionSetInput
    table_coverage: list[ActualTableCoverage]
    dynamic_goals: list[DynamicGoalProducerInput]
    assets: list[AssetProducerInput]


class ActualActionSetSnapshot(BoundaryModel):
    algorithm_version: Literal["full-policy-action-set-boundary-actual-v2"] = ALGORITHM
    scope: Literal["POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_V2"] = SCOPE
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    arbitrary_manual_intents_covered: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE", "UNKNOWN"]
    global_action_set_complete: StrictBool
    original_inventory_hash: Hash
    financial_input_hash: Hash
    input_hash: Hash
    snapshot_hash: Hash
    action_set_signature: Hash | None
    expected_candidate_keys: list[str]
    candidates: list[CandidateView]
    dynamic_candidate_keys: list[str]
    asset_candidate_keys: list[str]
    table_coverage: list[ActualTableCoverage]
    unsupported_producers: list[str]
    reasons: list[str]


class ActualGlobalBoundaryObservation(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    observation_run_id: UUID
    previous_observation_run_id: UUID | None
    original_request: GlobalBoundaryObserveRequest
    request_hash: Hash
    snapshot: ActualActionSetSnapshot
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] | None
    semantic_key: Hash | None
    requires_user_attention: bool
    previous_snapshot_hash: Hash | None
    previous_action_set_signature: Hash | None
    global_action_set_complete: StrictBool
    idempotent_replay: bool = False
    notification_support: Literal["ROOT_ACTUAL_V2_SOURCE_BRANCH_REQUIRED"] = (
        "ROOT_ACTUAL_V2_SOURCE_BRANCH_REQUIRED"
    )


def actual_producer_keys(inventory: dict[str, list[dict[str, Any]]]) -> list[str]:
    versions: dict[str, dict[str, Any]] = {}
    for raw_version in inventory["policy_versions"]:
        if (
            raw_version["policy_id"] not in versions
            or raw_version["version_number"] > versions[raw_version["policy_id"]]["version_number"]
        ):
            versions[raw_version["policy_id"]] = raw_version
    keys = []
    for policy in inventory["policies"]:
        version = versions.get(policy["id"])
        if version is None:
            keys.append("policy:" + policy["id"])
            continue
        kind = validate_configuration(version["configuration"])["type"]
        if kind in {"recurring_obligation", "asset_authorization"}:
            keys.append(
                ("payment:" if kind == "recurring_obligation" else "purchase:") + policy["id"]
            )
        elif kind == "goal_saving":
            goals = [row for row in inventory["goals"] if row["policy_id"] == policy["id"]]
            keys.extend("goal:" + row["id"] for row in goals)
            if not goals:
                keys.append("goal-policy:" + policy["id"])
        elif kind not in {"living_reserve", "emergency_buffer"}:
            keys.append("unsupported-policy:" + policy["id"])
    return sorted(keys + ["redemption:" + row["id"] for row in inventory["asset_positions"]])


def actual_inactive_keys(inventory: dict[str, list[dict[str, Any]]], now: datetime) -> set[str]:
    keys = {
        "redemption:" + row["id"]
        for row in inventory["asset_positions"]
        if row.get("status") == "REDEEMED" or row.get("policy_version_id") is None
    }
    for policy in inventory["policies"]:
        versions = [row for row in inventory["policy_versions"] if row["policy_id"] == policy["id"]]
        denied = policy["status"] in {"PROPOSED", "SUSPENDED", "REVOKED", "EXPIRED"}
        if versions:
            latest = max(versions, key=lambda row: row["version_number"])
            for name, future in (("valid_from", True), ("valid_until", False)):
                if latest.get(name) is not None:
                    clock = datetime.fromisoformat(latest[name])
                    if clock.tzinfo is None or clock.utcoffset() is None:
                        raise ValueError("Actual policy window is not aware")
                    denied = denied or (now < clock if future else now >= clock)
        if denied:
            keys.update(
                key for key in actual_producer_keys(inventory) if key.endswith(":" + policy["id"])
            )
            keys.update(
                "goal:" + row["id"]
                for row in inventory["goals"]
                if row["policy_id"] == policy["id"]
            )
    return keys


def _unknown(key: str, reason: str) -> CandidateView:
    return CandidateView(
        candidate_key=key,
        state="UNKNOWN",
        action_type=None,
        amount_cents=None,
        autonomy_level=None,
        signature=None,
        reasons=[reason],
    )


def _structure(data: ActualActionSetInput) -> list[str]:
    base = data.base
    reasons = [*base.inventory_reasons, *base.source_reasons]
    if (
        configuration_hash(base.financial_basis) != base.financial_input_hash
        or base.financial_basis.get("user_id") != str(base.user_id)
        or base.financial_basis.get("as_of") != base.as_of.isoformat()
    ):
        reasons.append("ACTUAL_FINANCIAL_BASIS_BINDING_DIFFERS")
    if set(base.original_inventory) - (REQUIRED_TABLES | AUXILIARY_TABLES):
        reasons.append("ACTUAL_UNREGISTERED_PHYSICAL_TABLE")
    if not REQUIRED_TABLES.issubset(base.original_inventory):
        reasons.append("ACTUAL_REQUIRED_PHYSICAL_TABLES_MISSING")
    coverage = {row.table: row for row in data.table_coverage}
    if len(coverage) != len(data.table_coverage) or set(coverage) != set(base.original_inventory):
        reasons.append("ACTUAL_COMPLETE_TABLE_DENOMINATOR_DIFFERS")
    for table, rows in base.original_inventory.items():
        proof = coverage.get(table)
        if (
            proof is None
            or not proof.complete
            or proof.actual_count != len(rows)
            or proof.captured_count != len(rows)
            or proof.rows_hash != configuration_hash({"rows": rows})
            or len(rows) > MAX_ROWS
        ):
            reasons.append("ACTUAL_TABLE_NOT_COMPLETE:" + table)
        if len(rows) != len({row.get("id") for row in rows}):
            reasons.append("ACTUAL_DUPLICATE_ORIGINAL_ROW:" + table)
        for row in rows:
            try:
                if str(UUID(row["id"])) != row["id"]:
                    raise ValueError("noncanonical ID")
            except (KeyError, ValueError, TypeError, AttributeError):
                reasons.append("ACTUAL_ORIGINAL_ROW_ID_INVALID:" + table)
            if (
                table not in GLOBAL_TABLES | {"users"}
                and row.get("user_id") != str(base.user_id)
                or "user_id" in row
                and row["user_id"] != str(base.user_id)
            ):
                reasons.append("ACTUAL_ORIGINAL_OWNER_DIFFERS:" + table)
    users = base.original_inventory.get("users", [])
    if (
        len(users) != 1
        or users[0].get("id") != str(base.user_id)
        or users[0].get("is_simulated") is not True
    ):
        reasons.append("ACTUAL_SIMULATED_OWNER_NOT_VERIFIED")
    if base.as_of.tzinfo is None or base.as_of.utcoffset() is None:
        reasons.append("ACTUAL_BUSINESS_CLOCK_NOT_AWARE")
    if not base.audit_verified:
        reasons.append("ACTUAL_COMPLETE_AUDIT_NOT_VERIFIED")
    if len(data.model_dump_json().encode("utf8")) > MAX_CAPTURE_BYTES:
        reasons.append("ACTUAL_CAPTURE_CAPACITY_EXCEEDED")
    return reasons


def derive_actual_action_set(data: ActualActionSetInput) -> ActualActionSetSnapshot:
    data = ActualActionSetInput.model_validate(data.model_dump())
    base, inventory = data.base, data.base.original_inventory
    reasons = _structure(data)
    views: dict[str, CandidateView] = {}
    try:
        expected, inactive = (
            actual_producer_keys(inventory),
            actual_inactive_keys(inventory, base.as_of),
        )
        dynamics, assets = dynamic_model_inventory(base), expected_asset_producers(base)
        actual_unsupported = unsupported_producers(inventory, base.epoch_id)
    except (ValueError, KeyError, TypeError) as error:
        expected, inactive, dynamics, assets, actual_unsupported = (
            [],
            set(),
            {},
            {},
            list(base.unsupported_producers),
        )
        reasons.append("ACTUAL_PRODUCER_INVENTORY_INVALID:" + str(error))
    if (
        sorted(base.expected_candidate_keys) != expected
        or len(base.candidates) != len(expected)
        or {row.candidate_key for row in base.candidates} != set(expected)
    ):
        reasons.append("ACTUAL_ORIGINAL_PRODUCER_DENOMINATOR_DIFFERS")
    if sorted(base.unsupported_producers) != actual_unsupported:
        reasons.append("ACTUAL_UNSUPPORTED_FAMILY_DENOMINATOR_DIFFERS")
    dynamic_keys = [row.candidate_key for row in data.dynamic_goals]
    if len(dynamic_keys) != len(set(dynamic_keys)) or set(dynamic_keys) != set(dynamics):
        reasons.append("ACTUAL_DYNAMIC_PRODUCER_DENOMINATOR_DIFFERS")
    shadows: dict[str, CandidateInput] = {}
    handled: set[str] = set()
    for producer in data.dynamic_goals:
        if (
            sorted(producer.model_evidence_ids) != dynamics.get(producer.candidate_key)
            or producer.candidate_key not in expected
        ):
            views[producer.candidate_key] = _unknown(
                producer.candidate_key, "ACTUAL_DYNAMIC_MODEL_DENOMINATOR_DIFFERS"
            )
            continue
        view, shadow = dynamic_candidate(base, producer, actual_v2_native_values=True)
        views[producer.candidate_key] = view
        if shadow is not None:
            shadows[producer.candidate_key] = shadow
        if view.state != "UNKNOWN":
            handled.add("DYNAMIC_GOAL_PRODUCER_ADAPTER_MISSING:" + producer.candidate_key[5:])
    evidence = {row["id"]: row for row in inventory.get("evidence_items", [])}
    for original in base.candidates:
        candidate = shadows.get(original.candidate_key, original)
        if candidate.excluded_by_current_policy and candidate.candidate_key not in inactive:
            views[candidate.candidate_key] = _unknown(
                candidate.candidate_key, "ACTUAL_CURRENT_POLICY_DENIAL_NOT_PROVEN"
            )
            continue
        if candidate.facts is not None:
            if not _original_candidate_binding(base, candidate):
                reasons.append(
                    "ACTUAL_ORIGINAL_CANDIDATE_BINDING_DIFFERS:" + candidate.candidate_key
                )
            for identity in {
                *candidate.facts.source_evidence_ids,
                *candidate.facts.authority.evidence_ids,
                *candidate.facts.payee.evidence_ids,
            }:
                raw = evidence.get(str(identity))
                if (
                    raw is None
                    or raw.get("status") != "VALID"
                    or raw.get("content_hash") != configuration_hash(raw.get("content", {}))
                ):
                    reasons.append("ACTUAL_REQUIRED_SOURCE_NOT_VERIFIED:" + str(identity))
        if candidate.candidate_key not in views:
            views[candidate.candidate_key] = candidate_view(candidate, base.user_id, base.as_of)
    asset_keys = [row.candidate_key for row in data.assets]
    if len(asset_keys) != len(set(asset_keys)) or set(asset_keys) != set(assets):
        reasons.append("ACTUAL_WHOLE_ASSET_PRODUCER_DENOMINATOR_DIFFERS")
    full_asset_results: dict[UUID, list[CandidateView]] = {}
    for asset_producer in data.assets:
        binding = assets.get(asset_producer.candidate_key)
        if (
            binding is None
            or binding[0] != asset_producer.full_policy_id
            or asset_producer.request is not None
            and (
                binding[1] != asset_producer.request.mvp_asset_policy_id
                or binding[2] != asset_producer.request.planning_mode
            )
        ):
            view = _unknown(
                asset_producer.candidate_key, "ACTUAL_WHOLE_ASSET_ORIGINAL_BINDING_DIFFERS"
            )
        else:
            view = asset_producer_view(base, asset_producer)
        views[asset_producer.candidate_key] = view
        full_asset_results.setdefault(asset_producer.full_policy_id, []).append(view)
    for identity, results in full_asset_results.items():
        original_keys = {key for key, binding in assets.items() if binding[0] == identity}
        if {row.candidate_key for row in results} == original_keys and all(
            row.state != "UNKNOWN" for row in results
        ):
            handled.add("FULL_PRODUCER_ADAPTER_MISSING:AssetAuthorizationPolicy:" + str(identity))
    unsupported = sorted(set(actual_unsupported) - handled)
    if unsupported:
        reasons.append("ACTUAL_UNSUPPORTED_CURRENT_PRODUCERS")
    if any(row.state == "UNKNOWN" for row in views.values()):
        reasons.append("ACTUAL_CURRENT_PRODUCER_UNKNOWN")
    if len(views) > MAX_CANDIDATES:
        reasons.append("ACTUAL_PRODUCER_CAPACITY_EXCEEDED")
    reasons = sorted(set(reasons))
    fields = {
        "user_id": base.user_id,
        "epoch_id": base.epoch_id,
        "as_of": base.as_of,
        "status": "UNKNOWN" if reasons else "COMPLETE",
        "global_action_set_complete": not reasons,
        "original_inventory_hash": configuration_hash(inventory),
        "financial_input_hash": base.financial_input_hash,
        "input_hash": configuration_hash(data.model_dump(mode="json")),
        "action_set_signature": None
        if reasons
        else configuration_hash(
            {
                "scope": SCOPE,
                "members": sorted(
                    {row.signature for row in views.values() if row.signature is not None}
                ),
            }
        ),
        "expected_candidate_keys": sorted(set(expected) | set(assets)),
        "candidates": sorted(views.values(), key=lambda row: row.candidate_key),
        "dynamic_candidate_keys": sorted(dynamics),
        "asset_candidate_keys": sorted(assets),
        "table_coverage": data.table_coverage,
        "unsupported_producers": unsupported,
        "reasons": reasons,
        "snapshot_hash": "0" * 64,
    }
    snapshot = ActualActionSetSnapshot.model_validate(fields)
    return snapshot.model_copy(
        update={
            "snapshot_hash": configuration_hash(
                snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
            )
        }
    )


def compare_actual_action_sets(
    before: ActualActionSetSnapshot | None, after: ActualActionSetSnapshot
) -> tuple[Literal["BoundaryCrossed", "BoundaryObserved"] | None, str | None]:
    if not after.global_action_set_complete or after.action_set_signature is None:
        return None, None
    if before is None:
        return "BoundaryObserved", None
    if (
        not before.global_action_set_complete
        or before.action_set_signature is None
        or (before.user_id, before.epoch_id, before.scope, before.algorithm_version)
        != (after.user_id, after.epoch_id, after.scope, after.algorithm_version)
        or before.as_of > after.as_of
    ):
        return None, None
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] = (
        "BoundaryObserved"
        if before.action_set_signature == after.action_set_signature
        else "BoundaryCrossed"
    )
    return kind, configuration_hash(
        {
            "user_id": str(after.user_id),
            "epoch_id": str(after.epoch_id),
            "scope": SCOPE,
            "before": before.action_set_signature,
            "after": after.action_set_signature,
        }
    )
