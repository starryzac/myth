"""Explicit two-mode FULL asset vector; no generic world engine or financial authority."""

import copy
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.domain.autonomy import classify_autonomy
from app.domain.autonomy_types import AutonomyDecision, AutonomyFacts
from app.domain.boundary_types import BoundaryModel
from app.domain.finite_uncertainty import AnswerPartition
from app.domain.full_asset_execution_guard import FullAssetRemainingCheck
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.services.full_asset_execution import FullAssetExecutionPreview
from pydantic import Field, StrictInt, model_validator

PROTOCOL: Literal["zhiyu-full-asset-mode-question-v1"] = "zhiyu-full-asset-mode-question-v1"
Mode = Literal["PORTFOLIO", "FIXED_LADDER"]
MODES: tuple[Mode, Mode] = ("PORTFOLIO", "FIXED_LADDER")
AnnotatedRevision = Annotated[StrictInt, Field(ge=1, le=17)]
SOURCE_FINGERPRINT_VERSION = "full-asset-mode-source-clock-v2"


def mode_source_fingerprint(manifest: dict[str, Any]) -> str:
    """Keep old raw receipts exact; normalize only proven recomputation labels in v2."""
    if "source_fingerprint_version" not in manifest:
        return configuration_hash(manifest)
    if manifest["source_fingerprint_version"] != SOURCE_FINGERPRINT_VERSION:
        raise ValueError("Unsupported asset mode source fingerprint version")
    from app.domain.decision_trace_types import TraceEvidence, TracePolicy
    from app.services.question_workflow import source_fingerprint

    native = source_fingerprint(
        {"autonomy_basis": manifest["complete_native_basis"]},
        [
            TraceEvidence.model_validate_json(json.dumps(row))
            for row in manifest["original_sources"]
        ],
        [TracePolicy.model_validate_json(json.dumps(row)) for row in manifest["original_policies"]],
    )
    if manifest["native_source_fingerprint"] != native:
        raise ValueError("Normalized native source fingerprint differs from complete originals")
    compared = copy.deepcopy(manifest)
    compared["complete_native_basis"] = {"normalized_native_source_fingerprint": native}
    return configuration_hash(compared)


class AssetVectorChild(BoundaryModel):
    batch_number: int
    facts: AutonomyFacts
    decision: AutonomyDecision


class AssetModeWorld(BoundaryModel):
    mode: Mode
    status: Literal["KNOWN", "UNKNOWN"]
    native_preview: FullAssetExecutionPreview
    ordered_children: list[AssetVectorChild]
    native_whole_check: FullAssetRemainingCheck | None
    permission_boundary: dict[str, Any]
    reasons: list[str]
    vector_signature: str | None
    bank_authority: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False

    @model_validator(mode="after")
    def complete_native_vector(self) -> Self:
        preview = self.native_preview
        if preview.original_request.planning_mode != self.mode:
            raise ValueError("Mode must belong to its complete native original")
        if self.status == "UNKNOWN":
            if self.vector_signature is not None:
                raise ValueError("Unknown native world cannot claim a signature")
            return self
        portfolio = preview.portfolio
        if (
            preview.state != "READY_TO_REVIEW"
            or preview.reasons
            or portfolio is None
            or self.native_whole_check is None
            or self.native_whole_check.checked_point_count != 1098
            or len(self.ordered_children) != len(portfolio.batches)
            or self.native_whole_check.remaining_batch_numbers
            != [batch.batch_number for batch in portfolio.batches]
            or self.native_whole_check.remaining_purchase_cents != portfolio.total_purchase_cents
        ):
            raise ValueError("Known vector requires every native whole-plan guard")
        for actual, batch in zip(self.ordered_children, portfolio.batches, strict=True):
            facts = actual.facts
            if (
                actual.batch_number != batch.batch_number
                or facts.effect != batch.command.effect
                or facts.confirmation is not None
                or facts.validation is None
                or facts.validation.status not in {"READY", "CONFIRMATION_REQUIRED"}
                or facts.authority.status != "AUTHORIZED"
                or not facts.source_evidence_ids
                or facts.source_issues
                or actual.decision.confirmation_satisfied
                or actual.decision.financial_evaluation != "VERIFIED"
                or actual.decision != classify_autonomy(facts)
            ):
                raise ValueError("Every child needs exact native facts and uninherited consent")
        if self.permission_boundary.get("whole_confirmation_required") is not True:
            raise ValueError("Whole financial confirmation cannot be waived by preferences")
        if self.vector_signature != vector_signature(self):
            raise ValueError("Complete vector signature differs")
        return self


