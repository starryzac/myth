"""Frozen joint USER/ASK plans over original goal permissions and income uses.

Full allocation confirmation limits a planning batch; it never creates a bank
grant. All amounts and sources are derived from verified server inputs.
"""

import json
from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import BankCommand, CashUse, ExecutionEffect
from app.domain.full_action_set_boundary_actual import _structure
from app.domain.full_action_set_joint_producers import (
    JointActionSetInput,
    _clock,
    _history,
    _original_funds_and_goals,
    _protection_basis,
    _sources,
    joint_action_ids,
    joint_goal_ids,
)
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalInput,
    derive_full_dynamic_goal_proof,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_joint_goal_planning import bind_full_joint_input
from app.domain.full_policy_configuration import GoalAllocationPolicy
from app.domain.full_protection_projection import project_full_protection
from app.domain.income_ledger import IncomeUse
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    MultiGoalAllocationInput,
    MultiGoalAllocationResult,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.full_policy_lifecycle import FullLifecycleResult, FullPolicyView
from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

ALGORITHM: Literal["registered-joint-goal-execution-v2"] = "registered-joint-goal-execution-v2"
ALGORITHM_V4: Literal["registered-joint-goal-execution-source-dag-v4"] = (
    "registered-joint-goal-execution-source-dag-v4"
)
MARKER = "full_joint_goal_execution"
NAMESPACE = UUID("514aaf21-d405-5f9c-8706-de1684e812e4")
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
Number = Annotated[StrictInt, Field(ge=1, le=8)]
MAX_PLAN_BYTES = 10 * 1024 * 1024


class FullJointGoalPrepareRequest(BoundaryModel):
    full_policy_id: UUIDReference
    expected_full_policy_version_id: UUIDReference
    expected_epoch_id: UUIDReference
    idempotency_key: Key


class FullJointGoalConfirmRequest(BoundaryModel):
    accepted: StrictBool
    reviewed_plan_hash: Hash
    expected_epoch_id: UUIDReference
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit(self) -> Self:
        if self.accepted is not True:
            raise ValueError("Joint consent must explicitly accept the entire original plan")
        return self


class FullJointGoalExecuteRequest(BoundaryModel):
    accepted: StrictBool
    reviewed_plan_hash: Hash
    expected_epoch_id: UUIDReference
    expected_child_number: Number
    expected_action_id: UUIDReference

    @model_validator(mode="after")
    def explicit(self) -> Self:
        if self.accepted is not True:
            raise ValueError("Execution must identify the original confirmed child")
        return self


class FullJointChildPrepareRequest(BoundaryModel):
    """Private persisted binding for the original pipeline, never client DTO."""

    plan_id: UUID
    plan_hash: Hash
    parent_request_hash: Hash
    epoch_id: UUID
    child_number: Number
    action_id: UUID


class FullJointGoalExecutionInput(BoundaryModel):
    """Private, complete captured originals; never an HTTP request."""

    protocol: Literal["joint-goal-execution-input-v2"] = "joint-goal-execution-input-v2"
    request: FullJointGoalPrepareRequest
    scope: FullPolicyView
    joint: JointActionSetInput


class FullJointGoalChild(BoundaryModel):
    child_number: Number
    goal_id: UUID
    action_id: UUID
    bank_idempotency_key: Key
    command: BankCommand
    original_model_evidence_id: UUID
    original_model_evidence_hash: Hash
    range_proof_hash: Hash


def plan_identity(user_id: UUID, body: FullJointGoalPrepareRequest) -> UUID:
    return uuid5(
        NAMESPACE,
        configuration_hash({"user": str(user_id), "request": body.model_dump(mode="json")}),
    )


def child_bank_key(plan_id: UUID, number: int) -> str:
    return f"joint-goal:{plan_id}:{number}"


def apply_joint_batch_cap(original: MultiGoalAllocationInput, cap: int) -> MultiGoalAllocationInput:
    """Preserve all goals/sources and only strengthen each original hard floor."""
    if type(cap) is not int or not 0 < cap <= 2**63 - 1:
        raise ValueError("JOINT_WHOLE_BATCH_CAP_INVALID")
    if len(original.hard_protection_points) != 1098:
        raise ValueError("JOINT_ALL_1098_POINTS_REQUIRED")
    return original.model_copy(
        update={
            "hard_protection_points": [
                point.model_copy(
                    update={
                        "other_protection_floor_cents": point.other_protection_floor_cents
                        + max(0, point.remaining_cents - cap)
                    }
                )
                for point in original.hard_protection_points
            ]
        }
    )


def _scope(data: FullJointGoalExecutionInput) -> GoalAllocationPolicy:
    body, view, joint = data.request, data.scope, data.joint
    actual, raw = joint.original_actual_input, joint.original_actual_input.base.original_inventory
    user, epoch, now = actual.base.user_id, actual.base.epoch_id, actual.base.as_of
    if (
        (body.full_policy_id, body.expected_full_policy_version_id, body.expected_epoch_id)
        != (view.policy_id, view.current_version.version_id, epoch)
        or view.epoch_id != epoch
        or view.template_name != "GoalAllocationPolicy"
    ):
        raise ValueError("JOINT_CURRENT_FULL_SCOPE_IDENTITY_DIFFERS")
    if (
        view.effective_status != "ACTIVE"
        or not view.planning_confirmation_valid
        or view.reference_validation != "CURRENT"
    ):
        raise ValueError("JOINT_FULL_SCOPE_NOT_CURRENT_ACTIVE_CONFIRMED")
    parent = next(row for row in raw["full_policies"] if row["id"] == str(view.policy_id))
    if (parent["user_id"], parent["epoch_id"], parent["template_name"], parent["status"]) != (
        str(user),
        str(epoch),
        "GoalAllocationPolicy",
        view.status,
    ) or _clock(parent["created_at"]) > now:
        raise ValueError("JOINT_FULL_SCOPE_ORIGINAL_PARENT_DIFFERS")
    versions = sorted(
        (row for row in raw["full_policy_versions"] if row["policy_id"] == parent["id"]),
        key=lambda row: row["version_number"],
    )
    commands = sorted(
        (row for row in raw["full_policy_commands"] if row["policy_id"] == parent["id"]),
        key=lambda row: row["command_number"],
    )
    if not versions or not commands or len(versions) > 10000 or len(commands) > 10000:
        raise ValueError("JOINT_SCOPE_COMPLETE_HISTORY_MISSING")
    if [row["version_number"] for row in versions] != list(range(1, len(versions) + 1)) or [
        row["command_number"] for row in commands
    ] != list(range(1, len(commands) + 1)):
        raise ValueError("JOINT_SCOPE_CONTINUOUS_HISTORY_REQUIRED")
    by_id = {row["id"]: row for row in versions}
    previous = None
    for version in versions:
        configuration = GoalAllocationPolicy.model_validate_json(
            json.dumps(version["configuration"])
        )
        if (
            version["configuration"] != configuration.model_dump(mode="json")
            or configuration_hash(version["configuration"]) != version["content_hash"]
            or version["previous_hash"] != previous
        ):
            raise ValueError("JOINT_SCOPE_VERSION_HASH_CHAIN_DIFFERS")
        confirmation = version["confirmation"]
        start = (
            datetime.combine(
                configuration.valid_from,
                time.min,
                ZoneInfo(str(actual.base.financial_basis["snapshot"]["timezone"])),
            ).astimezone(UTC)
            if configuration.valid_from is not None
            else _clock(version["confirmed_at"])
        )
        end = (
            datetime.combine(
                configuration.valid_until + timedelta(days=1),
                time.min,
                ZoneInfo(str(actual.base.financial_basis["snapshot"]["timezone"])),
            ).astimezone(UTC)
            if configuration.valid_until is not None
            else None
        )
        if (
            (
                version["user_id"],
                confirmation.get("user_id"),
                confirmation.get("epoch_id"),
                confirmation.get("policy_id"),
                confirmation.get("version_id"),
                confirmation.get("reviewed_hash"),
            )
            != (
                str(user),
                str(user),
                str(epoch),
                parent["id"],
                version["id"],
                version["content_hash"],
            )
            or confirmation.get("protocol") != "full-policy-confirmation-v1"
            or confirmation.get("template_name") != "GoalAllocationPolicy"
            or confirmation.get("accepted") is not True
            or confirmation.get("bank_authority") is not False
            or _clock(confirmation.get("confirmed_at")) != _clock(version["confirmed_at"])
            or _clock(version["confirmed_at"]) > now
            or _clock(version["created_at"]) != _clock(version["confirmed_at"])
            or _clock(version["valid_from"]) != start
            or (None if version.get("valid_until") is None else _clock(version["valid_until"]))
            != end
        ):
            raise ValueError("JOINT_SCOPE_EXACT_ORIGINAL_CONFIRMATION_REQUIRED")
        proof = next(
            row
            for row in raw["evidence_items"]
            if row["id"] == confirmation["confirmation_evidence_id"]
        )
        if (
            proof["user_id"] != str(user)
            or proof["content"] != confirmation
            or configuration_hash(confirmation) != proof["content_hash"]
            or proof["source_type"] != "FULL_POLICY_CONFIRMATION"
            or proof["source_ref"] != version["id"]
            or proof["evidence_level"] != "USER_CONFIRMED_POLICY"
            or proof["status"] != "VALID"
            or proof["id"] not in version["evidence_ids"]
            or _clock(proof["observed_at"]) != _clock(version["confirmed_at"])
            or _clock(proof["valid_from"]) != _clock(version["confirmed_at"])
            or proof.get("valid_to") is not None
            and now >= _clock(proof["valid_to"])
        ):
            raise ValueError("JOINT_SCOPE_CURRENT_CONFIRMATION_SOURCE_MISSING_OR_DIRTY")
        previous = version["content_hash"]
    previous_hash = previous_status = None
    confirmed: list[str] = []
    for row in commands:
        result = FullLifecycleResult.model_validate_json(json.dumps(row["result"]))
        version = by_id[row["version_id"]]
        if (
            row["user_id"] != str(user)
            or row["epoch_id"] != str(epoch)
            or configuration_hash(row["request"]) != row["request_hash"]
            or configuration_hash(row["result"]) != row["result_hash"]
            or row["previous_hash"] != previous_hash
            or row["previous_status"] != previous_status
            or _clock(row["created_at"]) > now
            or (
                result.command_id,
                result.policy_id,
                result.version_id,
                result.epoch_id,
                result.command_number,
                result.previous_command_hash,
                result.configuration_hash,
                result.status,
            )
            != (
                UUID(row["id"]),
                view.policy_id,
                UUID(version["id"]),
                epoch,
                row["command_number"],
                previous_hash,
                version["content_hash"],
                row["resulting_status"],
            )
        ):
            raise ValueError("JOINT_SCOPE_COMMAND_HASH_OWNER_CHAIN_DIFFERS")
        if row["kind"] in {"CREATE", "CHANGE", "RESUME"}:
            if (
                version["confirmation"].get("request_hash") != row["request_hash"]
                or version["confirmation"].get("request_key") != row["idempotency_key"]
                or _clock(version["confirmed_at"]) != _clock(row["created_at"])
            ):
                raise ValueError("JOINT_SCOPE_ORIGINAL_COMMAND_CONFIRMATION_DIFFERS")
            confirmed.append(version["id"])
        previous_hash, previous_status = row["result_hash"], row["resulting_status"]
    current = versions[-1]
    if (
        commands[0]["kind"] != "CREATE"
        or sorted(confirmed) != sorted(by_id)
        or commands[-1]["resulting_status"] != parent["status"]
        or commands[-1]["version_id"] != current["id"]
        or current["id"] != str(view.current_version.version_id)
        or current["configuration"] != view.current_version.configuration
        or current["content_hash"] != view.current_version.content_hash
        or current["confirmation"] != view.current_version.confirmation
    ):
        raise ValueError("JOINT_SCOPE_LATEST_ORIGINAL_HISTORY_DIFFERS")
    if (
        parent["status"] not in {"ACTIVE", "CONFIRMED"}
        or not _clock(current["valid_from"]) <= now
        or current.get("valid_until") is not None
        and now >= _clock(current["valid_until"])
    ):
        raise ValueError("JOINT_SCOPE_CURRENT_VALIDITY_OR_REVOCATION_DENIES")
    config = GoalAllocationPolicy.model_validate_json(json.dumps(current["configuration"]))
    ids = joint_goal_ids(actual)
    if not 2 <= len(ids) <= 8 or sorted(config.goal_ids, key=str) != ids:
        raise ValueError("JOINT_FINITE_SCOPE_MUST_INCLUDE_ALL_REGISTERED_GOALS")
    refs = current["impact_analysis"].get("reference_snapshots")
    expected = {
        str(identity): next(row for row in raw["goals"] if row["id"] == str(identity))
        for identity in ids
    }
    if not isinstance(refs, list) or len(refs) != len(expected):
        raise ValueError("JOINT_SCOPE_ALL_ORIGINAL_GOAL_REFERENCES_REQUIRED")
    for ref in refs:
        goal = expected[ref["id"]]
        if (
            ref["role"] != "goal"
            or ref["kind"] != "GOAL"
            or ref["binding_hash"]
            != configuration_hash(
                {
                    "id": goal["id"],
                    "owner": str(user),
                    "policy_version_id": goal["policy_version_id"],
                }
            )
            or ref["snapshot"].get("user_id") != str(user)
        ):
            raise ValueError("JOINT_SCOPE_CURRENT_GOAL_VERSION_REFERENCE_DIFFERS")
    if len({row["id"] for row in refs}) != len(expected):
        raise ValueError("JOINT_SCOPE_DUPLICATE_GOAL_REFERENCE")
    return config


