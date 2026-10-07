"""Twelve extension policy declarations, delegated to their original lifecycles.

Confirmation requests are internal adapters built from an audited USER_FORM
candidate by the extension router. They are not a public replacement for that
candidate's original request. No helper here submits a bank command.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.db.models import Account, Goal, Policy, PolicyVersion
from app.domain.full_policy_configuration import (
    FULL_TYPE_MAPPING,
    MVP_TYPE_MAPPING,
    DSLVersion,
    TemplateName,
    template_names,
    template_schema,
    validate_full_configuration,
)
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch
from app.services.audit_recording import audit_subject_data, record_policy_state
from app.services.full_goals import (
    FullGoalModelContent,
    _model_rows,
    _original_content,
    canonical_goal_bridge,
    confirm_full_goal_model,
    verify_full_goal_model_original,
)
from app.services.full_policy_change_multi import (
    MultiTemplatePreviewRequest,
    preview_multi_template_financial_change,
)
from app.services.full_policy_lifecycle import (
    FullCreateRequest,
    FullResumeRequest,
    FullStateRequest,
    change_full_policy,
    confirm_full_policy,
    list_full_policies,
    stop_full_policy,
)
from app.services.goals import create_goal_projection
from app.services.policy_lifecycle import (
    LifecycleResult,
    PolicyLifecycleError,
    _evidence,
    _now,
    _user,
    change_policy,
    confirm_proposal,
    effective_status,
    is_version_authorized,
    revoke_policy,
    suspend_policy,
)
from app.services.user_policy_declaration import UserDeclarationRequest, declare_user_policy
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

Backend = Literal["MVP", "FULL", "GOAL_BRIDGE"]
Hash = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,139}$")]
Reason = Annotated[StrictStr, Field(min_length=1, max_length=1000)]
MVP_TEMPLATES = frozenset(
    {"RecurringObligationPolicy", "LivingReservePolicy", "EmergencyBufferPolicy"}
)
LABELS = (
    "周期负债",
    "生活预留",
    "应急底线",
    "确定日期支出",
    "长期目标",
    "周期转账",
    "资产授权",
    "回收规则",
    "联合目标分配",
    "跨目标调拨",
    "季节预留",
    "提醒与介入",
)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogDescriptor(CatalogModel):
    template_name: TemplateName
    label: str
    backend: Backend
    dsl_version: DSLVersion = "FULL_V1"
    financial_consumption: Literal["SEPARATE_EXECUTION_PROTOCOL_REQUIRED"] = (
        "SEPARATE_EXECUTION_PROTOCOL_REQUIRED"
    )


class CatalogResponse(CatalogModel):
    items: list[CatalogDescriptor]


class CatalogSchemaResponse(CatalogDescriptor):
    configuration_schema: dict[str, Any]


class CatalogCandidateRequest(CatalogRequest):
    template_name: TemplateName
    dsl_version: DSLVersion = "FULL_V1"
    configuration: dict[str, Any]


class CatalogCandidate(CatalogDescriptor):
    candidate_only: Literal[True] = True
    configuration: dict[str, Any]
    configuration_hash: Hash
    base_configuration: dict[str, Any] | None = None
    base_configuration_hash: Hash | None = None
    can_confirm: bool
    issues: list[str] = Field(default_factory=list)
    reference_validation_pending: Literal[True] = True


class CatalogConfirmationRequest(CatalogCandidateRequest):
    expected_epoch_id: UUIDReference
    reviewed_hash: Hash
    accepted: StrictBool
    reason: Reason
    idempotency_key: Key
    reviewed_base_hash: Hash | None = None
    goal_id: UUIDReference | None = None
    expected_version_id: UUIDReference | None = None
    goal_account_id: UUIDReference | None = None

    @model_validator(mode="after")
    def explicit_original(self) -> Self:
        if self.accepted is not True or not self.reason.strip():
            raise ValueError("An explicit original candidate confirmation is required")
        context = (
            self.reviewed_base_hash,
            self.goal_id,
            self.expected_version_id,
            self.goal_account_id,
        )
        if self.template_name != "LongTermGoalPolicy" and any(v is not None for v in context):
            raise ValueError("Only the long-term goal bridge accepts goal context")
        if self.template_name == "LongTermGoalPolicy" and self.dsl_version == "FULL_V1":
            if self.reviewed_base_hash is None:
                raise ValueError("Both long-term goal hashes must be reviewed")
            if (self.goal_id is None) != (self.expected_version_id is None):
                raise ValueError("An existing goal requires its exact original version")
            if self.goal_id is not None and self.goal_account_id is not None:
                raise ValueError("An existing goal cannot change its account")
        elif self.template_name == "LongTermGoalPolicy":
            if any(v is not None for v in context[:3]):
                raise ValueError("An original MVP goal only accepts an initial account")
        return self


class CatalogLifecycleRequest(CatalogRequest):
    template_name: TemplateName
    dsl_version: DSLVersion = "FULL_V1"
    action: Literal["SUSPEND", "RESUME", "REVOKE"]
    expected_epoch_id: UUIDReference
    expected_version_id: UUIDReference
    reviewed_hash: Hash
    accepted: StrictBool
    reason: Reason
    idempotency_key: Key

    @model_validator(mode="after")
    def explicit_original(self) -> Self:
        if self.accepted is not True or not self.reason.strip():
            raise ValueError("An explicit original lifecycle request is required")
        return self


class CatalogChangePreviewRequest(CatalogCandidateRequest):
    expected_epoch_id: UUIDReference
    expected_version_id: UUIDReference


class CatalogLifecycleResult(CatalogModel):
    template_name: TemplateName
    backend: Backend
    policy_id: UUID
    current_version_id: UUID
    goal_id: UUID | None = None
    status: str
    configuration_hash: Hash
    original_result: dict[str, Any]
    receipt_is_current_authority: Literal[False] = False


class CatalogChangePreviewResult(CatalogModel):
    template_name: TemplateName
    backend: Backend
    preview_only: Literal[True] = True
    source_kind: Literal["MVP_POLICY", "FULL_POLICY"]
    candidate_configuration_hash: Hash
    base_configuration_hash: Hash | None = None
    financial_preview: dict[str, Any]
    extra_goal_fields_in_financial_preview: Literal[False] = False


class CatalogPolicyView(CatalogModel):
    template_name: TemplateName
    dsl_version: DSLVersion
    source_kind: Literal["MVP_POLICY", "FULL_POLICY", "GOAL_BRIDGE"]
    backend: Backend
    policy_id: UUID
    current_version_id: UUID
    goal_id: UUID | None = None
    configuration: dict[str, Any]
    configuration_hash: Hash
    status: str
    effective_status: str
    planning_confirmation_valid: bool
    lifecycle_supported: bool = True
    read_only_legacy: bool = False
    original_result: dict[str, Any]


class CatalogPolicyList(CatalogModel):
    items: list[CatalogPolicyView]


def backend_for(template_name: TemplateName, dsl_version: DSLVersion = "FULL_V1") -> Backend:
    if template_name not in template_names():
        raise PolicyLifecycleError("UNKNOWN_TEMPLATE", "策略模板不存在", 422)
    if dsl_version == "MVP_V1":
        if template_name not in MVP_TEMPLATES:
            raise PolicyLifecycleError(
                "UNSUPPORTED_TEMPLATE_VERSION", "扩展版仅开放三类同义MVP模板", 422
            )
        return "MVP"
    if template_name in MVP_TEMPLATES:
        return "MVP"
    return "GOAL_BRIDGE" if template_name == "LongTermGoalPolicy" else "FULL"


def catalog() -> CatalogResponse:
    return CatalogResponse(
        items=[
            CatalogDescriptor(template_name=name, label=label, backend=backend_for(name))
            for name, label in zip(template_names(), LABELS, strict=True)
        ]
    )


def catalog_schema(
    template_name: TemplateName,
    dsl_version: DSLVersion = "FULL_V1",
) -> CatalogSchemaResponse:
    descriptor = next(
        (item for item in catalog().items if item.template_name == template_name), None
    )
    if descriptor is None:
        raise PolicyLifecycleError("UNKNOWN_TEMPLATE", "策略模板不存在", 422)
    return CatalogSchemaResponse(
        **descriptor.model_copy(
            update={
                "dsl_version": dsl_version,
                "backend": backend_for(template_name, dsl_version),
            }
        ).model_dump(),
        configuration_schema=template_schema(template_name, dsl_version),
    )


def preview_candidate(body: CatalogCandidateRequest) -> CatalogCandidate:
    descriptor = next(item for item in catalog().items if item.template_name == body.template_name)
    descriptor = descriptor.model_copy(
        update={
            "dsl_version": body.dsl_version,
            "backend": backend_for(body.template_name, body.dsl_version),
        }
    )
    try:
        canonical = validate_full_configuration(
            body.template_name, body.configuration, version=body.dsl_version
        )
    except (ValueError, TypeError, RecursionError) as error:
        raise PolicyLifecycleError(
            "INVALID_CONFIGURATION", "策略候选字段不完整或无效", 422
        ) from error
    base, issues = None, []
    if descriptor.backend == "GOAL_BRIDGE":
        try:
            canonical, base = canonical_goal_bridge(canonical)
        except PolicyLifecycleError as error:
            issues.append(error.code)
    return CatalogCandidate(
        **descriptor.model_dump(),
        configuration=canonical,
        configuration_hash=configuration_hash(canonical),
        base_configuration=base,
        base_configuration_hash=configuration_hash(base) if base is not None else None,
        can_confirm=not issues,
        issues=issues,
    )


def _require_epoch(session: Session, user_id: UUID, epoch_id: UUID, now: datetime) -> None:
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.id != epoch_id or epoch.status != "OPEN" or epoch.opened_at > now:
        raise PolicyLifecycleError("STALE_DEMO_EPOCH", "策略操作须绑定当前已开启模拟轮次", 409)


def _reviewed(body: CatalogConfirmationRequest) -> CatalogCandidate:
    candidate = preview_candidate(body)
    if not candidate.can_confirm:
        raise PolicyLifecycleError(candidate.issues[0], "当前目标扩展缺少独立支持协议", 409)
    if (
        candidate.configuration_hash != body.reviewed_hash
        or candidate.base_configuration_hash != body.reviewed_base_hash
    ):
        raise PolicyLifecycleError("REVIEW_MISMATCH", "候选摘要已变化，请重新查看并确认", 409)
    return candidate


def _mvp_confirmation(
    session: Session,
    user_id: UUID,
    body: CatalogConfirmationRequest,
    configuration: dict[str, Any],
    now: datetime,
) -> LifecycleResult:
    declaration = declare_user_policy(
        session,
        user_id,
        UserDeclarationRequest(
            configuration=configuration,
            expected_epoch_id=body.expected_epoch_id,
            idempotency_key="x02:" + body.idempotency_key,
        ),
        now,
    )
    return confirm_proposal(
        session, user_id, declaration.proposal_id, configuration_hash(configuration), True, now
    )


def confirm_catalog_policy(
    session: Session,
    user_id: UUID,
    body: CatalogConfirmationRequest,
    now: datetime,
) -> CatalogLifecycleResult:
    """Caller supplies the audited USER_FORM configuration and owns commit/replay."""
    now, candidate = _now(now), _reviewed(body)
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("DIRTY_COMMAND_SESSION", "确认不能混入其他待写入行", 409)
    with session.begin_nested():
        _user(session, user_id)
        _require_epoch(session, user_id, body.expected_epoch_id, now)
        if candidate.backend == "FULL":
            full = confirm_full_policy(
                session,
                user_id,
                FullCreateRequest(
                    template_name=body.template_name,
                    configuration=candidate.configuration,
                    reviewed_hash=body.reviewed_hash,
                    accepted=True,
                    reason=body.reason,
                    idempotency_key=body.idempotency_key,
                ),
                now,
            )
            return CatalogLifecycleResult(
                template_name=body.template_name,
                backend="FULL",
                policy_id=full.policy_id,
                current_version_id=full.version_id,
                status=full.status,
                configuration_hash=full.configuration_hash,
                original_result=full.model_dump(mode="json"),
            )
        if candidate.backend == "MVP":
            result = _mvp_confirmation(session, user_id, body, candidate.configuration, now)
            goal_id = None
            if body.template_name == "LongTermGoalPolicy":
                goal_id = _initial_goal(session, user_id, body, result, now)
            return _mvp_result(
                body.template_name, result, candidate.configuration_hash, goal_id, candidate.backend
            )
        assert candidate.base_configuration is not None and body.reviewed_base_hash is not None
        if body.goal_id is None:
            result = _mvp_confirmation(session, user_id, body, candidate.base_configuration, now)
            goal_id = _initial_goal(session, user_id, body, result, now)
            expected_version_id = result.current_version_id
            session.flush()
        else:
            goal_id = body.goal_id
            assert body.expected_version_id is not None
            expected_version_id = body.expected_version_id
        bridge = confirm_full_goal_model(
            session,
            user_id,
            goal_id,
            expected_version_id,
            body.expected_epoch_id,
            candidate.configuration,
            body.reviewed_hash,
            body.reviewed_base_hash,
            True,
            body.reason,
            body.idempotency_key,
            now,
        )
        return CatalogLifecycleResult(
            template_name=body.template_name,
            backend="GOAL_BRIDGE",
            goal_id=bridge.goal_id,
            policy_id=bridge.lifecycle.policy_id,
            current_version_id=bridge.lifecycle.current_version_id,
            status=bridge.lifecycle.status,
            configuration_hash=bridge.full_configuration_hash,
            original_result=bridge.model_dump(mode="json"),
        )


def _mvp_result(
    template: TemplateName,
    result: LifecycleResult,
    digest: str,
    goal_id: UUID | None = None,
    backend: Backend | None = None,
) -> CatalogLifecycleResult:
    return CatalogLifecycleResult(
        template_name=template,
        backend=backend or backend_for(template),
        policy_id=result.policy_id,
        current_version_id=result.current_version_id,
        status=result.status,
        configuration_hash=digest,
        goal_id=goal_id,
        original_result=result.model_dump(mode="json"),
    )


def _initial_goal(
    session: Session,
    user_id: UUID,
    body: CatalogConfirmationRequest,
    result: LifecycleResult,
    now: datetime,
) -> UUID:
    goal = session.scalar(
        select(Goal).where(Goal.user_id == user_id, Goal.policy_id == result.policy_id)
    )
    if goal is not None:
        if body.goal_account_id is not None and body.goal_account_id != goal.account_id:
            raise PolicyLifecycleError("GOAL_ACCOUNT_CONFLICT", "原目标不可换绑账户", 409)
        return goal.id
    account_id = body.goal_account_id or session.scalar(
        select(Account.id)
        .where(Account.user_id == user_id, Account.account_type == "GOAL")
        .order_by(Account.id)
        .limit(1)
    )
    if account_id is None:
        raise PolicyLifecycleError("NOT_FOUND", "模拟目标账户未初始化", 404)
    return create_goal_projection(
        session, user_id, result.policy_id, result.current_version_id, account_id, now
    ).goal.id


def _mvp_binding(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    template: TemplateName,
) -> tuple[Policy, PolicyVersion]:
    policy = session.scalar(
        select(Policy).where(Policy.id == policy_id, Policy.user_id == user_id).with_for_update()
    )
    tag = "goal_saving" if template == "LongTermGoalPolicy" else FULL_TYPE_MAPPING[template]
    if policy is None or policy.policy_type != tag:
        raise PolicyLifecycleError("NOT_FOUND", "当前模板策略不存在", 404)
    version = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == policy_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if version is None:
        raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "策略缺少已确认版本", 409)
    return policy, version


def _resume_mvp(
    session: Session,
    user_id: UUID,
    policy: Policy,
    version: PolicyVersion,
    body: CatalogLifecycleRequest,
    now: datetime,
) -> LifecycleResult:
    key = "x02-resume:" + body.idempotency_key
    replay = session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.user_id == user_id,
            PolicyVersion.policy_id == policy.id,
            PolicyVersion.confirmation["request_key"].as_string() == "change:" + key,
        )
    )
    if replay is not None:
        return change_policy(
            session,
            user_id,
            policy.id,
            body.expected_version_id,
            replay.configuration,
            body.reviewed_hash,
            True,
            body.reason,
            key,
            now,
        )
    if policy.status != "SUSPENDED" or version.id != body.expected_version_id:
        raise PolicyLifecycleError("INVALID_POLICY_STATE", "仅可重审当前暂停策略", 409)
    if (
        version.valid_from is None
        or now < version.valid_from
        or (version.valid_until is not None and now >= version.valid_until)
    ):
        raise PolicyLifecycleError("POLICY_NOT_AUTHORIZED", "当前期限不能恢复策略", 409)
    if (
        version.content_hash != body.reviewed_hash
        or configuration_hash(version.configuration) != body.reviewed_hash
    ):
        raise PolicyLifecycleError("REVIEW_MISMATCH", "恢复须确认原配置摘要", 409)
    proofs = _evidence(session, user_id, version.evidence_ids, now)
    if not any(
        p.source_type == "POLICY_CONFIRMATION"
        and p.evidence_level == "USER_CONFIRMED_POLICY"
        and p.source_ref == str(version.id)
        and p.content == version.confirmation
        and p.content.get("accepted") is True
        for p in proofs
    ):
        raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "恢复缺少原用户确认来源", 409)
    before = audit_subject_data(policy)
    policy.status, policy.updated_at = "ACTIVE", now
    record_policy_state(
        session,
        policy,
        version,
        "SUSPENDED",
        now,
        reason_code="POLICY_RESUMED_BY_EXPLICIT_USER_REVIEW",
        before_data=before,
    )
    return change_policy(
        session,
        user_id,
        policy.id,
        body.expected_version_id,
        version.configuration,
        body.reviewed_hash,
        True,
        body.reason,
        key,
        now,
    )


def lifecycle_catalog_policy(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: CatalogLifecycleRequest,
    now: datetime,
) -> CatalogLifecycleResult:
    """Explicit state commands only; financial CHANGE uses signed reviewed-change."""
    now, backend = _now(now), backend_for(body.template_name, body.dsl_version)
    with session.begin_nested():
        _user(session, user_id)
        _require_epoch(session, user_id, body.expected_epoch_id, now)
        if backend == "FULL":
            # read_full_policy needs an RR/RO reader; binding here uses actual write rows.
            from app.db.full_models import FullPolicy, FullPolicyVersion

            policy = session.get(FullPolicy, policy_id)
            version = session.get(FullPolicyVersion, body.expected_version_id)
            if (
                policy is None
                or policy.user_id != user_id
                or policy.template_name != body.template_name
                or policy.epoch_id != body.expected_epoch_id
                or version is None
                or version.policy_id != policy_id
                or version.user_id != user_id
                or version.content_hash != body.reviewed_hash
            ):
                raise PolicyLifecycleError("REVIEW_MISMATCH", "完整策略原版本摘要不匹配", 409)
            if body.action == "RESUME":
                full = change_full_policy(
                    session,
                    user_id,
                    policy_id,
                    FullResumeRequest(
                        expected_version_id=body.expected_version_id,
                        reviewed_hash=body.reviewed_hash,
                        accepted=True,
                        reason=body.reason,
                        idempotency_key=body.idempotency_key,
                    ),
                    now,
                    resume=True,
                )
            else:
                full = stop_full_policy(
                    session,
                    user_id,
                    policy_id,
                    FullStateRequest(
                        expected_version_id=body.expected_version_id,
                        reason=body.reason,
                        idempotency_key=body.idempotency_key,
                    ),
                    now,
                    revoke=body.action == "REVOKE",
                )
            return CatalogLifecycleResult(
                template_name=body.template_name,
                backend=backend,
                policy_id=policy_id,
                current_version_id=full.version_id,
                status=full.status,
                configuration_hash=full.configuration_hash,
                original_result=full.model_dump(mode="json"),
            )
        policy_mvp, version_mvp = _mvp_binding(session, user_id, policy_id, body.template_name)
        original_goal, original_model = None, None
        base_body = body
        if backend == "GOAL_BRIDGE":
            original_goal, original_model = _goal_model(
                session, user_id, policy_mvp, version_mvp, now
            )
            if original_model is None or original_model.full_hash != body.reviewed_hash:
                raise PolicyLifecycleError("REVIEW_MISMATCH", "完整目标原模型摘要不匹配", 409)
            base_body = body.model_copy(update={"reviewed_hash": original_model.base_hash})
        if body.action == "RESUME":
            result = _resume_mvp(session, user_id, policy_mvp, version_mvp, base_body, now)
            if original_model is not None and original_goal is not None:
                session.flush()
                bridge = confirm_full_goal_model(
                    session,
                    user_id,
                    original_goal.id,
                    result.current_version_id,
                    body.expected_epoch_id,
                    original_model.full_configuration,
                    original_model.full_hash,
                    original_model.base_hash,
                    True,
                    body.reason,
                    "x02-resume:" + body.idempotency_key,
                    now,
                )
                result = bridge.lifecycle
        else:
            if version_mvp.content_hash != base_body.reviewed_hash:
                raise PolicyLifecycleError("REVIEW_MISMATCH", "策略原版本摘要不匹配", 409)
            delegate = revoke_policy if body.action == "REVOKE" else suspend_policy
            result = delegate(session, user_id, policy_id, body.expected_version_id, now)
        goal_id = (
            session.scalar(
                select(Goal.id).where(Goal.user_id == user_id, Goal.policy_id == policy_id)
            )
            if backend == "GOAL_BRIDGE"
            else None
        )
        return _mvp_result(body.template_name, result, body.reviewed_hash, goal_id, backend)


def preview_catalog_change(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: CatalogChangePreviewRequest,
    now: datetime,
) -> CatalogChangePreviewResult:
    """A clean RR/RO caller snapshot; retain actual PROJECTED/PARTIAL/UNKNOWN."""
    candidate = preview_candidate(body)
    if not candidate.can_confirm:
        raise PolicyLifecycleError(candidate.issues[0], "当前候选不可进入变更预览", 409)
    backend = candidate.backend
    kind: Literal["MVP_POLICY", "FULL_POLICY"] = (
        "FULL_POLICY" if backend == "FULL" else "MVP_POLICY"
    )
    configuration = (
        candidate.base_configuration if backend == "GOAL_BRIDGE" else candidate.configuration
    )
    assert configuration is not None
    result = preview_multi_template_financial_change(
        session,
        user_id,
        kind,
        policy_id,
        MultiTemplatePreviewRequest(
            expected_epoch_id=body.expected_epoch_id,
            expected_version_id=body.expected_version_id,
            configuration=configuration,
        ),
        now,
    )
    if result.template_name != body.template_name:
        raise PolicyLifecycleError("POLICY_TYPE_CHANGE", "变更不能替换策略模板", 409)
    return CatalogChangePreviewResult(
        template_name=body.template_name,
        backend=backend,
        source_kind=kind,
        candidate_configuration_hash=candidate.configuration_hash,
        base_configuration_hash=candidate.base_configuration_hash,
        financial_preview=result.model_dump(mode="json"),
    )


def list_catalog_policies(session: Session, user_id: UUID, now: datetime) -> CatalogPolicyList:
    """Read an RR/RO snapshot without refreshing or mutating lifecycle state."""
    now, items = _now(now), []
    names = {tag: name for name, tag in MVP_TYPE_MAPPING.items()}
    with session.no_autoflush, audit_read_scope(session):
        for policy in session.scalars(
            select(Policy)
            .where(Policy.user_id == user_id, Policy.policy_type.in_(names))
            .order_by(Policy.created_at, Policy.id)
            .limit(10001)
        ):
            version = session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == policy.id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            if version is None:
                raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "策略缺少版本", 409)
            template = names[policy.policy_type]
            goal_id = (
                session.scalar(
                    select(Goal.id).where(Goal.user_id == user_id, Goal.policy_id == policy.id)
                )
                if template == "LongTermGoalPolicy"
                else None
            )
            configuration, digest = version.configuration, version.content_hash
            backend: Backend = "MVP"
            dsl_version: DSLVersion = "MVP_V1"
            original: dict[str, Any] = {"version_number": version.version_number}
            if goal_id is not None:
                _, model = _goal_model(session, user_id, policy, version, now)
                original["full_goal_model"] = model.model_dump(mode="json") if model else None
                if model is not None:
                    configuration, digest = model.full_configuration, model.full_hash
                    backend, dsl_version = "GOAL_BRIDGE", "FULL_V1"
            legacy = template not in MVP_TEMPLATES and backend != "GOAL_BRIDGE"
            original["base_configuration_hash"] = version.content_hash
            items.append(
                CatalogPolicyView(
                    template_name=template,
                    backend=backend,
                    dsl_version=dsl_version,
                    source_kind="GOAL_BRIDGE" if backend == "GOAL_BRIDGE" else "MVP_POLICY",
                    policy_id=policy.id,
                    current_version_id=version.id,
                    goal_id=goal_id,
                    configuration=configuration,
                    configuration_hash=digest,
                    status=policy.status,
                    effective_status=effective_status(policy, version, now),
                    planning_confirmation_valid=is_version_authorized(
                        session, user_id, version.id, now
                    ),
                    lifecycle_supported=not legacy,
                    read_only_legacy=legacy,
                    original_result=original,
                )
            )
        if len(items) > 10000:
            raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "策略历史超过读取容量", 409)
        for full in list_full_policies(session, user_id, now).items:
            items.append(
                CatalogPolicyView(
                    template_name=full.template_name,
                    backend="FULL",
                    dsl_version="FULL_V1",
                    source_kind="FULL_POLICY",
                    policy_id=full.policy_id,
                    current_version_id=full.current_version.version_id,
                    configuration=full.current_version.configuration,
                    configuration_hash=full.current_version.content_hash,
                    status=full.status,
                    effective_status=full.effective_status,
                    planning_confirmation_valid=full.planning_confirmation_valid,
                    original_result=full.model_dump(mode="json"),
                )
            )
    return CatalogPolicyList(items=items)


def _goal_model(
    session: Session,
    user_id: UUID,
    policy: Policy,
    version: PolicyVersion,
    now: datetime,
) -> tuple[Goal | None, FullGoalModelContent | None]:
    """Retain a verified planning original even while its execution policy is paused."""
    goal = session.scalar(select(Goal).where(Goal.user_id == user_id, Goal.policy_id == policy.id))
    if goal is None:
        return None, None
    epoch = current_audit_epoch(session, user_id)
    if epoch is None:
        raise PolicyLifecycleError("STALE_DEMO_EPOCH", "完整目标缺少当前轮次", 409)
    matching = [
        row
        for row in _model_rows(session, user_id, goal.id)
        if _original_content(row).base_policy_version_id == version.id
    ]
    if len(matching) > 1:
        raise PolicyLifecycleError("INVALID_FULL_GOAL_MODEL", "完整目标原模型不唯一", 409)
    if not matching:
        return goal, None
    return goal, verify_full_goal_model_original(matching[0], goal, policy, version, epoch, now)