def canonical_effect(child: AssetVectorChild) -> dict[str, Any]:
    """Alpha-rename only a native newly derived purchase action/position, retaining ownership."""
    effect = child.facts.effect
    assert effect is not None
    value = effect.model_dump(mode="json")
    if (
        effect.action_type != "PURCHASE_ASSET"
        or effect.position_id != uuid5(effect.operation_id, "position")
        or effect.business_key != f"purchase:{effect.operation_id}"
    ):
        raise ValueError("This protocol accepts only native newly derived purchase positions")
    value["operation_id"] = f"derived_action:{child.batch_number}"
    value["business_key"] = f"derived_purchase:{child.batch_number}"
    value["position_id"] = f"derived_position:{child.batch_number}"
    # Existing source/destination/return/position accounts and income origins remain exact.
    # Only native action clocks are recomputation labels. Arrival durations and all
    # absolute original product/policy/source windows remain in the native originals.
    for name in ("valid_from", "expires_at"):
        value.pop(name)
    for name in ("latest_arrival_at",):
        clock = getattr(effect, name)
        if clock is not None:
            value[name] = int((clock - effect.valid_from).total_seconds())
    if effect.purchase_exit is not None:
        value["purchase_exit"] = effect.purchase_exit.model_dump(mode="json")
        for name in ("request_at", "principal_available_at"):
            clock = getattr(effect.purchase_exit, name)
            if clock is not None:
                value["purchase_exit"][name] = int((clock - effect.valid_from).total_seconds())
    return value


def vector_signature(world: AssetModeWorld) -> str:
    portfolio = world.native_preview.portfolio
    assert portfolio is not None and world.native_whole_check is not None
    children: list[dict[str, Any]] = []
    for child, batch in zip(world.ordered_children, portfolio.batches, strict=True):
        facts, decision = child.facts, child.decision
        assert facts.validation is not None
        children.append(
            {
                "batch_number": child.batch_number,
                "effect": canonical_effect(child),
                "catalogue": batch.catalogue.model_dump(mode="json"),
                "authority": facts.authority.model_dump(mode="json"),
                "autonomy_inputs": {
                    "initiation": facts.initiation,
                    "action_type": facts.action_type,
                    "payee": facts.payee.model_dump(mode="json"),
                    "source_evidence_ids": [
                        str(identity) for identity in facts.source_evidence_ids
                    ],
                    # The raw facts preserve their complete context hash. It
                    # contains the recomputation clock, so current-source
                    # identity comes from the separately complete normalized
                    # native source fingerprint, not this opaque clock digest.
                    "source_issues": [
                        issue.model_dump(mode="json") for issue in facts.source_issues
                    ],
                    "hard_block_reasons": facts.hard_block_reasons,
                    "confirmation_reasons": facts.confirmation_reasons,
                },
                "autonomy": decision.model_dump(
                    mode="json",
                    exclude={"effect_hash", "economic_signature", "candidate_signatures"},
                ),
                "validation": {
                    "status": facts.validation.status,
                    "reasons": sorted(facts.validation.reasons),
                    "projected_boundary_status": facts.validation.projected_boundary.status
                    if facts.validation.projected_boundary
                    else None,
                },
            }
        )
    return configuration_hash(
        {
            "protocol": PROTOCOL,
            "user_id": str(portfolio.user_id),
            "epoch_id": str(portfolio.epoch_id),
            "total_purchase_cents": portfolio.total_purchase_cents,
            "ordered_children": children,
            "permission_boundary": world.permission_boundary,
            "whole_guard": {
                "batch_numbers": world.native_whole_check.remaining_batch_numbers,
                "total_cents": world.native_whole_check.remaining_purchase_cents,
                "checked_point_count": world.native_whole_check.checked_point_count,
                "original_boundary_status": portfolio.combined_original_boundary.status,
            },
        }
    )


class AssetModeEvaluation(BoundaryModel):
    protocol: Literal["zhiyu-full-asset-mode-question-v1"] = PROTOCOL
    worlds: list[AssetModeWorld]
    selected_mode: Mode | None = None
    status: Literal["STABLE", "DIVERGENT", "UNKNOWN"]
    stable: bool | None
    should_ask: bool | None
    partitions: list[AnswerPartition]
    bank_authority: Literal[False] = False
    execution_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False
    requires_separate_whole_financial_confirmation: Literal[True] = True

    @model_validator(mode="after")
    def exact_domain(self) -> Self:
        if [world.mode for world in self.worlds] != list(MODES):
            raise ValueError("Both exact server-defined native mode worlds must be retained")
        expected = evaluate_modes(self.worlds, self.selected_mode, _validate=False)
        if self.model_dump(mode="json") != expected.model_dump(mode="json"):
            raise ValueError("Whole-vector partitions or state differ")
        return self


