"""One complete registered native joint world; preferences grant no bank authority."""

import copy
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_joint_goal_execution import FullJointGoalPreview
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt, model_validator

PROTOCOL: Literal["zhiyu-full-joint-fixed-question-v1"] = "zhiyu-full-joint-fixed-question-v1"
SOURCE_VERSION = "full-joint-fixed-source-clock-v1"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def joint_vector_signature(preview: FullJointGoalPreview) -> str:
    """Keep every goal, source, amount and order, renaming only derived child identities."""
    plan = preview.plan
    if plan is None:
        raise ValueError("A complete native plan is required")
    children = []
    for child in plan.children:
        effect = child.command.effect.model_dump(mode="json")
        effect["operation_id"] = f"derived_joint_child:{child.child_number}"
        effect.pop("valid_from")
        effect.pop("expires_at")
        children.append(
            {
                "number": child.child_number,
                "goal_id": str(child.goal_id),
                "effect": effect,
                "model_evidence_id": str(child.original_model_evidence_id),
                "model_evidence_hash": child.original_model_evidence_hash,
            }
        )
    return configuration_hash(
        {
            "protocol": PROTOCOL,
            "registered_goal_ids": [str(value) for value in preview.registered_goal_ids],
            "allocation": plan.allocation.model_dump(mode="json", exclude={"input_hash"}),
            "total_cents": plan.total_allocation_cents,
            "children": children,
            "scope": plan.inputs.scope.model_dump(mode="json"),
            "execution_mode": plan.execution_mode,
            "cross_operation_atomicity": plan.cross_operation_atomicity,
            "requires_separate_whole_financial_confirmation": True,
        }
    )


class JointFixedEvaluation(BoundaryModel):
    protocol: Literal["zhiyu-full-joint-fixed-question-v1"] = PROTOCOL
    supported_input: Literal["REGISTERED_CONFIGURATION"] = "REGISTERED_CONFIGURATION"
    candidate_count: Literal[1] = 1
    native_preview: FullJointGoalPreview
    status: Literal["STABLE", "UNKNOWN"]
    stable: bool | None
    should_ask: bool | None
    vector_signature: Hash | None
    pending_question: Literal[None] = None
    preference_question_count: Literal[0] = 0
    divergent_candidate_production: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    bank_authority: Literal[False] = False
    execution_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False
    requires_separate_whole_financial_confirmation: Literal[True] = True

    @model_validator(mode="after")
    def exact_native_world(self) -> Self:
        preview, plan = self.native_preview, self.native_preview.plan
        known = (
            preview.status == "READY_TO_REVIEW"
            and not preview.reasons
            and not preview.unresolved_original_action_ids
            and plan is not None
        )
        if known:
            assert plan is not None
            if (
                plan.inputs.request != preview.request
                or (plan.user_id, plan.epoch_id, plan.prepared_at)
                != (preview.user_id, preview.epoch_id, preview.as_of)
                or len(set(preview.registered_goal_ids)) != len(preview.registered_goal_ids)
                or not 2 <= len(preview.registered_goal_ids) <= 8
                or set(preview.registered_goal_ids)
                != {goal.goal_id for goal in plan.allocation_input.goals}
                or set(preview.registered_goal_ids)
                != {goal.goal_id for goal in plan.allocation.goals}
                or len(plan.allocation_input.hard_protection_points) != 1098
            ):
                raise ValueError("Joint world must retain the whole original goal denominator")
        expected = joint_vector_signature(preview) if known else None
        if (
            self.status != ("STABLE" if known else "UNKNOWN")
            or self.stable != (True if known else None)
            or self.should_ask != (False if known else None)
            or self.vector_signature != expected
        ):
            raise ValueError("One fixed native world cannot claim divergent preferences")
        return self


def evaluate_fixed(preview: FullJointGoalPreview) -> JointFixedEvaluation:
    known = (
        preview.status == "READY_TO_REVIEW"
        and not preview.reasons
        and not preview.unresolved_original_action_ids
        and preview.plan is not None
    )
    return JointFixedEvaluation(
        native_preview=preview,
        status="STABLE" if known else "UNKNOWN",
        stable=True if known else None,
        should_ask=False if known else None,
        vector_signature=joint_vector_signature(preview) if known else None,
    )


