"""Finite actual whole-T0 recovery producers, separate from financial authority."""

import json
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.audit_chain import build_subject, verify_frozen_projection
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand, ConfirmationGrant, ExecutionContext
from app.domain.full_action_set_boundary import (
    CandidateInput,
    CandidateView,
    Hash,
    _original_candidate_binding,
    candidate_view,
)
from app.domain.full_action_set_boundary_actual import (
    ActualActionSetInput,
    _structure,
    derive_actual_action_set,
)
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_recovery_execution import (
    MARKER,
    FullRecoveryExecutionInput,
    build_full_recovery_effect,
)
from app.domain.full_recovery_execution_trace import verify_frozen_full_recovery_trace
from app.domain.full_recovery_planning import FullRecoveryPlanningInput, plan_full_recovery
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.services.full_policy_lifecycle import FullPolicyView
from app.services.full_recovery_planning import FullRecoveryPlanningResponse
from pydantic import Field

ALGORITHM: Literal["full-policy-recovery-action-producers-v1"] = (
    "full-policy-recovery-action-producers-v1"
)
MAX_PRODUCERS = 64
MAX_BYTES = 16 * 1024 * 1024


class RecoveryOriginalCommand(BoundaryModel):
    action_id: UUID
    prepare_trace: DecisionTrace | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class RecoveryProducerInput(BoundaryModel):
    full_policy_id: UUID
    full_policy: FullPolicyView | None = None
    planning_inputs: FullRecoveryPlanningInput | None = None
    original_planning: FullRecoveryPlanningResponse | None = None
    execution_inputs: FullRecoveryExecutionInput | None = None
    candidate: CandidateInput | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class RecoveryActionSetInput(BoundaryModel):
    protocol: Literal["recovery-action-set-input-v1"] = "recovery-action-set-input-v1"
    original_actual_input: ActualActionSetInput
    expected_full_policy_ids: list[UUID]
    # All owned positions, including redeemed, manual, goal and unsupported positions.
    original_position_ids: list[UUID]
    # All owned redemption actions, including legacy, revoked and unresolved originals.
    original_action_ids: list[UUID]
    original_commands: list[RecoveryOriginalCommand]
    producers: list[RecoveryProducerInput]
    source_reasons: list[str] = Field(default_factory=list)


class RecoveryProducerResult(BoundaryModel):
    candidate_key: str
    full_policy_id: UUID
    full_policy_version_id: UUID | None
    view: CandidateView
    selected_position_ids: list[UUID]
    unsupported_position_ids: list[UUID]
    authority_excluded_position_ids: list[UUID] = Field(default_factory=list)
    unresolved_original_action_ids: list[UUID]
    shadow_original_candidate_key: str | None = None
    requires_new_exact_user_confirmation: Literal[True] = True


class RecoveryActionSetResult(BoundaryModel):
    algorithm_version: Literal["full-policy-recovery-action-producers-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    full_global_adapter_installed: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE_REGISTERED_RECOVERY_FAMILY", "UNKNOWN"]
    recovery_family_complete: bool
    original_actual_input_hash: Hash
    input_hash: Hash
    result_hash: Hash
    expected_full_policy_ids: list[UUID]
    original_position_ids: list[UUID]
    original_action_ids: list[UUID]
    unresolved_original_action_ids: list[UUID]
    results: list[RecoveryProducerResult]
    handled_unsupported_codes: list[str]
    original_actual_reasons: list[str]
    remaining_unsupported_producers: list[str]
    reasons: list[str]


def recovery_policy_ids(data: ActualActionSetInput) -> list[UUID]:
    return sorted(
        (
            UUID(row["id"])
            for row in data.base.original_inventory["full_policies"]
            if row["epoch_id"] == str(data.base.epoch_id)
            and row["template_name"] == "RecoveryPolicy"
        ),
        key=str,
    )


def recovery_action_ids(data: ActualActionSetInput) -> list[UUID]:
    return sorted(
        (
            UUID(row["id"])
            for row in data.base.original_inventory["action_plans"]
            if row["action_type"]
            in {"ASSET_REDEEM", "ASSET_MATURITY", "REDEEM_ASSET", "REDEEM_PRINCIPAL"}
            or MARKER in row["request"]
        ),
        key=str,
    )


def _clock(value: Any) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("RECOVERY_ORIGINAL_CLOCK_NOT_AWARE")
    return parsed


def _view(key: str, state: Literal["UNKNOWN", "EXCLUDED"], reason: str) -> CandidateView:
    return CandidateView(
        candidate_key=key,
        state=state,
        action_type=None,
        amount_cents=None,
        autonomy_level=None,
        signature=None,
        reasons=[reason],
    )


