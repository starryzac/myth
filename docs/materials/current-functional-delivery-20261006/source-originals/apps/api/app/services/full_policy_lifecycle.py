"""Explicit, durable FULL planning consent; never an original bank grant.

Eight new contracts live separately from MVP Policy rows. The three identical
templates use the original lifecycle; long-term goals use the existing dual-hash
goal bridge. Unsupported candidate financial projections remain unavailable.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast
from uuid import UUID, uuid4, uuid5

from app.db.models import (
    Account,
    ActionPlan,
    AssetPosition,
    AuditEpoch,
    EvidenceItem,
    Goal,
    Policy,
    PolicyVersion,
    User,
)
from app.domain.boundary import compute_boundary_with_details
from app.domain.full_policy_configuration import TemplateName, validate_full_configuration
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.dashboard import _capacity
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import FinancialBoundaryCard
from app.services.execution_sources import resolve_payee_binding
from app.services.financial_read import (
    finalize_financial_context,
    financial_card,
    load_verified_financial_context,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, _now, _user, _window
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.db.full_models import FullPolicy, FullPolicyCommand, FullPolicyVersion

PROTOCOL = "full-policy-confirmation-v1"
CONFIRMATION_SOURCE = "FULL_POLICY_CONFIRMATION"
FULL_TEMPLATES: tuple[TemplateName, ...] = (
    "DatedExpensePolicy",
    "PeriodicTransferPolicy",
    "AssetAuthorizationPolicy",
    "RecoveryPolicy",
    "GoalAllocationPolicy",
    "CrossGoalReallocationPolicy",
    "SeasonalReservePolicy",
    "InterventionPolicy",
)
PolicyState = Literal["ACTIVE", "CONFIRMED", "SUSPENDED", "EXPIRED", "REVOKED"]
CommandKind = Literal["CREATE", "CHANGE", "SUSPEND", "REVOKE", "RESUME", "REFRESH_TIME"]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Reason = Annotated[str, Field(min_length=1, max_length=1000)]
Key = Annotated[str, Field(min_length=1, max_length=160)]


class FullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FullConfirmationRequest(FullRequest):
    accepted: StrictBool
    reviewed_hash: Hash
    reason: Reason
    idempotency_key: Key

    @field_validator("accepted")
    @classmethod
    def explicit_acceptance(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value

    @field_validator("reason", "idempotency_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank value is required")
        return value


class FullCreateRequest(FullConfirmationRequest):
    template_name: TemplateName
    configuration: dict[str, Any]


class FullChangeRequest(FullConfirmationRequest):
    expected_version_id: UUID
    configuration: dict[str, Any]


class FullResumeRequest(FullConfirmationRequest):
    expected_version_id: UUID


class FullStateRequest(FullRequest):
    expected_version_id: UUID
    reason: Reason
    idempotency_key: Key

    @field_validator("reason", "idempotency_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return FullConfirmationRequest.nonblank(value)


class FullPreviewRequest(FullRequest):
    expected_version_id: UUID
    configuration: dict[str, Any]


class FullRefreshRequest(FullRequest):
    pass


class FullResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    dedicated_audit_event: Literal[False] = False


class FullVersionView(FullResponse):
    version_id: UUID
    policy_id: UUID
    version_number: int
    configuration: dict[str, Any]
    content_hash: Hash
    previous_hash: Hash | None
    summary: str
    confirmation: dict[str, Any]
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    change_reason: str
    evidence_ids: list[UUID]
    impact_analysis: dict[str, Any]
    confirmation_evidence_status: Literal[
        "CURRENT_EVIDENCE_MATCHED", "RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING"
    ]


class FullPolicyView(FullResponse):
    policy_id: UUID
    epoch_id: UUID
    template_name: TemplateName
    name: str
    status: PolicyState
    effective_status: str
    planning_confirmation_valid: bool
    reference_validation: Literal["CURRENT", "CHANGED_OR_UNAVAILABLE", "ARCHIVED"]
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    current_version: FullVersionView
    updated_at: datetime


class FullPolicyList(FullResponse):
    items: list[FullPolicyView]


class FullVersionList(FullResponse):
    items: list[FullVersionView]


class FullCommandView(FullResponse):
    command_id: UUID
    policy_id: UUID
    version_id: UUID
    kind: CommandKind
    command_number: int
    previous_hash: Hash | None
    idempotency_key: str
    request_hash: Hash
    previous_status: str | None
    resulting_status: PolicyState
    result: dict[str, Any]
    result_hash: Hash
    created_at: datetime


class FullCommandList(FullResponse):
    items: list[FullCommandView]


class FullCommandLookup(FullResponse):
    status: Literal["NOT_FOUND", "RECORDED"]
    idempotency_key: Key
    original_request: dict[str, Any] | None
    request_hash: Hash | None
    command: FullCommandView | None
    receipt_is_current_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False


class FullLifecycleResult(FullResponse):
    receipt_is_current_authority: Literal[False] = False
    policy_id: UUID
    epoch_id: UUID
    version_id: UUID
    command_id: UUID
    command_number: int = 1
    previous_command_hash: Hash | None = None
    status: PolicyState
    configuration_hash: Hash
    invalidated_action_ids: list[UUID] = Field(default_factory=list)
    inflight_action_ids: list[UUID] = Field(default_factory=list)
    action_dependencies_supported: Literal[False] = False
    requires_recompute: Literal[True] = True


class FullRefreshResult(FullResponse):
    results: list[FullLifecycleResult]


class FullChangePreview(FullResponse):
    preview_only: Literal[True] = True
    policy_id: UUID
    epoch_id: UUID
    expected_version_id: UUID
    as_of: datetime
    before_configuration: dict[str, Any]
    after_configuration: dict[str, Any]
    configuration_hash: Hash
    changed_fields: list[str]
    current_financial_boundary: FinancialBoundaryCard
    current_fact_digest: Hash
    reference_snapshots: list[dict[str, Any]]
    relevant_goal_ids: list[UUID]
    relevant_position_ids: list[UUID]
    relevant_current_action_ids: list[UUID]
    candidate_financial_status: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    delta_safe_idle_cents: None = None
    delta_goal_allocation_cents: None = None
    delta_position_principal_cents: None = None
    future_action_impact: Literal["NOT_IMPLEMENTED_NO_FULL_EXECUTION_ADAPTER"] = (
        "NOT_IMPLEMENTED_NO_FULL_EXECUTION_ADAPTER"
    )
    limitations: list[str]


def canonical_candidate(template: TemplateName, configuration: dict[str, Any]) -> dict[str, Any]:
    if template not in FULL_TEMPLATES:
        code = "USE_FULL_GOAL_BRIDGE" if template == "LongTermGoalPolicy" else "USE_MVP_LIFECYCLE"
        raise PolicyLifecycleError(code, "该模板沿原生命周期或完整目标双摘要确认入口处理", 409)
    try:
        return validate_full_configuration(template, configuration)
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_FULL_CONFIGURATION", "完整策略配置无效") from error


def reviewed_candidate(
    template: TemplateName, configuration: dict[str, Any], reviewed_hash: str, accepted: bool
) -> dict[str, Any]:
    if accepted is not True:
        raise PolicyLifecycleError("CONFIRMATION_REQUIRED", "必须明确确认完整配置")
    canonical = canonical_candidate(template, configuration)
    digest = configuration_hash(canonical)
    if (
        not isinstance(reviewed_hash, str)
        or len(reviewed_hash) != 64
        or any(character not in "0123456789abcdef" for character in reviewed_hash)
        or not hmac.compare_digest(digest, reviewed_hash)
    ):
        raise PolicyLifecycleError("REVIEW_MISMATCH", "完整配置已变化，请重新查看并确认", 409)
    return canonical


def derived_state(
    status: PolicyState,
    confirmed_at: datetime,
    valid_from: datetime,
    valid_until: datetime | None,
    now: datetime,
) -> str:
    now = _now(now)
    confirmed_at, valid_from = _now(confirmed_at), _now(valid_from)
    valid_until = _now(valid_until) if valid_until is not None else None
    if status == "REVOKED":
        return "REVOKED"
    if confirmed_at > now:
        return "CONFIRMATION_IN_FUTURE"
    if status == "EXPIRED" or (valid_until is not None and now >= valid_until):
        return "EXPIRED"
    if status == "SUSPENDED":
        return "SUSPENDED"
    return "CONFIRMED" if now < valid_from else "ACTIVE"


def next_state(kind: CommandKind, effective: str, intended: str | None = None) -> PolicyState:
    """A revoked or expired declaration cannot be revived by a status command."""
    if kind == "REVOKE":
        return "REVOKED"
    if kind == "SUSPEND" and effective in {"ACTIVE", "CONFIRMED", "SUSPENDED"}:
        return "SUSPENDED"
    if kind == "RESUME" and effective == "SUSPENDED" and intended in {"ACTIVE", "CONFIRMED"}:
        return "ACTIVE" if intended == "ACTIVE" else "CONFIRMED"
    if kind == "CHANGE" and effective in {"ACTIVE", "CONFIRMED", "SUSPENDED"}:
        if effective == "SUSPENDED":
            return "SUSPENDED"
        if intended in {"ACTIVE", "CONFIRMED", "EXPIRED"}:
            return cast(PolicyState, intended)
    if kind in {"CREATE", "REFRESH_TIME"} and intended in {"ACTIVE", "CONFIRMED", "EXPIRED"}:
        if kind == "REFRESH_TIME" and effective not in {"ACTIVE", "CONFIRMED", "EXPIRED"}:
            raise PolicyLifecycleError("INVALID_POLICY_STATE", "此完整策略当前不能刷新", 409)
        return cast(PolicyState, intended)
    raise PolicyLifecycleError("INVALID_POLICY_STATE", "完整策略状态不允许此命令", 409)


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(
        key
        for key in before.keys() | after.keys()
        if key not in before or key not in after or before[key] != after[key]
    )


def _invalid(message: str = "完整策略原件绑定或历史链无效") -> PolicyLifecycleError:
    return PolicyLifecycleError("INVALID_FULL_POLICY_SOURCE", message, 409)


def _full_window(
    user: User, configuration: dict[str, Any], now: datetime
) -> tuple[datetime, datetime | None]:
    start, end = _window(user, configuration, now)
    if end is not None and end <= start:
        raise PolicyLifecycleError("INVALID_WINDOW", "完整策略有效区间必须非空", 409)
    return start, end


def _owned(session: Session, model: Any, identifier: UUID, user_id: UUID, now: datetime) -> Any:
    row = session.get(model, identifier)
    if row is None or row.user_id != user_id or row.created_at > now:
        raise PolicyLifecycleError(
            "INVALID_FULL_REFERENCE", "完整策略引用不存在或不属于当前时点的用户", 409
        )
    return row


def _reference(role: str, kind: str, row: Any, binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "kind": kind,
        "id": str(row.id),
        "binding_hash": configuration_hash(binding),
        "snapshot": row_copy(row),
    }


def _references(
    session: Session,
    user_id: UUID,
    configuration: dict[str, Any],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[UUID]]:
    """Resolve current actual ownership/identity; no relationship is a bank grant."""
    from app.db.full_models import FullPolicy

    refs: list[dict[str, Any]] = []
    evidence_ids: list[UUID] = []
    goal_ids = set(configuration.get("goal_ids", [])) | set(
        configuration.get("source_goal_ids", [])
    )
    if configuration.get("goal_id"):
        goal_ids.add(configuration["goal_id"])
    for identifier in sorted(goal_ids):
        goal = _owned(session, Goal, UUID(identifier), user_id, now)
        refs.append(
            _reference(
                "goal",
                "GOAL",
                goal,
                {
                    "id": str(goal.id),
                    "owner": str(user_id),
                    "policy_version_id": str(goal.policy_version_id),
                },
            )
        )
    if configuration.get("source_account_id"):
        account = _owned(session, Account, UUID(configuration["source_account_id"]), user_id, now)
        if account.account_type != "CASH":
            raise PolicyLifecycleError(
                "INVALID_FULL_REFERENCE", "定期转账来源必须是当前用户现金账户", 409
            )
        refs.append(
            _reference(
                "source_account",
                "ACCOUNT",
                account,
                {
                    "id": str(account.id),
                    "owner": str(user_id),
                    "type": account.account_type,
                },
            )
        )
        evidence_id = resolve_payee_binding(session, user_id, configuration["payee_id"], now)
        proof = _evidence(session, user_id, [str(evidence_id)], now, lock=False)[0]
        refs.append(
            _reference(
                "payee_source",
                "EVIDENCE",
                proof,
                {
                    "id": str(proof.id),
                    "hash": proof.content_hash,
                    "payee_id": configuration["payee_id"],
                },
            )
        )
        evidence_ids.append(evidence_id)
    policy_roles = [
        (identifier, "protected_policy")
        for identifier in configuration.get("must_not_reduce_policy_ids", [])
    ]
    if configuration.get("asset_policy_id"):
        policy_roles.append((configuration["asset_policy_id"], "asset_policy"))
    for identifier, role in sorted(policy_roles):
        policy = session.get(Policy, UUID(identifier))
        if policy is not None:
            policy = _owned(session, Policy, policy.id, user_id, now)
            versions = list(
                session.scalars(
                    select(PolicyVersion)
                    .where(
                        PolicyVersion.user_id == user_id,
                        PolicyVersion.policy_id == policy.id,
                    )
                    .order_by(PolicyVersion.version_number)
                )
            )
            if not versions or (
                role == "asset_policy" and policy.policy_type != "asset_authorization"
            ):
                raise PolicyLifecycleError(
                    "INVALID_FULL_REFERENCE", "引用策略没有确切配置版本或资产范围", 409
                )
            version = versions[-1]
            if (
                version.created_at > now
                or configuration_hash(version.configuration) != version.content_hash
            ):
                raise _invalid()
            refs.append(
                _reference(
                    role,
                    "MVP_POLICY",
                    policy,
                    {
                        "id": str(policy.id),
                        "version_id": str(version.id),
                        "hash": version.content_hash,
                    },
                )
                | {"version_ids": [str(row.id) for row in versions]}
            )
        else:
            full = _owned(session, FullPolicy, UUID(identifier), user_id, now)
            epoch = current_audit_epoch(session, user_id)
            if epoch is None or full.epoch_id != epoch.id:
                raise PolicyLifecycleError(
                    "INVALID_FULL_REFERENCE", "不能将旧周期完整声明当当前引用", 409
                )
            if role == "asset_policy" and full.template_name != "AssetAuthorizationPolicy":
                raise PolicyLifecycleError("INVALID_FULL_REFERENCE", "恢复引用必须为资产声明", 409)
            versions_full = _versions(session, full, now)
            current = versions_full[-1]
            refs.append(
                _reference(
                    role,
                    "FULL_POLICY",
                    full,
                    {
                        "id": str(full.id),
                        "version_id": str(current.id),
                        "hash": current.content_hash,
                    },
                )
                | {"version_ids": [str(row.id) for row in versions_full]}
            )
    return refs, evidence_ids


def _verify_version(
    session: Session, policy: FullPolicy, version: FullPolicyVersion, now: datetime
) -> None:
    user = session.get(User, policy.user_id)
    if user is None or not user.is_simulated:
        raise _invalid()
    try:
        canonical = canonical_candidate(
            cast(TemplateName, policy.template_name), version.configuration
        )
        start, end = _full_window(user, canonical, version.confirmed_at)
        confirmation = version.confirmation
        expected = {
            "protocol": PROTOCOL,
            "user_id": str(policy.user_id),
            "epoch_id": str(policy.epoch_id),
            "policy_id": str(policy.id),
            "version_id": str(version.id),
            "template_name": policy.template_name,
            "reviewed_hash": version.content_hash,
            "confirmed_at": version.confirmed_at.isoformat(),
            "accepted": True,
            "bank_authority": False,
        }
        if (
            version.user_id != policy.user_id
            or version.policy_id != policy.id
            or version.created_at != version.confirmed_at
            or version.confirmed_at > now
            or version.configuration != canonical
            or configuration_hash(canonical) != version.content_hash
            or version.valid_from != start
            or version.valid_until != end
            or confirmation.get("accepted") is not True
            or confirmation.get("bank_authority") is not False
            or set(confirmation)
            != set(expected) | {"confirmation_evidence_id", "request_key", "request_hash"}
            or any(confirmation.get(key) != value for key, value in expected.items())
        ):
            raise _invalid()
        proof_id = UUID(confirmation["confirmation_evidence_id"])
        proof = session.get(EvidenceItem, proof_id)
        if proof is None and str(proof_id) in version.evidence_ids:
            epoch = session.get(AuditEpoch, policy.epoch_id)
            if epoch is not None and epoch.user_id == policy.user_id and epoch.status == "SEALED":
                # Only retained version/command bytes are checked here. No archive claim.
                return
        if (
            str(proof_id) not in version.evidence_ids
            or proof is None
            or proof.user_id != policy.user_id
            or proof.source_type != CONFIRMATION_SOURCE
            or proof.evidence_level != "USER_CONFIRMED_POLICY"
            or proof.source_ref != str(version.id)
            or proof.content != confirmation
            or configuration_hash(proof.content) != proof.content_hash
            or proof.status != "VALID"
            or proof.observed_at != version.confirmed_at
            or proof.valid_from != version.confirmed_at
        ):
            raise _invalid()
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, PolicyLifecycleError):
            raise
        raise _invalid() from error


def _versions(session: Session, policy: FullPolicy, now: datetime) -> list[FullPolicyVersion]:
    from app.db.full_models import FullPolicyVersion

    rows = list(
        session.scalars(
            select(FullPolicyVersion)
            .where(
                FullPolicyVersion.user_id == policy.user_id,
                FullPolicyVersion.policy_id == policy.id,
            )
            .order_by(FullPolicyVersion.version_number)
        )
    )
    if (
        not rows
        or len(rows) > 10000
        or [row.version_number for row in rows] != list(range(1, len(rows) + 1))
    ):
        raise _invalid()
    previous: FullPolicyVersion | None = None
    for row in rows:
        _verify_version(session, policy, row, now)
        if row.previous_hash != (previous.content_hash if previous else None):
            raise _invalid()
        if previous is not None and row.confirmed_at < previous.confirmed_at:
            raise _invalid()
        previous = row
    return rows


def _commands(
    session: Session, policy: FullPolicy, versions: list[FullPolicyVersion], now: datetime
) -> list[FullPolicyCommand]:
    from app.db.full_models import FullPolicyCommand

    rows = list(
        session.scalars(
            select(FullPolicyCommand)
            .where(
                FullPolicyCommand.user_id == policy.user_id,
                FullPolicyCommand.policy_id == policy.id,
            )
            .order_by(FullPolicyCommand.command_number)
        )
    )
    if (
        not rows
        or len(rows) > 10000
        or [row.command_number for row in rows] != list(range(1, len(rows) + 1))
    ):
        raise _invalid()
    known = {version.id: version for version in versions}
    previous: FullPolicyCommand | None = None
    confirmed_ids: list[UUID] = []
    for row in rows:
        try:
            result = FullLifecycleResult.model_validate(row.result)
        except ValueError as error:
            raise _invalid() from error
        if (
            row.epoch_id != policy.epoch_id
            or row.version_id not in known
            or row.created_at > now
            or configuration_hash(row.request) != row.request_hash
            or configuration_hash(row.result) != row.result_hash
            or row.previous_hash != (previous.result_hash if previous else None)
            or row.previous_status != (previous.resulting_status if previous else None)
            or result.command_id != row.id
            or result.command_number != row.command_number
            or result.previous_command_hash != row.previous_hash
            or result.policy_id != policy.id
            or result.epoch_id != policy.epoch_id
            or result.version_id != row.version_id
            or result.status != row.resulting_status
            or result.configuration_hash != known[row.version_id].content_hash
            or (previous is not None and row.created_at < previous.created_at)
        ):
            raise _invalid()
        if row.kind in {"CREATE", "CHANGE", "RESUME"}:
            confirmation = known[row.version_id].confirmation
            if (
                confirmation.get("request_hash") != row.request_hash
                or confirmation.get("request_key") != row.idempotency_key
                or known[row.version_id].confirmed_at != row.created_at
            ):
                raise _invalid()
            confirmed_ids.append(row.version_id)
        previous = row
    if (
        rows[0].kind != "CREATE"
        or len(confirmed_ids) != len(versions)
        or set(confirmed_ids) != set(known)
        or rows[-1].resulting_status != policy.status
        or rows[-1].version_id != versions[-1].id
    ):
        raise _invalid()
    return rows


def _binding(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime, *, lock: bool = False
) -> tuple[FullPolicy, list[FullPolicyVersion], list[FullPolicyCommand], bool]:
    from app.db.full_models import FullPolicy

    if lock:
        _user(session, user_id)
    owner = session.get(User, user_id)
    query = select(FullPolicy).where(FullPolicy.id == policy_id, FullPolicy.user_id == user_id)
    policy = session.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if owner is None or not owner.is_simulated or policy is None:
        raise PolicyLifecycleError("NOT_FOUND", "完整策略不存在", 404)
    epoch = session.get(AuditEpoch, policy.epoch_id)
    if epoch is None or epoch.user_id != user_id or policy.created_at > now:
        raise _invalid()
    current = current_audit_epoch(session, user_id)
    live = epoch.status == "OPEN" and current is not None and current.id == epoch.id
    if lock and not live:
        raise PolicyLifecycleError("ARCHIVED_FULL_POLICY", "旧周期完整策略只可历史查询", 409)
    versions = _versions(session, policy, now)
    commands = _commands(session, policy, versions, now)
    return policy, versions, commands, live


def _read_snapshot(session: Session) -> None:
    if (
        session.new
        or session.dirty
        or session.deleted
        or session.connection().get_isolation_level() != "REPEATABLE READ"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError(
            "INVALID_READ_SNAPSHOT", "完整策略读取及预览需要干净的RR只读事务", 409
        )


def _version_view(session: Session, version: FullPolicyVersion) -> FullVersionView:
    proof = session.get(EvidenceItem, UUID(version.confirmation["confirmation_evidence_id"]))
    return FullVersionView(
        version_id=version.id,
        policy_id=version.policy_id,
        version_number=version.version_number,
        configuration=version.configuration,
        content_hash=version.content_hash,
        previous_hash=version.previous_hash,
        summary=version.summary,
        confirmation=version.confirmation,
        confirmed_at=version.confirmed_at,
        valid_from=version.valid_from,
        valid_until=version.valid_until,
        change_reason=version.change_reason,
        evidence_ids=[UUID(value) for value in version.evidence_ids],
        impact_analysis=version.impact_analysis,
        confirmation_evidence_status=(
            "CURRENT_EVIDENCE_MATCHED"
            if proof is not None
            else "RETAINED_IN_VERSION_CURRENT_EVIDENCE_MISSING"
        ),
    )


def _reference_bindings(refs: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    return sorted((ref["role"], ref["kind"], ref["id"], ref["binding_hash"]) for ref in refs)


def read_full_policy(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> FullPolicyView:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        policy, versions, _, live = _binding(session, user_id, policy_id, now)
        version = versions[-1]
        effective = (
            derived_state(
                cast(PolicyState, policy.status),
                version.confirmed_at,
                version.valid_from,
                version.valid_until,
                now,
            )
            if live
            else "ARCHIVED"
        )
        reference_status: Literal["CURRENT", "CHANGED_OR_UNAVAILABLE", "ARCHIVED"] = "ARCHIVED"
        if live:
            try:
                refs, _ = _references(session, user_id, version.configuration, now)
                recorded = version.impact_analysis["reference_snapshots"]
                reference_status = (
                    "CURRENT"
                    if _reference_bindings(refs) == _reference_bindings(recorded)
                    else "CHANGED_OR_UNAVAILABLE"
                )
            except (PolicyLifecycleError, KeyError, TypeError, ValueError):
                reference_status = "CHANGED_OR_UNAVAILABLE"
        return FullPolicyView(
            policy_id=policy.id,
            epoch_id=policy.epoch_id,
            template_name=cast(TemplateName, policy.template_name),
            name=policy.name,
            status=cast(PolicyState, policy.status),
            effective_status=effective,
            planning_confirmation_valid=reference_status == "CURRENT"
            and effective in {"ACTIVE", "CONFIRMED"},
            reference_validation=reference_status,
            current_version=_version_view(session, version),
            updated_at=policy.updated_at,
        )


def list_full_policies(session: Session, user_id: UUID, now: datetime) -> FullPolicyList:
    from app.db.full_models import FullPolicy

    _read_snapshot(session)
    ids = list(
        session.scalars(
            select(FullPolicy.id)
            .where(FullPolicy.user_id == user_id)
            .order_by(FullPolicy.created_at, FullPolicy.id)
        )
    )
    if len(ids) > 10000:
        raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "完整策略历史超过读取容量", 409)
    return FullPolicyList(
        items=[read_full_policy(session, user_id, identifier, now) for identifier in ids]
    )


def list_full_versions(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> FullVersionList:
    _read_snapshot(session)
    _, versions, _, _ = _binding(session, user_id, policy_id, _now(now))
    return FullVersionList(items=[_version_view(session, row) for row in versions])


def list_full_commands(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> FullCommandList:
    _read_snapshot(session)
    _, _, rows, _ = _binding(session, user_id, policy_id, _now(now))
    return FullCommandList(items=[_command_view(row) for row in rows])


def _command_view(row: FullPolicyCommand) -> FullCommandView:
    return FullCommandView(
        command_id=row.id,
        policy_id=row.policy_id,
        version_id=row.version_id,
        kind=cast(CommandKind, row.kind),
        command_number=row.command_number,
        previous_hash=row.previous_hash,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        previous_status=row.previous_status,
        resulting_status=cast(PolicyState, row.resulting_status),
        result=row.result,
        result_hash=row.result_hash,
        created_at=row.created_at,
    )


def lookup_full_policy_command(
    session: Session, user_id: UUID, idempotency_key: str, now: datetime
) -> FullCommandLookup:
    """Recover the exact persisted command, never infer a missing write's outcome."""
    from app.db.full_models import FullPolicyCommand

    _read_snapshot(session)
    now = _now(now)
    if not idempotency_key.strip() or len(idempotency_key) > 160:
        raise PolicyLifecycleError("INVALID_IDEMPOTENCY_KEY", "幂等键长度必须为1至160且非空白", 422)
    with session.no_autoflush:
        owner = session.get(User, user_id)
        if owner is None or not owner.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        row = session.scalar(
            select(FullPolicyCommand).where(
                FullPolicyCommand.user_id == user_id,
                FullPolicyCommand.idempotency_key == idempotency_key,
            )
        )
        if row is None:
            return FullCommandLookup(
                status="NOT_FOUND",
                idempotency_key=idempotency_key,
                original_request=None,
                request_hash=None,
                command=None,
            )
        _, _, commands, _ = _binding(session, user_id, row.policy_id, now)
        exact = next((command for command in commands if command.id == row.id), None)
        if exact is None or exact.idempotency_key != idempotency_key:
            raise _invalid()
        return FullCommandLookup(
            status="RECORDED",
            idempotency_key=idempotency_key,
            original_request=exact.request,
            request_hash=exact.request_hash,
            command=_command_view(exact),
        )


def _request(
    kind: CommandKind, user_id: UUID, policy_id: UUID | None, body: FullRequest
) -> dict[str, Any]:
    checked = type(body).model_validate(body.model_dump())
    return {
        "protocol": "full-policy-command-v1",
        "kind": kind,
        "user_id": str(user_id),
        "policy_id": str(policy_id) if policy_id else None,
        "body": checked.model_dump(mode="json"),
    }


def _replay(
    session: Session, user_id: UUID, key: str, request: dict[str, Any], now: datetime
) -> FullLifecycleResult | None:
    from app.db.full_models import FullPolicyCommand

    row = session.scalar(
        select(FullPolicyCommand).where(
            FullPolicyCommand.user_id == user_id,
            FullPolicyCommand.idempotency_key == key,
        )
    )
    if row is None:
        return None
    if row.request_hash != configuration_hash(request) or row.request != request:
        raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同完整策略命令", 409)
    _, _, commands, _ = _binding(session, user_id, row.policy_id, now)
    if row.id not in {command.id for command in commands}:
        raise _invalid()
    return FullLifecycleResult.model_validate(row.result)


def _append_full_version(
    session: Session,
    user: User,
    policy: FullPolicy,
    configuration: dict[str, Any],
    previous: FullPolicyVersion | None,
    body: FullConfirmationRequest,
    request: dict[str, Any],
    now: datetime,
) -> FullPolicyVersion:
    from app.db.full_models import FullPolicyVersion

    refs, source_ids = _references(session, user.id, configuration, now)
    version_id, evidence_id = uuid4(), uuid4()
    digest = configuration_hash(configuration)
    start, end = _full_window(user, configuration, now)
    confirmation = {
        "protocol": PROTOCOL,
        "user_id": str(user.id),
        "epoch_id": str(policy.epoch_id),
        "policy_id": str(policy.id),
        "version_id": str(version_id),
        "template_name": policy.template_name,
        "reviewed_hash": digest,
        "confirmed_at": now.isoformat(),
        "accepted": True,
        "bank_authority": False,
        "confirmation_evidence_id": str(evidence_id),
        "request_key": body.idempotency_key,
        "request_hash": configuration_hash(request),
    }
    session.add(
        EvidenceItem(
            id=evidence_id,
            user_id=user.id,
            created_at=now,
            evidence_level="USER_CONFIRMED_POLICY",
            source_type=CONFIRMATION_SOURCE,
            source_ref=str(version_id),
            content=confirmation,
            content_hash=configuration_hash(confirmation),
            observed_at=now,
            valid_from=now,
            status="VALID",
        )
    )
    version = FullPolicyVersion(
        id=version_id,
        user_id=user.id,
        created_at=now,
        policy_id=policy.id,
        version_number=previous.version_number + 1 if previous else 1,
        configuration=configuration,
        content_hash=digest,
        previous_hash=previous.content_hash if previous else None,
        summary=configuration.get("name") or policy.name,
        confirmation=confirmation,
        confirmed_at=now,
        valid_from=start,
        valid_until=end,
        change_reason=body.reason,
        evidence_ids=sorted(str(identifier) for identifier in {evidence_id, *source_ids}),
        impact_analysis={
            "reference_snapshots": refs,
            "candidate_financial_status": "NOT_IMPLEMENTED",
            "candidate_financial_delta_cents": None,
            "bank_authority": False,
            "action_dependencies_supported": False,
            "dedicated_audit_event": False,
        },
    )
    session.add(version)
    session.flush()
    return version


