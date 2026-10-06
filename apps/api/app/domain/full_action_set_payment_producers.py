"""Finite current periodic-payment producers; no permission or financial writes."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_boundary import (
    CandidateInput,
    CandidateView,
    Hash,
    _original_candidate_binding,
    candidate_view,
)
from app.domain.full_action_set_boundary_actual import (
    ActualActionSetInput,
    derive_actual_action_set,
)
from app.domain.full_payment_permissions import (
    SOURCE,
    account_payment_identity,
    validate_payment_bridge,
    verify_original_payment_references,
    verify_payment_effect,
)
from app.domain.full_policy_configuration import PeriodicTransferPolicy
from app.domain.policy_configuration import (
    RecurringObligation,
    configuration_hash,
)
from app.services.full_payment_permissions import (
    PaymentCommandOriginal,
    PaymentCommandReceipt,
    PeriodicTransferProjectionBinding,
)
from app.services.full_policy_lifecycle import FullPolicyView
from pydantic import Field, StrictInt

ALGORITHM: Literal["full-policy-periodic-action-producers-v1"] = (
    "full-policy-periodic-action-producers-v1"
)
MAX_PRODUCERS = 64
MAX_BYTES = 16 * 1024 * 1024


class PeriodicOriginalCommand(BoundaryModel):
    receipt: PaymentCommandReceipt
    trace: DecisionTrace


class PeriodicPaymentProducerInput(BoundaryModel):
    candidate_key: str
    full_policy_id: UUID
    full_policy: FullPolicyView | None = None
    relation_binding: PeriodicTransferProjectionBinding | None = None
    # Complete current-version CONFIRM rows and their original START ancestry.
    confirmation_evidence_ids: list[UUID] = Field(default_factory=list)
    commands: list[PeriodicOriginalCommand] = Field(default_factory=list)
    candidate: CandidateInput | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class PeriodicActionSetInput(BoundaryModel):
    protocol: Literal["periodic-action-set-input-v1"] = "periodic-action-set-input-v1"
    original_actual_input: ActualActionSetInput
    expected_full_policy_ids: list[UUID]
    relation_source_count: StrictInt = Field(ge=0)
    # Includes malformed, inactive and historical SOURCE rows, not just current confirmations.
    relation_source_ids: list[UUID]
    producers: list[PeriodicPaymentProducerInput]
    source_reasons: list[str] = Field(default_factory=list)


class PeriodicProducerResult(BoundaryModel):
    candidate_key: str
    full_policy_id: UUID
    view: CandidateView
    shadow_original_candidate_key: str | None
    current_confirmation_evidence_ids: list[UUID]
    original_command_ids: list[UUID]
    unresolved_original_action_ids: list[UUID]


class PeriodicActionSetResult(BoundaryModel):
    algorithm_version: Literal["full-policy-periodic-action-producers-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    full_global_adapter_installed: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE_REGISTERED_PERIODIC_FAMILY", "UNKNOWN"]
    periodic_family_complete: bool
    original_actual_input_hash: Hash
    input_hash: Hash
    result_hash: Hash
    expected_full_policy_ids: list[UUID]
    relation_source_count: StrictInt
    relation_source_ids: list[UUID]
    results: list[PeriodicProducerResult]
    handled_unsupported_codes: list[str]
    # Other actual-v2 families and original UNKNOWN candidates are not silently removed.
    original_actual_reasons: list[str]
    remaining_unsupported_producers: list[str]
    reasons: list[str]


def periodic_policy_ids(data: ActualActionSetInput) -> list[UUID]:
    return sorted(
        (
            UUID(row["id"])
            for row in data.base.original_inventory["full_policies"]
            if row["epoch_id"] == str(data.base.epoch_id)
            and row["template_name"] == "PeriodicTransferPolicy"
        ),
        key=str,
    )


def relation_rows(data: ActualActionSetInput) -> list[dict[str, Any]]:
    return [
        row
        for row in data.base.original_inventory["evidence_items"]
        if row["source_type"] == SOURCE
    ]


def _key(identity: UUID) -> str:
    return "full-periodic:" + str(identity)


def _view(key: str, state: Literal["EXCLUDED", "UNKNOWN"], reason: str) -> CandidateView:
    return CandidateView(
        candidate_key=key,
        state=state,
        action_type=None,
        amount_cents=None,
        autonomy_level=None,
        signature=None,
        reasons=[reason],
    )


def _clock(value: Any) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Original clock must be aware")
    return parsed


def _full_original(data: ActualActionSetInput, full: FullPolicyView) -> PeriodicTransferPolicy:
    base = data.base
    raw = next(
        row for row in base.original_inventory["full_policies"] if row["id"] == str(full.policy_id)
    )
    version = next(
        row
        for row in base.original_inventory["full_policy_versions"]
        if row["id"] == str(full.current_version.version_id)
    )
    if (
        full.epoch_id != base.epoch_id
        or full.template_name != "PeriodicTransferPolicy"
        or raw["user_id"] != str(base.user_id)
        or raw["epoch_id"] != str(base.epoch_id)
        or raw["status"] != full.status
        or full.current_version.version_number
        != max(
            row["version_number"]
            for row in base.original_inventory["full_policy_versions"]
            if row["policy_id"] == str(full.policy_id)
        )
        or version["policy_id"] != str(full.policy_id)
        or version["user_id"] != str(base.user_id)
        or version["configuration"] != full.current_version.configuration
        or version["content_hash"] != full.current_version.content_hash
        or configuration_hash(version["configuration"]) != version["content_hash"]
        or version["confirmation"] != full.current_version.confirmation
        or version["impact_analysis"] != full.current_version.impact_analysis
        or version["previous_hash"] != full.current_version.previous_hash
        or version["evidence_ids"] != [str(x) for x in full.current_version.evidence_ids]
        or _clock(version["confirmed_at"]) != full.current_version.confirmed_at
        or _clock(version["valid_from"]) != full.current_version.valid_from
        or (None if version["valid_until"] is None else _clock(version["valid_until"]))
        != full.current_version.valid_until
    ):
        raise ValueError("Original FULL identity/version/configuration differs")
    if (
        full.current_version.confirmation.get("accepted") is not True
        or full.current_version.confirmation.get("reviewed_hash")
        != full.current_version.content_hash
    ):
        raise ValueError("Original exact FULL planning confirmation differs")
    return PeriodicTransferPolicy.model_validate_json(json.dumps(version["configuration"]))


def confirmation_ids(data: ActualActionSetInput, full: FullPolicyView) -> list[UUID]:
    values: list[UUID] = []
    for raw in relation_rows(data):
        content = raw["content"]
        if not isinstance(content, dict):
            raise ValueError("PERIODIC_ORIGINAL_RELATION_CONTENT_NOT_OBJECT")
        scope = content.get("scope", {})
        if not isinstance(scope, dict):
            raise ValueError("PERIODIC_ORIGINAL_RELATION_SCOPE_NOT_OBJECT")
        if (
            content.get("kind") == "CONFIRM"
            and scope.get("full_policy_id") == str(full.policy_id)
            and scope.get("full_version_id") == str(full.current_version.version_id)
        ):
            values.append(UUID(raw["id"]))
    return sorted(values, key=str)


def _command(data: ActualActionSetInput, item: PeriodicOriginalCommand) -> None:
    receipt, trace = item.receipt, item.trace
    original = receipt.original
    verify_trace(trace)
    raw = next(row for row in relation_rows(data) if row["id"] == str(receipt.evidence_id))
    if (
        original.user_id != data.base.user_id
        or original.epoch_id != data.base.epoch_id
        or receipt.evidence_hash != raw["content_hash"]
        or receipt.evidence_id != uuid5(original.command_id, "evidence")
        or raw["content_hash"] != configuration_hash(raw["content"])
        or raw["content"] != original.model_dump(mode="json")
        or raw["status"] != "VALID"
        or raw["source_ref"] != str(original.command_id)
        or raw["evidence_level"]
        != ("USER_DECLARED" if original.kind == "START" else "USER_CONFIRMED_POLICY")
        or _clock(raw["observed_at"]) != original.recorded_at
        or _clock(raw["valid_from"]) != original.recorded_at
        or _clock(raw["valid_to"]) != original.scope.valid_until
        or trace.user_id != data.base.user_id
        or trace.run_id != original.command_id
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
        or trace.as_of != original.recorded_at
        or trace.trace_hash != receipt.trace_hash
        or trace.parent_run_id != original.start_command_id
        or trace.algorithm_versions.get("full_payment_relation") != original.protocol
        or trace.inputs
        != {
            "kind": original.kind,
            "original_request": original.original_request,
            "request_hash": original.request_hash,
        }
        or trace.outcome != {"payment_relation_command": original.model_dump(mode="json")}
        or len(trace.policies) != 1
        or trace.policies[0].id != original.scope.original_version_id
        or trace.policies[0].policy_id != original.scope.original_policy_id
        or trace.policies[0].user_id != data.base.user_id
        or trace.policies[0].configuration_integrity != "VERIFIED"
        or configuration_hash(trace.policies[0].configuration)
        != original.scope.original_configuration_hash
        or trace.policies[0].configuration_hash != original.scope.original_configuration_hash
    ):
        raise ValueError("Original dedicated command/evidence/trace differs")
    inventory = {row["id"]: row for row in data.base.original_inventory["evidence_items"]}
    for source in trace.sources:
        current = inventory.get(str(source.id))
        if (
            current is None
            or source.user_id != data.base.user_id
            or source.content_integrity != "VERIFIED"
            or source.status_at_decision != "VALID"
            or source.content_hash != configuration_hash(source.content)
            or current["content_hash"] != source.content_hash
            or current["content"] != source.content
        ):
            raise ValueError("Complete original dedicated source copy differs")
    if not any(source.id == receipt.evidence_id for source in trace.sources):
        raise ValueError("Dedicated confirmation is missing from its original trace")


def _unresolved(data: ActualActionSetInput, identity: UUID, policy_ids: set[UUID]) -> list[UUID]:
    inventory = data.base.original_inventory
    action_ids: set[UUID] = set()
    for raw in inventory["evidence_items"]:
        content = raw["content"]
        if raw["source_type"] == "FULL_PAYMENT_ACTION_BINDING" and content.get("scope", {}).get(
            "full_policy_id"
        ) == str(identity):
            action_ids.add(UUID(content["action_id"]))
    action_ids.update(
        UUID(row["id"])
        for row in inventory["action_plans"]
        if row.get("policy_id") in {str(x) for x in policy_ids}
    )
    unresolved: set[UUID] = set()
    for action in inventory["action_plans"]:
        action_id = UUID(action["id"])
        if action_id not in action_ids:
            continue
        operations = [
            row for row in inventory["bank_operations"] if row["action_plan_id"] == action["id"]
        ]
        claims = [
            row
            for row in inventory["action_resource_reservations"]
            if row["action_plan_id"] == action["id"] and row["status"] == "RESERVED"
        ]
        if (
            action["status"] in {"SUBMITTED", "UNKNOWN"}
            or claims
            or any(row["status"] not in {"SETTLED", "REJECTED"} for row in operations)
        ):
            unresolved.add(action_id)
        if any(row["status"] == "SETTLED" for row in operations) and not any(
            row["action_plan_id"] == action["id"] for row in inventory["action_receipts"]
        ):
            unresolved.add(action_id)
    return sorted(unresolved, key=str)


def _current_scope_status(
    data: ActualActionSetInput, full: FullPolicyView, item: PeriodicOriginalCommand
) -> str:
    scope, now = item.receipt.original.scope, data.base.as_of
    if full.effective_status != "ACTIVE" or not scope.valid_from <= now < scope.valid_until:
        return "STALE"
    inventory = data.base.original_inventory
    policy = next(
        row for row in inventory["policies"] if row["id"] == str(scope.original_policy_id)
    )
    versions = [
        row
        for row in inventory["policy_versions"]
        if row["policy_id"] == str(scope.original_policy_id)
    ]
    latest = max(versions, key=lambda row: row["version_number"])
    if policy["status"] != "ACTIVE" or latest["id"] != str(scope.original_version_id):
        return "STALE"
    if latest.get("valid_from") is None or latest.get("confirmed_at") is None:
        raise ValueError("PERIODIC_ORIGINAL_CURRENT_CONFIRMATION_WINDOW_MISSING")
    if (
        now < _clock(latest["valid_from"])
        or now < _clock(latest["confirmed_at"])
        or latest.get("valid_until") is not None
        and now >= _clock(latest["valid_until"])
    ):
        return "STALE"
    if (
        scope.full_version_id != full.current_version.version_id
        or scope.full_configuration_hash != full.current_version.content_hash
        or latest["content_hash"] != scope.original_configuration_hash
    ):
        raise ValueError("PERIODIC_CURRENT_SCOPE_CONFIGURATION_DIFFERS")
    return "CURRENT"


def _derive_one(
    data: ActualActionSetInput, item: PeriodicPaymentProducerInput
) -> PeriodicProducerResult:
    key = _key(item.full_policy_id)
    ids, commands, unresolved = item.confirmation_evidence_ids, item.commands, []
    shadow: str | None = None
    try:
        if item.candidate_key != key or item.missing_reasons or item.full_policy is None:
            raise ValueError("PERIODIC_CURRENT_ORIGINALS_MISSING:" + ";".join(item.missing_reasons))
        full = item.full_policy
        config = _full_original(data, full)
        if confirmation_ids(data, full) != sorted(ids, key=str) or len(ids) != len(set(ids)):
            raise ValueError("PERIODIC_CURRENT_CONFIRMATION_DENOMINATOR_DIFFERS")
        confirm = [row for row in commands if row.receipt.original.kind == "CONFIRM"]
        if {row.receipt.evidence_id for row in confirm} != set(ids):
            raise ValueError("PERIODIC_COMPLETE_ORIGINAL_COMMANDS_MISSING")
        if len({row.receipt.original.command_id for row in commands}) != len(commands):
            raise ValueError("PERIODIC_DUPLICATE_ORIGINAL_COMMAND")
        by_id = {row.receipt.original.command_id: row for row in commands}
        for row in commands:
            _command(data, row)
        starts = {row.receipt.original.start_command_id for row in confirm}
        if set(by_id) != {row.receipt.original.command_id for row in confirm} | starts:
            raise ValueError("PERIODIC_START_ANCESTRY_DENOMINATOR_DIFFERS")
        for row in confirm:
            start_id = row.receipt.original.start_command_id
            if start_id is None:
                raise ValueError("PERIODIC_ORIGINAL_START_MISSING")
            start = by_id[start_id]
            if (
                start.receipt.original.kind != "START"
                or start.receipt.original.scope != row.receipt.original.scope
                or start.receipt.original.recorded_at > row.receipt.original.recorded_at
            ):
                raise ValueError("PERIODIC_ORIGINAL_START_DIFFERS")
        policy_ids = {row.receipt.original.scope.original_policy_id for row in confirm}
        unresolved = _unresolved(data, item.full_policy_id, policy_ids)
        if unresolved:
            raise ValueError("PERIODIC_ORIGINAL_INFLIGHT_RESPONSIBILITY_RETAINED")
        if full.effective_status != "ACTIVE":
            view = _view(key, "EXCLUDED", "CURRENT_FULL_PERIODIC_POLICY_NOT_ACTIVE")
        else:
            if not full.planning_confirmation_valid or item.relation_binding is None:
                raise ValueError("PERIODIC_CURRENT_PLANNING_OR_RELATION_PROOF_MISSING")
            binding = item.relation_binding
            if (
                (binding.full_policy_id, binding.full_version_id)
                != (full.policy_id, full.current_version.version_id)
                or binding.issues
                or binding.status == "UNKNOWN"
            ):
                raise ValueError("PERIODIC_RELATION_SOURCES_UNKNOWN")
            current = [row for row in confirm if row.receipt.current_scope_status == "CURRENT"]
            if any(row.receipt.current_scope_status == "UNKNOWN" for row in confirm):
                raise ValueError("PERIODIC_ORIGINAL_CURRENT_SCOPE_UNKNOWN")
            if any(
                row.receipt.current_scope_status != _current_scope_status(data, full, row)
                for row in confirm
            ):
                raise ValueError("PERIODIC_CURRENT_STATUS_NOT_REPRODUCIBLE")
            if not current:
                if binding.status != "NO_CURRENT_DEDICATED_RELATION":
                    raise ValueError("PERIODIC_EMPTY_RELATION_BINDING_DIFFERS")
                view = _view(key, "EXCLUDED", "NO_CURRENT_DEDICATED_RELATION")
            else:
                if len({row.receipt.original.scope.original_policy_id for row in current}) != 1:
                    raise ValueError("PERIODIC_MULTIPLE_ORIGINAL_POLICY_MAPPINGS")
                selected = next(
                    row
                    for row in current
                    if row.receipt.original.command_id == binding.relation_command_id
                )
                scope = selected.receipt.original.scope
                if any(row.receipt.original.scope != scope for row in current) or (
                    scope.amount_rule.model_dump(mode="json")
                    != config.amount_rule.model_dump(mode="json")
                    or scope.payee_id != config.payee_id
                    or scope.source_account_id != config.source_account_id
                    or scope.due_day != config.due_day
                    or scope.single_action_cap_cents != config.single_action_cap_cents
                    or scope.auto_execute != config.auto_execute
                    or scope.timezone != data.base.financial_basis["snapshot"]["timezone"]
                ):
                    raise ValueError("PERIODIC_ORIGINAL_SCOPE_NOT_EXACT_FULL_CONFIGURATION")
                if (
                    binding.status != "VERIFIED_CURRENT_RELATION"
                    or binding.relation_evidence_id != selected.receipt.evidence_id
                    or binding.relation_evidence_hash != selected.receipt.evidence_hash
                    or binding.scope_hash != selected.receipt.original.scope_hash
                    or scope.full_configuration_hash != full.current_version.content_hash
                    or not scope.valid_from <= data.base.as_of < scope.valid_until
                ):
                    raise ValueError("PERIODIC_EXACT_CURRENT_RELATION_DIFFERS")
                if (
                    len(binding.source_evidence_ids) != len(set(binding.source_evidence_ids))
                    or set(binding.source_evidence_hashes)
                    != {str(x) for x in binding.source_evidence_ids}
                    or selected.receipt.evidence_id not in binding.source_evidence_ids
                    or scope.payee_evidence_id not in binding.source_evidence_ids
                ):
                    raise ValueError("PERIODIC_RELATION_CURRENT_SOURCE_DENOMINATOR_DIFFERS")
                raw_evidence = {
                    row["id"]: row for row in data.base.original_inventory["evidence_items"]
                }
                for evidence_id in binding.source_evidence_ids:
                    proof = raw_evidence[str(evidence_id)]
                    if (
                        proof["status"] != "VALID"
                        or proof["content_hash"] != binding.source_evidence_hashes[str(evidence_id)]
                        or configuration_hash(proof["content"]) != proof["content_hash"]
                    ):
                        raise ValueError("PERIODIC_CURRENT_RELATION_SOURCE_NOT_VERIFIED")
                account = next(
                    row
                    for row in data.base.original_inventory["accounts"]
                    if row["id"] == str(scope.source_account_id)
                )
                original = next(
                    row
                    for row in data.base.original_inventory["policy_versions"]
                    if row["id"] == str(scope.original_version_id)
                )
                old = RecurringObligation.model_validate_json(json.dumps(original["configuration"]))
                validate_payment_bridge(config, old)
                scope_starts = [
                    full.current_version.valid_from,
                    full.current_version.confirmed_at,
                    _clock(original["valid_from"]),
                    _clock(original["confirmed_at"]),
                ]
                ends = [full.current_version.valid_until]
                if original.get("valid_until") is not None:
                    ends.append(_clock(original["valid_until"]))
                if (
                    full.current_version.valid_until is None
                    or scope.valid_from != max(scope_starts)
                    or scope.valid_until != min(end for end in ends if end is not None)
                ):
                    raise ValueError("PERIODIC_CURRENT_SCOPE_WINDOW_NOT_REPRODUCIBLE")
                if (
                    configuration_hash(account_payment_identity(account))
                    != scope.source_account_identity_hash
                    or original["content_hash"] != scope.original_configuration_hash
                    or original["policy_id"] != str(scope.original_policy_id)
                ):
                    raise ValueError("PERIODIC_CURRENT_ACCOUNT_OR_ORIGINAL_VERSION_DIFFERS")
                payee = next(
                    row
                    for row in data.base.original_inventory["evidence_items"]
                    if row["id"] == str(scope.payee_evidence_id)
                )
                verify_original_payment_references(
                    full.current_version.impact_analysis["reference_snapshots"],
                    account,
                    payee,
                    scope.payee_id,
                )
                if (
                    payee["status"] != "VALID"
                    or payee["evidence_level"] != "BANK_CONFIRMED"
                    or payee["content_hash"] != scope.payee_evidence_hash
                    or configuration_hash(payee["content"]) != scope.payee_evidence_hash
                ):
                    raise ValueError("PERIODIC_ORIGINAL_PAYEE_PROOF_DIFFERS")
                candidate = item.candidate
                if (
                    candidate is None
                    or candidate.facts is None
                    or candidate.facts.effect is None
                    or candidate.execution_context is None
                    or not _original_candidate_binding(data.base, candidate)
                ):
                    raise ValueError("PERIODIC_COMPLETE_CURRENT_CANDIDATE_MISSING")
                protected = [
                    source
                    for source in candidate.full_sources
                    if source.policy_id == full.policy_id
                ]
                if (
                    len(protected) != 1
                    or protected[0].version_id != full.current_version.version_id
                    or protected[0].content_hash != full.current_version.content_hash
                    or candidate.full_protection is None
                ):
                    raise ValueError("PERIODIC_CURRENT_FULL_PROTECTION_DENOMINATOR_MISSING")
                effect = candidate.facts.effect
                period = data.base.as_of.astimezone(ZoneInfo(scope.timezone)).strftime("%Y-%m")
                if (
                    effect.liability is None
                    or getattr(effect.liability, "period", None) != period
                    or candidate.facts.confirmation is not None
                ):
                    raise ValueError("PERIODIC_CURRENT_PERIOD_OR_NEW_ACTION_CONFIRMATION_DIFFERS")
                view = candidate_view(candidate, data.base.user_id, data.base.as_of).model_copy(
                    update={"candidate_key": key}
                )
                if view.state != "UNKNOWN":
                    try:
                        verify_payment_effect(
                            scope,
                            effect,
                            data.base.as_of,
                            verified_payee_evidence_ids={effect.payee_evidence_id}
                            if effect.payee_evidence_id
                            else set(),
                        )
                    except ValueError:
                        view = _view(
                            key,
                            "EXCLUDED",
                            "CURRENT_COMPLETE_EFFECT_OUTSIDE_DEDICATED_PAYMENT_SCOPE",
                        )
                if (
                    view.state == "INCLUDED"
                    and not scope.auto_execute
                    and view.autonomy_level != "ASK_ONCE"
                ):
                    raise ValueError("PERIODIC_ASK_REQUIRES_NEW_EXACT_USER_ACTION_CONFIRMATION")
                if view.state != "UNKNOWN":
                    shadow = candidate.candidate_key
    except (ValueError, TypeError, KeyError, StopIteration) as error:
        view = _view(key, "UNKNOWN", str(error))
    return PeriodicProducerResult(
        candidate_key=key,
        full_policy_id=item.full_policy_id,
        view=view,
        shadow_original_candidate_key=shadow,
        current_confirmation_evidence_ids=ids,
        original_command_ids=sorted((row.receipt.original.command_id for row in commands), key=str),
        unresolved_original_action_ids=unresolved,
    )


def derive_periodic_payment_producers(supplied: PeriodicActionSetInput) -> PeriodicActionSetResult:
    data = PeriodicActionSetInput.model_validate(supplied.model_dump())
    original = derive_actual_action_set(data.original_actual_input)
    reasons = list(data.source_reasons)
    try:
        expected = periodic_policy_ids(data.original_actual_input)
        sources = sorted(
            (UUID(row["id"]) for row in relation_rows(data.original_actual_input)), key=str
        )
        for raw in relation_rows(data.original_actual_input):
            parsed = PaymentCommandOriginal.model_validate_json(json.dumps(raw["content"]))
            if (
                parsed.user_id != data.original_actual_input.base.user_id
                or configuration_hash(raw["content"]) != raw["content_hash"]
            ):
                raise ValueError("PERIODIC_COMPLETE_RELATION_ROW_INVALID")
        if (
            expected != data.expected_full_policy_ids
            or {row.full_policy_id for row in data.producers} != set(expected)
            or len(data.producers) != len(expected)
        ):
            reasons.append("PERIODIC_FULL_POLICY_DENOMINATOR_DIFFERS")
        if (
            sources != data.relation_source_ids
            or data.relation_source_count != len(sources)
            or len(sources) != len(set(sources))
        ):
            reasons.append("PERIODIC_ALL_RELATION_SOURCE_DENOMINATOR_DIFFERS")
        results = [_derive_one(data.original_actual_input, row) for row in data.producers]
    except (ValueError, TypeError, KeyError) as error:
        expected, results = data.expected_full_policy_ids, []
        reasons.append("PERIODIC_ORIGINAL_INVENTORY_INVALID:" + str(error))
    # Exact original source/audit/count gates remain, even if another family is unsupported.
    reasons.extend(
        reason
        for reason in original.reasons
        if reason not in {"ACTUAL_UNSUPPORTED_CURRENT_PRODUCERS", "ACTUAL_CURRENT_PRODUCER_UNKNOWN"}
    )
    if any(row.view.state == "UNKNOWN" for row in results):
        reasons.append("PERIODIC_CURRENT_PRODUCER_UNKNOWN")
    shadow_keys = [
        row.shadow_original_candidate_key
        for row in results
        if row.shadow_original_candidate_key is not None
    ]
    if len(shadow_keys) != len(set(shadow_keys)):
        reasons.append("PERIODIC_SHADOW_MAPPING_NOT_UNIQUE")
    if len(expected) > MAX_PRODUCERS or len(data.model_dump_json().encode("utf8")) > MAX_BYTES:
        reasons.append("PERIODIC_CAPTURE_CAPACITY_EXCEEDED")
    complete = not reasons
    handled = sorted(
        "FULL_PRODUCER_ADAPTER_MISSING:PeriodicTransferPolicy:" + str(row.full_policy_id)
        for row in results
        if complete and row.view.state != "UNKNOWN"
    )
    value = PeriodicActionSetResult(
        user_id=data.original_actual_input.base.user_id,
        epoch_id=data.original_actual_input.base.epoch_id,
        as_of=data.original_actual_input.base.as_of,
        status="COMPLETE_REGISTERED_PERIODIC_FAMILY" if complete else "UNKNOWN",
        periodic_family_complete=complete,
        original_actual_input_hash=original.input_hash,
        input_hash=configuration_hash(data.model_dump(mode="json")),
        result_hash="0" * 64,
        expected_full_policy_ids=expected,
        relation_source_count=data.relation_source_count,
        relation_source_ids=data.relation_source_ids,
        results=results,
        handled_unsupported_codes=handled,
        original_actual_reasons=original.reasons,
        remaining_unsupported_producers=sorted(set(original.unsupported_producers) - set(handled)),
        reasons=sorted(set(reasons)),
    )
    return value.model_copy(
        update={
            "result_hash": configuration_hash(
                value.model_dump(mode="json", exclude={"result_hash"})
            )
        }
    )