def _full(data: ActualActionSetInput, full: FullPolicyView) -> RecoveryPolicy:
    inventory = data.base.original_inventory
    raw = next(row for row in inventory["full_policies"] if row["id"] == str(full.policy_id))
    versions = [row for row in inventory["full_policy_versions"] if row["policy_id"] == raw["id"]]
    version = max(versions, key=lambda row: row["version_number"])
    current = full.current_version
    if (
        full.epoch_id != data.base.epoch_id
        or full.template_name != "RecoveryPolicy"
        or raw["status"] != full.status
        or raw["epoch_id"] != str(full.epoch_id)
        or raw["user_id"] != str(data.base.user_id)
        or version["id"] != str(current.version_id)
        or version["version_number"] != current.version_number
        or version["configuration"] != current.configuration
        or version["content_hash"] != current.content_hash
        or configuration_hash(current.configuration) != current.content_hash
        or version["confirmation"] != current.confirmation
        or version["impact_analysis"] != current.impact_analysis
        or version["previous_hash"] != current.previous_hash
        or version["evidence_ids"] != [str(key) for key in current.evidence_ids]
        or _clock(version["confirmed_at"]) != current.confirmed_at
        or _clock(version["valid_from"]) != current.valid_from
        or (None if version["valid_until"] is None else _clock(version["valid_until"]))
        != current.valid_until
        or current.confirmation.get("accepted") is not True
        or current.confirmation.get("reviewed_hash") != current.content_hash
    ):
        raise ValueError("RECOVERY_CURRENT_FULL_ORIGINAL_DIFFERS")
    return RecoveryPolicy.model_validate_json(json.dumps(current.configuration))


def _source(data: ActualActionSetInput, key: UUID) -> dict[str, Any]:
    raw = next(
        row for row in data.base.original_inventory["evidence_items"] if row["id"] == str(key)
    )
    now = data.base.as_of
    if (
        raw["user_id"] != str(data.base.user_id)
        or raw["status"] != "VALID"
        or configuration_hash(raw["content"]) != raw["content_hash"]
        or _clock(raw["observed_at"]) > now
        or _clock(raw["valid_from"]) > now
        or raw.get("valid_to") is not None
        and now >= _clock(raw["valid_to"])
    ):
        raise ValueError("RECOVERY_CURRENT_SOURCE_NOT_VERIFIED")
    return raw


def _funding_destination(
    data: ActualActionSetInput, raw: dict[str, Any], content: dict[str, Any]
) -> UUID:
    """Use the actual original purchase pointer, including the pre-execution legacy shape."""
    inventory = data.base.original_inventory
    protocol = content.get("acquisition_protocol")
    if protocol not in {None, "execution-purchase-v1"}:
        raise ValueError("RECOVERY_ORIGINAL_FUNDING_PROTOCOL_NOT_SUPPORTED")
    ids = content["purchase_transaction_ids"] if protocol else [content["purchase_transaction_id"]]
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) != len(set(ids))
        or (content["purchase_transaction_id"] not in ids)
    ):
        raise ValueError("RECOVERY_COMPLETE_FUNDING_TRANSACTION_DENOMINATOR_DIFFERS")
    transactions = [
        next(row for row in inventory["transactions"] if row["id"] == key) for key in ids
    ]
    for transaction in transactions:
        source = _source(data, UUID(transaction["evidence_id"]))
        expected = {
            name: transaction[name]
            for name in ("account_id", "direction", "amount_cents", "balance_after_cents")
        }
        if (
            transaction["user_id"] != str(data.base.user_id)
            or transaction["direction"] != "DEBIT"
            or type(transaction["amount_cents"]) is not int
            or type(transaction["balance_after_cents"]) is not int
            or _clock(transaction["occurred_at"]) != _clock(raw["purchased_at"])
            or _clock(transaction["observed_at"]) > data.base.as_of
            or source["source_type"] != "SIMULATED_BANK_TRANSACTION"
            or source["evidence_level"] != "BANK_CONFIRMED"
            or source["content"].get("transaction_id") != transaction["id"]
            or source["content"].get("user_id", str(data.base.user_id)) != str(data.base.user_id)
            or _clock(source["content"]["occurred_at"]) != _clock(transaction["occurred_at"])
            or source["content"].get("economic_role") != "ASSET_PURCHASE"
            or any(source["content"].get(name) != value for name, value in expected.items())
        ):
            raise ValueError("RECOVERY_ORIGINAL_FUNDING_BANK_FACT_DIFFERS")
    if sum(row["amount_cents"] for row in transactions) != raw["principal_cents"]:
        raise ValueError("RECOVERY_ORIGINAL_FUNDING_PRINCIPAL_SUM_DIFFERS")
    if raw["goal_id"] is not None:
        goal = next(row for row in inventory["goals"] if row["id"] == raw["goal_id"])
        destination = goal["account_id"]
    else:
        destination = content["return_account_id"] if protocol else transactions[0]["account_id"]
    account = next(row for row in inventory["accounts"] if row["id"] == destination)
    if account["user_id"] != str(data.base.user_id) or account["account_type"] not in {
        "CASH",
        "GOAL",
    }:
        raise ValueError("RECOVERY_ORIGINAL_FUNDING_RETURN_ACCOUNT_DIFFERS")
    if protocol:
        action = next(
            row for row in inventory["action_plans"] if row["id"] == content["purchase_action_id"]
        )
        operation = next(
            row for row in inventory["bank_operations"] if row["id"] == content["bank_operation_id"]
        )
        receipt = next(
            row
            for row in inventory["action_receipts"]
            if row["id"] == content["purchase_receipt_id"]
        )
        command = BankCommand.model_validate_json(json.dumps(action["request"]["execution"]))
        effect = command.effect
        if (
            effect.action_type != "PURCHASE_ASSET"
            or effect.position_id != UUID(raw["id"])
            or effect.amount_cents != raw["principal_cents"]
            or effect.product_id != UUID(raw["product_id"])
            or effect.position_account_id != UUID(raw["account_id"])
            or effect.return_account_id != UUID(destination)
            or effect.policy_version_id != UUID(raw["policy_version_id"])
            or effect.goal_id != (UUID(raw["goal_id"]) if raw["goal_id"] else None)
            or {str(use.account_id): use.amount_cents for use in effect.cash_uses}
            != {row["account_id"]: row["amount_cents"] for row in transactions}
        ):
            raise ValueError("RECOVERY_ORIGINAL_PURCHASE_COMMAND_FUNDING_DIFFERS")
        subjects = [
            build_subject(
                user_id=data.base.user_id,
                epoch_id=data.base.epoch_id,
                kind=kind,
                id=UUID(row["id"]),
                data=row,
            )
            for kind, table in (("TRANSACTION", "transactions"), ("EVIDENCE", "evidence_items"))
            for row in inventory[table]
        ]
        postings = [
            row
            for row in inventory["simulated_bank_postings"]
            if row["operation_id"] == operation["id"]
        ]
        verify_frozen_projection(
            operation, action, receipt, postings, observed_at=data.base.as_of, subjects=subjects
        )
        if content["bank_posting_id"] not in {row["id"] for row in postings}:
            raise ValueError("RECOVERY_ORIGINAL_PURCHASE_POSITION_LEG_MISSING")
    return UUID(destination)


