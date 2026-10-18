"""A declared finite choice among current independently confirmed FULL scopes."""

import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.finite_uncertainty import AnswerPartition
from app.domain.full_joint_question_contract import (
    Hash,
    JointFixedEvaluation,
    joint_source_fingerprint,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import IntentModel
from pydantic import Field, StrictInt, model_validator

PROTOCOL: Literal["zhiyu-full-joint-scope-question-v1"] = "zhiyu-full-joint-scope-question-v1"
SOURCE_VERSION = "full-joint-declared-scopes-source-v1"
REJECTION_PROTOCOL: Literal["zhiyu-full-joint-scope-rejection-v1"] = (
    "zhiyu-full-joint-scope-rejection-v1"
)
RejectionCode = Literal[
    "JOINT_VERSION_CHANGED",
    "QUESTION_WIRE_CAPACITY_EXCEEDED",
    "STALE_QUESTION_REVISION",
    "STALE_QUESTION_ANSWER",
]
REJECTION_CODES = {
    "JOINT_VERSION_CHANGED",
    "QUESTION_WIRE_CAPACITY_EXCEEDED",
    "STALE_QUESTION_REVISION",
    "STALE_QUESTION_ANSWER",
}


class ScopeQuestionRejection(BoundaryModel):
    """An audited refusal of a preference observation, never a financial result."""

    protocol: Literal["zhiyu-full-joint-scope-rejection-v1"] = REJECTION_PROTOCOL
    record_kind: Literal["NONFINANCIAL_REJECTION"] = "NONFINANCIAL_REJECTION"
    user_id: UUID
    epoch_id: UUID
    command_id: UUID
    session_id: UUID | None
    authenticated_actor: LocalActorPrincipal
    original_request: dict[str, Any]
    request_hash: Hash
    as_of: datetime
    code: RejectionCode
    message: Annotated[str, Field(min_length=1, max_length=200)]
    http_status: Literal[409, 413]
    rejection_hash: Hash
    bank_authority: Literal[False] = False
    execution_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False
    financial_absence_proven: Literal[False] = False
    nonfinancial_preference_pending_only: Literal[True] = True
    session_head_advanced: Literal[False] = False

    @model_validator(mode="after")
    def exact_original_nonfinancial_refusal(self) -> Self:
        require_local_user(self.authenticated_actor, self.user_id, self.as_of)
        if set(self.original_request) != {"kind", "path", "body"}:
            raise ValueError("Nonfinancial refusal must retain its exact original request")
        if (
            self.original_request["kind"]
            not in {
                "FULL_JOINT_SCOPE_START",
                "FULL_JOINT_SCOPE_ANSWER",
                "FULL_JOINT_SCOPE_REFRESH",
                "FULL_JOINT_SCOPE_CLOSE",
            }
            or self.original_request["body"].get("client_request_id") != str(self.command_id)
            or self.original_request["body"].get("expected_epoch_id") != str(self.epoch_id)
            or self.http_status != (413 if self.code == "QUESTION_WIRE_CAPACITY_EXCEEDED" else 409)
            or self.request_hash
            != configuration_hash(
                {
                    "user_id": str(self.user_id),
                    "epoch_id": str(self.epoch_id),
                    "original_request": self.original_request,
                }
            )
            or self.rejection_hash
            != configuration_hash(self.model_dump(mode="json", exclude={"rejection_hash"}))
        ):
            raise ValueError(
                "Immutable preference refusal owner, epoch, request, code or hash differs"
            )
        return self


class RegisteredScopeReference(IntentModel):
    full_policy_id: UUID
    expected_full_policy_version_id: UUID


class RegisteredScopeWorld(BoundaryModel):
    full_policy_id: UUID
    current_full_policy_version_id: UUID
    native_evaluation: JointFixedEvaluation
    source_manifest: dict[str, Any]
    source_fingerprint: Hash
    economic_signature: Hash | None

    @model_validator(mode="after")
    def complete_original_world(self) -> Self:
        preview = self.native_evaluation.native_preview
        plan = preview.plan
        if (
            preview.request.full_policy_id != self.full_policy_id
            or preview.request.expected_full_policy_version_id
            != self.current_full_policy_version_id
            or self.source_fingerprint != joint_source_fingerprint(self.source_manifest)
            or self.source_manifest.get("vector_signature")
            != self.native_evaluation.vector_signature
            or self.economic_signature != economic_signature(self.native_evaluation)
            or self.source_manifest.get("user_id") != str(preview.user_id)
            or self.source_manifest.get("epoch_id") != str(preview.epoch_id)
            or self.source_manifest.get("registered_goal_ids")
            != [str(identity) for identity in preview.registered_goal_ids]
            or self.source_manifest.get("native_status") != preview.status
            or self.source_manifest.get("native_reasons") != preview.reasons
            or self.source_manifest.get("unresolved_action_ids")
            != [str(identity) for identity in preview.unresolved_original_action_ids]
            or plan is not None
            and (
                self.source_manifest.get("full_scope") != plan.inputs.scope.model_dump(mode="json")
                or self.source_manifest.get("allocation_input")
                != plan.allocation_input.model_dump(mode="json")
                or self.source_manifest.get("complete_native_basis")
                != plan.inputs.joint.original_actual_input.base.financial_basis
            )
        ):
            raise ValueError(
                "Declared scope world must retain its current complete native original"
            )
        return self


def economic_signature(evaluation: JointFixedEvaluation) -> str | None:
    """Compare all economic effects, never derived keys or planning-only scope identity.

    Original scope permissions and validity remain in separate exact source fingerprints.
    A shared vector does not select a scope or authorize an original financial request.
    """
    plan = evaluation.native_preview.plan
    if evaluation.status != "STABLE" or plan is None:
        return None
    effects = []
    for child in plan.children:
        effect = child.command.effect.model_dump(mode="json")
        effect["operation_id"] = f"derived_joint_child:{child.child_number}"
        effect.pop("valid_from")
        effect.pop("expires_at")
        effects.append(
            {
                "number": child.child_number,
                "goal_id": str(child.goal_id),
                "effect": effect,
                "original_model_evidence_id": str(child.original_model_evidence_id),
                "original_model_evidence_hash": child.original_model_evidence_hash,
            }
        )
    return configuration_hash(
        {
            "protocol": PROTOCOL,
            "registered_goal_ids": [
                str(value) for value in evaluation.native_preview.registered_goal_ids
            ],
            "goal_allocations": [goal.model_dump(mode="json") for goal in plan.allocation.goals],
            "total_cents": plan.total_allocation_cents,
            "children": effects,
            "execution_mode": plan.execution_mode,
            "cross_operation_atomicity": plan.cross_operation_atomicity,
            "requires_separate_whole_financial_confirmation": True,
        }
    )


class ScopeQuestion(BoundaryModel):
    variable_id: Literal["REGISTERED_SCOPE_CHOICE"] = "REGISTERED_SCOPE_CHOICE"
    wording: Literal["这次安排采用哪一条已经确认的联合规则？"] = (
        "这次安排采用哪一条已经确认的联合规则？"
    )
    source_fingerprint: Hash
    worst_residual_signature_count: Literal[1] = 1
    partitions: Annotated[list[AnswerPartition], Field(min_length=2, max_length=2)]
    question_hash: Hash

    @model_validator(mode="after")
    def exact_partition(self) -> Self:
        if (
            len({part.choice_key for part in self.partitions}) != len(self.partitions)
            or any(
                part.world_keys != [part.choice_key]
                or len(part.signatures) != 1
                or part.residual_signature_count != 1
                for part in self.partitions
            )
            or self.question_hash
            != configuration_hash(self.model_dump(mode="json", exclude={"question_hash"}))
        ):
            raise ValueError("One finite scope variable needs complete singleton answer partitions")
        return self


class ScopeEvaluation(BoundaryModel):
    protocol: Literal["zhiyu-full-joint-scope-question-v1"] = PROTOCOL
    supported_input: Literal["EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES"] = (
        "EXPLICIT_REGISTERED_SCOPE_ALTERNATIVES"
    )
    worlds: Annotated[list[RegisteredScopeWorld], Field(min_length=2, max_length=2)]
    source_fingerprint: Hash
    selected_policy_id: UUID | None = None
    status: Literal["STABLE", "DIVERGENT", "UNKNOWN"]
    distinct_signatures: list[str]
    pending_question: ScopeQuestion | None
    stable: bool | None
    should_ask: bool | None
    probability_model: Literal["NONE"] = "NONE"
    question_selection: Literal["ONE_DECLARED_VARIABLE_MINIMAX"] = "ONE_DECLARED_VARIABLE_MINIMAX"
    bank_authority: Literal[False] = False
    execution_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False
    requires_separate_whole_financial_confirmation: Literal[True] = True

    @model_validator(mode="after")
    def finite_complete_result(self) -> Self:
        ids = [world.full_policy_id for world in self.worlds]
        if (
            ids != sorted(set(ids), key=str)
            or self.selected_policy_id is not None
            and self.selected_policy_id not in ids
        ):
            raise ValueError(
                "Only the explicit sorted complete registered scope domain can be selected"
            )
        owners = {
            (
                world.native_evaluation.native_preview.user_id,
                world.native_evaluation.native_preview.epoch_id,
                world.native_evaluation.native_preview.as_of,
                tuple(world.native_evaluation.native_preview.registered_goal_ids),
            )
            for world in self.worlds
        }
        if len(owners) != 1:
            raise ValueError(
                "Native worlds require one owner, epoch, clock and complete Goal denominator"
            )
        known = all(world.economic_signature is not None for world in self.worlds)
        active = [
            world
            for world in self.worlds
            if self.selected_policy_id in (None, world.full_policy_id)
        ]
        signatures = sorted(
            {world.economic_signature for world in active if world.economic_signature is not None}
        )
        expected = "UNKNOWN" if not known else "STABLE" if len(signatures) == 1 else "DIVERGENT"
        question = (
            make_question(self.worlds, self.source_fingerprint) if expected == "DIVERGENT" else None
        )
        if (
            self.status != expected
            or self.distinct_signatures != signatures
            or self.stable != (None if not known else expected == "STABLE")
            or self.should_ask != (None if not known else expected == "DIVERGENT")
            or self.pending_question != question
        ):
            raise ValueError(
                "Finite comparison cannot invent a known world, preference or question"
            )
        return self


def make_question(worlds: list[RegisteredScopeWorld], fingerprint: str) -> ScopeQuestion:
    values: dict[str, Any] = dict(
        source_fingerprint=fingerprint,
        partitions=[
            AnswerPartition(
                choice_key=str(world.full_policy_id),
                world_keys=[str(world.full_policy_id)],
                signatures=[world.economic_signature],
                residual_signature_count=1,
            )
            for world in worlds
            if world.economic_signature is not None
        ],
    )
    temporary = ScopeQuestion.model_construct(**values, question_hash="0" * 64)
    return ScopeQuestion(
        **values,
        question_hash=configuration_hash(
            temporary.model_dump(mode="json", exclude={"question_hash"})
        ),
    )


def scope_source_fingerprint(manifest: dict[str, Any]) -> str:
    if (
        manifest.get("protocol") != PROTOCOL
        or manifest.get("source_fingerprint_version") != SOURCE_VERSION
    ):
        raise ValueError("Unsupported declared scope source comparison")
    return configuration_hash(manifest)


def evaluate_scopes(
    worlds: list[RegisteredScopeWorld], manifest: dict[str, Any], selected: UUID | None = None
) -> ScopeEvaluation:
    fingerprint = scope_source_fingerprint(manifest)
    worlds = sorted(worlds, key=lambda world: str(world.full_policy_id))
    known = all(world.economic_signature is not None for world in worlds)
    active = [world for world in worlds if selected in (None, world.full_policy_id)]
    signatures = sorted(
        {world.economic_signature for world in active if world.economic_signature is not None}
    )
    status: Literal["STABLE", "DIVERGENT", "UNKNOWN"] = (
        "UNKNOWN" if not known else "STABLE" if len(signatures) == 1 else "DIVERGENT"
    )
    return ScopeEvaluation(
        worlds=worlds,
        source_fingerprint=fingerprint,
        selected_policy_id=selected,
        status=status,
        distinct_signatures=signatures,
        pending_question=make_question(worlds, fingerprint) if status == "DIVERGENT" else None,
        stable=None if not known else status == "STABLE",
        should_ask=None if not known else status == "DIVERGENT",
    )


class ScopeQuestionRevision(BoundaryModel):
    protocol: Literal["zhiyu-full-joint-scope-question-v1"] = PROTOCOL
    user_id: UUID
    epoch_id: UUID
    session_id: UUID
    revision: Annotated[StrictInt, Field(ge=1, le=17)]
    previous_receipt_hash: Hash | None
    command_id: UUID
    command_kind: Literal["START", "ANSWER", "REFRESH", "REBASE", "CLOSE"]
    authenticated_actor: LocalActorPrincipal
    original_request: dict[str, Any]
    as_of: datetime
    valid_until: datetime
    source_manifest: dict[str, Any]
    source_fingerprint: Hash
    evaluation: ScopeEvaluation
    state: Literal["READY_FOR_REVIEW", "AWAITING_INPUT", "UNKNOWN", "CLOSED"]
    receipt_hash: Hash
    bank_authority: Literal[False] = False
    execution_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False

    @model_validator(mode="after")
    def exact_original(self) -> Self:
        require_local_user(self.authenticated_actor, self.user_id, self.as_of)
        if self.state != "CLOSED" and not (
            self.as_of < self.valid_until <= self.as_of + timedelta(minutes=15)
            and self.revision <= 16
        ):
            raise ValueError("Finite scope observations expire and keep a separate closing slot")
        if set(self.original_request) != {"kind", "path", "body"}:
            raise ValueError("Original scope request differs")
        body = self.original_request["body"]
        kinds = ["REFRESH", "ANSWER"] if self.command_kind == "REBASE" else [self.command_kind]
        if (
            self.original_request["kind"] not in ["FULL_JOINT_SCOPE_" + kind for kind in kinds]
            or body.get("client_request_id") != str(self.command_id)
            or body.get("expected_epoch_id") != str(self.epoch_id)
            or (self.state == "CLOSED") != (self.command_kind == "CLOSE")
            or any(
                (
                    world.native_evaluation.native_preview.user_id,
                    world.native_evaluation.native_preview.epoch_id,
                )
                != (self.user_id, self.epoch_id)
                for world in self.evaluation.worlds
            )
        ):
            raise ValueError("Original owner, epoch or command differs")
        expected_state = {
            "STABLE": "READY_FOR_REVIEW",
            "DIVERGENT": "AWAITING_INPUT",
            "UNKNOWN": "UNKNOWN",
        }[self.evaluation.status]
        if self.command_kind == "START":
            refs = [
                RegisteredScopeReference.model_validate_json(json.dumps(ref))
                for ref in body.get("alternatives", [])
            ]
            if sorted(
                [(ref.full_policy_id, ref.expected_full_policy_version_id) for ref in refs],
                key=lambda value: str(value[0]),
            ) != [
                (world.full_policy_id, world.current_full_policy_version_id)
                for world in self.evaluation.worlds
            ]:
                raise ValueError(
                    "START must retain exactly the explicit expected registered scope references"
                )
        if self.state != "CLOSED" and (
            self.state != expected_state
            or any(
                world.native_evaluation.native_preview.as_of != self.as_of
                for world in self.evaluation.worlds
            )
        ):
            raise ValueError("Receipt must retain this invocation's complete native worlds")
        expected_worlds = [
            {
                "full_policy_id": str(world.full_policy_id),
                "current_full_policy_version_id": str(world.current_full_policy_version_id),
                "native_source_fingerprint": world.source_fingerprint,
                "economic_signature": world.economic_signature,
            }
            for world in self.evaluation.worlds
        ]
        if (
            self.source_manifest
            != {
                "protocol": PROTOCOL,
                "source_fingerprint_version": SOURCE_VERSION,
                "user_id": str(self.user_id),
                "epoch_id": str(self.epoch_id),
                "worlds": expected_worlds,
            }
            or self.source_fingerprint != scope_source_fingerprint(self.source_manifest)
            or self.evaluation.source_fingerprint != self.source_fingerprint
            or self.receipt_hash
            != configuration_hash(self.model_dump(mode="json", exclude={"receipt_hash"}))
        ):
            raise ValueError("Exact whole-domain source manifest or original receipt differs")
        return self
