"""Actual current RR/RO two-original-mode residual proofs; no optimizer or writes."""

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.autonomy import classify_autonomy
from app.domain.full_asset_mode_contract import PROTOCOL, AssetModeEvaluation, AssetVectorChild
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_asset_residual import (
    AssetResidualEvaluation,
    ResidualModeInput,
    evaluate_residual_modes,
)
from app.domain.standing_owned_asset_read import AcceptedAssetReadProof
from app.services.action_contracts import PurchaseIntent
from app.services.autonomy import _basis
from app.services.decision_recording import start_capture
from app.services.execution_context import load_execution_context
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.product_catalog import verified_catalog_products
from app.services.question_workflow import source_fingerprint
from app.services.standing_owned_asset_scope import current_owned_scope
from app.services.zhiyu_orchestration import _audit_healthy
from sqlalchemy.orm import Session


def read_current_residual_modes(
    session: Session,
    user: UUID,
    accepted: AssetModeEvaluation,
    owned: AcceptedAssetReadProof,
    now: datetime,
) -> tuple[AssetResidualEvaluation, dict[str, Any]]:
    from app.services.zhiyu_standing_snapshot import owned_native_facts

    _read_snapshot(session)
    _audit_healthy(session, user)
    if current_owned_scope(session, user, now) != owned:
        raise ValueError("Original accepted whole read scope is required")
    accepted = AssetModeEvaluation.model_validate_json(accepted.model_dump_json())
    capture = start_capture(session)
    _basis(session, user, now)
    protection = compute_full_annual_protection(session, user, now)
    catalogue = verified_catalog_products(session, now)
    if (
        not protection.projection.full_obligations_complete_within_registered_current_scope
        or catalogue.status != "VERIFIED"
        or catalogue.issues
    ):
        raise ValueError("Complete current native protection and immutable catalogue required")
    inputs = []
    full = None
    for world in accepted.worlds:
        portfolio = world.native_preview.portfolio
        if portfolio is None or len(portfolio.batches) <= owned.settled_prefix:
            raise ValueError("Exact original finite world and remaining suffix required")
        request = portfolio.original_request
        full = read_full_policy(session, user, request.full_policy_id, now)
        suffix = portfolio.batches[owned.settled_prefix :]
        context = load_execution_context(
            session, user, suffix[0].command.effect, now, own_action_id=None
        )
        version = next(
            (
                row
                for row in context.versions
                if row.version_id == request.expected_mvp_policy_version_id
            ),
            None,
        )
        if version is None:
            raise ValueError("Current original native MVP asset permission is missing")
        permission = {
            "full_policy_id": str(full.policy_id),
            "full_policy_version_id": str(full.current_version.version_id),
            "full_policy_content_hash": full.current_version.content_hash,
            "full_configuration": full.current_version.configuration,
            "mvp_asset_policy_id": str(version.policy_id),
            "mvp_asset_policy_version_id": str(version.version_id),
            "mvp_configuration_hash": configuration_hash(version.configuration),
            "goal_id": str(request.goal_id),
            "goal_policy_version_id": str(request.expected_goal_policy_version_id),
            "whole_confirmation_required": True,
            "old_confirmation_inherited": False,
            "bank_authority": False,
            "cross_operation_atomicity": "NOT_AVAILABLE",
        }
        children = []
        for batch in suffix:
            facts = owned_native_facts(
                session,
                user,
                PurchaseIntent(kind="purchase_asset", policy_id=request.mvp_asset_policy_id),
                batch.command.effect,
                context,
                now,
            )
            children.append(
                AssetVectorChild(
                    batch_number=batch.batch_number, facts=facts, decision=classify_autonomy(facts)
                )
            )
        inputs.append(
            ResidualModeInput(
                original_world=world,
                current_context=context,
                current_full_content_hash=full.current_version.content_hash,
                current_permission_boundary=permission,
                current_children=children,
                full_sources=protection.full_policy_sources,
                full_source_issues=[row.code for row in protection.source_issues],
                full_inventory_complete=True,
                settled_prefix=owned.settled_prefix,
                actual_prefix_effects=[
                    effect.model_dump(mode="json")
                    for effect in owned.effects[: owned.settled_prefix]
                ],
                as_of=now,
            )
        )
    if full is None:
        raise ValueError("Both original full modes required")
    evaluation = evaluate_residual_modes(accepted, inputs)
    manifest = {
        "protocol": PROTOCOL,
        "user_id": str(user),
        "epoch_id": str(owned.epoch_id),
        "shanghai_day": now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "complete_native_basis": deepcopy(capture.inputs["autonomy_basis"]),
        "native_source_fingerprint": source_fingerprint(
            {"autonomy_basis": capture.inputs["autonomy_basis"]},
            list(capture.sources.values()),
            list(capture.policies.values()),
        ),
        "original_sources": [
            row.model_dump(mode="json")
            for row in sorted(capture.sources.values(), key=lambda item: item.id)
        ],
        "original_policies": [
            row.model_dump(mode="json")
            for row in sorted(capture.policies.values(), key=lambda item: item.id)
        ],
        "full_policy": full.model_dump(mode="json"),
        "catalogue": catalogue.model_dump(mode="json"),
        "full_protection_sources": [
            row.model_dump(mode="json") for row in protection.full_policy_sources
        ],
        "current_world_signatures": [world.remaining_signature for world in evaluation.worlds],
        "current_world_status": [world.status for world in evaluation.worlds],
    }
    return evaluation, manifest
