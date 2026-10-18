"""Closed accepted whole asset view; pure normalization never grants authority."""

import json
from copy import deepcopy
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.policy_configuration import configuration_hash
from app.domain.zhiyu_standing_journal import StandingRun
from pydantic import Field, StrictInt, model_validator

PROTOCOL: Literal["standing-owned-asset-read-v1"] = "standing-owned-asset-read-v1"
MARKER = "zhiyu_standing_delegated"
ACTION_FIELDS = (
    "id user_id decision_run_id policy_version_id source_account_id "
    "destination_account_id goal_id product_id position_id action_type amount_cents "
    "status idempotency_key request request_hash authorized_at expires_at created_at"
).split()
CLAIM_FIELDS = (
    "id user_id action_plan_id resource_kind resource_key amount_cents status "
    "resolved_at created_at"
).split()


class OwnedAssetSlot(BoundaryModel):
    action_id: UUID
    state: Literal["ABSENT", "SETTLED_PREFIX", "PLANNED_UNRESERVED", "EXECUTION_RESERVED"]
    action_original: dict[str, Any] | None
    resource_originals: list[dict[str, Any]]


class AcceptedAssetReadProof(BoundaryModel):
    protocol: Literal["standing-owned-asset-read-v1"] = PROTOCOL
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    accepted_evidence_id: UUID
    accepted_evidence_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted_native_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    current_run: StandingRun
    effects: Annotated[list[ExecutionEffect], Field(min_length=1, max_length=4)]
    settled_prefix: Annotated[StrictInt, Field(ge=0, le=4)]
    slots: Annotated[list[OwnedAssetSlot], Field(min_length=1, max_length=4)]
    exposure_evidence_id: UUID
    exposure_evidence_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    exposure_original: dict[str, Any]
    # Full physical originals for exact receipt-derived source comparison.
    # These never replace current context amounts or grant financial authority.
    prefix_originals: Annotated[list[dict[str, Any]], Field(max_length=4)] = Field(
        default_factory=list
    )
    account_originals: Annotated[list[dict[str, Any]], Field(max_length=100)] = Field(
        default_factory=list
    )
    position_originals: Annotated[list[dict[str, Any]], Field(max_length=10000)] = Field(
        default_factory=list
    )
    evidence_originals: Annotated[list[dict[str, Any]], Field(max_length=10000)] = Field(
        default_factory=list
    )
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def original_inventory(self) -> Self:
        validate_read_proof(self)
        return self


def manifest_rows(content: dict[str, Any], name: str) -> dict[str, str]:
    rows = content.get(name)
    if type(rows) is not list:
        raise ValueError("Complete native exposure manifest required")
    found: dict[str, str] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"id", "digest"}:
            raise ValueError("Closed native manifest record required")
        if type(row["id"]) is not str or type(row["digest"]) is not str or row["id"] in found:
            raise ValueError("Unique native exposure manifest identity required")
        found[row["id"]] = row["digest"]
    return found


def row_digest(row: dict[str, Any], fields: list[str]) -> str:
    if not set(fields) <= set(row):
        raise ValueError("Complete physical original fields required")
    return configuration_hash({name: row[name] for name in fields})


