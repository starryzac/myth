"""Current proof over the two original finite asset vectors, never a new optimum."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from app.domain.autonomy import classify_autonomy
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext, ExecutionEffect
from app.domain.full_asset_execution_guard import (
    FullAssetRemainingCheck,
    validate_remaining_combination,
)
from app.domain.full_asset_mode_contract import (
    MODES,
    AssetModeEvaluation,
    AssetModeWorld,
    AssetVectorChild,
    canonical_effect,
)
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt, model_validator

PROTOCOL: Literal["accepted-finite-asset-mode-residual-v1"] = (
    "accepted-finite-asset-mode-residual-v1"
)


class ResidualModeInput(BoundaryModel):
    original_world: AssetModeWorld
    current_context: ExecutionContext
    current_full_content_hash: str
    current_permission_boundary: dict[str, Any]
    current_children: Annotated[list[AssetVectorChild], Field(min_length=1, max_length=4)]
    full_sources: list[FullProtectionPolicySource]
    full_source_issues: list[str]
    full_inventory_complete: Literal[True]
    # Original prefixes are compared against the actually settled selected world.
    # No position is removed from current_context to produce this proof.
    settled_prefix: Annotated[StrictInt, Field(ge=0, le=3)]
    actual_prefix_effects: list[dict[str, Any]]
    as_of: datetime
    bank_authority: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False


class ResidualModeWorld(BoundaryModel):
    input: ResidualModeInput
    status: Literal["KNOWN", "UNKNOWN"]
    remaining_check: FullAssetRemainingCheck | None
    remaining_signature: str | None
    reasons: list[str]
    bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def replay(self) -> Self:
        expected = derive_residual_world(self.input, _validate=False)
        if self.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise ValueError("Current fixed remaining mode proof differs")
        return self


class AssetResidualEvaluation(BoundaryModel):
    protocol: Literal["accepted-finite-asset-mode-residual-v1"] = PROTOCOL
    accepted_evaluation: AssetModeEvaluation
    worlds: Annotated[list[ResidualModeWorld], Field(min_length=2, max_length=2)]
    status: Literal["STABLE", "DIVERGENT", "UNKNOWN"]
    stable: bool | None
    bank_authority: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False
    new_optimizer_used: Literal[False] = False
    settled_positions_removed_from_context: Literal[False] = False

    @model_validator(mode="after")
    def finite_domain(self) -> Self:
        if (
            self.accepted_evaluation.selected_mode is not None
            or self.accepted_evaluation.status != "STABLE"
            or [world.input.original_world for world in self.worlds]
            != self.accepted_evaluation.worlds
            or [world.input.original_world.mode for world in self.worlds] != list(MODES)
            or len({world.input.settled_prefix for world in self.worlds}) != 1
            or len({world.input.as_of for world in self.worlds}) != 1
        ):
            raise ValueError("Exact independently accepted two-mode finite domain required")
        unknown = any(world.status != "KNOWN" for world in self.worlds)
        signatures = {world.remaining_signature for world in self.worlds}
        status = "UNKNOWN" if unknown else "STABLE" if len(signatures) == 1 else "DIVERGENT"
        if self.status != status or self.stable != (None if unknown else status == "STABLE"):
            raise ValueError("Current residual finite stability differs")
        return self


def validation_signature(child: AssetVectorChild) -> dict[str, Any] | None:
    validation = child.facts.validation
    if validation is None:
        return None
    return {
        "status": validation.status,
        "reasons": validation.reasons,
        "projected_boundary_status": validation.projected_boundary.status
        if validation.projected_boundary
        else None,
    }


def derive_residual_world(value: ResidualModeInput, *, _validate: bool = True) -> ResidualModeWorld:
    original = AssetModeWorld.model_validate_json(value.original_world.model_dump_json())
    portfolio = original.native_preview.portfolio
    reasons: set[str] = set()
    check = None
    signature = None
    try:
        if portfolio is None or original.status != "KNOWN":
            raise ValueError("Original full native candidate was not known")
        prefix = value.settled_prefix
        suffix = portfolio.batches[prefix:]
        if (
            prefix >= len(portfolio.batches)
            or value.as_of != value.current_context.snapshot.as_of
            or value.current_permission_boundary != original.permission_boundary
            or len(value.actual_prefix_effects) != prefix
            or len(value.current_children) != len(suffix)
        ):
            raise ValueError("Exact current permission, clock and ordered denominator required")
        for child, raw in zip(
            original.ordered_children[:prefix], value.actual_prefix_effects, strict=True
        ):
            # Only generated purchase labels may differ between the original
            # two candidate worlds. Their ownership/product/terms stay exact.
            actual = child.model_copy(
                update={
                    "facts": child.facts.model_copy(
                        update={"effect": ExecutionEffect.model_validate_json(json.dumps(raw))}
                    )
                }
            )
            if canonical_effect(child) != canonical_effect(actual):
                raise ValueError("Actual settled prefix differs from this complete finite world")
            held = [
                position
                for position in value.current_context.positions
                if str(position.position_id) == raw["position_id"]
            ]
            if (
                len(held) != 1
                or held[0].principal_cents != raw["amount_cents"]
                or str(held[0].goal_id) != str(raw["goal_id"])
                or held[0].status not in {"HELD", "MATURED"}
            ):
                raise ValueError("Every settled prefix position remains in the current context")
        for child, batch in zip(value.current_children, suffix, strict=True):
            facts = child.facts
            validation = revalidate_execution(batch.command.effect, value.current_context)
            if (
                child.batch_number != batch.batch_number
                or facts.effect != batch.command.effect
                or facts.as_of != value.as_of
                or facts.confirmation is not None
                or facts.validation != validation
                or validation.status not in {"READY", "CONFIRMATION_REQUIRED"}
                or facts.authority.status != "AUTHORIZED"
                or not facts.authority.evidence_ids
                or not facts.source_evidence_ids
                or facts.source_issues
                or facts.hard_block_reasons
                or child.decision != classify_autonomy(facts)
                or child.decision.confirmation_satisfied
                or child.decision.financial_evaluation != "VERIFIED"
            ):
                raise ValueError("Each original remaining child needs current native facts")
        check = validate_remaining_combination(
            portfolio,
            value.current_context,
            [batch.batch_number for batch in suffix],
            value.as_of,
            current_full_configuration_hash=value.current_full_content_hash,
            full_sources=value.full_sources,
            full_source_issues=value.full_source_issues,
            full_inventory_complete=value.full_inventory_complete,
        )
        if check.checked_point_count != 1098:
            raise ValueError("Every current residual world retains all 1098 native points")
        signature = configuration_hash(
            {
                "protocol": PROTOCOL,
                "settled_prefix": prefix,
                "current_permission_boundary": value.current_permission_boundary,
                "original_remaining_numbers": check.remaining_batch_numbers,
                "remaining_purchase_cents": check.remaining_purchase_cents,
                "ordered_remaining": [
                    {
                        "batch_number": child.batch_number,
                        "effect": canonical_effect(child),
                        "catalogue": batch.catalogue.model_dump(mode="json"),
                        "authority": child.facts.authority.model_dump(mode="json"),
                        "autonomy": child.decision.model_dump(
                            mode="json",
                            exclude={"effect_hash", "economic_signature", "candidate_signatures"},
                        ),
                        "validation": validation_signature(child),
                    }
                    for child, batch in zip(value.current_children, suffix, strict=True)
                ],
                # The complete proofs above retain each derivation hash. Signature
                # equality excludes only hashes containing alpha-renamed future
                # position labels; all current real positions and sources stay exact.
                "current_context": value.current_context.model_dump(mode="json"),
                "full_sources": [item.model_dump(mode="json") for item in value.full_sources],
                "checked_point_count": check.checked_point_count,
            }
        )
    except (ValueError, AssertionError, KeyError, TypeError):
        reasons.add("CURRENT_FIXED_REMAINING_WHOLE_MODE_NOT_PROVEN")
    raw = dict(
        input=value,
        status="UNKNOWN" if reasons else "KNOWN",
        remaining_check=check,
        remaining_signature=None if reasons else signature,
        reasons=sorted(reasons),
    )
    return ResidualModeWorld(**raw) if _validate else ResidualModeWorld.model_construct(**raw)


def evaluate_residual_modes(
    accepted: AssetModeEvaluation, inputs: list[ResidualModeInput]
) -> AssetResidualEvaluation:
    worlds = [derive_residual_world(item) for item in inputs]
    unknown = any(world.status != "KNOWN" for world in worlds)
    status: Literal["STABLE", "DIVERGENT", "UNKNOWN"] = (
        "UNKNOWN"
        if unknown
        else (
            "STABLE" if len({world.remaining_signature for world in worlds}) == 1 else "DIVERGENT"
        )
    )
    return AssetResidualEvaluation(
        accepted_evaluation=accepted,
        worlds=worlds,
        status=status,
        stable=None if unknown else status == "STABLE",
    )