def _record_command(
    session: Session,
    policy: FullPolicy,
    version: FullPolicyVersion,
    previous: FullPolicyCommand | None,
    kind: CommandKind,
    key: str,
    request: dict[str, Any],
    before_status: str | None,
    now: datetime,
) -> FullLifecycleResult:
    from app.db.full_models import FullPolicyCommand

    command_id = uuid4()
    invalidated: list[UUID] = []
    inflight: list[UUID] = []
    # These are real adapter-specific rechecks, not a claim that every FULL
    # template has an execution dependency adapter. Original command replay skips
    # this path and retains its original stored result/hash.
    if policy.template_name == "CrossGoalReallocationPolicy":
        from app.services.full_policy_action_rechecks import recheck_full_goal_release_actions

        release = recheck_full_goal_release_actions(
            session, policy.user_id, policy.id, version.id, policy.status, command_id, now
        )
        invalidated.extend(release.invalidated_action_ids)
        inflight.extend(release.inflight_action_ids)
    elif policy.template_name == "PeriodicTransferPolicy":
        from app.services.full_payment_permissions import recheck_full_payment_actions

        payment = recheck_full_payment_actions(
            session, policy.user_id, policy.id, version.id, policy.status, command_id, now
        )
        invalidated.extend(payment.invalidated_action_ids)
        inflight.extend(payment.inflight_action_ids)
    elif policy.template_name == "AssetAuthorizationPolicy":
        from app.services.full_asset_action_rechecks import recheck_full_asset_actions

        asset_invalidated, asset_inflight = recheck_full_asset_actions(
            session, policy.user_id, policy.id, policy.epoch_id, now, command_id
        )
        invalidated.extend(asset_invalidated)
        inflight.extend(asset_inflight)
    result = FullLifecycleResult(
        policy_id=policy.id,
        epoch_id=policy.epoch_id,
        version_id=version.id,
        command_id=command_id,
        command_number=previous.command_number + 1 if previous else 1,
        previous_command_hash=previous.result_hash if previous else None,
        status=cast(PolicyState, policy.status),
        configuration_hash=version.content_hash,
        invalidated_action_ids=sorted(set(invalidated), key=str),
        inflight_action_ids=sorted(set(inflight), key=str),
    )
    original = result.model_dump(mode="json")
    session.add(
        FullPolicyCommand(
            id=command_id,
            user_id=policy.user_id,
            created_at=now,
            epoch_id=policy.epoch_id,
            policy_id=policy.id,
            version_id=version.id,
            kind=kind,
            command_number=result.command_number,
            previous_hash=result.previous_command_hash,
            idempotency_key=key,
            request=request,
            request_hash=configuration_hash(request),
            previous_status=before_status,
            resulting_status=policy.status,
            result=original,
            result_hash=configuration_hash(original),
        )
    )
    policy.updated_at = now
    session.flush()
    return result


