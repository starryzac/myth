"""USER-only paired goal transfers using owned originals and the existing ledger.

No old PROTECTED_CASH consent is inherited. Scope previews are immutable and
queryable by their original key; new acceptance always rechecks both goals.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Goal,
    SimulatedBankPosting,
    User,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.full_goal_release_execution import GoalReleaseUse, Hash, PositiveCents
from app.domain.full_goal_transfer import (
    NAMESPACE,
    GoalTransferAuthorization,
    GoalTransferBankCommand,
    GoalTransferConfirmRequest,
    GoalTransferCurrentScope,
    GoalTransferEffect,
    GoalTransferGoalBinding,
    GoalTransferPermission,
    GoalTransferPolicyBinding,
    GoalTransferPolicyConfiguration,
    GoalTransferScope,
    Key,
    goal_transfer_action_identity,
    goal_transfer_bank_key,
    goal_transfer_effect_hash,
    project_goal_transfer_protection,
    validate_goal_transfer_authorization,
)
from app.domain.full_goal_transfer_current_proof import (
    GoalTransferCurrentProof,
    validate_current_goal_transfer_effect,
)
from app.domain.full_goal_transfer_provenance import goal_transfer_policy_lifetime_usage
from app.domain.full_policy_configuration import LongTermGoalPolicy
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.goal_release_provenance import GoalCashSourceProof
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import UUIDReference, configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch, row_copy
from app.services.audit_recording import record_action_created
from app.services.decision_trace import capture_sources, get_decision_trace, record_trace
from app.services.execution_exposure import refresh_execution_exposure
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_goal_release_authorization import _reader
from app.services.full_goal_release_execution import _transition, select_original_release_uses
from app.services.full_goal_transfer_inventory import (
    GoalTransferInventory,
    read_goal_transfer_inventory,
)
from app.services.full_goal_transfer_reader import (
    read_goal_transfer_bank_command,
    read_goal_transfer_command,
    verify_goal_transfer_legs,
    verify_goal_transfer_receipt,
)
from app.services.full_goals import read_full_goal_model
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from app.services.recovery_projection import _epochs
from pydantic import StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

ALGORITHM = "full-goal-transfer-v1"
AUTH_SOURCE = "FULL_GOAL_TRANSFER_AUTHORIZATION"


class GoalTransferPreviewRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    policy_id: UUIDReference
    expected_policy_version_id: UUIDReference
    source_goal_id: UUIDReference
    expected_source_policy_version_id: UUIDReference
    target_goal_id: UUIDReference
    expected_target_policy_version_id: UUIDReference
    amount_cents: PositiveCents
    idempotency_key: Key


class GoalTransferPreview(BoundaryModel):
    schema_version: Literal["goal-transfer-preview-v1"] = "goal-transfer-preview-v1"
    preview_id: UUID
    user_id: UUID
    epoch_id: UUID
    scope: GoalTransferCurrentScope | GoalTransferScope
    scope_hash: Hash
    input_hash: Hash
    source_available_cents: int
    target_current_cents: int
    policy_used_cents: int
    protection_point_count: Literal[1098] = 1098
    expires_at: datetime
    bank_authority: Literal[False] = False


class GoalTransferPrepareRequest(GoalTransferConfirmRequest):
    preview_id: UUIDReference


class GoalTransferExecuteRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_effect_hash: Hash
    accepted: StrictBool

    @model_validator(mode="after")
    def explicit(self) -> "GoalTransferExecuteRequest":
        if self.accepted is not True:
            raise ValueError("Only explicit USER paired transfer acceptance is supported")
        return self


class GoalTransferActionResponse(BoundaryModel):
    schema_version: Literal["goal-transfer-action-v1"] = "goal-transfer-action-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    action_id: UUID
    original_action_status: str
    original_command: GoalTransferBankCommand
    original_prepare_request: GoalTransferPrepareRequest
    original_request_hash: Hash
    authorization: GoalTransferAuthorization
    authorization_evidence_id: UUID
    authorization_evidence_hash: Hash
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


class GoalTransferLookup(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original: GoalTransferActionResponse | None
    replacement_allowed: Literal[False] = False


class GoalTransferRecheckResult(BoundaryModel):
    invalidated_action_ids: list[UUID]
    inflight_action_ids: list[UUID]


def _error(
    message: str, code: str = "GOAL_TRANSFER_NOT_READY", status: int = 409
) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, status)


def _goal(
    session: Session, user: UUID, identity: UUID, expected_version: UUID, epoch: UUID, now: datetime
) -> GoalTransferGoalBinding:
    goal = session.scalar(select(Goal).where(Goal.id == identity, Goal.user_id == user))
    model = read_full_goal_model(session, user, identity, now)
    if (
        goal is None
        or goal.account_id is None
        or goal.policy_version_id != expected_version
        or model.status != "VERIFIED"
        or model.policy_effective_status not in {"ACTIVE", "CONFIRMED"}
        or model.epoch_id != epoch
        or model.base_policy_version_id != goal.policy_version_id
        or model.full_configuration is None
        or model.full_configuration_hash is None
        or model.evidence_id is None
        or model.evidence_hash is None
    ):
        raise _error("双方目标的当前原版本、有效状态或完整模型已变化", "STALE_TRANSFER_GOAL")
    full = LongTermGoalPolicy.model_validate_json(json.dumps(model.full_configuration))
    if (
        configuration_hash(full.model_dump(mode="json")) != model.full_configuration_hash
        or full.minimum_guarantee_cents != goal.minimum_protection_cents
    ):
        raise _error("目标最低保障或完整配置摘要未核实")
    return GoalTransferGoalBinding(
        goal_id=goal.id,
        account_id=goal.account_id,
        policy_id=goal.policy_id,
        policy_version_id=goal.policy_version_id,
        full_model_evidence_id=model.evidence_id,
        full_model_evidence_hash=model.evidence_hash,
        full_configuration_hash=model.full_configuration_hash,
        minimum_guarantee_cents=full.minimum_guarantee_cents,
        target_cents=full.target_cents,
    )


def _candidate(
    session: Session,
    user: UUID,
    body: GoalTransferPreviewRequest,
    now: datetime,
    *,
    original_scope: GoalTransferCurrentScope | GoalTransferScope | None = None,
    retained_bases: list[GoalCashSourceProof] | None = None,
    require_scope_proof_match: bool = True,
) -> tuple[
    GoalTransferCurrentScope | GoalTransferScope,
    GoalTransferInventory,
    list[GoalReleaseUse],
    int,
    str,
    str,
]:
    _read_snapshot(session)
    with audit_read_scope(session):
        owner, epoch = session.get(User, user), current_audit_epoch(session, user)
        if (
            owner is None
            or not owner.is_simulated
            or epoch is None
            or epoch.status != "OPEN"
            or epoch.id != body.expected_epoch_id
        ):
            raise _error("需要当前本人模拟开放周期", "STALE_AUDIT_EPOCH")
        policy = read_full_policy(session, user, body.policy_id, now)
        version = policy.current_version
        if (
            policy.template_name != "CrossGoalReallocationPolicy"
            or policy.epoch_id != epoch.id
            or version.version_id != body.expected_policy_version_id
            or policy.effective_status not in {"ACTIVE", "CONFIRMED"}
            or not policy.planning_confirmation_valid
            or policy.reference_validation != "CURRENT"
            or version.valid_until is None
        ):
            raise _error("跨目标规则原版本或当前有效范围已变化", "STALE_TRANSFER_POLICY")
        try:
            config = GoalTransferPolicyConfiguration.model_validate_json(
                json.dumps(version.configuration)
            )
        except ValueError as error:
            raise _error("需要显式GOAL_TRANSFER_V1新规则；旧受保护现金授权不能继承") from error
        binding = GoalTransferPolicyBinding(
            policy_id=policy.policy_id,
            version_id=version.version_id,
            configuration=config,
            configuration_hash=version.content_hash,
        )
        source = _goal(
            session,
            user,
            body.source_goal_id,
            body.expected_source_policy_version_id,
            epoch.id,
            now,
        )
        target = _goal(
            session,
            user,
            body.target_goal_id,
            body.expected_target_policy_version_id,
            epoch.id,
            now,
        )
        if source.account_id == target.account_id:
            raise _error(
                "当前支持不同账户下的两个目标，请选择其他接收目标",
                "GOAL_TRANSFER_DISTINCT_ACCOUNTS_REQUIRED",
            )
        if owner.timezone not in {"Asia/Shanghai", "UTC"}:
            raise _error("当前本人时区不在有限目标间转移范围")
        timezone: Literal["Asia/Shanghai", "UTC"] = (
            "Asia/Shanghai" if owner.timezone == "Asia/Shanghai" else "UTC"
        )
        inventory = read_goal_transfer_inventory(
            session,
            user,
            source.goal_id,
            target.goal_id,
            epoch.id,
            now,
            retained_bases=retained_bases,
        )
        scope = original_scope or GoalTransferCurrentScope(
            user_id=user,
            epoch_id=epoch.id,
            policy=binding,
            permissions=[
                GoalTransferPermission(
                    role="RELEASE_SOURCE", goal=source, amount_cents=body.amount_cents
                ),
                GoalTransferPermission(
                    role="RECEIVE_TARGET", goal=target, amount_cents=body.amount_cents
                ),
            ],
            source_provenance_version="goal-transfer-current-provenance-v1",
            current_provenance_hash=inventory.current_proof.scope_input_hash,
            timezone=timezone,
            valid_from=now,
            valid_until=min(now + timedelta(minutes=15), version.valid_until),
        )
        if (
            scope.user_id,
            scope.epoch_id,
            scope.policy,
            scope.source,
            scope.target,
            scope.amount_cents,
        ) != (
            user,
            epoch.id,
            binding,
            source,
            target,
            body.amount_cents,
        ) or not scope.valid_from <= now < scope.valid_until:
            raise _error("双向原范围、两边版本或期限已失效", "STALE_TRANSFER_SCOPE")
        if not isinstance(scope, GoalTransferCurrentScope):
            raise _error(
                "旧basis scope须重新预览并取得显式current来源双向确认",
                "STALE_TRANSFER_PROOF_VERSION",
            )
        if (
            require_scope_proof_match
            and scope.current_provenance_hash != inventory.current_proof.scope_input_hash
        ):
            raise _error(
                "当前经济来源已改变，须原件重查后明确新双向scope", "STALE_TRANSFER_CURRENT_PROOF"
            )
        usage = goal_transfer_policy_lifetime_usage(
            user,
            policy.policy_id,
            inventory.releases,
            inventory.transfers,
            actual_release_bank_ids={row.bank_operation_id for row in inventory.releases},
            actual_transfer_bank_ids={row.bank_operation_id for row in inventory.transfers},
            now=now,
        )
        if usage + body.amount_cents > config.total_cap_cents:
            raise _error("原规则所有版本的累计释放及转移额度已不足")
        uses = select_original_release_uses(
            inventory.ownership.remaining_original_uses[source.goal_id], body.amount_cents
        )
        full = compute_full_annual_protection(session, user, now)
        context, matched, exposures = load_verified_financial_context(session, user, now)
        context, issues, _ = finalize_financial_context(context, full.audit)
        if (
            not matched
            or issues
            or full.source_issues
            or not full.audit.complete
            or full.audit.status != "VALID"
        ):
            raise _error("当前完整银行、审计或保护来源未核实")
        data = FullProtectionProjectionInput(
            snapshot=context.snapshot,
            boundary_versions=context.versions,
            positions=context.positions,
            boundary_products=context.products,
            policies=full.full_policy_sources,
            reserved_cash_by_account={
                identity: amount
                for exposure in exposures or []
                for identity, amount in exposure.reserved_cash_by_account.items()
            },
            full_source_inventory_complete=full.projection.status != "UNKNOWN",
        )
        if (full.user_id, full.as_of) != (user, now) or (
            project_full_protection(data).input_hash != full.projection.input_hash
        ):
            raise _error("实际完整保护输入与本次本人原快照不一致")
        before, after = project_goal_transfer_protection(data, scope)
        protection_hash = configuration_hash(
            {
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json"),
                "full_input": full.input_digest,
            }
        )
        financial_hash = configuration_hash(
            {
                "inventory": inventory.input_hash,
                "scope": scope.model_dump(mode="json"),
                "uses": [row.model_dump(mode="json") for row in uses],
                "protection": protection_hash,
                "now": now.isoformat(),
            }
        )
        return scope, inventory, uses, usage, protection_hash, financial_hash


def _preview_id(user: UUID, epoch: UUID, key: str) -> UUID:
    return uuid5(NAMESPACE, f"preview:{user}:{epoch}:{key}")


def _preview_original(
    session: Session, user: UUID, identity: UUID, now: datetime
) -> tuple[GoalTransferPreview, GoalTransferPreviewRequest, GoalTransferCurrentProof | None]:
    result = get_decision_trace(session, user, identity, now)
    trace = result.trace
    if (
        result.completeness != "COMPLETE"
        or result.audit_chain_status != "VALID"
        or trace is None
        or trace.algorithm_versions.get("goal_transfer") != ALGORITHM
    ):
        raise _error("原转移预览及其完整审计未验真", "GOAL_TRANSFER_PREVIEW_INTEGRITY_ERROR")
    preview = GoalTransferPreview.model_validate_json(
        json.dumps(trace.outcome["goal_transfer_preview"])
    )
    request = GoalTransferPreviewRequest.model_validate_json(
        json.dumps(trace.inputs["original_preview_request"])
    )
    proof = (
        GoalTransferCurrentProof.model_validate_json(
            json.dumps(trace.outcome["goal_transfer_current_proof"])
        )
        if "goal_transfer_current_proof" in trace.outcome
        else None
    )
    if (
        preview.preview_id != identity
        or identity != _preview_id(user, request.expected_epoch_id, request.idempotency_key)
        or preview.user_id != user
        or preview.epoch_id != request.expected_epoch_id
        or preview.scope_hash != configuration_hash(preview.scope.model_dump(mode="json"))
    ):
        raise _error("原转移预览范围被替换")
    if proof is not None and (
        not isinstance(preview.scope, GoalTransferCurrentScope)
        or preview.scope.current_provenance_hash != proof.scope_input_hash
        or (proof.user_id, proof.epoch_id, proof.source_goal_id, proof.target_goal_id)
        != (user, preview.epoch_id, preview.scope.source.goal_id, preview.scope.target.goal_id)
    ):
        raise _error("原current preview来源证明与已审双向scope不同")
    return preview, request, proof


def read_goal_transfer_preview_by_key(
    session: Session, user: UUID, epoch: UUID, key: str, now: datetime
) -> GoalTransferPreview | None:
    _read_snapshot(session)
    identity = _preview_id(user, epoch, key)
    row = session.get(DecisionRun, identity)
    if row is None:
        return None
    return _preview_original(session, user, identity, now)[0]


def preview_goal_transfer(
    engine: Engine, user_id: UUID, body: GoalTransferPreviewRequest, now: datetime
) -> GoalTransferPreview:
    body = GoalTransferPreviewRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    identity = _preview_id(user_id, body.expected_epoch_id, body.idempotency_key)
    with audit_command_guard(engine, user_id), Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        if writer.get(DecisionRun, identity) is not None:
            preview, request, _ = _preview_original(writer, user_id, identity, now)
            if request != body:
                raise _error("原预览键不能换双目标、金额或版本", "IDEMPOTENCY_CONFLICT")
            return preview
        with _reader(engine) as reader:
            scope, inventory, _, usage, _, digest = _candidate(reader, user_id, body, now)
        preview = GoalTransferPreview(
            preview_id=identity,
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            scope=scope,
            scope_hash=configuration_hash(scope.model_dump(mode="json")),
            input_hash=digest,
            source_available_cents=sum(
                row.amount_cents
                for row in inventory.ownership.remaining_original_uses[scope.source.goal_id]
            ),
            target_current_cents=inventory.ownership.goal_cash_cents[scope.target.goal_id],
            policy_used_cents=usage,
            expires_at=scope.valid_until,
        )
        trace = build_trace(
            run_id=identity,
            user_id=user_id,
            phase="EVALUATION",
            as_of=now,
            parent_run_id=None,
            action_id=None,
            algorithm_versions={"goal_transfer": ALGORITHM},
            inputs={"original_preview_request": body.model_dump(mode="json")},
            sources=capture_sources(
                writer,
                user_id,
                {
                    scope.source.full_model_evidence_id,
                    scope.target.full_model_evidence_id,
                    inventory.current_proof.income_evidence_id,
                },
            ),
            policies=[],
            constraints=[],
            candidates=[],
            outcome={
                "goal_transfer_preview": preview.model_dump(mode="json"),
                "goal_transfer_current_proof": inventory.current_proof.model_dump(mode="json"),
            },
        )
        record_trace(writer, trace)
        return preview


def _authorization(action: ActionPlan, session: Session) -> GoalTransferAuthorization:
    command = read_goal_transfer_command(action)
    original = GoalTransferAuthorization.model_validate_json(
        json.dumps(action.request["goal_transfer_authorization"])
    )
    proof = session.get(EvidenceItem, command.effect.authorization_evidence_id)
    if (
        proof is None
        or proof.user_id != action.user_id
        or proof.source_type != AUTH_SOURCE
        or proof.evidence_level != "USER_CONFIRMED_POLICY"
        or proof.content != original.model_dump(mode="json")
        or proof.content_hash != command.effect.authorization_evidence_hash
        or proof.content_hash != configuration_hash(proof.content)
    ):
        raise _error("双向USER原确认凭证缺失或已被替换")
    return original


def _trace(
    session: Session,
    action: ActionPlan,
    now: datetime,
    phase: Literal["PREPARE", "BANK_ACCEPT"],
    candidate_hash: str,
    *,
    existing_run: DecisionRun | None = None,
) -> None:
    command = read_goal_transfer_command(action)
    effect = command.effect
    ids = {
        effect.authorization_evidence_id,
        effect.scope.source.full_model_evidence_id,
        effect.scope.target.full_model_evidence_id,
    }
    trace = build_trace(
        run_id=action.decision_run_id
        if phase == "PREPARE"
        else uuid5(action.id, "bank-accept-decision"),
        user_id=action.user_id,
        phase=phase,
        as_of=now,
        action_id=action.id,
        parent_run_id=None if phase == "PREPARE" else action.decision_run_id,
        algorithm_versions={"goal_transfer": ALGORITHM},
        inputs={
            "goal_transfer_effect": effect.model_dump(mode="json"),
            "action_request": action.request,
            "actual_candidate_hash": candidate_hash,
        },
        sources=capture_sources(session, action.user_id, ids),
        policies=[],
        constraints=[],
        candidates=[],
        outcome={
            "action_id": str(action.id),
            "effect_hash": command.effect_hash,
            "decision": "READY",
            "creates_new_income": False,
        },
    )
    record_trace(session, trace, existing_run=existing_run)


def prepare_goal_transfer(
    engine: Engine,
    user_id: UUID,
    body: GoalTransferPrepareRequest,
    now: datetime,
    principal: LocalActorPrincipal,
) -> GoalTransferActionResponse:
    body = GoalTransferPrepareRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    identity = goal_transfer_action_identity(user_id, body.expected_epoch_id, body.idempotency_key)
    with audit_command_guard(engine, user_id), Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        existing = writer.get(ActionPlan, identity)
        if existing is not None:
            if existing.user_id != user_id or existing.request.get(
                "original_prepare_request"
            ) != body.model_dump(mode="json"):
                raise _error("转移原键不能换请求或金额", "IDEMPOTENCY_CONFLICT")
            return _read_action(writer, user_id, identity, now)
        require_local_user(principal, user_id, now)
        with _reader(engine) as reader:
            preview, request, preview_proof = _preview_original(
                reader, user_id, body.preview_id, now
            )
            if preview_proof is None or not isinstance(preview.scope, GoalTransferCurrentScope):
                raise _error("需要显式当前来源证明的新双向预览")
            if (
                preview.scope_hash != body.reviewed_scope_hash
                or preview.epoch_id != body.expected_epoch_id
            ):
                raise _error("必须明确同意刚核对的双向原范围", "GOAL_TRANSFER_SCOPE_HASH_MISMATCH")
            scope, inventory, uses, _, protection_hash, financial_hash = _candidate(
                reader, user_id, request, now, original_scope=preview.scope
            )
        original_request = GoalTransferConfirmRequest.model_validate_json(
            body.model_dump_json(exclude={"preview_id"})
        )
        authorization = GoalTransferAuthorization(
            authorization_id=identity,
            scope=scope,
            scope_hash=preview.scope_hash,
            original_request=original_request,
            principal_at_confirmation=principal,
            confirmed_at=now,
            request_hash=configuration_hash(
                {"user_id": str(user_id), "request": original_request.model_dump(mode="json")}
            ),
        )
        auth_content = authorization.model_dump(mode="json")
        proof = EvidenceItem(
            id=uuid5(identity, "authorization-evidence"),
            user_id=user_id,
            created_at=now,
            evidence_level="USER_CONFIRMED_POLICY",
            source_type=AUTH_SOURCE,
            source_ref=str(identity),
            content=auth_content,
            content_hash=configuration_hash(auth_content),
            observed_at=now,
            valid_from=now,
            valid_to=scope.valid_until,
            status="VALID",
        )
        writer.add(proof)
        writer.flush()
        bank_key = goal_transfer_bank_key(user_id, body.expected_epoch_id, body.idempotency_key)
        effect = GoalTransferEffect(
            operation_id=identity,
            business_key=bank_key,
            bank_idempotency_key=bank_key,
            authorization_id=identity,
            authorization_evidence_id=proof.id,
            authorization_evidence_hash=proof.content_hash,
            authorization_request_hash=authorization.request_hash,
            scope=scope,
            scope_hash=preview.scope_hash,
            source_uses=uses,
            source_provenance_hash=inventory.current_proof.proof_hash,
            target_ownership_hash=inventory.target_ownership_hash,
            financial_input_hash=financial_hash,
            protection_input_hash=protection_hash,
            valid_from=now,
            expires_at=scope.valid_until,
        )
        command = GoalTransferBankCommand(
            effect=effect, effect_hash=goal_transfer_effect_hash(effect)
        )
        payload = {
            "protocol": "goal-transfer-action-v2",
            "original_prepare_request": body.model_dump(mode="json"),
            "original_preview_request": request.model_dump(mode="json"),
            "goal_transfer_execution": command.model_dump(mode="json"),
            "goal_transfer_authorization": auth_content,
            "goal_transfer_current_proof": inventory.current_proof.model_dump(mode="json"),
        }
        initial = {
            "original_prepare_request": body.model_dump(mode="json"),
            "effect_hash": command.effect_hash,
        }
        run = DecisionRun(
            id=uuid5(identity, "decision"),
            user_id=user_id,
            created_at=now,
            idempotency_key="goal-transfer-decision:" + str(identity),
            trigger_type="ACTION_PREPARE",
            algorithm_version=ALGORITHM,
            as_of=now,
            input_snapshot=initial,
            snapshot_hash=configuration_hash(initial),
            policy_version_ids=[
                str(scope.source.policy_version_id),
                str(scope.target.policy_version_id),
            ],
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
            policy_version_id=scope.source.policy_version_id,
            source_account_id=scope.source.account_id,
            destination_account_id=scope.target.account_id,
            goal_id=scope.source.goal_id,
            product_id=None,
            position_id=None,
            action_type="TRANSFER_GOAL",
            amount_cents=scope.amount_cents,
            autonomy_level="ASK_ONCE",
            status="PLANNED",
            idempotency_key=bank_key,
            request=payload,
            request_hash=configuration_hash(payload),
            authorized_at=now,
            expires_at=effect.expires_at,
        )
        writer.add(action)
        writer.flush()
        _trace(writer, action, now, "PREPARE", financial_hash, existing_run=run)
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


def _read_action(
    session: Session, user: UUID, identity: UUID, now: datetime
) -> GoalTransferActionResponse:
    action = session.get(ActionPlan, identity)
    if action is None or action.user_id != user:
        raise _error("当前本人原目标间转移不存在", "NOT_FOUND", 404)
    command, authorization = read_goal_transfer_command(action), _authorization(action, session)
    trace = get_decision_trace(session, user, action.decision_run_id, now)
    if (
        trace.completeness != "COMPLETE"
        or trace.audit_chain_status != "VALID"
        or trace.trace is None
    ):
        raise _error("目标间转移原决策或审计未完整验真")
    operations = list(
        session.scalars(
            select(BankOperation)
            .where(BankOperation.user_id == user, BankOperation.action_plan_id == identity)
            .limit(2)
        )
    )
    receipts = list(
        session.scalars(
            select(ActionReceipt)
            .where(ActionReceipt.user_id == user, ActionReceipt.action_plan_id == identity)
            .limit(2)
        )
    )
    if len(operations) > 1 or len(receipts) > 1 or receipts and not operations:
        raise _error("目标间转移原银行或原回执不唯一")
    bank, receipt = operations[0] if operations else None, receipts[0] if receipts else None
    verified = False
    if bank is not None:
        read_goal_transfer_bank_command(session, bank, now)
        if bank.status == "SETTLED":
            verify_goal_transfer_legs(session, bank, now)
            verified = True
        if receipt is not None:
            verify_goal_transfer_receipt(session, bank, receipt, now)
    if action.status in {"SUCCEEDED", "RECONCILED"} and (not verified or receipt is None):
        raise _error("成功状态不能替代原四腿和双归属回执")
    return GoalTransferActionResponse(
        user_id=user,
        as_of=now,
        action_id=identity,
        original_action_status=action.status,
        original_command=command,
        original_prepare_request=GoalTransferPrepareRequest.model_validate_json(
            json.dumps(action.request["original_prepare_request"])
        ),
        original_request_hash=action.request_hash,
        authorization=authorization,
        authorization_evidence_id=command.effect.authorization_evidence_id,
        authorization_evidence_hash=command.effect.authorization_evidence_hash,
        bank_operation_id=bank.id if bank else None,
        original_bank_status=bank.status if bank else None,
        bank_settlement_legs_verified=verified,
        receipt_id=receipt.id if receipt else None,
        service_receipt_verified=receipt is not None,
        original_receipt=row_copy(receipt) if receipt else None,
        unresolved=action.status in {"SUBMITTED", "UNKNOWN"}
        or bank is not None
        and receipt is None,
    )


def read_goal_transfer_action(
    session: Session, user: UUID, action_id: UUID, now: datetime
) -> GoalTransferActionResponse:
    _read_snapshot(session)
    return _read_action(session, user, action_id, _now(now))


def read_goal_transfer_by_key(
    session: Session, user: UUID, epoch: UUID, key: str, now: datetime
) -> GoalTransferLookup:
    _read_snapshot(session)
    identity = goal_transfer_action_identity(user, epoch, key)
    exists = session.get(ActionPlan, identity)
    original = _read_action(session, user, identity, _now(now)) if exists is not None else None
    return GoalTransferLookup(
        user_id=user,
        epoch_id=epoch,
        idempotency_key=key,
        status="RECORDED" if original else "NOT_FOUND_NOT_FINAL",
        original=original,
    )


def _fresh_candidate(engine: Engine, action: ActionPlan, now: datetime) -> str:
    command = read_goal_transfer_command(action)
    effect = command.effect
    with _reader(engine) as reader:
        authorization = _authorization(action, reader)
        request = GoalTransferPreviewRequest.model_validate_json(
            json.dumps(action.request["original_preview_request"])
        )
        if action.request.get("protocol") != "goal-transfer-action-v2":
            raise _error("旧未受理basis动作需要新current scope；已settled原件仍可原key恢复")
        saved_proof = GoalTransferCurrentProof.model_validate_json(
            json.dumps(action.request["goal_transfer_current_proof"])
        )
        validate_current_goal_transfer_effect(saved_proof, effect)
        scope, inventory, _, used, _, digest = _candidate(
            reader,
            action.user_id,
            request,
            now,
            original_scope=effect.scope,
            require_scope_proof_match=False,
        )
        validate_goal_transfer_authorization(
            command,
            authorization,
            current_policy=scope.policy,
            current_source=scope.source,
            current_target=scope.target,
            current_epoch=scope.epoch_id,
            policy_lifetime_used_cents=used,
            now=now,
        )
        available = {
            (row.allocation_action_id, row.fragment_id): row
            for row in inventory.ownership.remaining_original_uses[effect.source_goal_id]
        }
        if inventory.target_ownership_hash != effect.target_ownership_hash:
            raise _error("接收目标归属已变化，不能替换已确认目标原件")
        for use in effect.source_uses:
            current = available.get((use.allocation_action_id, use.fragment_id))
            if (
                current is None
                or use.amount_cents > current.amount_cents
                or any(
                    getattr(use, key) != getattr(current, key)
                    for key in GoalReleaseUse.model_fields
                    if key != "amount_cents"
                )
            ):
                raise _error("原精确allocation切片已用尽或改源，不能用新收入替旧key重扣")
    return digest


def validate_goal_transfer_exposure(
    action: ActionPlan,
    declaration: dict[str, Any],
    receipts: list[ActionReceipt],
    operations: list[BankOperation],
    reservations: list[ActionResourceReservation],
    postings: list[SimulatedBankPosting],
    epoch: datetime,
    *,
    session: Session | None = None,
) -> GoalTransferBankCommand:
    command = read_goal_transfer_command(action)
    ops = [row for row in operations if row.action_plan_id == action.id]
    received = [row for row in receipts if row.action_plan_id == action.id]
    claims = [row for row in reservations if row.action_plan_id == action.id]
    legs = [row for row in postings if row.operation_id == action.id]
    if (
        action.created_at > epoch
        or declaration.get("action_id") != str(action.id)
        or claims
        or len(ops) > 1
        or len(received) > 1
    ):
        raise ValueError(
            "Paired transfer exposure cannot hide claims, Bank originals or duplicates"
        )
    state = declaration.get("state")
    if state in {"PLANNED_UNRESERVED", "TRANSFER_PENDING_UNRESERVED"}:
        allowed = {"PLANNED", "AUTHORIZED"} if state == "PLANNED_UNRESERVED" else {"SUBMITTED"}
        if (
            set(declaration) != {"action_id", "state"}
            or action.status not in allowed
            or ops
            or received
            or legs
        ):
            raise ValueError("Pending transfer is not a settled or reserved result")
    elif state == "EXECUTION_SETTLED":
        if (
            session is None
            or action.status not in {"SUCCEEDED", "RECONCILED"}
            or len(ops) != 1
            or len(received) != 1
            or set(declaration) != {"action_id", "state", "receipt_id"}
            or declaration["receipt_id"] != str(received[0].id)
        ):
            raise ValueError("Completed transfer requires its exact original receipt")
        verify_goal_transfer_receipt(session, ops[0], received[0], epoch)
    elif state == "NO_EFFECT":
        if (
            set(declaration) != {"action_id", "state"}
            or action.status not in {"CANCELLED", "INVALIDATED"}
            or ops
            or received
            or legs
        ):
            raise ValueError("Only a verified absent economic effect releases transfer capacity")
    else:
        raise ValueError("Original transfer result is unresolved, never guessed as no effect")
    return command


def recheck_goal_transfer_actions(
    session: Session,
    user_id: UUID,
    full_policy_id: UUID,
    current_version_id: UUID,
    current_status: str,
    command_id: UUID,
    now: datetime,
) -> GoalTransferRecheckResult:
    invalidated, inflight = [], []
    actions = list(
        session.scalars(
            select(ActionPlan)
            .where(ActionPlan.user_id == user_id, ActionPlan.action_type == "TRANSFER_GOAL")
            .order_by(ActionPlan.id)
            .limit(10001)
        )
    )
    if len(actions) > 10000:
        raise _error("原目标间转移分母超出完整检查容量")
    for action in actions:
        effect = read_goal_transfer_command(action).effect
        if effect.scope.policy.policy_id != full_policy_id or action.status in {
            "SUCCEEDED",
            "RECONCILED",
            "CANCELLED",
            "INVALIDATED",
        }:
            continue
        bank = session.scalar(
            select(BankOperation).where(
                BankOperation.user_id == user_id, BankOperation.action_plan_id == action.id
            )
        )
        if bank is not None or action.status in {"SUBMITTED", "UNKNOWN"}:
            inflight.append(action.id)
        elif effect.scope.policy.version_id != current_version_id or current_status not in {
            "ACTIVE",
            "CONFIRMED",
        }:
            _transition(
                session, action, "INVALIDATED", now, "TRANSFER_POLICY_CURRENT_VERSION_CHANGED"
            )
            invalidated.append(action.id)
    if invalidated:
        _epochs(session, user_id, now, command_id)
        refresh_execution_exposure(
            session,
            user_id,
            now,
            command_id,
            declarations={
                identity: {"action_id": str(identity), "state": "NO_EFFECT"}
                for identity in invalidated
            },
        )
    return GoalTransferRecheckResult(
        invalidated_action_ids=invalidated, inflight_action_ids=inflight
    )


def process_goal_transfer_bank(
    engine: Engine, user_id: UUID, action_id: UUID, now: datetime
) -> None:
    from app.services.full_goal_transfer_projection import process_goal_transfer_bank as original

    original(engine, user_id, action_id, now)


def project_goal_transfer_execution(
    session: Session, operation: BankOperation, now: datetime
) -> ActionReceipt:
    from app.services.full_goal_transfer_projection import (
        project_goal_transfer_execution as original,
    )

    return original(session, operation, now)


def execute_goal_transfer(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: GoalTransferExecuteRequest,
    now: datetime,
    principal: LocalActorPrincipal,
) -> GoalTransferActionResponse:
    now = _now(now)
    with audit_command_guard(engine, user_id):
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            action = writer.get(ActionPlan, action_id)
            if action is None or action.user_id != user_id:
                raise _error("当前本人原转移动作不存在", "NOT_FOUND", 404)
            command = read_goal_transfer_command(action)
            if (
                body.expected_epoch_id != command.effect.epoch_id
                or body.reviewed_effect_hash != command.effect_hash
                or body.accepted is not True
            ):
                raise _error("只能接受或核对同一原转移效果")
            original = _read_action(writer, user_id, action_id, now)
            if original.service_receipt_verified:
                return original
            bank = writer.scalar(
                select(BankOperation).where(
                    BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                )
            )
            if bank is None:
                if action.status not in {"PLANNED", "AUTHORIZED"}:
                    raise _error("原结果未决或已失效，不能另建银行效果")
                require_local_user(principal, user_id, now)
                _fresh_candidate(engine, action, now)
                _transition(
                    writer, action, "AUTHORIZED", now, "GOAL_TRANSFER_USER_EXECUTE_CONFIRMED"
                )
                _epochs(writer, user_id, now, action_id)
                refresh_execution_exposure(
                    writer,
                    user_id,
                    now,
                    action_id,
                    declarations={
                        action_id: {
                            "action_id": str(action_id),
                            "state": "PLANNED_UNRESERVED",
                        }
                    },
                )
        try:
            process_goal_transfer_bank(engine, user_id, action_id, now)
        except PolicyLifecycleError:
            raise
        except Exception:
            try:
                _record_unknown(engine, user_id, action_id, now)
            except Exception:
                pass
            raise
        try:
            with Session(engine) as projection, projection.begin():
                _user(projection, user_id)
                bank = projection.scalar(
                    select(BankOperation).where(
                        BankOperation.user_id == user_id, BankOperation.action_plan_id == action_id
                    )
                )
                if bank is None:
                    raise _error("独立银行原件暂不可读，保留原键")
                project_goal_transfer_execution(projection, bank, now)
                return _read_action(projection, user_id, action_id, now)
        except Exception:
            try:
                _record_unknown(engine, user_id, action_id, now)
            except Exception:
                pass
            raise


def _record_unknown(engine: Engine, user_id: UUID, action_id: UUID, now: datetime) -> None:
    with Session(engine) as failed, failed.begin():
        _user(failed, user_id)
        action = failed.get(ActionPlan, action_id)
        if (
            action is not None
            and action.user_id == user_id
            and action.status not in {"SUCCEEDED", "RECONCILED"}
        ):
            _transition(failed, action, "UNKNOWN", now, "GOAL_TRANSFER_ORIGINAL_RESULT_UNKNOWN")
