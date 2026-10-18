"""Explicit SYSTEM delegated v2 mathematical gate, separate from USER v1.

Private typed originals only. No HTTP facts, synthetic USER consent, bank,
database, authority cache or callback pretending to prove a native family.
"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.autonomy import classify_autonomy
from app.domain.autonomy_types import AutonomyFacts
from app.domain.bank_posting_codec import (
    bank_posting_snapshot_version,
    validate_posting_original,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import ExecutionContext, ExecutionEffect, ExecutionValidation
from app.domain.full_asset_execution import FullAssetFrozenPortfolio
from app.domain.full_asset_execution_guard import validate_remaining_combination
from app.domain.full_asset_mode_contract import AssetModeEvaluation, canonical_effect
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalInput,
    FullDynamicGoalProof,
    derive_full_dynamic_goal_proof,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_joint_goal_execution import FullJointFrozenPlan, verify_frozen_joint_plan
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.local_actor_session_types import require_local_user
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.standing_asset_residual import AssetResidualEvaluation
from app.domain.standing_asset_source_lineage import prefix_source_equivalent
from app.domain.standing_income_goal_scope import (
    AnyGrant,
    SignedIncomeGoalGrant,
    native_scope,
    native_variant,
)
from app.domain.standing_income_goal_slice import (
    SingleGoalIncomeSliceOriginal,
    derive_income_goal_modes,
)
from app.domain.standing_owned_asset_read import AcceptedAssetReadProof, owned_source_equivalent
from app.domain.zhiyu_standing_journal import StandingRun, accounted_day_total
from app.domain.zhiyu_standing_scope import Family, Hash, covered_effects
from app.services.full_policy_lifecycle import FullPolicyView
from app.services.zhiyu_standing_sources import ObservedOriginal
from app.services.zhiyu_standing_store import StandingControl
from pydantic import Field, StrictInt, model_validator

ALGORITHM: Literal["zhiyu-standing-delegated-native-v2"] = "zhiyu-standing-delegated-native-v2"
MARKER = "zhiyu_standing_delegated"
PHASES = ("PREPARE", "RESERVE", "BANK_ACCEPT")


class SingleGoalOriginal(BoundaryModel):
    kind: Literal["SINGLE_GOAL"] = "SINGLE_GOAL"
    current: FullDynamicGoalInput
    effect: ExecutionEffect


class JointGoalOriginal(BoundaryModel):
    kind: Literal["JOINT_GOALS"] = "JOINT_GOALS"
    plan: FullJointFrozenPlan
    # Includes every goal model, even an original zero or settled allocation.
    current_models: dict[UUID, tuple[UUID, Hash, UUID]]
    current_full_version_id: UUID
    current_full_content_hash: Hash
    current_full_policy: FullPolicyView
    current_inputs: list[FullDynamicGoalInput]


class AssetOriginal(BoundaryModel):
    kind: Literal["ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"]
    portfolio: FullAssetFrozenPortfolio
    context: ExecutionContext
    current_full_content_hash: Hash
    current_full_policy: FullPolicyView
    full_inventory_complete: Literal[True]
    remaining_batch_numbers: list[StrictInt]
    current_mode_evaluation: AssetModeEvaluation
    residual_mode_evaluation: AssetResidualEvaluation | None = None
    owned_read_proof: AcceptedAssetReadProof | None = None
    owned_source_manifest: dict[str, Any] | None = None
    mode_source_manifest: dict[str, Any] | None = None
    # An actually bank-settled maturity source, not a forecast or a MATURE quote.
    maturity_original: ObservedOriginal | None = None


class PaymentOriginal(BoundaryModel):
    kind: Literal["PERIODIC_PAYMENT"] = "PERIODIC_PAYMENT"
    effect: ExecutionEffect
    context: ExecutionContext
    # The complete signed native relation, re-read by the snapshot adapter.
    original_authorization: dict[str, Any]
    current_scope: dict[str, Any]


FamilyOriginal = Annotated[
    SingleGoalOriginal
    | SingleGoalIncomeSliceOriginal
    | JointGoalOriginal
    | AssetOriginal
    | PaymentOriginal,
    Field(discriminator="kind"),
]


class WholeKnowledge(BoundaryModel):
    """Server current whole vector; old planning receipts never grant authority.

    Deterministic current originals are a one-world domain only when the actual
    complete current X05 history has no outstanding question. Assets always
    retain both native asset-mode worlds via AssetOriginal above. This internal
    contract is never an HTTP request model.
    """

    protocol: Literal["standing-current-whole-knowledge-v2"] = "standing-current-whole-knowledge-v2"
    native_original_hash: Hash
    ordered_effect_hashes: list[Hash]
    current_question_originals: list[dict[str, Any]]
    pending_question_ids: list[UUID]
    source_context_hash: Hash
    status: Literal["KNOWN", "UNKNOWN"]
    bank_authority: Literal[False] = False
    old_one_shot_user_consent_inherited: Literal[False] = False


class DelegatedNativeInput(BoundaryModel):
    protocol: Literal[
        "zhiyu-standing-delegated-native-v2", "zhiyu-standing-income-goal-native-v1"
    ] = ALGORITHM
    phase: Literal["PREPARE", "RESERVE", "BANK_ACCEPT"]
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    original_source: ObservedOriginal
    grant: AnyGrant
    current_control: StandingControl
    current_version_ids: list[UUID]
    current_runs: list[StandingRun]
    spent_today_cents: Annotated[StrictInt, Field(ge=0)]
    # On RESERVE/BANK the accepted event itself already reserves the whole day
    # cap and must not be counted twice; every other accepted/UNKNOWN run stays.
    event_accepted_total_cents: Annotated[StrictInt, Field(ge=0)] = 0
    settled_prefix: Annotated[StrictInt, Field(ge=0, le=8)] = 0
    original_settlements: list[dict[str, Any]] = Field(default_factory=list)
    native: FamilyOriginal
    knowledge: WholeKnowledge
    current_facts: list[AutonomyFacts]
    full_sources: list[FullProtectionPolicySource]
    full_source_issues: list[str]
    bank_authority: Literal[False] = False
    old_one_shot_user_consent_inherited: Literal[False] = False

    @model_validator(mode="after")
    def exact_family(self) -> Self:
        if (
            self.native.kind if self.native.kind != "SINGLE_GOAL_EVENT_SLICE" else "SINGLE_GOAL"
        ) != self.grant.scope.family:
            raise ValueError("Standing family must bind the complete native original")
        cumulative = isinstance(self.grant, SignedIncomeGoalGrant)
        if self.protocol != native_variant(self.grant) or cumulative != isinstance(
            self.native, SingleGoalIncomeSliceOriginal
        ):
            raise ValueError("Explicit native/grant/goal mode variant differs; no legacy fallback")
        if len(set(self.current_version_ids)) != len(self.current_version_ids):
            raise ValueError("Current version identities must be unique")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("A current aware server clock is required")
        return self


class DelegatedNativeProof(BoundaryModel):
    protocol: Literal[
        "zhiyu-standing-delegated-native-v2", "zhiyu-standing-income-goal-native-v1"
    ] = ALGORITHM
    family: Family
    phase: Literal["PREPARE", "RESERVE", "BANK_ACCEPT"]
    user_id: UUID
    epoch_id: UUID
    event_key: Hash
    grant_id: UUID
    grant_hash: Hash
    control_revision: StrictInt
    as_of: datetime
    status: Literal["READY", "BLOCKED", "UNKNOWN"]
    ordered_effect_hashes: list[Hash]
    original_total_cents: StrictInt
    original_source_hash: Hash
    native_original_hash: Hash
    source_context_hash: Hash
    native_validations: list[ExecutionValidation]
    current_protection_hashes: list[Hash]
    reasons: list[str]
    proof_hash: Hash
    executor_role: Literal["SYSTEM"] = "SYSTEM"
    old_one_shot_user_consent_inherited: Literal[False] = False
    # A proof alone is not bank authority: actual source/audit/locks are native.
    bank_authority: Literal[False] = False


def original_effects(native: FamilyOriginal) -> list[ExecutionEffect]:
    if isinstance(native, (SingleGoalOriginal, SingleGoalIncomeSliceOriginal)):
        return [native.effect]
    if isinstance(native, JointGoalOriginal):
        return [child.command.effect for child in native.plan.children]
    if isinstance(native, AssetOriginal):
        return [batch.command.effect for batch in native.portfolio.batches]
    return [native.effect]


def native_original_hash(native: FamilyOriginal) -> str:
    return configuration_hash(native.model_dump(mode="json"))


def source_context_hash(value: DelegatedNativeInput) -> str:
    return configuration_hash(
        {
            "user_id": str(value.user_id),
            "epoch_id": str(value.epoch_id),
            "as_of": value.as_of.isoformat(),
            "source": value.original_source.model_dump(mode="json"),
            "native": value.native.model_dump(mode="json"),
            "facts": [fact.model_dump(mode="json") for fact in value.current_facts],
            "versions": [str(identity) for identity in value.current_version_ids],
            "current_runs": [row.model_dump(mode="json") for row in value.current_runs],
            "current_control": value.current_control.model_dump(mode="json"),
            "full_sources": [row.model_dump(mode="json") for row in value.full_sources],
            "full_issues": value.full_source_issues,
            "question_originals": value.knowledge.current_question_originals,
        }
    )


def _native_math(
    value: DelegatedNativeInput,
) -> tuple[list[ExecutionContext], list[FullDynamicGoalProof | None], set[str]]:
    native = value.native
    effects = original_effects(native)[value.settled_prefix :]
    contexts: list[ExecutionContext] = []
    dynamic: list[FullDynamicGoalProof | None] = []
    reasons: set[str] = set()
    if isinstance(native, SingleGoalIncomeSliceOriginal):
        modes = derive_income_goal_modes(
            native.current, native.observed, native.effect, selected_mode="CUMULATIVE_EVENT_TARGET"
        )
        if (
            modes != native.modes
            or modes.status != "READY"
            or native.observed != value.original_source
        ):
            reasons.add("CURRENT_COMPLETE_CUMULATIVE_EVENT_MODE_NOT_VERIFIED")
        contexts, dynamic = [native.current.context], [None]
    elif isinstance(native, SingleGoalOriginal):
        proof = derive_full_dynamic_goal_proof(native.current, native.effect)
        if proof.status != "VERIFIED_RANGE":
            reasons.update(["SINGLE_NATIVE_RANGE_NOT_VERIFIED", *proof.reasons])
        contexts, dynamic = [native.current.context], [proof]
    elif isinstance(native, JointGoalOriginal):
        verify_frozen_joint_plan(native.plan)
        plan = native.plan
        if (
            plan.user_id != value.user_id
            or plan.epoch_id != value.epoch_id
            or native.current_full_version_id != plan.inputs.request.expected_full_policy_version_id
            or native.current_full_content_hash != plan.inputs.scope.current_version.content_hash
            or native.current_full_policy.policy_id != plan.inputs.request.full_policy_id
            or native.current_full_policy.current_version.version_id
            != native.current_full_version_id
            or native.current_full_policy.current_version.content_hash
            != native.current_full_content_hash
            or native.current_full_policy.effective_status != "ACTIVE"
            or not native.current_full_policy.planning_confirmation_valid
            or native.current_full_policy.reference_validation != "CURRENT"
        ):
            reasons.add("JOINT_CURRENT_WHOLE_SCOPE_CHANGED")
        expected_models = {
            row.data.request.goal_id: (
                row.data.model_evidence_id,
                row.data.model_evidence_hash,
                row.data.request.expected_policy_version_id,
            )
            for row in plan.inputs.joint.original_actual_input.dynamic_goals
            if row.data is not None
        }
        if native.current_models != expected_models:
            reasons.add("JOINT_COMPLETE_CURRENT_MODEL_DENOMINATOR_CHANGED")
        if len(native.current_inputs) != len(effects):
            raise ValueError("Every fixed joint child needs its original current native input")
        for data, effect in zip(native.current_inputs, effects, strict=True):
            if data.request.goal_id != effect.goal_id:
                reasons.add("JOINT_FIXED_CHILD_ORDER_OR_OWNER_CHANGED")
            proof = derive_full_dynamic_goal_proof(data, effect)
            if proof.status != "VERIFIED_RANGE":
                reasons.update(["JOINT_CURRENT_FIXED_CHILD_RANGE_NOT_VERIFIED", *proof.reasons])
            contexts.append(data.context)
            dynamic.append(proof)
    elif isinstance(native, AssetOriginal):
        portfolio = native.portfolio
        full = native.current_full_policy
        owned = native.owned_read_proof
        if owned is not None and (
            owned.effects != original_effects(native)
            or owned.settled_prefix != value.settled_prefix
            or owned.current_run not in value.current_runs
            or (owned.user_id, owned.epoch_id, owned.as_of)
            != (value.user_id, value.epoch_id, value.as_of)
        ):
            raise ValueError("Current owned residual scope differs from this fixed whole")
        if (
            full.policy_id != portfolio.original_request.full_policy_id
            or full.current_version.version_id
            != portfolio.original_request.expected_full_policy_version_id
            or full.current_version.content_hash != native.current_full_content_hash
            or full.effective_status != "ACTIVE"
            or not full.planning_confirmation_valid
            or full.reference_validation != "CURRENT"
        ):
            reasons.add("CURRENT_ORIGINAL_FULL_ASSET_SCOPE_CHANGED")
        if native.remaining_batch_numbers != list(
            range(value.settled_prefix + 1, len(portfolio.batches) + 1)
        ):
            raise ValueError("The complete fixed remaining asset order must be retained")
        check = validate_remaining_combination(
            portfolio,
            native.context,
            native.remaining_batch_numbers,
            value.as_of,
            current_full_configuration_hash=native.current_full_content_hash,
            full_sources=value.full_sources,
            full_source_issues=value.full_source_issues,
            full_inventory_complete=native.full_inventory_complete,
        )
        if check.checked_point_count != 1098:
            reasons.add("ASSET_COMPLETE_1098_CHECK_REQUIRED")
        if owned is not None:
            if native.mode_source_manifest is None or native.owned_source_manifest is None:
                raise ValueError("Original complete accepted source manifest is required")
            equivalent = (
                prefix_source_equivalent(
                    owned, native.mode_source_manifest, native.owned_source_manifest
                )
                if owned.settled_prefix
                else owned_source_equivalent(
                    owned, native.mode_source_manifest, native.owned_source_manifest
                )
            )
            if not equivalent:
                raise ValueError(
                    "External source change cannot be mapped to an accepted self descendant"
                )
            residual = native.residual_mode_evaluation
            if residual is None:
                raise ValueError("Explicit original finite residual protocol is required")
            residual = AssetResidualEvaluation.model_validate_json(residual.model_dump_json())
            if residual.accepted_evaluation != native.current_mode_evaluation:
                raise ValueError("Residual finite domain cannot replace accepted mode worlds")
            if residual.status != "STABLE" or residual.stable is not True:
                reasons.add("CURRENT_X05_FIXED_REMAINING_VECTOR_NOT_KNOWN")
            matched = [
                world
                for world in residual.worlds
                if world.input.original_world.mode == portfolio.original_request.planning_mode
            ]
            if len(matched) != 1:
                raise ValueError("Exact selected original residual world missing")
            residual_world = matched[0]
            if (
                residual_world.input.original_world.native_preview.portfolio != portfolio
                or residual_world.input.current_context != native.context
                or residual_world.input.settled_prefix != value.settled_prefix
                or residual_world.input.as_of != value.as_of
                or residual_world.input.full_sources != value.full_sources
                or residual_world.input.full_source_issues != value.full_source_issues
                or residual_world.remaining_check != check
                or [child.facts for child in residual_world.input.current_children]
                != value.current_facts
            ):
                raise ValueError(
                    "Current fixed residual math/source/facts do not bind this native whole"
                )
        else:
            if native.residual_mode_evaluation is not None:
                raise ValueError(
                    "Residual mode protocol requires independently accepted owned scope"
                )
            evaluation = AssetModeEvaluation.model_validate_json(
                native.current_mode_evaluation.model_dump_json()
            )
            if evaluation.status != "STABLE" or evaluation.stable is not True:
                reasons.add("CURRENT_X05_WHOLE_ASSET_VECTOR_NOT_KNOWN")
            matching = [
                world
                for world in evaluation.worlds
                if world.mode == portfolio.original_request.planning_mode
            ]
            if len(matching) != 1 or matching[0].status != "KNOWN":
                reasons.add("CURRENT_NATIVE_ASSET_MODE_WORLD_MISSING")
            else:
                world = matching[0]
                if len(world.ordered_children) != len(effects):
                    reasons.add("CURRENT_X05_WHOLE_CHILD_DENOMINATOR_CHANGED")
                else:
                    # No single-batch reduction. Canonicalization permits only the
                    # old tested native newly generated purchase labels/clocks.
                    for child, effect in zip(world.ordered_children, effects, strict=True):
                        original_child = child.model_copy(
                            update={"facts": child.facts.model_copy(update={"effect": effect})}
                        )
                        if canonical_effect(child) != canonical_effect(original_child):
                            reasons.add("CURRENT_X05_WHOLE_ECONOMIC_VECTOR_CHANGED")
        if native.kind == "MATURITY_REINVESTMENT":
            original = native.maturity_original
            if (
                original is None
                or original != value.original_source
                or original.source.source_kind != "SETTLED_MATURITY_RECEIPT"
                or original.source.original_source_hash
                != configuration_hash(original.original_source)
            ):
                reasons.add("ACTUAL_SETTLED_MATURITY_RECEIPT_REQUIRED")
            else:
                raw = original.original_source
                action = raw.get("action", {})
                requests, receipts, operations = (
                    raw.get("bank_requests"),
                    raw.get("receipts"),
                    raw.get("bank_operations"),
                )
                if (
                    type(action.get("amount_cents")) is not int
                    or action.get("amount_cents", 0) <= 0
                    or not isinstance(requests, list)
                    or len(requests) != 1
                    or not isinstance(receipts, list)
                    or len(receipts) != 1
                    or not isinstance(operations, list)
                    or len(operations) != 1
                    or str(receipts[0].get("id")) != original.source.original_source_id
                    or requests[0].get("status") != "SETTLED"
                    or operations[0].get("status") != "SETTLED"
                    or sum(effect.amount_cents for effect in effects) > action["amount_cents"]
                    or any(
                        use.account_id != UUID(str(requests[0]["destination_account_id"]))
                        for effect in effects
                        for use in effect.cash_uses
                    )
                    or any(
                        (str(effect.goal_id) if effect.goal_id is not None else None)
                        != action.get("goal_id")
                        for effect in effects
                    )
                ):
                    reasons.add(
                        "REINVESTMENT_EXCEEDS_ACTUAL_ORIGINAL_RECEIVED_PRINCIPAL_OR_OWNERSHIP"
                    )
        contexts, dynamic = [native.context] * len(effects), [None] * len(effects)
    else:
        from app.domain.full_payment_permissions import PaymentRelationScope, verify_payment_effect
        from app.services.full_payment_permissions import PaymentCommandOriginal

        payment_original = PaymentCommandOriginal.model_validate_json(
            json.dumps(native.original_authorization)
        )
        scope = PaymentRelationScope.model_validate_json(json.dumps(native.current_scope))
        if (
            payment_original.kind != "CONFIRM"
            or payment_original.scope != scope
            or not scope.auto_execute
        ):
            reasons.add("CURRENT_ORIGINAL_AUTO_PAYMENT_RELATION_REQUIRED")
        verify_payment_effect(
            scope,
            native.effect,
            value.as_of,
            verified_payee_evidence_ids={
                identity for fact in value.current_facts for identity in fact.payee.evidence_ids
            },
        )
        contexts, dynamic = [native.context], [None]
    return contexts, dynamic, reasons


def settled_posting_legs_match(
    effect: ExecutionEffect,
    operation: dict[str, Any],
    receipt: dict[str, Any],
    postings: list[dict[str, Any]],
) -> bool:
    """Exact original legs; bookkeeping dimensions never enter economic conservation."""
    from app.services.execution_projection import expected_execution_posting_legs

    if not postings or operation["id"] != str(effect.operation_id):
        return False
    actual: dict[str, tuple[str, str, int]] = {}
    for raw in postings:
        validate_posting_original(raw, bank_posting_snapshot_version(raw))
        row = raw
        leg = row["leg_ref"]
        if (
            leg in actual
            or row["operation_id"] != operation["id"]
            or row["user_id"] != str(effect.user_id)
            or row["id"] != str(uuid5(effect.operation_id, "posting:" + leg))
            or row["occurred_at"] != operation["settled_at"]
        ):
            return False
        actual[leg] = (row["ledger_key"], row["ledger_dimension"], row["delta_cents"])
    ids = receipt["response"]["posting_ids"]
    return (
        actual == expected_execution_posting_legs(effect)
        and sum(
            row["delta_cents"] for row in postings if row["ledger_dimension"] == "ECONOMIC"
        ) == 0
        and isinstance(ids, list)
        and len(ids) == len(postings)
        and all(isinstance(identity, str) for identity in ids)
        and set(ids) == {row["id"] for row in postings}
    )


def derive_delegated_native_proof(value: DelegatedNativeInput) -> DelegatedNativeProof:
    value = DelegatedNativeInput.model_validate_json(value.model_dump_json())
    effects = original_effects(value.native)
    remaining = effects[value.settled_prefix :]
    reasons: set[str] = set()
    unknown: set[str] = set()
    validations: list[ExecutionValidation] = []
    protection_hashes: list[str] = []
    native_hash = native_original_hash(value.native)
    source = value.original_source.source
    expected_kind = {
        "SINGLE_GOAL": "SETTLED_INCOME",
        "JOINT_GOALS": "SETTLED_INCOME",
        "ASSET_PORTFOLIO": "SETTLED_INCOME",
        "MATURITY_REINVESTMENT": "SETTLED_MATURITY_RECEIPT",
        "PERIODIC_PAYMENT": "SERVER_PERIOD",
    }[value.grant.scope.family]
    if source.source_kind != expected_kind:
        reasons.add("EXACT_NATIVE_TRIGGER_FAMILY_REQUIRED")
    day = value.as_of.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    if (
        accounted_day_total(value.current_runs, value.user_id, value.epoch_id, day)
        != value.spent_today_cents
    ):
        reasons.add("COMPLETE_CURRENT_JOURNAL_DAILY_BUDGET_DIFFERS")
    own_runs = [row for row in value.current_runs if row.source.event_key == source.event_key]
    if len(own_runs) != 1:
        reasons.add("ONE_ORIGINAL_SOURCE_JOURNAL_REQUIRED")
    else:
        own = own_runs[0]
        expected_own = own.accepted_total_cents if own.accounting_day == day else 0
        if expected_own != value.event_accepted_total_cents:
            reasons.add("ACCEPTED_EVENT_DAILY_INVENTORY_DIFFERS")
        if value.phase != "PREPARE" and (
            own.state not in {"ACCEPTED", "UNKNOWN"}
            or own.grant_id != value.grant.grant_id
            or own.grant_hash != configuration_hash(value.grant.model_dump(mode="json"))
            or own.original_action_ids != [row.operation_id for row in effects]
            or own.original_effect_hashes != [execution_effect_hash(row) for row in effects]
            or own.accepted_total_cents != sum(row.amount_cents for row in effects)
        ):
            reasons.add("EXACT_ACCEPTED_WHOLE_JOURNAL_INVENTORY_REQUIRED")
    require_local_user(
        value.current_control.actor, value.user_id, value.current_control.recorded_at
    )
    if (
        value.settled_prefix >= len(effects)
        or len(value.original_settlements) != value.settled_prefix
    ):
        reasons.add("COMPLETE_SETTLED_PREFIX_OR_REMAINING_INVENTORY_DIFFERS")
    for effect, settlement in zip(
        effects[: value.settled_prefix], value.original_settlements, strict=False
    ):
        try:
            operation, receipt, action = (
                settlement["bank"],
                settlement["receipt"],
                settlement["action"],
            )
            postings = settlement["postings"]
            if (
                operation["user_id"] != str(value.user_id)
                or operation["action_plan_id"] != str(effect.operation_id)
                or operation["status"] != "SETTLED"
                or operation["request"]["effect"] != effect.model_dump(mode="json")
                or operation["request_hash"] != configuration_hash(operation["request"])
                or receipt["action_plan_id"] != str(effect.operation_id)
                or action["id"] != str(effect.operation_id)
                or action["status"] not in {"SUCCEEDED", "RECONCILED"}
                or action["idempotency_key"] != operation["idempotency_key"]
                or not settled_posting_legs_match(effect, operation, receipt, postings)
            ):
                reasons.add("COMPLETE_PRIOR_ORIGINAL_RECEIPT_OR_BANK_EFFECT_DIFFERS")
        except (KeyError, TypeError, ValueError):
            unknown.add("COMPLETE_PRIOR_ORIGINAL_RECEIPT_NOT_VERIFIED")
    if (
        (source.user_id, source.epoch_id) != (value.user_id, value.epoch_id)
        or source.original_source_hash != configuration_hash(value.original_source.original_source)
        or value.current_control.grant_id != value.grant.grant_id
        or value.current_control.recorded_at > value.as_of
        or value.current_control.actor.user_id != value.user_id
    ):
        reasons.add("DELEGATED_ORIGINAL_OWNER_SOURCE_OR_CONTROL_DIFFERS")
    knowledge = value.knowledge
    if (
        knowledge.status != "KNOWN"
        or knowledge.pending_question_ids
        or knowledge.native_original_hash != native_hash
        or knowledge.ordered_effect_hashes != [execution_effect_hash(row) for row in effects]
        or knowledge.source_context_hash != source_context_hash(value)
    ):
        unknown.add("CURRENT_X05_COMPLETE_WHOLE_VECTOR_NOT_KNOWN")
    if value.full_source_issues or not value.current_facts:
        unknown.add("CURRENT_NATIVE_FULL_SOURCE_INVENTORY_NOT_VERIFIED")
    contexts: list[ExecutionContext] = []
    proofs: list[FullDynamicGoalProof | None] = []
    try:
        contexts, proofs, native_reasons = _native_math(value)
        reasons.update(native_reasons)
    except (ValueError, TypeError, KeyError, StopIteration, OverflowError):
        unknown.add("COMPLETE_NATIVE_FAMILY_MATH_NOT_VERIFIED")
    if len(value.current_facts) != len(remaining) or len(contexts) != len(remaining):
        unknown.add("COMPLETE_NATIVE_CHILD_FACTS_DENOMINATOR_CHANGED")
    else:
        for effect, context, dynamic, fact in zip(
            remaining, contexts, proofs, value.current_facts, strict=True
        ):
            # New SYSTEM authority never consumes a prior one-shot confirmation,
            # and never clears the real MVP native requires_confirmation bit.
            validation = revalidate_execution(effect, context, full_dynamic_goal_proof=dynamic)
            validations.append(validation)
            if (
                context.user_id != value.user_id
                or context.snapshot.as_of != value.as_of
                or context.requires_confirmation
                or fact.confirmation is not None
                or fact.confirmation_reasons
                or fact.effect != effect
                or fact.as_of != value.as_of
                or fact.validation != validation
                or fact.authority.status != "AUTHORIZED"
                or set(fact.authority.policy_version_ids) != set(effect.policy_version_ids)
                or not fact.authority.evidence_ids
                or not fact.source_evidence_ids
                or fact.source_issues
                or fact.hard_block_reasons
                or classify_autonomy(fact).level != "AUTO_EXECUTE"
                or not classify_autonomy(fact).execution_eligible
                or validation.status != "READY"
            ):
                reasons.add("CURRENT_EXACT_NATIVE_MVP_AUTO_PERMISSION_REQUIRED")
            protection = validate_full_execution_protection(
                effect,
                context,
                validation,
                value.full_sources,
                source_issues=tuple(value.full_source_issues),
            )
            if protection.status not in {"PASSED", "NO_ADDITIONAL_POLICY"}:
                unknown.update(["CURRENT_NATIVE_FULL_PROTECTION_NOT_VERIFIED", *protection.reasons])
            protection_hashes.append(protection.result_hash)
            # Typed current canonical configuration is retained, never a bool.
            for version in context.versions:
                if version.version_id in effect.policy_version_ids:
                    validate_configuration(version.configuration)
    spent = value.spent_today_cents - value.event_accepted_total_cents
    if spent < 0:
        reasons.add("ACCEPTED_EVENT_DAILY_INVENTORY_DIFFERS")
    reasons.update(
        covered_effects(
            native_scope(value.grant.scope),
            effects,
            user_id=value.user_id,
            epoch_id=value.epoch_id,
            current_version_ids=set(value.current_version_ids),
            now=value.as_of,
            spent_today_cents=spent,
            control_state=value.current_control.state,
            complete_worlds_verified=not unknown,
            native_current_scope_verified=not reasons,
            settled_maturity_original_verified=isinstance(value.native, AssetOriginal)
            and value.native.kind == "MATURITY_REINVESTMENT"
            and value.native.maturity_original is not None,
            current_product_terms={
                row.product_id: row for context in contexts for row in context.products
            },
        )
    )
    fields = {
        "protocol": value.protocol,
        "family": value.grant.scope.family,
        "phase": value.phase,
        "user_id": value.user_id,
        "epoch_id": value.epoch_id,
        "event_key": source.event_key,
        "grant_id": value.grant.grant_id,
        "grant_hash": configuration_hash(value.grant.model_dump(mode="json")),
        "control_revision": value.current_control.revision,
        "as_of": value.as_of,
        "status": "UNKNOWN" if unknown else "BLOCKED" if reasons else "READY",
        "ordered_effect_hashes": [execution_effect_hash(row) for row in effects],
        "original_total_cents": sum(row.amount_cents for row in effects),
        "original_source_hash": source.original_source_hash,
        "native_original_hash": native_hash,
        "source_context_hash": source_context_hash(value),
        "native_validations": [row.model_dump(mode="json") for row in validations],
        "current_protection_hashes": protection_hashes,
        "reasons": sorted(reasons | unknown),
    }
    temporary = DelegatedNativeProof.model_validate_json(
        json.dumps(fields | {"proof_hash": "0" * 64}, default=str)
    )
    return temporary.model_copy(
        update={
            "proof_hash": configuration_hash(
                temporary.model_dump(mode="json", exclude={"proof_hash"})
            )
        }
    )


def verify_standing_delegated_trace(trace: DecisionTrace) -> None:
    """Historical mathematical replay; current control/funds are a fresh service gate."""
    from app.services.zhiyu_standing_recording import verify_standing_trace

    verify_standing_trace(trace)


def acceptance_inventory(value: DelegatedNativeInput, proof: DelegatedNativeProof) -> None:
    if proof != derive_delegated_native_proof(value) or proof.status != "READY":
        raise ValueError("Only this actual whole-source READY proof can enter ACCEPTED")
