"""Closed USER entry to paired Goal transfer, keeping original request locators."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import ClientRequest, EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.models import ActionPlan
from app.domain.full_goal_release_execution import Hash, PositiveCents
from app.domain.full_goal_transfer import GoalTransferPolicyConfiguration, goal_transfer_effect_hash
from app.domain.local_actor_session_types import require_local_user
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.demo_console import _epoch
from app.services.full_goal_release_authorization import _reader
from app.services.full_goal_transfer import (
    GoalTransferExecuteRequest,
    GoalTransferPrepareRequest,
    GoalTransferPreviewRequest,
    execute_goal_transfer,
    prepare_goal_transfer,
    preview_goal_transfer,
    read_goal_transfer_action,
    read_goal_transfer_by_key,
    read_goal_transfer_preview_by_key,
)
from app.services.full_goals import read_full_goal_model
from app.services.full_policy_lifecycle import read_full_policy
from app.services.goals import list_goals
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PREFIX = "/api/v1/zhiyu-next/goal-transfers"
PROTOCOL = "zhiyu-next-goal-transfer-read-v1"
router = APIRouter(prefix=PREFIX, tags=["知余目标间调拨"], dependencies=[Depends(isolated)])


def failure(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("GOAL_TRANSFER_BINDING_MISMATCH", message, 409)


def require_user(principal: Any, user_id: UUID, now: datetime) -> None:
    try:
        require_local_user(principal, user_id, now)
    except ValueError:
        raise HTTPException(403, "需要当前本人明确确认会话") from None


def native_key(epoch: UUID, phase: Literal["preview", "prepare"], key: UUID) -> str:
    return f"zhiyu-next:{epoch}:goal-transfer-{phase}:{key}"


class PreviewBody(ClientRequest):
    expected_epoch_id: UUIDReference
    policy_id: UUIDReference
    expected_policy_version_id: UUIDReference
    source_goal_id: UUIDReference
    expected_source_policy_version_id: UUIDReference
    target_goal_id: UUIDReference
    expected_target_policy_version_id: UUIDReference
    amount_cents: PositiveCents


class PrepareBody(ClientRequest):
    expected_epoch_id: UUIDReference
    preview_id: UUIDReference
    reviewed_scope_hash: Hash
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit paired USER confirmation required")
        return value


class ExecuteBody(ClientRequest, GoalTransferExecuteRequest):
    client_request_id: UUIDReference


def envelope(
    session: Session, user_id: UUID, engine: Engine, kind: str, native: Any
) -> dict[str, Any]:
    return {
        **identity(session, user_id, engine),
        "protocol": PROTOCOL,
        "user_id": str(user_id),
        "kind": kind,
        "native": native.model_dump(mode="json") if isinstance(native, BaseModel) else native,
        "bank_authority": False,
        "receipt_is_current_authority": False,
    }


def current_epoch(session: Session, user_id: UUID, expected: UUID) -> None:
    if _epoch(session, user_id) != expected:
        raise PolicyLifecycleError("STALE_AUDIT_EPOCH", "当前周期已变化，请保留原请求", 409)


def verified_transfer_activity(
    session: Session, user_id: UUID, action: ActionPlan, now: datetime
) -> dict[str, Any]:
    actual = read_goal_transfer_action(session, user_id, action.id, now)
    command = actual.original_command
    effect = command.effect
    if (
        action.user_id != user_id
        or action.action_type != "TRANSFER_GOAL"
        or configuration_hash(action.request) != action.request_hash
        or actual.user_id != user_id
        or actual.action_id != action.id
        or effect.user_id != user_id
        or effect.operation_id != action.id
        or command.effect_hash != goal_transfer_effect_hash(effect)
        or action.request.get("goal_transfer_execution") != command.model_dump(mode="json")
        or actual.service_receipt_verified
        and (actual.original_receipt is None or actual.unresolved)
    ):
        raise failure("原目标调拨命令、所有者、效果或回执未核实")
    return {
        "id": str(action.id),
        "at": action.created_at.isoformat(),
        "intent": "目标之间调拨",
        "authorization": "本次明确的来源释放与目标接收确认",
        "decision": "原调拨回执已独立核实"
        if actual.service_receipt_verified
        else "同一原调拨正在核实，请保留原请求"
        if actual.unresolved
        else "原调拨尚未取得完成回执",
        "amount_cents": effect.amount_cents,
        "status": actual.original_action_status,
        "action_id": str(action.id),
        "scenario": None,
        "goal_id": str(effect.source_goal_id),
    }


@router.get("/context/{policy_id}")
def context_read(
    policy_id: UUID, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    with _reader(engine) as session:
        policy = read_full_policy(session, user.id, policy_id, now)
        if (
            policy.template_name != "CrossGoalReallocationPolicy"
            or policy.current_version.configuration.get("scope_version") != "GOAL_TRANSFER_V1"
        ):
            raise failure("请选择目标间调拨规则")
        config = GoalTransferPolicyConfiguration.model_validate_json(
            json.dumps(policy.current_version.configuration)
        )
        goals = {goal.id: goal for goal in list_goals(session, user.id).items}

        def bindings(ids: list[UUID]) -> list[dict[str, Any]]:
            result = []
            for goal_id in ids:
                if goal_id not in goals:
                    raise failure("调拨目标不属于当前用户")
                result.append(
                    {
                        "goal": goals[goal_id].model_dump(mode="json"),
                        "model": read_full_goal_model(session, user.id, goal_id, now).model_dump(
                            mode="json"
                        ),
                    }
                )
            return result

        actions = list(
            session.scalars(
                select(ActionPlan)
                .where(ActionPlan.user_id == user.id, ActionPlan.action_type == "TRANSFER_GOAL")
                .order_by(ActionPlan.created_at, ActionPlan.id)
                .limit(1001)
            )
        )
        if len(actions) > 1000:
            raise failure("原调拨记录超出此有限入口，不能截断或推断")
        originals = []
        unresolved = False
        for action in actions:
            original = read_goal_transfer_action(session, user.id, action.id, now)
            unresolved = unresolved or original.unresolved
            if (
                original.original_command.effect.scope.policy.policy_id == policy_id
                and original.original_command.effect.epoch_id == _epoch(session, user.id)
            ):
                originals.append(original.model_dump(mode="json"))
        return envelope(
            session,
            user.id,
            engine,
            "CONTEXT",
            {
                "policy": policy.model_dump(mode="json"),
                "sources": bindings(config.source_goal_ids),
                "targets": bindings(config.target_goal_ids),
                "originals": originals,
                "any_unresolved": unresolved,
            },
        )


@router.post("/preview")
def preview(
    body: PreviewBody,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    values = body.model_dump(mode="json", exclude={"client_request_id"})
    values["idempotency_key"] = native_key(
        body.expected_epoch_id, "preview", body.client_request_id
    )
    native = preview_goal_transfer(
        engine, user.id, GoalTransferPreviewRequest.model_validate_json(json.dumps(values)), now
    )
    with _reader(engine) as session:
        return envelope(session, user.id, engine, "PREVIEW", native)


@router.get("/previews/{epoch}/{key}")
def preview_read(
    epoch: UUID,
    key: UUID,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with _reader(engine) as session:
        native = read_goal_transfer_preview_by_key(
            session, user.id, epoch, native_key(epoch, "preview", key), now
        )
        return envelope(session, user.id, engine, "PREVIEW_LOOKUP", native)


@router.post("/prepare")
def prepare(
    body: PrepareBody,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    principal: PrincipalDependency,
) -> dict[str, Any]:
    require_user(principal, user.id, now)
    with _reader(engine) as session:
        current_epoch(session, user.id, body.expected_epoch_id)
    values = body.model_dump(mode="json", exclude={"client_request_id"})
    values["idempotency_key"] = native_key(
        body.expected_epoch_id, "prepare", body.client_request_id
    )
    native = prepare_goal_transfer(
        engine,
        user.id,
        GoalTransferPrepareRequest.model_validate_json(json.dumps(values)),
        now,
        principal,
    )
    with _reader(engine) as session:
        return envelope(session, user.id, engine, "ACTION", native)


@router.get("/operations/{epoch}/{key}")
def action_lookup(
    epoch: UUID,
    key: UUID,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with _reader(engine) as session:
        return envelope(
            session,
            user.id,
            engine,
            "ACTION_LOOKUP",
            read_goal_transfer_by_key(
                session, user.id, epoch, native_key(epoch, "prepare", key), now
            ),
        )


@router.get("/actions/{action_id}")
def action_read(
    action_id: UUID,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with _reader(engine) as session:
        return envelope(
            session,
            user.id,
            engine,
            "ACTION",
            read_goal_transfer_action(session, user.id, action_id, now),
        )


@router.post("/actions/{action_id}/execute")
def execute(
    action_id: UUID,
    body: ExecuteBody,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    principal: PrincipalDependency,
) -> dict[str, Any]:
    require_user(principal, user.id, now)
    with _reader(engine) as session:
        current_epoch(session, user.id, body.expected_epoch_id)
    native_body = GoalTransferExecuteRequest.model_validate_json(
        body.model_dump_json(exclude={"client_request_id"})
    )
    native = execute_goal_transfer(engine, user.id, action_id, native_body, now, principal)
    with _reader(engine) as session:
        return envelope(session, user.id, engine, "ACTION", native)