def _planning(data: ActualActionSetInput, item: RecoveryProducerInput) -> None:
    full, inputs, response = item.full_policy, item.planning_inputs, item.original_planning
    if full is None or inputs is None or response is None or response.plan is None:
        raise ValueError("RECOVERY_COMPLETE_ACTUAL_PLANNING_INPUT_MISSING")
    base, inventory = data.base, data.base.original_inventory
    config = _full(data, full)
    expected_snapshot = dict(base.financial_basis["snapshot"])
    actual_snapshot = inputs.snapshot.model_dump(mode="json")
    # Source digests have different complete evidence scopes, retained and verified below.
    for snapshot in (expected_snapshot, actual_snapshot):
        snapshot.pop("source_digest", None)
        snapshot.pop("horizon_days", None)
    if (
        inputs.configuration != config
        or inputs.user_id != base.user_id
        or inputs.policy_id != full.policy_id
        or inputs.policy_version_id != full.current_version.version_id
        or inputs.snapshot.as_of != base.as_of
        or inputs.snapshot.horizon_days != 365
        or actual_snapshot != expected_snapshot
        or [row.model_dump(mode="json") for row in inputs.boundary_versions]
        != base.financial_basis["versions"]
        or [row.model_dump(mode="json") for row in inputs.positions]
        != base.financial_basis["positions"]
        or inputs.planning_confirmation_valid != full.planning_confirmation_valid
        or inputs.confirmed_at != full.current_version.confirmed_at
        or inputs.valid_from != full.current_version.valid_from
        or inputs.valid_until != full.current_version.valid_until
        or inputs.planning_deadline_at is not None
        or response.source_issues
        or response.state != "COMPUTED"
        or (response.user_id, response.policy_id, response.as_of)
        != (base.user_id, full.policy_id, base.as_of)
        or plan_full_recovery(inputs) != response.plan
    ):
        raise ValueError("RECOVERY_ACTUAL_PLANNING_NOT_REPRODUCIBLE")
    sources = [_source(data, key) for key in response.source_evidence_ids]
    digest = configuration_hash(
        {
            "user_id": str(base.user_id),
            "as_of": base.as_of.isoformat(),
            "sources": [
                {"id": row["id"], "hash": row["content_hash"]}
                for row in sorted(sources, key=lambda row: row["id"])
            ],
            "issues": [],
        }
    )
    if (
        len(sources) != len({row["id"] for row in sources})
        or inputs.snapshot.source_digest != digest
        or response.input_hash
        != configuration_hash(
            {
                "financial_source_digest": digest,
                "policy_version_id": str(full.current_version.version_id),
                "plan_input_hash": response.plan.input_hash,
                "catalogue": response.catalogue.model_dump(mode="json"),
                "planning_deadline_at": None,
            }
        )
    ):
        raise ValueError("RECOVERY_COMPLETE_PLANNING_SOURCE_HASH_DIFFERS")
    linked = inputs.linked_asset_policy
    if linked.kind != "MVP_POLICY":
        raise ValueError("RECOVERY_FULL_ONLY_ASSET_EXECUTION_NOT_SUPPORTED")
    version = next(
        row for row in inventory["policy_versions"] if row["id"] == str(linked.version_id)
    )
    current = max(
        (row for row in inventory["policy_versions"] if row["policy_id"] == str(linked.policy_id)),
        key=lambda row: row["version_number"],
    )
    if (
        version != current
        or version["configuration"] != linked.configuration
        or (version["content_hash"] != linked.content_hash)
    ):
        raise ValueError("RECOVERY_LINKED_CURRENT_ORIGINAL_VERSION_DIFFERS")
    owned = [row for row in inventory["asset_positions"] if row["status"] != "REDEEMED"]
    if {str(row.original.position_id) for row in inputs.holdings} != {row["id"] for row in owned}:
        raise ValueError("RECOVERY_COMPLETE_POSITION_DENOMINATOR_DIFFERS")
    if len(inputs.holdings) != len(owned):
        raise ValueError("RECOVERY_DUPLICATE_POSITION_ORIGINAL")
    catalogues = {row["id"]: row for row in inventory["product_catalog_versions"]}
    products = {row["id"]: row for row in inventory["asset_products"]}
    for holding in inputs.holdings:
        original = holding.original
        raw = next(row for row in owned if row["id"] == str(original.position_id))
        immutable = catalogues[str(holding.catalogue_version_id)]
        canonical = immutable["canonical_product"]
        fields = set(AssetProductTerms.model_fields) - {"product_id", "terms_digest"}
        terms = AssetProductTerms.model_validate_json(
            json.dumps(
                {name: canonical[name] for name in fields}
                | {"product_id": raw["product_id"], "terms_digest": immutable["terms_digest"]}
            )
        )
        if (
            terms != original.product
            or immutable["product_id"] != raw["product_id"]
            or immutable["product_hash"] != holding.product_record_hash
            or configuration_hash(canonical) != immutable["product_hash"]
            or products[raw["product_id"]] != canonical
            or configuration_hash(canonical["maturity_rule"]) != terms.terms_digest
            or raw["account_id"] != str(original.account_id)
            or raw["goal_id"] != (str(original.goal_id) if original.goal_id else None)
            or _clock(raw["purchased_at"]) != original.purchased_at
            or (None if raw["maturity_at"] is None else _clock(raw["maturity_at"]))
            != holding.maturity_at
            or (original.original_authorization is None and raw["policy_version_id"] is not None)
            or original.original_authorization is not None
            and str(original.original_authorization.version_id) != raw["policy_version_id"]
        ):
            raise ValueError("RECOVERY_ORIGINAL_POSITION_CATALOGUE_OR_TERMS_DIFFERS")
        if original.original_authorization is not None:
            authorization = original.original_authorization
            saved = next(
                row
                for row in inventory["policy_versions"]
                if row["id"] == str(authorization.version_id)
            )
            if (
                saved["policy_id"] != str(authorization.policy_id)
                or saved["configuration"] != authorization.configuration
                or saved["content_hash"] != authorization.content_hash
                or configuration_hash(authorization.configuration) != authorization.content_hash
                or _clock(saved["confirmed_at"]) != authorization.confirmed_at
                or _clock(saved["valid_from"]) != authorization.valid_from
                or (None if saved["valid_until"] is None else _clock(saved["valid_until"]))
                != authorization.valid_until
            ):
                raise ValueError("RECOVERY_ORIGINAL_ACQUISITION_VERSION_DIFFERS")
        for evidence_id in original.evidence_ids:
            proof = _source(data, evidence_id)
            if (
                proof["source_type"] != "SIMULATED_BANK_POSITION"
                or proof["evidence_level"] != "BANK_CONFIRMED"
                or proof["content"].get("user_id") != str(base.user_id)
                or proof["content"].get("position_id") != raw["id"]
                or any(
                    proof["content"].get(name) != raw[name]
                    for name in (
                        "account_id",
                        "product_id",
                        "goal_id",
                        "policy_version_id",
                        "principal_cents",
                        "status",
                    )
                )
                or _clock(proof["content"]["purchased_at"]) != _clock(raw["purchased_at"])
                or original.acquisition == "MANUAL"
                and (
                    proof["content"].get("acquisition") != "synthetic_user_manual_purchase"
                    or raw["policy_version_id"] is not None
                )
                or _funding_destination(data, raw, proof["content"])
                != original.destination_account_id
            ):
                raise ValueError("RECOVERY_ORIGINAL_POSITION_FUNDING_SOURCE_DIFFERS")
        if len(original.evidence_ids) != 1:
            raise ValueError("RECOVERY_EXACT_BANK_POSITION_PROOF_REQUIRED")
        quote = original.quote
        if quote is not None and holding.quote_source == "BANK_CONFIRMED":
            if len(quote.evidence_ids) != 1:
                raise ValueError("RECOVERY_ORIGINAL_QUOTE_SOURCE_NOT_UNIQUE")
            proof = _source(data, quote.evidence_ids[0])
            content = proof["content"]
            saved_quote = RecoveryQuote.model_validate_json(json.dumps(content["quote"]))
            if (
                proof["source_type"] != "SIMULATED_REDEMPTION_QUOTE"
                or proof["evidence_level"] != "BANK_CONFIRMED"
                or content.get("protocol") != "recovery-quote-v1"
                or content.get("user_id") != str(base.user_id)
                or content.get("position_id") != raw["id"]
                or content.get("destination_account_id") != str(original.destination_account_id)
                or content.get("goal_id") != raw["goal_id"]
                or saved_quote.model_copy(update={"evidence_ids": quote.evidence_ids}) != quote
            ):
                raise ValueError("RECOVERY_EXACT_ORIGINAL_QUOTE_DIFFERS")
        elif quote is not None and holding.quote_source == "DERIVED_ORIGINAL_TERMS":
            if terms.asset_class == "CASH_MGMT_T0" and (
                quote.evidence_ids
                or quote.request_at != base.as_of
                or quote.quote_id
                != uuid5(
                    original.position_id,
                    f"redemption-quote:{terms.terms_digest}:{base.as_of.isoformat()}",
                )
                or quote.expires_at != base.as_of + timedelta(minutes=15)
                or quote.principal_available_at != base.as_of
                or quote.kind != "REDEEM"
                or (quote.principal_cents, quote.net_cents) != (raw["principal_cents"],) * 2
                or quote.fee_cents != 0
                or quote.loss_cents != 0
            ):
                raise ValueError("RECOVERY_DERIVED_T0_ORIGINAL_QUOTE_DIFFERS")
    if (
        response.catalogue.state != "REGISTERED"
        or response.catalogue.issues
        or not response.catalogue.complete_within_registered_capacity
        or response.catalogue.unregistered_product_ids
    ):
        raise ValueError("RECOVERY_COMPLETE_IMMUTABLE_CATALOGUE_MISSING")
    if {str(row.id) for row in response.catalogue.versions} != set(catalogues) or len(
        response.catalogue.versions
    ) != len(catalogues):
        raise ValueError("RECOVERY_COMPLETE_CATALOGUE_DENOMINATOR_DIFFERS")
    for view in response.catalogue.versions:
        saved = catalogues[str(view.id)]
        if (
            not view.current_source_matched
            or saved["product_id"] != str(view.product_id)
            or saved["product_code"] != view.product_code
            or saved["version_number"] != view.version_number
            or saved["canonical_product"] != view.original_product
            or saved["product_hash"] != view.product_hash
            or saved["terms_digest"] != view.terms_digest
        ):
            raise ValueError("RECOVERY_COMPLETE_CATALOGUE_ORIGINAL_VIEW_DIFFERS")


