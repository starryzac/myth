"""Current dedicated goal release family in one clean read-only transaction."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

from app.db.models import User
from app.domain.decision_trace import verify_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_release_producers import (
    ALGORITHM,
    MAX_PRODUCERS,
    ReleaseActionSetInput,
    ReleaseActionSetResult,
    ReleaseAuthorizationOriginal,
    ReleaseOriginalCommand,
    ReleaseProducerInput,
    derive_release_producers,
    release_action_ids,
    release_authorization_ids,
    release_policy_ids,
    release_producer_keys,
)
from app.domain.full_goal_reallocation import (
    CONDITIONS,
    ReallocationDecisionInput,
    RepairFinancialFacts,
    RepairGoalFacts,
    RepairPolicyFacts,
    RepairUsageFacts,
)
from app.domain.full_goal_release_authorization import GoalReleaseAuthorization
from app.domain.full_policy_configuration import CrossGoalReallocationPolicy, LongTermGoalPolicy
from app.domain.full_protection_projection import FullProtectionProjectionInput
from app.services.audit_chain import current_audit_epoch
from app.services.autonomy_envelope import _snapshot
from app.services.decision_recording import (
    CAPTURE_KEY,
    DecisionCapture,
    capture_evidence,
    start_capture,
)
from app.services.decision_trace import get_decision_trace
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_action_set_boundary import _verify_original_source_copies
from app.services.full_action_set_boundary_actual import (
    ActualActionSetCapture,
    capture_actual_action_set,
)
from app.services.full_goal_release_authorization import read_release_authorization
from app.services.full_goal_release_execution import (
    GoalReleaseCandidate,
    GoalReleasePrepareRequest,
    _effect,
    preview_goal_release_execution,
    release_action_identity,
)
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_protection_projection import (
    FullAnnualProtectionResponse,
    compute_full_annual_protection,
)
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ReleaseActionSetCapture:
    inputs: ReleaseActionSetInput
    result: ReleaseActionSetResult
    originals: DecisionCapture


def _repair_inputs(
    original: ActualActionSetCapture,
    candidate: GoalReleaseCandidate,
    full: FullAnnualProtectionResponse,
) -> ReallocationDecisionInput:
    """Rebuild the original planning-only input; its exact digest must match later."""
    actual, preview = original.inputs.base, candidate.actual_preview
    math = preview.decision.math
    inventory = actual.original_inventory
    goal = next(
        row
        for row in inventory["goals"]
        if row["id"] == str(candidate.original_request.source_goal_id)
    )
    model, ownership, point = (
        preview.goal_model,
        preview.goal_ownership,
        preview.original_current_protection_point,
    )
    if model is None or model.full_configuration is None or ownership is None or point is None:
        raise ValueError("RELEASE_COMPLETE_ACTUAL_REPAIR_GOAL_OR_POINT_MISSING")
    config = LongTermGoalPolicy.model_validate_json(json.dumps(model.full_configuration))
    reservations = [
        row for row in inventory["action_resource_reservations"] if row["status"] == "RESERVED"
    ]
    current = full.projection.full_annual_projection
    points = (
        [row for row in current.calculation_trace if row.day == 0 and row.phase == "BEFORE_PAYMENT"]
        if current
        else []
    )
    if len(points) != 1 or math.cash_cents is None or math.locked_goal_cash_cents is None:
        raise ValueError("RELEASE_COMPLETE_ACTUAL_REPAIR_FINANCIAL_INPUT_MISSING")
    other = sum(
        value
        for key, value in points[0].protected_cents_by_reason.items()
        if key not in {"obligations", "living", "emergency", "goal_cash", "goal_minimum"}
    )
    financial_issues = [
        row
        for row in preview.source_issues
        if row.entity_type in {"reconciliation", "protection", "reservation", "goal"}
        and row.code != "FULL_GOAL_MINIMUM_MODEL_MISSING"
    ]
    zone = actual.financial_basis["snapshot"]["timezone"]
    if zone not in {"Asia/Shanghai", "UTC"}:
        raise ValueError("RELEASE_ACTUAL_TIMEZONE_NOT_SUPPORTED")
    timezone: Literal["Asia/Shanghai", "UTC"] = "UTC" if zone == "UTC" else "Asia/Shanghai"
    return ReallocationDecisionInput(
        user_id=actual.user_id,
        epoch_id=actual.epoch_id,
        as_of=actual.as_of,
        timezone=timezone,
        policy=RepairPolicyFacts(
            policy_id=preview.policy.policy_id,
            version_id=preview.policy.current_version.version_id,
            epoch_id=actual.epoch_id,
            content_hash=preview.policy.current_version.content_hash,
            configuration=CrossGoalReallocationPolicy.model_validate_json(
                json.dumps(preview.policy.current_version.configuration)
            ),
            current_confirmed=preview.policy.planning_confirmation_valid,
            references_current=preview.policy.reference_validation == "CURRENT",
            effective_status=preview.policy.effective_status,
        ),
        goal=RepairGoalFacts(
            goal_id=UUID(goal["id"]),
            account_id=UUID(goal["account_id"]) if goal["account_id"] else None,
            original_policy_version_id=UUID(goal["policy_version_id"]),
            cash_owned_cents=ownership.cash.bank_cents,
            principal_owned_cents=ownership.principal.bank_cents,
            minimum_guarantee_cents=config.minimum_guarantee_cents,
            reserved_goal_cash_cents=sum(
                row["amount_cents"]
                for row in reservations
                if row["resource_kind"] == "GOAL_CASH" and row["resource_key"] == goal["id"]
            ),
            ownership_verified=ownership.current_ownership_proof_verified
            and ownership.cash.state == ownership.principal.state == "MATCHED",
            model_verified=model.status == "VERIFIED",
        ),
        financial=RepairFinancialFacts(
            cash_cents=math.cash_cents,
            locked_goal_cash_cents=math.locked_goal_cash_cents,
            reserved_cash_cents=sum(
                row["amount_cents"] for row in reservations if row["resource_kind"] == "CASH"
            ),
            required_by_condition={
                condition: point.protected_cents_by_reason.get(name, 0)
                for condition, name in zip(
                    CONDITIONS, ("obligations", "living", "emergency"), strict=True
                )
            },
            other_full_protection_cents=other,
            verified=not financial_issues,
        ),
        usage=RepairUsageFacts(status="MISSING", consumed_cents=None, source_refs=[]),
        source_issues=[row.code for row in preview.source_issues],
    )


def _protection_inputs(
    session: Session, user: UUID, now: datetime
) -> tuple[FullProtectionProjectionInput, FullAnnualProtectionResponse]:
    full = compute_full_annual_protection(session, user, now)
    context, matched, exposures = load_verified_financial_context(session, user, now)
    context, issues, _ = finalize_financial_context(context, full.audit)
    if (
        not matched
        or not full.audit.complete
        or full.audit.status != "VALID"
        or issues
        or full.source_issues
    ):
        raise ValueError("RELEASE_COMPLETE_CURRENT_PROTECTION_SOURCE_NOT_VERIFIED")
    capture_evidence(session, user, sorted(context.sources.used | set(full.source_evidence_ids)))
    return FullProtectionProjectionInput(
        snapshot=context.snapshot,
        boundary_versions=context.versions,
        positions=context.positions,
        boundary_products=context.products,
        policies=full.full_policy_sources,
        reserved_cash_by_account={
            key: value
            for exposure in exposures or []
            for key, value in exposure.reserved_cash_by_account.items()
        },
        full_source_inventory_complete=not full.source_issues
        and full.projection.status != "UNKNOWN",
    ), full


def _candidate(
    session: Session,
    user: UUID,
    now: datetime,
    original: ActualActionSetCapture,
    item: ReleaseProducerInput,
    authorization: ReleaseAuthorizationOriginal,
) -> ReleaseProducerInput:
    full, confirmed = item.full_policy, authorization.current_original
    assert full is not None and confirmed is not None
    original_auth = confirmed.original_authorization
    goal = next(
        row
        for row in original.inputs.base.original_inventory["goals"]
        if row["id"] == str(item.source_goal_id)
    )
    key = "boundary-release:" + str(
        uuid5(
            original.inputs.base.epoch_id,
            f"{item.full_policy_id}:{item.source_goal_id}:{item.destination_account_id}",
        )
    )
    body = GoalReleasePrepareRequest(
        policy_id=item.full_policy_id,
        source_goal_id=item.source_goal_id,
        expected_policy_version_id=full.current_version.version_id,
        expected_goal_policy_version_id=UUID(goal["policy_version_id"]),
        expected_epoch_id=original.inputs.base.epoch_id,
        authorization_epoch_id=original_auth.epoch_id,
        authorization_idempotency_key=original_auth.idempotency_key,
        destination_account_id=item.destination_account_id,
        idempotency_key=key,
    )
    candidate = preview_goal_release_execution(session, user, body, now)
    item = item.model_copy(
        update={"authorization_source_id": authorization.source_id, "actual_candidate": candidate}
    )
    inputs, protection = _protection_inputs(session, user, now)
    repair = _repair_inputs(original, candidate, protection)
    income = read_income_state(session, user, now)
    ids = {authorization.source_id, income.evidence_id, *full.current_version.evidence_ids}
    ids.update(row.full_model_evidence_id for row in original_auth.scope.source_goals)
    capture_evidence(session, user, sorted(ids))
    command = (
        _effect(release_action_identity(user, original.inputs.base.epoch_id, key), candidate)
        if candidate.state == "READY"
        else None
    )
    return item.model_copy(
        update={
            "command": command,
            "repair_inputs": repair,
            "protection_inputs": inputs,
            "full_input_digest": protection.input_digest,
            "income": income.ledger,
            "income_evidence_id": income.evidence_id,
            "income_evidence_hash": income.evidence_hash,
        }
    )


def capture_current_release_producers(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    original_actual_capture: ActualActionSetCapture | None = None,
) -> ReleaseActionSetCapture:
    _snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if user is None or not user.is_simulated or epoch is None or epoch.status != "OPEN":
        raise PolicyLifecycleError(
            "CURRENT_OWNER_EPOCH_NOT_AVAILABLE", "需要当前模拟用户开放epoch", 409
        )
    actual = original_actual_capture or capture_actual_action_set(session, user_id, now)
    if (actual.inputs.base.user_id, actual.inputs.base.epoch_id, actual.inputs.base.as_of) != (
        user_id,
        epoch.id,
        now,
    ):
        raise ValueError("RELEASE_SAME_INVOCATION_OWNER_EPOCH_CLOCK_DIFFERS")
    previous = session.info.get(CAPTURE_KEY)
    capture = start_capture(session)
    capture.sources.update(actual.originals.sources)
    capture.policies.update(actual.originals.policies)
    authorizations: list[ReleaseAuthorizationOriginal] = []
    commands: list[ReleaseOriginalCommand] = []
    producers: list[ReleaseProducerInput] = []
    reasons: list[str] = []
    try:
        with session.no_autoflush:
            for identity in release_authorization_ids(actual.inputs):
                authorization_item = ReleaseAuthorizationOriginal(source_id=identity)
                try:
                    raw = next(
                        row
                        for row in actual.inputs.base.original_inventory["evidence_items"]
                        if row["id"] == str(identity)
                    )
                    saved = GoalReleaseAuthorization.model_validate_json(json.dumps(raw["content"]))
                    lookup = read_release_authorization(
                        session, user_id, saved.epoch_id, saved.idempotency_key, now
                    )
                    recorded = get_decision_trace(session, user_id, saved.authorization_id, now)
                    if (
                        lookup.original is None
                        or recorded.trace is None
                        or recorded.completeness != "COMPLETE"
                        or recorded.audit_chain_status != "VALID"
                    ):
                        raise ValueError("RELEASE_DEDICATED_ORIGINAL_TRACE_OR_AUDIT_MISSING")
                    authorization_item = authorization_item.model_copy(
                        update={
                            "current_original": lookup.original,
                            "original_trace": recorded.trace,
                        }
                    )
                    capture_evidence(session, user_id, [identity])
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                    PolicyLifecycleError,
                ) as error:
                    authorization_item = authorization_item.model_copy(
                        update={"missing_reasons": [str(error)]}
                    )
                authorizations.append(authorization_item)
            for identity in release_action_ids(actual.inputs):
                item_command = ReleaseOriginalCommand(action_id=identity)
                try:
                    raw_action = next(
                        row
                        for row in actual.inputs.base.original_inventory["action_plans"]
                        if row["id"] == str(identity)
                    )
                    record = get_decision_trace(
                        session, user_id, UUID(raw_action["decision_run_id"]), now
                    )
                    if (
                        record.trace is None
                        or record.completeness != "COMPLETE"
                        or record.audit_chain_status != "VALID"
                    ):
                        raise ValueError("RELEASE_ORIGINAL_COMMAND_TRACE_OR_AUDIT_MISSING")
                    item_command = item_command.model_copy(update={"prepare_trace": record.trace})
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                    PolicyLifecycleError,
                ) as error:
                    item_command = item_command.model_copy(update={"missing_reasons": [str(error)]})
                commands.append(item_command)
            try:
                keys = release_producer_keys(actual.inputs)
            except (ValueError, TypeError, KeyError, StopIteration) as error:
                keys = []
                reasons.append("RELEASE_CURRENT_PRODUCER_DENOMINATOR_MISSING:" + str(error))
            for policy_id, goal_id, destination in keys:
                item = ReleaseProducerInput(
                    full_policy_id=policy_id,
                    source_goal_id=goal_id,
                    destination_account_id=destination,
                )
                try:
                    if len(keys) > MAX_PRODUCERS:
                        raise ValueError("RELEASE_CURRENT_PRODUCER_CAPACITY_EXCEEDED")
                    full = read_full_policy(session, user_id, policy_id, now)
                    item = item.model_copy(update={"full_policy": full})
                    capture_evidence(session, user_id, full.current_version.evidence_ids)
                    current = [
                        row
                        for row in authorizations
                        if row.current_original is not None
                        and row.current_original.current_scope_status == "CURRENT"
                        and row.current_original.original_authorization.policy_id == policy_id
                    ]
                    scopes = {
                        row.current_original.original_authorization.scope_hash
                        for row in current
                        if row.current_original is not None
                    }
                    if len(scopes) > 1:
                        raise ValueError("RELEASE_CURRENT_DEDICATED_SCOPE_NOT_UNIQUE")
                    if current and full.status not in {"REVOKED", "SUSPENDED", "EXPIRED"}:
                        selected = min(current, key=lambda row: str(row.source_id))
                        item = _candidate(session, user_id, now, actual, item, selected)
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    StopIteration,
                    PolicyLifecycleError,
                    OverflowError,
                ) as error:
                    item = item.model_copy(update={"missing_reasons": [str(error)]})
                producers.append(item)
            inputs = ReleaseActionSetInput(
                original_actual_input=actual.inputs,
                expected_full_policy_ids=release_policy_ids(actual.inputs),
                original_authorization_source_ids=release_authorization_ids(actual.inputs),
                original_action_ids=release_action_ids(actual.inputs),
                authorizations=authorizations,
                original_commands=commands,
                producers=producers,
                source_reasons=reasons,
            )
            result = derive_release_producers(inputs)
    finally:
        if previous is None:
            session.info.pop(CAPTURE_KEY, None)
        else:
            session.info[CAPTURE_KEY] = previous
            if isinstance(previous, DecisionCapture):
                previous.sources.update(capture.sources)
                previous.policies.update(capture.policies)
    return ReleaseActionSetCapture(inputs=inputs, result=result, originals=capture)


def read_current_release_producers(
    session: Session, user_id: UUID, now: datetime
) -> ReleaseActionSetResult:
    return capture_current_release_producers(session, user_id, now).result


def verify_release_source_copies(
    trace: DecisionTrace, inputs: ReleaseActionSetInput, result: ReleaseActionSetResult
) -> None:
    _verify_original_source_copies(
        trace, inputs.original_actual_input.base, result.release_family_complete
    )
    copied = {row.id: row for row in trace.sources}
    ids = set(inputs.original_authorization_source_ids)
    for item in inputs.producers:
        if item.full_policy is not None:
            ids.update(item.full_policy.current_version.evidence_ids)
        if item.income_evidence_id is not None:
            ids.add(item.income_evidence_id)
        if (
            item.actual_candidate is not None
            and item.actual_candidate.original_authorization is not None
        ):
            scope = item.actual_candidate.original_authorization.original_authorization.scope
            ids.update(row.full_model_evidence_id for row in scope.source_goals)
        if item.protection_inputs is not None:
            for policy_source in item.protection_inputs.policies:
                ids.update(policy_source.evidence_ids)
                for reference in policy_source.protected_references:
                    ids.update(reference.evidence_ids)
    raw_sources = {
        UUID(row["id"]): row
        for row in inputs.original_actual_input.base.original_inventory["evidence_items"]
    }
    if any(
        identity not in copied
        or copied[identity].user_id != result.user_id
        or copied[identity].content_integrity != "VERIFIED"
        or identity not in raw_sources
        or copied[identity].content != raw_sources[identity]["content"]
        or copied[identity].content_hash != raw_sources[identity]["content_hash"]
        for identity in ids
    ):
        raise ValueError("RELEASE_COMPLETE_ORIGINAL_SOURCE_COPY_MISSING")


def verify_frozen_release_producers(trace: DecisionTrace) -> ReleaseActionSetResult:
    verify_trace(trace)
    if (
        trace.algorithm_versions.get("release_action_producers") != ALGORITHM
        or trace.phase != "EVALUATION"
        or trace.action_id is not None
    ):
        raise ValueError("Not the exact release producer algorithm")
    inputs = ReleaseActionSetInput.model_validate_json(
        json.dumps(trace.inputs["release_action_set_input"])
    )
    result = derive_release_producers(inputs)
    verify_release_source_copies(trace, inputs, result)
    if (trace.user_id, trace.as_of) != (result.user_id, result.as_of) or trace.outcome != {
        "release_action_set_result": result.model_dump(mode="json")
    }:
        raise ValueError("Original release family result or owner/clock differs")
    return result