def confirm_full_policy(
    session: Session, user_id: UUID, body: FullCreateRequest, now: datetime
) -> FullLifecycleResult:
    from app.db.full_models import FullPolicy

    now = _now(now)
    canonical = reviewed_candidate(
        body.template_name, body.configuration, body.reviewed_hash, body.accepted
    )
    request = _request("CREATE", user_id, None, body)
    with session.begin_nested():
        user = _user(session, user_id)
        replay = _replay(session, user_id, body.idempotency_key, request, now)
        if replay is not None:
            return replay
        epoch = current_audit_epoch(session, user_id)
        if epoch is None:
            raise PolicyLifecycleError("INVALID_FULL_EPOCH", "完整策略确认缺少当前审计周期", 409)
        identifier = uuid5(user_id, "full-policy-v1:" + body.idempotency_key)
        if session.get(FullPolicy, identifier) is not None:
            raise _invalid()
        start, end = _full_window(user, canonical, now)
        intended = derived_state("ACTIVE", now, start, end, now)
        policy = FullPolicy(
            id=identifier,
            user_id=user_id,
            created_at=now,
            epoch_id=epoch.id,
            template_name=body.template_name,
            dsl_version="FULL_V1",
            name=canonical.get("name") or canonical["type"],
            status=next_state("CREATE", "PROPOSED", intended),
            updated_at=now,
        )
        session.add(policy)
        session.flush()
        version = _append_full_version(session, user, policy, canonical, None, body, request, now)
        return _record_command(
            session, policy, version, None, "CREATE", body.idempotency_key, request, None, now
        )


