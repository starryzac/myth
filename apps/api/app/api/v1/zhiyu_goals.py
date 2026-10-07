"""Thin Next adapters; exact originals stay in the original verified services."""

from collections import defaultdict
from typing import Annotated, Any, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.full_goal_conflicts import GoalRepairPreviewBody
from app.api.v1.full_goals import FullGoalConfirmationRequest, FullGoalPreviewRequest
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.domain.full_goal_adjustments import GoalAdjustmentRequest
from app.domain.full_joint_goal_execution import (
    FullJointFrozenPlan,
    FullJointGoalConfirmRequest,
    FullJointGoalExecuteRequest,
    FullJointGoalPrepareRequest,
)
from app.domain.local_actor_session_types import require_local_user
from app.services.audit_chain import audit_read_scope
from app.services.full_goal_adjustments import (
    preview_current_goal_adjustments,
    read_goal_adjustments,
)
from app.services.full_goal_commands import lookup_full_goal_command
from app.services.full_goal_conflicts import preview_full_goal_repairs, read_full_goal_conflicts
from app.services.full_goals import (
    confirm_full_goal_model,
    preview_full_goal_model,
    read_full_goal_model,
)
from app.services.full_joint_goal_execution import preview_full_joint_goal_execution
from app.services.full_joint_goal_execution_dispatch import (
    confirm_full_joint_goal_execution,
    execute_full_joint_goal_child,
    prepare_full_joint_goal_execution,
)
from app.services.full_joint_goal_execution_store import (
    FullJointGoalExecutionResponse,
    lookup_full_joint_goal_execution,
    read_full_joint_goal_execution,
    read_joint_plan_original,
)
from app.services.full_joint_goal_planning import full_joint_goal_planning
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import serial_user
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from fastapi import APIRouter, Depends, Path, Query, Request


def isolated_goals(request: Request, engine: EngineDependency) -> None:
    if request.method == "POST" and request.url.path in {
        "/api/v1/zhiyu-next/goals/joint/preview",
        "/api/v1/zhiyu-next/goals/joint/prepare",
    }:
        require_zhiyu_next_engine(engine, check_connection=True)
        keys = [key for key, _ in request.query_params.multi_items()]
        if len(keys) != len(set(keys)) or not set(keys) <= {"archive_protocol"}:
            raise PolicyLifecycleError("UNSUPPORTED_QUERY", "只接受明确的原件协议选择", 422)
    elif request.method == "GET" and request.url.path == "/api/v1/zhiyu-next/goals/planning":
        require_zhiyu_next_engine(engine, check_connection=True)
        keys = [key for key, _ in request.query_params.multi_items()]
        if len(keys) != len(set(keys)) or not set(keys) <= {"point_offset", "point_limit"}:
            raise PolicyLifecycleError("UNSUPPORTED_QUERY", "只接受时间线显示分页参数", 422)
    else:
        isolated(request, engine)


router = APIRouter(
    prefix="/api/v1/zhiyu-next/goals",
    tags=["知余多目标与年度安排"],
    dependencies=[Depends(isolated_goals)],
)
Key = Annotated[
    str, Path(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
]


def fixed_plan_summary(plan: FullJointFrozenPlan) -> dict[str, Any]:
    """Display all economic effects; no expansion or recalculation on the client."""
    return {
        "protocol": plan.protocol,
        "simulation": True,
        "bank_authority": False,
        "funds_reserved": False,
        "plan_id": str(plan.plan_id),
        "user_id": str(plan.user_id),
        "epoch_id": str(plan.epoch_id),
        "prepared_at": plan.prepared_at.isoformat(),
        "expires_at": plan.expires_at.isoformat(),
        "plan_hash": plan.plan_hash,
        "original_request": plan.inputs.request.model_dump(mode="json"),
        "total_allocation_cents": plan.total_allocation_cents,
        "execution_mode": plan.execution_mode,
        "cross_operation_atomicity": plan.cross_operation_atomicity,
        "allocation": plan.allocation.model_dump(mode="json"),
        "children": [child.model_dump(mode="json") for child in plan.children],
        "full_binding_hash": plan.full_binding_hash,
        "complete_original_retained": True,
        "current_execution_goal_limit": 8,
        "current_execution_validation": "DEVELOPMENT_PENDING_ACTUAL_JOINT_ACCEPTANCE",
    }


def execution_summary(actual: FullJointGoalExecutionResponse) -> dict[str, Any]:
    result = actual.model_dump(mode="json", exclude={"original_plan"})
    result["original_plan"] = fixed_plan_summary(actual.original_plan)
    result["protocol"] = "zhiyu-next-joint-execution-read-v1"
    return result


@router.get("/planning")
def planning(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    point_offset: Annotated[int, Query(ge=0, le=1098)] = 0,
    point_limit: Annotated[int, Query(ge=1, le=366)] = 93,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = full_joint_goal_planning(session, user.id, now)
        projection = actual.full_protection.projection
        annual = projection.full_annual_projection
        months: dict[str, list[Any]] = defaultdict(list)
        if annual is not None:
            for point in annual.calculation_trace:
                months[point.date.strftime("%Y-%m")].append(point)
        points = annual.calculation_trace if annual is not None else []
        candidate = actual.binding.candidate if actual.binding is not None else None
        return {
            **identity(session, user.id, engine),
            "protocol": "zhiyu-next-joint-planning-read-v1",
            "planning_only": True,
            "grants_authority": False,
            "as_of": actual.as_of.isoformat(),
            "state": actual.state,
            "reasons": actual.reasons,
            "input_hash": actual.input_hash,
            "funds_scope": actual.funds_scope,
            "registered_goal_count": actual.original_joint.registered_goal_count,
            "included_goal_ids": [str(value) for value in actual.original_joint.included_goal_ids],
            "uncovered_goal_ids": [
                str(value) for value in actual.original_joint.uncovered_goal_ids
            ],
            "goals": [goal.model_dump(mode="json") for goal in candidate.goals]
            if candidate
            else [],
            "allocation": actual.allocation.model_dump(mode="json") if actual.allocation else None,
            "conflict": actual.conflict.model_dump(mode="json") if actual.conflict else None,
            "annual": {
                "horizon_days": 365,
                "calculation_points_total": len(points),
                "all_points_used_for_safety": True,
                "boundary_hash": annual.boundary_hash if annual else None,
                "state": projection.status,
                "safe_idle_cents": annual.safe_idle_cents if annual else None,
                "minimum_margin_cents": annual.minimum_margin_cents if annual else None,
                "future_income_in_current_cash_cents": (
                    projection.future_income_in_current_cash_cents
                ),
                "future_income_status": projection.future_income_status,
                "months": [
                    {
                        "month": month,
                        "points_count": len(rows),
                        "minimum_margin_cents": min(point.margin_cents for point in rows),
                        "maximum_protected_cents": max(
                            sum(point.protected_cents_by_reason.values()) for point in rows
                        ),
                        "minimum_cash_cents": min(point.cash_cents for point in rows),
                    }
                    for month, rows in sorted(months.items())
                ],
                "point_offset": point_offset,
                "point_limit": point_limit,
                "points": [
                    point.model_dump(mode="json")
                    for point in points[point_offset : point_offset + point_limit]
                ],
                "display_pagination_only": True,
            },
            "limitations": actual.limitations,
            "current_execution_goal_limit": 8,
            "current_execution_validation": "DEVELOPMENT_PENDING_ACTUAL_JOINT_ACCEPTANCE",
        }


@router.post("/joint/preview")
def preview(
    body: FullJointGoalPrepareRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    archive_protocol: Annotated[Literal["V3", "V4"], Query()] = "V3",
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = preview_full_joint_goal_execution(
            session, user.id, body, now, archive=True, source_dag=archive_protocol == "V4"
        )
        result = actual.model_dump(mode="json", exclude={"plan"})
        result["plan"] = fixed_plan_summary(actual.plan) if actual.plan is not None else None
        return {**identity(session, user.id, engine), "preview": result}


@router.post("/joint/prepare")
def prepare(
    body: FullJointGoalPrepareRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
    archive_protocol: Annotated[Literal["V3", "V4"], Query()] = "V3",
) -> dict[str, Any]:
    with serial_user(engine, user.id):
        actual = prepare_full_joint_goal_execution(
            engine, user.id, body, actor, now, archive=True, source_dag=archive_protocol == "V4"
        )
        return {**identity(session, user.id, engine), "execution": execution_summary(actual)}


@router.get("/joint/by-key/{key}")
def by_key(
    key: Key,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = lookup_full_joint_goal_execution(session, user.id, key, now)
        result = actual.model_dump(mode="json", exclude={"original"})
        result["original"] = (
            execution_summary(actual.original) if actual.original is not None else None
        )
        return {**identity(session, user.id, engine), **result}


@router.get("/joint/{plan_id}")
def original(
    plan_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = read_full_joint_goal_execution(session, user.id, plan_id, now)
        return {**identity(session, user.id, engine), "execution": execution_summary(actual)}


@router.get("/joint/{plan_id}/archive")
def archive_original(
    plan_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        parent, plan, _, _ = read_joint_plan_original(session, user.id, plan_id, now)
        return {
            **identity(session, user.id, engine),
            "archive": parent.plan,
            "plan_hash": plan.plan_hash,
            "bank_authority": False,
            "original_request_hash": parent.request_hash,
        }


@router.post("/joint/{plan_id}/confirm")
def confirm(
    plan_id: UUID,
    body: FullJointGoalConfirmRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    with serial_user(engine, user.id):
        actual = confirm_full_joint_goal_execution(engine, user.id, plan_id, body, actor, now)
        return {**identity(session, user.id, engine), "execution": execution_summary(actual)}


@router.post("/joint/{plan_id}/execute-child")
def execute_child(
    plan_id: UUID,
    body: FullJointGoalExecuteRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    with serial_user(engine, user.id):
        actual = execute_full_joint_goal_child(engine, user.id, plan_id, body, actor, now)
        return {**identity(session, user.id, engine), "execution": execution_summary(actual)}


@router.get("/conflicts")
def conflicts(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = read_full_goal_conflicts(session, user.id, now)
        return {**identity(session, user.id, engine), "conflicts": actual.model_dump(mode="json")}


@router.post("/repairs/preview")
def repairs_preview(
    body: GoalRepairPreviewBody,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = preview_full_goal_repairs(session, user.id, body.domain_request(), now)
        result = actual.model_dump(mode="json")
        for row in result["version_previews"]:
            row["preview_endpoint"] = f"/api/v1/zhiyu-next/goals/models/{row['goal_id']}/preview"
            row["confirmation_endpoint"] = (
                f"/api/v1/zhiyu-next/goals/models/{row['goal_id']}/confirm"
            )
        return {**identity(session, user.id, engine), "repair": result}


@router.get("/adjustments")
def adjustments(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = read_goal_adjustments(session, user.id, now)
        return {**identity(session, user.id, engine), "adjustments": actual.model_dump(mode="json")}


@router.post("/adjustments/preview")
def adjustments_preview(
    body: GoalAdjustmentRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = preview_current_goal_adjustments(session, user.id, body, now)
        result = actual.model_dump(mode="json")
        for row in result["version_previews"]:
            row["preview_endpoint"] = f"/api/v1/zhiyu-next/goals/models/{row['goal_id']}/preview"
            row["confirmation_endpoint"] = (
                f"/api/v1/zhiyu-next/goals/models/{row['goal_id']}/confirm"
            )
        return {**identity(session, user.id, engine), "repair": result}


@router.get("/models/{goal_id}")
def goal_model(
    goal_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = read_full_goal_model(session, user.id, goal_id, now)
        return {**identity(session, user.id, engine), "model": actual.model_dump(mode="json")}


@router.post("/models/{goal_id}/preview")
def goal_model_preview(
    goal_id: UUID,
    body: FullGoalPreviewRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = preview_full_goal_model(
            session, user.id, goal_id, body.expected_version_id, body.configuration, now
        )
        return {**identity(session, user.id, engine), "preview": actual.model_dump(mode="json")}


@router.post("/models/{goal_id}/confirm")
def goal_model_confirm(
    goal_id: UUID,
    body: FullGoalConfirmationRequest,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    actor: PrincipalDependency,
) -> dict[str, Any]:
    with serial_user(engine, user.id):
        require_local_user(actor, user.id, now)
        actual = confirm_full_goal_model(
            session,
            user.id,
            goal_id,
            body.expected_version_id,
            body.expected_epoch_id,
            body.configuration,
            body.reviewed_full_hash,
            body.reviewed_base_hash,
            body.accepted,
            body.reason,
            body.idempotency_key,
            now,
        )
        return {
            **identity(session, user.id, engine),
            "confirmation": actual.model_dump(mode="json"),
        }


@router.get("/models/{goal_id}/commands/by-key/{key}")
def goal_model_command(
    goal_id: UUID,
    key: Annotated[str, Path(min_length=1, max_length=150)],
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = lookup_full_goal_command(session, user.id, goal_id, key, now)
        return {**identity(session, user.id, engine), **actual.model_dump(mode="json")}