def validate_read_proof(proof: AcceptedAssetReadProof) -> None:
    effects, run = proof.effects, proof.current_run
    if (
        proof.as_of.tzinfo is None
        or proof.as_of.utcoffset() is None
        or (run.source.user_id, run.source.epoch_id) != (proof.user_id, proof.epoch_id)
        or run.state not in {"ACCEPTED", "UNKNOWN"}
        or run.whole_plan_hash != proof.accepted_native_hash
        or run.original_action_ids != [effect.operation_id for effect in effects]
        or run.original_effect_hashes != [execution_effect_hash(effect) for effect in effects]
        or run.accepted_total_cents != sum(effect.amount_cents for effect in effects)
        or [slot.action_id for slot in proof.slots] != run.original_action_ids
        or len(proof.slots) != len(effects)
        or not 0 <= proof.settled_prefix < len(effects)
        or len(run.original_receipt_ids) > proof.settled_prefix
    ):
        raise ValueError("Exact original accepted whole/run/ordered prefix required")
    scope_goal = effects[0].goal_id
    if scope_goal is None or any(
        effect.user_id != proof.user_id
        or effect.action_type != "PURCHASE_ASSET"
        or effect.goal_id != scope_goal
        or effect.income_uses
        or effect.policy_id != effects[0].policy_id
        or effect.policy_version_ids != effects[0].policy_version_ids
        for effect in effects
    ):
        raise ValueError("This closed read scope supports original goal-owned purchases only")
    raw = proof.exposure_original
    if (
        raw.get("user_id") != str(proof.user_id)
        or raw.get("simulation") is not True
        or raw.get("complete") is not True
        or raw.get("protocol") not in {"asset-exposure-v3", "asset-exposure-v4"}
        or configuration_hash(raw) != proof.exposure_evidence_hash
    ):
        raise ValueError("Complete independently verified current exposure original required")
    actions = manifest_rows(raw, "actions")
    reservations = manifest_rows(raw, "resource_reservations")
    declarations = raw.get("settlements")
    if type(declarations) is not list:
        raise ValueError("Complete current native settlement denominator required")
    for number, (effect, slot) in enumerate(zip(effects, proof.slots, strict=True), 1):
        expected_key = "standing:" + run.source.event_key + ":" + str(number)
        if run.original_bank_keys[number - 1] != expected_key:
            raise ValueError("Original event cannot create a replacement bank key")
        if slot.state == "ABSENT":
            if (
                number <= proof.settled_prefix
                or slot.action_original is not None
                or slot.resource_originals
                or str(slot.action_id) in actions
            ):
                raise ValueError("Absent original action cannot hide claims or settled prefix")
            continue
        row = slot.action_original
        if row is None or actions.get(str(slot.action_id)) != row_digest(row, ACTION_FIELDS):
            raise ValueError("Current complete exposure must anchor the exact own action")
        command = BankCommand.model_validate_json(json.dumps(row["request"]["execution"]))
        marker = row["request"].get(MARKER)
        if (
            command.effect != effect
            or command.effect_hash != execution_effect_hash(effect)
            or row["request_hash"] != configuration_hash(row["request"])
            or row["id"] != str(slot.action_id)
            or row["user_id"] != str(proof.user_id)
            or row["autonomy_level"] != "AUTO_EXECUTE"
            or type(marker) is not dict
            or set(marker)
            != {
                "protocol",
                "event_key",
                "grant_id",
                "whole_plan_hash",
                "accepted_proof_evidence_id",
                "accepted_proof_evidence_hash",
                "child_number",
                "bank_key",
            }
            or marker
            != {
                "protocol": "zhiyu-standing-fixed-action-binding-v2",
                "event_key": run.source.event_key,
                "grant_id": str(run.grant_id),
                "whole_plan_hash": proof.accepted_native_hash,
                "accepted_proof_evidence_id": str(proof.accepted_evidence_id),
                "accepted_proof_evidence_hash": proof.accepted_evidence_hash,
                "child_number": number,
                "bank_key": expected_key,
            }
        ):
            raise ValueError(
                "Exact current SYSTEM-v2 action/request/effect/accepted proof required"
            )
        owns = [item for item in declarations if item.get("action_id") == str(slot.action_id)]
        if len(owns) != 1 or owns[0].get("state") != (
            "MATERIALIZED" if slot.state == "SETTLED_PREFIX" else slot.state
        ):
            raise ValueError("One exact native declaration for every own physical action required")
        if slot.state == "SETTLED_PREFIX":
            if number > proof.settled_prefix or row["status"] not in {"SUCCEEDED", "RECONCILED"}:
                raise ValueError("Settled own position must remain in the actual prefix")
            for claim in slot.resource_originals:
                if (
                    claim["action_plan_id"] != str(effect.operation_id)
                    or claim["user_id"] != str(proof.user_id)
                    or claim["status"] != "CONSUMED"
                    or claim["resolved_at"] is None
                    or reservations.get(claim["id"]) != row_digest(claim, CLAIM_FIELDS)
                ):
                    raise ValueError("Settled own resources retain their actual consumed originals")
            continue
        if number != proof.settled_prefix + 1:
            raise ValueError("Only the exact next original child may have pending physical state")
        expected_claims = {
            ("CASH", str(use.account_id)): use.amount_cents for use in effect.cash_uses
        } | {
            ("BUSINESS", effect.business_key): 1,
            ("MANAGED", "goal:" + str(effect.goal_id)): effect.amount_cents,
            ("GOAL_CASH", str(effect.goal_id)): effect.amount_cents,
        }
        actual: dict[tuple[str, str], int] = {}
        for claim in slot.resource_originals:
            key = (claim["resource_kind"], claim["resource_key"])
            if (
                key in actual
                or type(claim["amount_cents"]) is not int
                or claim["amount_cents"] <= 0
                or claim["status"] != "RESERVED"
                or claim["resolved_at"] is not None
                or claim["user_id"] != str(proof.user_id)
                or claim["action_plan_id"] != str(slot.action_id)
                or claim["id"] != str(uuid5(slot.action_id, f"resource:{key[0]}:{key[1]}"))
                or reservations.get(claim["id"]) != row_digest(claim, CLAIM_FIELDS)
            ):
                raise ValueError("Only exact current native own claims may be excluded")
            actual[key] = claim["amount_cents"]
        if slot.state == "PLANNED_UNRESERVED":
            if actual or row["status"] not in {"PLANNED", "AUTHORIZED"}:
                raise ValueError("Unreserved original removes no money or exposure")
        elif actual != expected_claims or row["status"] not in {"SUBMITTED", "UNKNOWN"}:
            raise ValueError("Complete original pending purchase claim denominator differs")