def fixed_joint_effect(
    data: FullDynamicGoalInput,
    goal: AllocationGoal,
    allocation: MultiGoalAllocationResult,
    plan_id: UUID,
    number: int,
    expires_at: datetime,
) -> ExecutionEffect:
    """Use exactly the joint result, never the single-goal greedy selection."""
    amount = next(row.amount_cents for row in allocation.goals if row.goal_id == goal.goal_id)
    if type(amount) is not int or amount <= 0:
        raise ValueError("JOINT_POSITIVE_FIXED_CHILD_REQUIRED")
    uses = [
        IncomeUse(
            fragment_id=row.fragment_id,
            origin_transaction_id=row.origin_transaction_id,
            account_id=row.source_account_id,
            amount_cents=row.amount_cents,
        )
        for row in allocation.income_uses
        if row.goal_id == goal.goal_id
    ]
    cash: dict[UUID, int] = {}
    for row in uses:
        cash[row.account_id] = cash.get(row.account_id, 0) + row.amount_cents
    return ExecutionEffect(
        operation_id=uuid5(plan_id, f"child:{number}"),
        user_id=data.context.user_id,
        business_key=f"goal:{goal.goal_id}:{data.context.snapshot.as_of.astimezone(ZoneInfo(data.context.snapshot.timezone)).strftime('%Y-%m')}",
        action_type="ALLOCATE_GOAL",
        amount_cents=amount,
        cash_uses=[
            CashUse(account_id=key, amount_cents=cash[key]) for key in sorted(cash, key=str)
        ],
        income_uses=uses,
        destination_account_id=goal.account_id,
        goal_id=goal.goal_id,
        policy_id=goal.policy_id,
        policy_version_id=goal.effective_policy_version_id,
        policy_version_ids=[goal.effective_policy_version_id],
        valid_from=data.context.snapshot.as_of,
        expires_at=expires_at,
    )


class FullJointFrozenPlan(BoundaryModel):
    protocol: Literal[
        "registered-joint-goal-execution-v2",
        "registered-joint-goal-execution-archive-v3",
        "registered-joint-goal-execution-source-dag-v4",
    ] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    funds_reserved: Literal[False] = False
    execution_mode: Literal["USER_ASK_FIXED_ORDER"] = "USER_ASK_FIXED_ORDER"
    cross_operation_atomicity: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    plan_id: UUID
    user_id: UUID
    epoch_id: UUID
    prepared_at: datetime
    expires_at: datetime
    inputs: FullJointGoalExecutionInput
    allocation_input: MultiGoalAllocationInput
    allocation: MultiGoalAllocationResult
    full_binding_hash: Hash
    total_allocation_cents: Annotated[StrictInt, Field(gt=0)]
    children: Annotated[list[FullJointGoalChild], Field(min_length=1, max_length=8)]
    plan_hash: Hash

    @model_validator(mode="after")
    def entire_fixed_plan(self) -> Self:
        request = self.inputs.request
        amounts = {row.goal_id: row.amount_cents for row in self.allocation.goals}
        positives = {
            key: value for key, value in amounts.items() if type(value) is int and value > 0
        }
        if (
            self.plan_id != plan_identity(self.user_id, request)
            or self.user_id != self.inputs.joint.original_actual_input.base.user_id
            or self.epoch_id != request.expected_epoch_id
            or self.prepared_at != self.inputs.joint.original_actual_input.base.as_of
            or self.prepared_at.utcoffset() is None
            or self.expires_at.utcoffset() is None
            or not self.prepared_at < self.expires_at <= self.prepared_at + timedelta(minutes=15)
            or self.allocation.status != "OPTIMAL"
            or any(value is None for value in amounts.values())
            or len(amounts) != len(self.allocation.goals)
            or self.allocation.input_hash
            != configuration_hash(self.allocation_input.model_dump(mode="json"))
            or [row.child_number for row in self.children] != list(range(1, len(self.children) + 1))
            or {row.goal_id for row in self.children} != set(positives)
            or len({row.goal_id for row in self.children}) != len(self.children)
            or self.total_allocation_cents
            != sum(row.command.effect.amount_cents for row in self.children)
            or self.total_allocation_cents
            > self.inputs.scope.current_version.configuration["max_single_allocation_cents"]
            or self.plan_hash
            != configuration_hash(self.model_dump(mode="json", exclude={"plan_hash"}))
        ):
            raise ValueError("JOINT_WHOLE_FROZEN_IDENTITY_HASH_ORDER_OR_CAP_DIFFERS")
        for child in self.children:
            effect = child.command.effect
            goal = next(row for row in self.allocation_input.goals if row.goal_id == child.goal_id)
            source = next(
                row.data
                for row in self.inputs.joint.original_actual_input.dynamic_goals
                if row.data is not None and row.data.request.goal_id == child.goal_id
            )
            expected = fixed_joint_effect(
                source, goal, self.allocation, self.plan_id, child.child_number, self.expires_at
            )
            if (
                effect != expected
                or child.action_id != uuid5(self.plan_id, f"child:{child.child_number}")
                or child.bank_idempotency_key != child_bank_key(self.plan_id, child.child_number)
                or child.original_model_evidence_id != source.model_evidence_id
                or child.original_model_evidence_hash != source.model_evidence_hash
            ):
                raise ValueError("JOINT_WHOLE_FROZEN_CHILD_EFFECT_OR_SOURCE_DIFFERS")
        return self


class FullJointGoalPreview(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    preview_only: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    request: FullJointGoalPrepareRequest
    status: Literal["READY_TO_REVIEW", "BLOCKED", "UNKNOWN"]
    registered_goal_ids: list[UUID]
    unresolved_original_action_ids: list[UUID]
    plan: FullJointFrozenPlan | None
    input_hash: Hash
    reasons: list[str]
    limitations: list[str]


def derive_joint_execution_plan(data: FullJointGoalExecutionInput) -> FullJointGoalPreview:
    """Original RAW v2 derivation and capacity decisions remain unchanged."""
    return _derive_joint_execution_plan(data, archive=False)


def derive_archived_joint_execution_plan(data: FullJointGoalExecutionInput) -> FullJointGoalPreview:
    """Replay all original financial predicates with a separately bounded v3 wire."""
    return _derive_joint_execution_plan(data, archive=True)


def derive_source_archived_joint_execution_plan(
    data: FullJointGoalExecutionInput,
) -> FullJointGoalPreview:
    """Explicit V4 transport; every original financial predicate remains native."""
    return _derive_joint_execution_plan(data, archive=True, source_dag=True)


def _derive_joint_execution_plan(
    data: FullJointGoalExecutionInput, *, archive: bool, source_dag: bool = False
) -> FullJointGoalPreview:
    from app.domain.full_joint_goal_archive_protocol import (
        ALGORITHM_V3,
        binding_for_plan,
        encode_joint_record,
        validate_joint_original_capacity,
    )
    from app.domain.full_joint_goal_source_adapter import (
        source_binding_for_input,
        source_binding_for_plan,
    )
    from app.domain.full_joint_goal_source_archive import encode_shared_joint

    if source_dag and not archive:
        raise ValueError("JOINT_SOURCE_DAG_REQUIRES_EXPLICIT_ARCHIVE")
    data = FullJointGoalExecutionInput.model_validate(data.model_dump())
    joint, actual = data.joint, data.joint.original_actual_input
    goals = joint_goal_ids(actual)
    reasons = [*joint.source_reasons, *_structure(actual)]
    unresolved: list[UUID] = []
    plan = None
    status: Literal["READY_TO_REVIEW", "BLOCKED", "UNKNOWN"] = "UNKNOWN"
    try:
        if reasons or not archive and len(data.model_dump_json().encode("utf8")) > MAX_PLAN_BYTES:
            raise ValueError("JOINT_CAPTURE_INCOMPLETE_OR_CAPACITY_EXCEEDED")
        if source_dag:
            raw_input = data.model_dump(mode="json")
            encode_shared_joint(raw_input, source_binding_for_input(raw_input), "INPUT")
        elif archive:
            validate_joint_original_capacity(data.model_dump(mode="json"))
        if (
            joint.expected_goal_ids != goals
            or joint.original_action_ids != joint_action_ids(actual)
            or [row.action_id for row in joint.original_commands] != joint.original_action_ids
        ):
            raise ValueError("JOINT_COMPLETE_GOAL_ACTION_DENOMINATOR_DIFFERS")
        unresolved = _history(joint)
        if unresolved:
            raise ValueError("JOINT_ORIGINAL_UNRESOLVED_RESPONSIBILITY_RETAINED")
        config = _scope(data)
        _sources(joint)
        _original_funds_and_goals(joint)
        _protection_basis(joint)
        original, protection, planning = (
            joint.original_joint_input,
            joint.protection_inputs,
            joint.actual_planning,
        )
        if original is None or protection is None or planning is None:
            raise ValueError("JOINT_COMPLETE_ACTUAL_MATH_INPUT_MISSING")
        projection = project_full_protection(protection)
        binding = bind_full_joint_input(
            original,
            projection,
            joint.verified_source_refs,
            seasonal_proof_sources=protection.policies,
        )
        if (
            binding.status != "VERIFIED"
            or binding.reasons
            or planning.binding != binding
            or planning.full_protection.projection != projection
            or planning.original_joint.allocation != solve_multi_goal_allocation(original)
            or planning.allocation != solve_multi_goal_allocation(binding.candidate)
            or planning.full_protection.audit.status != "VALID"
            or not planning.full_protection.audit.complete
            or planning.original_joint.uncovered_goal_ids
            or not planning.original_joint.independent_bank_projection_matched
        ):
            raise ValueError("JOINT_ORIGINAL_FULL_1098_PROTECTION_OR_SOLVER_NOT_VERIFIED")
        candidate = apply_joint_batch_cap(binding.candidate, config.max_single_allocation_cents)
        result = solve_multi_goal_allocation(candidate)
        status = "BLOCKED"
        if result.status != "OPTIMAL":
            raise ValueError("JOINT_EXACT_WHOLE_SOLVER_" + result.status)
        total = sum(row.amount_cents or 0 for row in result.goals)
        if total <= 0 or total > config.max_single_allocation_cents:
            raise ValueError("JOINT_ZERO_OR_OVER_WHOLE_CAP_NOT_EXECUTABLE")
        now = actual.base.as_of
        ends = [now + timedelta(minutes=15)]
        ends.extend(goal.valid_until for goal in original.goals if goal.valid_until is not None)
        if data.scope.current_version.valid_until is not None:
            ends.append(data.scope.current_version.valid_until)
        expires = min(ends)
        parent = plan_identity(actual.base.user_id, data.request)
        dynamic = {
            row.data.request.goal_id: row for row in actual.dynamic_goals if row.data is not None
        }
        children: list[FullJointGoalChild] = []
        for goal in sorted(original.goals, key=lambda row: str(row.goal_id)):
            amount = next(row.amount_cents for row in result.goals if row.goal_id == goal.goal_id)
            if amount == 0:
                continue
            producer = dynamic[goal.goal_id]
            if (
                producer.data is None
                or producer.authority is None
                or producer.authority.status != "AUTHORIZED"
                or producer.authority.reasons
            ):
                raise ValueError("JOINT_ORIGINAL_GOAL_PERMISSION_NOT_VERIFIED")
            effect = fixed_joint_effect(
                producer.data, goal, result, parent, len(children) + 1, expires
            )
            proof = derive_full_dynamic_goal_proof(producer.data, effect)
            validation = revalidate_execution(
                effect, producer.data.context, full_dynamic_goal_proof=proof
            )
            veto = validate_full_execution_protection(
                effect,
                producer.data.context,
                validation,
                producer.data.protection_policies,
                source_issues=tuple(producer.data.protection_issues),
            )
            if (
                proof.status != "VERIFIED_RANGE"
                or validation.status not in {"READY", "CONFIRMATION_REQUIRED"}
                or veto.status not in {"PASSED", "NO_ADDITIONAL_POLICY"}
            ):
                raise ValueError(
                    "JOINT_FIXED_CHILD_CURRENT_GATE_DENIED:"
                    + ",".join([*proof.reasons, *validation.reasons, *veto.reasons])
                )
            children.append(
                FullJointGoalChild(
                    child_number=len(children) + 1,
                    goal_id=goal.goal_id,
                    action_id=effect.operation_id,
                    bank_idempotency_key=child_bank_key(parent, len(children) + 1),
                    command=BankCommand(effect=effect, effect_hash=execution_effect_hash(effect)),
                    original_model_evidence_id=producer.data.model_evidence_id,
                    original_model_evidence_hash=producer.data.model_evidence_hash,
                    range_proof_hash=proof.proof_hash,
                )
            )
        # Construction here only supplies already typed derived values so that
        # the hash includes defaults; the complete strict validator follows.
        temporary = FullJointFrozenPlan.model_construct(
            protocol=ALGORITHM_V4 if source_dag else ALGORITHM_V3 if archive else ALGORITHM,
            plan_id=parent,
            user_id=actual.base.user_id,
            epoch_id=actual.base.epoch_id,
            prepared_at=now,
            expires_at=expires,
            inputs=data,
            allocation_input=candidate,
            allocation=result,
            full_binding_hash=binding.binding_hash,
            total_allocation_cents=total,
            children=children,
            plan_hash="0" * 64,
        )
        encoded = temporary.model_dump(mode="json", exclude={"plan_hash"})
        plan = FullJointFrozenPlan.model_validate_json(
            json.dumps(encoded | {"plan_hash": configuration_hash(encoded)})
        )
        if source_dag:
            raw_plan = plan.model_dump(mode="json")
            bound = source_binding_for_plan(raw_plan)
            encode_shared_joint(raw_plan, bound, "PLAN")
            encode_shared_joint(raw_plan["inputs"], bound, "INPUT")
        elif archive:
            raw_plan = plan.model_dump(mode="json")
            bound = binding_for_plan(raw_plan)
            encode_joint_record(raw_plan, bound, "PLAN")
            encode_joint_record(raw_plan["inputs"], bound, "INPUT")
        elif len(plan.model_dump_json().encode("utf8")) > MAX_PLAN_BYTES:
            raise ValueError("JOINT_FROZEN_WHOLE_PLAN_CAPACITY_EXCEEDED")
        status = "READY_TO_REVIEW"
    except (ValueError, TypeError, KeyError, StopIteration, OverflowError) as error:
        reasons.append(str(error))
        plan = None
    return FullJointGoalPreview(
        user_id=actual.base.user_id,
        epoch_id=actual.base.epoch_id,
        as_of=actual.base.as_of,
        request=data.request,
        status=status,
        registered_goal_ids=goals,
        unresolved_original_action_ids=unresolved,
        plan=plan,
        input_hash=configuration_hash(data.model_dump(mode="json")),
        reasons=sorted(set(reasons)),
        limitations=[
            "Requires durable identities and original typed hooks; preview performs no writes.",
            "Complete monthly scope of 2-8 goals; weekly and multi-period execution unavailable.",
            "Bank accepts only revalidated fixed amounts/sources; UNKNOWN stops the next child.",
        ],
    )


def verify_frozen_joint_plan(plan: FullJointFrozenPlan) -> None:
    from app.domain.full_joint_goal_archive_protocol import ALGORITHM_V3
    from app.domain.immutable_joint_validation_scope import verify_original_content

    if plan.protocol not in {ALGORITHM_V3, ALGORITHM_V4}:
        _verify_frozen_joint_plan_uncached(plan)
        return
    verify_original_content(
        plan.protocol,
        plan.model_dump(mode="json", exclude={"plan_hash"}),
        plan.plan_hash,
        lambda: _verify_frozen_joint_plan_uncached(plan),
    )


def _verify_frozen_joint_plan_uncached(plan: FullJointFrozenPlan) -> None:
    from app.domain.full_joint_goal_archive_protocol import ALGORITHM_V3

    replayed = (
        derive_source_archived_joint_execution_plan(plan.inputs)
        if plan.protocol == ALGORITHM_V4
        else derive_archived_joint_execution_plan(plan.inputs)
        if plan.protocol == ALGORITHM_V3
        else derive_joint_execution_plan(plan.inputs)
    )
    if replayed.status != "READY_TO_REVIEW" or replayed.plan != plan:
        raise ValueError("JOINT_FROZEN_PLAN_COMPLETE_ORIGINAL_MATH_DIFFERS")


class FullJointWholeConsentContent(BoundaryModel):
    """Closed original server-authenticated consent, without human-study claims."""

    protocol: Literal["joint-goal-whole-consent-v2"] = "joint-goal-whole-consent-v2"
    simulation: Literal[True] = True
    user_id: UUID
    plan_id: UUID
    epoch_id: UUID
    plan_hash: Hash
    accepted: Literal[True] = True
    original_request: FullJointGoalConfirmRequest
    request_hash: Hash
    confirmed_at: datetime
    valid_until: datetime
    actor: LocalActorPrincipal
    actor_session_id: UUID
    actor_role: Literal["USER"] = "USER"
    authentication_source: Literal["LOCAL_SIGNED_SESSION"] = "LOCAL_SIGNED_SESSION"
    human_identity_verified: Literal[False] = False

    @model_validator(mode="after")
    def actual_actor_and_original_body(self) -> Self:
        require_local_user(self.actor, self.user_id, self.confirmed_at)
        if (
            self.actor_session_id != self.actor.session_id
            or self.original_request.expected_epoch_id != self.epoch_id
            or self.original_request.reviewed_plan_hash != self.plan_hash
            or self.request_hash
            != configuration_hash(self.original_request.model_dump(mode="json"))
            or self.valid_until.tzinfo is None
            or self.valid_until.utcoffset() is None
            or not self.confirmed_at < self.valid_until
        ):
            raise ValueError("JOINT_WHOLE_ACTOR_OR_ORIGINAL_BODY_DIFFERS")
        return self


def whole_consent_content(
    plan: FullJointFrozenPlan,
    body: FullJointGoalConfirmRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullJointWholeConsentContent:
    if not plan.prepared_at <= now < plan.expires_at:
        raise ValueError("JOINT_WHOLE_CONFIRMATION_OUTSIDE_PLAN_WINDOW")
    return FullJointWholeConsentContent(
        user_id=plan.user_id,
        plan_id=plan.plan_id,
        epoch_id=plan.epoch_id,
        plan_hash=plan.plan_hash,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        confirmed_at=now,
        valid_until=plan.expires_at,
        actor=principal,
        actor_session_id=principal.session_id,
    )


def require_fixed_child(
    plan: FullJointFrozenPlan, body: FullJointGoalExecuteRequest, states: list[str]
) -> FullJointGoalChild:
    """States are supplied only after complete actual bank/receipt verification."""
    if (
        body.expected_epoch_id != plan.epoch_id
        or body.reviewed_plan_hash != plan.plan_hash
        or len(states) != len(plan.children)
    ):
        raise ValueError("JOINT_ORIGINAL_EXECUTE_IDENTITY_OR_DENOMINATOR_DIFFERS")
    child = next(
        (row for row in plan.children if row.child_number == body.expected_child_number), None
    )
    if child is None or child.action_id != body.expected_action_id:
        raise ValueError("JOINT_FIXED_EXPECTED_CHILD_DIFFERS")
    terminal = {"ORIGINAL_RECEIPT_VERIFIED"}
    if any(value not in terminal for value in states[: child.child_number - 1]):
        raise ValueError("JOINT_PRIOR_CHILD_NOT_VERIFIED_NO_ADVANCE")
    if states[child.child_number - 1] not in {
        "PLANNED_UNRESERVED",
        "AUTHORIZED",
        "SUBMITTED",
        "UNKNOWN",
        "ORIGINAL_RECEIPT_VERIFIED",
    }:
        raise ValueError("JOINT_CURRENT_CHILD_UNSUPPORTED_OR_MISSING_ORIGINAL")
    return child
