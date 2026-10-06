"""Exact current joint suggestions mapped only to existing verified economic effects."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.audit_chain import build_subject, verify_frozen_projection
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.full_action_set_boundary import CandidateView, Hash
from app.domain.full_action_set_boundary_actual import (
    ActualActionSetInput,
    _structure,
    derive_actual_action_set,
)
from app.domain.full_action_set_boundary_full import _raw_sources, dynamic_candidate
from app.domain.full_action_set_release_producers import _product_originals
from app.domain.full_dynamic_goal_execution import (
    MARKER,
    _originals,
    native_income_original_matches,
)
from app.domain.full_joint_goal_planning import bind_full_joint_input, captured_references
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.income_ledger import IncomeLedger
from app.domain.multi_goal_allocation import (
    MultiGoalAllocationInput,
    SourceReference,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services.full_goals import FullGoalModelContent, _request_hash
from app.services.full_joint_goal_planning import FullJointPlanningResponse
from pydantic import Field

ALGORITHM: Literal["full-policy-joint-action-producers-v1"] = (
    "full-policy-joint-action-producers-v1"
)
MAX_GOALS = 8
MAX_BYTES = 16 * 1024 * 1024


class JointOriginalCommand(BoundaryModel):
    action_id: UUID
    prepare_trace: DecisionTrace | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class JointActionSetInput(BoundaryModel):
    protocol: Literal["joint-action-set-input-v1"] = "joint-action-set-input-v1"
    original_actual_input: ActualActionSetInput
    expected_goal_ids: list[UUID]
    original_action_ids: list[UUID]
    original_commands: list[JointOriginalCommand]
    original_joint_input: MultiGoalAllocationInput | None = None
    actual_planning: FullJointPlanningResponse | None = None
    protection_inputs: FullProtectionProjectionInput | None = None
    verified_source_refs: list[SourceReference] = Field(default_factory=list)
    source_reasons: list[str] = Field(default_factory=list)


class JointProducerResult(BoundaryModel):
    candidate_key: str
    goal_id: UUID
    view: CandidateView
    shadow_original_candidate_key: str | None = None


class JointActionSetResult(BoundaryModel):
    algorithm_version: Literal["full-policy-joint-action-producers-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    full_global_adapter_installed: Literal[False] = False
    current_joint_execution: Literal["EXACT_EXISTING_EFFECT_ONLY"] = "EXACT_EXISTING_EFFECT_ONLY"
    different_allocation_execution: Literal["NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION"] = (
        "NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION"
    )
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE_REGISTERED_JOINT_FAMILY", "UNKNOWN"]
    joint_family_complete: bool
    original_actual_input_hash: Hash
    expected_goal_ids: list[UUID]
    original_action_ids: list[UUID]
    unresolved_original_action_ids: list[UUID]
    expected_candidate_keys: list[str]
    results: list[JointProducerResult]
    handled_unsupported_codes: list[str]
    original_actual_reasons: list[str]
    remaining_unsupported_producers: list[str]
    reasons: list[str]
    input_hash: Hash
    result_hash: Hash


def joint_goal_ids(actual: ActualActionSetInput) -> list[UUID]:
    return sorted((UUID(row["id"]) for row in actual.base.original_inventory["goals"]), key=str)


def joint_action_ids(actual: ActualActionSetInput) -> list[UUID]:
    return sorted(
        (
            UUID(row["id"])
            for row in actual.base.original_inventory["action_plans"]
            if row["action_type"] == "ALLOCATE_GOAL" or MARKER in row["request"]
        ),
        key=str,
    )


def joint_key(goal_id: UUID) -> str:
    return "full-joint:goal:" + str(goal_id)


def _clock(value: Any) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
        raise ValueError("JOINT_ORIGINAL_CLOCK_NOT_AWARE")
    return parsed


def _sources(data: JointActionSetInput) -> None:
    actual, refs = data.original_actual_input, data.verified_source_refs
    original = data.original_joint_input
    if original is None:
        raise ValueError("JOINT_ORIGINAL_TYPED_INPUT_MISSING")
    raw = {UUID(row["id"]): row for row in actual.base.original_inventory["evidence_items"]}
    ids = {row.evidence_id: row for row in refs}
    if not refs or len(refs) != len(ids):
        raise ValueError("JOINT_COMPLETE_SOURCE_DENOMINATOR_MISSING_OR_DUPLICATE")
    for ref in refs:
        row = raw[ref.evidence_id]
        if (
            ref.user_id != actual.base.user_id
            or row["user_id"] != str(ref.user_id)
            or row["status"] != "VALID"
            or ref.content_hash != row["content_hash"]
            or row["content_hash"] != configuration_hash(row["content"])
            or _clock(row["observed_at"]) > actual.base.as_of
            or _clock(row["valid_from"]) > actual.base.as_of
            or row.get("valid_to") is not None
            and actual.base.as_of >= _clock(row["valid_to"])
        ):
            raise ValueError("JOINT_CURRENT_SOURCE_OWNER_HASH_OR_CLOCK_DIFFERS")
    if any(ids.get(ref.evidence_id) != ref for ref in captured_references(original)):
        raise ValueError("JOINT_ORIGINAL_SOURCE_NOT_FRESHLY_BOUND")
    metadata = actual.base.financial_basis["evidence"]
    if len(metadata) != len({row["id"] for row in metadata}) or any(
        row["hash"] != raw[UUID(row["id"])]["content_hash"] or UUID(row["id"]) not in ids
        for row in metadata
    ):
        raise ValueError("JOINT_ORIGINAL_FINANCIAL_SOURCE_METADATA_NOT_BOUND")


def _original_funds_and_goals(data: JointActionSetInput) -> None:
    actual, original = data.original_actual_input, data.original_joint_input
    assert original is not None
    if (original.user_id, original.as_of) != (actual.base.user_id, actual.base.as_of):
        raise ValueError("JOINT_ACTUAL_OWNER_OR_CLOCK_DIFFERS")
    if sorted((goal.goal_id for goal in original.goals), key=str) != data.expected_goal_ids:
        raise ValueError("JOINT_ALL_OWNED_GOAL_DENOMINATOR_DIFFERS")
    raw = actual.base.original_inventory
    producers = {item.candidate_key: item for item in actual.dynamic_goals}
    ledger: IncomeLedger | None = None
    for goal in original.goals:
        item = producers["goal:" + str(goal.goal_id)]
        if item.data is None or item.authority is None:
            raise ValueError("JOINT_CURRENT_GOAL_MODEL_OR_PERMISSION_INPUT_MISSING")
        _raw_sources(actual.base, item, actual_v2_native_values=True)
        expected, _ = _originals(item.data)
        if goal.model_dump(exclude={"source_refs", "negotiable_fields"}) != expected.model_dump(
            exclude={"source_refs", "negotiable_fields"}
        ):
            raise ValueError("JOINT_ORIGINAL_MODEL_OWNERSHIP_OR_MONTH_DIFFERS")
        stored = next(row for row in raw["goals"] if row["id"] == str(goal.goal_id))
        for key, value in (
            ("policy_id", str(goal.policy_id)),
            ("policy_version_id", str(goal.effective_policy_version_id)),
            ("account_id", str(goal.account_id)),
            ("target_cents", goal.target_cents),
            ("monthly_min_cents", goal.monthly_min_cents),
            ("monthly_target_cents", goal.monthly_target_cents),
            ("monthly_max_cents", goal.monthly_max_cents),
            ("minimum_protection_cents", goal.minimum_guarantee_cents),
            ("importance", goal.importance),
            ("deadline", goal.deadline.isoformat()),
        ):
            if stored[key] != value:
                raise ValueError("JOINT_COMPLETE_RAW_GOAL_COPY_DIFFERS")
        model = FullGoalModelContent.model_validate_json(json.dumps(item.data.model_original))
        source = next(
            row for row in raw["evidence_items"] if row["id"] == str(item.data.model_evidence_id)
        )
        current = max(
            (
                row
                for row in raw["evidence_items"]
                if row["source_type"] == "FULL_GOAL_MODEL_V1" and row["source_ref"] == stored["id"]
            ),
            key=lambda row: (_clock(row["created_at"]), row["id"]),
        )
        if (
            source != current
            or model.request_hash
            != _request_hash(
                model.user_id,
                model.epoch_id,
                model.goal_id,
                model.expected_version_id,
                model.full_configuration,
                model.reason,
                model.idempotency_key,
            )
            or _clock(source["observed_at"]) != model.confirmed_at
            or _clock(source["valid_from"]) != model.confirmed_at
            or source["source_type"] != "FULL_GOAL_MODEL_V1"
            or source["evidence_level"] != "USER_CONFIRMED_POLICY"
        ):
            raise ValueError("JOINT_CURRENT_ORIGINAL_FULL_MODEL_NOT_BOUND")
        if ledger is None:
            ledger = item.data.income
        elif ledger != item.data.income:
            raise ValueError("JOINT_GOALS_DO_NOT_SHARE_SAME_ACTUAL_INCOME_LEDGER")
    if ledger is None or not native_income_original_matches(
        actual.base.financial_basis["income"], ledger
    ):
        raise ValueError("JOINT_COMPLETE_ORIGINAL_INCOME_NOT_BOUND")
    lots = {lot.fragment_id: lot for lot in original.income_lots}
    expected_fragments = {row.fragment_id for row in ledger.fragments if row.available_cents > 0}
    if set(lots) != expected_fragments:
        raise ValueError("JOINT_ALL_AVAILABLE_INCOME_FRAGMENT_DENOMINATOR_DIFFERS")
    origins = {row.origin_transaction_id: row for row in ledger.origins}
    for fragment in ledger.fragments:
        if fragment.available_cents <= 0:
            continue
        lot, origin = lots[fragment.fragment_id], origins[fragment.origin_transaction_id]
        if (
            lot.origin_transaction_id != origin.origin_transaction_id
            or lot.account_id != fragment.account_id
            or lot.available_cents != fragment.available_cents
            or lot.received_cents != origin.amount_cents
            or lot.occurred_at != origin.occurred_at
            or lot.observed_at != origin.observed_at
            or lot.bank_evidence_id != origin.bank_evidence_id
            or lot.bank_evidence_hash != origin.bank_evidence_hash
        ):
            raise ValueError("JOINT_ACTUAL_FRAGMENT_OR_BANK_ORIGINAL_DIFFERS")


def _protection_basis(data: JointActionSetInput) -> None:
    actual, inputs = data.original_actual_input, data.protection_inputs
    if inputs is None:
        raise ValueError("JOINT_COMPLETE_PROTECTION_INPUT_MISSING")
    observed, expected = (
        inputs.snapshot.model_dump(mode="json"),
        dict(actual.base.financial_basis["snapshot"]),
    )
    for value in (observed, expected):
        value.pop("source_digest", None)
    if (
        observed != expected
        or inputs.snapshot.as_of != actual.base.as_of
        or [row.model_dump(mode="json") for row in inputs.boundary_versions]
        != actual.base.financial_basis["versions"]
        or [row.model_dump(mode="json") for row in inputs.positions]
        != actual.base.financial_basis["positions"]
    ):
        raise ValueError("JOINT_SAME_INVOCATION_COMPLETE_FINANCIAL_BASIS_DIFFERS")
    _product_originals(actual, inputs.boundary_products)
    raw = actual.base.original_inventory
    parents = [
        row
        for row in raw["full_policies"]
        if row["epoch_id"] == str(actual.base.epoch_id)
        and row["template_name"]
        in {"DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"}
    ]
    if {row.policy_id for row in inputs.policies} != {UUID(row["id"]) for row in parents} or len(
        inputs.policies
    ) != len(parents):
        raise ValueError("JOINT_COMPLETE_FULL_PROTECTION_DENOMINATOR_DIFFERS")
    for source in inputs.policies:
        parent = next(row for row in parents if row["id"] == str(source.policy_id))
        version = max(
            (
                row
                for row in raw["full_policy_versions"]
                if row["policy_id"] == str(source.policy_id)
            ),
            key=lambda row: row["version_number"],
        )
        if (
            str(source.version_id) != version["id"]
            or source.configuration != version["configuration"]
            or source.content_hash != version["content_hash"]
            or source.confirmation != version["confirmation"]
            or source.evidence_ids != [UUID(value) for value in version["evidence_ids"]]
            or source.confirmed_at != _clock(version["confirmed_at"])
            or source.valid_from != _clock(version["valid_from"])
            or source.valid_until
            != (None if version["valid_until"] is None else _clock(version["valid_until"]))
            or source.version_number != version["version_number"]
            or source.template_name != parent["template_name"]
            or source.reference_snapshots
            != version["impact_analysis"].get("reference_snapshots", [])
        ):
            raise ValueError("JOINT_CURRENT_FULL_PROTECTION_ORIGINAL_DIFFERS")
        # Current specialized history/seasonal/payee wrappers require an
        # independently replayed reference adapter. Their label is not proof.
        if source.reference_snapshots or source.protected_references:
            raise ValueError("JOINT_CURRENT_FULL_REFERENCE_REPLAY_NOT_IMPLEMENTED")
        status = parent["status"]
        if status not in {"ACTIVE", "CONFIRMED", "SUSPENDED", "REVOKED", "EXPIRED"}:
            raise ValueError("JOINT_CURRENT_FULL_STATE_NOT_KNOWN")
        effective = (
            "REVOKED"
            if status == "REVOKED"
            else "CONFIRMATION_IN_FUTURE"
            if source.confirmed_at > actual.base.as_of
            else "EXPIRED"
            if status == "EXPIRED"
            or source.valid_until is not None
            and actual.base.as_of >= source.valid_until
            else "SUSPENDED"
            if status == "SUSPENDED"
            else "CONFIRMED"
            if actual.base.as_of < source.valid_from
            else "ACTIVE"
        )
        if (
            source.effective_status != effective
            or not source.references_current
            or source.planning_confirmation_valid != (effective in {"ACTIVE", "CONFIRMED"})
            or not source.evidence_ids
            or any(
                identity not in {ref.evidence_id for ref in data.verified_source_refs}
                for identity in source.evidence_ids
            )
        ):
            raise ValueError("JOINT_CURRENT_FULL_PERMISSION_OR_REFERENCE_LABEL_NOT_PROVEN")
    # Claimed reservations never disappear from this full curve.
    contexts = [item.data.context for item in actual.dynamic_goals if item.data is not None]
    if not contexts or any(
        inputs.reserved_cash_by_account != context.reserved_cash_by_account for context in contexts
    ):
        raise ValueError("JOINT_COMPLETE_ORIGINAL_CASH_CLAIMS_DIFFERS")


def _history(data: JointActionSetInput) -> list[UUID]:
    actual = data.original_actual_input
    raw = actual.base.original_inventory
    unresolved = []
    subjects = [
        build_subject(
            user_id=actual.base.user_id,
            epoch_id=actual.base.epoch_id,
            kind=kind,
            id=UUID(row["id"]),
            data=row,
        )
        for kind, table in (("TRANSACTION", "transactions"), ("EVIDENCE", "evidence_items"))
        for row in raw[table]
    ]
    for saved in data.original_commands:
        try:
            action = next(row for row in raw["action_plans"] if row["id"] == str(saved.action_id))
            if saved.prepare_trace is None or saved.missing_reasons:
                raise ValueError("JOINT_PREPARE_ORIGINAL_MISSING")
            trace = saved.prepare_trace
            verify_trace(trace)
            command = BankCommand.model_validate_json(json.dumps(action["request"]["execution"]))
            effect = command.effect
            if (
                (trace.user_id, trace.action_id, trace.phase)
                != (actual.base.user_id, saved.action_id, "PREPARE")
                or str(trace.run_id) != action["decision_run_id"]
                or trace.as_of > actual.base.as_of
                or trace.inputs["effect"] != effect.model_dump(mode="json")
                or command.effect_hash != execution_effect_hash(effect)
                or effect.operation_id != saved.action_id
                or effect.user_id != actual.base.user_id
                or effect.action_type != "ALLOCATE_GOAL"
                or action["request_hash"] != configuration_hash(action["request"])
                or MARKER in action["request"]
                and trace.inputs.get("action_request") != action["request"]
            ):
                raise ValueError("JOINT_HISTORICAL_ACTION_COMMAND_OR_TRACE_DIFFERS")
            banks = [row for row in raw["bank_operations"] if row["action_plan_id"] == action["id"]]
            receipts = [
                row for row in raw["action_receipts"] if row["action_plan_id"] == action["id"]
            ]
            if action["status"] in {"SUBMITTED", "UNKNOWN"} or any(
                row["action_plan_id"] == action["id"] and row["status"] == "RESERVED"
                for row in raw["action_resource_reservations"]
            ):
                raise ValueError("JOINT_ORIGINAL_INFLIGHT_RESPONSIBILITY_RETAINED")
            if not banks:
                if receipts or action["status"] not in {
                    "PLANNED",
                    "AUTHORIZED",
                    "INVALIDATED",
                    "CANCELLED",
                }:
                    raise ValueError("JOINT_NO_BANK_STATE_NOT_PROVEN")
                continue
            if (
                len(banks) != 1
                or len(receipts) != 1
                or banks[0]["status"] != "SETTLED"
                or action["status"] not in {"SUCCEEDED", "RECONCILED"}
            ):
                raise ValueError("JOINT_ORIGINAL_BANK_OR_RECEIPT_UNRESOLVED")
            verify_frozen_projection(
                banks[0],
                action,
                receipts[0],
                [
                    row
                    for row in raw["simulated_bank_postings"]
                    if row["operation_id"] == banks[0]["id"]
                ],
                observed_at=actual.base.as_of,
                subjects=subjects,
            )
        except (ValueError, TypeError, KeyError, StopIteration, OverflowError):
            unresolved.append(saved.action_id)
    for bank in raw["bank_operations"]:
        if (
            bank["operation_type"] == "ALLOCATE_GOAL"
            and UUID(bank["action_plan_id"]) not in data.original_action_ids
        ):
            unresolved.append(UUID(bank["action_plan_id"]))
    return sorted(set(unresolved), key=str)


def match_joint_effect(
    effect: ExecutionEffect,
    goal_id: UUID,
    amount: int,
    uses: list[Any],
    destination: UUID,
    version: UUID,
) -> None:
    original = sorted(
        (row.fragment_id, row.origin_transaction_id, row.account_id, row.amount_cents)
        for row in effect.income_uses
    )
    selected = sorted(
        (row.fragment_id, row.origin_transaction_id, row.source_account_id, row.amount_cents)
        for row in uses
        if row.goal_id == goal_id
    )
    if (
        effect.action_type != "ALLOCATE_GOAL"
        or effect.goal_id != goal_id
        or effect.destination_account_id != destination
        or effect.policy_version_id != version
        or effect.amount_cents != amount
        or original != selected
        or sum(row[-1] for row in selected) != amount
    ):
        raise ValueError("NOT_IMPLEMENTED_FOR_DIFFERENT_ALLOCATION_OR_SOURCE_USES")


def derive_joint_producers(supplied: JointActionSetInput) -> JointActionSetResult:
    data = JointActionSetInput.model_validate(supplied.model_dump())
    actual = data.original_actual_input
    original_actual = derive_actual_action_set(actual)
    goals = joint_goal_ids(actual)
    keys = [joint_key(identity) for identity in goals]
    reasons = [*data.source_reasons, *_structure(actual)]
    unresolved: list[UUID] = []
    results: list[JointProducerResult] = []
    try:
        if (
            data.expected_goal_ids != goals
            or data.original_action_ids != joint_action_ids(actual)
            or len(data.original_commands) != len(data.original_action_ids)
            or {row.action_id for row in data.original_commands} != set(data.original_action_ids)
        ):
            raise ValueError("JOINT_COMPLETE_GOAL_OR_COMMAND_DENOMINATOR_DIFFERS")
        if len(goals) > MAX_GOALS or len(data.model_dump_json().encode("utf8")) > MAX_BYTES:
            raise ValueError("JOINT_CURRENT_CAPTURE_CAPACITY_EXCEEDED")
        unresolved = _history(data)
        if unresolved:
            raise ValueError("JOINT_ORIGINAL_INFLIGHT_RESPONSIBILITY_RETAINED")
        if any(
            row["template_name"] == "GoalAllocationPolicy"
            and row["epoch_id"] == str(actual.base.epoch_id)
            and row["status"] in {"ACTIVE", "CONFIRMED"}
            for row in actual.base.original_inventory["full_policies"]
        ):
            raise ValueError("JOINT_FULL_ALLOCATION_POLICY_NOT_CONSUMED_BY_ORIGINAL_PLANNER")
        if not goals:
            if actual.dynamic_goals:
                raise ValueError("JOINT_ORPHAN_MODEL_OR_GOAL_SOURCE_RETAINED")
        else:
            _sources(data)
            _original_funds_and_goals(data)
            _protection_basis(data)
            original, planning, protection = (
                data.original_joint_input,
                data.actual_planning,
                data.protection_inputs,
            )
            if original is None or planning is None or protection is None:
                raise ValueError("JOINT_ACTUAL_PLANNER_OR_PROTECTION_MISSING")
            projection = project_full_protection(protection)
            binding = bind_full_joint_input(
                original,
                projection,
                data.verified_source_refs,
                seasonal_proof_sources=protection.policies,
            )
            allocation = solve_multi_goal_allocation(binding.candidate)
            if (
                planning.user_id != actual.base.user_id
                or planning.as_of != actual.base.as_of
                or planning.original_joint.registered_goal_count != len(goals)
                or sorted(planning.original_joint.included_goal_ids, key=str) != goals
                or planning.original_joint.uncovered_goal_ids
                or not planning.original_joint.independent_bank_projection_matched
                or planning.original_joint.source_issues
                or planning.original_joint.allocation != solve_multi_goal_allocation(original)
                or planning.full_protection.projection != projection
                or planning.full_protection.full_policy_sources != protection.policies
                or not planning.full_protection.audit.complete
                or planning.full_protection.audit.epoch_id != actual.base.epoch_id
                or planning.full_protection.audit.status != "VALID"
                or planning.full_protection.source_issues
                or planning.binding != binding
                or planning.allocation != allocation
                or planning.state != "COMPUTED"
                or planning.full_protection.input_digest
                != configuration_hash(
                    {
                        "original_financial_digest": protection.snapshot.source_digest,
                        "full_input_hash": projection.input_hash,
                    }
                )
                or planning.input_hash
                != configuration_hash(
                    {
                        "original_joint_hash": planning.original_joint.input_hash,
                        "full_protection_hash": planning.full_protection.input_digest,
                        "binding_hash": binding.binding_hash,
                        "reasons": sorted(set([*binding.reasons, *allocation.reasons])),
                    }
                )
                or binding.status != "VERIFIED"
                or allocation.status != "OPTIMAL"
                or planning.reasons != sorted(set([*binding.reasons, *allocation.reasons]))
                or binding.reasons
            ):
                raise ValueError("JOINT_FULL_ORIGINAL_SOLVER_SOURCES_OR_1098_POINTS_NOT_REPLAYED")
            amounts = {row.goal_id: row.amount_cents for row in allocation.goals}
            if set(amounts) != set(goals) or len(amounts) != len(allocation.goals):
                raise ValueError("JOINT_COMPLETE_SOLVER_GOAL_DENOMINATOR_DIFFERS")
            dynamic = {row.candidate_key: row for row in actual.dynamic_goals}
            original_views = {row.candidate_key: row for row in original_actual.candidates}
            for goal in original.goals:
                amount = amounts[goal.goal_id]
                key = joint_key(goal.goal_id)
                shadow = None
                if type(amount) is not int:
                    raise ValueError("JOINT_NULL_ALLOCATION_IS_NOT_ZERO")
                if amount == 0:
                    view = CandidateView(
                        candidate_key=key,
                        state="EXCLUDED",
                        action_type="ALLOCATE_GOAL",
                        amount_cents=0,
                        autonomy_level=None,
                        signature=None,
                        reasons=["EXACT_OPTIMAL_CURRENT_JOINT_ZERO_NO_EXISTING_PRODUCER_SHADOW"],
                    )
                else:
                    old_key = "goal:" + str(goal.goal_id)
                    view, candidate = dynamic_candidate(
                        actual.base, dynamic[old_key], actual_v2_native_values=True
                    )
                    if (
                        candidate is None
                        or candidate.facts is None
                        or candidate.facts.effect is None
                        or view.state != "INCLUDED"
                        or view != original_views.get(old_key)
                    ):
                        raise ValueError("JOINT_COMPLETE_EXISTING_ECONOMIC_EFFECT_NOT_AVAILABLE")
                    match_joint_effect(
                        candidate.facts.effect,
                        goal.goal_id,
                        amount,
                        allocation.income_uses,
                        goal.account_id,
                        goal.effective_policy_version_id,
                    )
                    shadow = old_key
                    view = view.model_copy(update={"candidate_key": key})
                results.append(
                    JointProducerResult(
                        candidate_key=key,
                        goal_id=goal.goal_id,
                        view=view,
                        shadow_original_candidate_key=shadow,
                    )
                )
    except (ValueError, TypeError, KeyError, StopIteration, OverflowError) as error:
        reasons.append(str(error))
    if reasons:
        # No partial success is represented as an executable whole joint batch.
        results = [
            JointProducerResult(
                candidate_key=joint_key(identity),
                goal_id=identity,
                view=CandidateView(
                    candidate_key=joint_key(identity),
                    state="UNKNOWN",
                    action_type="ALLOCATE_GOAL",
                    amount_cents=None,
                    autonomy_level=None,
                    signature=None,
                    reasons=sorted(set(reasons)),
                ),
            )
            for identity in goals
        ]
    complete = not reasons and len(results) == len(goals)
    values = dict(
        user_id=actual.base.user_id,
        epoch_id=actual.base.epoch_id,
        as_of=actual.base.as_of,
        status="COMPLETE_REGISTERED_JOINT_FAMILY" if complete else "UNKNOWN",
        joint_family_complete=complete,
        original_actual_input_hash=configuration_hash(actual.model_dump(mode="json")),
        expected_goal_ids=goals,
        original_action_ids=data.original_action_ids,
        unresolved_original_action_ids=unresolved,
        expected_candidate_keys=keys,
        results=results,
        handled_unsupported_codes=[],
        original_actual_reasons=original_actual.reasons,
        remaining_unsupported_producers=original_actual.unsupported_producers,
        reasons=sorted(set(reasons)),
        input_hash=configuration_hash(data.model_dump(mode="json")),
    )
    result = JointActionSetResult.model_validate(values | {"result_hash": "0" * 64})
    return result.model_copy(
        update={
            "result_hash": configuration_hash(
                result.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )
