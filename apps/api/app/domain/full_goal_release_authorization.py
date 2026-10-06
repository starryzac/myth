"""Explicit emergency release consent, separate from FULL planning confirmation.

This protocol binds permission, never a present financial amount or bank fact.
An execution adapter must still verify ownership, the emergency and cumulative use.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from app.domain.boundary_types import BoundaryModel
from app.domain.full_policy_configuration import EmergencyCondition
from app.domain.policy_configuration import MoneyCents, UUIDReference, configuration_hash
from pydantic import Field, StrictBool, model_validator

PROTOCOL: Literal["full-goal-release-authorization-v1"] = "full-goal-release-authorization-v1"
SOURCE = "FULL_GOAL_RELEASE_AUTHORIZATION"
NAMESPACE = UUID("c341ff79-6c74-5fc9-a892-b9f5a53ac303")
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, Field(min_length=1, max_length=150)]
PositiveMoney = Annotated[MoneyCents, Field(gt=0)]


class GoalReleaseBinding(BoundaryModel):
    goal_id: UUID
    original_policy_id: UUID
    original_policy_version_id: UUID
    full_model_evidence_id: UUID
    full_model_evidence_hash: Hash
    full_configuration_hash: Hash
    minimum_guarantee_cents: MoneyCents


class GoalReleaseScope(BoundaryModel):
    protocol: Literal["full-goal-release-authorization-v1"] = PROTOCOL
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    policy_version_id: UUID
    policy_configuration_hash: Hash
    source_goals: Annotated[list[GoalReleaseBinding], Field(min_length=1, max_length=32)]
    emergency_conditions: Annotated[list[EmergencyCondition], Field(min_length=1, max_length=3)]
    destination_scope: Literal["PROTECTED_CASH"] = "PROTECTED_CASH"
    single_action_cap_cents: PositiveMoney
    total_cap_cents: PositiveMoney
    valid_from: datetime
    valid_until: datetime
    principal_release_allowed: Literal[False] = False
    ordinary_goal_redistribution_allowed: Literal[False] = False
    creates_new_income: Literal[False] = False
    changes_original_assigned_income: Literal[False] = False
    cumulative_scope: Literal["POLICY_ID_ALL_VERSIONS"] = "POLICY_ID_ALL_VERSIONS"
    overrides_default_lock_only_for_listed_emergencies: Literal[True] = True

    @model_validator(mode="after")
    def bounded_scope(self) -> Self:
        identities = [goal.goal_id for goal in self.source_goals]
        if identities != sorted(set(identities), key=str):
            raise ValueError("Source goals require unique canonical identities")
        if len(set(self.emergency_conditions)) != len(self.emergency_conditions):
            raise ValueError("Emergency conditions must be unique")
        if self.emergency_conditions != sorted(self.emergency_conditions):
            raise ValueError("Emergency conditions require canonical ordering")
        if self.single_action_cap_cents > self.total_cap_cents:
            raise ValueError("A single action cap cannot exceed the cumulative cap")
        if self.valid_from >= self.valid_until:
            raise ValueError("A release scope needs a finite nonempty validity window")
        return self


class ReleaseAuthorizationPreviewRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    expected_policy_version_id: UUIDReference


class ReleaseAuthorizationConfirmation(ReleaseAuthorizationPreviewRequest):
    reviewed_scope_hash: Hash
    accepted: StrictBool
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.accepted is not True or not self.idempotency_key.strip():
            raise ValueError(
                "Release permission needs explicit boolean consent and an original key"
            )
        return self


class GoalReleaseAuthorization(BoundaryModel):
    protocol: Literal["full-goal-release-authorization-v1"] = PROTOCOL
    authorization_id: UUID
    user_id: UUID
    epoch_id: UUID
    policy_id: UUID
    policy_version_id: UUID
    scope: GoalReleaseScope
    scope_hash: Hash
    accepted: StrictBool
    idempotency_key: Key
    original_request: ReleaseAuthorizationConfirmation
    request_hash: Hash
    confirmed_at: datetime
    valid_until: datetime
    permission_kind: Literal["EMERGENCY_GOAL_CASH_RELEASE"] = "EMERGENCY_GOAL_CASH_RELEASE"

    @model_validator(mode="after")
    def complete_original_consent(self) -> Self:
        expected_id = release_authorization_identity(
            self.user_id, self.epoch_id, self.idempotency_key
        )
        expected_request_hash = release_authorization_request_hash(
            self.user_id, self.policy_id, self.original_request
        )
        if (
            self.accepted is not True
            or self.authorization_id != expected_id
            or self.user_id != self.scope.user_id
            or self.epoch_id != self.scope.epoch_id
            or self.policy_id != self.scope.policy_id
            or self.policy_version_id != self.scope.policy_version_id
            or self.scope_hash != configuration_hash(self.scope.model_dump(mode="json"))
            or self.original_request.reviewed_scope_hash != self.scope_hash
            or self.original_request.expected_epoch_id != self.epoch_id
            or self.original_request.expected_policy_version_id != self.policy_version_id
            or self.original_request.idempotency_key != self.idempotency_key
            or self.request_hash != expected_request_hash
            or not self.scope.valid_from <= self.confirmed_at < self.scope.valid_until
            or self.valid_until != self.scope.valid_until
        ):
            raise ValueError("Release consent does not bind its complete original scope")
        return self


def release_authorization_identity(user_id: UUID, epoch_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{user_id}:{epoch_id}:{key}")


def release_authorization_request_hash(
    user_id: UUID, policy_id: UUID, body: ReleaseAuthorizationConfirmation
) -> str:
    return configuration_hash(
        {
            "kind": "CONFIRM_EMERGENCY_GOAL_RELEASE_PERMISSION",
            "user_id": str(user_id),
            "policy_id": str(policy_id),
            "request": body.model_dump(mode="json"),
        }
    )
