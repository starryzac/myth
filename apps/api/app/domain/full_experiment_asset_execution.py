"""Private DEVELOPMENT single selected Effect; never an old P-optimal portfolio."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

from app.domain.boundary_types import BoundaryModel, BoundaryResult
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand, CashUse, ExecutionContext, ExecutionEffect
from app.domain.full_asset_allocation import FullAssetPlanningInput
from app.domain.full_asset_execution import FullAssetCatalogueReference, FullAssetPrepareRequest
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_experiment_asset_selection import (
    FullExperimentAssetSelection,
    FullMechanismRuleOriginal,
    RegisteredFullMechanismRule,
    select_current_general_purchase,
)
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.domain.income_ledger import IncomeUse
from app.domain.policy_configuration import configuration_hash, validate_configuration
from pydantic import Field, StrictBool, StrictStr

ALGORITHM: Literal["full-experiment-asset-execution-v1"] = "full-experiment-asset-execution-v1"
MARKER = "full_experiment_asset_execution"
GUARDS_VERSION = "full-experiment-asset-guards-v1"
KEY_PREFIX = "action:full-experiment-asset:"
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class FullExperimentAssetRequest(BoundaryModel):
    """Trusted Python parameter only. It must never enter a public HTTP DTO."""

    original_request: FullAssetPrepareRequest
    rule_original: RegisteredFullMechanismRule


class FullExperimentAssetExecutionInput(BoundaryModel):
    purpose: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    request: FullExperimentAssetRequest
    original_selection: FullExperimentAssetSelection
    rule: FullMechanismRuleOriginal
    rule_original_utf8: str
    current_planning: FullAssetPlanningInput
    current_curve: BoundaryResult
    context: ExecutionContext
    catalogue: list[FullAssetCatalogueReference]
    position_account_id: UUID
    income_uses: list[IncomeUse]
    expires_at: datetime
    protection_sources: list[FullProtectionPolicySource]
    protection_inventory_complete: StrictBool
    source_issues: list[str]
    source_originals: list[TraceEvidence]
    current_originals: dict[str, Any]
    own_action_id: UUID | None = None


class FullExperimentAssetExecutionProof(BoundaryModel):
    protocol: Literal["full-experiment-asset-execution-v1"] = ALGORITHM
    purpose: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    effect_is_original: Literal[True] = True
    receipt_is_current_authority: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    as_of: datetime
    status: Literal["VERIFIED_SCOPE"] = "VERIFIED_SCOPE"
    input_hash: Hash
    effect_hash: Hash
    current_source_hash: Hash
    original_selection_hash: Hash
    current_mechanism: dict[str, Any]
    current_product_denominator: int
    original_execution_validation: dict[str, Any]
    current_full_protection: dict[str, Any]
    proof_hash: Hash


def experiment_asset_bank_key(original_key: str) -> str:
    if (
        type(original_key) is not str
        or not 1 <= len(original_key) <= 160
        or not original_key.strip()
    ):
        raise ValueError("An exact original registered key is required")
    return KEY_PREFIX + configuration_hash({"key": original_key})


def _original_selected(data: FullExperimentAssetExecutionInput) -> None:
    original = FullExperimentAssetSelection.model_validate_json(
        data.original_selection.model_dump_json()
    )
    if (
        original.status != "PROPOSED"
        or original.selected_portfolio is None
        or original.planning_input is None
        or original.mechanism_input is None
        or original.original_request != data.request.original_request
        or original.user_id != data.user_id
        or original.epoch_id != data.epoch_id
        or original.rule_original != data.rule
        or original.rule_original_sha256 != data.request.rule_original.sha256
        or original.source_originals["rule_original_utf8"] != data.rule_original_utf8
    ):
        raise ValueError("The original complete actual selection is missing or rebound")
    protection = original.source_originals["full_protection"]
    mvp = original.source_originals["mvp_version"]
    if not isinstance(protection, dict) or not isinstance(mvp, dict):
        raise ValueError("Complete original FULL curve/MVP sources are required")
    curve = BoundaryResult.model_validate_json(
        json.dumps(protection["projection"]["full_annual_projection"])
    )
    bindings = [row.catalogue for row in original.product_originals]
    inputs, decision, products, batch = select_current_general_purchase(
        original.planning_input,
        curve,
        mvp["configuration"],
        bindings,
        original.rule_original,
        original.rule_original_sha256,
        original.current_source_hash,
    )
    if (
        inputs != original.mechanism_input
        or decision != original.mechanism_decision
        or products != original.product_originals
        or batch != original.selected_portfolio.batch
    ):
        raise ValueError("The original registered mechanism cannot independently reproduce")


def build_full_experiment_asset_effect(
    supplied: FullExperimentAssetExecutionInput,
    action_id: UUID,
    original_effect: ExecutionEffect | None = None,
) -> tuple[ExecutionEffect, FullExperimentAssetExecutionProof]:
    data = FullExperimentAssetExecutionInput.model_validate_json(supplied.model_dump_json())
    body, plan, context, now = (
        data.request.original_request,
        data.current_planning,
        data.context,
        data.as_of,
    )
    _original_selected(data)
    if (
        hashlib.sha256(data.rule_original_utf8.encode("utf-8")).hexdigest()
        != data.request.rule_original.sha256
        or FullMechanismRuleOriginal.model_validate_json(data.rule_original_utf8) != data.rule
        or data.rule.original_request != body
        or data.rule.user_id != data.user_id
        or data.epoch_id != body.expected_epoch_id
        or body.goal_id is not None
        or body.planning_mode != "PORTFOLIO"
        or plan.configuration.goal_id is not None
        or plan.policy_id != body.full_policy_id
        or plan.policy_version_id != body.expected_full_policy_version_id
        or context.user_id != data.user_id
        or plan.snapshot != context.snapshot
        or plan.positions != context.positions
        or plan.boundary_versions != context.versions
        or plan.boundary_products != context.boundary_products
        or plan.exposure != context.exposure
        or now != context.snapshot.as_of
        or not plan.planning_confirmation_valid
        or not max(plan.confirmed_at, plan.valid_from) <= now
        or (plan.valid_until is not None and now >= plan.valid_until)
        or data.source_issues
        or context.source_issues
        or not data.protection_inventory_complete
        or data.own_action_id not in {None, action_id}
        or not data.source_originals
        or len({row.id for row in data.source_originals}) != len(data.source_originals)
        or any(
            row.user_id != data.user_id
            or row.content_integrity != "VERIFIED"
            or row.status_at_decision != "VALID"
            or configuration_hash(row.content) != row.content_hash
            or row.captured_content_hash != row.content_hash
            or max(row.observed_at, row.valid_from) > now
            or (row.valid_to is not None and now >= row.valid_to)
            for row in data.source_originals
        )
    ):
        raise ValueError("Exact current complete private GENERAL sources are required")
    mvp = next(
        (v for v in context.versions if v.version_id == body.expected_mvp_policy_version_id), None
    )
    if mvp is None or mvp.policy_id != body.mvp_asset_policy_id:
        raise ValueError("The exact current original MVP permission is required")
    config = validate_configuration(mvp.configuration)
    if (
        configuration_hash(config) != mvp.content_hash
        or config["type"] != "asset_authorization"
        or config["scope"] != "general_idle_funds"
        or not max(mvp.confirmed_at, mvp.valid_from) <= now
        or (mvp.valid_until is not None and now >= mvp.valid_until)
    ):
        raise ValueError("The current MVP scope/window/hash is not verified")
    projection_input = FullProtectionProjectionInput(
        snapshot=context.snapshot,
        positions=context.positions,
        boundary_versions=context.versions,
        boundary_products=context.boundary_products,
        policies=data.protection_sources,
        reserved_cash_by_account=context.reserved_cash_by_account,
    )
    current = project_full_protection(projection_input)
    if (
        not current.full_obligations_complete_within_registered_current_scope
        or current.full_annual_projection != data.current_curve
        or len(data.current_curve.calculation_trace) != 1098
    ):
        raise ValueError("The complete current 1098-point FULL curve cannot reproduce")
    source_hash = configuration_hash(
        {
            "protocol": ALGORITHM,
            "user_id": str(data.user_id),
            "epoch_id": str(data.epoch_id),
            "as_of": now.isoformat(),
            "originals": data.current_originals,
        }
    )
    mechanism, decision, products, batch = select_current_general_purchase(
        plan,
        data.current_curve,
        config,
        data.catalogue,
        data.rule,
        data.request.rule_original.sha256,
        source_hash,
    )
    if decision.status != "PROPOSAL" or batch is None:
        raise ValueError("The current registered mechanism has no executable candidate")
    if (
        batch.amount_cents
        > min(plan.configuration.single_action_cap_cents, config["single_action_cap_cents"])
        or context.exposure is None
        or context.exposure.managed_principal_cents
        + context.exposure.pending_purchase_cents
        + batch.amount_cents
        > min(plan.configuration.max_auto_managed_cents, config["max_auto_managed_cents"])
    ):
        raise ValueError("Actual FULL and MVP caps remain mandatory for every arm")
    if original_effect is None:
        if data.own_action_id is not None or not now < data.expires_at <= now + timedelta(
            minutes=15
        ):
            raise ValueError("A new original effect has one bounded actual preparation window")
        effect = ExecutionEffect(
            operation_id=action_id,
            user_id=data.user_id,
            business_key="purchase:" + str(action_id),
            action_type="PURCHASE_ASSET",
            amount_cents=batch.amount_cents,
            cash_uses=[
                CashUse(account_id=r.account_id, amount_cents=r.amount_cents)
                for r in batch.cash_uses
            ],
            income_uses=data.income_uses,
            policy_id=body.mvp_asset_policy_id,
            policy_version_id=body.expected_mvp_policy_version_id,
            policy_version_ids=[body.expected_mvp_policy_version_id],
            product_id=batch.product_id,
            product_version_number=batch.version_number,
            terms_digest=batch.terms_digest,
            position_id=uuid5(action_id, "position"),
            position_account_id=data.position_account_id,
            return_account_id=batch.cash_uses[0].account_id,
            purchase_exit=batch.exit_plan,
            latest_arrival_at=batch.principal_available_at + (data.expires_at - now),
            valid_from=now,
            expires_at=data.expires_at,
        )
    else:
        effect = ExecutionEffect.model_validate_json(original_effect.model_dump_json())
        if (
            effect.operation_id != action_id
            or effect.user_id != data.user_id
            or effect.action_type != "PURCHASE_ASSET"
            or effect.goal_id is not None
            or effect.amount_cents != batch.amount_cents
            or effect.product_id != batch.product_id
            or effect.product_version_number != batch.version_number
            or effect.terms_digest != batch.terms_digest
            or effect.position_account_id != data.position_account_id
            or effect.policy_id != body.mvp_asset_policy_id
            or effect.policy_version_id != body.expected_mvp_policy_version_id
            or effect.policy_version_ids != [body.expected_mvp_policy_version_id]
            or effect.income_uses != data.income_uses
            or effect.expires_at != data.expires_at
            or effect.cash_uses
            != [
                CashUse(account_id=r.account_id, amount_cents=r.amount_cents)
                for r in batch.cash_uses
            ]
            or not effect.valid_from <= now < effect.expires_at
        ):
            raise ValueError("Fresh selection must verify the exact old effect, never replace it")
    required = context.model_copy(update={"requires_confirmation": True})
    validation = revalidate_execution(effect, required)
    if validation.status not in {"READY", "CONFIRMATION_REQUIRED"}:
        raise ValueError(
            "Original financial/MVP/income checks reject:" + ",".join(validation.reasons)
        )
    bounds = None
    if validation.projected_snapshot is not None:
        projected_input = projection_input.model_copy(
            update={
                "snapshot": validation.projected_snapshot,
                "positions": validation.projected_positions,
            }
        )
        projected = project_full_protection(projected_input)
        if projected.source_account_checks:
            bounds = derive_full_account_debit_bounds(
                effect, required, validation, projected_input, projected
            )
    protection = validate_full_execution_protection(
        effect, required, validation, data.protection_sources, account_debit_bounds=bounds
    )
    if protection.status not in {"PASSED", "NO_ADDITIONAL_POLICY"}:
        raise ValueError("Current FULL protection veto:" + ",".join(protection.reasons))
    values = {
        "user_id": data.user_id,
        "epoch_id": data.epoch_id,
        "action_id": action_id,
        "as_of": now,
        "input_hash": configuration_hash(data.model_dump(mode="json")),
        "effect_hash": execution_effect_hash(effect),
        "current_source_hash": source_hash,
        "original_selection_hash": configuration_hash(
            data.original_selection.model_dump(mode="json")
        ),
        "current_mechanism": {
            "inputs": mechanism.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "products": [r.model_dump(mode="json") for r in products],
        },
        "current_product_denominator": len(products),
        "original_execution_validation": validation.model_dump(mode="json"),
        "current_full_protection": protection.model_dump(mode="json"),
    }
    proof = FullExperimentAssetExecutionProof.model_validate({**values, "proof_hash": "0" * 64})
    return effect, proof.model_copy(
        update={
            "proof_hash": configuration_hash(proof.model_dump(mode="json", exclude={"proof_hash"}))
        }
    )


def read_frozen_full_experiment_asset_proof(
    trace: DecisionTrace,
) -> FullExperimentAssetExecutionProof:
    """Pure historical algorithm; never reads current files or database/parent traces."""
    verify_trace(trace)
    try:
        if trace.algorithm_versions.get(MARKER) != ALGORITHM or trace.action_id is None:
            raise ValueError("Exact private experimental algorithm/action is required")
        saved = trace.inputs["planning"][MARKER]
        if set(saved) != {"inputs", "proof"}:
            raise ValueError("Complete original private proof input is required")
        data = FullExperimentAssetExecutionInput.model_validate_json(json.dumps(saved["inputs"]))
        command = BankCommand.model_validate_json(
            json.dumps(trace.inputs["action_request"]["execution"])
        )
        _, proof = build_full_experiment_asset_effect(data, trace.action_id, command.effect)
        marker = trace.inputs["action_request"][MARKER]
        if set(marker) != {
            "protocol",
            "purpose",
            "user_id",
            "epoch_id",
            "request",
            "request_hash",
            "effect_hash",
            "original_proof",
        }:
            raise ValueError("Closed exact original private marker is required")
        original_proof = FullExperimentAssetExecutionProof.model_validate_json(
            json.dumps(marker["original_proof"])
        )
        if (
            proof.model_dump(mode="json") != saved["proof"]
            or trace.user_id != data.user_id
            or trace.as_of != data.as_of
            or marker["protocol"] != ALGORITHM
            or marker["purpose"] != "DEVELOPMENT"
            or marker["user_id"] != str(data.user_id)
            or marker["epoch_id"] != str(data.epoch_id)
            or marker["request"] != data.request.model_dump(mode="json")
            or marker["request_hash"] != configuration_hash(data.request.model_dump(mode="json"))
            or marker["effect_hash"] != command.effect_hash
            or original_proof.user_id != data.user_id
            or original_proof.epoch_id != data.epoch_id
            or original_proof.action_id != trace.action_id
            or original_proof.effect_hash != command.effect_hash
            or original_proof.original_selection_hash != proof.original_selection_hash
            or original_proof.as_of != command.effect.valid_from
            or any(
                {row.id: row for row in trace.sources}.get(source.id) != source
                for source in data.source_originals
            )
            or (
                trace.phase == "PREPARE"
                and (
                    original_proof != proof
                    or trace.parent_run_id is not None
                    or trace.run_id != uuid5(trace.action_id, "decision")
                )
            )
        ):
            raise ValueError("The entire original private effect/proof cannot reproduce")
        return proof
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("Private frozen financial source is missing") from error
