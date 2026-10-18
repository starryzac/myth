"""Explicit paired USER scope and conserved goal-to-goal cash commands.

This is a separate protocol. The old PROTECTED_CASH configuration, consent and
three-leg release cannot be interpreted as permission for this operation.
These contracts verify bindings; only the production adapter can verify actual
current policy/goal/Bank/audit originals. No constructor grants authority.
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel, BoundaryResult
from app.domain.full_goal_release_execution import (
    Cents,
    GoalReleaseLegSpec,
    GoalReleasePostingOriginal,
    GoalReleaseUse,
    Hash,
    PositiveCents,
    ZeroCents,
)
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import (
    CommonConfiguration,
    UUIDReference,
    configuration_hash,
)
from pydantic import Field, StrictBool, model_validator

NAMESPACE = UUID("b360811c-b870-5162-a877-9f7eddb330af")
Key = Annotated[str, Field(min_length=1, max_length=150)]
GoalIDs = Annotated[list[UUIDReference], Field(min_length=1, max_length=32)]


class GoalTransferPolicyConfiguration(CommonConfiguration):
    """New explicit configuration; never a changed default on the old V1 class."""

    type: Literal["cross_goal_reallocation"]
    scope_version: Literal["GOAL_TRANSFER_V1"]
    destination_scope: Literal["GOAL"]
    enabled: StrictBool
    source_goal_ids: GoalIDs
    target_goal_ids: GoalIDs
    single_action_cap_cents: PositiveCents
    total_cap_cents: PositiveCents
    must_not_reduce_policy_ids: Annotated[list[UUIDReference], Field(max_length=32)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def explicit_finite_pair_set(self) -> Self:
        if (
            len(set(self.source_goal_ids)) != len(self.source_goal_ids)
            or len(set(self.target_goal_ids)) != len(self.target_goal_ids)
            or len(set(self.must_not_reduce_policy_ids)) != len(self.must_not_reduce_policy_ids)
            or self.single_action_cap_cents > self.total_cap_cents
            or self.valid_from is None
            or self.valid_until is None
            or self.valid_from > self.valid_until
        ):
            raise ValueError(
                "Goal transfer needs explicit finite sources, receivers, dates and caps"
            )
        return self


class GoalTransferPolicyBinding(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    configuration: GoalTransferPolicyConfiguration
    configuration_hash: Hash

    @model_validator(mode="after")
    def original_configuration(self) -> Self:
        if self.configuration_hash != configuration_hash(
            self.configuration.model_dump(mode="json")
        ):
            raise ValueError("Transfer policy must bind the complete new GOAL configuration")
        return self


class GoalTransferGoalBinding(BoundaryModel):
    goal_id: UUID
    account_id: UUID
    policy_id: UUID
    policy_version_id: UUID
    full_model_evidence_id: UUID
    full_model_evidence_hash: Hash
    full_configuration_hash: Hash
    minimum_guarantee_cents: Cents
    target_cents: PositiveCents

    @model_validator(mode="after")
    def minimum_within_target(self) -> Self:
        if self.minimum_guarantee_cents > self.target_cents:
            raise ValueError("Goal minimum cannot exceed its original target")
        return self


class GoalTransferPermission(BoundaryModel):
    role: Literal["RELEASE_SOURCE", "RECEIVE_TARGET"]
    goal: GoalTransferGoalBinding
    amount_cents: PositiveCents


class GoalTransferScope(BoundaryModel):
    protocol: Literal["full-goal-transfer-paired-scope-v1"] = "full-goal-transfer-paired-scope-v1"
    user_id: UUID
    epoch_id: UUID
    policy: GoalTransferPolicyBinding
    permissions: Annotated[list[GoalTransferPermission], Field(min_length=2, max_length=2)]
    timezone: Literal["Asia/Shanghai", "UTC"]
    valid_from: datetime
    valid_until: datetime
    authority_kind: Literal["NEW_USER_PAIRED_GOAL_TRANSFER"] = "NEW_USER_PAIRED_GOAL_TRANSFER"
    standing_authority: Literal[False] = False
    inherits_protected_cash_authority: Literal[False] = False
    cumulative_scope: Literal["POLICY_ID_ALL_VERSIONS"] = "POLICY_ID_ALL_VERSIONS"

    @model_validator(mode="after")
    def paired_scope(self) -> Self:
        source, target = self.permissions
        config = self.policy.configuration
        if (
            [row.role for row in self.permissions] != ["RELEASE_SOURCE", "RECEIVE_TARGET"]
            or source.goal.goal_id == target.goal.goal_id
            or source.goal.account_id == target.goal.account_id
            or source.amount_cents != target.amount_cents
            or not config.enabled
            or source.goal.goal_id not in config.source_goal_ids
            or target.goal.goal_id not in config.target_goal_ids
            or source.amount_cents > config.single_action_cap_cents
            or not 0 < (self.valid_until - self.valid_from).total_seconds() <= 900
            or config.valid_from is None
            or config.valid_until is None
            or self.valid_from.astimezone(ZoneInfo(self.timezone)).date() < config.valid_from
            or self.valid_until.astimezone(ZoneInfo(self.timezone)).date() > config.valid_until
        ):
            raise ValueError("Transfer scope requires both exact current goal permissions and caps")
        return self

    @property
    def source(self) -> GoalTransferGoalBinding:
        return self.permissions[0].goal

    @property
    def target(self) -> GoalTransferGoalBinding:
        return self.permissions[1].goal

    @property
    def amount_cents(self) -> int:
        return self.permissions[0].amount_cents


class GoalTransferCurrentScope(GoalTransferScope):
    """Explicit extension: old paired-scope serializer remains unchanged."""

    source_provenance_version: Literal["goal-transfer-current-provenance-v1"]
    current_provenance_hash: Hash


class GoalTransferConfirmRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_scope_hash: Hash
    accepted: StrictBool
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit_pair_confirmation(self) -> Self:
        if self.accepted is not True or not self.idempotency_key.strip():
            raise ValueError("A new paired scope needs explicit USER confirmation and original key")
        return self


def goal_transfer_action_identity(user: UUID, epoch: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{user}:{epoch}:{key}")


def goal_transfer_bank_key(user: UUID, epoch: UUID, key: str) -> str:
    return "goal-transfer:" + configuration_hash(
        {"user_id": str(user), "epoch_id": str(epoch), "key": key}
    )


class GoalTransferAuthorization(BoundaryModel):
    protocol: Literal["full-goal-transfer-authorization-v1"] = "full-goal-transfer-authorization-v1"
    authorization_id: UUID
    scope: GoalTransferCurrentScope | GoalTransferScope
    scope_hash: Hash
    original_request: GoalTransferConfirmRequest
    principal_at_confirmation: LocalActorPrincipal
    confirmed_at: datetime
    request_hash: Hash

    @model_validator(mode="after")
    def original_pair_authorization(self) -> Self:
        scope, request = self.scope, self.original_request
        require_local_user(self.principal_at_confirmation, scope.user_id, self.confirmed_at)
        if (
            self.authorization_id
            != goal_transfer_action_identity(scope.user_id, scope.epoch_id, request.idempotency_key)
            or self.scope_hash != configuration_hash(scope.model_dump(mode="json"))
            or request.expected_epoch_id != scope.epoch_id
            or request.reviewed_scope_hash != self.scope_hash
            or not scope.valid_from <= self.confirmed_at < scope.valid_until
            or self.request_hash
            != configuration_hash(
                {"user_id": str(scope.user_id), "request": request.model_dump(mode="json")}
            )
        ):
            raise ValueError(
                "Paired USER consent must bind the whole new scope and original request"
            )
        return self


class GoalTransferEffect(BoundaryModel):
    protocol: Literal["full-goal-transfer-effect-v1"] = "full-goal-transfer-effect-v1"
    simulation: Literal[True] = True
    action_type: Literal["TRANSFER_GOAL"] = "TRANSFER_GOAL"
    operation_id: UUID
    business_key: Key
    bank_idempotency_key: Key
    authorization_id: UUID
    authorization_evidence_id: UUID
    authorization_evidence_hash: Hash
    authorization_request_hash: Hash
    scope: GoalTransferCurrentScope | GoalTransferScope
    scope_hash: Hash
    source_uses: Annotated[list[GoalReleaseUse], Field(min_length=1, max_length=10000)]
    source_provenance_hash: Hash
    target_ownership_hash: Hash
    financial_input_hash: Hash
    protection_input_hash: Hash
    valid_from: datetime
    expires_at: datetime
    fee_cents: ZeroCents = 0
    loss_cents: ZeroCents = 0
    principal_change_cents: ZeroCents = 0
    new_income_cents: ZeroCents = 0
    available_income_change_cents: ZeroCents = 0
    assigned_income_change_cents: ZeroCents = 0

    @model_validator(mode="after")
    def exact_pair_economics(self) -> Self:
        keys = [(row.allocation_action_id, row.fragment_id) for row in self.source_uses]
        if (
            keys != sorted(set(keys))
            or sum(row.amount_cents for row in self.source_uses) != self.scope.amount_cents
            or self.scope_hash != configuration_hash(self.scope.model_dump(mode="json"))
            or self.operation_id != self.authorization_id
            or not self.scope.valid_from
            <= self.valid_from
            < self.expires_at
            <= self.scope.valid_until
            or not self.business_key.strip()
            or not self.bank_idempotency_key.strip()
        ):
            raise ValueError(
                "Transfer command must preserve both goals, original slices and clocks"
            )
        return self

    @property
    def user_id(self) -> UUID:
        return self.scope.user_id

    @property
    def epoch_id(self) -> UUID:
        return self.scope.epoch_id

    @property
    def source_goal_id(self) -> UUID:
        return self.scope.source.goal_id

    @property
    def target_goal_id(self) -> UUID:
        return self.scope.target.goal_id

    @property
    def source_account_id(self) -> UUID:
        return self.scope.source.account_id

    @property
    def destination_account_id(self) -> UUID:
        return self.scope.target.account_id

    @property
    def amount_cents(self) -> int:
        return self.scope.amount_cents


def goal_transfer_effect_hash(effect: GoalTransferEffect) -> str:
    checked = GoalTransferEffect.model_validate(effect.model_dump())
    return configuration_hash(checked.model_dump(mode="json"))


class GoalTransferBankCommand(BoundaryModel):
    protocol: Literal["full-goal-transfer-bank-v1"] = "full-goal-transfer-bank-v1"
    effect: GoalTransferEffect
    effect_hash: Hash

    @model_validator(mode="after")
    def exact_effect(self) -> Self:
        if self.effect_hash != goal_transfer_effect_hash(self.effect):
            raise ValueError("Transfer bank hash must bind the complete paired effect")
        return self


def validate_goal_transfer_authorization(
    command: GoalTransferBankCommand,
    original: GoalTransferAuthorization,
    *,
    current_policy: GoalTransferPolicyBinding,
    current_source: GoalTransferGoalBinding,
    current_target: GoalTransferGoalBinding,
    current_epoch: UUID,
    policy_lifetime_used_cents: int,
    now: datetime,
) -> None:
    """Internal adapter inputs must be freshly read originals, never client claims."""
    effect, scope = command.effect, original.scope
    request = original.original_request
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or effect.scope != scope
        or effect.scope_hash != original.scope_hash
        or effect.authorization_id != original.authorization_id
        or effect.authorization_request_hash != original.request_hash
        or current_policy != scope.policy
        or current_source != scope.source
        or current_target != scope.target
        or current_epoch != scope.epoch_id
        or effect.bank_idempotency_key
        != goal_transfer_bank_key(scope.user_id, scope.epoch_id, request.idempotency_key)
        or effect.business_key != effect.bank_idempotency_key
        or not original.confirmed_at <= effect.valid_from <= now < effect.expires_at
        or type(policy_lifetime_used_cents) is not int
        or policy_lifetime_used_cents < 0
        or policy_lifetime_used_cents + scope.amount_cents
        > current_policy.configuration.total_cap_cents
    ):
        raise ValueError(
            "Both current original permissions, versions and lifetime cap are required"
        )


def goal_transfer_leg_specs(command: GoalTransferBankCommand) -> tuple[GoalReleaseLegSpec, ...]:
    scope = command.effect.scope
    source, target, amount = scope.source, scope.target, scope.amount_cents
    return (
        GoalReleaseLegSpec(
            leg_ref=f"cash:{source.account_id}",
            ledger_key=f"CASH:{source.account_id}",
            ledger_dimension="ECONOMIC",
            account_id=source.account_id,
            delta_cents=-amount,
            required_metadata={},
        ),
        GoalReleaseLegSpec(
            leg_ref=f"cash:{target.account_id}",
            ledger_key=f"CASH:{target.account_id}",
            ledger_dimension="ECONOMIC",
            account_id=target.account_id,
            delta_cents=amount,
            required_metadata={},
        ),
        GoalReleaseLegSpec(
            leg_ref=f"goal_cash:{source.goal_id}",
            ledger_key=f"GOAL_CASH:{source.goal_id}",
            ledger_dimension="GOAL_OWNERSHIP",
            account_id=source.account_id,
            delta_cents=-amount,
            required_metadata={
                "goal_id": str(source.goal_id),
                "account_id": str(source.account_id),
            },
        ),
        GoalReleaseLegSpec(
            leg_ref=f"goal_cash:{target.goal_id}",
            ledger_key=f"GOAL_CASH:{target.goal_id}",
            ledger_dimension="GOAL_OWNERSHIP",
            account_id=target.account_id,
            delta_cents=amount,
            required_metadata={
                "goal_id": str(target.goal_id),
                "account_id": str(target.account_id),
            },
        ),
    )


class GoalTransferSettlementOriginal(BoundaryModel):
    command: GoalTransferBankCommand
    bank_operation_id: UUID
    action_plan_id: UUID
    user_id: UUID
    operation_type: Literal["TRANSFER_GOAL"] = "TRANSFER_GOAL"
    request_hash: Hash
    business_key: str
    idempotency_key: str
    status: Literal["ACCEPTED", "UNKNOWN", "SETTLED", "REJECTED"]
    requested_at: datetime
    available_at: datetime
    settled_at: datetime | None
    postings: list[GoalReleasePostingOriginal]
    original_bank_row_hash: Hash


def validate_goal_transfer_bank_original(
    original: GoalTransferSettlementOriginal, now: datetime
) -> None:
    """Historical identity/legs only; expired current permission is not regranted."""
    command, effect = original.command, original.command.effect
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or original.bank_operation_id != original.action_plan_id
        or original.bank_operation_id != effect.operation_id
        or original.user_id != effect.scope.user_id
        or original.request_hash != configuration_hash(command.model_dump(mode="json"))
        or original.business_key != effect.business_key
        or original.idempotency_key != effect.bank_idempotency_key
        or not effect.valid_from <= original.requested_at < effect.expires_at
        or not original.requested_at <= original.available_at <= now
    ):
        raise ValueError("Transfer original Bank identity, request or clock differs")
    if original.status != "SETTLED":
        if original.postings or original.settled_at is not None:
            raise ValueError("Unsettled transfer cannot contain unexplained economic legs")
        return
    if original.settled_at is None or not original.available_at <= original.settled_at <= now:
        raise ValueError("Transfer settlement clock is not original and observed")
    expected = {row.leg_ref: row for row in goal_transfer_leg_specs(command)}
    if len(original.postings) != 4 or {row.leg_ref for row in original.postings} != set(expected):
        raise ValueError("Transfer settlement requires exactly the four original conserved legs")
    for row in original.postings:
        spec = expected[row.leg_ref]
        if (
            row.posting_id != uuid5(effect.operation_id, "posting:" + row.leg_ref)
            or row.user_id != original.user_id
            or row.operation_id != original.bank_operation_id
            or row.ledger_key != spec.ledger_key
            or row.ledger_dimension != spec.ledger_dimension
            or row.account_id != spec.account_id
            or row.position_id is not None
            or row.redemption_id is not None
            or row.external_fact_id is not None
            or row.delta_cents != spec.delta_cents
            or row.balance_before_cents + row.delta_cents != row.balance_after_cents
            or row.entry_kind == "OPENING"
            or row.occurred_at != original.settled_at
            or row.ledger_metadata != spec.required_metadata
        ):
            raise ValueError("Transfer leg changed its original goal, account, amount or identity")
    for dimension in ("ECONOMIC", "GOAL_OWNERSHIP"):
        if sum(row.delta_cents for row in original.postings if row.ledger_dimension == dimension):
            raise ValueError("Transfer must conserve cash and goal ownership separately")


def validate_goal_transfer_protection(before: BoundaryResult, after: BoundaryResult) -> None:
    """Adapter supplies both real solver outputs from the exact same current inputs."""
    if before.status != "READY" or after.status != "READY":
        raise ValueError("Both complete original protection projections must be READY")
    earlier, later = before.calculation_trace, after.calculation_trace
    phases = {"BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL"}
    for trace in (earlier, later):
        if len(trace) != 1098 or {(row.day, row.phase) for row in trace} != {
            (d, p) for d in range(366) for p in phases
        }:
            raise ValueError("Goal transfer requires all 366 days and three original phases")
    for left, right in zip(earlier, later, strict=True):
        if (left.day, left.date, left.phase) != (right.day, right.date, right.phase):
            raise ValueError("Goal transfer protection point identities changed")
        if (
            right.cash_cents < left.cash_cents
            or right.protected_cents_by_reason != left.protected_cents_by_reason
            or sum(right.protected_cents_by_reason.values())
            > sum(left.protected_cents_by_reason.values())
            or right.margin_cents < left.margin_cents
        ):
            raise ValueError("Goal transfer worsens an original hard protection point")


def validate_goal_transfer_balances(
    scope: GoalTransferScope,
    *,
    source_cash_cents: int,
    source_principal_cents: int,
    target_cash_cents: int,
    target_principal_cents: int,
) -> None:
    values = (source_cash_cents, source_principal_cents, target_cash_cents, target_principal_cents)
    if (
        any(type(value) is not int or value < 0 for value in values)
        or source_cash_cents < scope.amount_cents
        or source_cash_cents + source_principal_cents - scope.amount_cents
        < scope.source.minimum_guarantee_cents
        or target_cash_cents + target_principal_cents + scope.amount_cents
        > scope.target.target_cents
    ):
        raise ValueError(
            "Current source minimum, original cash and receiver target must remain valid"
        )


def project_goal_transfer_protection(
    data: FullProtectionProjectionInput,
    scope: GoalTransferScope,
) -> tuple[BoundaryResult, BoundaryResult]:
    """Use the unchanged FULL solver before/after only the four exact transfer deltas."""
    if (
        data.snapshot.source_issues
        or data.full_source_issues
        or not data.full_source_inventory_complete
    ):
        raise ValueError("Complete current financial/protection inputs are required")
    goals = {row.goal_id: row for row in data.snapshot.goals}
    cash = {row.account_id: row for row in data.snapshot.cash_accounts}
    if len(goals) != len(data.snapshot.goals) or len(cash) != len(data.snapshot.cash_accounts):
        raise ValueError("Current goal and cash identities must be unique")
    source, target = goals.get(scope.source.goal_id), goals.get(scope.target.goal_id)
    if (
        source is None
        or target is None
        or source.account_id != scope.source.account_id
        or target.account_id != scope.target.account_id
        or source.policy_id != scope.source.policy_id
        or target.policy_id != scope.target.policy_id
        or scope.source.account_id not in cash
        or scope.target.account_id not in cash
        or cash[scope.source.account_id].balance_cents < scope.amount_cents
    ):
        raise ValueError("Both current goal/account ownership identities must be intact")
    validate_goal_transfer_balances(
        scope,
        source_cash_cents=source.cash_owned_cents,
        source_principal_cents=source.principal_owned_cents,
        target_cash_cents=target.cash_owned_cents,
        target_principal_cents=target.principal_owned_cents,
    )
    original = data.model_dump()
    for row in original["snapshot"]["cash_accounts"]:
        if row["account_id"] == scope.source.account_id:
            row["balance_cents"] -= scope.amount_cents
        elif row["account_id"] == scope.target.account_id:
            row["balance_cents"] += scope.amount_cents
    for row in original["snapshot"]["goals"]:
        delta = (
            -scope.amount_cents
            if row["goal_id"] == scope.source.goal_id
            else (scope.amount_cents if row["goal_id"] == scope.target.goal_id else 0)
        )
        row["cash_owned_cents"] += delta
        row["allocated_cents"] += delta
    hypothetical = FullProtectionProjectionInput.model_validate(original)
    before, after = project_full_protection(data), project_full_protection(hypothetical)
    if before.source_account_checks or after.source_account_checks:
        raise ValueError("Registered future account-debit protection has not been proved")
    if before.full_annual_projection is None or after.full_annual_projection is None:
        raise ValueError("Full protection must preserve both original annual projections")
    validate_goal_transfer_protection(before.full_annual_projection, after.full_annual_projection)
    return before.full_annual_projection, after.full_annual_projection