def change_full_policy(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: FullChangeRequest | FullResumeRequest,
    now: datetime,
    *,
    resume: bool = False,
) -> FullLifecycleResult:
    now = _now(now)
    kind: CommandKind = "RESUME" if resume else "CHANGE"
    request = _request(kind, user_id, policy_id, body)
    with session.begin_nested():
        user = _user(session, user_id)
        replay = _replay(session, user_id, body.idempotency_key, request, now)
        if replay is not None:
            return replay
        policy, versions, commands, _ = _binding(session, user_id, policy_id, now, lock=True)
        current = versions[-1]
        if current.id != body.expected_version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "完整策略版本已变化", 409)
        if now < policy.updated_at:
            raise PolicyLifecycleError("INVALID_CLOCK", "不能回写过去的完整策略命令", 409)
        configuration = (
            current.configuration if resume else cast(FullChangeRequest, body).configuration
        )
        canonical = reviewed_candidate(
            cast(TemplateName, policy.template_name),
            configuration,
            body.reviewed_hash,
            body.accepted,
        )
        start, end = _full_window(user, canonical, now)
        effective = derived_state(
            cast(PolicyState, policy.status),
            current.confirmed_at,
            current.valid_from,
            current.valid_until,
            now,
        )
        intended = derived_state("ACTIVE", now, start, end, now)
        target = next_state(kind, effective, intended)
        before = policy.status
        version = _append_full_version(
            session, user, policy, canonical, current, body, request, now
        )
        policy.status, policy.name = target, canonical.get("name") or policy.name
        return _record_command(
            session, policy, version, commands[-1], kind, body.idempotency_key, request, before, now
        )