def _history(data: ActualActionSetInput, item: RecoveryOriginalCommand) -> bool:
    """True means unresolved; a status string never proves settlement."""
    if item.missing_reasons or item.prepare_trace is None:
        return True
    inventory, now = data.base.original_inventory, data.base.as_of
    raw = next(row for row in inventory["action_plans"] if row["id"] == str(item.action_id))
    trace = item.prepare_trace
    verify_trace(trace)
    command = BankCommand.model_validate_json(json.dumps(raw["request"]["execution"]))
    context = ExecutionContext.model_validate_json(json.dumps(trace.inputs["execution_context"]))
    original_confirmation = trace.inputs.get("confirmation")
    confirmation = (
        ConfirmationGrant.model_validate_json(json.dumps(original_confirmation))
        if original_confirmation is not None
        else None
    )
    if (
        trace.phase != "PREPARE"
        or trace.user_id != data.base.user_id
        or trace.action_id != item.action_id
        or str(trace.run_id) != raw["decision_run_id"]
        or trace.as_of > now
        or trace.inputs["effect"] != command.effect.model_dump(mode="json")
        or command.effect.operation_id != item.action_id
        or command.effect.user_id != data.base.user_id
        or command.effect_hash != execution_effect_hash(command.effect)
        or raw["request_hash"] != configuration_hash(raw["request"])
        or revalidate_execution(command.effect, context, confirmation=confirmation).model_dump(
            mode="json"
        )
        != trace.outcome["validation"]
    ):
        raise ValueError("RECOVERY_ORIGINAL_COMMAND_TRACE_DIFFERS")
    if MARKER in raw["request"]:
        verify_frozen_full_recovery_trace(trace)
        if trace.inputs["action_request"] != raw["request"]:
            raise ValueError("RECOVERY_ORIGINAL_FULL_COMMAND_DIFFERS")
    operations = [row for row in inventory["bank_operations"] if row["action_plan_id"] == raw["id"]]
    receipts = [row for row in inventory["action_receipts"] if row["action_plan_id"] == raw["id"]]
    claims = [
        row
        for row in inventory["action_resource_reservations"]
        if row["action_plan_id"] == raw["id"] and row["status"] == "RESERVED"
    ]
    redemptions = [
        row for row in inventory["simulated_bank_redemptions"] if row["action_plan_id"] == raw["id"]
    ]
    if raw["status"] in {"SUBMITTED", "UNKNOWN"} or claims:
        return True
    # This reader parsed the exact modern BankCommand above. Any legacy redemption
    # row with this action needs its separate original protocol, never an ignored row.
    if redemptions:
        return True
    if not operations:
        return bool(receipts or redemptions) or raw["status"] not in {
            "PLANNED",
            "AUTHORIZED",
            "INVALIDATED",
            "CANCELLED",
        }
    if (
        len(operations) != 1
        or len(receipts) != 1
        or raw["status"] not in {"SUCCEEDED", "RECONCILED"}
    ):
        return True
    operation = operations[0]
    if operation["status"] != "SETTLED":
        return True
    subjects = [
        build_subject(
            user_id=data.base.user_id,
            epoch_id=data.base.epoch_id,
            kind=kind,
            id=UUID(row["id"]),
            data=row,
        )
        for kind, table in (("TRANSACTION", "transactions"), ("EVIDENCE", "evidence_items"))
        for row in inventory[table]
    ]
    verify_frozen_projection(
        operation,
        raw,
        receipts[0],
        [
            row
            for row in inventory["simulated_bank_postings"]
            if row["operation_id"] == operation["id"]
        ],
        observed_at=now,
        subjects=subjects,
        redemption=redemptions[0] if len(redemptions) == 1 else None,
    )
    return False