def joint_source_fingerprint(manifest: dict[str, Any]) -> str:
    """Only recomputation labels are normalized; raw receipts remain exact."""
    from app.domain.decision_trace_types import TraceEvidence, TracePolicy
    from app.services.question_workflow import source_fingerprint

    if manifest.get("source_fingerprint_version") != SOURCE_VERSION:
        raise ValueError("Unsupported joint question source fingerprint version")
    native = source_fingerprint(
        {"autonomy_basis": manifest["complete_native_basis"]},
        [TraceEvidence.model_validate_json(json.dumps(row)) for row in manifest["sources"]],
        [TracePolicy.model_validate_json(json.dumps(row)) for row in manifest["policies"]],
    )
    compared = copy.deepcopy(manifest)
    compared["complete_native_basis"] = {"normalized_native_source_fingerprint": native}
    original = compared.get("allocation_input")
    if original is not None:
        original.pop("as_of")
    return configuration_hash(compared)


class JointQuestionRevision(BoundaryModel):
    protocol: Literal["zhiyu-full-joint-fixed-question-v1"] = PROTOCOL
    user_id: UUID
    epoch_id: UUID
    session_id: UUID
    revision: Annotated[StrictInt, Field(ge=1, le=17)]
    previous_receipt_hash: Hash | None
    command_id: UUID
    command_kind: Literal["START", "REFRESH", "REBASE", "CLOSE"]
    authenticated_actor: LocalActorPrincipal
    original_request: dict[str, Any]
    as_of: datetime
    valid_until: datetime
    source_manifest: dict[str, Any]
    source_fingerprint: Hash
    evaluation: JointFixedEvaluation
    state: Literal["READY_FOR_REVIEW", "UNKNOWN", "CLOSED"]
    receipt_hash: Hash
    bank_authority: Literal[False] = False
    execution_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False

    @model_validator(mode="after")
    def exact_original_receipt(self) -> Self:
        require_local_user(self.authenticated_actor, self.user_id, self.as_of)
        if self.state != "CLOSED" and not (
            self.as_of < self.valid_until <= self.as_of + timedelta(minutes=15)
            and self.revision <= 16
        ):
            raise ValueError("Fresh joint preference receipts require a finite lifetime")
        if set(self.original_request) != {"kind", "path", "body"}:
            raise ValueError("Original joint preference request shape differs")
        body = self.original_request["body"]
        original_kind = "REFRESH" if self.command_kind == "REBASE" else self.command_kind
        if (
            body.get("client_request_id") != str(self.command_id)
            or body.get("expected_epoch_id") != str(self.epoch_id)
            or self.original_request["kind"] != "FULL_JOINT_FIXED_" + original_kind
            or (self.state == "CLOSED") != (self.command_kind == "CLOSE")
            or self.evaluation.native_preview.user_id != self.user_id
            or self.evaluation.native_preview.epoch_id != self.epoch_id
        ):
            raise ValueError("Exact original owner, epoch or command differs")
        expected = "UNKNOWN" if self.evaluation.status == "UNKNOWN" else "READY_FOR_REVIEW"
        if self.state != "CLOSED" and self.state != expected:
            raise ValueError("Receipt state must retain the complete native world result")
        if self.state != "CLOSED" and self.evaluation.native_preview.as_of != self.as_of:
            raise ValueError("Only this fresh invocation may supply a current joint world")
        plan = self.evaluation.native_preview.plan
        if (
            self.source_manifest.get("protocol") != PROTOCOL
            or self.source_manifest.get("user_id") != str(self.user_id)
            or self.source_manifest.get("epoch_id") != str(self.epoch_id)
            or self.source_manifest.get("native_status") != self.evaluation.native_preview.status
            or self.source_manifest.get("native_reasons") != self.evaluation.native_preview.reasons
            or self.source_manifest.get("registered_goal_ids")
            != [str(value) for value in self.evaluation.native_preview.registered_goal_ids]
            or plan is not None
            and (
                self.source_manifest.get("full_scope") != plan.inputs.scope.model_dump(mode="json")
                or self.source_manifest.get("allocation_input")
                != plan.allocation_input.model_dump(mode="json")
            )
        ):
            raise ValueError("Source manifest must belong to its exact complete native world")
        if (
            joint_source_fingerprint(self.source_manifest) != self.source_fingerprint
            or self.source_manifest.get("vector_signature") != self.evaluation.vector_signature
            or self.receipt_hash
            != configuration_hash(self.model_dump(mode="json", exclude={"receipt_hash"}))
        ):
            raise ValueError("Original receipt or complete source fingerprint differs")
        return self