def stop_full_policy(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: FullStateRequest,
    now: datetime,
    *,
    revoke: bool = False,
) -> FullLifecycleResult:
    now = _now(now)
    kind: CommandKind = "REVOKE" if revoke else "SUSPEND"
    request = _request(kind, user_id, policy_id, body)
    with session.begin_nested():
        _user(session, user_id)
        replay = _replay(session, user_id, body.idempotency_key, request, now)
        if replay is not None:
            return replay
        policy, versions, commands, _ = _binding(session, user_id, policy_id, now, lock=True)
        current = versions[-1]
        if current.id != body.expected_version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "完整策略版本已变化", 409)
        if now < policy.updated_at:
            raise PolicyLifecycleError("INVALID_CLOCK", "不能回写过去的完整策略命令", 409)
        effective = derived_state(
            cast(PolicyState, policy.status),
            current.confirmed_at,
            current.valid_from,
            current.valid_until,
            now,
        )
        before = policy.status
        policy.status = next_state(kind, effective)
        return _record_command(
            session, policy, current, commands[-1], kind, body.idempotency_key, request, before, now
        )


def refresh_full_policy_time(session: Session, user_id: UUID, now: datetime) -> FullRefreshResult:
    from app.db.full_models import FullPolicy

    now = _now(now)
    results: list[FullLifecycleResult] = []
    with session.begin_nested():
        _user(session, user_id)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None:
            raise PolicyLifecycleError("INVALID_FULL_EPOCH", "完整策略刷新缺少当前周期", 409)
        ids = list(
            session.scalars(
                select(FullPolicy.id)
                .where(
                    FullPolicy.user_id == user_id,
                    FullPolicy.epoch_id == epoch.id,
                )
                .order_by(FullPolicy.id)
            )
        )
        for identifier in ids:
            policy, versions, commands, _ = _binding(session, user_id, identifier, now, lock=True)
            current = versions[-1]
            effective = derived_state(
                cast(PolicyState, policy.status),
                current.confirmed_at,
                current.valid_from,
                current.valid_until,
                now,
            )
            if effective == policy.status or effective not in {"ACTIVE", "CONFIRMED", "EXPIRED"}:
                continue
            if now < policy.updated_at:
                raise PolicyLifecycleError("INVALID_CLOCK", "不能回写过去的完整策略命令", 409)
            target = next_state("REFRESH_TIME", effective, effective)
            boundary_time = current.valid_until if target == "EXPIRED" else current.valid_from
            if boundary_time is None:
                raise _invalid()
            request = {
                "protocol": "full-policy-command-v1",
                "kind": "REFRESH_TIME",
                "user_id": str(user_id),
                "policy_id": str(policy.id),
                "version_id": str(current.id),
                "target": target,
                "boundary_time": boundary_time.isoformat(),
            }
            key = f"time:{policy.id}:{current.id}:{target}"
            before, policy.status = policy.status, target
            results.append(
                _record_command(
                    session,
                    policy,
                    current,
                    commands[-1],
                    "REFRESH_TIME",
                    key,
                    request,
                    before,
                    now,
                )
            )
    return FullRefreshResult(results=results)