def _one(
    data: ActualActionSetInput, item: RecoveryProducerInput, unresolved: list[UUID]
) -> RecoveryProducerResult:
    key = "full-recovery:" + str(item.full_policy_id)
    selected: list[UUID] = []
    unsupported: list[UUID] = []
    authority_excluded: list[UUID] = []
    shadow = None
    try:
        if item.missing_reasons or item.full_policy is None:
            raise ValueError("RECOVERY_ORIGINAL_SOURCE_MISSING:" + ";".join(item.missing_reasons))
        full = item.full_policy
        config = _full(data, full)
        if unresolved:
            raise ValueError("RECOVERY_ORIGINAL_INFLIGHT_RESPONSIBILITY_RETAINED")
        if full.status in {"REVOKED", "SUSPENDED", "EXPIRED"} or (
            full.current_version.valid_until is not None
            and data.base.as_of >= full.current_version.valid_until
        ):
            view = _view(key, "EXCLUDED", "CURRENT_FULL_RECOVERY_POLICY_NOT_ACTIVE")
        else:
            _planning(data, item)
            inputs, response = item.planning_inputs, item.original_planning
            assert inputs is not None and response is not None and response.plan is not None
            plan = response.plan
            if config.goal_id is not None:
                raise ValueError("RECOVERY_GOAL_EXECUTION_FAMILY_NOT_SUPPORTED")
            if plan.status == "NO_RECOVERY_NEEDED":
                view = _view(key, "EXCLUDED", "COMPLETE_ORIGINAL_NO_RECOVERY_NEEDED")
            elif plan.status == "NOT_TRIGGERED":
                view = _view(key, "EXCLUDED", "COMPLETE_ORIGINAL_RECOVERY_NOT_TRIGGERED")
            else:
                # Old _redeem requires an original acquisition version. A fully
                # proven manual purchase with null original version cannot obtain
                # that version from a new planning confirmation or current policy.
                authority_excluded = [
                    row.original.position_id
                    for row in inputs.holdings
                    if row.original.acquisition == "MANUAL"
                    and row.original.original_authorization is None
                ]
                unsupported = [
                    row.position_id
                    for row in plan.candidates
                    if row.decision != "EXCLUDED_SCOPE"
                    and row.position_id not in authority_excluded
                    and (
                        row.original_quote is None
                        or row.original_quote.kind != "REDEEM"
                        or row.liquidity_rank != 0
                        or row.fee_cents != 0
                        or row.independent_loss_cents != 0
                    )
                ]
                selected = [row.position_id for row in plan.lossless_steps]
                if unsupported:
                    raise ValueError("RECOVERY_T1_MATURITY_PARTIAL_LOSS_OR_UNKNOWN_UNSUPPORTED")
                if len(selected) != 1:
                    raise ValueError("RECOVERY_COMBINATION_OR_EMPTY_SELECTION_NOT_SUPPORTED")
                execution, candidate = item.execution_inputs, item.candidate
                if execution is None or candidate is None or candidate.facts is None:
                    raise ValueError("RECOVERY_ACTUAL_EXECUTION_PREVIEW_MISSING")
                if (
                    execution.planning_input_hash != plan.input_hash
                    or execution.planning_response_hash != response.input_hash
                    or execution.candidate
                    != next(row for row in plan.candidates if row.position_id == selected[0])
                    or execution.selected_position_ids != selected
                    or execution.candidate_position_ids
                    != [row.position_id for row in plan.candidates]
                    or execution.deadline_at != plan.deadline_at
                    or execution.full_policy_id != full.policy_id
                    or execution.full_policy_version_id != full.current_version.version_id
                    or execution.as_of != data.base.as_of
                    or execution.user_id != data.base.user_id
                    or execution.epoch_id != data.base.epoch_id
                    or execution.full_configuration != config
                    or execution.full_configuration_hash != full.current_version.content_hash
                    or execution.reference_validation != full.reference_validation
                    or execution.full_effective_status != full.effective_status
                    or execution.planning_confirmation_valid != full.planning_confirmation_valid
                    or execution.confirmed_at != full.current_version.confirmed_at
                    or execution.valid_from != full.current_version.valid_from
                    or execution.valid_until != full.current_version.valid_until
                    or execution.linked_asset_policy_id != inputs.linked_asset_policy.policy_id
                    or execution.linked_asset_policy_version_id
                    != inputs.linked_asset_policy.version_id
                    or execution.linked_asset_configuration_hash
                    != inputs.linked_asset_policy.content_hash
                    or not _original_candidate_binding(data.base, candidate)
                ):
                    raise ValueError("RECOVERY_COMPLETE_EXECUTION_PLAN_BINDING_DIFFERS")
                for ref in execution.source_refs:
                    proof = _source(data, ref.evidence_id)
                    if (
                        proof["content_hash"] != ref.content_hash
                        or ref.user_id != data.base.user_id
                    ):
                        raise ValueError("RECOVERY_EXECUTION_SOURCE_HASH_DIFFERS")
                effect, _ = build_full_recovery_effect(execution)
                facts, context = candidate.facts, candidate.execution_context
                if (
                    facts.effect != effect
                    or context is None
                    or not context.requires_confirmation
                    or facts.confirmation is not None
                    or candidate.full_protection is None
                    or context.redemption_quote != execution.candidate.original_quote
                    or context.boundary_products != inputs.boundary_products
                ):
                    raise ValueError("RECOVERY_FRESH_EXACT_ASK_OR_FULL_PROTECTION_MISSING")
                inventory = data.base.original_inventory
                protected_ids = {
                    row["id"]
                    for row in inventory["full_policies"]
                    if row["epoch_id"] == str(data.base.epoch_id)
                    and row["template_name"]
                    in {"DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"}
                }
                if {str(row.policy_id) for row in candidate.full_sources} != protected_ids:
                    raise ValueError("RECOVERY_COMPLETE_FULL_PROTECTION_SOURCE_DENOMINATOR_DIFFERS")
                for source in candidate.full_sources:
                    current = max(
                        (
                            row
                            for row in inventory["full_policy_versions"]
                            if row["policy_id"] == str(source.policy_id)
                        ),
                        key=lambda row: row["version_number"],
                    )
                    if (
                        str(source.version_id) != current["id"]
                        or source.configuration != current["configuration"]
                        or source.content_hash != current["content_hash"]
                        or source.confirmation != current["confirmation"]
                    ):
                        raise ValueError("RECOVERY_CURRENT_FULL_PROTECTION_SOURCE_DIFFERS")
                view = candidate_view(candidate, data.base.user_id, data.base.as_of).model_copy(
                    update={"candidate_key": key}
                )
                if view.state == "INCLUDED" and view.autonomy_level != "ASK_ONCE":
                    raise ValueError("RECOVERY_NEW_USER_CONFIRMATION_CANNOT_BE_INHERITED")
                # Different operation identity alone is not an economic change, but any
                # other scope/terms/arrival/amount field must be exactly the same.
                old = [
                    row
                    for row in data.base.candidates
                    if row.candidate_key == candidate.candidate_key
                ]
                if len(old) == 1 and old[0].facts is not None and old[0].facts.effect is not None:
                    if old[0].facts.effect.model_dump(
                        exclude={"operation_id"}
                    ) == effect.model_dump(exclude={"operation_id"}):
                        shadow = candidate.candidate_key
    except (ValueError, TypeError, KeyError, StopIteration, OverflowError) as error:
        view = _view(key, "UNKNOWN", str(error))
    return RecoveryProducerResult(
        candidate_key=key,
        full_policy_id=item.full_policy_id,
        full_policy_version_id=item.full_policy.current_version.version_id
        if item.full_policy
        else None,
        view=view,
        selected_position_ids=selected,
        unsupported_position_ids=unsupported,
        authority_excluded_position_ids=authority_excluded,
        unresolved_original_action_ids=unresolved,
        shadow_original_candidate_key=shadow,
    )


