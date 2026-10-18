"""Owned Next entry to the existing dedicated emergency goal-cash consumer."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import ClientRequest, EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.models import Account, ActionPlan, EvidenceItem
from app.domain.full_goal_reallocation import ReallocationPreviewRequest
from app.domain.full_goal_release_authorization import (
    SOURCE,
    GoalReleaseAuthorization,
    ReleaseAuthorizationConfirmation,
    ReleaseAuthorizationPreviewRequest,
)
from app.domain.full_goal_release_execution import goal_release_effect_hash
from app.domain.local_actor_session_types import require_local_user
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.demo_console import _epoch
from app.services.full_goal_release_authorization import (
    _reader,
    confirm_release_authorization,
    preview_release_authorization,
    read_release_authorization,
)
from app.services.full_goal_release_execution import (
    GoalReleaseExecuteRequest,
    GoalReleasePrepareRequest,
    execute_goal_release_execution,
    prepare_goal_release_execution,
    preview_goal_release_execution,
    read_goal_release_by_key,
    read_goal_release_execution,
)
from app.services.full_goals import read_full_goal_model
from app.services.full_policy_lifecycle import read_full_policy
from app.services.goals import list_goals
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PREFIX = "/api/v1/zhiyu-next/goal-releases"
PROTOCOL = "zhiyu-next-goal-release-read-v1"
router = APIRouter(prefix=PREFIX, tags=["知余目标现金应急回拨"], dependencies=[Depends(isolated)])


def failure(message: str, code: str = "GOAL_RELEASE_BINDING_MISMATCH") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


def require_user(principal: Any, user_id: UUID, now: datetime) -> None:
    try:
        require_local_user(principal, user_id, now)
    except ValueError:
        raise HTTPException(403, "需要当前本人明确确认会话") from None


def native_key(epoch: UUID, phase: Literal["authorization", "prepare"], key: UUID) -> str:
    return f"zhiyu-next:{epoch}:goal-release-{phase}:{key}"


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_epoch_id: UUID
    expected_policy_version_id: UUID


class ConfirmBody(ClientRequest, PreviewBody):
    reviewed_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit USER confirmation is required")
        return value


class PrepareBody(ClientRequest, ReallocationPreviewRequest):
    client_request_id: UUIDReference
    authorization_epoch_id: UUIDReference
    authorization_idempotency_key: str = Field(min_length=1, max_length=150)
    destination_account_id: UUIDReference


class ExecuteBody(ClientRequest, GoalReleaseExecuteRequest):
    client_request_id: UUIDReference


def native_prepare(body: PrepareBody) -> GoalReleasePrepareRequest:
    value = body.model_dump(mode="json", exclude={"client_request_id"})
    value["idempotency_key"] = native_key(body.expected_epoch_id, "prepare", body.client_request_id)
    return GoalReleasePrepareRequest.model_validate_json(json.dumps(value))


def current_epoch(session: Session, user_id: UUID, expected: UUID) -> None:
    if _epoch(session, user_id) != expected:
        raise failure("当前周期已变化，请保留原请求", "STALE_AUDIT_EPOCH")


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


def verified_release_activity(
    session: Session, user_id: UUID, action: ActionPlan, now: datetime
) -> dict[str, Any]:
    """Never coerce this native effect into the unrelated generic BankCommand."""
    if (
        action.user_id != user_id
        or action.request.get("protocol") != "goal-release-action-v1"
        or configuration_hash(action.request) != action.request_hash
    ):
        raise failure("原回拨动作的归属或完整原载荷摘要未核实")
    actual = read_goal_release_execution(session, user_id, action.id, now)
    command = actual.original_command
    effect = command.effect
    if (
        actual.user_id != user_id
        or actual.action_id != action.id
        or effect.user_id != user_id
        or effect.operation_id != action.id
        or effect.epoch_id != actual.original_prepare_request.expected_epoch_id
        or command.effect_hash != goal_release_effect_hash(effect)
        or action.request.get("goal_release_execution") != command.model_dump(mode="json")
        or action.request.get("original_prepare_request")
        != actual.original_prepare_request.model_dump(mode="json")
        or actual.service_receipt_verified
        and (actual.original_receipt is None or actual.unresolved)
    ):
        raise failure("原回拨命令、完整效果或独立回执未核实")
    return {
        "id": str(action.id),
        "at": action.created_at.isoformat(),
        "intent": "目标现金应急回拨",
        "authorization": "专用应急释放范围与本次明确确认",
        "decision": "原回拨回执已独立核实"
        if actual.service_receipt_verified
        else "同一原回拨正在核实，不能另建替代动作"
        if actual.unresolved
        else "原回拨尚未取得完成回执",
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
            or policy.current_version.configuration.get("scope_version") is not None
        ):
            raise failure("请选择当前目标现金应急回拨规则")
        goals = {goal.id: goal for goal in list_goals(session, user.id).items}
        source_ids = policy.current_version.configuration.get("source_goal_ids", [])
        if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 32:
            raise failure("原目标范围不完整")
        sources = []
        for value in source_ids:
            goal_id = UUID(str(value))
            if goal_id not in goals:
                raise failure("原目标不属于当前用户")
            sources.append(
                {
                    "goal": goals[goal_id].model_dump(mode="json"),
                    "model": read_full_goal_model(session, user.id, goal_id, now).model_dump(
                        mode="json"
                    ),
                }
            )
        accounts = list(
            session.scalars(
                select(Account)
                .where(Account.user_id == user.id, Account.account_type == "CASH")
                .order_by(Account.id)
                .limit(1001)
            )
        )
        declarations = list(
            session.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.user_id == user.id, EvidenceItem.source_type == SOURCE)
                .order_by(EvidenceItem.created_at, EvidenceItem.id)
                .limit(1001)
            )
        )
        actions = list(
            session.scalars(
                select(ActionPlan)
                .where(ActionPlan.user_id == user.id)
                .order_by(ActionPlan.created_at, ActionPlan.id)
                .limit(1001)
            )
        )
        if max(len(accounts), len(declarations), len(actions)) > 1000:
            raise failure("原件数量超出此有限入口，不能截断或推断", "GOAL_RELEASE_INVENTORY_LIMIT")
        authorizations = []
        for declaration in declarations:
            original = GoalReleaseAuthorization.model_validate_json(json.dumps(declaration.content))
            if original.user_id != user.id:
                raise failure("原授权所有者不一致")
            if original.policy_id == policy_id and original.epoch_id == _epoch(session, user.id):
                lookup = read_release_authorization(
                    session, user.id, original.epoch_id, original.idempotency_key, now
                )
                if lookup.original is None:
                    raise failure("已保存专用授权缺少独立原件")
                authorizations.append(lookup.original.model_dump(mode="json"))
        originals = []
        any_unresolved = False
        for action in actions:
            if action.request.get("protocol") == "goal-release-action-v1":
                actual = read_goal_release_execution(session, user.id, action.id, now)
                any_unresolved = any_unresolved or actual.unresolved
                if (
                    actual.original_prepare_request.policy_id == policy_id
                    and actual.original_prepare_request.expected_epoch_id
                    == _epoch(session, user.id)
                ):
                    originals.append(actual.model_dump(mode="json"))
        return envelope(
            session,
            user.id,
            engine,
            "CONTEXT",
            {
                "policy": policy.model_dump(mode="json"),
                "sources": sources,
                "accounts": [{"id": str(account.id), "name": account.name} for account in accounts],
                "authorizations": authorizations,
                "actions": originals,
                "any_unresolved": any_unresolved,
            },
        )


@router.post("/authorizations/{policy_id}/preview")
def authorization_preview(
    policy_id: UUID,
    body: PreviewBody,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with _reader(engine) as session:
        current_epoch(session, user.id, body.expected_epoch_id)
        native = preview_release_authorization(
            session,
            user.id,
            policy_id,
            ReleaseAuthorizationPreviewRequest.model_validate_json(body.model_dump_json()),
            now,
        )
        return envelope(session, user.id, engine, "AUTHORIZATION_PREVIEW", native)


@router.post("/authorizations/{policy_id}/confirm")
def authorization_confirm(
    policy_id: UUID,
    body: ConfirmBody,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    principal: PrincipalDependency,
) -> dict[str, Any]:
    require_user(principal, user.id, now)
    with _reader(engine) as session:
        current_epoch(session, user.id, body.expected_epoch_id)
    value = body.model_dump(mode="json", exclude={"client_request_id"})
    value["idempotency_key"] = native_key(
        body.expected_epoch_id, "authorization", body.client_request_id
    )
    native = confirm_release_authorization(
        engine,
        user.id,
        policy_id,
        ReleaseAuthorizationConfirmation.model_validate_json(json.dumps(value)),
        now,
    )
    with _reader(engine) as session:
        return envelope(session, user.id, engine, "AUTHORIZATION", native)


@router.get("/authorizations/{policy_id}/by-key/{key}")
def authorization_lookup(
    policy_id: UUID,
    key: UUID,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with _reader(engine) as session:
        epoch = _epoch(session, user.id)
        native = read_release_authorization(
            session, user.id, epoch, native_key(epoch, "authorization", key), now
        )
        if (
            native.original is not None
            and native.original.original_authorization.policy_id != policy_id
        ):
            raise failure("原授权不能替换规则")
        return envelope(session, user.id, engine, "AUTHORIZATION_LOOKUP", native)


@router.post("/preview")
def cash_preview(
    body: PrepareBody, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    with _reader(engine) as session:
        current_epoch(session, user.id, body.expected_epoch_id)
        return envelope(
            session,
            user.id,
            engine,
            "CANDIDATE",
            preview_goal_release_execution(session, user.id, native_prepare(body), now),
        )


@router.post("/prepare")
def cash_prepare(
    body: PrepareBody,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    principal: PrincipalDependency,
) -> dict[str, Any]:
    require_user(principal, user.id, now)
    with _reader(engine) as session:
        current_epoch(session, user.id, body.expected_epoch_id)
    native = prepare_goal_release_execution(engine, user.id, native_prepare(body), now)
    with _reader(engine) as session:
        return envelope(session, user.id, engine, "ACTION", native)


@router.get("/by-key/{key}")
def cash_lookup(
    key: UUID, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    with _reader(engine) as session:
        epoch = _epoch(session, user.id)
        return envelope(
            session,
            user.id,
            engine,
            "ACTION_LOOKUP",
            read_goal_release_by_key(
                session, user.id, epoch, native_key(epoch, "prepare", key), now
            ),
        )


@router.get("/actions/{action_id}")
def cash_read(
    action_id: UUID, engine: EngineDependency, user: DemoUserDependency, now: ClockDependency
) -> dict[str, Any]:
    with _reader(engine) as session:
        return envelope(
            session,
            user.id,
            engine,
            "ACTION",
            read_goal_release_execution(session, user.id, action_id, now),
        )


@router.post("/actions/{action_id}/execute")
def cash_execute(
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
    native_body = GoalReleaseExecuteRequest.model_validate_json(
        body.model_dump_json(exclude={"client_request_id"})
    )
    native = execute_goal_release_execution(engine, user.id, action_id, native_body, now)
    with _reader(engine) as session:
        return envelope(session, user.id, engine, "ACTION", native)
