"""One explicit USER whole-maturity command through the original independent bank.

No price, amount, clock, permission or receipt is accepted from the HTTP caller.
The two installed private hooks are mandatory before any new preparation.
"""

import json
from datetime import datetime, time
from typing import Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.audit_guard import audit_command_guard
from app.db.models import (
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    AuditEpoch,
    BankOperation,
    DecisionRun,
    EvidenceItem,
    Goal,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
)
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace, verify_trace
from app.domain.full_maturity_execution import (
    ALGORITHM,
    CONSENT_SOURCE,
    GUARDS_VERSION,
    KEY_PREFIX,
    MARKER,
    VALIDATION_INFO_KEY,
    FullMaturityConfirmation,
    FullMaturityConsent,
    FullMaturityExecuteRequest,
    FullMaturityInput,
    FullMaturityProof,
    FullMaturityRequest,
    MaturityContractCommand,
    derive_maturity_proof,
    maturity_action_payload,
    maturity_bank_key,
    verify_frozen_maturity_trace,
    verify_maturity_consent,
)
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_protection_projection import FullProtectionProjectionInput
from app.domain.full_recovery_planning import FullRecoveryPlanningInput
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.audit_recording import (
    audit_subject_data,
    record_action_created,
    record_action_transition,
)
from app.services.dashboard_helpers import current_epoch_audit
from app.services.decision_trace import (
    capture_policies,
    capture_sources,
    get_decision_trace,
    record_trace,
)
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_maturity_replanning import read_original_maturity
from app.services.full_policy_lifecycle import _read_snapshot, list_full_policies, read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.full_recovery_execution import _reader
from app.services.full_recovery_planning import _holdings, _linked_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user, is_version_authorized
from app.services.product_catalog import read_catalog
from app.services.recovery_projection import finalize_projections, project_request, refresh_exposure
from app.services.recovery_sources import _version
from app.services.simulated_bank import BankRequest, ledger_heads
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def _fail(code: str, message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


class FullMaturityPreview(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    read_only: Literal[True] = True
    user_id: UUID
    original_request: FullMaturityRequest
    proof: FullMaturityProof
    limitations: list[str]


class FullMaturityAction(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    user_id: UUID
    action_id: UUID
    epoch_id: UUID
    original_request: FullMaturityRequest
    original_action_request: dict[str, Any]
    client_request_hash: str
    server_request_hash: str
    reviewed_command_hash: str
    bank_key: str
    autonomy_level: Literal["ASK_ONCE"] = "ASK_ONCE"
    status: str
    command: MaturityContractCommand
    original_consent: FullMaturityConsent | None
    original_event: dict[str, Any]
    economic_verified: Literal[False] = False
    service_receipt_verified: bool
    epoch_state: Literal["OPEN", "SEALED", "MISSING"]
    historical: bool


class FullMaturityLookup(BoundaryModel):
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    not_found_is_final: Literal[False] = False
    user_id: UUID
    idempotency_key: str
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    action: FullMaturityAction | None = None


LIMITATIONS = [
    "ONE_WHOLE_ORIGINAL_FIXED_MATURE_POSITION_SIGNED_USER_ASK_ONLY",
    "CONDITIONAL_RETURN_IS_NOT_CURRENT_CASH_BEFORE_NATIVE_SETTLEMENT_AND_RECEIPT",
    "ORIGINAL_AUTO_BATCH_MATURITY_AND_OLD_HASHES_ARE_UNCHANGED",
    "NO_PARTIAL_EARLY_LOSSY_ATOMIC_MULTI_POSITION_OR_YIELD_PAYMENT",
    "REMAINING_NEGATIVE_CHECKPOINTS_ARE_NOT_A_COMPLETE_RECOVERY_SOLUTION",
    "OLD_BANK_RESULT_RECOVERY_USES_ORIGINAL_KEY_AFTER_POLICY_REVOKE_NO_NEW_ACCEPTANCE",
]


def require_installed_guard() -> None:
    from app.services import asset_exposure_import, simulated_bank

    if (
        getattr(simulated_bank, "FULL_MATURITY_GUARDS_VERSION", None) != GUARDS_VERSION
        or getattr(asset_exposure_import, "FULL_MATURITY_GUARDS_VERSION", None) != GUARDS_VERSION
    ):
        raise _fail("FULL_MATURITY_NOT_IMPLEMENTED", "原银行与完整曝光严格接缝均须已安装")


def read_current_maturity_input(
    session: Session, user_id: UUID, body: FullMaturityRequest, now: datetime
) -> FullMaturityInput:
    _read_snapshot(session)
    now = _now(now)
    full = read_full_policy(session, user_id, body.policy_id, now)
    if full.template_name != "RecoveryPolicy":
        raise _fail("FULL_MATURITY_SCOPE_REQUIRED", "需要当前已确认的完整恢复策略")
    config = RecoveryPolicy.model_validate(full.current_version.configuration)
    epoch = session.get(AuditEpoch, body.expected_epoch_id)
    if (
        epoch is None
        or epoch.user_id != user_id
        or epoch.status != "OPEN"
        or full.epoch_id != epoch.id
        or epoch.opened_at > now
    ):
        raise _fail("FULL_MATURITY_EPOCH_CHANGED", "需要实际用户当前开放轮次")
    context, bank_matched, exposures = load_verified_financial_context(session, user_id, now)
    if not bank_matched:
        raise _fail("FULL_MATURITY_BANK_SOURCE_UNKNOWN", "独立银行与应用全账未匹配")
    audit = current_epoch_audit(session, user_id, [])
    if audit.status != "VALID" or not audit.complete:
        raise _fail("FULL_MATURITY_AUDIT_UNKNOWN", "当前完整审计必须实际有效")
    catalogue = read_catalog(session, now)
    if not catalogue.complete_within_registered_capacity or catalogue.issues:
        raise _fail("FULL_MATURITY_CATALOG_UNKNOWN", "完整不可变目录未证明")
    linked = _linked_policy(session, context, config)
    manual = {key for scope in (exposures or []) for key in scope.excluded_manual_position_ids}
    holdings, _ = _holdings(session, context, catalogue, manual)
    current_positions = list(
        session.scalars(
            select(AssetPosition)
            .where(AssetPosition.user_id == user_id, AssetPosition.status != "REDEEMED")
            .limit(101)
        )
    )
    if len(current_positions) > 100 or len(holdings) != len(current_positions):
        raise _fail("FULL_MATURITY_POSITION_INVENTORY_UNKNOWN", "完整持仓分母不得丢失")
    selected = [row for row in holdings if row.original.position_id == body.position_id]
    position = session.get(AssetPosition, body.position_id)
    if len(selected) != 1 or position is None or position.user_id != user_id:
        raise _fail("FULL_MATURITY_POSITION_UNKNOWN", "当前用户唯一原持仓未证明")
    original = selected[0].original.original_authorization
    if original is None:
        raise _fail("FULL_MATURITY_ACQUISITION_UNKNOWN", "原持仓购买授权未证明")
    mvp = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == original.policy_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if mvp is None:
        raise _fail("FULL_MATURITY_CURRENT_MVP_MISSING", "当前原MVP版本缺失")
    # A FULL planning declaration cannot replace the current original MVP grant.
    current_mvp = _version(mvp)
    from app.domain.full_recovery_planning import _linked_configuration

    if current_mvp.configuration.get("scope") != config.scope or current_mvp.configuration.get(
        "goal_id"
    ) != (str(config.goal_id) if config.goal_id else None):
        raise _fail("FULL_MATURITY_CURRENT_MVP_SCOPE_CHANGED", "当前原MVP范围已经改变")
    goal_deadline = None
    goal_valid = False
    if config.goal_id is not None:
        from app.services.asset_allocation import _goal_projection_matches

        goal = session.get(Goal, config.goal_id)
        version = session.scalar(
            select(PolicyVersion)
            .where(
                PolicyVersion.user_id == user_id,
                PolicyVersion.policy_id == goal.policy_id if goal else PolicyVersion.id.is_(None),
            )
            .order_by(PolicyVersion.version_number.desc())
            .limit(1)
        )
        goal_valid = bool(
            goal
            and goal.user_id == user_id
            and version
            and goal.policy_version_id == version.id
            and _goal_projection_matches(goal, version)
            and is_version_authorized(session, user_id, version.id, now)
        )
        if not goal_valid or goal is None:
            raise _fail("FULL_MATURITY_CURRENT_GOAL_UNKNOWN", "原目标当前版本与归属未证明")
        goal_deadline = datetime.combine(goal.deadline, time(), ZoneInfo(context.snapshot.timezone))
    full_projection = compute_full_annual_protection(session, user_id, now)
    protection_inventory = [
        row
        for row in list_full_policies(session, user_id, now).items
        if row.template_name
        in {"DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"}
    ]
    if (
        len(protection_inventory) > 200
        or {row.policy_id for row in protection_inventory}
        != {row.policy_id for row in full_projection.full_policy_sources}
        or any(row.state == "UNKNOWN" for row in full_projection.projection.policy_states)
    ):
        raise _fail("FULL_MATURITY_FULL_INVENTORY_UNKNOWN", "当前完整保护目录或声明未证明")
    context.sources.used.update(full.current_version.evidence_ids)
    context.sources.used.update(full_projection.source_evidence_ids)
    context.sources.used.update(UUID(key) for key in mvp.evidence_ids)
    context, issues, _ = finalize_financial_context(context, audit)
    issue_pairs = {(row.code, row.source_ref) for row in issues}
    issue_pairs.update((row.code, row.source_ref) for row in full_projection.source_issues)
    plan = FullRecoveryPlanningInput(
        user_id=user_id,
        policy_id=body.policy_id,
        policy_version_id=full.current_version.version_id,
        configuration=config,
        planning_confirmation_valid=full.planning_confirmation_valid,
        confirmed_at=full.current_version.confirmed_at,
        valid_from=full.current_version.valid_from,
        valid_until=full.current_version.valid_until,
        linked_asset_policy=linked,
        snapshot=context.snapshot.model_copy(update={"horizon_days": 365}),
        boundary_versions=context.versions,
        positions=context.positions,
        boundary_products=context.products,
        holdings=holdings,
        goal_deadline_at=goal_deadline,
        goal_reference_verified=goal_valid,
    )
    # Apply the exact current MVP limits even if linked planning is a FULL policy.
    legacy_plan = plan.model_copy(
        update={
            "linked_asset_policy": linked.model_copy(
                update={
                    "policy_id": mvp.policy_id,
                    "version_id": mvp.id,
                    "kind": "MVP_POLICY",
                    "configuration": current_mvp.configuration,
                    "content_hash": mvp.content_hash,
                    "confirmation_valid": is_version_authorized(session, user_id, mvp.id, now),
                    "confirmed_at": mvp.confirmed_at,
                    "valid_from": mvp.valid_from,
                    "valid_until": mvp.valid_until,
                }
            ),
            "configuration": config.model_copy(update={"asset_policy_id": mvp.policy_id}),
        }
    )
    from app.domain.full_recovery_planning import _candidate

    legacy = _candidate(legacy_plan, selected[0], _linked_configuration(legacy_plan), None)
    from app.domain.full_maturity_execution import explicit_mature_ask_eligible

    authorized = is_version_authorized(session, user_id, mvp.id, now) and (
        explicit_mature_ask_eligible(legacy)
    )
    head = ledger_heads(session, user_id).get("POSITION:" + str(body.position_id))
    if head is None or head.position_id != body.position_id:
        raise _fail("FULL_MATURITY_BANK_HEAD_MISSING", "独立本金链原件缺失")
    catalogues = [row for row in catalogue.versions if row.id == selected[0].catalogue_version_id]
    if len(catalogues) != 1:
        raise _fail("FULL_MATURITY_CATALOG_BINDING_INVALID", "原持仓目录版本未匹配")
    originals = capture_sources(session, user_id, context.sources.used)
    return FullMaturityInput(
        user_id=user_id,
        epoch_id=epoch.id,
        request=body,
        as_of=now,
        current_effective_status=full.effective_status,
        current_reference_validation=full.reference_validation,
        planning=plan,
        protection=FullProtectionProjectionInput(
            snapshot=plan.snapshot,
            boundary_versions=plan.boundary_versions,
            positions=plan.positions,
            boundary_products=plan.boundary_products,
            policies=full_projection.full_policy_sources,
            reserved_cash_by_account={
                key: amount
                for item in (exposures or [])
                for key, amount in item.reserved_cash_by_account.items()
            },
            full_source_inventory_complete=True,
            full_source_issues=[
                code + ":" + ref
                for code, ref in issue_pairs
                if (code, ref) != ("UNRECONCILED_POSITION_AVAILABILITY", str(body.position_id))
            ],
        ),
        current_mvp_authorized=authorized,
        independent_bank_principal_cents=head.balance_after_cents,
        independent_bank_head=row_copy(head),
        source_evidence_ids=[row.id for row in originals],
        source_issues=sorted(issue_pairs),
        source_originals=[row.model_dump(mode="json") for row in originals],
        full_original=full.model_dump(mode="json"),
        current_mvp_original=row_copy(mvp),
        position_original=row_copy(position),
        catalogue_original=catalogues[0].model_dump(mode="json"),
        original_full_projection=full_projection.model_dump(mode="json"),
    )


def preview_maturity_execution(
    session: Session, user_id: UUID, body: FullMaturityRequest, now: datetime
) -> FullMaturityPreview:
    data = read_current_maturity_input(session, user_id, body, now)
    return FullMaturityPreview(
        user_id=user_id,
        original_request=body,
        proof=derive_maturity_proof(data),
        limitations=LIMITATIONS,
    )


def has_maturity_binding(session: Session, action: ActionPlan) -> bool:
    if MARKER in action.request or action.idempotency_key.startswith(KEY_PREFIX):
        return True
    row = session.get(DecisionRun, action.decision_run_id)
    trace = row.input_snapshot.get("decision_trace", {}) if row else {}
    return isinstance(trace, dict) and trace.get("algorithm_versions", {}).get(MARKER) == ALGORITHM


def _frozen(
    session: Session, action: ActionPlan, now: datetime
) -> tuple[FullMaturityInput, FullMaturityProof, MaturityContractCommand]:
    try:
        read = get_decision_trace(session, action.user_id, action.decision_run_id, now)
        trace = read.trace
        if read.completeness != "COMPLETE" or read.audit_chain_status != "VALID" or trace is None:
            raise ValueError("Complete original trace and its actual audit are required")
        verify_trace(trace)
        verify_frozen_maturity_trace(trace)
        data = FullMaturityInput.model_validate_json(json.dumps(trace.inputs["maturity_input"]))
        proof = derive_maturity_proof(data)
        command = MaturityContractCommand.model_validate_json(
            json.dumps(action.request["bank_request"])
        )
        expected = _payload(data, proof, action.id)
        if (
            trace.phase != "PREPARE"
            or trace.action_id != action.id
            or trace.user_id != action.user_id
            or trace.algorithm_versions.get(MARKER) != ALGORITHM
            or trace.inputs["action_request"] != expected
            or action.request != expected
            or action.request_hash != configuration_hash(expected)
            or proof.status != "VERIFIED_SCOPE"
            or proof.command != command
            or trace.outcome != {"maturity_proof": proof.model_dump(mode="json")}
            or action.id != uuid5(data.epoch_id, maturity_bank_key(data.request.idempotency_key))
            or action.idempotency_key != maturity_bank_key(data.request.idempotency_key)
            or action.action_type != "ASSET_MATURITY"
            or action.autonomy_level != "ASK_ONCE"
            or action.policy_version_id != command.original_policy_version_id
            or action.amount_cents != command.principal_cents
            or action.position_id != command.position_id
            or action.goal_id != command.goal_id
            or action.product_id != command.product_id
            or action.source_account_id != command.position_account_id
            or action.destination_account_id != command.destination_account_id
            or action.expires_at != command.expires_at
            or action.created_at != command.requested_at
        ):
            raise ValueError("Original immutable action, complete inputs and proof disagree")
        return data, proof, command
    except (ValueError, KeyError, TypeError) as error:
        raise _fail("FULL_MATURITY_ORIGINAL_INVALID", str(error)) from error


def _payload(data: FullMaturityInput, proof: FullMaturityProof, action_id: UUID) -> dict[str, Any]:
    if proof.command is None or proof.command_hash is None:
        raise _fail("FULL_MATURITY_NOT_READY", "当前整仓合同未证明")
    return maturity_action_payload(data, proof, action_id)


def _consent(
    session: Session,
    action: ActionPlan,
    data: FullMaturityInput,
    proof: FullMaturityProof,
    now: datetime,
    *,
    current: bool = False,
) -> FullMaturityConsent | None:
    identity = uuid5(action.id, "maturity-user-consent")
    row = session.get(EvidenceItem, identity)
    trace_id = uuid5(action.id, "maturity-user-confirmation-trace")
    trace_row = session.get(DecisionRun, trace_id)
    if row is None:
        if trace_row is not None or action.authorized_at is not None:
            raise _fail("FULL_MATURITY_CONSENT_MISSING", "不可把丢失的原确认当作无需确认")
        return None
    try:
        content = FullMaturityConsent.model_validate_json(json.dumps(row.content))
        if proof.command_hash is None or proof.command is None:
            raise ValueError("Frozen command hash is absent")
        verify_maturity_consent(
            content,
            action.id,
            action.user_id,
            data.epoch_id,
            proof.command_hash,
            now,
            current=current,
        )
        recorded = get_decision_trace(session, action.user_id, trace_id, now)
        if (
            row.user_id != action.user_id
            or row.source_type != CONSENT_SOURCE
            or row.source_ref != str(action.id)
            or row.evidence_level != "USER_CONFIRMED_ACTION"
            or row.content_hash != configuration_hash(row.content)
            or row.status != "VALID"
            or row.observed_at != content.confirmed_at
            or row.valid_from != content.confirmed_at
            or row.valid_to != proof.command.expires_at
            or row.created_at != content.confirmed_at
            or action.authorized_at != content.confirmed_at
            or recorded.completeness != "COMPLETE"
            or recorded.audit_chain_status != "VALID"
            or recorded.trace is None
            or recorded.trace.phase != "CONFIRM"
            or recorded.trace.outcome != {"maturity_user_consent": row.content}
            or recorded.trace.inputs
            != {
                "confirmation": content.original_confirmation.model_dump(mode="json"),
                "action_request": action.request,
                "bank_key": action.idempotency_key,
            }
            or recorded.trace.parent_run_id != action.decision_run_id
            or recorded.trace.action_id != action.id
            or recorded.trace.algorithm_versions.get(MARKER) != ALGORITHM
            or len(recorded.trace.sources) != 1
            or recorded.trace.sources[0].id != row.id
            or recorded.trace.sources[0].content != row.content
            or (current and not content.confirmed_at <= now < proof.command.expires_at)
        ):
            raise ValueError("Retained USER confirmation and original trace disagree")
        return content
    except (ValueError, KeyError, TypeError) as error:
        raise _fail("FULL_MATURITY_CONSENT_INVALID", str(error)) from error


def _view(session: Session, action: ActionPlan, now: datetime) -> FullMaturityAction:
    data, proof, command = _frozen(session, action, now)
    consent = _consent(session, action, data, proof, now)
    epoch = session.get(AuditEpoch, data.epoch_id)
    event = read_original_maturity(session, action.user_id, action.id, now)
    if event.proof_status == "INVALID":
        raise _fail("FULL_MATURITY_RECEIPT_INVALID", ",".join(event.issues))
    state: Literal["OPEN", "SEALED", "MISSING"] = (
        "MISSING" if epoch is None else ("OPEN" if epoch.status == "OPEN" else "SEALED")
    )
    return FullMaturityAction(
        user_id=action.user_id,
        action_id=action.id,
        epoch_id=data.epoch_id,
        original_request=data.request,
        original_action_request=action.request,
        client_request_hash=configuration_hash(data.request.model_dump(mode="json")),
        server_request_hash=action.request_hash,
        reviewed_command_hash=proof.command_hash or "",
        bank_key=action.idempotency_key,
        status=action.status,
        command=command,
        original_consent=consent,
        original_event=event.model_dump(mode="json"),
        service_receipt_verified=event.service_receipt_verified,
        epoch_state=state,
        historical=state != "OPEN",
    )


def lookup_maturity_execution(
    session: Session, user_id: UUID, key: str, now: datetime
) -> FullMaturityLookup:
    _read_snapshot(session)
    original = session.scalar(
        select(ActionPlan).where(
            ActionPlan.user_id == user_id, ActionPlan.idempotency_key == maturity_bank_key(key)
        )
    )
    return FullMaturityLookup(
        user_id=user_id,
        idempotency_key=key,
        status="RECORDED" if original else "NOT_FOUND_NOT_FINAL",
        action=_view(session, original, _now(now)) if original else None,
    )


def _fresh(
    engine: Engine,
    user_id: UUID,
    body: FullMaturityRequest,
    now: datetime,
    command: MaturityContractCommand | None = None,
) -> tuple[FullMaturityInput, FullMaturityProof]:
    with _reader(engine) as read:
        data = read_current_maturity_input(read, user_id, body, now)
        proof = derive_maturity_proof(data, command)
    if proof.status != "VERIFIED_SCOPE":
        raise _fail("FULL_MATURITY_CURRENT_SCOPE_NOT_PROVEN", ",".join(proof.reasons))
    return data, proof


def _owned(session: Session, user_id: UUID, action_id: UUID) -> ActionPlan:
    action = session.get(ActionPlan, action_id)
    if action is None or action.user_id != user_id or not has_maturity_binding(session, action):
        raise PolicyLifecycleError("NOT_FOUND", "原用户到期整仓动作不存在", 404)
    return action


def _declaration(action: ActionPlan) -> dict[str, Any]:
    if action.status in {"PLANNED", "AUTHORIZED"}:
        state = "MATURITY_USER_PLANNED"
    elif action.status in {"SUBMITTED", "UNKNOWN"}:
        state = "MATURITY_USER_SUBMITTED"
    elif action.status in {"INVALIDATED", "CANCELLED"}:
        state = "MATURITY_USER_NO_EFFECT"
    else:
        raise _fail("FULL_MATURITY_DECLARATION_INVALID", "不可推断未支持状态为无effect")
    return {
        "action_id": str(action.id),
        "state": state,
        "original_request_hash": action.request_hash,
    }


def prepare_maturity_execution(
    engine: Engine,
    user_id: UUID,
    body: FullMaturityRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullMaturityAction:
    now = _now(now)
    require_local_user(principal, user_id, now)
    require_installed_guard()
    with audit_command_guard(engine, user_id):
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            with _reader(engine) as read:
                existing = lookup_maturity_execution(read, user_id, body.idempotency_key, now)
                if existing.action is not None:
                    if existing.action.original_request != body:
                        raise _fail("IDEMPOTENCY_CONFLICT", "原键不得更换持仓或版本")
                    return existing.action
            pending = writer.scalar(
                select(ActionPlan.id)
                .where(
                    ActionPlan.user_id == user_id,
                    ActionPlan.position_id == body.position_id,
                    ActionPlan.action_type.in_(["ASSET_REDEEM", "ASSET_MATURITY"]),
                    ActionPlan.status.in_(["PLANNED", "AUTHORIZED", "SUBMITTED", "UNKNOWN"]),
                )
                .limit(1)
            )
            if pending is not None:
                raise _fail("FULL_MATURITY_POSITION_PENDING", "原持仓已有未决动作，不可另建原键")
            data, proof = _fresh(engine, user_id, body, now)
            command = proof.command
            if command is None:
                raise _fail("FULL_MATURITY_CONTRACT_MISSING", "完整到期命令缺失")
            action_id = uuid5(data.epoch_id, maturity_bank_key(body.idempotency_key))
            run_id = uuid5(action_id, "maturity-user-prepare")
            payload = _payload(data, proof, action_id)
            snapshot = {"original_request": body.model_dump(mode="json")}
            run = DecisionRun(
                id=run_id,
                user_id=user_id,
                created_at=now,
                idempotency_key="maturity:prepare:" + str(action_id),
                trigger_type="USER_MATURITY",
                algorithm_version=ALGORITHM,
                as_of=now,
                input_snapshot=snapshot,
                snapshot_hash=configuration_hash(snapshot),
                policy_version_ids=[str(command.original_policy_version_id)],
                evidence_ids=[],
                result={},
                status="PENDING",
            )
            writer.add(run)
            writer.flush()
            action = ActionPlan(
                id=action_id,
                user_id=user_id,
                created_at=now,
                decision_run_id=run_id,
                policy_version_id=command.original_policy_version_id,
                source_account_id=command.position_account_id,
                destination_account_id=command.destination_account_id,
                goal_id=command.goal_id,
                product_id=command.product_id,
                position_id=command.position_id,
                action_type="ASSET_MATURITY",
                amount_cents=command.principal_cents,
                autonomy_level="ASK_ONCE",
                status="PLANNED",
                idempotency_key=maturity_bank_key(body.idempotency_key),
                request=payload,
                request_hash=configuration_hash(payload),
                expires_at=command.expires_at,
            )
            writer.add(action)
            writer.flush()
            trace = build_trace(
                run_id=run_id,
                user_id=user_id,
                phase="PREPARE",
                as_of=now,
                action_id=action_id,
                algorithm_versions={"trace": "decision-trace-v1", MARKER: ALGORITHM},
                inputs={"maturity_input": data.model_dump(mode="json"), "action_request": payload},
                sources=capture_sources(writer, user_id, data.source_evidence_ids),
                policies=capture_policies(writer, user_id, [command.original_policy_version_id]),
                outcome={"maturity_proof": proof.model_dump(mode="json")},
            )
            record_trace(writer, trace, run)
            refresh_exposure(writer, user_id, now, action_id, {action.id: _declaration(action)})
            record_action_created(writer, action, now)
        with _reader(engine) as read:
            return _view(read, _owned(read, user_id, action_id), now)


def confirm_maturity_execution(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: FullMaturityConfirmation,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullMaturityAction:
    now = _now(now)
    require_local_user(principal, user_id, now)
    require_installed_guard()
    with audit_command_guard(engine, user_id):
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            action = _owned(writer, user_id, action_id)
            with _reader(engine) as read:
                recorded = _owned(read, user_id, action_id)
                data, proof, command = _frozen(read, recorded, now)
                if (body.expected_epoch_id, body.reviewed_command_hash) != (
                    data.epoch_id,
                    proof.command_hash,
                ):
                    raise _fail("FULL_MATURITY_CONFIRMATION_MISMATCH", "必须明确复核完整原命令")
                if _consent(read, recorded, data, proof, now) is not None:
                    return _view(read, recorded, now)
            if action.status != "PLANNED":
                raise _fail("FULL_MATURITY_NOT_CONFIRMABLE", "原动作不是待用户确认状态")
            _fresh(engine, user_id, data.request, now, command)
            content = FullMaturityConsent(
                user_id=user_id,
                epoch_id=data.epoch_id,
                action_id=action_id,
                reviewed_command_hash=body.reviewed_command_hash,
                original_confirmation=body,
                principal_at_confirmation=principal,
                confirmed_at=now,
            ).model_dump(mode="json")
            evidence = EvidenceItem(
                id=uuid5(action_id, "maturity-user-consent"),
                user_id=user_id,
                created_at=now,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type=CONSENT_SOURCE,
                source_ref=str(action_id),
                content=content,
                content_hash=configuration_hash(content),
                observed_at=now,
                valid_from=now,
                valid_to=command.expires_at,
                status="VALID",
            )
            writer.add(evidence)
            writer.flush()
            record_trace(
                writer,
                build_trace(
                    run_id=uuid5(action_id, "maturity-user-confirmation-trace"),
                    user_id=user_id,
                    phase="CONFIRM",
                    as_of=now,
                    action_id=action_id,
                    parent_run_id=action.decision_run_id,
                    algorithm_versions={"trace": "decision-trace-v1", MARKER: ALGORITHM},
                    inputs={
                        "confirmation": body.model_dump(mode="json"),
                        "action_request": action.request,
                        "bank_key": action.idempotency_key,
                    },
                    sources=capture_sources(writer, user_id, [evidence.id]),
                    policies=[],
                    outcome={"maturity_user_consent": content},
                ),
            )
            before = audit_subject_data(action)
            action.status, action.authorized_at = "AUTHORIZED", now
            writer.flush()
            refresh_exposure(writer, user_id, now, evidence.id, {action.id: _declaration(action)})
            record_action_transition(
                writer,
                action,
                "PLANNED",
                now,
                reason_code="EXPLICIT_MATURITY_USER_CONFIRMATION",
                before_data=before,
                details={"confirmation_evidence_id": str(evidence.id)},
            )
        with _reader(engine) as read:
            return _view(read, _owned(read, user_id, action_id), now)


def execute_maturity_execution(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    body: FullMaturityExecuteRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullMaturityAction:
    from app.services import simulated_bank

    now = _now(now)
    require_local_user(principal, user_id, now)
    require_installed_guard()
    with audit_command_guard(engine, user_id):
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            action = _owned(writer, user_id, action_id)
            with _reader(engine) as read:
                original = _owned(read, user_id, action_id)
                data, proof, command = _frozen(read, original, now)
                if (body.expected_epoch_id, body.reviewed_command_hash) != (
                    data.epoch_id,
                    proof.command_hash,
                ):
                    raise _fail("FULL_MATURITY_EXECUTE_MISMATCH", "只可沿原轮次、完整命令和键恢复")
                bank = read.scalar(
                    select(SimulatedBankRedemption).where(
                        SimulatedBankRedemption.action_plan_id == action_id
                    )
                )
                if _consent(read, original, data, proof, now, current=bank is None) is None:
                    raise _fail("FULL_MATURITY_USER_CONFIRMATION_REQUIRED", "原USER逐次确认缺失")
                view = _view(read, original, now)
                if view.service_receipt_verified:
                    return view
            if bank is None:
                if action.status not in {"AUTHORIZED", "SUBMITTED", "UNKNOWN"}:
                    raise _fail("FULL_MATURITY_NOT_EXECUTABLE", "当前状态不允许首次银行受理")
                _fresh(engine, user_id, data.request, now, command)
                if action.status != "SUBMITTED":
                    before = audit_subject_data(action)
                    prior = action.status
                    action.status = "SUBMITTED"
                    writer.flush()
                    refresh_exposure(
                        writer,
                        user_id,
                        now,
                        uuid5(action_id, "maturity-submit"),
                        {action.id: _declaration(action)},
                    )
                    record_action_transition(
                        writer,
                        action,
                        prior,
                        now,
                        reason_code="USER_MATURITY_SUBMITTED",
                        before_data=before,
                    )
        try:
            simulated_bank.process_redemption(engine, user_id, action_id, now)
        except (TimeoutError, ConnectionError, PolicyLifecycleError) as error:
            with Session(engine) as writer, writer.begin():
                _user(writer, user_id)
                action = _owned(writer, user_id, action_id)
                if action.status == "SUBMITTED":
                    before = audit_subject_data(action)
                    action.status = "UNKNOWN"
                    writer.flush()
                    bank_original = writer.scalar(
                        select(SimulatedBankRedemption.id).where(
                            SimulatedBankRedemption.action_plan_id == action_id
                        )
                    )
                    if bank_original is None:
                        # Complete no-effect ORIGINALS only. An existing bank
                        # result never becomes a fabricated no-effect statement.
                        refresh_exposure(
                            writer,
                            user_id,
                            now,
                            uuid5(action_id, "maturity-unknown-no-bank"),
                            {action.id: _declaration(action)},
                        )
                    record_action_transition(
                        writer,
                        action,
                        "SUBMITTED",
                        now,
                        reason_code="MATURITY_BANK_RESULT_UNKNOWN",
                        before_data=before,
                        details={"error_type": type(error).__name__},
                    )
            raise
        with Session(engine) as writer, writer.begin():
            _user(writer, user_id)
            action = _owned(writer, user_id, action_id)
            banks = list(
                writer.scalars(
                    select(SimulatedBankRedemption).where(
                        SimulatedBankRedemption.action_plan_id == action_id
                    )
                )
            )
            if len(banks) != 1:
                raise _fail("FULL_MATURITY_BANK_RESULT_UNKNOWN", "原唯一银行请求尚未核对")
            declaration = project_request(writer, banks[0], now)
            finalize_projections(writer, user_id, now, banks[0].id, {action_id: declaration})
        with _reader(engine) as read:
            return _view(read, _owned(read, user_id, action_id), now)


def validate_current_maturity_bank_request(
    session: Session, action: ActionPlan, command: BankRequest, now: datetime
) -> None:
    """Bank's existing USER lock/gate owns this write transaction; fresh RR/RO only."""
    if (
        action.status != "SUBMITTED"
        or action.autonomy_level != "ASK_ONCE"
        or command.kind != "MATURE"
    ):
        raise _fail("FULL_MATURITY_BANK_REJECTED", "新协议必须是原ASK到期整仓提交")
    session.info.pop(VALIDATION_INFO_KEY, None)
    with _reader(session.get_bind().engine) as read:
        original = _owned(read, action.user_id, action.id)
        data, proof, bound = _frozen(read, original, now)
        consent = _consent(read, original, data, proof, now, current=True)
        if (
            action.request != original.request
            or action.request_hash != original.request_hash
            or command.model_dump(mode="json") != bound.model_dump(mode="json")
            or consent is None
        ):
            raise _fail("FULL_MATURITY_BANK_REJECTED", "完整原请求或原USER确认未匹配")
        sources = capture_sources(read, action.user_id, [uuid5(action.id, "maturity-user-consent")])
        consent_source = sources[0].model_dump(mode="json")
    current, checked = _fresh(session.get_bind().engine, action.user_id, data.request, now, bound)
    # Evidence handoff for this exact bank transaction only. It must be popped by
    # the original recorder AFTER this guard; it never substitutes for the guard.
    session.info[VALIDATION_INFO_KEY] = {
        "action_id": str(action.id),
        "inputs": current.model_dump(mode="json"),
        "proof": checked.model_dump(mode="json"),
        "original_consent": consent.model_dump(mode="json"),
        "consent_source": consent_source,
    }


def validate_maturity_exposure_original(
    session: Session,
    action: ActionPlan,
    declaration: dict[str, Any],
    bank_requests: list[SimulatedBankRedemption],
    receipts: list[ActionReceipt],
    bank_operations: list[BankOperation],
    resource_reservations: list[ActionResourceReservation],
    bank_postings: list[SimulatedBankPosting],
    now: datetime,
) -> bool:
    """Validate the complete original no-effect member; never recurse into current exposure."""
    if not has_maturity_binding(session, action):
        return False
    data, proof, command = _frozen(session, action, now)
    if any(row.action_plan_id == action.id for row in bank_requests):
        return False  # Original two-leg and application receipt validator remains mandatory.
    if declaration != _declaration(action):
        raise _fail("FULL_MATURITY_EXPOSURE_INVALID", "新无effect声明不匹配真实原状态")
    position = session.get(AssetPosition, command.position_id)
    conflicting = any(
        row.user_id != action.user_id
        or row.action_plan_id == action.id
        or (
            row.status == "RESERVED"
            and row.resource_kind == "POSITION"
            and row.resource_key == str(command.position_id)
        )
        for row in resource_reservations
    )
    if (
        position is None
        or row_copy(position) != data.position_original
        or any(row.action_plan_id == action.id for row in receipts)
        or any(row.action_plan_id == action.id for row in bank_operations)
        or any(
            row.redemption_id == uuid5(action.id, "simulated-bank-redemption")
            or row.operation_id == uuid5(action.id, "simulated-bank-redemption")
            for row in bank_postings
        )
        or conflicting
    ):
        raise _fail(
            "FULL_MATURITY_EXPOSURE_INVALID", "无effect要求原持仓未变且无银行/回执/冲突预留"
        )
    head = ledger_heads(session, action.user_id).get("POSITION:" + str(command.position_id))
    if (
        head is None
        or row_copy(head) != data.independent_bank_head
        or head.balance_after_cents != command.principal_cents
    ):
        raise _fail("FULL_MATURITY_EXPOSURE_INVALID", "原独立本金链已变化")
    if action.status in {"AUTHORIZED", "SUBMITTED", "UNKNOWN"}:
        if _consent(session, action, data, proof, now) is None:
            raise _fail("FULL_MATURITY_EXPOSURE_INVALID", "已确认/提交必须有原USER证据")
    return True
