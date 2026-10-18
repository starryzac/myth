"""Explicit selected cumulative mode; old scope/grant parsers remain unchanged."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.domain.zhiyu_standing_scope import Hash, SignedStandingGrant, StandingScope
from pydantic import Field, TypeAdapter, model_validator

NativeProtocol = Literal[
    "zhiyu-standing-delegated-native-v2", "zhiyu-standing-income-goal-native-v1"
]

NATIVE_PROTOCOL: Literal["zhiyu-standing-income-goal-native-v1"] = (
    "zhiyu-standing-income-goal-native-v1"
)
SCOPE_PROTOCOL: Literal["zhiyu-next-standing-income-goal-scope-v2"] = (
    "zhiyu-next-standing-income-goal-scope-v2"
)
GRANT_PROTOCOL: Literal["zhiyu-next-signed-income-goal-grant-v2"] = (
    "zhiyu-next-signed-income-goal-grant-v2"
)


class IncomeGoalScope(BoundaryModel):
    protocol: Literal["zhiyu-next-standing-income-goal-scope-v2"] = SCOPE_PROTOCOL
    goal_mode: Literal["CUMULATIVE_EVENT_TARGET"]
    native_scope: StandingScope

    @model_validator(mode="after")
    def only_selected_single_goal(self) -> Self:
        if self.native_scope.family != "SINGLE_GOAL":
            raise ValueError("Cumulative event mode is one owned goal, not another family")
        return self

    @property
    def user_id(self) -> UUID:
        return self.native_scope.user_id

    @property
    def epoch_id(self) -> UUID:
        return self.native_scope.epoch_id

    @property
    def family(self) -> Literal["SINGLE_GOAL"]:
        return "SINGLE_GOAL"

    @property
    def policy_version_ids(self) -> list[UUID]:
        return self.native_scope.policy_version_ids

    @property
    def source_account_ids(self) -> list[UUID]:
        return self.native_scope.source_account_ids

    @property
    def goal_ids(self) -> list[UUID]:
        return self.native_scope.goal_ids

    @property
    def product_ids(self) -> list[UUID]:
        return self.native_scope.product_ids

    @property
    def payee_id(self) -> str | None:
        return self.native_scope.payee_id

    @property
    def per_action_cap_cents(self) -> int:
        return self.native_scope.per_action_cap_cents

    @property
    def per_event_cap_cents(self) -> int:
        return self.native_scope.per_event_cap_cents

    @property
    def daily_cap_cents(self) -> int:
        return self.native_scope.daily_cap_cents

    @property
    def maximum_children(self) -> int:
        return self.native_scope.maximum_children

    @property
    def issued_at(self) -> datetime:
        return self.native_scope.issued_at

    @property
    def expires_at(self) -> datetime:
        return self.native_scope.expires_at


class SignedIncomeGoalGrant(BoundaryModel):
    protocol: Literal["zhiyu-next-signed-income-goal-grant-v2"] = GRANT_PROTOCOL
    grant_id: UUID
    scope: IncomeGoalScope
    reviewed_scope_hash: Hash
    actor_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime
    accepted: Literal[True]
    native_bank_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_separate_user_origin(self) -> Self:
        if self.reviewed_scope_hash != configuration_hash(self.scope.model_dump(mode="json")):
            raise ValueError("USER must review this exact cumulative-event scope")
        # Original USER identity/finite window checks are reused with the real
        # native scope. This object is never persisted as a replacement v1 grant.
        SignedStandingGrant(
            grant_id=self.grant_id,
            scope=self.scope.native_scope,
            reviewed_scope_hash=configuration_hash(self.scope.native_scope.model_dump(mode="json")),
            actor_at_confirmation=self.actor_at_confirmation,
            confirmed_at=self.confirmed_at,
            accepted=self.accepted,
        )
        return self


AnyScope = Annotated[StandingScope | IncomeGoalScope, Field(discriminator="protocol")]
AnyGrant = Annotated[SignedStandingGrant | SignedIncomeGoalGrant, Field(discriminator="protocol")]
SCOPE_ADAPTER: TypeAdapter[StandingScope | IncomeGoalScope] = TypeAdapter(AnyScope)
GRANT_ADAPTER: TypeAdapter[SignedStandingGrant | SignedIncomeGoalGrant] = TypeAdapter(AnyGrant)


def parse_scope(raw: str) -> StandingScope | IncomeGoalScope:
    return SCOPE_ADAPTER.validate_json(raw)


def parse_grant(raw: str) -> SignedStandingGrant | SignedIncomeGoalGrant:
    return GRANT_ADAPTER.validate_json(raw)


def native_scope(scope: StandingScope | IncomeGoalScope) -> StandingScope:
    return scope.native_scope if isinstance(scope, IncomeGoalScope) else scope


def native_variant(grant: SignedStandingGrant | SignedIncomeGoalGrant) -> NativeProtocol:
    return (
        NATIVE_PROTOCOL
        if isinstance(grant, SignedIncomeGoalGrant)
        else "zhiyu-standing-delegated-native-v2"
    )
