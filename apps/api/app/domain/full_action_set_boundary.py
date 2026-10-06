"""Deterministic finite policy-producer action sets; missing worlds are not empty sets."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.autonomy import classify_autonomy
from app.domain.autonomy_types import AutonomyFacts
from app.domain.boundary_action_events import action_signature
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext
from app.domain.full_execution_protection import (
    FullExecutionProtectionResult,
    validate_full_execution_protection,
)
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.full_registered_account_debits import FullAccountDebitBoundsProof
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.question_workflow import CommandKey
from pydantic import Field

ALGORITHM: Literal["full-policy-action-set-boundary-v1"] = "full-policy-action-set-boundary-v1"
SCOPE: Literal["POLICY_BACKED_SERVER_PRODUCERS_V1"] = "POLICY_BACKED_SERVER_PRODUCERS_V1"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
REQUIRED_INVENTORY = frozenset(
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
        "simulated_bank_ledger_heads",
        "simulated_bank_postings",
        "action_resource_reservations",
        "evidence_items",
        "full_policies",
        "full_policy_versions",
        "full_policy_commands",
        "product_catalog_versions",
    }
)


class GlobalBoundaryObserveRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    previous_observation_run_id: UUIDReference | None = None
    idempotency_key: CommandKey


class CandidateInput(BoundaryModel):
    candidate_key: str
    facts: AutonomyFacts | None = None
    execution_context: ExecutionContext | None = None
    # A finite, verified current policy denial excludes a producer. Unknown
    # planner errors and absent original fields must remain UNKNOWN.
    excluded_by_current_policy: bool = False
    missing_reasons: list[str] = Field(default_factory=list)
    full_protection: FullExecutionProtectionResult | None = None
    full_sources: list[FullProtectionPolicySource] = Field(default_factory=list)
    full_source_issues: list[str] = Field(default_factory=list)
    full_account_debit_bounds: FullAccountDebitBoundsProof | None = None


class CandidateView(BoundaryModel):
    candidate_key: str
    state: Literal["INCLUDED", "EXCLUDED", "UNKNOWN"]
    action_type: str | None
    amount_cents: int | None
    autonomy_level: str | None
    signature: Hash | None
    reasons: list[str]


class ActionSetInput(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    # Actual complete rows captured by the service, not a client declaration.
    original_inventory: dict[str, list[dict[str, Any]]]
    inventory_reasons: list[str]
    expected_candidate_keys: list[str]
    candidates: list[CandidateInput]
    financial_input_hash: Hash
    financial_basis: dict[str, Any]
    audit_verified: bool
    source_reasons: list[str]
    unsupported_producers: list[str]


class ActionSetSnapshot(BoundaryModel):
    algorithm_version: Literal["full-policy-action-set-boundary-v1"] = ALGORITHM
    scope: Literal["POLICY_BACKED_SERVER_PRODUCERS_V1"] = SCOPE
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE", "UNKNOWN"]
    global_action_set_complete: bool
    arbitrary_manual_intents_covered: Literal[False] = False
    original_inventory_hash: Hash
    financial_input_hash: Hash
    input_hash: Hash
    snapshot_hash: Hash
    action_set_signature: Hash | None
    expected_candidate_keys: list[str]
    candidates: list[CandidateView]
    unsupported_producers: list[str]
    reasons: list[str]


class GlobalBoundaryObservation(BoundaryModel):
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
    snapshot: ActionSetSnapshot
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] | None
    semantic_key: Hash | None
    requires_user_attention: bool
    previous_snapshot_hash: Hash | None
    previous_action_set_signature: Hash | None
    global_action_set_complete: bool
    idempotent_replay: bool = False
    question_delivery: Literal["ROOT_GLOBAL_INTERVENTION_BRANCH_REQUIRED"] = (
        "ROOT_GLOBAL_INTERVENTION_BRANCH_REQUIRED"
    )


def candidate_view(value: CandidateInput, user_id: UUID, now: datetime) -> CandidateView:
    facts, reasons = value.facts, list(value.missing_reasons)
    if value.excluded_by_current_policy and not reasons and facts is None:
        return CandidateView(
            candidate_key=value.candidate_key,
            state="EXCLUDED",
            action_type=None,
            amount_cents=None,
            autonomy_level=None,
            signature=None,
            reasons=["CURRENT_ORIGINAL_POLICY_NOT_ACTIVE"],
        )
    if facts is None:
        reasons.append("ACTUAL_PRODUCER_RESULT_MISSING")
    elif facts.user_id != user_id or facts.as_of != now:
        reasons.append("ACTUAL_CANDIDATE_OWNER_OR_CLOCK_DIFFERS")
    elif facts.source_issues or facts.authority.status in {"MISSING_EVIDENCE", "STALE_VERSION"}:
        reasons.append("ACTUAL_CANDIDATE_SOURCES_UNKNOWN")
    elif facts.effect is None or facts.validation is None or value.execution_context is None:
        # Generic EXECUTION_NOT_READY cannot distinguish missing source from a
        # proved zero suggestion. It is deliberately not a verified exclusion.
        reasons.append("COMPLETE_ACTUAL_EFFECT_AND_VALIDATION_MISSING")
    else:
        context = value.execution_context
        try:
            rebuilt = revalidate_execution(facts.effect, context, confirmation=facts.confirmation)
            if (
                rebuilt != facts.validation
                or context.user_id != user_id
                or context.snapshot.as_of != now
            ):
                reasons.append("ACTUAL_FINANCIAL_VALIDATION_NOT_REPRODUCIBLE")
            if rebuilt.status == "INSUFFICIENT_EVIDENCE":
                reasons.append("ACTUAL_FINANCIAL_VALIDATION_UNKNOWN")
            if value.full_protection is not None and (
                value.full_protection.original_effect_hash != rebuilt.effect_hash
                or value.full_protection.status == "UNKNOWN"
            ):
                reasons.append("FULL_PROTECTION_NOT_PROVEN")
            if value.full_protection is not None:
                rebuilt_full = validate_full_execution_protection(
                    facts.effect,
                    context,
                    rebuilt,
                    value.full_sources,
                    source_issues=tuple(value.full_source_issues),
                    account_debit_bounds=value.full_account_debit_bounds,
                )
                if rebuilt_full != value.full_protection:
                    reasons.append("FULL_PROTECTION_NOT_REPRODUCIBLE")
            elif value.full_sources or value.full_source_issues:
                reasons.append("FULL_PROTECTION_RESULT_MISSING")
            if not reasons:
                decision = classify_autonomy(facts)
                included = decision.level != "BLOCKED" and (
                    value.full_protection is None
                    or value.full_protection.status in {"PASSED", "NO_ADDITIONAL_POLICY"}
                )
                return CandidateView(
                    candidate_key=value.candidate_key,
                    state="INCLUDED" if included else "EXCLUDED",
                    action_type=facts.effect.action_type,
                    amount_cents=facts.effect.amount_cents,
                    autonomy_level=decision.level,
                    signature=action_signature(facts.effect, rebuilt, decision.level)
                    if included
                    else None,
                    reasons=decision.reasons
                    + (value.full_protection.reasons if value.full_protection else []),
                )
        except (ValueError, TypeError, KeyError):
            reasons.append("ACTUAL_FINANCIAL_VALIDATION_NOT_REPRODUCIBLE")
    return CandidateView(
        candidate_key=value.candidate_key,
        state="UNKNOWN",
        action_type=facts.action_type if facts else None,
        amount_cents=None,
        autonomy_level=None,
        signature=None,
        reasons=sorted(set(reasons)),
    )


def derive_action_set(value: ActionSetInput) -> ActionSetSnapshot:
    value = ActionSetInput.model_validate(value.model_dump())
    reasons = [*value.inventory_reasons, *value.source_reasons]
    if (
        configuration_hash(value.financial_basis) != value.financial_input_hash
        or value.financial_basis.get("user_id") != str(value.user_id)
        or value.financial_basis.get("as_of") != value.as_of.isoformat()
    ):
        reasons.append("ORIGINAL_FINANCIAL_BASIS_BINDING_DIFFERS")
    if not REQUIRED_INVENTORY.issubset(value.original_inventory):
        reasons.append("REQUIRED_ORIGINAL_INVENTORY_MISSING")
    users = value.original_inventory.get("users", [])
    if (
        len(users) != 1
        or users[0].get("id") != str(value.user_id)
        or users[0].get("is_simulated") is not True
    ):
        reasons.append("ACTUAL_SIMULATED_OWNER_NOT_VERIFIED")
    expected, actual = (
        value.expected_candidate_keys,
        [row.candidate_key for row in value.candidates],
    )
    try:
        if set(expected) != set(required_producer_keys(value.original_inventory)):
            reasons.append("RAW_ORIGINAL_PRODUCER_DENOMINATOR_DIFFERS")
        if set(value.unsupported_producers) != set(
            unsupported_producers(value.original_inventory, value.epoch_id)
        ):
            reasons.append("RAW_UNSUPPORTED_PRODUCERS_DIFFER")
    except (KeyError, TypeError, ValueError):
        reasons.append("RAW_PRODUCER_INVENTORY_INVALID")
    if value.as_of.tzinfo is None or value.as_of.utcoffset() is None:
        reasons.append("ACTUAL_SERVER_CLOCK_MISSING")
    if (
        len(expected) != len(set(expected))
        or len(actual) != len(set(actual))
        or set(expected) != set(actual)
    ):
        reasons.append("COMPLETE_PRODUCER_DENOMINATOR_DIFFERS")
    if not value.audit_verified:
        reasons.append("ACTUAL_AUDIT_NOT_VERIFIED")
    if value.unsupported_producers:
        reasons.append("UNSUPPORTED_CURRENT_PRODUCERS")
    for name, rows in value.original_inventory.items():
        if len(rows) > 200 or len({row.get("id") for row in rows}) != len(rows):
            reasons.append("RAW_ORIGINAL_TABLE_INCOMPLETE_OR_DUPLICATE:" + name)
        for row in rows:
            try:
                if str(UUID(row["id"])) != row["id"]:
                    raise ValueError("Noncanonical original identity")
            except (KeyError, ValueError, TypeError, AttributeError):
                reasons.append("ORIGINAL_ROW_ID_INVALID:" + name)
            if "user_id" in row and row["user_id"] != str(value.user_id):
                reasons.append("INVENTORY_OWNER_DIFFERS:" + name)
            if (
                name not in {"users", "asset_products", "product_catalog_versions"}
                and "user_id" not in row
            ):
                reasons.append("INVENTORY_OWNER_MISSING:" + name)
    try:
        inactive = known_inactive_keys(value.original_inventory, value.as_of)
    except (KeyError, TypeError, ValueError):
        inactive = set()
        reasons.append("CURRENT_POLICY_DENIAL_INVENTORY_INVALID")
    evaluated = [
        row.model_copy(
            update={"missing_reasons": [*row.missing_reasons, "CURRENT_POLICY_DENIAL_NOT_PROVEN"]}
        )
        if row.excluded_by_current_policy and row.candidate_key not in inactive
        else row
        for row in value.candidates
    ]
    for candidate in evaluated:
        if candidate.facts is not None and not _original_candidate_binding(value, candidate):
            reasons.append("ORIGINAL_CANDIDATE_BINDING_DIFFERS:" + candidate.candidate_key)
        if candidate.facts is not None:
            evidence = {
                row["id"]: row for row in value.original_inventory.get("evidence_items", [])
            }
            for identity in [
                *candidate.facts.source_evidence_ids,
                *candidate.facts.authority.evidence_ids,
                *candidate.facts.payee.evidence_ids,
            ]:
                raw = evidence.get(str(identity))
                if (
                    raw is None
                    or raw.get("content_hash") != configuration_hash(raw.get("content", {}))
                    or raw.get("status") != "VALID"
                ):
                    reasons.append("ORIGINAL_CANDIDATE_EVIDENCE_NOT_VERIFIED:" + str(identity))
    candidates = sorted(
        (candidate_view(row, value.user_id, value.as_of) for row in evaluated),
        key=lambda row: row.candidate_key,
    )
    if any(row.state == "UNKNOWN" for row in candidates):
        reasons.append("CURRENT_PRODUCER_UNKNOWN")
    signature = (
        None
        if reasons
        else configuration_hash(
            {
                "scope": SCOPE,
                "members": sorted(
                    {row.signature for row in candidates if row.signature is not None}
                ),
            }
        )
    )
    values = dict(
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        as_of=value.as_of,
        status="UNKNOWN" if reasons else "COMPLETE",
        global_action_set_complete=not reasons,
        original_inventory_hash=configuration_hash(value.original_inventory),
        financial_input_hash=value.financial_input_hash,
        input_hash=configuration_hash(value.model_dump(mode="json")),
        action_set_signature=signature,
        expected_candidate_keys=sorted(expected),
        candidates=candidates,
        unsupported_producers=sorted(value.unsupported_producers),
        reasons=sorted(set(reasons)),
    )
    normalized = {
        **values,
        "user_id": str(value.user_id),
        "epoch_id": str(value.epoch_id),
        "as_of": value.as_of.isoformat(),
        "candidates": [row.model_dump(mode="json") for row in candidates],
    }
    return ActionSetSnapshot.model_validate(
        {**values, "snapshot_hash": configuration_hash(normalized)}
    )


def _original_candidate_binding(value: ActionSetInput, candidate: CandidateInput) -> bool:
    facts, context = candidate.facts, candidate.execution_context
    assert facts is not None
    effect = facts.effect
    if (
        facts.initiation != "CONFIRMED_POLICY"
        or facts.source_context_hash != value.financial_input_hash
        or effect is None
        or context is None
        or context.snapshot.model_dump(mode="json") != value.financial_basis.get("snapshot")
        or set(facts.authority.policy_version_ids) != set(effect.policy_version_ids)
    ):
        return False
    prefix, _, identity = candidate.candidate_key.partition(":")
    inventory = value.original_inventory
    if prefix in {"payment", "purchase"}:
        if (
            effect.action_type != {"payment": "PAY_RECURRING", "purchase": "PURCHASE_ASSET"}[prefix]
            or str(effect.policy_id) != identity
        ):
            return False
    elif prefix == "goal":
        goal = next((row for row in inventory.get("goals", []) if row["id"] == identity), None)
        if (
            effect.action_type != "ALLOCATE_GOAL"
            or str(effect.goal_id) != identity
            or goal is None
            or goal["policy_id"] != str(effect.policy_id)
        ):
            return False
    elif prefix == "redemption":
        if effect.action_type != "REDEEM_ASSET" or str(effect.position_id) != identity:
            return False
    else:
        return False
    raw_versions = {row["id"]: row for row in inventory.get("policy_versions", [])}
    accounts = {row["id"]: row for row in inventory.get("accounts", [])}
    for cash in context.snapshot.cash_accounts:
        raw_cash = accounts.get(str(cash.account_id))
        if (
            raw_cash is None
            or raw_cash.get("balance_cents") != cash.balance_cents
            or raw_cash.get("account_type") != cash.account_type
        ):
            return False
    for version in context.versions:
        raw_version = raw_versions.get(str(version.version_id))
        if (
            raw_version is None
            or raw_version.get("policy_id") != str(version.policy_id)
            or raw_version.get("configuration") != version.configuration
            or raw_version.get("content_hash") != version.content_hash
        ):
            return False
    return {str(identity) for identity in effect.policy_version_ids}.issubset(
        raw_versions
    ) and candidate.candidate_key not in known_inactive_keys(inventory, value.as_of)


def required_producer_keys(inventory: dict[str, list[dict[str, Any]]]) -> list[str]:
    versions: dict[str, dict[str, Any]] = {}
    for version in inventory.get("policy_versions", []):
        key = version["policy_id"]
        if key not in versions or version["version_number"] > versions[key]["version_number"]:
            versions[key] = version
    keys = []
    for policy in inventory.get("policies", []):
        current_version = versions.get(policy["id"])
        if current_version is None:
            keys.append("policy:" + policy["id"])
            continue
        kind = current_version["configuration"].get("type")
        if kind == "recurring_expense":
            keys.append("payment:" + policy["id"])
        elif kind == "asset_allocation":
            keys.append("purchase:" + policy["id"])
        elif kind == "goal_saving":
            goals = [row for row in inventory.get("goals", []) if row["policy_id"] == policy["id"]]
            keys.extend("goal:" + row["id"] for row in goals)
            if not goals:
                keys.append("goal-policy:" + policy["id"])
        elif kind not in {"living_reserve", "emergency_buffer"}:
            keys.append("unsupported-policy:" + policy["id"])
    return sorted(
        keys + ["redemption:" + row["id"] for row in inventory.get("asset_positions", [])]
    )


def unsupported_producers(inventory: dict[str, list[dict[str, Any]]], epoch_id: UUID) -> list[str]:
    values = [
        "FULL_PRODUCER_ADAPTER_MISSING:" + row["template_name"] + ":" + row["id"]
        for row in inventory.get("full_policies", [])
        if row["epoch_id"] == str(epoch_id)
        and row["status"] in {"ACTIVE", "CONFIRMED"}
        and row["template_name"] not in {"SeasonalReservePolicy", "InterventionPolicy"}
    ]
    values.extend(
        "DYNAMIC_GOAL_PRODUCER_ADAPTER_MISSING:" + row["source_ref"]
        for row in inventory.get("evidence_items", [])
        if row["source_type"] == "FULL_GOAL_MODEL_V1" and row["status"] == "VALID"
    )
    return sorted(values)


def known_inactive_keys(inventory: dict[str, list[dict[str, Any]]], now: datetime) -> set[str]:
    keys = {
        "redemption:" + row["id"]
        for row in inventory.get("asset_positions", [])
        if row.get("status") == "REDEEMED"
        or "policy_version_id" in row
        and row["policy_version_id"] is None
    }
    for policy in inventory.get("policies", []):
        inactive = policy.get("status") in {"REVOKED", "SUSPENDED", "EXPIRED", "PROPOSED"}
        versions = [
            row
            for row in inventory.get("policy_versions", [])
            if row.get("policy_id") == policy.get("id")
        ]
        if versions:
            latest = max(versions, key=lambda row: row["version_number"])
            try:
                start, end = latest.get("valid_from"), latest.get("valid_until")
                inactive = inactive or start is not None and now < datetime.fromisoformat(start)
                inactive = inactive or end is not None and now >= datetime.fromisoformat(end)
            except (TypeError, ValueError):
                pass
        if inactive:
            keys.update(
                key for key in required_producer_keys(inventory) if key.endswith(":" + policy["id"])
            )
            keys.update(
                "goal:" + row["id"]
                for row in inventory.get("goals", [])
                if row.get("policy_id") == policy["id"]
            )
    return keys


def compare_action_sets(
    before: ActionSetSnapshot | None, after: ActionSetSnapshot
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
            "before": before.action_set_signature,
            "after": after.action_set_signature,
            "scope": SCOPE,
        }
    )
