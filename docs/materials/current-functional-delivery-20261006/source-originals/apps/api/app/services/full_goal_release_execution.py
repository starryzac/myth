"""Dedicated simulated goal cash release: immutable prepare, bank, projection and recovery."""

import json
from datetime import datetime, timedelta
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    Transaction,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.full_goal_reallocation import ReallocationPreviewRequest
from app.domain.full_goal_release_authorization import Key
from app.domain.full_goal_release_execution import (
    Cents,
    GoalReleaseBankCommand,
    GoalReleaseEffect,
    GoalReleaseUse,
    Hash,
    goal_release_effect_hash,
    goal_release_leg_specs,
    validate_release_authorization_binding,
)
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.audit_chain import audit_read_scope, row_copy
from app.services.audit_recording import (
    audit_subject_data,
    record_action_created,
    record_action_projected,
    record_action_transition,
    record_bank_accepted,
    record_bank_settled,
)
from app.services.decision_trace import capture_sources, get_decision_trace, record_trace
from app.services.execution_bank import _post
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_projection import _cash_preflight, _proof
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_goal_reallocation import (
    FullGoalReallocationPreview,
    preview_goal_reallocation,
)
from app.services.full_goal_release_authorization import (
    ReleaseAuthorizationResponse,
    _reader,
    read_release_authorization,
)
from app.services.full_goal_release_inventory import (
    GoalReleaseInventory,
    read_goal_release_inventory,
)
from app.services.full_goal_release_reader import (
    read_goal_release_bank_command,
    read_goal_release_command,
    verify_goal_release_legs,
    verify_goal_release_receipt,
)
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from app.services.recovery_projection import _epochs, replace_proof
from app.services.simulated_bank import ledger_heads, require_settlement_order
from pydantic import StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

ALGORITHM = "full-goal-emergency-release-v1"
NAMESPACE = UUID("321c7149-2db6-5338-a4de-0166d6cdf303")


class GoalReleasePrepareRequest(ReallocationPreviewRequest):
    authorization_epoch_id: UUIDReference
    authorization_idempotency_key: Key
    destination_account_id: UUIDReference
    idempotency_key: Key


class GoalReleaseExecuteRequest(BoundaryModel):
    accepted: StrictBool
    reviewed_effect_hash: Hash
    expected_epoch_id: UUIDReference

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.accepted is not True:
            raise ValueError("An original release effect requires explicit acceptance")
        return self


class GoalReleaseProtectionCheck(BoundaryModel):
    status: Literal["VERIFIED_NONWORSENING", "UNKNOWN", "BLOCKED"]
    actual_before_input_hash: Hash | None
    hypothetical_after_input_hash: Hash | None
    compared_point_count: Cents
    expected_point_count: Literal[1098] = 1098
    reasons: list[str]
    source_hash: Hash
    bank_authority: Literal[False] = False
    hypothetical_is_bank_fact: Literal[False] = False


