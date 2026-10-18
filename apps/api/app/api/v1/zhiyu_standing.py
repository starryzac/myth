"""Reviewed finite USER scopes, native source candidates and original run reads."""

import json
import os
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.zhiyu_catalog import AcceptedRequest, Hash, identity, isolated, original_request
from app.api.v1.zhiyu_next import ClientRequest, EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.full_models import FullPolicy, FullPolicyVersion
from app.db.models import Account, AuditEpoch, EvidenceItem, Goal, Policy, PolicyVersion
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.standing_income_goal_scope import (
    NATIVE_PROTOCOL,
    AnyGrant,
    AnyScope,
    IncomeGoalScope,
    SignedIncomeGoalGrant,
    native_variant,
    parse_grant,
    parse_scope,
)
from app.domain.zhiyu_standing_journal import grant_record_payload
from app.domain.zhiyu_standing_scope import Family, SignedStandingGrant, StandingScope
from app.services.audit_chain import audit_read_scope
from app.services.demo_console import _epoch
from app.services.full_goals import read_full_goal_model
from app.services.full_policy_lifecycle import read_full_policy
from app.services.historical_read import historical_ledger_scope
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from app.services.product_catalog import verified_catalog_products
from app.services.zhiyu_orchestration import (
    MARKER,
    _audit_healthy,
    operation,
    remember_operation,
    replay_operation,
    serial_user,
    store,
)
from app.services.zhiyu_standing_store import (
    PREFIX,
    StandingControl,
    _rows,
    control_grant,
    lock_user_writer,
    original_control,
    original_grant,
    original_runs,
)
from fastapi import APIRouter, Depends
from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictInt,
    model_serializer,
    model_validator,
)
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/v1/zhiyu-next/standing",
    tags=["知余持续安排"],
    dependencies=[Depends(isolated)],
)


class ScopeCandidateRequest(ClientRequest):
    expected_epoch_id: UUID
    family: Family
    policy_version_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    source_account_ids: Annotated[list[UUID], Field(min_length=1, max_length=64)]
    goal_ids: Annotated[list[UUID], Field(max_length=8)] = Field(default_factory=list)
    product_ids: Annotated[list[UUID], Field(max_length=64)] = Field(default_factory=list)
    goal_mode: Literal["CUMULATIVE_EVENT_TARGET"] | None = None
    validity_preset: Literal["ONE_DAY", "SEVEN_DAYS", "THIRTY_DAYS"] = "SEVEN_DAYS"

    @model_serializer(mode="wrap")
    def original_mode_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        original: dict[str, Any] = handler(self)
        if self.goal_mode is None:
            original.pop("goal_mode", None)
        return original

    @model_validator(mode="after")
    def identities_are_choices(self) -> "ScopeCandidateRequest":
        if self.goal_mode is not None and self.family != "SINGLE_GOAL":
            raise ValueError("Explicit cumulative mode requires one SINGLE_GOAL")
        for values in (
            self.policy_version_ids,
            self.source_account_ids,
            self.goal_ids,
            self.product_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("Select each original identity only once")
        if self.family in {"SINGLE_GOAL", "JOINT_GOALS"} and (
            not self.goal_ids
            or self.product_ids
            or self.family == "SINGLE_GOAL"
            and len(self.goal_ids) != 1
            or self.family == "JOINT_GOALS"
            and len(self.goal_ids) < 2
        ):
            raise ValueError("Goal families need their exact owned goal choices")
        if self.family in {"ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"} and not self.product_ids:
            raise ValueError("Choose a finite server catalogue product set")
        if self.family == "PERIODIC_PAYMENT" and (self.goal_ids or self.product_ids):
            raise ValueError("A fixed payee cannot use goal or product destinations")
        return self


class ScopeConfirmRequest(AcceptedRequest):
    candidate_id: UUID
    reviewed_scope_hash: Hash
    reviewed_candidate_hash: Hash


class ScopeControlRequest(AcceptedRequest):
    grant_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=127)]
    command: Literal["PAUSE", "RESUME", "REVOKE"]


def _unavailable(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("STANDING_CURRENT_SCOPE_UNAVAILABLE", message, 409)


def _native_sources(
    session: Session, user_id: UUID, body: ScopeCandidateRequest, now: datetime
) -> dict[str, Any]:
    """Choices select original identities; all terms and limits come from native rows."""
    _epoch(session, user_id, body.expected_epoch_id)
    _audit_healthy(session, user_id)
    sources: list[dict[str, Any]] = []
    native_configs: list[dict[str, Any]] = []
    for version_id in sorted(body.policy_version_ids):
        native = session.get(PolicyVersion, version_id)
        if native is not None:
            policy = session.get(Policy, native.policy_id)
            if (
                native.user_id != user_id
                or policy is None
                or policy.user_id != user_id
                or not is_version_authorized(session, user_id, version_id, now)
                or native.content_hash != configuration_hash(native.configuration)
            ):
                raise _unavailable("原生策略版本已改变、暂停或无法核验")
            canonical = validate_configuration(native.configuration)
            if canonical != native.configuration:
                raise _unavailable("原策略完整配置与规范原件不一致")
            native_configs.append(canonical)
            sources.append(
                {
                    "kind": "MVP",
                    "version_id": str(version_id),
                    "policy_id": str(native.policy_id),
                    "content_hash": native.content_hash,
                    "configuration": canonical,
                }
            )
            continue
        full = session.get(FullPolicyVersion, version_id)
        policy_full = session.get(FullPolicy, full.policy_id) if full is not None else None
        if (
            full is None
            or full.user_id != user_id
            or policy_full is None
            or policy_full.user_id != user_id
            or policy_full.epoch_id != body.expected_epoch_id
        ):
            raise _unavailable("当前完整策略版本原件缺失")
        view = read_full_policy(session, user_id, policy_full.id, now)
        if (
            view.current_version.version_id != version_id
            or not view.planning_confirmation_valid
            or view.reference_validation != "CURRENT"
        ):
            raise _unavailable("完整策略版本已改变或原确认失效")
        sources.append(
            {
                "kind": "FULL",
                "version_id": str(version_id),
                "policy_id": str(full.policy_id),
                "content_hash": full.content_hash,
                "configuration": view.current_version.configuration,
                "template_name": view.template_name,
            }
        )
    accounts = []
    for account_id in sorted(body.source_account_ids):
        account = session.get(Account, account_id)
        if account is None or account.user_id != user_id or account.account_type != "CASH":
            raise _unavailable("来源必须是当前用户原现金账户")
        accounts.append(
            {
                "account_id": str(account.id),
                "user_id": str(account.user_id),
                "account_type": account.account_type,
                "currency": account.currency,
                "bank_code": account.bank_code,
                "external_ref": account.external_ref,
            }
        )
    goals = []
    for goal_id in sorted(body.goal_ids):
        goal = session.get(Goal, goal_id)
        if (
            goal is None
            or goal.user_id != user_id
            or goal.policy_version_id not in (body.policy_version_ids)
        ):
            raise _unavailable("目标须绑定选定的当期原生版本")
        model = read_full_goal_model(session, user_id, goal_id, now)
        if model.status != "VERIFIED" or model.base_policy_version_id != goal.policy_version_id:
            raise _unavailable("目标完整模型缺失或原生版本已改变")
        goals.append(
            {
                "goal_id": str(goal.id),
                "policy_version_id": str(goal.policy_version_id),
                "monthly_max_cents": goal.monthly_max_cents,
                "full_model": model.model_dump(mode="json"),
            }
        )
    catalogue = None
    if body.product_ids:
        products = verified_catalog_products(session, now)
        if products.status != "VERIFIED":
            raise _unavailable("需核验服务器当期完整产品目录")
        selected = [
            product for product in products.products if product.product_id in body.product_ids
        ]
        if len(selected) != len(body.product_ids):
            raise _unavailable("选定产品当前原目录缺失")
        bindings = [item for item in products.bindings if item.product_id in body.product_ids]
        if len(bindings) != len(selected):
            raise _unavailable("选定产品原目录绑定不完整")
        catalogue = {
            "products": [
                item.model_dump(mode="json")
                for item in sorted(selected, key=lambda item: item.product_id)
            ],
            "bindings": [
                item.model_dump(mode="json")
                for item in sorted(bindings, key=lambda item: item.product_id)
            ],
        }
    return {
        "policies": sources,
        "native_configurations": native_configs,
        "source_accounts": accounts,
        "goals": goals,
        "catalogue": catalogue,
    }


def _source_epoch_binding(
    session: Session, user_id: UUID, epoch_id: UUID, *, lock: bool = False
) -> tuple[Any, ...]:
    """Read actual columns, avoiding a stale ORM identity-map epoch object."""
    statement = select(
        AuditEpoch.id, AuditEpoch.status, AuditEpoch.event_count, AuditEpoch.last_sequence,
        AuditEpoch.last_event_id, AuditEpoch.last_event_hash,
    ).where(AuditEpoch.id == epoch_id, AuditEpoch.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).one_or_none()
    if row is None or row[1] != "OPEN":
        raise _unavailable("持续范围必须来自同一用户当期开放审计原件")
    return tuple(row)


def _review_native_sources(
    writer: Session, engine: Engine, user_id: UUID, body: ScopeCandidateRequest, now: datetime
) -> dict[str, Any]:
    """Keep native source reads clean RR/RO inside the original owner write gate.

    The caller has locked User and holds serial_user through commit. Locking the
    original AuditEpoch also serializes audited publication; the detached reader
    validates all current policy/goal/catalogue sources under one real snapshot.
    No writer session is marked read-only and no current permission is cached.
    """
    locked = _source_epoch_binding(writer, user_id, body.expected_epoch_id, lock=True)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection) as reader:
                with historical_ledger_scope(reader), audit_read_scope(reader):
                    sources = _native_sources(reader, user_id, body, now)
                    observed = _source_epoch_binding(reader, user_id, body.expected_epoch_id)
    if observed != locked or _source_epoch_binding(
        writer, user_id, body.expected_epoch_id
    ) != observed:
        raise _unavailable("持续范围读取与保存间的当期来源或审计序列已改变")
    _epoch(writer, user_id, body.expected_epoch_id)
    return sources


def scope_from_originals(
    body: ScopeCandidateRequest, user_id: UUID, sources: dict[str, Any], issued_at: datetime
) -> AnyScope:
    """Deterministic server limits are reviewed; this creates no AUTO/native permission."""
    configs = sources["native_configurations"]
    family = body.family
    kwargs: dict[str, Any] = {}
    if family in {"SINGLE_GOAL", "JOINT_GOALS"}:
        maxima = [row["monthly_max_cents"] for row in sources["goals"]]
        if not maxima or any(type(amount) is not int or amount < 1 for amount in maxima):
            raise _unavailable("目标原月度上限不足以生成有限范围")
        action_cap, event_cap = max(maxima), sum(maxima)
        if family == "JOINT_GOALS":
            allocation = [
                row
                for row in sources["policies"]
                if row.get("template_name") == ("GoalAllocationPolicy")
            ]
            if len(allocation) != 1:
                raise _unavailable("多目标范围需一个当前完整目标分配原策略")
            config = allocation[0]["configuration"]
            if set(config.get("goal_ids", [])) != {str(value) for value in body.goal_ids}:
                raise _unavailable("完整目标分配策略必须覆盖同一组目标")
            cap = config.get("max_single_allocation_cents")
            if type(cap) is not int or cap < 1:
                raise _unavailable("完整目标分配上限原件缺失")
            event_cap = min(event_cap, cap)
            action_cap = min(action_cap, event_cap)
        children = len(body.goal_ids)
    elif family == "PERIODIC_PAYMENT":
        payments = [row for row in configs if row["type"] == "recurring_obligation"]
        if len(payments) != 1:
            raise _unavailable("固定付款范围需一个当期原生收款人策略")
        payment = payments[0]
        rule = payment["amount_rule"]
        amount = rule.get("amount_cents") if rule["kind"] == "exact" else rule.get("max_cents")
        if type(amount) is not int or amount < 1:
            raise _unavailable("账单金额未知时不能声明可自动执行的金额范围")
        action_cap = event_cap = amount
        children = 1
        kwargs["payee_id"] = payment["payee_id"]
    else:
        assets = [row for row in configs if row["type"] == "asset_authorization"]
        if not assets:
            raise _unavailable("资产持续范围需真实当前原生资产权限")
        action_cap = min(row["single_action_cap_cents"] for row in assets)
        event_cap = min(row["max_auto_managed_cents"] for row in assets)
        classes = set(assets[0]["allowed_asset_classes"])
        for row in assets[1:]:
            classes.intersection_update(row["allowed_asset_classes"])
        if not classes or not sources["catalogue"]:
            raise _unavailable("资产原授权与目录范围没有交集")
        risk = min(row["max_principal_risk_level"] for row in assets)
        days = min(row["max_lock_days"] + row["max_redemption_delay_days"] for row in assets)
        if days < 1:
            raise _unavailable("当前持续资产范围需一个明确的本金可用期限上限")
        for product in sources["catalogue"]["products"]:
            if product["asset_class"] not in classes or product["risk_level"] > risk:
                raise _unavailable("选定产品不在原资产权限范围内")
        children = 4
        kwargs.update(
            asset_classes=sorted(classes),
            maximum_risk_level=risk,
            maximum_principal_unavailable_days=days,
        )
    if type(action_cap) is not int or type(event_cap) is not int or min(action_cap, event_cap) < 1:
        raise _unavailable("原生金额范围无法生成有限授权")
    native = StandingScope(
        user_id=user_id,
        epoch_id=body.expected_epoch_id,
        family=family,
        policy_version_ids=sorted(body.policy_version_ids),
        source_account_ids=sorted(body.source_account_ids),
        goal_ids=sorted(body.goal_ids),
        product_ids=sorted(body.product_ids),
        per_action_cap_cents=action_cap,
        per_event_cap_cents=event_cap,
        daily_cap_cents=event_cap,
        maximum_children=children,
        issued_at=issued_at,
        expires_at=issued_at
        + timedelta(days={"ONE_DAY": 1, "SEVEN_DAYS": 7, "THIRTY_DAYS": 30}[body.validity_preset]),
        **kwargs,
    )

    return (
        IncomeGoalScope(goal_mode=body.goal_mode, native_scope=native)
        if body.goal_mode is not None
        else native
    )


def _candidate(session: Session, user_id: UUID, candidate_id: UUID) -> dict[str, Any]:
    key = PREFIX + "candidate:" + str(candidate_id)
    rows = _rows(session, user_id, key)
    if len(rows) != 1 or rows[0].content["key"] != key:
        raise PolicyLifecycleError("STANDING_CANDIDATE_NOT_FOUND", "请重读原持续范围候选", 404)
    payload: dict[str, Any] = rows[0].content["payload"]
    if set(payload) != {"protocol", "candidate", "candidate_hash", "native_bank_authority"} or (
        payload["protocol"] != "zhiyu-next-standing-candidate-record-v1"
        or payload["native_bank_authority"] is not False
        or configuration_hash(payload["candidate"]) != payload["candidate_hash"]
    ):
        raise _unavailable("原持续范围候选无法完整核验")
    value = payload["candidate"]
    body = ScopeCandidateRequest.model_validate_json(json.dumps(value["original_request"]))
    scope = parse_scope(json.dumps(value["scope"]))
    if (
        value["candidate_id"] != str(candidate_id)
        or scope.user_id != user_id
        or scope.epoch_id != _epoch(session, user_id)
        or candidate_id
        != uuid5(body.expected_epoch_id, "standing-candidate:" + str(body.client_request_id))
        or value["scope_hash"] != configuration_hash(value["scope"])
        or scope.issued_at != rows[0].observed_at
    ):
        raise _unavailable("原候选身份或完整范围hash不一致")
    return payload


def income_goal_variant_capability() -> dict[str, Any]:
    """File/source-only capability, never infer a reviewed pin from an artifact."""
    from app.workers.zhiyu_standing_worker import require_verified_source

    reviewed = os.environ.get("ZHIYU_NEXT_INCOME_GOAL_REVIEWED_SOURCE_SHA256")
    available = False
    try:
        require_verified_source(
            "SINGLE_GOAL",
            native_protocol=NATIVE_PROTOCOL,
            reviewed_manifest_sha256=reviewed,
        )
        available = True
    except (ValueError, OSError):
        pass
    return {
        "native_protocol": NATIVE_PROTOCOL,
        "goal_mode": "CUMULATIVE_EVENT_TARGET",
        "source_validation_status": (
            "VERIFIED_ACTUAL_VARIANT_ACCEPTANCE"
            if available
            else "DEVELOPMENT_PENDING_ACTUAL_VARIANT_ACCEPTANCE"
        ),
        "candidate_selection_available": available,
        "worker_execution_status": "REQUIRES_EXPLICIT_REVIEWED_SOURCE_PIN",
    }


