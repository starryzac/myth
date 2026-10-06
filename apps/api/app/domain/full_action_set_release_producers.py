"""Exact current emergency cash-release candidates; never an execution grant."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from app.domain.audit_chain import build_subject, verify_frozen_projection
from app.domain.boundary_types import BoundaryModel, BoundaryProduct, FixedReturnTerms
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.execution_types import BankCommand
from app.domain.full_action_set_boundary import CandidateView, Hash
from app.domain.full_action_set_boundary_actual import (
    ActualActionSetInput,
    _structure,
    derive_actual_action_set,
)
from app.domain.full_goal_reallocation import ReallocationDecisionInput, decide_cash_reallocation
from app.domain.full_goal_release_audit import read_frozen_goal_release_action
from app.domain.full_goal_release_authorization import SOURCE, GoalReleaseAuthorization
from app.domain.full_goal_release_execution import (
    GoalReleaseBankCommand,
    compute_release_policy_usage,
    replay_goal_release_residuals,
    validate_release_authorization_binding,
)
from app.domain.full_policy_configuration import CrossGoalReallocationPolicy, LongTermGoalPolicy
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.goal_release_provenance import GoalCashSourceProof
from app.domain.income_ledger import IncomeLedger
from app.domain.policy_configuration import configuration_hash
from app.services.full_goal_release_authorization import ReleaseAuthorizationResponse
from app.services.full_goal_release_execution import (
    GoalReleaseCandidate,
    select_original_release_uses,
)
from app.services.full_goal_release_inventory import validate_retained_goal_cash_basis
from app.services.full_goals import (
    MODEL_SOURCE,
    FullGoalModelContent,
    _request_hash,
    canonical_goal_bridge,
)
from app.services.full_policy_lifecycle import FullPolicyView
from pydantic import Field

ALGORITHM: Literal["full-policy-release-action-producers-v1"] = (
    "full-policy-release-action-producers-v1"
)
MAX_PRODUCERS = 64
MAX_BYTES = 16 * 1024 * 1024


class ReleaseAuthorizationOriginal(BoundaryModel):
    source_id: UUID
    original_trace: DecisionTrace | None = None
    current_original: ReleaseAuthorizationResponse | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class ReleaseOriginalCommand(BoundaryModel):
    action_id: UUID
    prepare_trace: DecisionTrace | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class ReleaseProducerInput(BoundaryModel):
    full_policy_id: UUID
    source_goal_id: UUID
    destination_account_id: UUID
    full_policy: FullPolicyView | None = None
    authorization_source_id: UUID | None = None
    actual_candidate: GoalReleaseCandidate | None = None
    command: GoalReleaseBankCommand | None = None
    repair_inputs: ReallocationDecisionInput | None = None
    protection_inputs: FullProtectionProjectionInput | None = None
    full_input_digest: Hash | None = None
    income: IncomeLedger | None = None
    income_evidence_id: UUID | None = None
    income_evidence_hash: Hash | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class ReleaseActionSetInput(BoundaryModel):
    protocol: Literal["release-action-set-input-v1"] = "release-action-set-input-v1"
    original_actual_input: ActualActionSetInput
    expected_full_policy_ids: list[UUID]
    original_authorization_source_ids: list[UUID]
    original_action_ids: list[UUID]
    authorizations: list[ReleaseAuthorizationOriginal]
    original_commands: list[ReleaseOriginalCommand]
    producers: list[ReleaseProducerInput]
    source_reasons: list[str] = Field(default_factory=list)


class ReleaseProducerResult(BoundaryModel):
    candidate_key: str
    full_policy_id: UUID
    source_goal_id: UUID
    destination_account_id: UUID
    view: CandidateView
    unresolved_original_action_ids: list[UUID]
    requires_new_exact_user_confirmation: Literal[True] = True
    shadow_original_candidate_key: Literal[None] = None


class ReleaseActionSetResult(BoundaryModel):
    algorithm_version: Literal["full-policy-release-action-producers-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    full_global_adapter_installed: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE_REGISTERED_RELEASE_FAMILY", "UNKNOWN"]
    release_family_complete: bool
    original_actual_input_hash: Hash
    input_hash: Hash
    result_hash: Hash
    expected_full_policy_ids: list[UUID]
    original_authorization_source_ids: list[UUID]
    original_action_ids: list[UUID]
    unresolved_original_action_ids: list[UUID]
    expected_candidate_keys: list[str]
    results: list[ReleaseProducerResult]
    handled_unsupported_codes: list[str]
    original_actual_reasons: list[str]
    remaining_unsupported_producers: list[str]
    reasons: list[str]


def release_key(policy: UUID, goal: UUID, destination: UUID) -> str:
    return f"full-release:{policy}:{goal}:{destination}"


def release_policy_ids(actual: ActualActionSetInput) -> list[UUID]:
    return sorted(
        (
            UUID(row["id"])
            for row in actual.base.original_inventory["full_policies"]
            if row["epoch_id"] == str(actual.base.epoch_id)
            and row["template_name"] == "CrossGoalReallocationPolicy"
        ),
        key=str,
    )


def release_authorization_ids(actual: ActualActionSetInput) -> list[UUID]:
    # All owned authorization originals, including stale/archived/malformed originals.
    return sorted(
        (
            UUID(row["id"])
            for row in actual.base.original_inventory["evidence_items"]
            if row["source_type"] == SOURCE
        ),
        key=str,
    )


def release_action_ids(actual: ActualActionSetInput) -> list[UUID]:
    return sorted(
        (
            UUID(row["id"])
            for row in actual.base.original_inventory["action_plans"]
            if row["action_type"] == "RELEASE_GOAL" or "goal_release_execution" in row["request"]
        ),
        key=str,
    )


def release_producer_keys(actual: ActualActionSetInput) -> list[tuple[UUID, UUID, UUID]]:
    inventory = actual.base.original_inventory
    destinations = sorted(
        (UUID(row["id"]) for row in inventory["accounts"] if row["account_type"] == "CASH"), key=str
    )
    result: list[tuple[UUID, UUID, UUID]] = []
    for identity in release_policy_ids(actual):
        version = max(
            (row for row in inventory["full_policy_versions"] if row["policy_id"] == str(identity)),
            key=lambda row: row["version_number"],
        )
        config = CrossGoalReallocationPolicy.model_validate_json(
            json.dumps(version["configuration"])
        )
        # A missing listed Goal remains a candidate UNKNOWN, never a silently removed source.
        result.extend(
            (identity, goal, destination)
            for goal in sorted(config.source_goal_ids, key=str)
            for destination in destinations
        )
    return result


def _clock(value: Any) -> datetime:
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("RELEASE_ORIGINAL_CLOCK_NOT_AWARE")
    return value


def _full(actual: ActualActionSetInput, full: FullPolicyView) -> CrossGoalReallocationPolicy:
    inventory = actual.base.original_inventory
    raw = next(row for row in inventory["full_policies"] if row["id"] == str(full.policy_id))
    version = max(
        (row for row in inventory["full_policy_versions"] if row["policy_id"] == raw["id"]),
        key=lambda row: row["version_number"],
    )
    current = full.current_version
    if (
        full.epoch_id != actual.base.epoch_id
        or full.template_name != "CrossGoalReallocationPolicy"
        or full.status != raw["status"]
        or str(current.version_id) != version["id"]
        or current.version_number != version["version_number"]
        or current.configuration != version["configuration"]
        or current.content_hash != version["content_hash"]
        or configuration_hash(current.configuration) != current.content_hash
        or current.confirmation != version["confirmation"]
        or current.confirmation.get("accepted") is not True
        or current.confirmation.get("reviewed_hash") != current.content_hash
        or current.evidence_ids != [UUID(key) for key in version["evidence_ids"]]
        or current.confirmed_at != _clock(version["confirmed_at"])
        or current.valid_from != _clock(version["valid_from"])
        or current.valid_until
        != (None if version["valid_until"] is None else _clock(version["valid_until"]))
    ):
        raise ValueError("RELEASE_CURRENT_FULL_ORIGINAL_DIFFERS")
    if not current.evidence_ids:
        raise ValueError("RELEASE_CURRENT_FULL_CONFIRMATION_SOURCE_MISSING")
    for key in current.evidence_ids:
        proof = next(row for row in inventory["evidence_items"] if row["id"] == str(key))
        if (
            proof["user_id"] != str(actual.base.user_id)
            or proof["status"] != "VALID"
            or proof["content_hash"] != configuration_hash(proof["content"])
            or _clock(proof["observed_at"]) > actual.base.as_of
        ):
            raise ValueError("RELEASE_CURRENT_FULL_CONFIRMATION_SOURCE_NOT_PROVEN")
    return CrossGoalReallocationPolicy.model_validate_json(json.dumps(current.configuration))


def _authorization(
    actual: ActualActionSetInput, original: ReleaseAuthorizationOriginal
) -> GoalReleaseAuthorization:
    if (
        original.missing_reasons
        or original.original_trace is None
        or original.current_original is None
    ):
        raise ValueError("RELEASE_AUTHORIZATION_ORIGINAL_TRACE_OR_CURRENT_SOURCE_MISSING")
    raw = next(
        row
        for row in actual.base.original_inventory["evidence_items"]
        if row["id"] == str(original.source_id)
    )
    saved = GoalReleaseAuthorization.model_validate_json(json.dumps(raw["content"]))
    trace, current = original.original_trace, original.current_original
    verify_trace(trace)
    if (
        raw["user_id"] != str(actual.base.user_id)
        or raw["source_type"] != SOURCE
        or raw["evidence_level"] != "USER_CONFIRMED_POLICY"
        or raw["status"] != "VALID"
        or raw["content_hash"] != configuration_hash(raw["content"])
        or raw["source_ref"] != str(saved.authorization_id)
        or _clock(raw["created_at"]) != saved.confirmed_at
        or _clock(raw["valid_from"]) != saved.confirmed_at
        or _clock(raw["valid_to"]) != saved.valid_until
        or original.source_id != uuid5(saved.authorization_id, "authorization-evidence")
        or current.original_authorization != saved
        or current.evidence_id != original.source_id
        or current.evidence_hash != raw["content_hash"]
        or current.original_trace_hash != trace.trace_hash
        or trace.user_id != actual.base.user_id
        or trace.run_id != saved.authorization_id
        or trace.algorithm_versions.get("goal_release_authorization") != saved.protocol
        or trace.inputs["original_request"] != saved.original_request.model_dump(mode="json")
        or trace.outcome["goal_release_authorization"] != raw["content"]
        or _clock(raw["observed_at"]) != saved.confirmed_at
        or saved.confirmed_at > actual.base.as_of
    ):
        raise ValueError("RELEASE_AUTHORIZATION_IMMUTABLE_ORIGINAL_DIFFERS")
    copied = [row for row in trace.sources if row.id == original.source_id]
    if (
        len(copied) != 1
        or copied[0].content != raw["content"]
        or copied[0].content_integrity != "VERIFIED"
    ):
        raise ValueError("RELEASE_AUTHORIZATION_ORIGINAL_SOURCE_COPY_MISSING")
    if current.current_scope_status == "UNKNOWN":
        raise ValueError("RELEASE_CURRENT_AUTHORIZATION_SCOPE_UNKNOWN")
    if (current.current_scope_status == "CURRENT") != _scope_is_current(actual, saved):
        raise ValueError("RELEASE_CURRENT_AUTHORIZATION_SCOPE_LABEL_DIFFERS")
    return saved


def _scope_is_current(actual: ActualActionSetInput, saved: GoalReleaseAuthorization) -> bool:
    """Derive the permission binding from the actual current originals, not its label."""
    base, scope = actual.base, saved.scope
    raw = base.original_inventory
    if saved.epoch_id != base.epoch_id or base.as_of >= saved.valid_until:
        return False
    parent = next(row for row in raw["full_policies"] if row["id"] == str(saved.policy_id))
    latest = max(
        (row for row in raw["full_policy_versions"] if row["policy_id"] == parent["id"]),
        key=lambda row: row["version_number"],
    )
    config = CrossGoalReallocationPolicy.model_validate_json(json.dumps(latest["configuration"]))
    if parent["status"] in {"REVOKED", "SUSPENDED", "EXPIRED"} or not config.enabled:
        return False
    if latest["id"] != str(saved.policy_version_id):
        return False
    if (
        latest["content_hash"] != scope.policy_configuration_hash
        or configuration_hash(latest["configuration"]) != scope.policy_configuration_hash
        or latest["confirmation"].get("accepted") is not True
        or latest["confirmation"].get("reviewed_hash") != scope.policy_configuration_hash
        or sorted(config.source_goal_ids, key=str) != [row.goal_id for row in scope.source_goals]
        or sorted(config.emergency_conditions) != scope.emergency_conditions
        or config.single_action_cap_cents != scope.single_action_cap_cents
        or config.total_cap_cents != scope.total_cap_cents
        or not _clock(latest["valid_from"]) <= base.as_of
        or latest["valid_until"] is None
        or base.as_of >= _clock(latest["valid_until"])
    ):
        raise ValueError("RELEASE_CURRENT_FINITE_SCOPE_OR_FULL_CONFIRMATION_NOT_PROVEN")
    for binding in scope.source_goals:
        goal = next(row for row in raw["goals"] if row["id"] == str(binding.goal_id))
        if goal["policy_version_id"] != str(binding.original_policy_version_id):
            return False
        originals = [
            row
            for row in raw["evidence_items"]
            if row["source_type"] == MODEL_SOURCE and row["source_ref"] == str(binding.goal_id)
        ]
        evidence = max(originals, key=lambda row: (_clock(row["created_at"]), row["id"]))
        if evidence["id"] != str(binding.full_model_evidence_id):
            return False
        model = FullGoalModelContent.model_validate_json(json.dumps(evidence["content"]))
        full_config, base_config = canonical_goal_bridge(model.full_configuration)
        version = next(
            row for row in raw["policy_versions"] if row["id"] == goal["policy_version_id"]
        )
        latest_mvp = max(
            (row for row in raw["policy_versions"] if row["policy_id"] == goal["policy_id"]),
            key=lambda row: row["version_number"],
        )
        policy = next(row for row in raw["policies"] if row["id"] == goal["policy_id"])
        if version != latest_mvp or policy["status"] not in {"ACTIVE", "CONFIRMED"}:
            return False
        if _clock(version["valid_from"]) > base.as_of or (
            version["valid_until"] is not None and base.as_of >= _clock(version["valid_until"])
        ):
            return False
        if (
            model.user_id != base.user_id
            or model.epoch_id != base.epoch_id
            or model.goal_id != binding.goal_id
            or model.policy_id != binding.original_policy_id
            or model.base_policy_version_id != binding.original_policy_version_id
            or model.full_hash != binding.full_configuration_hash
            or model.full_hash != configuration_hash(model.full_configuration)
            or model.full_configuration != full_config
            or model.base_hash != configuration_hash(base_config)
            or model.request_hash
            != _request_hash(
                model.user_id,
                model.epoch_id,
                model.goal_id,
                model.expected_version_id,
                full_config,
                model.reason,
                model.idempotency_key,
            )
            or _clock(evidence["created_at"]) != model.confirmed_at
            or _clock(evidence["observed_at"]) != model.confirmed_at
            or _clock(evidence["valid_from"]) != model.confirmed_at
            or model.base_hash != version["content_hash"]
            or evidence["evidence_level"] != "USER_CONFIRMED_POLICY"
            or evidence["status"] != "VALID"
            or model.confirmed_at > base.as_of
            or evidence["content_hash"] != binding.full_model_evidence_hash
            or evidence["content_hash"] != configuration_hash(evidence["content"])
            or LongTermGoalPolicy.model_validate_json(
                json.dumps(model.full_configuration)
            ).minimum_guarantee_cents
            != binding.minimum_guarantee_cents
            or goal["minimum_protection_cents"] != binding.minimum_guarantee_cents
        ):
            raise ValueError("RELEASE_CURRENT_FULL_GOAL_MODEL_BINDING_NOT_PROVEN")
    return True


def _history(actual: ActualActionSetInput, original: ReleaseOriginalCommand) -> bool:
    if original.missing_reasons or original.prepare_trace is None:
        return True
    inventory = actual.base.original_inventory
    action = next(row for row in inventory["action_plans"] if row["id"] == str(original.action_id))
    command, _ = read_frozen_goal_release_action(action)
    trace = original.prepare_trace
    verify_trace(trace)
    if (
        trace.phase != "PREPARE"
        or trace.user_id != actual.base.user_id
        or trace.action_id != original.action_id
        or str(trace.run_id) != action["decision_run_id"]
        or trace.as_of > actual.base.as_of
        or trace.algorithm_versions.get("goal_release") != "full-goal-emergency-release-v1"
        or trace.inputs["action_request"] != action["request"]
        or trace.inputs["goal_release_effect"] != command.effect.model_dump(mode="json")
    ):
        raise ValueError("RELEASE_ORIGINAL_COMMAND_TRACE_DIFFERS")
    if action["status"] in {"SUBMITTED", "UNKNOWN"} or any(
        row["action_plan_id"] == action["id"] and row["status"] == "RESERVED"
        for row in inventory["action_resource_reservations"]
    ):
        return True
    banks = [row for row in inventory["bank_operations"] if row["action_plan_id"] == action["id"]]
    receipts = [
        row for row in inventory["action_receipts"] if row["action_plan_id"] == action["id"]
    ]
    if not banks:
        return bool(receipts) or action["status"] not in {
            "PLANNED",
            "AUTHORIZED",
            "INVALIDATED",
            "CANCELLED",
        }
    if (
        len(banks) != 1
        or len(receipts) != 1
        or banks[0]["status"] != "SETTLED"
        or action["status"] not in {"SUCCEEDED", "RECONCILED"}
    ):
        return True
    effect = command.effect
    identity = uuid5(effect.operation_id, "release-confirmation:" + command.effect_hash)
    proof = next(row for row in inventory["evidence_items"] if row["id"] == str(identity))
    confirmed = _clock(proof["observed_at"])
    expected = {
        "protocol": "goal-release-action-confirmation-v1",
        "simulation": True,
        "user_id": str(effect.user_id),
        "action_id": str(effect.operation_id),
        "epoch_id": str(effect.epoch_id),
        "effect_hash": command.effect_hash,
        "accepted": True,
        "confirmed_at": confirmed.isoformat(),
        "valid_until": effect.expires_at.isoformat(),
    }
    if (
        proof["user_id"] != str(effect.user_id)
        or proof["evidence_level"] != "USER_CONFIRMED_ACTION"
        or proof["source_type"] != "USER_GOAL_RELEASE_ACTION_CONFIRMATION"
        or proof["source_ref"] != str(effect.operation_id)
        or proof["status"] != "VALID"
        or proof["content"] != expected
        or type(proof["content"].get("accepted")) is not bool
        or proof["content_hash"] != configuration_hash(expected)
        or _clock(proof["created_at"]) != confirmed
        or _clock(proof["valid_from"]) != confirmed
        or _clock(proof["valid_to"]) != effect.expires_at
        or not effect.valid_from <= confirmed < effect.expires_at
    ):
        raise ValueError("RELEASE_ORIGINAL_ACTION_CONFIRMATION_NOT_PROVEN")
    subjects = [
        build_subject(
            user_id=actual.base.user_id,
            epoch_id=actual.base.epoch_id,
            kind=kind,
            id=UUID(row["id"]),
            data=row,
        )
        for kind, table in (("TRANSACTION", "transactions"), ("EVIDENCE", "evidence_items"))
        for row in inventory[table]
    ]
    verify_frozen_projection(
        banks[0],
        action,
        receipts[0],
        [
            row
            for row in inventory["simulated_bank_postings"]
            if row["operation_id"] == banks[0]["id"]
        ],
        observed_at=actual.base.as_of,
        subjects=subjects,
    )
    return False


def _protection(actual: ActualActionSetInput, item: ReleaseProducerInput) -> None:
    candidate, inputs = item.actual_candidate, item.protection_inputs
    if (
        candidate is None
        or candidate.protection is None
        or inputs is None
        or item.full_input_digest is None
    ):
        raise ValueError("RELEASE_COMPLETE_NONWORSENING_INPUTS_MISSING")
    amount = candidate.amount_cents
    if type(amount) is not int or amount <= 0:
        raise ValueError("RELEASE_CURRENT_POSITIVE_AMOUNT_MISSING")
    observed = inputs.snapshot.model_dump(mode="json")
    expected = dict(actual.base.financial_basis["snapshot"])
    for original_snapshot in (observed, expected):
        original_snapshot.pop("source_digest", None)
        original_snapshot.pop("horizon_days", None)
    if (
        observed != expected
        or inputs.snapshot.as_of != actual.base.as_of
        or inputs.snapshot.horizon_days != 365
        or [row.model_dump(mode="json") for row in inputs.boundary_versions]
        != actual.base.financial_basis["versions"]
        or [row.model_dump(mode="json") for row in inputs.positions]
        != actual.base.financial_basis["positions"]
    ):
        raise ValueError("RELEASE_SAME_INVOCATION_FINANCIAL_INPUT_DIFFERS")
    _product_originals(actual, inputs.boundary_products)
    raw = actual.base.original_inventory
    policies = [
        row
        for row in raw["full_policies"]
        if row["epoch_id"] == str(actual.base.epoch_id)
        and row["template_name"]
        in {"DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"}
    ]
    if {row.policy_id for row in inputs.policies} != {UUID(row["id"]) for row in policies} or len(
        inputs.policies
    ) != len(policies):
        raise ValueError("RELEASE_COMPLETE_FULL_PROTECTION_POLICY_DENOMINATOR_DIFFERS")
    for policy_source in inputs.policies:
        version = max(
            (
                row
                for row in raw["full_policy_versions"]
                if row["policy_id"] == str(policy_source.policy_id)
            ),
            key=lambda row: row["version_number"],
        )
        if (
            str(policy_source.version_id) != version["id"]
            or policy_source.configuration != version["configuration"]
            or policy_source.content_hash != version["content_hash"]
            or policy_source.confirmation != version["confirmation"]
            or policy_source.evidence_ids != [UUID(key) for key in version["evidence_ids"]]
            or policy_source.reference_snapshots
            != version["impact_analysis"].get("reference_snapshots", [])
        ):
            raise ValueError("RELEASE_CURRENT_FULL_PROTECTION_ORIGINAL_DIFFERS")
    before = project_full_protection(inputs)
    goal = next(row for row in inputs.snapshot.goals if row.goal_id == item.source_goal_id)
    source = next(row for row in inputs.snapshot.cash_accounts if row.account_id == goal.account_id)
    target = next(
        row
        for row in inputs.snapshot.cash_accounts
        if row.account_id == item.destination_account_id
    )
    if (
        source.account_id == target.account_id
        or target.account_type != "CASH"
        or goal.cash_owned_cents < amount
        or source.balance_cents < amount
        or before.status == "UNKNOWN"
        or before.source_account_checks
    ):
        raise ValueError("RELEASE_CURRENT_PROTECTION_OR_SOURCE_ACCOUNT_NOT_PROVEN")
    snapshot = inputs.snapshot.model_copy(
        update={
            "cash_accounts": [
                row.model_copy(
                    update={
                        "balance_cents": row.balance_cents - amount
                        if row.account_id == source.account_id
                        else row.balance_cents + amount
                    }
                )
                if row.account_id in {source.account_id, target.account_id}
                else row
                for row in inputs.snapshot.cash_accounts
            ],
            "goals": [
                row.model_copy(
                    update={
                        "cash_owned_cents": row.cash_owned_cents - amount,
                        "allocated_cents": row.allocated_cents - amount,
                    }
                )
                if row.goal_id == item.source_goal_id
                else row
                for row in inputs.snapshot.goals
            ],
        }
    )
    after = project_full_protection(inputs.model_copy(update={"snapshot": snapshot}))
    old, new = before.full_annual_projection, after.full_annual_projection
    if (
        old is None
        or new is None
        or len(old.calculation_trace) != 1098
        or len(new.calculation_trace) != 1098
    ):
        raise ValueError("RELEASE_COMPLETE_365_THREE_PHASE_PROTECTION_MISSING")
    if (
        after.status == "UNKNOWN"
        or after.source_account_checks
        or any(
            (left.day, left.date, left.phase) != (right.day, right.date, right.phase)
            or right.cash_cents < left.cash_cents
            or right.margin_cents < left.margin_cents
            or sum(right.protected_cents_by_reason.values())
            > sum(left.protected_cents_by_reason.values())
            for left, right in zip(old.calculation_trace, new.calculation_trace, strict=True)
        )
    ):
        raise ValueError("RELEASE_WORSENS_ORIGINAL_FULL_PROTECTION")
    proof = candidate.protection
    if (
        item.full_input_digest
        != configuration_hash(
            {
                "original_financial_digest": inputs.snapshot.source_digest,
                "full_input_hash": before.input_hash,
            }
        )
        or candidate.actual_preview.full_protection_input_hash != item.full_input_digest
        or proof.status != "VERIFIED_NONWORSENING"
        or proof.reasons
        or proof.compared_point_count != 1098
        or proof.actual_before_input_hash != before.input_hash
        or proof.hypothetical_after_input_hash != after.input_hash
        or proof.source_hash
        != configuration_hash(
            {
                "full_input": item.full_input_digest,
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json"),
                "amount": amount,
                "destination": str(item.destination_account_id),
            }
        )
    ):
        raise ValueError("RELEASE_ORIGINAL_PROTECTION_PROOF_DIFFERS")


def _product_originals(actual: ActualActionSetInput, products: list[BoundaryProduct]) -> None:
    raws = [
        row
        for row in actual.base.original_inventory["asset_products"]
        if _clock(row["effective_from"]) <= actual.base.as_of
        and (row["effective_until"] is None or actual.base.as_of < _clock(row["effective_until"]))
    ]
    if {row.product_id for row in products} != {UUID(row["id"]) for row in raws} or len(
        products
    ) != len(raws):
        raise ValueError("RELEASE_COMPLETE_BOUNDARY_PRODUCT_DENOMINATOR_DIFFERS")
    for product in products:
        raw = next(row for row in raws if row["id"] == str(product.product_id))
        rule = raw["maturity_rule"]
        fixed = None
        if rule.get("protocol") == "fixed-principal-return-v1":
            if (
                rule.get("day_basis") != "CALENDAR"
                or rule.get("guaranteed") is not True
                or rule.get("auto_rollover", False) is not False
                or raw["principal_fluctuation"]
                or raw["risk_level"] != 0
            ):
                raise ValueError("RELEASE_ORIGINAL_FIXED_PRINCIPAL_TERMS_NOT_PROVEN")
            fixed = FixedReturnTerms.model_validate(
                {
                    key: rule[key]
                    for key in (
                        "term_days",
                        "settlement_delay_days",
                        "principal_return_bps",
                        "rollover",
                    )
                }
            )
            if (
                fixed.term_days < raw["lock_days"]
                or fixed.settlement_delay_days < raw["redemption_delay_days"]
            ):
                raise ValueError("RELEASE_ORIGINAL_FIXED_PRINCIPAL_TIMING_DIFFERS")
        fields = {
            "product_id": raw["id"],
            "version_number": raw["version_number"],
            "asset_class": raw["asset_class"],
            "minimum_purchase_cents": raw["minimum_purchase_cents"],
            "fixed_return": fixed.model_dump(mode="json") if fixed else None,
        }
        if product.model_dump(mode="json") != fields | {"terms_digest": configuration_hash(fields)}:
            raise ValueError("RELEASE_ORIGINAL_BOUNDARY_PRODUCT_TERMS_DIFFERS")


def _inventory(actual: ActualActionSetInput, item: ReleaseProducerInput) -> None:
    candidate, income = item.actual_candidate, item.income
    if candidate is None or income is None or item.income_evidence_id is None:
        raise ValueError("RELEASE_COMPLETE_ORIGINAL_INCOME_OR_INVENTORY_MISSING")
    inventory = candidate.actual_inventory
    raw = actual.base.original_inventory
    source = next(row for row in raw["evidence_items"] if row["id"] == str(item.income_evidence_id))
    if (
        income.user_id != actual.base.user_id
        or item.income_evidence_hash != source["content_hash"]
        or source["content_hash"] != configuration_hash(source["content"])
        or source["content"] != income.model_dump(mode="json")
        or source["source_type"] != "SIMULATED_NEW_FUNDS_LEDGER"
        or source["evidence_level"] != "BANK_CONFIRMED"
        or source["status"] != "VALID"
        or inventory.state != "VERIFIED"
        or not inventory.financial_truth_verified
        or not inventory.application_projection_matched
        or inventory.reasons
        or inventory.user_id != actual.base.user_id
        or inventory.epoch_id != actual.base.epoch_id
        or inventory.as_of != actual.base.as_of
        or inventory.goal_id != item.source_goal_id
        or inventory.full_policy_id != item.full_policy_id
    ):
        raise ValueError("RELEASE_CURRENT_ORIGINAL_INVENTORY_OR_INCOME_DIFFERS")
    rows = {(table, UUID(row["id"])): row for table, values in raw.items() for row in values}
    required = {"action_plans", "bank_operations", "action_receipts", "simulated_bank_postings"}
    if {row.table for row in inventory.inventory} != required or len(inventory.inventory) != len(
        required
    ):
        raise ValueError("RELEASE_COMPLETE_INVENTORY_TABLE_DENOMINATOR_DIFFERS")
    for table in inventory.inventory:
        expected = raw[table.table]
        if (
            not table.complete
            or table.actual_count != len(expected)
            or table.captured_count != len(expected)
            or len(table.original_refs) != len(expected)
            or {ref.row_id for ref in table.original_refs} != {UUID(row["id"]) for row in expected}
            or any(
                ref.row_hash != configuration_hash(rows[(ref.table, ref.row_id)])
                for ref in table.original_refs
            )
        ):
            raise ValueError("RELEASE_COMPLETE_CURRENT_INVENTORY_ORIGINALS_DIFFER")
    nonrelease = {
        UUID(row["id"])
        for row in raw["bank_operations"]
        if row["operation_type"] != "RELEASE_GOAL"
        and row["request"].get("protocol") != "full-goal-release-bank-v1"
    }
    validate_retained_goal_cash_basis(inventory.original_basis, rows, income, nonrelease)
    _allocation_originals(actual, income, inventory.original_basis)
    releases = [row for row in raw["bank_operations"] if UUID(row["id"]) not in nonrelease]
    if {row.bank_operation_id for row in inventory.release_originals} != {
        UUID(row["id"]) for row in releases
    } or len(inventory.release_originals) != len(releases):
        raise ValueError("RELEASE_ALL_VERSION_BANK_ORIGINAL_DENOMINATOR_DIFFERS")
    for original in inventory.release_originals:
        bank = rows[("bank_operations", original.bank_operation_id)]
        if (
            original.original_bank_row_hash != configuration_hash(bank)
            or original.command.model_dump(mode="json") != bank["request"]
            or original.status != bank["status"]
            or original.request_hash != bank["request_hash"]
            or original.action_plan_id != UUID(bank["action_plan_id"])
        ):
            raise ValueError("RELEASE_ORIGINAL_BANK_COMMAND_COPY_DIFFERS")
        postings = [
            row
            for row in raw["simulated_bank_postings"]
            if row["operation_id"] == str(original.bank_operation_id)
        ]
        if (
            {row.posting_id for row in original.postings} != {UUID(row["id"]) for row in postings}
            or len(original.postings) != len(postings)
            or any(
                row.original_row_hash
                != configuration_hash(rows[("simulated_bank_postings", row.posting_id)])
                for row in original.postings
            )
        ):
            raise ValueError("RELEASE_ORIGINAL_BANK_POSTING_COPY_DIFFERS")
    basis = inventory.original_basis
    goal = next(row for row in raw["goals"] if row["id"] == str(item.source_goal_id))
    heads = {
        key: max(
            (row for row in raw["simulated_bank_postings"] if row["ledger_key"] == key),
            key=lambda row: row["sequence_number"],
        )
        for key in (f"GOAL_CASH:{item.source_goal_id}", f"GOAL_PRINCIPAL:{item.source_goal_id}")
    }
    cash, principal = (
        heads[f"GOAL_CASH:{item.source_goal_id}"],
        heads[f"GOAL_PRINCIPAL:{item.source_goal_id}"],
    )
    if any(
        row["user_id"] != str(actual.base.user_id)
        or row["account_id"] != goal["account_id"]
        or row["ledger_metadata"] != {"goal_id": goal["id"], "account_id": goal["account_id"]}
        for row in (cash, principal)
    ):
        raise ValueError("RELEASE_CURRENT_BANK_GOAL_HEAD_IDENTITY_DIFFERS")
    residual = replay_goal_release_residuals(
        basis,
        inventory.release_originals,
        actual_release_operation_count=len(releases),
        whole_bank_source_verified=True,
        current_goal_cash_cents=cash["balance_after_cents"],
        current_goal_principal_cents=principal["balance_after_cents"],
        current_assigned_by_fragment={
            row.fragment_id: row.assigned_cents for row in income.fragments
        },
        current_source_binding_hash=inventory.source_binding_hash,
        now=actual.base.as_of,
    )
    usage = compute_release_policy_usage(
        actual.base.user_id,
        item.full_policy_id,
        inventory.release_originals,
        actual_release_operation_count=len(releases),
        whole_bank_source_verified=True,
        current_source_binding_hash=inventory.source_binding_hash,
        now=actual.base.as_of,
    )
    if (
        residual != inventory.residual
        or usage != inventory.policy_usage
        or residual.state != "VERIFIED"
    ):
        raise ValueError("RELEASE_CURRENT_RESIDUAL_OR_LIFETIME_USAGE_DIFFERS")
    amount = candidate.amount_cents
    if (
        type(amount) is not int
        or select_original_release_uses(inventory.release_uses_available, amount)
        != candidate.selected_release_uses
    ):
        raise ValueError("RELEASE_ORIGINAL_RESIDUAL_SELECTION_DIFFERS")


def _allocation_originals(
    actual: ActualActionSetInput, income: IncomeLedger, basis: GoalCashSourceProof
) -> None:
    """Recheck actual allocation commands/receipts and the full ASSIGNED denominator."""
    raw = actual.base.original_inventory
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
    committed = [
        row
        for row in income.reservations
        if row.operation == "ALLOCATE_GOAL" and row.state == "COMMITTED"
    ]
    totals: dict[UUID, int] = {}
    source_slices: dict[tuple[UUID, UUID], Any] = {}
    for reservation in committed:
        action = next(row for row in raw["action_plans"] if row["id"] == str(reservation.action_id))
        bank = next(row for row in raw["bank_operations"] if row["action_plan_id"] == action["id"])
        receipts = [row for row in raw["action_receipts"] if row["action_plan_id"] == action["id"]]
        command = BankCommand.model_validate_json(json.dumps(bank["request"]))
        effect = command.effect
        if (
            len(receipts) != 1
            or bank["status"] != "SETTLED"
            or effect.action_type != "ALLOCATE_GOAL"
            or command.model_dump(mode="json") != action["request"]["execution"]
            or tuple(effect.income_uses) != reservation.uses
            or effect.user_id != actual.base.user_id
        ):
            raise ValueError("RELEASE_ORIGINAL_ALLOCATION_COMMAND_OR_COMMITTED_USES_DIFFERS")
        verify_frozen_projection(
            bank,
            action,
            receipts[0],
            [row for row in raw["simulated_bank_postings"] if row["operation_id"] == bank["id"]],
            observed_at=actual.base.as_of,
            subjects=subjects,
        )
        for use in effect.income_uses:
            totals[use.fragment_id] = totals.get(use.fragment_id, 0) + use.amount_cents
            if effect.goal_id == basis.goal_id:
                source_slices[(effect.operation_id, use.fragment_id)] = (command, action, use)
    if any(row.assigned_cents != totals.get(row.fragment_id, 0) for row in income.fragments):
        raise ValueError("RELEASE_COMPLETE_ASSIGNED_SOURCE_DENOMINATOR_DIFFERS")
    if len(basis.original_allocation_slices) != len(source_slices):
        raise ValueError("RELEASE_COMPLETE_SOURCE_ALLOCATION_SLICE_DENOMINATOR_DIFFERS")
    for part in basis.original_allocation_slices:
        command, action, use = source_slices[(part.allocation_action_id, part.fragment_id)]
        if (
            part.original_allocated_cents != use.amount_cents
            or part.allocation_effect_hash != command.effect_hash
            or part.allocation_bank_request_hash
            != configuration_hash(command.model_dump(mode="json"))
            or part.allocation_action_request_hash != action["request_hash"]
            or part.origin_transaction_id != use.origin_transaction_id
            or part.income_location_account_id != use.account_id
        ):
            raise ValueError("RELEASE_ORIGINAL_CASH_SOURCE_SLICE_DIFFERS")


def _repair_source_binding(actual: ActualActionSetInput, item: ReleaseProducerInput) -> None:
    """Planning math must use these original financial points and the current source Goal."""
    if (
        item.protection_inputs is None
        or item.repair_inputs is None
        or item.actual_candidate is None
    ):
        raise ValueError("RELEASE_ORIGINAL_REPAIR_INPUT_MISSING")
    inputs, repair = item.protection_inputs, item.repair_inputs
    projection = project_full_protection(inputs)
    core = next(
        row
        for row in projection.original_execution_view.calculation_trace
        if row.day == 0 and row.phase == "BEFORE_PAYMENT"
    )
    full = projection.full_annual_projection
    if full is None:
        raise ValueError("RELEASE_COMPLETE_CURRENT_REPAIR_PROTECTION_MISSING")
    point = next(
        row for row in full.calculation_trace if row.day == 0 and row.phase == "BEFORE_PAYMENT"
    )
    owned = next(row for row in inputs.snapshot.goals if row.goal_id == item.source_goal_id)
    goal = next(
        row
        for row in actual.base.original_inventory["goals"]
        if row["id"] == str(item.source_goal_id)
    )
    reservations = [
        row
        for row in actual.base.original_inventory["action_resource_reservations"]
        if row["status"] == "RESERVED"
    ]
    expected_conditions = dict(
        zip(
            ("HARD_OBLIGATION_SHORTFALL", "LIVING_RESERVE_SHORTFALL", "EMERGENCY_BUFFER_SHORTFALL"),
            (
                core.protected_cents_by_reason.get(key, 0)
                for key in ("obligations", "living", "emergency")
            ),
            strict=True,
        )
    )
    additional = sum(
        value
        for key, value in point.protected_cents_by_reason.items()
        if key not in {"obligations", "living", "emergency", "goal_cash", "goal_minimum"}
    )
    if (
        reservations
        or not repair.financial.verified
        or not repair.goal.ownership_verified
        or not repair.goal.model_verified
        or repair.financial.cash_cents != core.cash_cents
        or repair.financial.locked_goal_cash_cents
        != core.protected_cents_by_reason.get("goal_cash", 0)
        or repair.financial.reserved_cash_cents != 0
        or repair.goal.reserved_goal_cash_cents != 0
        or repair.financial.required_by_condition != expected_conditions
        or repair.financial.other_full_protection_cents != additional
        or repair.goal.goal_id != item.source_goal_id
        or repair.goal.account_id != owned.account_id
        or repair.goal.cash_owned_cents != owned.cash_owned_cents
        or repair.goal.principal_owned_cents != owned.principal_owned_cents
        or repair.goal.minimum_guarantee_cents != goal["minimum_protection_cents"]
        or str(repair.goal.original_policy_version_id) != goal["policy_version_id"]
        or repair.policy.policy_id != item.full_policy_id
        or repair.policy.epoch_id != actual.base.epoch_id
        or item.full_policy is None
        or repair.policy.version_id != item.full_policy.current_version.version_id
        or repair.policy.content_hash != item.full_policy.current_version.content_hash
        or repair.policy.configuration.model_dump(mode="json")
        != item.full_policy.current_version.configuration
    ):
        raise ValueError("RELEASE_CURRENT_REPAIR_MATH_SOURCE_DIFFERS")


def _one(
    actual: ActualActionSetInput,
    item: ReleaseProducerInput,
    auths: dict[UUID, ReleaseAuthorizationOriginal],
    unresolved: list[UUID],
) -> ReleaseProducerResult:
    key = release_key(item.full_policy_id, item.source_goal_id, item.destination_account_id)
    state: Literal["INCLUDED", "EXCLUDED", "UNKNOWN"] = "UNKNOWN"
    reason = None
    amount, signature = None, None
    try:
        if item.missing_reasons or item.full_policy is None:
            raise ValueError("RELEASE_CURRENT_SOURCE_MISSING:" + ";".join(item.missing_reasons))
        config = _full(actual, item.full_policy)
        goal = next(
            row
            for row in actual.base.original_inventory["goals"]
            if row["id"] == str(item.source_goal_id)
        )
        destination = next(
            row
            for row in actual.base.original_inventory["accounts"]
            if row["id"] == str(item.destination_account_id)
        )
        if goal["user_id"] != str(actual.base.user_id) or destination["account_type"] != "CASH":
            raise ValueError("RELEASE_LISTED_OWNED_GOAL_OR_DESTINATION_NOT_PROVEN")
        if unresolved:
            raise ValueError("RELEASE_ORIGINAL_INFLIGHT_RESPONSIBILITY_RETAINED")
        full = item.full_policy
        if full.status in {"SUSPENDED", "REVOKED", "EXPIRED"} or not config.enabled:
            state, reason = "EXCLUDED", "CURRENT_FULL_RELEASE_POLICY_DISABLED"
        elif item.authorization_source_id is None:
            # The full source list was checked above; missing originals never enter this branch.
            relevant = [
                row
                for row in auths.values()
                if row.current_original is not None
                and row.current_original.original_authorization.policy_id == item.full_policy_id
                and row.current_original.current_scope_status == "CURRENT"
            ]
            if relevant:
                raise ValueError("RELEASE_CURRENT_AUTHORIZATION_NOT_SELECTED")
            state, reason = "EXCLUDED", "COMPLETE_NO_CURRENT_DEDICATED_RELEASE_SCOPE"
        else:
            original = auths[item.authorization_source_id]
            authority = _authorization(actual, original)
            current = original.current_original
            candidate, command, repair = item.actual_candidate, item.command, item.repair_inputs
            if current is None or current.current_scope_status != "CURRENT":
                state, reason = "EXCLUDED", "ORIGINAL_DEDICATED_RELEASE_SCOPE_NOT_CURRENT"
            elif candidate is None or repair is None:
                raise ValueError("RELEASE_ACTUAL_CANDIDATE_AND_REPLAY_INPUTS_MISSING")
            else:
                preview = candidate.actual_preview
                if (
                    candidate.user_id != actual.base.user_id
                    or candidate.epoch_id != actual.base.epoch_id
                    or candidate.as_of != actual.base.as_of
                    or preview.policy != full
                    or candidate.original_authorization != current
                    or candidate.original_request.policy_id != item.full_policy_id
                    or candidate.original_request.expected_epoch_id != actual.base.epoch_id
                    or candidate.original_request.expected_policy_version_id
                    != full.current_version.version_id
                    or str(candidate.original_request.expected_goal_policy_version_id)
                    != goal["policy_version_id"]
                    or candidate.original_request.authorization_epoch_id != authority.epoch_id
                    or candidate.original_request.authorization_idempotency_key
                    != authority.idempotency_key
                    or candidate.original_request.source_goal_id != item.source_goal_id
                    or candidate.original_request.destination_account_id
                    != item.destination_account_id
                    or decide_cash_reallocation(repair) != preview.decision
                    or candidate.input_hash
                    != configuration_hash(
                        {
                            "request": candidate.original_request.model_dump(mode="json"),
                            "preview": preview.source_binding_hash,
                            "inventory": candidate.actual_inventory.source_binding_hash,
                            "authorization": current.model_dump(mode="json"),
                            "protection": candidate.protection.model_dump(mode="json")
                            if candidate.protection
                            else None,
                            "reasons": candidate.reasons,
                            "as_of": candidate.as_of.isoformat(),
                        }
                    )
                    or repair.user_id != actual.base.user_id
                    or repair.epoch_id != actual.base.epoch_id
                    or repair.as_of != actual.base.as_of
                ):
                    raise ValueError("RELEASE_ACTUAL_PREVIEW_OR_REPAIR_INPUT_DIFFERS")
                if candidate.state == "UNKNOWN":
                    raise ValueError(
                        "RELEASE_ACTUAL_FINANCIAL_PREVIEW_UNKNOWN:" + ";".join(candidate.reasons)
                    )
                if candidate.state == "BLOCKED":
                    # A service refusal can also contain missing financial originals.
                    # Its label alone is not a replayed proof of an empty action set.
                    raise ValueError(
                        "RELEASE_SERVICE_REFUSAL_NOT_INDEPENDENTLY_REPLAYED:"
                        + ";".join(candidate.reasons)
                    )
                else:
                    if command is None:
                        raise ValueError("RELEASE_EXACT_READONLY_COMMAND_MISSING")
                    _inventory(actual, item)
                    _protection(actual, item)
                    _repair_source_binding(actual, item)
                    effect = command.effect
                    validate_release_authorization_binding(command, authority, actual.base.as_of)
                    if (
                        candidate.reasons
                        or effect.amount_cents != preview.decision.math.minimum_repair_cents
                        or effect.amount_cents != candidate.amount_cents
                        or effect.release_uses != candidate.selected_release_uses
                        or effect.source_goal_id != item.source_goal_id
                        or effect.destination_account_id != item.destination_account_id
                        or effect.financial_input_hash != candidate.input_hash
                        or effect.source_provenance_hash
                        != candidate.actual_inventory.original_basis.source_binding_hash
                        or candidate.actual_inventory.policy_usage.cap_occupied_cents is None
                        or preview.decision.math.source_cash_releasable_above_minimum_cents is None
                        or effect.amount_cents
                        > preview.decision.math.source_cash_releasable_above_minimum_cents
                        or not preview.decision.triggered_conditions
                        or not set(preview.decision.triggered_conditions)
                        <= set(authority.scope.emergency_conditions)
                        or effect.amount_cents
                        + candidate.actual_inventory.policy_usage.cap_occupied_cents
                        > authority.scope.total_cap_cents
                    ):
                        raise ValueError("RELEASE_EXACT_ORIGINAL_ECONOMICS_DIFFER")
                    state, amount = "INCLUDED", effect.amount_cents
                    signature = configuration_hash(
                        {
                            "action_type": "RELEASE_GOAL",
                            "autonomy_level": "ASK_ONCE",
                            "amount_cents": amount,
                            "source_goal_id": str(effect.source_goal_id),
                            "source_account_id": str(effect.source_account_id),
                            "destination_account_id": str(effect.destination_account_id),
                            "destination_scope": effect.destination_scope,
                            "minimum_guarantee_cents": effect.minimum_guarantee_cents,
                            "emergency_conditions": effect.emergency_conditions,
                            "release_uses": [
                                row.model_dump(mode="json") for row in effect.release_uses
                            ],
                            "fee_cents": 0,
                            "loss_cents": 0,
                            "principal_change_cents": 0,
                            "other_goal_change_cents": 0,
                            "available_income_increase_cents": 0,
                            "assigned_income_decrease_cents": 0,
                            "settlement_delay_days": 0,
                        }
                    )
    except (ValueError, TypeError, KeyError, StopIteration, OverflowError) as error:
        state, reason, amount, signature = "UNKNOWN", str(error), None, None
    return ReleaseProducerResult(
        candidate_key=key,
        full_policy_id=item.full_policy_id,
        source_goal_id=item.source_goal_id,
        destination_account_id=item.destination_account_id,
        view=CandidateView(
            candidate_key=key,
            state=state,
            action_type="RELEASE_GOAL" if state == "INCLUDED" else None,
            amount_cents=amount,
            autonomy_level="ASK_ONCE" if state == "INCLUDED" else None,
            signature=signature,
            reasons=[reason] if reason else [],
        ),
        unresolved_original_action_ids=unresolved,
    )


def derive_release_producers(supplied: ReleaseActionSetInput) -> ReleaseActionSetResult:
    data = ReleaseActionSetInput.model_validate(supplied.model_dump())
    actual = data.original_actual_input
    original_actual = derive_actual_action_set(actual)
    reasons = [*data.source_reasons, *_structure(actual)]
    unresolved: list[UUID] = []
    expected: list[tuple[UUID, UUID, UUID]] = []
    results: list[ReleaseProducerResult] = []
    try:
        expected = release_producer_keys(actual)
        if (
            data.expected_full_policy_ids != release_policy_ids(actual)
            or data.original_authorization_source_ids != release_authorization_ids(actual)
            or data.original_action_ids != release_action_ids(actual)
            or len(data.authorizations) != len(data.original_authorization_source_ids)
            or {row.source_id for row in data.authorizations}
            != set(data.original_authorization_source_ids)
            or len(data.original_commands) != len(data.original_action_ids)
            or {row.action_id for row in data.original_commands} != set(data.original_action_ids)
            or len(data.producers) != len(expected)
            or {
                (row.full_policy_id, row.source_goal_id, row.destination_account_id)
                for row in data.producers
            }
            != set(expected)
        ):
            raise ValueError("RELEASE_COMPLETE_ORIGINAL_SOURCE_OR_CANDIDATE_DENOMINATOR_DIFFERS")
        for authorization in data.authorizations:
            _authorization(actual, authorization)
        for original in data.original_commands:
            try:
                if _history(actual, original):
                    unresolved.append(original.action_id)
            except (ValueError, TypeError, KeyError, StopIteration):
                unresolved.append(original.action_id)
        for bank in actual.base.original_inventory["bank_operations"]:
            if (
                bank["operation_type"] == "RELEASE_GOAL"
                or bank["request"].get("protocol") == "full-goal-release-bank-v1"
            ) and UUID(bank["action_plan_id"]) not in data.original_action_ids:
                raise ValueError("RELEASE_ORIGINAL_BANK_WITHOUT_CAPTURED_ACTION")
        if unresolved:
            reasons.append("RELEASE_ORIGINAL_INFLIGHT_OR_UNVERIFIED_HISTORY_RETAINED")
        auths = {row.source_id: row for row in data.authorizations}
        results = [_one(actual, row, auths, sorted(unresolved, key=str)) for row in data.producers]
    except (ValueError, TypeError, KeyError, StopIteration, OverflowError) as error:
        reasons.append("RELEASE_COMPLETE_ORIGINAL_SOURCE_NOT_PROVEN:" + str(error))
    if any(row.view.state == "UNKNOWN" for row in results):
        reasons.append("RELEASE_CURRENT_PRODUCER_UNKNOWN")
    if len(expected) > MAX_PRODUCERS or len(data.model_dump_json().encode()) > MAX_BYTES:
        reasons.append("RELEASE_CAPTURE_CAPACITY_EXCEEDED")
    complete = not reasons
    handled = (
        [
            "FULL_PRODUCER_ADAPTER_MISSING:CrossGoalReallocationPolicy:" + str(identity)
            for identity in data.expected_full_policy_ids
        ]
        if complete
        else []
    )
    result = ReleaseActionSetResult(
        user_id=actual.base.user_id,
        epoch_id=actual.base.epoch_id,
        as_of=actual.base.as_of,
        status="COMPLETE_REGISTERED_RELEASE_FAMILY" if complete else "UNKNOWN",
        release_family_complete=complete,
        original_actual_input_hash=configuration_hash(actual.model_dump(mode="json")),
        input_hash=configuration_hash(data.model_dump(mode="json")),
        result_hash="0" * 64,
        expected_full_policy_ids=data.expected_full_policy_ids,
        original_authorization_source_ids=data.original_authorization_source_ids,
        original_action_ids=data.original_action_ids,
        unresolved_original_action_ids=sorted(unresolved, key=str),
        expected_candidate_keys=[release_key(*row) for row in expected],
        results=results,
        handled_unsupported_codes=handled,
        original_actual_reasons=original_actual.reasons,
        remaining_unsupported_producers=sorted(
            set(original_actual.unsupported_producers) - set(handled)
        ),
        reasons=sorted(set(reasons)),
    )
    return result.model_copy(
        update={
            "result_hash": configuration_hash(
                result.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )
