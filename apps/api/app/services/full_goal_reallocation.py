"""Current read-only emergency repair facts; original goal authority stays locked."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.db.models import ActionResourceReservation, Goal, User
from app.domain.boundary_types import BoundaryModel, BoundaryPoint, SourceIssue
from app.domain.full_goal_reallocation import (
    Hash,
    ReallocationDecision,
    ReallocationDecisionInput,
    ReallocationPreviewRequest,
    RepairFinancialFacts,
    RepairGoalFacts,
    RepairPolicyFacts,
    RepairUsageFacts,
    decide_cash_reallocation,
)
from app.domain.full_policy_configuration import (
    CrossGoalReallocationPolicy,
    EmergencyCondition,
    LongTermGoalPolicy,
)
from app.domain.full_reconciliation import ReconciliationGoal
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.full_goals import FullGoalModelResponse, read_full_goal_model
from app.services.full_policy_lifecycle import FullPolicyView, _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.full_reconciliation import full_reconciliation
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class FullGoalReallocationPreview(BoundaryModel):
    schema_version: Literal["full-goal-reallocation-preview-v1"] = (
        "full-goal-reallocation-preview-v1"
    )
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    bank_authority: Literal[False] = False
    financial_grant_created: Literal[False] = False
    original_goal_bridge_cross_enabled: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    original_request: ReallocationPreviewRequest
    policy: FullPolicyView
    goal_model: FullGoalModelResponse | None
    goal_ownership: ReconciliationGoal | None
    original_current_protection_point: BoundaryPoint | None
    decision: ReallocationDecision
    reconciliation_input_hash: Hash
    full_protection_input_hash: Hash
    source_binding_hash: Hash
    source_issues: list[SourceIssue]
    limitations: list[str]


def preview_goal_reallocation(
    session: Session,
    user_id: UUID,
    request: ReallocationPreviewRequest,
    now: datetime,
) -> FullGoalReallocationPreview:
    _read_snapshot(session)
    now = _now(now)
    user = session.scalar(select(User).where(User.id == user_id))
    epoch = current_audit_epoch(session, user_id)
    if user is None or not user.is_simulated or epoch is None or epoch.status != "OPEN":
        raise PolicyLifecycleError(
            "CURRENT_OWNER_EPOCH_NOT_AVAILABLE", "缺少当前模拟用户开放epoch", 409
        )
    if epoch.id != request.expected_epoch_id:
        raise PolicyLifecycleError("STALE_AUDIT_EPOCH", "当前epoch已变化，请重新读取", 409)
    goal = session.scalar(
        select(Goal).where(Goal.user_id == user_id, Goal.id == request.source_goal_id)
    )
    if goal is None:
        raise PolicyLifecycleError("NOT_FOUND", "当前用户源目标不存在", 404)
    if goal.policy_version_id != request.expected_goal_policy_version_id:
        raise PolicyLifecycleError("STALE_POLICY_VERSION", "源目标原版本已变化", 409)
    with session.no_autoflush, historical_ledger_scope(session):
        return _preview(session, user_id, request, now, user.timezone, goal)


def _preview(
    session: Session,
    user_id: UUID,
    request: ReallocationPreviewRequest,
    now: datetime,
    timezone: str,
    goal: Goal,
) -> FullGoalReallocationPreview:
    if timezone not in {"Asia/Shanghai", "UTC"}:
        raise PolicyLifecycleError("UNSUPPORTED_USER_TIMEZONE", "原边界不支持当前用户时区", 409)
    zone: Literal["Asia/Shanghai", "UTC"] = "UTC" if timezone == "UTC" else "Asia/Shanghai"
    policy = read_full_policy(session, user_id, request.policy_id, now)
    if policy.template_name != "CrossGoalReallocationPolicy":
        raise PolicyLifecycleError(
            "INVALID_REALLOCATION_POLICY", "仅显式回拨有限模板可供本预览", 422
        )
    current = policy.current_version
    if current.version_id != request.expected_policy_version_id:
        raise PolicyLifecycleError("STALE_POLICY_VERSION", "回拨规则原版本已变化", 409)
    configuration = CrossGoalReallocationPolicy.model_validate(current.configuration)
    if configuration_hash(configuration.model_dump(mode="json")) != current.content_hash:
        raise PolicyLifecycleError(
            "INVALID_FULL_POLICY_HISTORY", "回拨规则原canonical hash不一致", 409
        )
    reconciliation = full_reconciliation(session, user_id, now)
    protection = compute_full_annual_protection(session, user_id, now)
    issues: list[SourceIssue] = []

    def issue(code: str, entity: str, identity: object) -> None:
        issues.append(SourceIssue(code=code, entity_type=entity, entity_id=str(identity)))

    if (
        protection.user_id != user_id
        or reconciliation.user_id != user_id
        or protection.as_of != now
        or reconciliation.as_of != now
    ):
        raise PolicyLifecycleError(
            "REALLOCATION_SOURCE_BINDING_MISMATCH", "原保护与银行来源时点/owner不一致", 409
        )
    point_candidates = [
        row
        for row in protection.projection.original_execution_view.calculation_trace
        if row.day == 0 and row.phase == "BEFORE_PAYMENT"
    ]
    point = point_candidates[0] if len(point_candidates) == 1 else None
    complete = all(row.complete for row in reconciliation.inventory)
    reconciled = (
        complete
        and reconciliation.bank_ledger_verified
        and reconciliation.current_application_projection_matched
        and reconciliation.audit.status == "VALID"
    )
    if not reconciled:
        issue(
            "CURRENT_BANK_APPLICATION_OR_AUDIT_NOT_VERIFIED",
            "reconciliation",
            reconciliation.input_hash,
        )
    if any(row.state != "MATCHED" for row in reconciliation.account_cash):
        issue("CURRENT_BANK_CASH_NOT_VERIFIED", "reconciliation", reconciliation.input_hash)
    for ownership in reconciliation.goal_ownership:
        if (
            not ownership.current_ownership_proof_verified
            or ownership.cash.state != "MATCHED"
            or ownership.principal.state != "MATCHED"
        ):
            issue("CURRENT_COMPLETE_GOAL_OWNERSHIP_NOT_VERIFIED", "goal", ownership.goal_id)
    if (
        protection.source_issues
        or protection.projection.original_execution_view.status == "INSUFFICIENT_EVIDENCE"
        or not protection.audit.complete
        or protection.audit.status != "VALID"
    ):
        issue(
            "ORIGINAL_CURRENT_CORE_PROTECTION_NOT_VERIFIED", "protection", protection.input_digest
        )
    if point is None:
        issue("ORIGINAL_CURRENT_PROTECTION_POINT_MISSING", "protection", protection.input_digest)
    core: dict[EmergencyCondition, int] = {
        "HARD_OBLIGATION_SHORTFALL": point.protected_cents_by_reason.get("obligations", 0)
        if point
        else 0,
        "LIVING_RESERVE_SHORTFALL": point.protected_cents_by_reason.get("living", 0)
        if point
        else 0,
        "EMERGENCY_BUFFER_SHORTFALL": point.protected_cents_by_reason.get("emergency", 0)
        if point
        else 0,
    }
    if (
        point is not None
        and not {"obligations", "living", "emergency", "goal_cash", "goal_minimum"}
        <= point.protected_cents_by_reason.keys()
    ):
        issue("ORIGINAL_CORE_FLOOR_COMPONENT_MISSING", "protection", protection.input_digest)
    bank_cash = sum(
        row.bank_cents for row in reconciliation.account_cash if row.bank_cents is not None
    )
    owned_cash = sum(
        row.cash.bank_cents
        for row in reconciliation.goal_ownership
        if row.cash.bank_cents is not None
    )
    locked_cash = point.protected_cents_by_reason.get("goal_cash", 0) if point else 0
    if point is not None and (point.cash_cents != bank_cash or locked_cash < owned_cash):
        issue(
            "CURRENT_PROTECTION_BANK_CASH_BINDING_MISMATCH", "protection", protection.input_digest
        )
    # A current pending claim can overlap goal-owned cash. Do not double subtract
    # it then advertise an artificially enlarged "unique" emergency minimum.
    query = select(ActionResourceReservation).where(
        ActionResourceReservation.user_id == user_id, ActionResourceReservation.status == "RESERVED"
    )
    reservation_count = session.scalar(
        select(func.count())
        .select_from(ActionResourceReservation)
        .where(
            ActionResourceReservation.user_id == user_id,
            ActionResourceReservation.status == "RESERVED",
        )
    )
    reservations = list(session.scalars(query.order_by(ActionResourceReservation.id).limit(10000)))
    if type(reservation_count) is not int or reservation_count != len(reservations):
        issue("CURRENT_RESERVATION_INVENTORY_INCOMPLETE", "reservation", user_id)
    if reservations:
        issue("CURRENT_INFLIGHT_RESOURCE_ATTRIBUTION_NOT_IMPLEMENTED", "reservation", user_id)
    reserved_cash = sum(row.amount_cents for row in reservations if row.resource_kind == "CASH")
    reserved_goal = sum(
        row.amount_cents
        for row in reservations
        if row.resource_kind == "GOAL_CASH" and row.resource_key == str(goal.id)
    )
    actual_ownership = next(
        (row for row in reconciliation.goal_ownership if row.goal_id == goal.id), None
    )
    model = None
    minimum = None
    try:
        model = read_full_goal_model(session, user_id, goal.id, now)
        if model.status == "VERIFIED" and model.full_configuration is not None:
            full = LongTermGoalPolicy.model_validate(model.full_configuration)
            minimum = full.minimum_guarantee_cents
        else:
            issue("FULL_GOAL_MINIMUM_MODEL_MISSING", "goal", goal.id)
    except PolicyLifecycleError as error:
        issue(error.code, "goal_model", goal.id)
    full_current = protection.projection.full_annual_projection
    full_today = (
        [
            row
            for row in full_current.calculation_trace
            if row.day == 0 and row.phase == "BEFORE_PAYMENT"
        ]
        if full_current
        else []
    )
    other_full = (
        sum(
            value
            for key, value in full_today[0].protected_cents_by_reason.items()
            if key not in {"obligations", "living", "emergency", "goal_cash", "goal_minimum"}
        )
        if len(full_today) == 1
        else 0
    )
    if len(full_today) == 1 and full_today[0].protected_cents_by_reason.get(
        "pending_cash_reservations", 0
    ):
        issue("CURRENT_INFLIGHT_EXPOSURE_ATTRIBUTION_NOT_IMPLEMENTED", "reservation", user_id)
    if protection.projection.status == "UNKNOWN":
        issue(
            "FULL_CURRENT_ADDITIONAL_PROTECTION_NOT_VERIFIED", "protection", protection.input_digest
        )
    financial_issues = [
        row
        for row in issues
        if row.entity_type in {"reconciliation", "protection", "reservation", "goal"}
        and row.code != "FULL_GOAL_MINIMUM_MODEL_MISSING"
    ]
    # There is no original cross-goal settlement/usage protocol today. A missing
    # all-policy lifetime counter is never inferred as zero from no success logs.
    data = ReallocationDecisionInput(
        user_id=user_id,
        epoch_id=request.expected_epoch_id,
        as_of=now,
        timezone=zone,
        policy=RepairPolicyFacts(
            policy_id=policy.policy_id,
            version_id=current.version_id,
            epoch_id=policy.epoch_id,
            content_hash=current.content_hash,
            configuration=configuration,
            current_confirmed=policy.planning_confirmation_valid,
            references_current=policy.reference_validation == "CURRENT",
            effective_status=policy.effective_status,
        ),
        goal=RepairGoalFacts(
            goal_id=goal.id,
            account_id=goal.account_id,
            original_policy_version_id=goal.policy_version_id,
            cash_owned_cents=actual_ownership.cash.bank_cents if actual_ownership else None,
            principal_owned_cents=actual_ownership.principal.bank_cents
            if actual_ownership
            else None,
            minimum_guarantee_cents=minimum,
            reserved_goal_cash_cents=reserved_goal,
            ownership_verified=actual_ownership is not None
            and actual_ownership.current_ownership_proof_verified
            and actual_ownership.cash.state == actual_ownership.principal.state == "MATCHED",
            model_verified=minimum is not None,
        ),
        financial=RepairFinancialFacts(
            cash_cents=bank_cash,
            locked_goal_cash_cents=locked_cash,
            reserved_cash_cents=reserved_cash,
            required_by_condition=core,
            other_full_protection_cents=other_full,
            verified=not financial_issues and point is not None,
        ),
        usage=RepairUsageFacts(status="MISSING", consumed_cents=None, source_refs=[]),
        source_issues=[row.code for row in issues],
    )
    decision = decide_cash_reallocation(data)
    source_hash = configuration_hash(
        {
            "user_id": str(user_id),
            "epoch_id": str(request.expected_epoch_id),
            "as_of": now.isoformat(),
            "request": request.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "actual_goal": row_copy(goal),
            "model": model.model_dump(mode="json") if model else None,
            "reconciliation_input_hash": reconciliation.input_hash,
            "full_protection_input_hash": protection.input_digest,
            "reserved_count": reservation_count,
            "reserved_rows": [row_copy(row) for row in reservations],
            "decision_input_hash": decision.input_hash,
        }
    )
    return FullGoalReallocationPreview(
        user_id=user_id,
        epoch_id=request.expected_epoch_id,
        as_of=now,
        original_request=request,
        policy=policy,
        goal_model=model,
        goal_ownership=actual_ownership,
        original_current_protection_point=point,
        decision=decision,
        reconciliation_input_hash=reconciliation.input_hash,
        full_protection_input_hash=protection.input_digest,
        source_binding_hash=source_hash,
        source_issues=issues,
        limitations=[
            "CURRENT_CORE_OBLIGATIONS_LIVING_EMERGENCY_SCOPE_ONLY",
            "GOAL_CASH_OWNERSHIP_RELEASE_DOES_NOT_MOVE_PRINCIPAL_OR_ASSET_PLACEMENT",
            "MATHEMATICAL_MINIMUM_IS_NOT_A_FINANCIAL_PREAUTHORIZATION",
            "ORIGINAL_GOAL_BRIDGE_REJECTS_CROSS_GOAL_ENABLEMENT",
            "DEDICATED_CURRENT_GOAL_CONFIRMATION_GRANT_AND_LIFETIME_POLICY_USAGE_NOT_IMPLEMENTED",
            "ANY_CURRENT_INFLIGHT_RESOURCE_ATTRIBUTION_IS_UNKNOWN_NOT_DOUBLE_SUBTRACTED",
            "ADDITIONAL_FULL_FLOORS_FUTURE_RECOVERY_AND_INCOME_REATTRIBUTION_NOT_EXECUTABLE",
            "NO_BANK_EFFECT_RECEIPT_INCOME_FRAGMENT_OR_HISTORICAL_HASH_IS_CHANGED",
        ],
    )