class GoalReleaseCandidate(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    original_request: GoalReleasePrepareRequest
    state: Literal["READY", "BLOCKED", "UNKNOWN"]
    amount_cents: Cents | None
    actual_preview: FullGoalReallocationPreview
    actual_inventory: GoalReleaseInventory
    original_authorization: ReleaseAuthorizationResponse | None
    protection: GoalReleaseProtectionCheck | None
    selected_release_uses: list[GoalReleaseUse]
    reasons: list[str]
    input_hash: Hash
    bank_authority: Literal[False] = False
    creates_new_income: Literal[False] = False


class GoalReleaseActionResponse(BoundaryModel):
    schema_version: Literal["goal-release-action-v1"] = "goal-release-action-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    action_id: UUID
    original_action_status: str
    original_command: GoalReleaseBankCommand
    original_request_hash: Hash
    original_prepare_request: GoalReleasePrepareRequest
    decision_run_id: UUID
    action_confirmation_evidence_id: UUID | None
    action_confirmation_verified: bool
    bank_operation_id: UUID | None
    original_bank_status: str | None
    bank_settlement_legs_verified: bool
    receipt_id: UUID | None
    service_receipt_verified: bool
    original_receipt: dict[str, Any] | None
    unresolved: bool
    read_only_response: Literal[True] = True
    current_authority_assessed: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    economic_experiment_verified: Literal[False] = False
    grants_new_authority: Literal[False] = False


class GoalReleaseLookup(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original: GoalReleaseActionResponse | None
    not_found_is_final: Literal[False] = False
    replacement_allowed: Literal[False] = False


def _error(
    message: str, code: str = "GOAL_RELEASE_NOT_READY", status: int = 409
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, status)


def release_action_identity(user_id: UUID, epoch_id: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{user_id}:{epoch_id}:{key}")


def release_bank_key(user_id: UUID, epoch_id: UUID, key: str) -> str:
    return "goal-release:" + configuration_hash(
        {"user_id": str(user_id), "epoch_id": str(epoch_id), "key": key}
    )


def select_original_release_uses(
    available: list[GoalReleaseUse], amount: int
) -> list[GoalReleaseUse]:
    if type(amount) is not int or amount <= 0:
        raise ValueError("A release needs a positive independently computed integer amount")
    selected = []
    remaining = amount
    identities = [(row.allocation_action_id, row.fragment_id) for row in available]
    if len(set(identities)) != len(identities):
        raise ValueError("Original release inventory contains a repeated source slice")
    for row in sorted(available, key=lambda part: (part.allocation_action_id, part.fragment_id)):
        use = min(row.amount_cents, remaining)
        if use:
            selected.append(row.model_copy(update={"amount_cents": use}))
            remaining -= use
        if not remaining:
            break
    if remaining:
        raise ValueError("Complete verified original cash sources do not cover the unique repair")
    return selected


def _protection(
    session: Session,
    user_id: UUID,
    source_goal: UUID,
    destination: UUID,
    amount: int,
    now: datetime,
) -> GoalReleaseProtectionCheck:
    full = compute_full_annual_protection(session, user_id, now)
    context, bank_matched, exposures = load_verified_financial_context(session, user_id, now)
    context, issues, _ = finalize_financial_context(context, full.audit)
    reasons = [row.code for row in (*full.source_issues, *issues)]
    data = FullProtectionProjectionInput(
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
    )
    before = project_full_protection(data)
    goals = {row.goal_id: row for row in data.snapshot.goals}
    goal = goals.get(source_goal)
    if not bank_matched or not full.audit.complete or full.audit.status != "VALID":
        reasons.append("CURRENT_FINANCIAL_CONTEXT_OR_EXACT_AUDIT_NOT_VERIFIED")
    if (full.user_id, full.as_of) != (
        user_id,
        now,
    ) or before.input_hash != full.projection.input_hash:
        reasons.append("ACTUAL_FULL_PROTECTION_INPUT_BINDING_DIFFERS")
    if before.status == "UNKNOWN" or before.source_account_checks:
        reasons.append("FULL_FUTURE_ACCOUNT_DEBITS_OR_PROTECTION_NOT_PROVEN")
    after = None
    count = 0
    if goal is None or goal.account_id is None or goal.cash_owned_cents < amount:
        reasons.append("CURRENT_SOURCE_GOAL_CASH_NOT_PROVEN")
    elif not reasons:
        accounts = {row.account_id: row for row in data.snapshot.cash_accounts}
        source, target = accounts.get(goal.account_id), accounts.get(destination)
        if (
            source is None
            or target is None
            or target.account_type != "CASH"
            or source.balance_cents < amount
            or source.account_id == target.account_id
        ):
            reasons.append("CURRENT_OWNED_CASH_ACCOUNTS_NOT_PROVEN")
        else:
            # Planning-only post-effect values; no new evidence or authority is created.
            hypothetical = data.snapshot.model_copy(
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
                        for row in data.snapshot.cash_accounts
                    ],
                    "goals": [
                        row.model_copy(
                            update={
                                "cash_owned_cents": row.cash_owned_cents - amount,
                                "allocated_cents": row.allocated_cents - amount,
                            }
                        )
                        if row.goal_id == source_goal
                        else row
                        for row in data.snapshot.goals
                    ],
                }
            )
            after = project_full_protection(data.model_copy(update={"snapshot": hypothetical}))
            if after.status == "UNKNOWN" or after.source_account_checks:
                reasons.append("POST_RELEASE_FULL_PROTECTION_NOT_PROVEN")
            old = before.full_annual_projection
            new = after.full_annual_projection
            if (
                old is None
                or new is None
                or len(old.calculation_trace) != 1098
                or len(new.calculation_trace) != 1098
            ):
                reasons.append("FULL_365_DAY_THREE_PHASE_COVERAGE_MISSING")
            else:
                for left, right in zip(old.calculation_trace, new.calculation_trace, strict=True):
                    if (left.day, left.date, left.phase) != (right.day, right.date, right.phase):
                        reasons.append("FULL_PROTECTION_POINT_IDENTITY_DIFFERS")
                        break
                    count += 1
                    if (
                        right.cash_cents < left.cash_cents
                        or sum(right.protected_cents_by_reason.values())
                        > sum(left.protected_cents_by_reason.values())
                        or right.margin_cents < left.margin_cents
                    ):
                        reasons.append("RELEASE_WORSENS_ORIGINAL_HARD_PROTECTION_POINT")
    return GoalReleaseProtectionCheck(
        status="VERIFIED_NONWORSENING"
        if not reasons and count == 1098
        else "BLOCKED"
        if "RELEASE_WORSENS_ORIGINAL_HARD_PROTECTION_POINT" in reasons
        else "UNKNOWN",
        actual_before_input_hash=before.input_hash,
        hypothetical_after_input_hash=after.input_hash if after else None,
        compared_point_count=count,
        reasons=sorted(set(reasons)),
        source_hash=configuration_hash(
            {
                "full_input": full.input_digest,
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json") if after else None,
                "amount": amount,
                "destination": str(destination),
            }
        ),
    )


def preview_goal_release_execution(
    session: Session, user_id: UUID, body: GoalReleasePrepareRequest, now: datetime
) -> GoalReleaseCandidate:
    _read_snapshot(session)
    now = _now(now)
    with audit_read_scope(session):
        preview = preview_goal_reallocation(
            session,
            user_id,
            ReallocationPreviewRequest.model_validate(
                body.model_dump(include=set(ReallocationPreviewRequest.model_fields))
            ),
            now,
        )
        inventory = read_goal_release_inventory(
            session,
            user_id,
            body.source_goal_id,
            body.expected_epoch_id,
            body.expected_goal_policy_version_id,
            body.policy_id,
            now,
        )
        lookup = read_release_authorization(
            session, user_id, body.authorization_epoch_id, body.authorization_idempotency_key, now
        )
        authorization = lookup.original
        reasons = [row.code for row in preview.source_issues]
        reasons.extend(inventory.reasons)
        amount = preview.decision.math.minimum_repair_cents
        blocked = []
        if body.authorization_epoch_id != body.expected_epoch_id:
            blocked.append("DEDICATED_AUTHORIZATION_EPOCH_DIFFERS")
        if authorization is None or authorization.current_scope_status != "CURRENT":
            reasons.append("CURRENT_DEDICATED_AUTHORIZATION_NOT_VERIFIED")
        elif (
            authorization.original_authorization.policy_id,
            authorization.original_authorization.policy_version_id,
        ) != (body.policy_id, body.expected_policy_version_id):
            blocked.append("DEDICATED_AUTHORIZATION_POLICY_VERSION_DIFFERS")
        if preview.decision.math.status != "COMPUTED" or type(amount) is not int:
            reasons.append("CURRENT_UNIQUE_MINIMUM_REPAIR_UNKNOWN")
        elif amount <= 0:
            blocked.append("NO_CURRENT_EMERGENCY")
        if inventory.state != "VERIFIED" or not inventory.application_projection_matched:
            reasons.append("CURRENT_COMPLETE_SOURCE_BANK_AND_APPLICATION_NOT_MATCHED")
        if inventory.policy_usage.cap_occupied_cents is None:
            reasons.append("CURRENT_ALL_POLICY_VERSION_USAGE_UNKNOWN")
        # The old planning preview deliberately has no execution grant or usage.
        # Inspect its actual math and current raw sources, never remove its UNKNOWN status.
        if (
            authorization is not None
            and authorization.current_scope_status == "CURRENT"
            and type(amount) is int
            and amount > 0
        ):
            scope = authorization.original_authorization.scope
            if not set(preview.decision.triggered_conditions) <= set(scope.emergency_conditions):
                blocked.append("ACTUAL_EMERGENCY_NOT_EXPLICITLY_AUTHORIZED")
            releasable = preview.decision.math.source_cash_releasable_above_minimum_cents
            if releasable is None:
                reasons.append("SOURCE_MINIMUM_GUARANTEE_UNKNOWN")
            elif amount > releasable:
                blocked.append("SOURCE_MINIMUM_GUARANTEE_WOULD_BE_REDUCED")
            used = inventory.policy_usage.cap_occupied_cents
            if (
                amount > scope.single_action_cap_cents
                or used is not None
                and amount + used > scope.total_cap_cents
            ):
                blocked.append("DEDICATED_SINGLE_OR_ALL_VERSION_CAP_EXCEEDED")
        destination = session.get(Account, body.destination_account_id)
        if (
            destination is None
            or destination.user_id != user_id
            or destination.account_type != "CASH"
            or destination.id == inventory.original_basis.goal_account_id
        ):
            blocked.append("DESTINATION_NOT_CURRENT_OWNED_PROTECTED_CASH")
        uses: list[GoalReleaseUse] = []
        protection = None
        if not reasons and not blocked and type(amount) is int and amount > 0:
            try:
                uses = select_original_release_uses(inventory.release_uses_available, amount)
                protection = _protection(
                    session, user_id, body.source_goal_id, body.destination_account_id, amount, now
                )
                if protection.status != "VERIFIED_NONWORSENING":
                    reasons.extend(protection.reasons)
            except (ValueError, TypeError, PolicyLifecycleError) as error:
                reasons.append(
                    error.code
                    if isinstance(error, PolicyLifecycleError)
                    else "ORIGINAL_SOURCE_OR_PROTECTION_NOT_VERIFIED"
                )
        problems = sorted(set(reasons + blocked))
        values = {
            "user_id": user_id,
            "epoch_id": body.expected_epoch_id,
            "as_of": now,
            "original_request": body,
            "state": "BLOCKED" if blocked else "UNKNOWN" if reasons else "READY",
            "amount_cents": amount if not problems else None,
            "actual_preview": preview,
            "actual_inventory": inventory,
            "original_authorization": authorization,
            "protection": protection,
            "selected_release_uses": uses if not problems else [],
            "reasons": problems,
        }
        return GoalReleaseCandidate.model_validate(
            {
                **values,
                "input_hash": configuration_hash(
                    {
                        "request": body.model_dump(mode="json"),
                        "preview": preview.source_binding_hash,
                        "inventory": inventory.source_binding_hash,
                        "authorization": authorization.model_dump(mode="json")
                        if authorization
                        else None,
                        "protection": protection.model_dump(mode="json") if protection else None,
                        "reasons": problems,
                        "as_of": now.isoformat(),
                    }
                ),
            }
        )


def validate_goal_release_exposure(
    action: ActionPlan,
    declaration: dict[str, Any],
    receipts: list[ActionReceipt],
    operations: list[BankOperation],
    reservations: list[ActionResourceReservation],
    postings: list[SimulatedBankPosting],
    epoch: datetime,
    *,
    session: Session | None = None,
) -> GoalReleaseBankCommand:
    """New explicit unreserved protocol; no old resource or income claim is invented."""
    command = read_goal_release_command(action)
    if declaration.get("action_id") != str(action.id) or action.created_at > epoch:
        raise ValueError("New release exposure does not bind the actual action or source time")
    own_ops = [row for row in operations if row.action_plan_id == action.id]
    own_receipts = [row for row in receipts if row.action_plan_id == action.id]
    own_claims = [row for row in reservations if row.action_plan_id == action.id]
    own_postings = [row for row in postings if row.operation_id == action.id]
    if own_claims or len(own_ops) > 1 or len(own_receipts) > 1:
        raise ValueError(
            "Dedicated release cannot invent claims or duplicate its original bank result"
        )
    state = declaration.get("state")
    if state in {"PLANNED_UNRESERVED", "RELEASE_PENDING_UNRESERVED"}:
        allowed = {"PLANNED", "AUTHORIZED"} if state == "PLANNED_UNRESERVED" else {"SUBMITTED"}
        if (
            set(declaration) != {"action_id", "state"}
            or action.status not in allowed
            or own_ops
            or own_receipts
            or own_postings
        ):
            raise ValueError("A pending release is not a reserved or settled financial result")
    elif state == "EXECUTION_SETTLED":
        if (
            session is None
            or action.status not in {"SUCCEEDED", "RECONCILED"}
            or len(own_ops) != 1
            or len(own_receipts) != 1
            or set(declaration) != {"action_id", "state", "receipt_id"}
            or declaration.get("receipt_id") != str(own_receipts[0].id)
        ):
            raise ValueError("A completed release needs its exact independent original receipt")
        verify_goal_release_receipt(session, own_ops[0], own_receipts[0], epoch)
    elif state == "NO_EFFECT":
        if (
            set(declaration) != {"action_id", "state"}
            or action.status not in {"CANCELLED", "INVALIDATED"}
            or own_receipts
            or own_postings
            or own_ops
            and own_ops[0].status != "REJECTED"
        ):
            raise ValueError("Unknown original effects cannot be relabeled as absent")
        if own_ops:
            if session is None:
                raise ValueError("Actual original rejection reader required")
            read_goal_release_bank_command(session, own_ops[0], epoch)
    else:
        raise ValueError("Unreconciled dedicated release must remain financially unknown")
    return command


def _envelope(
    body: GoalReleasePrepareRequest,
    command: GoalReleaseBankCommand,
    candidate: GoalReleaseCandidate,
) -> dict[str, Any]:
    return {
        "protocol": "goal-release-action-v1",
        "original_prepare_request": body.model_dump(mode="json"),
        "goal_release_execution": command.model_dump(mode="json"),
        "goal_release_source_basis": candidate.actual_inventory.original_basis.model_dump(
            mode="json"
        ),
        "prepared_financial_input_hash": candidate.input_hash,
        "prepared_protection": candidate.protection.model_dump(mode="json")
        if candidate.protection
        else None,
    }


def _effect(action_id: UUID, candidate: GoalReleaseCandidate) -> GoalReleaseBankCommand:
    authorization = candidate.original_authorization
    amount = candidate.amount_cents
    if (
        candidate.state != "READY"
        or authorization is None
        or amount is None
        or not candidate.selected_release_uses
    ):
        raise _error("原当前缺口、权限、来源与完整保护未同时验真")
    auth = authorization.original_authorization
    source = next(
        (
            row
            for row in auth.scope.source_goals
            if row.goal_id == candidate.original_request.source_goal_id
        ),
        None,
    )
    if source is None:
        raise _error("专用授权不包含当前源目标")
    effect = GoalReleaseEffect(
        operation_id=action_id,
        user_id=candidate.user_id,
        epoch_id=candidate.epoch_id,
        business_key=f"goal-release:{action_id}",
        bank_idempotency_key=release_bank_key(
            candidate.user_id, candidate.epoch_id, candidate.original_request.idempotency_key
        ),
        policy_id=auth.policy_id,
        policy_version_id=auth.policy_version_id,
        policy_configuration_hash=auth.scope.policy_configuration_hash,
        authorization_id=auth.authorization_id,
        authorization_evidence_id=authorization.evidence_id,
        authorization_evidence_hash=authorization.evidence_hash,
        authorization_scope_hash=auth.scope_hash,
        authorization_request_hash=auth.request_hash,
        source_goal_id=source.goal_id,
        original_goal_policy_id=source.original_policy_id,
        original_goal_policy_version_id=source.original_policy_version_id,
        full_model_evidence_id=source.full_model_evidence_id,
        full_model_evidence_hash=source.full_model_evidence_hash,
        full_configuration_hash=source.full_configuration_hash,
        minimum_guarantee_cents=source.minimum_guarantee_cents,
        source_account_id=candidate.actual_inventory.original_basis.goal_account_id,
        destination_account_id=candidate.original_request.destination_account_id,
        amount_cents=amount,
        emergency_conditions=sorted(candidate.actual_preview.decision.triggered_conditions),
        release_uses=candidate.selected_release_uses,
        source_provenance_hash=candidate.actual_inventory.original_basis.source_binding_hash,
        financial_input_hash=candidate.input_hash,
        valid_from=candidate.as_of,
        expires_at=min(candidate.as_of + timedelta(minutes=15), auth.valid_until),
    )
    command = GoalReleaseBankCommand(effect=effect, effect_hash=goal_release_effect_hash(effect))
    validate_release_authorization_binding(command, auth, candidate.as_of)
    return command


def _trace(
    session: Session,
    action: ActionPlan,
    candidate: GoalReleaseCandidate,
    now: datetime,
    phase: Literal["PREPARE", "BANK_ACCEPT"],
    *,
    existing_run: DecisionRun | None = None,
) -> None:
    command = read_goal_release_command(action)
    ids = {command.effect.authorization_evidence_id, command.effect.full_model_evidence_id}
    basis = candidate.actual_inventory.original_basis
    if basis.income_evidence_id:
        ids.add(basis.income_evidence_id)
    run_id = (
        action.decision_run_id if phase == "PREPARE" else uuid5(action.id, "bank-accept-decision")
    )
    trace = build_trace(
        run_id=run_id,
        user_id=action.user_id,
        phase=phase,
        as_of=now,
        action_id=action.id,
        parent_run_id=None if phase == "PREPARE" else action.decision_run_id,
        algorithm_versions={"goal_release": ALGORITHM},
        inputs={
            "goal_release_effect": command.effect.model_dump(mode="json"),
            "action_request": action.request,
            "actual_candidate": candidate.model_dump(mode="json"),
        },
        sources=capture_sources(session, action.user_id, ids),
        policies=[],
        constraints=[],
        candidates=[],
        outcome={
            "action_id": str(action.id),
            "effect_hash": command.effect_hash,
            "decision": "READY",
            "money_reserved": False,
            "creates_new_income": False,
        },
    )
    record_trace(session, trace, existing_run=existing_run)


def prepare_goal_release_execution(
    engine: Engine, user_id: UUID, body: GoalReleasePrepareRequest, now: datetime
) -> GoalReleaseActionResponse:
    body = GoalReleasePrepareRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    identity = release_action_identity(user_id, body.expected_epoch_id, body.idempotency_key)
    with audit_command_guard(engine, user_id), Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        existing = writer.get(ActionPlan, identity)
        if existing is not None:
            if existing.user_id != user_id or existing.request.get(
                "original_prepare_request"
            ) != body.model_dump(mode="json"):
                raise _error("原回拨键不能改变已持久原输入", "IDEMPOTENCY_CONFLICT")
            return _read_action(writer, user_id, identity, now)
        with _reader(engine) as reader:
            candidate = preview_goal_release_execution(reader, user_id, body, now)
        command = _effect(identity, candidate)
        payload = _envelope(body, command, candidate)
        initial = {
            "original_prepare_request": body.model_dump(mode="json"),
            "effect_hash": command.effect_hash,
        }
        run = DecisionRun(
            id=uuid5(identity, "decision"),
            user_id=user_id,
            created_at=now,
            idempotency_key="goal-release-decision:" + str(identity),
            trigger_type="ACTION_PREPARE",
            algorithm_version=ALGORITHM,
            as_of=now,
            input_snapshot=initial,
            snapshot_hash=configuration_hash(initial),
            policy_version_ids=[str(command.effect.original_goal_policy_version_id)],
            evidence_ids=[],
            result={"action_id": str(identity)},
            status="SUCCEEDED",
            completed_at=now,
        )
        writer.add(run)
        writer.flush()
        action = ActionPlan(
            id=identity,
            user_id=user_id,
            created_at=now,
            decision_run_id=run.id,
            policy_version_id=command.effect.original_goal_policy_version_id,
            source_account_id=command.effect.source_account_id,
            destination_account_id=command.effect.destination_account_id,
            goal_id=command.effect.source_goal_id,
            product_id=None,
            position_id=None,
            action_type="RELEASE_GOAL",
            amount_cents=command.effect.amount_cents,
            autonomy_level="ASK_ONCE",
            status="PLANNED",
            idempotency_key=command.effect.bank_idempotency_key,
            request=payload,
            request_hash=configuration_hash(payload),
            authorized_at=None,
            expires_at=command.effect.expires_at,
        )
        writer.add(action)
        writer.flush()
        _trace(writer, action, candidate, now, "PREPARE", existing_run=run)
        _epochs(writer, user_id, now, identity)
        refresh_execution_exposure(
            writer,
            user_id,
            now,
            identity,
            declarations={identity: {"action_id": str(identity), "state": "PLANNED_UNRESERVED"}},
        )
        record_action_created(writer, action, now)
        return _read_action(writer, user_id, identity, now)


def read_goal_release_execution(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> GoalReleaseActionResponse:
    _read_snapshot(session)
    return _read_action(session, user_id, action_id, _now(now))


def _read_action(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> GoalReleaseActionResponse:
    action = session.get(ActionPlan, action_id)
    if action is None or action.user_id != user_id:
        raise _error("当前用户原回拨行动不存在", "NOT_FOUND", 404)
    command = read_goal_release_command(action)
    confirmation = read_goal_release_action_confirmation(session, command)
    if (
        action.status in {"AUTHORIZED", "SUBMITTED", "UNKNOWN", "SUCCEEDED", "RECONCILED"}
        and confirmation is None
    ):
        raise _error("原逐行动明确确认缺失", "GOAL_RELEASE_CONFIRMATION_MISSING")
    trace = get_decision_trace(session, user_id, action.decision_run_id, now)
    if (
        trace.completeness != "COMPLETE"
        or trace.audit_chain_status != "VALID"
        or trace.trace is None
    ):
        raise _error("回拨原决策、行动摘要或审计缺失", "GOAL_RELEASE_ORIGINAL_INTEGRITY_ERROR")
    ops = list(
        session.scalars(
            select(BankOperation)
            .where(BankOperation.user_id == user_id, BankOperation.action_plan_id == action.id)
            .limit(2)
        )
    )
    receipts = list(
        session.scalars(
            select(ActionReceipt)
            .where(ActionReceipt.user_id == user_id, ActionReceipt.action_plan_id == action.id)
            .limit(2)
        )
    )
    if len(ops) > 1 or len(receipts) > 1:
        raise _error("原逻辑回拨存在重复银行或回执", "GOAL_RELEASE_ORIGINAL_INTEGRITY_ERROR")
    bank = ops[0] if ops else None
    receipt = receipts[0] if receipts else None
    if receipt is not None and bank is None:
        raise _error("原回执没有对应银行原件", "GOAL_RELEASE_ORIGINAL_INTEGRITY_ERROR")
    bank_verified = False
    if bank is not None:
        read_goal_release_bank_command(session, bank, now)
        if bank.status == "SETTLED":
            verify_goal_release_legs(session, bank, now)
            bank_verified = True
        if receipt is not None:
            verify_goal_release_receipt(session, bank, receipt, now)
    if action.status in {"SUCCEEDED", "RECONCILED"} and (receipt is None or not bank_verified):
        raise _error("完成字符串不能替代原银行三腿和回执", "GOAL_RELEASE_ORIGINAL_INTEGRITY_ERROR")
    body = GoalReleasePrepareRequest.model_validate_json(
        json.dumps(action.request["original_prepare_request"])
    )
    return GoalReleaseActionResponse(
        user_id=user_id,
        as_of=now,
        action_id=action.id,
        original_action_status=action.status,
        original_command=command,
        original_request_hash=action.request_hash,
        original_prepare_request=body,
        decision_run_id=action.decision_run_id,
        action_confirmation_evidence_id=confirmation.id if confirmation else None,
        action_confirmation_verified=confirmation is not None,
        bank_operation_id=bank.id if bank else None,
        original_bank_status=bank.status if bank else None,
        bank_settlement_legs_verified=bank_verified,
        receipt_id=receipt.id if receipt else None,
        service_receipt_verified=receipt is not None,
        original_receipt=row_copy(receipt) if receipt else None,
        unresolved=action.status in {"SUBMITTED", "UNKNOWN"}
        or bank is not None
        and receipt is None,
    )


def read_goal_release_by_key(
    session: Session, user_id: UUID, epoch_id: UUID, key: str, now: datetime
) -> GoalReleaseLookup:
    _read_snapshot(session)
    now = _now(now)
    identity = release_action_identity(user_id, epoch_id, key)
    action = session.get(ActionPlan, identity)
    original = None if action is None else _read_action(session, user_id, identity, now)
    if original is not None and (
        original.original_command.effect.epoch_id != epoch_id
        or original.original_prepare_request.idempotency_key != key
    ):
        raise _error("原键与原epoch不一致", "GOAL_RELEASE_ORIGINAL_INTEGRITY_ERROR")
    return GoalReleaseLookup(
        user_id=user_id,
        epoch_id=epoch_id,
        idempotency_key=key,
        status="RECORDED" if original else "NOT_FOUND_NOT_FINAL",
        original=original,
    )


def _fresh_candidate(engine: Engine, action: ActionPlan, now: datetime) -> GoalReleaseCandidate:
    command = read_goal_release_command(action)
    body = GoalReleasePrepareRequest.model_validate_json(
        json.dumps(action.request["original_prepare_request"])
    )
    with _reader(engine) as reader:
        candidate = preview_goal_release_execution(reader, action.user_id, body, now)
    authorization = candidate.original_authorization
    effect = command.effect
    if candidate.state != "READY" or authorization is None:
        raise _error("当前缺口、授权或全部来源已变化，原经济后果不可受理")
    validate_release_authorization_binding(command, authorization.original_authorization, now)
    if (
        candidate.amount_cents != effect.amount_cents
        or candidate.selected_release_uses != effect.release_uses
        or authorization.evidence_id != effect.authorization_evidence_id
        or authorization.evidence_hash != effect.authorization_evidence_hash
        or candidate.actual_inventory.original_basis.goal_account_id != effect.source_account_id
    ):
        raise _error("当前唯一最低修复或原来源已变化，不能改写原经济后果")
    return candidate


def _transition(
    session: Session, action: ActionPlan, status: str, now: datetime, reason: str
) -> None:
    before_status = action.status
    before_data = audit_subject_data(action)
    action.status = status
    if status == "SUBMITTED":
        action.authorized_at = action.authorized_at or now
    session.flush()
    record_action_transition(
        session, action, before_status, now, reason_code=reason, before_data=before_data
    )


def validate_goal_release_execute_request(
    body: GoalReleaseExecuteRequest, command: GoalReleaseBankCommand
) -> None:
    """Exact original effect consent, including historical same-key recovery."""
    if (
        body.reviewed_effect_hash != command.effect_hash
        or body.expected_epoch_id != command.effect.epoch_id
    ):
        raise _error("逐行动确认与原经济后果或原epoch不同", "CONFIRMATION_MISMATCH")


def read_goal_release_action_confirmation(
    session: Session, command: GoalReleaseBankCommand
) -> EvidenceItem | None:
    """Verify original consent; its historical existence grants no current permission."""
    effect = command.effect
    identity = uuid5(effect.operation_id, "release-confirmation:" + command.effect_hash)
    proof = session.get(EvidenceItem, identity)
    if proof is None:
        return None
    content = proof.content
    expected = {
        "protocol": "goal-release-action-confirmation-v1",
        "simulation": True,
        "user_id": str(effect.user_id),
        "action_id": str(effect.operation_id),
        "epoch_id": str(effect.epoch_id),
        "effect_hash": command.effect_hash,
        "accepted": True,
        "confirmed_at": proof.observed_at.isoformat(),
        "valid_until": effect.expires_at.isoformat(),
    }
    if (
        proof.id != identity
        or proof.user_id != effect.user_id
        or proof.evidence_level != "USER_CONFIRMED_ACTION"
        or proof.source_type != "USER_GOAL_RELEASE_ACTION_CONFIRMATION"
        or proof.source_ref != str(effect.operation_id)
        or proof.status != "VALID"
        or content != expected
        or type(content.get("accepted")) is not bool
        or proof.content_hash != configuration_hash(content)
        or configuration_hash(content) != configuration_hash(expected)
        or proof.created_at != proof.observed_at
        or proof.valid_from != proof.observed_at
        or proof.valid_to != effect.expires_at
        or not effect.valid_from <= proof.observed_at < effect.expires_at
    ):
        raise _error("原逐行动确认原件或摘要不一致", "GOAL_RELEASE_CONFIRMATION_INVALID")
    return proof


def _confirm_in_transaction(
    session: Session, action: ActionPlan, command: GoalReleaseBankCommand, now: datetime
) -> None:
    if read_goal_release_action_confirmation(session, command) is not None:
        return
    effect = command.effect
    if action.status != "PLANNED" or not effect.valid_from <= now < effect.expires_at:
        raise _error("不能为已提交或过期原行动补造确认", "GOAL_RELEASE_CONFIRMATION_MISSING")
    identity = uuid5(action.id, "release-confirmation:" + command.effect_hash)
    content = {
        "protocol": "goal-release-action-confirmation-v1",
        "simulation": True,
        "user_id": str(action.user_id),
        "action_id": str(action.id),
        "epoch_id": str(effect.epoch_id),
        "effect_hash": command.effect_hash,
        "accepted": True,
        "confirmed_at": now.isoformat(),
        "valid_until": effect.expires_at.isoformat(),
    }
    session.add(
        EvidenceItem(
            id=identity,
            user_id=action.user_id,
            created_at=now,
            evidence_level="USER_CONFIRMED_ACTION",
            source_type="USER_GOAL_RELEASE_ACTION_CONFIRMATION",
            source_ref=str(action.id),
            content=content,
            content_hash=configuration_hash(content),
            status="VALID",
            observed_at=now,
            valid_from=now,
            valid_to=effect.expires_at,
        )
    )
    session.flush()
    before = audit_subject_data(action)
    action.status, action.authorized_at = "AUTHORIZED", now
    session.flush()
    record_action_transition(
        session,
        action,
        "PLANNED",
        now,
        reason_code="USER_GOAL_RELEASE_ACTION_CONFIRMED",
        cause_ref=str(identity),
        before_data=before,
        details={"confirmation_evidence_id": str(identity), "effect_hash": command.effect_hash},
    )


def process_goal_release_bank(
    engine: Engine, user_id: UUID, action_id: UUID, now: datetime
) -> None:
    """Own transaction: fresh original permission and current math before new actual legs."""
    now = _now(now)
    with Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        action = writer.get(ActionPlan, action_id)
        if action is None or action.user_id != user_id:
            raise _error("原回拨行动不存在", "NOT_FOUND", 404)
        command = read_goal_release_command(action)
        existing = writer.scalar(
            select(BankOperation).where(
                BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
            )
        )
        if existing is not None:
            read_goal_release_bank_command(writer, existing, now)
            if existing.status == "SETTLED":
                verify_goal_release_legs(writer, existing, now)
                return
            raise _error(
                "银行原键尚无已验真结算，不能再次生成效果", "GOAL_RELEASE_BANK_RESULT_UNKNOWN"
            )
        if action.status != "SUBMITTED":
            raise _error("专用回拨需原应用提交阶段", "INVALID_ACTION_STATE")
        if read_goal_release_action_confirmation(writer, command) is None:
            raise _error("独立银行受理前缺原逐行动确认", "GOAL_RELEASE_CONFIRMATION_MISSING")
        require_settlement_order(writer, user_id, now)
        # The writer keeps the original user lock; the fresh reader has no writes
        # and never takes a second user row lock or borrows current permission.
        candidate = _fresh_candidate(engine, action, now)
        _trace(writer, action, candidate, now, "BANK_ACCEPT")
        effect = command.effect
        operation = BankOperation(
            id=effect.operation_id,
            user_id=user_id,
            created_at=now,
            action_plan_id=action.id,
            legacy_redemption_id=None,
            closing_position_id=None,
            operation_type="RELEASE_GOAL",
            business_key=effect.business_key,
            idempotency_key=effect.bank_idempotency_key,
            request=command.model_dump(mode="json"),
            request_hash=configuration_hash(command.model_dump(mode="json")),
            requested_at=now,
            available_at=now,
            settled_at=None,
            status="ACCEPTED",
        )
        writer.add(operation)
        writer.flush()
        record_bank_accepted(writer, operation, now)
        heads = ledger_heads(writer, user_id)
        for leg in goal_release_leg_specs(command):
            head = heads.get(leg.ledger_key)
            if (
                head is None
                or head.user_id != user_id
                or head.account_id != leg.account_id
                or head.ledger_dimension != leg.ledger_dimension
                or any(
                    head.ledger_metadata.get(key) != value
                    for key, value in leg.required_metadata.items()
                )
            ):
                raise _error("独立银行原账户或目标归属头与原经济后果不一致")
            _post(writer, operation, leg.ledger_key, leg.delta_cents, now, leg.leg_ref)
        operation.status, operation.settled_at = "SETTLED", now
        writer.flush()
        verify_goal_release_legs(writer, operation, now)
        record_bank_settled(writer, operation, now)


def _cash_transaction(
    session: Session, command: GoalReleaseBankCommand, row: SimulatedBankPosting, now: datetime
) -> UUID:
    effect = command.effect
    identity = uuid5(effect.operation_id, "transaction:" + str(row.leg_ref))
    source_ref = f"bank-operation:{effect.operation_id}:{row.leg_ref}"
    counterparty = f"goal-release:{effect.source_goal_id}"
    payload = {
        "simulation": True,
        "user_id": str(effect.user_id),
        "transaction_id": str(identity),
        "account_id": str(row.account_id),
        "direction": "DEBIT" if row.delta_cents < 0 else "CREDIT",
        "amount_cents": abs(row.delta_cents),
        "balance_after_cents": row.balance_after_cents,
        "occurred_at": row.occurred_at.isoformat(),
        "counterparty_ref": counterparty,
        "economic_role": "INTERNAL_TRANSFER",
        "bank_operation_id": str(effect.operation_id),
        "bank_posting_id": str(row.id),
    }
    proof_id = uuid5(identity, "evidence")
    session.add(
        EvidenceItem(
            id=proof_id,
            user_id=effect.user_id,
            created_at=now,
            evidence_level="BANK_CONFIRMED",
            source_type="SIMULATED_BANK_TRANSACTION",
            source_ref=source_ref,
            content=payload,
            content_hash=configuration_hash(payload),
            valid_from=row.occurred_at,
            observed_at=now,
            status="VALID",
        )
    )
    session.flush()
    session.add(
        Transaction(
            id=identity,
            user_id=effect.user_id,
            created_at=now,
            account_id=row.account_id,
            evidence_id=proof_id,
            source_ref=source_ref,
            direction=payload["direction"],
            amount_cents=abs(row.delta_cents),
            balance_after_cents=row.balance_after_cents,
            category="internal_transfer",
            category_confirmed=False,
            counterparty_ref=counterparty,
            is_one_off=False,
            occurred_at=row.occurred_at,
            observed_at=now,
        )
    )
    return identity


def _goal_projection(
    session: Session,
    command: GoalReleaseBankCommand,
    rows: list[SimulatedBankPosting],
    now: datetime,
) -> None:
    effect = command.effect
    goal = session.get(Goal, effect.source_goal_id)
    leg = next((row for row in rows if row.leg_ref == "goal_cash"), None)
    if (
        goal is None
        or goal.user_id != effect.user_id
        or goal.account_id != effect.source_account_id
        or leg is None
    ):
        raise _error("当前源目标不能替换原已结算归属", "BANK_RECONCILIATION_REQUIRED")
    proof = _proof(session, effect.user_id, "SIMULATED_GOAL_OWNERSHIP", "goal_id", goal.id, now)
    positions = list(
        session.scalars(
            select(AssetPosition).where(
                AssetPosition.user_id == effect.user_id,
                AssetPosition.goal_id == goal.id,
                AssetPosition.status != "REDEEMED",
            )
        )
    )
    principal = sum(row.principal_cents for row in positions)
    expected = {
        "protocol": "goal-ownership-v1",
        "policy_id": str(goal.policy_id),
        "account_id": str(goal.account_id),
        "allocated_cents": goal.allocated_cents,
        "cash_owned_cents": goal.allocated_cents - principal,
        "principal_owned_cents": principal,
        "position_ids": sorted(str(row.id) for row in positions),
    }
    if (
        any(proof.content.get(key) != value for key, value in expected.items())
        or leg.balance_before_cents != expected["cash_owned_cents"]
    ):
        raise _error("目标投影与原银行前归属不一致", "BANK_RECONCILIATION_REQUIRED")
    goal.allocated_cents = leg.balance_after_cents + principal
    replace_proof(
        session,
        proof,
        {
            **proof.content,
            "as_of": now.isoformat(),
            "allocated_cents": goal.allocated_cents,
            "cash_owned_cents": leg.balance_after_cents,
            "principal_owned_cents": principal,
        },
        now,
        effect.operation_id,
    )


def project_goal_release_execution(
    session: Session, operation: BankOperation, now: datetime
) -> ActionReceipt:
    """Actual savepoint; historical settlement recovery does not borrow current permission."""
    now = _now(now)
    with session.begin_nested():
        _user(session, operation.user_id)
        command = read_goal_release_bank_command(session, operation, now)
        rows = verify_goal_release_legs(session, operation, now)
        action = session.get(ActionPlan, operation.action_plan_id)
        assert action is not None
        receipts = list(
            session.scalars(
                select(ActionReceipt)
                .where(
                    ActionReceipt.user_id == operation.user_id,
                    ActionReceipt.action_plan_id == action.id,
                )
                .limit(2)
            )
        )
        if receipts:
            if len(receipts) != 1:
                raise _error("原回执不唯一", "BANK_RECONCILIATION_REQUIRED")
            verify_goal_release_receipt(session, operation, receipts[0], now)
            return receipts[0]
        cash = _cash_preflight(session, operation, rows, now)
        if len(cash) != 2:
            raise _error("回拨需要原两账户投影", "BANK_RECONCILIATION_REQUIRED")
        # Before any mutation, current original income has no amount/bucket change.
        income = read_income_state(session, operation.user_id, now)
        income_proof = session.get(EvidenceItem, income.evidence_id)
        assert income_proof is not None
        transactions = []
        for account, proof, row in cash:
            account.balance_cents, account.observed_at = row.balance_after_cents, now
            replace_proof(
                session,
                proof,
                {
                    **proof.content,
                    "balance_cents": account.balance_cents,
                    "as_of": now.isoformat(),
                    "bank_posting_id": str(row.id),
                },
                now,
                operation.id,
            )
            transactions.append(_cash_transaction(session, command, row, now))
        _goal_projection(session, command, rows, now)
        # Refresh only metadata clocks; all original income origins/fragments/uses,
        # assigned/spent amounts and monthly contributions remain byte-value equal.
        replace_proof(
            session,
            income_proof,
            {**income_proof.content, "as_of": now.isoformat()},
            now,
            operation.id,
        )
        _epochs(session, operation.user_id, now, operation.id)
        receipt = ActionReceipt(
            id=uuid5(operation.id, "receipt"),
            user_id=operation.user_id,
            created_at=now,
            action_plan_id=action.id,
            attempt_number=1,
            receipt_ref=f"bank-operation:{operation.id}",
            status="SUCCEEDED",
            executed_cents=command.effect.amount_cents,
            fee_cents=0,
            loss_cents=0,
            response={
                "bank_operation_id": str(operation.id),
                "posting_ids": sorted(str(row.id) for row in rows),
                "transaction_ids": sorted(str(value) for value in transactions),
            },
            occurred_at=operation.settled_at,
            reconciled_at=now,
        )
        session.add(receipt)
        action.status = "SUCCEEDED"
        session.flush()
        refresh_execution_exposure(
            session,
            operation.user_id,
            now,
            operation.id,
            declarations={
                action.id: {
                    "action_id": str(action.id),
                    "state": "EXECUTION_SETTLED",
                    "receipt_id": str(receipt.id),
                }
            },
        )
        record_action_projected(session, action, operation, receipt, now)
        verify_goal_release_receipt(session, operation, receipt, now)
        return receipt


def _record_unknown(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> None:
    with Session(engine) as session, session.begin():
        _user(session, user_id)
        action = session.get(ActionPlan, action_id)
        if (
            action is None
            or action.user_id != user_id
            or action.status in {"SUCCEEDED", "RECONCILED"}
        ):
            return
        _transition(session, action, "UNKNOWN", now, "GOAL_RELEASE_ORIGINAL_RESULT_UNKNOWN")
        # Deliberately no no-effect/reservation assertion. Financial readers keep
        # the original unknown bank/projection gap until same-key reconciliation.


def execute_goal_release_execution(
    engine: Engine, user_id: UUID, action_id: UUID, body: GoalReleaseExecuteRequest, now: datetime
) -> GoalReleaseActionResponse:
    body = GoalReleaseExecuteRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id):
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            action = writer.get(ActionPlan, action_id)
            if action is None or action.user_id != user_id:
                raise _error("原回拨行动不存在", "NOT_FOUND", 404)
            original = _read_action(writer, user_id, action_id, now)
            validate_goal_release_execute_request(body, original.original_command)
            if original.service_receipt_verified:
                return original
            operation = writer.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                )
            )
            if operation is None:
                if action.status not in {"PLANNED", "AUTHORIZED", "SUBMITTED"}:
                    raise _error(
                        "原结果未知或已失效；不能凭无当前行重复提交", "INVALID_ACTION_STATE"
                    )
                _fresh_candidate(engine, action, now)
                _confirm_in_transaction(writer, action, original.original_command, now)
                _transition(writer, action, "SUBMITTED", now, "GOAL_RELEASE_PENDING_UNRESERVED")
                _epochs(writer, user_id, now, action_id)
                refresh_execution_exposure(
                    writer,
                    user_id,
                    now,
                    action_id,
                    declarations={
                        action_id: {
                            "action_id": str(action_id),
                            "state": "RELEASE_PENDING_UNRESERVED",
                        }
                    },
                )
        # Application submit has committed. Bank is a distinct original transaction.
        try:
            process_goal_release_bank(engine, user_id, action_id, now)
        except PolicyLifecycleError:
            # A known read-only refusal before bank is not an economic failure.
            # Keep the original request for review; never generate a replacement key.
            raise
        except Exception:
            try:
                _record_unknown(engine, user_id, action_id, now)
            except Exception:
                pass  # Preserve the actual original bank exception as first cause.
            raise
        try:
            with Session(engine) as projection, projection.begin():
                _user(projection, user_id)
                operation = projection.scalar(
                    select(BankOperation).where(
                        BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                    )
                )
                if operation is None:
                    raise _error("独立银行原件尚不可读", "GOAL_RELEASE_BANK_RESULT_UNKNOWN")
                project_goal_release_execution(projection, operation, now)
                return _read_action(projection, user_id, action_id, now)
        except Exception:
            try:
                _record_unknown(engine, user_id, action_id, now)
            except Exception:
                pass  # Never replace a committed bank fact or original projection cause.
            raise
