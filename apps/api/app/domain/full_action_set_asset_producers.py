"""Finite whole-portfolio action signatures over actual frozen producer inputs.

This family does not claim a global set or grant banking authority. A whole
portfolio is a distinct ASK operation, never a sum of independent suggestions.
"""

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.autonomy_types import AuthorityAssessment
from app.domain.boundary_action_events import action_signature
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import revalidate_execution
from app.domain.full_action_set_boundary import (
    ActionSetInput,
    CandidateView,
    Hash,
    derive_action_set,
)
from app.domain.full_asset_allocation import FullAssetPlanningInput, plan_full_assets
from app.domain.full_asset_execution import (
    FullAssetExecutionBasis,
    FullAssetPrepareRequest,
    build_frozen_portfolio,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.goal_allocation import IncomeLot
from app.domain.income_ledger import IncomeLedger
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.full_asset_execution import FullAssetExecutionPreview
from app.services.full_policy_lifecycle import FullPolicyView
from pydantic import Field

ALGORITHM: Literal["full-whole-asset-producer-family-v1"] = "full-whole-asset-producer-family-v1"
MAX_PRODUCERS = 16


class AssetProducerInput(BoundaryModel):
    candidate_key: str
    full_policy_id: UUID
    request: FullAssetPrepareRequest | None = None
    original_policy: FullPolicyView | None = None
    actual_preview: FullAssetExecutionPreview | None = None
    planning_input: FullAssetPlanningInput | None = None
    execution_basis: FullAssetExecutionBasis | None = None
    authority: AuthorityAssessment | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class AssetFamilyInput(BoundaryModel):
    base: ActionSetInput
    producers: list[AssetProducerInput]
    missing_reasons: list[str] = Field(default_factory=list)


class AssetFamilyResult(BoundaryModel):
    algorithm_version: Literal["full-whole-asset-producer-family-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    # The original MVP single purchase still has a real independent endpoint.
    # Only 307 was explicitly approved to replace its same-Goal nominal producer.
    replaces_original_candidate_keys: list[str] = Field(default_factory=list)
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    family_complete: bool
    expected_candidate_keys: list[str]
    covered_full_policy_ids: list[UUID]
    candidates: list[CandidateView]
    input_hash: Hash
    result_hash: Hash
    reasons: list[str]


def expected_asset_producers(
    base: ActionSetInput,
) -> dict[str, tuple[UUID, UUID | None, str | None]]:
    versions: dict[str, dict[str, Any]] = {}
    for row in base.original_inventory.get("policy_versions", []):
        if (
            row["policy_id"] not in versions
            or row["version_number"] > versions[row["policy_id"]]["version_number"]
        ):
            versions[row["policy_id"]] = row
    result: dict[str, tuple[UUID, UUID | None, str | None]] = {}
    for parent in base.original_inventory.get("full_policies", []):
        if parent.get("template_name") != "AssetAuthorizationPolicy" or parent.get(
            "epoch_id"
        ) != str(base.epoch_id):
            continue
        full_versions = [
            row
            for row in base.original_inventory.get("full_policy_versions", [])
            if row.get("policy_id") == parent["id"]
        ]
        config = AssetAuthorizationPolicy.model_validate_json(
            json.dumps(max(full_versions, key=lambda row: row["version_number"])["configuration"])
        )
        matched = []
        for mvp in base.original_inventory.get("policies", []):
            version = versions.get(mvp["id"])
            if version is None:
                continue
            value = validate_configuration(version["configuration"])
            if (
                value["type"] == "asset_authorization"
                and value["scope"] == config.scope
                and value.get("goal_id") == (str(config.goal_id) if config.goal_id else None)
            ):
                matched.append(mvp["id"])
        full_id = UUID(parent["id"])
        if not matched:
            result[f"full-asset:{full_id}:NO_MATCHING_MVP_SCOPE"] = (full_id, None, None)
        for identity in sorted(matched):
            for mode in ("PORTFOLIO", "FIXED_LADDER"):
                result[f"full-asset:{full_id}:{identity}:{mode}"] = (full_id, UUID(identity), mode)
    return dict(sorted(result.items()))


def _unknown(key: str, reasons: list[str]) -> CandidateView:
    return CandidateView(
        candidate_key=key,
        state="UNKNOWN",
        action_type="PURCHASE_PORTFOLIO",
        amount_cents=None,
        autonomy_level=None,
        signature=None,
        reasons=sorted(set(reasons)),
    )


def _clock(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("WHOLE_ASSET_ORIGINAL_CLOCK_NOT_AWARE")
    return at.astimezone(UTC)


def _economic_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    # A planner adds its actual Full sources to the source digest. This is not
    # a change to cash, ownership or the server clock; each digest is checked
    # separately against its own complete original evidence denominator.
    return {
        key: part for key, part in value.items() if key not in {"horizon_days", "source_digest"}
    }


def _inactive(base: ActionSetInput, full_id: UUID) -> bool:
    parent = next(
        row for row in base.original_inventory["full_policies"] if row["id"] == str(full_id)
    )
    latest = max(
        (
            row
            for row in base.original_inventory["full_policy_versions"]
            if row["policy_id"] == str(full_id)
        ),
        key=lambda row: row["version_number"],
    )
    return (
        parent["status"] in {"SUSPENDED", "REVOKED", "EXPIRED"}
        or base.as_of < _clock(latest["valid_from"])
        or latest.get("valid_until") is not None
        and base.as_of >= _clock(latest["valid_until"])
    )


def _catalogue(base: ActionSetInput, candidate: AssetProducerInput) -> None:
    data, preview = candidate.planning_input, candidate.actual_preview
    assert data is not None and preview is not None
    rows = base.original_inventory.get("product_catalog_versions", [])
    products = {row["id"]: row for row in base.original_inventory.get("asset_products", [])}
    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        canonical = row["canonical_product"]
        raw = products.get(row["product_id"])
        if raw is None:
            raise ValueError("WHOLE_ASSET_CURRENT_CATALOGUE_SOURCE_MISSING")
        normalized = dict(raw)
        for field in ("created_at", "effective_from", "effective_until"):
            if normalized.get(field) is not None:
                normalized[field] = (
                    _clock(normalized[field])
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
        if (
            normalized != canonical
            or configuration_hash(canonical) != row["product_hash"]
            or configuration_hash(canonical["maturity_rule"]) != row["terms_digest"]
        ):
            raise ValueError("WHOLE_ASSET_IMMUTABLE_CATALOGUE_BYTES_DIFFER")
        start, end = _clock(row["effective_from"]), row.get("effective_until")
        if (
            row.get("protocol_version") != "product-catalog-v1"
            or canonical["id"] != row["product_id"]
            or canonical["product_code"] != row["product_code"]
            or canonical["version_number"] != row["version_number"]
            or canonical["effective_from"]
            != start.isoformat(timespec="microseconds").replace("+00:00", "Z")
            or canonical["effective_until"]
            != (
                _clock(end).isoformat(timespec="microseconds").replace("+00:00", "Z")
                if end
                else None
            )
        ):
            raise ValueError("WHOLE_ASSET_CATALOGUE_IDENTITY_OR_WINDOW_DIFFERS")
        if _clock(row["observed_at"]) > base.as_of:
            raise ValueError("WHOLE_ASSET_CATALOGUE_NOT_YET_OBSERVED")
        if start <= base.as_of and (end is None or base.as_of < _clock(end)):
            if row["product_id"] in active:
                raise ValueError("WHOLE_ASSET_DUPLICATE_CURRENT_CATALOGUE")
            active[row["product_id"]] = row
    if {row["product_id"] for row in rows} != set(products):
        raise ValueError("WHOLE_ASSET_COMPLETE_CATALOGUE_DENOMINATOR_DIFFERS")
    if {str(p.product_id) for p in data.products} != set(active):
        raise ValueError("WHOLE_ASSET_ACTIVE_CATALOGUE_DENOMINATOR_DIFFERS")
    bindings = {
        str(row.product_id): row for row in preview.original_full_planning.catalogue.bindings
    }
    if (
        len(bindings) != len(preview.original_full_planning.catalogue.bindings)
        or set(bindings) != set(active)
        or preview.original_full_planning.catalogue.status != "VERIFIED"
        or preview.original_full_planning.catalogue.issues
    ):
        raise ValueError("WHOLE_ASSET_CATALOGUE_BINDING_DENOMINATOR_DIFFERS")
    for terms in data.products:
        row = active[str(terms.product_id)]
        fields = set(AssetProductTerms.model_fields) - {"product_id", "terms_digest"}
        original_terms = AssetProductTerms.model_validate_json(
            json.dumps(
                {field: row["canonical_product"][field] for field in fields}
                | {"product_id": row["product_id"], "terms_digest": row["terms_digest"]}
            )
        )
        ref = bindings.get(str(terms.product_id))
        if (
            original_terms != terms
            or ref is None
            or str(ref.catalogue_version_id) != row["id"]
            or ref.product_record_hash != row["product_hash"]
            or ref.terms_digest != row["terms_digest"]
        ):
            raise ValueError("WHOLE_ASSET_IMMUTABLE_TERMS_OR_BINDING_DIFFERS")


def _originals(base: ActionSetInput, candidate: AssetProducerInput) -> None:
    data, original = candidate.planning_input, candidate.original_policy
    request, preview = candidate.request, candidate.actual_preview
    assert data is not None and original is not None and request is not None and preview is not None
    if (
        original.policy_id != candidate.full_policy_id
        or original.epoch_id != base.epoch_id
        or preview.user_id != base.user_id
        or preview.epoch_id != base.epoch_id
        or preview.as_of != base.as_of
        or preview.original_request != request
        or data.snapshot.as_of != base.as_of
        or _economic_snapshot(data.snapshot.model_dump(mode="json"))
        != _economic_snapshot(base.financial_basis["snapshot"])
        or data.policy_id != original.policy_id
        or data.policy_version_id != original.current_version.version_id
        or data.configuration.model_dump(mode="json") != original.current_version.configuration
        or not original.planning_confirmation_valid
        or original.reference_validation != "CURRENT"
        or original.effective_status != "ACTIVE"
        or original.template_name != "AssetAuthorizationPolicy"
        or request.full_policy_id != original.policy_id
        or request.expected_full_policy_version_id != original.current_version.version_id
        or request.expected_epoch_id != base.epoch_id
        or request.goal_id != data.configuration.goal_id
        or data.snapshot.source_issues
        or preview.original_full_planning.user_id != base.user_id
        or preview.original_full_planning.as_of != base.as_of
        or preview.original_full_planning.source_issues
        or preview.original_full_protection.source_issues
        or not (
            preview.original_full_protection.projection.full_obligations_complete_within_registered_current_scope
        )
        or preview.original_full_protection.user_id != base.user_id
        or preview.original_full_protection.as_of != base.as_of
    ):
        raise ValueError("WHOLE_ASSET_ACTUAL_OWNER_CLOCK_SCOPE_OR_ORIGINAL_DIFFERS")
    versions = {row["id"]: row for row in base.original_inventory.get("full_policy_versions", [])}
    current = versions.get(str(original.current_version.version_id))
    latest = max(
        (row for row in versions.values() if row["policy_id"] == str(original.policy_id)),
        key=lambda row: row["version_number"],
        default=None,
    )
    if (
        current is None
        or current != latest
        or current["configuration"] != original.current_version.configuration
        or current["content_hash"] != configuration_hash(data.configuration.model_dump(mode="json"))
        or current["confirmation"] != original.current_version.confirmation
        or current["user_id"] != str(base.user_id)
        or current["policy_id"] != str(original.policy_id)
    ):
        raise ValueError("WHOLE_ASSET_CURRENT_FULL_VERSION_NOT_BOUND")
    evidence = {row["id"]: row for row in base.original_inventory.get("evidence_items", [])}
    accounts = {row["id"]: row for row in base.original_inventory["accounts"]}
    for cash_account in data.snapshot.cash_accounts:
        raw_account = accounts.get(str(cash_account.account_id))
        if (
            raw_account is None
            or raw_account["balance_cents"] != cash_account.balance_cents
            or raw_account["account_type"] != cash_account.account_type
        ):
            raise ValueError("WHOLE_ASSET_ORIGINAL_CASH_ROWS_DIFFER")
    identities = {
        *preview.original_full_planning.source_evidence_ids,
        *preview.original_full_protection.source_evidence_ids,
        *original.current_version.evidence_ids,
        *(candidate.authority.evidence_ids if candidate.authority else []),
        *(candidate.execution_basis.source_evidence_ids if candidate.execution_basis else []),
        *(
            [key for lot in candidate.execution_basis.context.lots for key in lot.evidence_ids]
            if candidate.execution_basis
            else []
        ),
    }
    for identity in identities:
        row = evidence.get(str(identity))
        if (
            row is None
            or row.get("user_id") != str(base.user_id)
            or row.get("status") != "VALID"
            or configuration_hash(row.get("content", {})) != row.get("content_hash")
        ):
            raise ValueError("WHOLE_ASSET_ORIGINAL_SOURCE_NOT_VERIFIED")
        if row.get("observed_at") is None:
            raise ValueError("WHOLE_ASSET_ORIGINAL_SOURCE_OBSERVATION_MISSING")
        for name, lower in (
            ("observed_at", True),
            ("valid_from", True),
            ("valid_until", False),
            ("valid_to", False),
        ):
            if row.get(name) is not None:
                at = _clock(row[name])
                if lower and at > base.as_of or not lower and at <= base.as_of:
                    raise ValueError("WHOLE_ASSET_ORIGINAL_SOURCE_NOT_CURRENT")
    if (
        candidate.authority is None
        or candidate.authority.status != "AUTHORIZED"
        or candidate.authority.reasons
    ):
        raise ValueError("WHOLE_ASSET_ORIGINAL_MVP_PERMISSION_NOT_VERIFIED")
    confirmation = current["confirmation"]
    proof = evidence.get(confirmation.get("confirmation_evidence_id"))
    if (
        confirmation.get("protocol") != "full-policy-confirmation-v1"
        or confirmation.get("user_id") != str(base.user_id)
        or confirmation.get("epoch_id") != str(base.epoch_id)
        or confirmation.get("policy_id") != str(original.policy_id)
        or confirmation.get("version_id") != current["id"]
        or confirmation.get("reviewed_hash") != current["content_hash"]
        or confirmation.get("accepted") is not True
        or confirmation.get("bank_authority") is not False
        or proof is None
        or proof.get("source_type") != "FULL_POLICY_CONFIRMATION"
        or proof.get("source_ref") != current["id"]
        or proof.get("evidence_level") != "USER_CONFIRMED_POLICY"
        or proof.get("content") != confirmation
        or proof["id"] not in current["evidence_ids"]
        or _clock(proof["observed_at"]) != _clock(current["confirmed_at"])
    ):
        raise ValueError("WHOLE_ASSET_FULL_PLANNING_CONFIRMATION_ORIGINAL_DIFFERS")
    raw_mvp = {row["id"]: row for row in base.original_inventory["policy_versions"]}
    asset_version = raw_mvp.get(str(request.expected_mvp_policy_version_id))
    if asset_version is None or asset_version["policy_id"] != str(request.mvp_asset_policy_id):
        raise ValueError("WHOLE_ASSET_ORIGINAL_REQUEST_MVP_VERSION_DIFFERS")
    asset_configuration = validate_configuration(asset_version["configuration"])
    if (
        asset_configuration["type"] != "asset_authorization"
        or asset_configuration["scope"] != data.configuration.scope
        or asset_configuration.get("goal_id") != (str(request.goal_id) if request.goal_id else None)
    ):
        raise ValueError("WHOLE_ASSET_ORIGINAL_REQUEST_MVP_SCOPE_DIFFERS")
    expected_authority = {request.expected_mvp_policy_version_id}
    if request.expected_goal_policy_version_id is not None:
        goal = next(
            (row for row in base.original_inventory["goals"] if row["id"] == str(request.goal_id)),
            None,
        )
        if (
            goal is None
            or goal["policy_version_id"] != str(request.expected_goal_policy_version_id)
            or goal["asset_policy_id"] != str(request.mvp_asset_policy_id)
            or data.goal_policy_version_id != request.expected_goal_policy_version_id
        ):
            raise ValueError("WHOLE_ASSET_ORIGINAL_GOAL_AND_ASSET_LINK_DIFFERS")
        expected_authority.add(request.expected_goal_policy_version_id)
    if (
        len(candidate.authority.policy_version_ids) != len(expected_authority)
        or set(candidate.authority.policy_version_ids) != expected_authority
    ):
        raise ValueError("WHOLE_ASSET_ORIGINAL_REQUEST_PERMISSION_DENOMINATOR_DIFFERS")
    for typed_version in data.boundary_versions:
        raw_version = raw_mvp.get(str(typed_version.version_id))
        if (
            raw_version is None
            or raw_version["configuration"] != typed_version.configuration
            or raw_version["content_hash"] != typed_version.content_hash
            or raw_version["policy_id"] != str(typed_version.policy_id)
        ):
            raise ValueError("WHOLE_ASSET_ORIGINAL_BOUNDARY_VERSION_DIFFERS")
    required_permission: set[UUID] = set()
    for identity in candidate.authority.policy_version_ids:
        row = raw_mvp.get(str(identity))
        if (
            row is None
            or row.get("confirmed_at") is None
            or _clock(row["confirmed_at"]) > base.as_of
        ):
            raise ValueError("WHOLE_ASSET_CURRENT_MVP_PERMISSION_ORIGINAL_MISSING")
        parent = next(
            (
                parent
                for parent in base.original_inventory["policies"]
                if parent["id"] == row["policy_id"]
            ),
            None,
        )
        latest_mvp = max(
            (version for version in raw_mvp.values() if version["policy_id"] == row["policy_id"]),
            key=lambda version: version["version_number"],
        )
        if (
            parent is None
            or parent["status"] not in {"ACTIVE", "CONFIRMED"}
            or latest_mvp["id"] != row["id"]
            or _clock(row["valid_from"]) > base.as_of
            or row.get("valid_until") is not None
            and base.as_of >= _clock(row["valid_until"])
        ):
            raise ValueError("WHOLE_ASSET_CURRENT_MVP_PERMISSION_WINDOW_DIFFERS")
        grant = row["confirmation"]
        expected = {
            "user_id": str(base.user_id),
            "policy_id": row["policy_id"],
            "version_id": row["id"],
            "reviewed_hash": row["content_hash"],
            "confirmed_at": row["confirmed_at"],
            "accepted": True,
        }
        if (
            configuration_hash(validate_configuration(row["configuration"])) != row["content_hash"]
            or any(grant.get(key) != value for key, value in expected.items())
            or not any(
                evidence.get(key, {}).get("source_type") == "POLICY_CONFIRMATION"
                and evidence[key].get("source_ref") == row["id"]
                and evidence[key].get("evidence_level") == "USER_CONFIRMED_POLICY"
                and evidence[key].get("content") == grant
                for key in row["evidence_ids"]
            )
        ):
            raise ValueError("WHOLE_ASSET_MVP_CONFIRMATION_ORIGINAL_DIFFERS")
        required_permission.update(UUID(key) for key in row["evidence_ids"])
    if required_permission != set(candidate.authority.evidence_ids):
        raise ValueError("WHOLE_ASSET_PERMISSION_SOURCE_DENOMINATOR_DIFFERS")
    digest = configuration_hash(
        {
            "user_id": str(base.user_id),
            "as_of": base.as_of.isoformat(),
            "sources": [
                {"id": str(key), "hash": evidence[str(key)]["content_hash"]}
                for key in sorted(preview.original_full_planning.source_evidence_ids)
            ],
            "issues": [],
        }
    )
    if data.snapshot.source_digest != digest:
        raise ValueError("WHOLE_ASSET_PLANNING_SOURCE_DIGEST_DIFFERS")
    if candidate.execution_basis is not None:
        context = candidate.execution_basis.context
        original_income = base.financial_basis.get("income")
        if original_income is None:
            if context.lots:
                raise ValueError("WHOLE_ASSET_ORIGINAL_INCOME_LEDGER_MISSING")
        else:
            ledger = IncomeLedger.model_validate_json(json.dumps(original_income))
            statements = [
                row
                for row in evidence.values()
                if row.get("source_type") == "SIMULATED_NEW_FUNDS_LEDGER"
                and row.get("status") == "VALID"
                and row.get("content") == original_income
            ]
            if (
                len(statements) != 1
                or statements[0].get("evidence_level") != "BANK_CONFIRMED"
                or UUID(statements[0]["id"]) not in identities
                or ledger.user_id != base.user_id
                or ledger.as_of > base.as_of
            ):
                raise ValueError("WHOLE_ASSET_CURRENT_INCOME_LEDGER_ORIGINAL_DIFFERS")
            origins = {row.origin_transaction_id: row for row in ledger.origins}
            lots = [
                IncomeLot(
                    origin_transaction_id=row.origin_transaction_id,
                    account_id=row.account_id,
                    fragment_id=row.fragment_id,
                    amount_cents=origins[row.origin_transaction_id].amount_cents,
                    available_cents=row.available_cents,
                    occurred_at=origins[row.origin_transaction_id].occurred_at,
                    observed_at=origins[row.origin_transaction_id].observed_at,
                    evidence_ids=[
                        UUID(statements[0]["id"]),
                        origins[row.origin_transaction_id].bank_evidence_id,
                    ],
                )
                for row in sorted(ledger.fragments, key=lambda row: row.fragment_id)
            ]
            if lots != context.lots:
                raise ValueError("WHOLE_ASSET_COMPLETE_ORIGINAL_INCOME_LOTS_DIFFER")
        protection = candidate.execution_basis.full_protection_sources
        expected_protection = {
            row["id"]
            for row in base.original_inventory["full_policies"]
            if row["template_name"]
            in {"DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"}
        }
        if (
            len(protection) != len({row.policy_id for row in protection})
            or {str(row.policy_id) for row in protection} != expected_protection
            or protection != preview.original_full_protection.full_policy_sources
        ):
            raise ValueError("WHOLE_ASSET_COMPLETE_FULL_PROTECTION_DENOMINATOR_DIFFERS")
        for source in protection:
            raw = versions.get(str(source.version_id))
            latest_source = max(
                (row for row in versions.values() if row["policy_id"] == str(source.policy_id)),
                key=lambda row: row["version_number"],
                default=None,
            )
            if (
                raw is None
                or raw != latest_source
                or raw["configuration"] != source.configuration
                or raw["content_hash"] != source.content_hash
                or raw["confirmation"] != source.confirmation
                or set(raw["evidence_ids"]) != {str(key) for key in source.evidence_ids}
            ):
                raise ValueError("WHOLE_ASSET_FULL_PROTECTION_ORIGINAL_VERSION_DIFFERS")
        if (
            _economic_snapshot(context.snapshot.model_dump(mode="json"))
            != _economic_snapshot(data.snapshot.model_dump(mode="json"))
            or context.exposure != data.exposure
        ):
            raise ValueError("WHOLE_ASSET_PLANNING_AND_EXECUTION_ORIGINAL_SCOPE_DIFFERS")
        if (
            context.snapshot.source_issues
            or context.snapshot.cash_accounts != data.snapshot.cash_accounts
            or [row.model_dump(mode="json") for row in context.versions]
            != base.financial_basis.get("versions")
            or [row.model_dump(mode="json") for row in context.positions]
            != base.financial_basis.get("positions")
        ):
            raise ValueError("WHOLE_ASSET_COMPLETE_ORIGINAL_CONTEXT_DIFFERS")
        cash: dict[UUID, int] = {}
        goals: dict[UUID, int] = {}
        positions: set[UUID] = set()
        for claim in base.original_inventory["action_resource_reservations"]:
            if claim["status"] != "RESERVED":
                continue
            if (
                _clock(claim["created_at"]) > base.as_of
                or type(claim["amount_cents"]) is not int
                or claim["amount_cents"] < 0
            ):
                raise ValueError("WHOLE_ASSET_ORIGINAL_CLAIM_INVALID")
            identity, amount = UUID(claim["resource_key"]), claim["amount_cents"]
            if claim["resource_kind"] == "CASH":
                cash[identity] = cash.get(identity, 0) + amount
            elif claim["resource_kind"] == "GOAL_CASH":
                goals[identity] = goals.get(identity, 0) + amount
            elif claim["resource_kind"] == "POSITION":
                positions.add(identity)
            else:
                raise ValueError("WHOLE_ASSET_ORIGINAL_CLAIM_KIND_UNKNOWN")
        for fragment in (base.financial_basis.get("income") or {}).get("fragments", []):
            if fragment.get("legacy_reserved_cents"):
                identity = UUID(fragment["account_id"])
                cash[identity] = cash.get(identity, 0) + fragment["legacy_reserved_cents"]
        if (
            cash != context.reserved_cash_by_account
            or goals != context.reserved_goal_cash_by_goal
            or positions != set(context.reserved_position_ids)
        ):
            raise ValueError("WHOLE_ASSET_COMPLETE_ORIGINAL_CLAIMS_DIFFER")
    _catalogue(base, candidate)


def asset_producer_view(base: ActionSetInput, candidate: AssetProducerInput) -> CandidateView:
    try:
        if _inactive(base, candidate.full_policy_id):
            return CandidateView(
                candidate_key=candidate.candidate_key,
                state="EXCLUDED",
                action_type="PURCHASE_PORTFOLIO",
                amount_cents=None,
                autonomy_level="BLOCKED",
                signature=None,
                reasons=["CURRENT_ORIGINAL_FULL_POLICY_NOT_ACTIVE"],
            )
    except (ValueError, KeyError, TypeError, StopIteration):
        return _unknown(candidate.candidate_key, ["CURRENT_ORIGINAL_FULL_POLICY_DENIAL_NOT_PROVEN"])
    if candidate.missing_reasons:
        return _unknown(candidate.candidate_key, candidate.missing_reasons)
    if (
        candidate.request is None
        or candidate.original_policy is None
        or candidate.planning_input is None
        or candidate.actual_preview is None
    ):
        return _unknown(candidate.candidate_key, ["COMPLETE_ACTUAL_WHOLE_ASSET_INPUT_MISSING"])
    try:
        _originals(base, candidate)
        planning = plan_full_assets(candidate.planning_input)
        preview = candidate.actual_preview
        if planning != preview.original_full_planning.allocation:
            raise ValueError("WHOLE_ASSET_ACTUAL_OPTIMIZER_RESULT_NOT_REPRODUCIBLE")
        if (
            candidate.request.planning_mode == "FIXED_LADDER"
            and candidate.request.goal_id is None
            and planning.reasons == ["FIXED_LADDER_REQUIRES_AN_ACTUAL_GOAL"]
        ):
            return CandidateView(
                candidate_key=candidate.candidate_key,
                state="EXCLUDED",
                action_type="PURCHASE_PORTFOLIO",
                amount_cents=None,
                autonomy_level="BLOCKED",
                signature=None,
                reasons=planning.reasons,
            )
        if planning.status != "OPTIMAL":
            return _unknown(candidate.candidate_key, planning.reasons)
        if (
            not planning.batches
            and preview.portfolio is None
            and not preview.original_full_planning.source_issues
        ):
            return CandidateView(
                candidate_key=candidate.candidate_key,
                state="EXCLUDED",
                action_type="PURCHASE_PORTFOLIO",
                amount_cents=0,
                autonomy_level="BLOCKED",
                signature=None,
                reasons=["ACTUAL_OPTIMAL_PLAN_RETAINS_CASH"],
            )
        if candidate.execution_basis is None or preview.portfolio is None:
            return _unknown(
                candidate.candidate_key, ["ACTUAL_WHOLE_EXECUTION_BASIS_MISSING", *preview.reasons]
            )
        portfolio = build_frozen_portfolio(candidate.request, candidate.execution_basis)
        if portfolio != preview.portfolio or preview.state != "READY_TO_REVIEW":
            raise ValueError("WHOLE_ASSET_ACTUAL_PORTFOLIO_NOT_REPRODUCIBLE")
        if candidate.authority is None or set(candidate.authority.policy_version_ids) != set(
            portfolio.batches[0].command.effect.policy_version_ids
        ):
            raise ValueError("WHOLE_ASSET_EXACT_MVP_PERMISSION_DENOMINATOR_DIFFERS")
        signatures = []
        for batch in portfolio.batches:
            validation = revalidate_execution(
                batch.command.effect, candidate.execution_basis.context
            )
            if validation.status not in {"READY", "CONFIRMATION_REQUIRED"}:
                raise ValueError("WHOLE_ASSET_ORIGINAL_BATCH_FINANCIAL_GATE_NOT_READY")
            signatures.append(action_signature(batch.command.effect, validation, "ASK_ONCE"))
        return CandidateView(
            candidate_key=candidate.candidate_key,
            state="INCLUDED",
            action_type="PURCHASE_PORTFOLIO",
            amount_cents=portfolio.total_purchase_cents,
            autonomy_level="ASK_ONCE",
            signature=configuration_hash(
                {
                    "kind": "ORDERED_WHOLE_PURCHASE",
                    "amount_cents": portfolio.total_purchase_cents,
                    "ordered_batches": signatures,
                    "requires_original_whole_consent": True,
                }
            ),
            reasons=["EXACT_WHOLE_PORTFOLIO_USER_CONFIRMATION_REQUIRED"],
        )
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return _unknown(candidate.candidate_key, [str(error)])


def derive_asset_family(data: AssetFamilyInput) -> AssetFamilyResult:
    data = AssetFamilyInput.model_validate(data.model_dump())
    reasons: list[str] = list(data.missing_reasons)
    try:
        expected = expected_asset_producers(data.base)
    except (ValueError, TypeError, KeyError) as error:
        expected = {}
        reasons.append("ORIGINAL_WHOLE_ASSET_DENOMINATOR_INVALID:" + str(error))
    structural = derive_action_set(data.base)
    # This helper only covers its own family; original producer/other-family
    # UNKNOWN is retained by the future global aggregator, not miscounted here.
    reasons.extend(
        reason
        for reason in structural.reasons
        if reason not in {"UNSUPPORTED_CURRENT_PRODUCERS", "CURRENT_PRODUCER_UNKNOWN"}
        and not reason.startswith("ORIGINAL_CANDIDATE_BINDING_DIFFERS:")
    )
    keys = [row.candidate_key for row in data.producers]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        reasons.append("COMPLETE_WHOLE_ASSET_PRODUCER_DENOMINATOR_DIFFERS")
    if len(keys) > MAX_PRODUCERS:
        reasons.append("WHOLE_ASSET_PRODUCER_CAPACITY_EXCEEDED")
    candidates = []
    for producer in data.producers:
        binding = expected.get(producer.candidate_key)
        if (
            binding is None
            or binding[0] != producer.full_policy_id
            or producer.request is not None
            and (
                binding[1] != producer.request.mvp_asset_policy_id
                or binding[2] != producer.request.planning_mode
            )
        ):
            candidates.append(
                _unknown(producer.candidate_key, ["ORIGINAL_WHOLE_ASSET_BINDING_DIFFERS"])
            )
        else:
            candidates.append(asset_producer_view(data.base, producer))
    if any(row.state == "UNKNOWN" for row in candidates):
        reasons.append("CURRENT_WHOLE_ASSET_PRODUCER_UNKNOWN")
    fields = dict(
        user_id=data.base.user_id,
        epoch_id=data.base.epoch_id,
        as_of=data.base.as_of,
        family_complete=not reasons,
        expected_candidate_keys=sorted(expected),
        covered_full_policy_ids=sorted({binding[0] for binding in expected.values()})
        if not reasons
        else [],
        candidates=sorted(candidates, key=lambda row: row.candidate_key),
        input_hash=configuration_hash(data.model_dump(mode="json")),
        reasons=sorted(set(reasons)),
    )
    temporary = AssetFamilyResult.model_validate({**fields, "result_hash": "0" * 64})
    return temporary.model_copy(
        update={
            "result_hash": configuration_hash(
                temporary.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )
