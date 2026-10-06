"""A narrowing proof for original whole-position redemption, never a bank grant."""

import json
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_recovery_planning import FullRecoveryCandidate
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.multi_goal_allocation import SourceReference
from app.domain.policy_configuration import UUIDReference, configuration_hash
from pydantic import Field, StrictBool, StrictStr, field_validator

ALGORITHM: Literal["full-recovery-execution-v1"] = "full-recovery-execution-v1"
MARKER = "full_recovery_execution"
GUARDS_VERSION = "full-recovery-execution-guards-v1"
CONSENT_SOURCE = "FULL_RECOVERY_USER_ACTION_CONSENT"
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(min_length=1, max_length=120)]


class FullRecoveryPrepareRequest(BoundaryModel):
    policy_id: UUIDReference
    expected_version_id: UUIDReference
    expected_epoch_id: UUIDReference
    position_id: UUIDReference
    idempotency_key: Key

    @field_validator("idempotency_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The original key must not be blank")
        return value


class FullRecoveryConfirmation(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_effect_hash: Hash
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Exact explicit one-action acceptance is required")
        return value


class FullRecoveryExecuteRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_effect_hash: Hash


class FullRecoveryExecutionInput(BoundaryModel):
    """Only the server RR/RO reader supplies these facts; never an HTTP DTO."""

    user_id: UUID
    epoch_id: UUID
    request: FullRecoveryPrepareRequest
    as_of: datetime
    full_policy_id: UUID
    full_policy_version_id: UUID
    full_configuration: RecoveryPolicy
    full_configuration_hash: Hash
    planning_confirmation_valid: StrictBool
    reference_validation: Literal["CURRENT", "CHANGED_OR_UNAVAILABLE", "ARCHIVED"]
    full_effective_status: str
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    linked_asset_policy_id: UUID
    linked_asset_policy_version_id: UUID
    linked_asset_configuration_hash: Hash
    planning_status: str
    planning_response_hash: Hash
    planning_input_hash: Hash
    candidate_position_ids: list[UUID]
    selected_position_ids: list[UUID]
    candidate: FullRecoveryCandidate
    deadline_at: datetime | None
    original_effect: ExecutionEffect
    source_refs: list[SourceReference]
    protection_inventory_complete: StrictBool
    source_issues: list[str] = Field(default_factory=list)


class FullRecoveryExecutionProof(BoundaryModel):
    protocol: Literal["full-recovery-execution-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    preserves_original_permission_checks: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    full_policy_version_id: UUID
    position_id: UUID
    as_of: datetime
    status: Literal["VERIFIED_SCOPE", "BLOCKED", "UNKNOWN"]
    candidate: FullRecoveryCandidate
    deadline_at: datetime | None
    input_hash: Hash
    effect_hash: Hash | None
    reasons: list[str]
    proof_hash: Hash


class FullRecoveryUserConsent(BoundaryModel):
    protocol: Literal["full-recovery-execution-v1"] = ALGORITHM
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    action_id: UUID
    original_effect_hash: Hash
    original_confirmation_evidence_id: UUID
    principal_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime


def recovery_bank_key(key: str) -> str:
    if type(key) is not str or not 0 < len(key) <= 120 or not key.strip():
        raise ValueError("An exact nonblank original key is required")
    return "action:full-recovery:" + configuration_hash({"key": key})


def derive_full_recovery_execution_proof(
    supplied: FullRecoveryExecutionInput, effect: ExecutionEffect | None = None
) -> FullRecoveryExecutionProof:
    """Narrow identity/scope/cost/deadline; original financial checks stay external."""
    data = FullRecoveryExecutionInput.model_validate(supplied.model_dump())
    body, config, candidate, now = data.request, data.full_configuration, data.candidate, data.as_of
    blocked: list[str] = []
    unknown = list(data.source_issues)
    if not data.protection_inventory_complete:
        unknown.append("FULL_PROTECTION_INVENTORY_NOT_PROVEN")
    if not data.source_refs or any(ref.user_id != data.user_id for ref in data.source_refs):
        unknown.append("COMPLETE_OWNED_ORIGINAL_SOURCES_REQUIRED")
    if len({ref.evidence_id for ref in data.source_refs}) != len(data.source_refs):
        unknown.append("DUPLICATE_ORIGINAL_SOURCE_IDENTITY")
    if (
        body.policy_id != data.full_policy_id
        or body.expected_epoch_id != data.epoch_id
        or body.expected_version_id != data.full_policy_version_id
        or configuration_hash(config.model_dump(mode="json")) != data.full_configuration_hash
        or not data.planning_confirmation_valid
        or data.reference_validation != "CURRENT"
        or data.full_effective_status not in {"CONFIRMED", "ACTIVE"}
        or not max(data.confirmed_at, data.valid_from) <= now
        or (data.valid_until is not None and now >= data.valid_until)
    ):
        blocked.append("CURRENT_CONFIRMED_FULL_VERSION_REQUIRED")
    if (
        config.asset_policy_id != data.linked_asset_policy_id
        or candidate.position_id != body.position_id
        or len(set(data.candidate_position_ids)) != len(data.candidate_position_ids)
        or len(set(data.selected_position_ids)) != len(data.selected_position_ids)
        or not set(data.selected_position_ids) <= set(data.candidate_position_ids)
        or body.position_id not in data.selected_position_ids
    ):
        blocked.append("EXACT_SELECTED_ORIGINAL_POSITION_REQUIRED")
    if data.planning_status not in {"CONDITIONAL_RECOVERY_PLAN", "LIQUIDITY_RISK"}:
        blocked.append("ACTUAL_RECOVERY_TRIGGER_REQUIRED")
    if candidate.decision == "UNKNOWN":
        unknown.append("ORIGINAL_CANDIDATE_NOT_PROVEN")
    if (
        not candidate.lossless_eligible
        or not candidate.within_full_planning_limits
        or candidate.decision != "ASK_ONCE"
        or candidate.goal_id != config.goal_id
        or candidate.original_policy_version_id is None
        or not config.allow_auto_recovery_without_penalty
    ):
        blocked.append("CURRENT_LOSSLESS_SAME_SCOPE_LIMITS_REQUIRED")
    quote = candidate.original_quote
    if quote is None or candidate.quote_source == "MISSING":
        unknown.append("EXACT_ORIGINAL_QUOTE_REQUIRED")
    elif (
        quote.kind != "REDEEM"
        or quote.fee_cents != 0
        or quote.loss_cents != 0
        or quote.net_cents != quote.principal_cents
        or not quote.request_at <= now < quote.expires_at
    ):
        blocked.append("MATURE_OR_LOSSY_REDEMPTION_NOT_SUPPORTED")
    if data.deadline_at is None or candidate.on_time is not True:
        blocked.append("ACTUAL_ON_TIME_ARRIVAL_NOT_PROVEN")
    actual = effect if effect is not None else data.original_effect
    if (
        actual.user_id != data.user_id
        or actual.action_type != "REDEEM_ASSET"
        or actual.position_id != body.position_id
        or actual.destination_account_id != candidate.destination_account_id
        or actual.goal_id != config.goal_id
        or actual.amount_cents != candidate.principal_cents
        or actual.amount_cents > config.single_action_cap_cents
        or actual.fee_cents != 0
        or actual.loss_cents != 0
        or actual.net_cents != candidate.principal_cents
        or actual.cash_uses
        or actual.income_uses
        or actual.product_id != candidate.product_id
        or actual.product_version_number != candidate.product_version_number
        or actual.terms_digest != candidate.terms_digest
        or actual.original_policy_version_id != candidate.original_policy_version_id
        or actual.policy_id != data.linked_asset_policy_id
        or actual.policy_version_id != data.linked_asset_policy_version_id
        or actual.settlement_delay_days > config.max_redemption_delay_days
        or not actual.valid_from <= now < actual.expires_at
        or (quote is not None and actual.quote_id != quote.quote_id)
        or quote is not None
        and (
            quote.user_id != data.user_id
            or quote.position_id != body.position_id
            or quote.principal_cents != candidate.principal_cents
            or quote.product_id != candidate.product_id
            or quote.product_version_number != candidate.product_version_number
            or quote.terms_digest != candidate.terms_digest
            or quote.principal_available_at - quote.request_at
            != timedelta(days=actual.settlement_delay_days)
        )
        or effect is not None
        and (
            actual.model_dump(exclude={"latest_arrival_at"})
            != data.original_effect.model_dump(exclude={"latest_arrival_at"})
        )
    ):
        blocked.append("ORIGINAL_EFFECT_SCOPE_OR_TERMS_DIFFER")
    arrival = now + timedelta(days=actual.settlement_delay_days)
    if (
        actual.latest_arrival_at is None
        or data.deadline_at is None
        or arrival > min(actual.latest_arrival_at, data.deadline_at)
        or (effect is not None and actual.latest_arrival_at > data.deadline_at)
    ):
        blocked.append("ORIGINAL_CONFIRMED_ARRIVAL_BOUND_EXCEEDED")
    status: Literal["VERIFIED_SCOPE", "BLOCKED", "UNKNOWN"] = (
        "UNKNOWN" if unknown else ("BLOCKED" if blocked else "VERIFIED_SCOPE")
    )
    proof = FullRecoveryExecutionProof(
        user_id=data.user_id,
        epoch_id=data.epoch_id,
        policy_id=body.policy_id,
        full_policy_version_id=data.full_policy_version_id,
        position_id=body.position_id,
        as_of=now,
        status=status,
        candidate=candidate,
        deadline_at=data.deadline_at,
        input_hash=configuration_hash(data.model_dump(mode="json")),
        effect_hash=execution_effect_hash(actual) if effect is not None else None,
        reasons=sorted(set(unknown + blocked)),
        proof_hash="0" * 64,
    )
    return proof.model_copy(
        update={
            "proof_hash": configuration_hash(proof.model_dump(mode="json", exclude={"proof_hash"}))
        }
    )


def build_full_recovery_effect(
    data: FullRecoveryExecutionInput,
) -> tuple[ExecutionEffect, FullRecoveryExecutionProof]:
    checked = derive_full_recovery_execution_proof(data)
    if checked.status != "VERIFIED_SCOPE" or data.deadline_at is None:
        raise ValueError("FULL_RECOVERY_SCOPE_NOT_READY:" + ",".join(checked.reasons))
    original = data.original_effect
    assert original.latest_arrival_at is not None
    effect = ExecutionEffect.model_validate(
        original.model_copy(
            update={"latest_arrival_at": min(original.latest_arrival_at, data.deadline_at)}
        ).model_dump()
    )
    proof = derive_full_recovery_execution_proof(data, effect)
    if proof.status != "VERIFIED_SCOPE":
        raise ValueError("FULL_RECOVERY_EFFECT_NOT_READY:" + ",".join(proof.reasons))
    return effect, proof


def verify_full_recovery_consent(
    consent: FullRecoveryUserConsent, effect: ExecutionEffect, epoch_id: UUID
) -> None:
    consent = FullRecoveryUserConsent.model_validate(consent.model_dump())
    require_local_user(consent.principal_at_confirmation, effect.user_id, consent.confirmed_at)
    if (
        (consent.user_id, consent.action_id, consent.epoch_id)
        != (effect.user_id, effect.operation_id, epoch_id)
        or consent.original_effect_hash != execution_effect_hash(effect)
        or not effect.valid_from <= consent.confirmed_at < effect.expires_at
    ):
        raise ValueError("Exact original USER one-action consent differs")


def read_frozen_full_recovery_proof(trace: DecisionTrace) -> FullRecoveryExecutionProof:
    """Historical new-algorithm bridge; still supplements original frozen financial verification."""
    verify_trace(trace)
    if trace.phase not in {"PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"} or (
        trace.algorithm_versions.get(MARKER) != ALGORITHM
    ):
        raise ValueError("Exact original recovery execution-phase algorithm required")
    frozen = trace.inputs["planning"][MARKER]
    data = FullRecoveryExecutionInput.model_validate_json(json.dumps(frozen["inputs"]))
    original = trace.inputs["action_request"]
    command = BankCommand.model_validate_json(json.dumps(original["execution"]))
    marker = original[MARKER]
    proof = derive_full_recovery_execution_proof(data, command.effect)
    sources = {row.id: row for row in trace.sources}
    if (
        trace.user_id != data.user_id
        or trace.action_id != command.effect.operation_id
        or trace.as_of != data.as_of
        or data.original_effect.operation_id != command.effect.operation_id
        or marker["protocol"] != ALGORITHM
        or marker["user_id"] != str(data.user_id)
        or marker["epoch_id"] != str(data.epoch_id)
        or marker["request"] != data.request.model_dump(mode="json")
        or marker["request_hash"] != configuration_hash(data.request.model_dump(mode="json"))
        or marker["effect_hash"] != command.effect_hash
        or trace.phase == "PREPARE"
        and marker["original_proof"] != proof.model_dump(mode="json")
        or frozen["proof"] != proof.model_dump(mode="json")
        or trace.inputs["effect"] != command.effect.model_dump(mode="json")
        or original["intent"]
        != {"kind": "redeem_asset", "position_id": str(data.request.position_id)}
        or proof.status != "VERIFIED_SCOPE"
        or any(
            ref.evidence_id not in sources
            or sources[ref.evidence_id].user_id != ref.user_id
            or sources[ref.evidence_id].content_hash != ref.content_hash
            or sources[ref.evidence_id].captured_content_hash != ref.content_hash
            or sources[ref.evidence_id].content_integrity != "VERIFIED"
            or sources[ref.evidence_id].status_at_decision != "VALID"
            or sources[ref.evidence_id].observed_at > trace.as_of
            or sources[ref.evidence_id].valid_from > trace.as_of
            or (until := sources[ref.evidence_id].valid_to) is not None
            and trace.as_of >= until
            or configuration_hash(sources[ref.evidence_id].content) != ref.content_hash
            for ref in data.source_refs
        )
    ):
        raise ValueError(
            "Original frozen recovery proof/identity/actual source denominator differs"
        )
    return proof