def pending_ids(proof: AcceptedAssetReadProof) -> set[UUID]:
    validate_read_proof(proof)
    return {slot.action_id for slot in proof.slots if slot.state == "EXECUTION_RESERVED"}


def normalize_exposures(
    proof: AcceptedAssetReadProof, original: list[AssetExposure]
) -> list[AssetExposure]:
    """Subtract only owned pending; keep every settled position and other action."""
    owned = pending_ids(proof)
    cash: dict[UUID, int] = {}
    goals: dict[UUID, int] = {}
    amounts: dict[UUID, int] = {}
    for slot, effect in zip(proof.slots, proof.effects, strict=True):
        if slot.action_id not in owned:
            continue
        for use in effect.cash_uses:
            cash[use.account_id] = cash.get(use.account_id, 0) + use.amount_cents
        assert effect.goal_id is not None
        goals[effect.goal_id] = goals.get(effect.goal_id, 0) + effect.amount_cents
        amounts[effect.operation_id] = effect.amount_cents
    if not original or len({item.goal_id for item in original}) != len(original):
        raise ValueError("Complete unique native scope denominator required")
    output = []
    counted = {identity for item in original for identity in item.counted_action_ids}
    if not owned <= counted:
        raise ValueError("Current owned purchase missing from raw complete exposure")
    for exposure in original:
        if exposure.as_of > proof.as_of:
            raise ValueError("Future native exposure is not a current original")
        reduced_cash = dict(exposure.reserved_cash_by_account)
        reduced_goals = dict(exposure.reserved_goal_cash_by_goal)
        for current, deduction in ((reduced_cash, cash), (reduced_goals, goals)):
            for identity, amount in deduction.items():
                if current.get(identity, 0) < amount:
                    raise ValueError("Own claims exceed current verified full exposure")
                current[identity] -= amount
        own_pending = sum(amounts.get(identity, 0) for identity in exposure.counted_action_ids)
        if own_pending > exposure.pending_purchase_cents:
            raise ValueError("Own pending exceeds native aggregate original")
        output.append(
            AssetExposure.model_validate_json(
                exposure.model_copy(
                    update={
                        "reserved_cash_by_account": reduced_cash,
                        "reserved_goal_cash_by_goal": reduced_goals,
                        "pending_purchase_cents": exposure.pending_purchase_cents - own_pending,
                        "counted_action_ids": [
                            identity
                            for identity in exposure.counted_action_ids
                            if identity not in owned
                        ],
                    }
                ).model_dump_json()
            )
        )
    return output


def remove_unsettled_manifest(
    proof: AcceptedAssetReadProof, original: dict[str, Any]
) -> dict[str, Any]:
    """Only prefix-zero physical source equivalence; settled economics never vanish."""
    validate_read_proof(proof)
    if proof.settled_prefix:
        raise ValueError("Settled source needs separate exact receipt transition proof")
    value = deepcopy(original)
    own = {str(slot.action_id) for slot in proof.slots if slot.action_original is not None}
    claims = {row["id"] for slot in proof.slots for row in slot.resource_originals}
    value["actions"] = [row for row in value["actions"] if row["id"] not in own]
    value["resource_reservations"] = [
        row for row in value["resource_reservations"] if row["id"] not in claims
    ]
    value["settlements"] = [row for row in value["settlements"] if row["action_id"] not in own]
    value.pop("as_of")
    return value


