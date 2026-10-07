"""Owned, audited USER form candidates and the original twelve policy lifecycles."""

from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid5

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_next import ClientRequest, EngineDependency
from app.db.full_models import FullPolicy
from app.db.models import Account, EvidenceItem, Goal, Policy, Transaction
from app.domain.full_policy_configuration import DSLVersion, TemplateName
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope
from app.services.demo_console import _epoch
from app.services.execution_sources import resolve_payee_binding
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.zhiyu_orchestration import (
    MARKER,
    operation,
    remember_operation,
    remember_rejection,
    replay_operation,
    serial_user,
)
from app.services.zhiyu_policy_catalog import (
    CatalogCandidateRequest,
    CatalogConfirmationRequest,
    CatalogLifecycleRequest,
    catalog,
    catalog_schema,
    confirm_catalog_policy,
    lifecycle_catalog_policy,
    list_catalog_policies,
    preview_candidate,
)
from app.zhiyu_next_isolation import require_zhiyu_next_engine
from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, StrictBool, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def isolated(request: Request, engine: EngineDependency) -> None:
    require_zhiyu_next_engine(engine, check_connection=True)
    params = list(request.query_params.multi_items())
    schema_read = request.method == "GET" and request.url.path.startswith(
        "/api/v1/zhiyu-next/policy-schema/"
    )
    if params and not (schema_read and len(params) == 1 and params[0][0] == "dsl_version"):
        raise PolicyLifecycleError("UNSUPPORTED_QUERY", "接口不接受额外事实或时钟", 422)


router = APIRouter(
    prefix="/api/v1/zhiyu-next", tags=["知余十二策略"], dependencies=[Depends(isolated)]
)
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CandidateRequest(ClientRequest):
    expected_epoch_id: UUID
    template_name: TemplateName
    dsl_version: DSLVersion = "FULL_V1"
    configuration: dict[str, Any]
    goal_id: UUID | None = None
    expected_version_id: UUID | None = None
    goal_account_id: UUID | None = None

    @model_validator(mode="after")
    def goal_scope(self) -> "CandidateRequest":
        if self.template_name != "LongTermGoalPolicy":
            if any(
                value is not None
                for value in (self.goal_id, self.expected_version_id, self.goal_account_id)
            ):
                raise ValueError("Goal context is only supported by LongTermGoalPolicy")
        elif (self.goal_id is None) != (self.expected_version_id is None):
            raise ValueError("An existing goal requires both goal_id and expected_version_id")
        elif self.goal_id is not None and self.goal_account_id is not None:
            raise ValueError("An existing goal cannot replace its goal account")
        return self


class AcceptedRequest(ClientRequest):
    expected_epoch_id: UUID
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit USER acceptance is required")
        return value


class ConfirmRequest(AcceptedRequest):
    candidate_id: UUID
    reviewed_hash: Hash