def derive_recovery_producers(supplied: RecoveryActionSetInput) -> RecoveryActionSetResult:
    data = RecoveryActionSetInput.model_validate(supplied.model_dump())
    actual, original = (
        data.original_actual_input,
        derive_actual_action_set(data.original_actual_input),
    )
    reasons = [*data.source_reasons, *_structure(actual)]
    unresolved: list[UUID] = []
    expected = data.expected_full_policy_ids
    try:
        expected = recovery_policy_ids(actual)
        positions = sorted(
            (UUID(row["id"]) for row in actual.base.original_inventory["asset_positions"]), key=str
        )
        commands = recovery_action_ids(actual)
        if (
            expected != data.expected_full_policy_ids
            or len(data.producers) != len(expected)
            or {row.full_policy_id for row in data.producers} != set(expected)
        ):
            raise ValueError("RECOVERY_FULL_POLICY_DENOMINATOR_DIFFERS")
        if (
            positions != data.original_position_ids
            or commands != data.original_action_ids
            or len(data.original_commands) != len(commands)
            or {row.action_id for row in data.original_commands} != set(commands)
        ):
            raise ValueError("RECOVERY_ALL_POSITION_OR_COMMAND_DENOMINATOR_DIFFERS")
        for row in actual.base.original_inventory["bank_operations"]:
            if (
                row["operation_type"]
                in {"ASSET_REDEEM", "ASSET_MATURITY", "REDEEM_ASSET", "REDEEM_PRINCIPAL"}
                and UUID(row["action_plan_id"]) not in commands
            ):
                raise ValueError("RECOVERY_ORIGINAL_BANK_WITHOUT_CAPTURED_ACTION")
        for row in actual.base.original_inventory["simulated_bank_redemptions"]:
            if UUID(row["action_plan_id"]) not in commands:
                raise ValueError("RECOVERY_ORIGINAL_REDEMPTION_WITHOUT_CAPTURED_ACTION")
        for item in data.original_commands:
            try:
                if _history(actual, item):
                    unresolved.append(item.action_id)
            except (ValueError, TypeError, KeyError, StopIteration):
                unresolved.append(item.action_id)
        if unresolved:
            reasons.append("RECOVERY_ORIGINAL_INFLIGHT_OR_UNVERIFIED_HISTORY_RETAINED")
        results = [_one(actual, item, sorted(unresolved, key=str)) for item in data.producers]
    except (ValueError, TypeError, KeyError) as error:
        results = []
        reasons.append("RECOVERY_ORIGINAL_DENOMINATOR_INVALID:" + str(error))
    if any(row.view.state == "UNKNOWN" for row in results):
        reasons.append("RECOVERY_CURRENT_PRODUCER_UNKNOWN")
    shadows = [
        row.shadow_original_candidate_key for row in results if row.shadow_original_candidate_key
    ]
    if len(shadows) != len(set(shadows)):
        reasons.append("RECOVERY_ORIGINAL_SHADOW_NOT_UNIQUE")
    if len(expected) > MAX_PRODUCERS or len(data.model_dump_json().encode()) > MAX_BYTES:
        reasons.append("RECOVERY_CAPTURE_CAPACITY_EXCEEDED")
    complete = not reasons
    handled = sorted(
        "FULL_PRODUCER_ADAPTER_MISSING:RecoveryPolicy:" + str(row.full_policy_id)
        for row in results
        if complete
    )
    result = RecoveryActionSetResult(
        user_id=actual.base.user_id,
        epoch_id=actual.base.epoch_id,
        as_of=actual.base.as_of,
        status="COMPLETE_REGISTERED_RECOVERY_FAMILY" if complete else "UNKNOWN",
        recovery_family_complete=complete,
        original_actual_input_hash=original.input_hash,
        input_hash=configuration_hash(data.model_dump(mode="json")),
        result_hash="0" * 64,
        expected_full_policy_ids=expected,
        original_position_ids=data.original_position_ids,
        original_action_ids=data.original_action_ids,
        unresolved_original_action_ids=sorted(unresolved, key=str),
        results=results,
        handled_unsupported_codes=handled,
        original_actual_reasons=original.reasons,
        remaining_unsupported_producers=sorted(set(original.unsupported_producers) - set(handled)),
        reasons=sorted(set(reasons)),
    )
    return result.model_copy(
        update={
            "result_hash": configuration_hash(
                result.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )


def verify_frozen_recovery_producer_inputs(raw: dict[str, Any]) -> RecoveryActionSetResult:
    """Exact new typed math; outer original trace/audit/source verification is also required."""
    return derive_recovery_producers(RecoveryActionSetInput.model_validate_json(json.dumps(raw)))
