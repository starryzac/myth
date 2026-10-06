"""Durable notification identities; preferences and receipts grant no financial authority."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.domain.boundary_types import BoundaryModel
from app.domain.full_action_set_boundary import GlobalBoundaryObservation
from app.domain.full_action_set_boundary_actual import ActualGlobalBoundaryObservation
from app.domain.full_action_set_boundary_full import FullGlobalBoundaryObservation
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.domain.question_workflow import CommandKey, PendingPlanningQuestion, QuestionRevision
from pydantic import Field, StrictBool, StrictInt, TypeAdapter, field_validator, model_validator

PROTOCOL: Literal["full-intervention-message-v1"] = "full-intervention-message-v1"
ALGORITHM = "full-intervention-v1"
CONSUMER = "intervention-center-v1"
NAMESPACE = UUID("e8e2b21b-0675-4aa7-9b60-616fdd118da2")
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
State = Literal["PENDING", "ACKNOWLEDGED", "INVALIDATED", "RECORDED_ONLY", "DEFERRED"]


class QuestionObservationRequest(BoundaryModel):
    kind: Literal["QUESTION"]
    session_id: UUIDReference
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]
    expected_run_id: UUIDReference
    reviewed_source_trace_hash: Hash
    expected_epoch_id: UUIDReference
    intervention_policy_id: UUIDReference | None = None
    idempotency_key: CommandKey


class BoundaryObservationRequest(BoundaryModel):
    kind: Literal["SINGLE_ACTION_BOUNDARY"]
    observation_run_id: UUIDReference
    reviewed_source_trace_hash: Hash
    expected_epoch_id: UUIDReference
    intervention_policy_id: UUIDReference | None = None
    idempotency_key: CommandKey


class GlobalBoundaryObservationRequest(BoundaryModel):
    kind: Literal["GLOBAL_ACTION_SET_BOUNDARY"]
    observation_run_id: UUIDReference
    reviewed_source_trace_hash: Hash
    expected_epoch_id: UUIDReference
    intervention_policy_id: UUIDReference | None = None
    idempotency_key: CommandKey


ObserveRequest = Annotated[
    QuestionObservationRequest | BoundaryObservationRequest | GlobalBoundaryObservationRequest,
    Field(discriminator="kind"),
]


class DeliveryRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_payload_hash: Hash


class AcknowledgmentRequest(DeliveryRequest):
    idempotency_key: CommandKey
    acknowledged: Literal[True]

    @field_validator("acknowledged", mode="before")
    @classmethod
    def explicit_boolean(cls, value: Any) -> Literal[True]:
        if value is not True:
            raise ValueError("Only explicit boolean true acknowledges an original notification")
        return True


class InterventionMessage(BoundaryModel):
    protocol: Literal["full-intervention-message-v1"] = PROTOCOL
    message_id: UUID
    user_id: UUID
    epoch_id: UUID
    source_kind: Literal["QUESTION", "SINGLE_ACTION_BOUNDARY", "GLOBAL_ACTION_SET_BOUNDARY"]
    source_run_id: UUID
    source_trace_hash: Hash
    semantic_key: Hash
    creation_command_run_id: UUID
    session_id: UUID | None
    question_revision: Annotated[StrictInt, Field(ge=1, le=16)] | None
    question: PendingPlanningQuestion | None
    boundary_observation: dict[str, Any] | None
    intervention_policy_binding: dict[str, Any] | None
    requires_user_attention: bool
    created_at: datetime
    bank_authority: Literal[False] = False
    answers_question: Literal[False] = False
    execution_eligible: Literal[False] = False
    global_action_set_complete: StrictBool = False

    @model_validator(mode="after")
    def exact_source(self) -> Self:
        if self.message_id != message_identity(self.user_id, self.epoch_id, self.semantic_key):
            raise ValueError("Message identity must bind owner, epoch and original semantics")
        if self.source_kind == "QUESTION":
            if (
                self.session_id is None
                or self.question_revision is None
                or self.question is None
                or self.boundary_observation is not None
                or not self.requires_user_attention
            ):
                raise ValueError("Question messages require the one exact original question")
        elif (
            self.session_id is not None
            or self.question_revision is not None
            or self.question is not None
            or self.boundary_observation is None
        ):
            raise ValueError("Boundary messages cannot claim a planning question")
        if self.source_kind == "GLOBAL_ACTION_SET_BOUNDARY":
            original: (
                GlobalBoundaryObservation
                | FullGlobalBoundaryObservation
                | ActualGlobalBoundaryObservation
            ) = TypeAdapter(
                GlobalBoundaryObservation
                | FullGlobalBoundaryObservation
                | ActualGlobalBoundaryObservation
            ).validate_json(json.dumps(self.boundary_observation))
            if (
                not self.global_action_set_complete
                or not original.global_action_set_complete
                or not original.snapshot.global_action_set_complete
                or original.snapshot.status != "COMPLETE"
                or original.snapshot.action_set_signature is None
                or original.previous_observation_run_id is None
                or original.kind is None
                or original.semantic_key != self.semantic_key
                or (original.user_id, original.epoch_id, original.observation_run_id)
                != (self.user_id, self.epoch_id, self.source_run_id)
                or (original.snapshot.user_id, original.snapshot.epoch_id)
                != (self.user_id, self.epoch_id)
                or original.requires_user_attention != self.requires_user_attention
                or original.requires_user_attention != (original.kind == "BoundaryCrossed")
                or original.idempotent_replay
            ):
                raise ValueError("Global messages require their complete original comparison")
        elif self.global_action_set_complete:
            raise ValueError("A question or single action cannot claim a complete global set")
        return self


class InterventionReceipt(BoundaryModel):
    protocol: Literal["full-intervention-command-v1"] = "full-intervention-command-v1"
    kind: Literal["OBSERVE", "ACKNOWLEDGE"]
    user_id: UUID
    epoch_id: UUID
    idempotency_key: CommandKey
    request_hash: Hash
    original_command: dict[str, Any]
    message_id: UUID
    payload_hash: Hash
    recorded_at: datetime
    duplicate_semantics: bool
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False

    @model_validator(mode="after")
    def exact_command(self) -> Self:
        if (
            configuration_hash(self.original_command) != self.request_hash
            or self.original_command.get("kind") != self.kind
            or self.original_command.get("user_id") != str(self.user_id)
        ):
            raise ValueError("Receipt must retain the complete exact original command")
        request = self.original_command.get("request")
        if not isinstance(request, dict) or (
            request.get("expected_epoch_id") != str(self.epoch_id)
            or request.get("idempotency_key") != self.idempotency_key
        ):
            raise ValueError("Receipt command epoch or key differs")
        if self.kind == "OBSERVE":
            if set(self.original_command) != {"kind", "user_id", "request"}:
                raise ValueError("Observe command envelope has unknown fields")
            TypeAdapter(ObserveRequest).validate_json(json.dumps(request))
        else:
            if (
                set(self.original_command) != {"kind", "user_id", "message_id", "request"}
                or self.original_command["message_id"] != str(self.message_id)
                or request.get("reviewed_payload_hash") != self.payload_hash
                or self.duplicate_semantics
            ):
                raise ValueError("Acknowledgment cannot change the original message or content")
            AcknowledgmentRequest.model_validate_json(json.dumps(request))
        return self


def message_identity(user: UUID, epoch: UUID, semantic_key: str) -> UUID:
    return uuid5(NAMESPACE, f"message:{user}:{epoch}:{semantic_key}")


def command_identity(user: UUID, epoch: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE, f"command:{user}:{epoch}:{key}")


def question_semantics(state: QuestionRevision) -> str:
    """Preserve all remaining-world signatures and their original choice partitions."""
    question = state.pending_question
    result = state.evaluation
    if (
        question is None
        or state.state != "PENDING_ANSWER"
        or not result.complete_within_declared_domain
        or result.unknown_or_unsupported_world_count
        or result.known_world_count != result.expected_world_count
        or len(result.worlds) != result.expected_world_count
        or result.question is None
        or any(
            world.outcome.signature is None
            or world.outcome.status != "KNOWN"
            or world.world_key != configuration_hash(world.assignments)
            for world in result.worlds
        )
        or len({world.world_key for world in result.worlds}) != len(result.worlds)
        or result.distinct_signatures
        != sorted(
            {
                world.outcome.signature
                for world in result.worlds
                if world.outcome.signature is not None
            }
        )
    ):
        raise ValueError("Unknown or incomplete worlds cannot imply equivalent economics")
    variable = next(row for row in state.variables if row.variable_id == question.variable_id)
    worlds = sorted(
        [
            {"assignments": world.assignments, "signature": world.outcome.signature}
            for world in result.worlds
        ],
        key=configuration_hash,
    )
    return configuration_hash(
        {
            "kind": "QUESTION",
            "base_action_id": str(state.base_action_id),
            "variable": variable.model_dump(mode="json"),
            "worlds": worlds,
            "partitions": [
                {
                    "choice_key": row.choice_key,
                    "signatures": row.signatures,
                    "residual_signature_count": row.residual_signature_count,
                }
                for row in result.question.partitions
            ],
        }
    )


def boundary_semantics(before: str, after: str) -> str:
    # Original signatures already retain money, property, timing, loss and permission.
    for digest in (before, after):
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Boundary signature must be an exact verified digest")
    return configuration_hash(
        {"scope": "ORIGINAL_SINGLE_ACTION_COMPARISON", "before": before, "after": after}
    )


def effective_state(
    stored: State, *, source_status: Literal["CURRENT", "STALE", "UNKNOWN", "ARCHIVED"]
) -> State | Literal["UNKNOWN", "ARCHIVED"]:
    if stored in {"ACKNOWLEDGED", "INVALIDATED", "RECORDED_ONLY"}:
        return stored
    return (
        "ARCHIVED"
        if source_status == "ARCHIVED"
        else (
            "INVALIDATED"
            if source_status == "STALE"
            else ("UNKNOWN" if source_status == "UNKNOWN" else stored)
        )
    )