class LifecycleRequest(AcceptedRequest):
    source_kind: Literal["MVP_POLICY", "FULL_POLICY", "GOAL_BRIDGE"]
    policy_id: UUID
    goal_id: UUID | None = None
    expected_version_id: UUID
    reviewed_hash: Hash
    command: Literal["SUSPEND", "RESUME", "REVOKE"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def goal_scope(self) -> "LifecycleRequest":
        if self.goal_id is not None and self.source_kind != "GOAL_BRIDGE":
            raise ValueError("Goal context is only supported by GOAL_BRIDGE")
        return self


def identity(session: Session, user_id: UUID, engine: Engine) -> dict[str, Any]:
    return {
        "simulation": True,
        "variant": "zhiyu-next",
        "environment_id": require_zhiyu_next_engine(engine),
        "epoch_id": str(_epoch(session, user_id)),
    }


def original_request(body: ClientRequest, path: str, kind: str) -> dict[str, Any]:
    return {"kind": kind, "path": path, "body": body.model_dump(mode="json")}


def saved_operation(
    session: Session, user_id: UUID, request_id: UUID
) -> tuple[dict[str, Any], dict[str, Any]]:
    # operation() validates the whole audit chain and this immutable row first.
    verified = operation(session, user_id, request_id)
    if verified is None:
        raise PolicyLifecycleError("NOT_FOUND", "原策略请求尚无可核实结果，请保留原定位", 404)
    epoch = _epoch(session, user_id)
    row = session.get(EvidenceItem, uuid5(epoch, f"{MARKER}:OPERATION:{request_id}"))
    assert row is not None
    original: dict[str, Any] = row.content["payload"]["request"]
    if not original.get("kind", "").startswith("POLICY_"):
        raise PolicyLifecycleError("REQUEST_SCOPE_MISMATCH", "原请求不是策略操作", 409)
    return verified, original


@router.get("/policy-templates")
def templates(
    session: SessionDependency, user: DemoUserDependency, engine: EngineDependency
) -> dict[str, Any]:
    return {
        **identity(session, user.id, engine),
        "bank_authority": False,
        "templates": [
            {
                "template_name": item.template_name,
                "title": item.label,
                "lifecycle_backend": item.backend,
                "dsl_version": item.dsl_version,
                "candidate_available": True,
                "confirm_available": True,
                "execution_status": "NOT_OPEN",
                "reason": "此入口保存规划规则；资金执行须另有对应权限与已验证消费链",
            }
            for item in catalog().items
        ],
    }


@router.get("/policy-schema/{template_name}")
def schema(
    template_name: TemplateName,
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
    dsl_version: Annotated[DSLVersion, Query()] = "FULL_V1",
) -> dict[str, Any]:
    result = catalog_schema(template_name, dsl_version)
    accounts = list(session.scalars(select(Account).where(Account.user_id == user.id)))
    goals = list(session.scalars(select(Goal).where(Goal.user_id == user.id)))
    policies = list(session.scalars(select(Policy).where(Policy.user_id == user.id)))
    full_policies = list(session.scalars(select(FullPolicy).where(FullPolicy.user_id == user.id)))
    choices = [{"value": str(row.id), "label": row.name} for row in accounts]
    goal_choices = [{"value": str(row.id), "label": row.name} for row in goals]
    payees: dict[str, dict[str, str]] = {}
    for transaction in session.scalars(
        select(Transaction)
        .where(
            Transaction.user_id == user.id,
            Transaction.direction == "DEBIT",
            Transaction.counterparty_ref.is_not(None),
            Transaction.occurred_at <= now,
            Transaction.observed_at <= now,
        )
        .order_by(Transaction.occurred_at.desc(), Transaction.id)
    ):
        payee = transaction.counterparty_ref
        if payee is None or payee in payees:
            continue
        try:
            resolve_payee_binding(session, user.id, payee, now)
        except PolicyLifecycleError:
            continue
        payees[payee] = {
            "value": payee,
            "label": {"rent": "历史房租收款对象", "utilities": "历史公用费用收款对象"}.get(
                transaction.category, "已核实历史收款对象"
            ),
        }
    policy_choices = [{"value": str(row.id), "label": row.name} for row in policies]
    policy_choices += [{"value": str(row.id), "label": row.name} for row in full_policies]
    assets = [
        {"value": str(row.id), "label": row.name}
        for row in policies
        if row.policy_type == "asset_authorization"
    ]
    if template_name == "RecoveryPolicy":
        assets += [
            {"value": str(row.id), "label": row.name}
            for row in full_policies
            if row.template_name == "AssetAuthorizationPolicy"
        ]
    return {
        **identity(session, user.id, engine),
        "template_name": result.template_name,
        "dsl_version": result.dsl_version,
        "lifecycle_backend": result.backend,
        "json_schema": result.configuration_schema,
        "schema_sha256": configuration_hash(result.configuration_schema),
        "cross_field_validation_required": True,
        "candidate_only": True,
        "bank_authority": False,
        "reference_choices": {
            **{key: choices for key in ("account_id", "destination_account_id")},
            "source_account_id": [
                {"value": str(row.id), "label": row.name}
                for row in accounts
                if row.account_type == "CASH"
            ],
            "goal_account_id": [
                {"value": str(row.id), "label": row.name}
                for row in accounts
                if row.account_type == "GOAL"
            ],
            **{
                key: goal_choices
                for key in (
                    "goal_id",
                    "goal_ids",
                    "source_goal_ids",
                    "target_goal_id",
                    "source_goal_id",
                )
            },
            "payee_id": list(payees.values()),
            "asset_policy_id": assets,
            "must_not_reduce_policy_ids": policy_choices,
            "cross_goal_reallocation_policy_id": [
                {"value": str(row.id), "label": row.name}
                for row in full_policies
                if row.template_name == "CrossGoalReallocationPolicy"
            ],
        },
    }


@router.post("/policy-candidates")
def candidate(
    body: CandidateRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    original = original_request(body, "/api/v1/zhiyu-next/policy-candidates", "POLICY_CANDIDATE")
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            actual = preview_candidate(
                CatalogCandidateRequest(
                    template_name=body.template_name,
                    dsl_version=body.dsl_version,
                    configuration=body.configuration,
                )
            )
            result = {
                **identity(session, user.id, engine),
                "client_request_id": str(body.client_request_id),
                "candidate_id": str(body.client_request_id),
                "template_name": actual.template_name,
                "dsl_version": actual.dsl_version,
                "canonical_configuration": actual.configuration,
                "configuration_hash": actual.configuration_hash,
                "lifecycle_backend": actual.backend,
                "base_configuration_hash": actual.base_configuration_hash,
                "summary": f"将保存{actual.label}规则；这次确认不提交资金动作。",
                "can_confirm": actual.can_confirm,
                "issues": actual.issues,
                "bank_authority": False,
                "planning_confirmed": False,
                "impact": {
                    "status": "UNKNOWN",
                    "changes": [],
                    "summary": "尚未建立规则，不以字段校验代替当前资金影响预览。",
                    "uncovered": ["执行权限与相应消费链须单独核验"],
                },
            }
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.post("/policy-commands/confirm")
def confirm(
    body: ConfirmRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    original = original_request(
        body, "/api/v1/zhiyu-next/policy-commands/confirm", "POLICY_CONFIRM"
    )
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            epoch = _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            prior, request = saved_operation(session, user.id, body.candidate_id)
            if prior["status"] != "COMPLETED" or request["kind"] != "POLICY_CANDIDATE":
                raise PolicyLifecycleError("CANDIDATE_NOT_READY", "原候选尚未可核实", 409)
            prior_result = prior["result"]
            if (
                prior_result["configuration_hash"] != body.reviewed_hash
                or not prior_result["can_confirm"]
            ):
                raise PolicyLifecycleError("REVIEW_MISMATCH", "请审阅当前原候选", 409)
            declaration = CandidateRequest.model_validate(request["body"])
            actual = confirm_catalog_policy(
                session,
                user.id,
                CatalogConfirmationRequest(
                    template_name=declaration.template_name,
                    dsl_version=declaration.dsl_version,
                    configuration=prior_result["canonical_configuration"],
                    expected_epoch_id=epoch,
                    reviewed_hash=body.reviewed_hash,
                    accepted=True,
                    reason="用户一次明确确认已审阅的策略候选",
                    idempotency_key=f"zhiyu-next:{epoch}:policy-confirm:{body.candidate_id}",
                    reviewed_base_hash=prior_result.get("base_configuration_hash"),
                    goal_id=declaration.goal_id,
                    expected_version_id=declaration.expected_version_id,
                    goal_account_id=declaration.goal_account_id,
                ),
                now,
            )
            result = {
                **identity(session, user.id, engine),
                **actual.model_dump(mode="json"),
                "client_request_id": str(body.client_request_id),
                "candidate_id": str(body.candidate_id),
                "planning_confirmed": True,
                "lifecycle_backend": actual.backend,
            }
            result.pop("original_result", None)
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.post("/policy-commands/lifecycle")
def lifecycle(
    body: LifecycleRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    original = original_request(
        body, "/api/v1/zhiyu-next/policy-commands/lifecycle", "POLICY_LIFECYCLE"
    )
    try:
        with serial_user(engine, user.id), Session(engine) as session, session.begin():
            epoch = _epoch(session, user.id, body.expected_epoch_id)
            replay = replay_operation(session, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
            # Resolve the template from current owned server rows, never caller metadata.
            if body.source_kind == "FULL_POLICY":
                full = session.get(FullPolicy, body.policy_id)
                if full is None or full.user_id != user.id:
                    raise PolicyLifecycleError("NOT_FOUND", "原策略不存在", 404)
                template = cast(TemplateName, full.template_name)
            else:
                current = session.get(Policy, body.policy_id)
                if current is None or current.user_id != user.id:
                    raise PolicyLifecycleError("NOT_FOUND", "原策略不存在", 404)
                mapping = {
                    "recurring_obligation": "RecurringObligationPolicy",
                    "living_reserve": "LivingReservePolicy",
                    "emergency_buffer": "EmergencyBufferPolicy",
                    "goal_saving": "LongTermGoalPolicy",
                }
                selected = mapping.get(current.policy_type)
                if selected is None:
                    raise PolicyLifecycleError("UNSUPPORTED_TEMPLATE", "该生命周期尚未接通", 409)
                template = cast(TemplateName, selected)
                expected_kind = (
                    "GOAL_BRIDGE" if current.policy_type == "goal_saving" else "MVP_POLICY"
                )
                if body.source_kind != expected_kind:
                    raise PolicyLifecycleError(
                        "REQUEST_SCOPE_MISMATCH", "策略生命周期范围不匹配", 409
                    )
                if body.goal_id is not None:
                    goal = session.get(Goal, body.goal_id)
                    if goal is None or goal.user_id != user.id or goal.policy_id != body.policy_id:
                        raise PolicyLifecycleError(
                            "REQUEST_SCOPE_MISMATCH", "目标与当前策略不匹配", 409
                        )
            actual = lifecycle_catalog_policy(
                session,
                user.id,
                body.policy_id,
                CatalogLifecycleRequest(
                    template_name=template,
                    action=body.command,
                    expected_epoch_id=epoch,
                    expected_version_id=body.expected_version_id,
                    reviewed_hash=body.reviewed_hash,
                    accepted=True,
                    reason=body.reason,
                    idempotency_key=f"zhiyu-next:{epoch}:policy-life:{body.client_request_id}",
                ),
                now,
            )
            result = {
                **identity(session, user.id, engine),
                **actual.model_dump(mode="json"),
                "client_request_id": str(body.client_request_id),
                "lifecycle_backend": actual.backend,
                "planning_confirmed": body.command == "RESUME",
            }
            result.pop("original_result", None)
            remember_operation(session, user.id, body.client_request_id, original, result, now)
            return result
    except PolicyLifecycleError as error:
        remember_rejection(engine, user.id, body.client_request_id, original, error, now)
        raise


@router.get("/policy-commands/{client_request_id}")
def command(
    client_request_id: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        result, request = saved_operation(session, user.id, client_request_id)
        return {
            **result,
            **identity(session, user.id, engine),
            "original_request": {"path": request["path"], "body": request["body"]},
        }


@router.get("/policy-records")
def records(
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        actual = list_catalog_policies(session, user.id, now)
        return {
            **identity(session, user.id, engine),
            "items": [
                {
                    **item.model_dump(mode="json", exclude={"original_result", "backend"}),
                    "source_kind": {
                        "MVP": "MVP_POLICY",
                        "FULL": "FULL_POLICY",
                        "GOAL_BRIDGE": "GOAL_BRIDGE",
                    }[item.backend],
                    "lifecycle_backend": item.backend,
                    "title": next(
                        row.label
                        for row in catalog().items
                        if row.template_name == item.template_name
                    ),
                    "name": item.configuration.get("name")
                    or next(
                        row.label
                        for row in catalog().items
                        if row.template_name == item.template_name
                    ),
                    "planning_confirmed": item.planning_confirmation_valid,
                    "lifecycle_available": item.lifecycle_supported,
                    "execution_status": "NOT_OPEN",
                    "reason": "规则原件可管理；资金消费权限与当前执行结果须另行核实",
                }
                for item in actual.items
            ],
        }