def preview_full_policy_change(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: FullPreviewRequest,
    now: datetime,
) -> FullChangePreview:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        policy, versions, _, live = _binding(session, user_id, policy_id, now)
        current = versions[-1]
        if not live:
            raise PolicyLifecycleError("ARCHIVED_FULL_POLICY", "旧周期不能预览当前修改", 409)
        if body.expected_version_id != current.id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "完整策略版本已变化", 409)
        canonical = canonical_candidate(
            cast(TemplateName, policy.template_name), body.configuration
        )
        effective = derived_state(
            cast(PolicyState, policy.status),
            current.confirmed_at,
            current.valid_from,
            current.valid_until,
            now,
        )
        user = session.get(User, user_id)
        assert user is not None
        start, end = _full_window(user, canonical, now)
        next_state("CHANGE", effective, derived_state("ACTIVE", now, start, end, now))
        refs, _ = _references(session, user_id, canonical, now)
        _capacity(session, user_id)
        context, _, _ = load_verified_financial_context(session, user_id, now)
        audit = current_epoch_audit(session, user_id, [])
        context, issues, digest = finalize_financial_context(context, audit)
        computed = compute_boundary_with_details(
            context.snapshot, context.versions, context.positions, context.products
        )
        before = financial_card(computed.boundary, computed.details, issues, digest)
        goals = {UUID(ref["id"]) for ref in refs if ref["kind"] == "GOAL"}
        old_versions = {
            UUID(value)
            for ref in refs
            if ref["kind"] == "MVP_POLICY"
            for value in ref["version_ids"]
        }
        positions = list(
            session.scalars(
                select(AssetPosition)
                .where(AssetPosition.user_id == user_id)
                .order_by(AssetPosition.id)
            )
        )
        actions = list(
            session.scalars(
                select(ActionPlan).where(ActionPlan.user_id == user_id).order_by(ActionPlan.id)
            )
        )
        relevant_positions = [
            row
            for row in positions
            if row.goal_id in goals or row.policy_version_id in old_versions
        ]
        relevant_actions = [
            row for row in actions if row.goal_id in goals or row.policy_version_id in old_versions
        ]
        fact_digest = configuration_hash(
            {
                "protocol": "full-policy-preview-facts-v1",
                "base_financial_digest": digest,
                "policy": row_copy(policy),
                "version": row_copy(current),
                "references": refs,
                "positions": [row_copy(row) for row in relevant_positions],
                "actions": [row_copy(row) for row in relevant_actions],
            }
        )
        return FullChangePreview(
            policy_id=policy.id,
            epoch_id=policy.epoch_id,
            expected_version_id=current.id,
            as_of=now,
            before_configuration=current.configuration,
            after_configuration=canonical,
            configuration_hash=configuration_hash(canonical),
            changed_fields=changed_fields(current.configuration, canonical),
            current_financial_boundary=before,
            current_fact_digest=fact_digest,
            reference_snapshots=refs,
            relevant_goal_ids=sorted(goals),
            relevant_position_ids=[row.id for row in relevant_positions],
            relevant_current_action_ids=[row.id for row in relevant_actions],
            limitations=[
                "完整模板尚未接入金融计算和执行：候选资金、目标、持仓及未来动作差量均未实现，不能解释为零。",
                "所列目标、持仓和当前动作来自原引用；未生成未来动作，当前声明没有原执行依赖。",
                "此次预览无写入、确认或银行授权；实际提交重新读取 owner、周期、版本与证据。",
            ],
        )