def owned_source_equivalent(
    proof: AcceptedAssetReadProof, before: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Prove only own unbanked source substitution; unsupported changes stay stale.

    Both complete current worlds are separately checked by the caller. This
    function never applies a historical answer, changes its original receipt,
    hides another pending action, or normalizes a settled position away.
    """
    try:
        left, right = deepcopy(before), deepcopy(current)
        fields = {
            "protocol",
            "user_id",
            "epoch_id",
            "shanghai_day",
            "native_source_fingerprint",
            "original_sources",
            "original_policies",
            "full_policy",
            "catalogue",
            "full_protection_sources",
            "current_world_signatures",
            "current_world_status",
            "complete_native_basis",
        }
        if "source_fingerprint_version" in left or "source_fingerprint_version" in right:
            fields.add("source_fingerprint_version")
            if any(manifest.get("source_fingerprint_version") != "full-asset-mode-source-clock-v2"
                   for manifest in (left, right)):
                return False
        if set(left) != fields or set(right) != fields or proof.settled_prefix:
            return False
        old = [
            row
            for row in left["original_sources"]
            if row["source_type"] == "SIMULATED_ASSET_EXPOSURE"
        ]
        new = [
            row
            for row in right["original_sources"]
            if row["source_type"] == "SIMULATED_ASSET_EXPOSURE"
        ]
        if len(old) != 1 or len(new) != 1:
            return False
        old_source, new_source = old[0], new[0]
        for row in (old_source, new_source):
            if (
                row["content_integrity"] != "VERIFIED"
                or row["status_at_decision"] != "VALID"
                or row["content_hash"] != row["captured_content_hash"]
                or row["content_hash"] != configuration_hash(row["content"])
            ):
                return False
        if (
            new_source["id"] != str(proof.exposure_evidence_id)
            or new_source["content"] != proof.exposure_original
            or new_source["content_hash"] != proof.exposure_evidence_hash
            or remove_unsettled_manifest(proof, old_source["content"])
            != remove_unsettled_manifest(proof, new_source["content"])
        ):
            return False
        # No old USER answer is reused. Current two-mode proof independently
        # establishes the same fixed financial vector; source labels may differ.
        for manifest in (left, right):
            if manifest["current_world_status"] != ["KNOWN", "KNOWN"]:
                return False
        if (
            len(set(right["current_world_signatures"])) != 1
            or right["current_world_signatures"][0] is None
        ):
            return False
        right["current_world_signatures"] = left["current_world_signatures"]
        right["original_sources"] = [
            old_source if row is new_source else row for row in right["original_sources"]
        ]
        right["original_sources"].sort(key=lambda row: row["id"])
        left["original_sources"].sort(key=lambda row: row["id"])
        right["native_source_fingerprint"] = left["native_source_fingerprint"]
        old_basis, new_basis = left["complete_native_basis"], right["complete_native_basis"]
        if set(old_basis) != {
            "user_id",
            "as_of",
            "snapshot",
            "versions",
            "positions",
            "products",
            "exposure",
            "income",
            "evidence",
        } or set(new_basis) != set(old_basis):
            return False
        for basis in (old_basis, new_basis):
            basis.pop("as_of")
            basis["snapshot"].pop("as_of")
            for item in basis["snapshot"].get("living_reserves", []):
                item.pop("estimation_input_digest", None)
            for name in ("exposure", "income"):
                if isinstance(basis[name], dict):
                    basis[name].pop("as_of", None)
        new_basis["snapshot"]["source_digest"] = old_basis["snapshot"]["source_digest"]
        new_basis["exposure"]["evidence_ids"] = sorted(
            [
                old_source["id"] if identity == new_source["id"] else identity
                for identity in new_basis["exposure"]["evidence_ids"]
            ]
        )
        new_basis["evidence"] = sorted(
            [
                {"id": old_source["id"], "hash": old_source["content_hash"]}
                if item["id"] == new_source["id"]
                else item
                for item in new_basis["evidence"]
            ],
            key=lambda item: item["id"],
        )
        return left == right
    except (KeyError, TypeError, ValueError, IndexError):
        return False