def evaluate_modes(
    worlds: list[AssetModeWorld], selected: Mode | None = None, *, _validate: bool = True
) -> AssetModeEvaluation:
    active = [world for world in worlds if selected is None or world.mode == selected]
    unknown = any(world.status != "KNOWN" for world in worlds)
    signatures = {world.vector_signature for world in active}
    status = "UNKNOWN" if unknown else "STABLE" if len(signatures) == 1 else "DIVERGENT"
    partitions = (
        []
        if unknown
        else [
            AnswerPartition(
                choice_key=world.mode,
                world_keys=[configuration_hash({"asset_mode": world.mode})],
                signatures=[world.vector_signature] if world.vector_signature is not None else [],
                residual_signature_count=1,
            )
            for world in worlds
        ]
    )
    values: dict[str, Any] = dict(
        worlds=worlds,
        selected_mode=selected,
        status=status,
        stable=None if unknown else status == "STABLE",
        should_ask=None if unknown else status == "DIVERGENT",
        partitions=partitions,
    )
    return (
        AssetModeEvaluation(**values)
        if _validate
        else AssetModeEvaluation.model_construct(**values)
    )


class AssetModeRevision(BoundaryModel):
    protocol: Literal["zhiyu-full-asset-mode-question-v1"] = PROTOCOL
    user_id: UUID
    epoch_id: UUID
    session_id: UUID
    revision: AnnotatedRevision
    previous_receipt_hash: str | None
    command_id: UUID
    command_kind: Literal["START", "ANSWER", "REFRESH", "REBASE", "CLOSE"]
    authenticated_actor: LocalActorPrincipal
    original_request: dict[str, Any]
    as_of: datetime
    valid_until: datetime
    source_manifest: dict[str, Any]
    source_fingerprint: str
    evaluation: AssetModeEvaluation
    state: Literal["PENDING_ANSWER", "READY_FOR_REVIEW", "UNKNOWN", "CLOSED"]
    question_id: UUID | None
    answer_applied: bool
    receipt_hash: str
    bank_authority: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False

    @model_validator(mode="after")
    def receipt_integrity(self) -> Self:
        actor = self.authenticated_actor
        if (
            actor.user_id != self.user_id
            or actor.role != "USER"
            or not actor.issued_at <= self.as_of < actor.expires_at
        ):
            raise ValueError("Preference receipt must bind its then-current true signed USER")
        if self.state != "CLOSED" and not self.as_of < self.valid_until <= self.as_of + timedelta(
            minutes=15
        ):
            raise ValueError("Current preference receipt needs a nonempty finite lifetime")
        if set(self.original_request) != {"kind", "path", "body"}:
            raise ValueError("Preference original request shape differs")
        body = self.original_request.get("body", {})
        if body.get("client_request_id") != str(self.command_id) or body.get(
            "expected_epoch_id"
        ) != str(self.epoch_id):
            raise ValueError("Original command identity and epoch differ")
        declared_kind = self.original_request["kind"]
        expected_kinds = (
            {"FULL_ASSET_MODE_ANSWER", "FULL_ASSET_MODE_REFRESH"}
            if self.command_kind == "REBASE"
            else {"FULL_ASSET_MODE_" + self.command_kind}
        )
        if declared_kind not in expected_kinds:
            raise ValueError("Persisted command must retain its actual original intent")
        if self.answer_applied and (
            self.command_kind != "ANSWER" or body.get("mode") != self.evaluation.selected_mode
        ):
            raise ValueError("Only a fresh original answer may apply the selected mode")
        if self.receipt_hash != configuration_hash(
            self.model_dump(mode="json", exclude={"receipt_hash"})
        ):
            raise ValueError("Persistent vector receipt hash differs")
        if mode_source_fingerprint(self.source_manifest) != self.source_fingerprint:
            raise ValueError("Full current source fingerprint differs")
        if self.state != "CLOSED":
            expected = (
                "UNKNOWN"
                if self.evaluation.status == "UNKNOWN"
                else "PENDING_ANSWER"
                if self.evaluation.should_ask
                else "READY_FOR_REVIEW"
            )
            expected_question = (
                uuid5(self.session_id, f"asset_mode:{self.revision}:{self.source_fingerprint}")
                if expected == "PENDING_ANSWER"
                else None
            )
            if self.state != expected or self.question_id != expected_question:
                raise ValueError("Only the exact current divergent question may be pending")
        elif self.question_id is not None:
            raise ValueError("A closed preference receipt is historical only")
        return self