@router.get("/schema")
def schema() -> dict[str, Any]:
    return {
        "simulation": True,
        "protocol": "zhiyu-next-standing-schema-v1",
        "native_bank_authority": False,
        "candidate_schema": ScopeCandidateRequest.model_json_schema(),
        "scope_schema": StandingScope.model_json_schema(),
        "income_goal_scope_schema": IncomeGoalScope.model_json_schema(),
        "income_goal_variant": income_goal_variant_capability(),
        "confirmation_schema": ScopeConfirmRequest.model_json_schema(),
        "control_schema": ScopeControlRequest.model_json_schema(),
        "requires_separate_current_native_auto_permission": True,
    }


@router.post("/candidates")
def candidate(
    body: ScopeCandidateRequest,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    original = original_request(
        body, "/api/v1/zhiyu-next/standing/candidates", "STANDING_CANDIDATE"
    )
    with serial_user(engine, user.id), Session(engine) as session, session.begin():
        lock_user_writer(session, user.id)
        _epoch(session, user.id, body.expected_epoch_id)
        replay = replay_operation(session, user.id, body.client_request_id, original)
        if replay is not None:
            return replay
        sources = _review_native_sources(session, engine, user.id, body, now)
        scope = scope_from_originals(body, user.id, sources, now)
        candidate_id = uuid5(
            body.expected_epoch_id, "standing-candidate:" + str(body.client_request_id)
        )
        value = {
            "candidate_id": str(candidate_id),
            "original_request": body.model_dump(mode="json"),
            "scope": scope.model_dump(mode="json"),
            "scope_hash": configuration_hash(scope.model_dump(mode="json")),
            "original_sources": sources,
            "created_at": now.isoformat(),
        }
        payload = {
            "protocol": "zhiyu-next-standing-candidate-record-v1",
            "candidate": value,
            "candidate_hash": configuration_hash(value),
            "native_bank_authority": False,
        }
        store(session, user.id, "EVENT", PREFIX + "candidate:" + str(candidate_id), payload, now)
        result = {
            **identity(session, user.id, engine),
            **payload,
            "client_request_id": str(body.client_request_id),
        }
        remember_operation(session, user.id, body.client_request_id, original, result, now)
        return result


@router.get("/candidates/{candidate_id}")
def read_candidate(
    candidate_id: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        _audit_healthy(session, user.id)
        return {**identity(session, user.id, engine), **_candidate(session, user.id, candidate_id)}


@router.post("/confirm")
def confirm(
    body: ScopeConfirmRequest,
    user: DemoUserDependency,
    engine: EngineDependency,
    principal: PrincipalDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    from app.domain.local_actor_session_types import require_local_user

    require_local_user(principal, user.id, now)
    original = original_request(body, "/api/v1/zhiyu-next/standing/confirm", "STANDING_CONFIRM")
    with serial_user(engine, user.id), Session(engine) as session, session.begin():
        lock_user_writer(session, user.id)
        _epoch(session, user.id, body.expected_epoch_id)
        _audit_healthy(session, user.id)
        replay = replay_operation(session, user.id, body.client_request_id, original)
        if replay is not None:
            return replay
        saved = _candidate(session, user.id, body.candidate_id)
        value = saved["candidate"]
        scope = parse_scope(json.dumps(value["scope"]))
        if body.reviewed_scope_hash != value["scope_hash"] or (
            body.reviewed_candidate_hash != saved["candidate_hash"]
        ):
            raise PolicyLifecycleError(
                "STANDING_REVIEW_HASH_CHANGED", "请复核同一原范围与来源", 409
            )
        request = ScopeCandidateRequest.model_validate_json(json.dumps(value["original_request"]))
        sources = _review_native_sources(session, engine, user.id, request, now)
        if sources != value["original_sources"] or (
            scope_from_originals(request, user.id, sources, scope.issued_at) != scope
        ):
            raise _unavailable("策略、目录或来源范围已改变，请生成新候选")
        grant_id = uuid5(scope.epoch_id, "standing-grant:" + str(body.candidate_id))
        if _rows(session, user.id, PREFIX + "grant:" + str(grant_id)):
            raise PolicyLifecycleError(
                "STANDING_GRANT_ALREADY_CONFIRMED", "请读取原持续授权与控制", 409
            )
        grant: AnyGrant
        if isinstance(scope, IncomeGoalScope):
            grant = SignedIncomeGoalGrant(
                grant_id=grant_id,
                scope=scope,
                reviewed_scope_hash=body.reviewed_scope_hash,
                actor_at_confirmation=principal,
                confirmed_at=now,
                accepted=True,
            )
        else:
            grant = SignedStandingGrant(
                grant_id=grant_id,
                scope=scope,
                reviewed_scope_hash=body.reviewed_scope_hash,
                actor_at_confirmation=principal,
                confirmed_at=now,
                accepted=True,
            )
        control = StandingControl(
            grant_id=grant_id,
            revision=1,
            expected_previous_revision=0,
            state="ACTIVE",
            client_request_id=body.client_request_id,
            actor=principal,
            recorded_at=now,
        )
        # USER origin, original command result and initial control commit together.
        store(
            session,
            user.id,
            "EVENT",
            PREFIX + "grant:" + str(grant_id),
            grant_record_payload(grant),
            now,
        )
        store(
            session,
            user.id,
            "EVENT",
            PREFIX + "control:" + str(grant_id) + ":1",
            control.model_dump(mode="json"),
            now,
        )
        result = {
            **identity(session, user.id, engine),
            "grant": grant.model_dump(mode="json"),
            "native_protocol": native_variant(grant),
            "worker_execution_status": "REQUIRES_EXPLICIT_REVIEWED_SOURCE_PIN",
            "control": control.model_dump(mode="json"),
            "native_bank_authority": False,
            "client_request_id": str(body.client_request_id),
            "original_request": original,
        }
        remember_operation(session, user.id, body.client_request_id, original, result, now)
        return result


@router.post("/control")
def control(
    body: ScopeControlRequest,
    user: DemoUserDependency,
    engine: EngineDependency,
    principal: PrincipalDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    from app.domain.local_actor_session_types import require_local_user

    require_local_user(principal, user.id, now)
    original = original_request(body, "/api/v1/zhiyu-next/standing/control", "STANDING_CONTROL")
    with serial_user(engine, user.id):
        with Session(engine) as read:
            _epoch(read, user.id, body.expected_epoch_id)
            replay = replay_operation(read, user.id, body.client_request_id, original)
            if replay is not None:
                return replay
        states: dict[str, Literal["ACTIVE", "PAUSED", "REVOKED"]] = {
            "PAUSE": "PAUSED",
            "RESUME": "ACTIVE",
            "REVOKE": "REVOKED",
        }
        state = states[body.command]
        actual = control_grant(
            engine,
            user.id,
            body.grant_id,
            body.expected_revision,
            state,
            principal,
            body.client_request_id,
            now,
        )
        result = {
            "simulation": True,
            "control": actual.model_dump(mode="json"),
            "native_bank_authority": False,
            "client_request_id": str(body.client_request_id),
            "original_request": original,
        }
        # If this response boundary fails, the original signed native control row
        # remains independently readable and global UUID matching forbids replacement.
        with Session(engine) as write, write.begin():
            lock_user_writer(write, user.id)
            _audit_healthy(write, user.id)
            remember_operation(write, user.id, body.client_request_id, original, result, now)
        return result


@router.get("/commands/{request_id}")
def read_command(
    request_id: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        _audit_healthy(session, user.id)
        saved = operation(session, user.id, request_id)
        if saved is not None:
            row = session.get(
                EvidenceItem, uuid5(_epoch(session, user.id), f"{MARKER}:OPERATION:{request_id}")
            )
            if row is None or not row.content["payload"]["request"].get("kind", "").startswith(
                "STANDING_"
            ):
                raise PolicyLifecycleError(
                    "STANDING_COMMAND_SCOPE_DIFFERS", "原请求不属于持续安排", 409
                )
            return {
                **identity(session, user.id, engine),
                "status": "RECORDED",
                "operation": saved,
                "original_request": row.content["payload"]["request"],
                "client_request_id": str(request_id),
                "native_bank_authority": False,
            }
        # Preserve a real signed control committed before the wrapper result.
        controls = [
            StandingControl.model_validate_json(json.dumps(row.content["payload"]))
            for row in _rows(session, user.id, PREFIX + "control:")
            if row.content["payload"].get("client_request_id") == str(request_id)
        ]
        if len(controls) > 1:
            raise _unavailable("同一原控制请求出现重复原件")
        if controls:
            actual = controls[0]
            grant = original_grant(session, user.id, actual.grant_id)
            original_control(session, user.id, actual.grant_id)
            recovered_request = None
            if actual.revision > 1:
                commands: dict[str, Literal["PAUSE", "RESUME", "REVOKE"]] = {
                    "PAUSED": "PAUSE",
                    "ACTIVE": "RESUME",
                    "REVOKED": "REVOKE",
                }
                # Reconstruct only the exact closed command schema from the
                # signed native control. This does not recreate an operation.
                recovered_body = ScopeControlRequest(
                    client_request_id=actual.client_request_id,
                    expected_epoch_id=grant.scope.epoch_id,
                    accepted=True,
                    grant_id=actual.grant_id,
                    expected_revision=actual.expected_previous_revision,
                    command=commands[actual.state],
                )
                recovered_request = original_request(
                    recovered_body, "/api/v1/zhiyu-next/standing/control", "STANDING_CONTROL"
                )
            return {
                **identity(session, user.id, engine),
                "status": "PARTIAL_WRAPPER_RESULT",
                "original_control": actual.model_dump(mode="json"),
                "original_request": recovered_request,
                "client_request_id": str(request_id),
                "native_bank_authority": False,
            }
        raise PolicyLifecycleError("STANDING_COMMAND_NOT_FOUND", "请保留原持续请求定位后核实", 404)


@router.get("")
def read_all(
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        _audit_healthy(session, user.id)
        originals = []
        runs = original_runs(session, user.id)
        for row in _rows(session, user.id, PREFIX + "grant:"):
            actual = parse_grant(json.dumps(row.content["payload"]["grant"]))
            grant = original_grant(session, user.id, actual.grant_id)
            control = original_control(session, user.id, grant.grant_id)
            originals.append(
                {
                    "grant": grant.model_dump(mode="json"),
                    "native_protocol": native_variant(grant),
                    "worker_execution_status": "REQUIRES_EXPLICIT_REVIEWED_SOURCE_PIN",
                    "control": control.model_dump(mode="json"),
                    "effective_state": (
                        "EXPIRED"
                        if control.state == "ACTIVE"
                        and not grant.scope.issued_at <= now < grant.scope.expires_at
                        else control.state
                    ),
                    "runs": [
                        run.model_dump(mode="json")
                        for run in runs
                        if run.grant_id == grant.grant_id
                    ],
                }
            )
        return {
            **identity(session, user.id, engine),
            "grants": originals,
            "income_goal_variant": income_goal_variant_capability(),
            "native_bank_authority": False,
            "executor_role": "SYSTEM",
        }


@router.get("/grants/{grant_id}")
def read_grant(
    grant_id: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    engine: EngineDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with historical_ledger_scope(session), audit_read_scope(session):
        _audit_healthy(session, user.id)
        grant = original_grant(session, user.id, grant_id)
        control = original_control(session, user.id, grant_id)
        runs = [row for row in original_runs(session, user.id) if row.grant_id == grant_id]
        return {
            **identity(session, user.id, engine),
            "grant": grant.model_dump(mode="json"),
            "native_protocol": native_variant(grant),
            "worker_execution_status": "REQUIRES_EXPLICIT_REVIEWED_SOURCE_PIN",
            "control": control.model_dump(mode="json"),
            "effective_state": (
                "EXPIRED"
                if control.state == "ACTIVE"
                and not grant.scope.issued_at <= now < grant.scope.expires_at
                else control.state
            ),
            "runs": [row.model_dump(mode="json") for row in runs],
            "native_bank_authority": False,
            "executor_role": "SYSTEM",
        }
