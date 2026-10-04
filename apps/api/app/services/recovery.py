"""Whole-position safety recovery with three independently committed phases."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    DecisionRun,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    User,
)
from app.domain.boundary_types import BoundaryResult, SourceIssue
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery import plan_recovery
from app.domain.recovery_types import RecoveryPlan
from app.services.boundary import BoundarySourceIssue, compute_user_boundary, load_boundary_context
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.recovery_projection import finalize_projections, project_request, refresh_exposure
from app.services.recovery_sources import recovery_inputs
from app.services.simulated_bank import BankRequest, process_redemption
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class RecoveryPreviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    plan: RecoveryPlan
    source_evidence_ids: list[UUID]
    input_digest: str
    source_issues: list[BoundarySourceIssue]


class RecoveryActionStatus(BaseModel):
    action_id: UUID
    position_id: UUID
    status: str
    bank_request_id: UUID | None = None
    bank_status: str | None = None
    receipt_id: UUID | None = None


class RecoveryNotification(BaseModel):
    code: str
    message: str
    action_id: UUID | None = None


class RecoveryRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    run_id: UUID
    as_of: datetime
    status: str
    plan: RecoveryPlan
    actual_boundary: BoundaryResult
    actions: list[RecoveryActionStatus]
    notifications: list[RecoveryNotification]


def preview_recovery(session: Session, user_id: UUID, now: datetime) -> RecoveryPreviewResponse:
    with session.no_autoflush:
        context = load_boundary_context(session, user_id, now)
        positions, authorities = recovery_inputs(session, context)
        issues = sorted(context.sources.issues, key=lambda item: (item.code, item.source_ref))
        plan = plan_recovery(
            context.snapshot,
            context.versions,
            context.positions,
            context.products,
            positions,
            authorities,
            user_id=user_id,
            source_issues=[
                SourceIssue(code=item.code, entity_type="source", entity_id=item.source_ref)
                for item in issues
            ],
        )
        from app.services.decision_recording import (
            capture_boundary,
            capture_versions,
            current_capture,
        )

        capture = current_capture(session)
        if capture is not None:
            capture_boundary(session, "recovery_boundary", context)
            capture.inputs["recovery_planning"] = {
                "positions": [item.model_dump(mode="json") for item in positions],
                "authorities": [item.model_dump(mode="json") for item in authorities],
            }
            capture_versions(
                session,
                user_id,
                [
                    item.original_authorization.version_id
                    for item in positions
                    if item.original_authorization is not None
                ],
            )
        return RecoveryPreviewResponse(
            user_id=user_id,
            as_of=context.snapshot.as_of,
            plan=plan,
            source_evidence_ids=sorted(context.sources.used),
            source_issues=issues,
            input_digest=configuration_hash(
                {
                    "sources": [
                        {"id": str(key), "hash": context.sources.evidence[key].content_hash}
                        for key in sorted(context.sources.used)
                    ],
                    "issues": [item.model_dump(mode="json") for item in issues],
                }
            ),
        )


def run_recovery(
    engine: Engine, user_id: UUID, idempotency_key: str, now: datetime
) -> RecoveryRunResponse:
    from app.db.audit_guard import audit_command_guard

    with audit_command_guard(engine, user_id):
        return _run_recovery(engine, user_id, idempotency_key, now)


def _run_recovery(
    engine: Engine, user_id: UUID, idempotency_key: str, now: datetime
) -> RecoveryRunResponse:
    from app.db.audit_guard import transaction_gate
    from app.services.audit_recording import record_action_created, record_recovery_observed

    if not 1 <= len(idempotency_key) <= 160 or not idempotency_key.strip():
        raise PolicyLifecycleError("INVALID_IDEMPOTENCY_KEY", "恢复请求键必须为1到160个非空白字符")
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    key = "recovery:" + configuration_hash({"key": idempotency_key})
    # Phase 1 commits the application request before any separate bank transaction starts.
    with Session(engine) as session, session.begin():
        transaction_gate(session, user_id)
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        run = session.scalar(
            select(DecisionRun).where(
                DecisionRun.user_id == user_id, DecisionRun.idempotency_key == key
            )
        )
        if run is None:
            from app.services.decision_recording import (
                capture_boundary,
                record_recovery_plan,
                start_capture,
            )

            capture = start_capture(session)
            preview = preview_recovery(session, user_id, now)
            context = load_boundary_context(session, user_id, now)
            recovery_positions, _ = recovery_inputs(session, context)
            mature = [
                item
                for item in recovery_positions
                if item.quote is not None
                and item.quote.kind == "MATURE"
                and not item.reserved_principal_cents
            ]
            mature_ids = {str(item.position_id) for item in mature}
            if any(
                item.code != "UNRECONCILED_POSITION_AVAILABILITY"
                or item.source_ref not in mature_ids
                for item in context.sources.issues
            ):
                mature = []
            capture_boundary(session, "maturity_boundary", context)
            capture.inputs["maturity_positions"] = [item.model_dump(mode="json") for item in mature]
            initial = {
                "idempotency_key": idempotency_key,
                "preview": preview.model_dump(mode="json"),
            }
            run = DecisionRun(
                id=uuid4(),
                user_id=user_id,
                created_at=now,
                idempotency_key=key,
                trigger_type="SAFETY_RECOVERY",
                algorithm_version=preview.plan.algorithm_version,
                as_of=now,
                input_snapshot=initial,
                snapshot_hash=configuration_hash(initial),
                policy_version_ids=sorted(
                    {str(item.current_policy_version_id) for item in preview.plan.steps}
                ),
                evidence_ids=[str(item) for item in preview.source_evidence_ids],
                result={"notifications": []},
                status="PENDING",
                completed_at=None,
            )
            session.add(run)
            session.flush()
            declarations = {}
            for step in preview.plan.steps:
                if step.autonomy_level != "AUTO_EXECUTE":
                    continue
                bank = BankRequest(
                    user_id=user_id,
                    position_id=step.position_id,
                    position_account_id=step.source_account_id,
                    product_id=step.product_id,
                    goal_id=step.goal_id,
                    original_policy_version_id=step.original_policy_version_id,
                    destination_account_id=step.destination_account_id,
                    principal_cents=step.quote.principal_cents,
                    requested_at=now,
                    available_at=step.quote.principal_available_at,
                    expires_at=step.expires_at,
                )
                request_payload: dict[str, Any] = {
                    "recovery": step.model_dump(mode="json"),
                    "bank_request": bank.model_dump(mode="json"),
                }
                identity = uuid5(run.id, str(step.position_id))
                action = ActionPlan(
                    id=identity,
                    user_id=user_id,
                    created_at=now,
                    decision_run_id=run.id,
                    policy_version_id=step.current_policy_version_id,
                    source_account_id=step.source_account_id,
                    destination_account_id=step.destination_account_id
                    if step.destination_account_id != step.source_account_id
                    else None,
                    goal_id=step.goal_id,
                    product_id=step.product_id,
                    position_id=step.position_id,
                    action_type="ASSET_REDEEM",
                    amount_cents=step.quote.principal_cents,
                    autonomy_level="AUTO_EXECUTE",
                    status="SUBMITTED",
                    idempotency_key=f"recovery:{identity}",
                    request=request_payload,
                    request_hash=configuration_hash(request_payload),
                    authorized_at=now,
                    expires_at=step.expires_at,
                )
                session.add(action)
                declarations[identity] = {
                    "action_id": str(identity),
                    "state": "REDEMPTION_REQUESTED",
                }
            for item in mature:
                quote = item.quote
                assert quote is not None
                bank = BankRequest(
                    user_id=user_id,
                    position_id=item.position_id,
                    position_account_id=item.account_id,
                    product_id=item.product.product_id,
                    goal_id=item.goal_id,
                    original_policy_version_id=item.original_authorization.version_id
                    if item.original_authorization
                    else None,
                    destination_account_id=item.destination_account_id,
                    principal_cents=quote.principal_cents,
                    requested_at=now,
                    available_at=now,
                    expires_at=quote.expires_at,
                    kind="MATURE",
                )
                request_payload = {
                    "bank_request": bank.model_dump(mode="json"),
                    "contract_terms_digest": item.product.terms_digest,
                    "contract_evidence_ids": [str(identifier) for identifier in item.evidence_ids],
                }
                identity = uuid5(run.id, f"mature:{item.position_id}")
                session.add(
                    ActionPlan(
                        id=identity,
                        user_id=user_id,
                        created_at=now,
                        decision_run_id=run.id,
                        policy_version_id=item.original_authorization.version_id
                        if item.original_authorization
                        else None,
                        source_account_id=item.account_id,
                        destination_account_id=item.destination_account_id
                        if item.destination_account_id != item.account_id
                        else None,
                        goal_id=item.goal_id,
                        product_id=item.product.product_id,
                        position_id=item.position_id,
                        action_type="ASSET_MATURITY",
                        amount_cents=quote.principal_cents,
                        autonomy_level="AUTO_EXECUTE",
                        status="SUBMITTED",
                        idempotency_key=f"recovery:{identity}",
                        request=request_payload,
                        request_hash=configuration_hash(request_payload),
                        authorized_at=now,
                        expires_at=quote.expires_at,
                    )
                )
                declarations[identity] = {
                    "action_id": str(identity),
                    "state": "REDEMPTION_REQUESTED",
                }
            session.flush()
            record_recovery_plan(session, run, preview.plan, mature, now)
            if declarations:
                refresh_exposure(session, user_id, now, run.id, declarations)
            else:
                run.status, run.completed_at = "SUCCEEDED", now
                record_recovery_observed(
                    session, run, now, kind="RUN_COMPLETED", details={"action_ids": []}
                )
            for created_action in session.scalars(
                select(ActionPlan)
                .where(ActionPlan.user_id == user_id, ActionPlan.decision_run_id == run.id)
                .order_by(ActionPlan.id)
            ):
                record_action_created(session, created_action, now)
        elif (
            run.trigger_type != "SAFETY_RECOVERY"
            or run.input_snapshot.get("idempotency_key") != idempotency_key
            or run.snapshot_hash != configuration_hash(run.input_snapshot)
        ):
            raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "既有恢复请求的内容不能改变", 409)
        run_id = run.id
        action_ids = list(
            session.scalars(
                select(ActionPlan.id)
                .where(ActionPlan.decision_run_id == run.id)
                .order_by(ActionPlan.created_at, ActionPlan.id)
            )
        )
    # Phase 2 owns a different Session/transaction and commits before the application projection.
    bank_errors: list[tuple[UUID, PolicyLifecycleError]] = []
    for action_id in action_ids:
        try:
            process_redemption(engine, user_id, action_id, now)
        except PolicyLifecycleError as error:
            # A later refusal cannot hide an earlier independently committed economic effect.
            # This does not assert no effect: the bank request is queried again in phase 3.
            bank_errors.append((action_id, error))
    # Phase 3 is independently retryable: a failed projection never rolls back bank postings.
    projection_errors: list[PolicyLifecycleError] = []
    if action_ids:
        with Session(engine) as session, session.begin():
            transaction_gate(session, user_id)
            session.scalar(select(User).where(User.id == user_id).with_for_update())
            requests = list(
                session.scalars(
                    select(SimulatedBankRedemption)
                    .where(SimulatedBankRedemption.action_plan_id.in_(action_ids))
                    .order_by(SimulatedBankRedemption.settled_at, SimulatedBankRedemption.id)
                )
            )
            cash_order = {
                row.redemption_id: (row.ledger_key, row.sequence_number)
                for row in session.scalars(
                    select(SimulatedBankPosting).where(
                        SimulatedBankPosting.user_id == user_id,
                        SimulatedBankPosting.entry_kind == "CASH_CREDIT",
                    )
                )
            }
            requests.sort(key=lambda item: cash_order.get(item.id, ("~pending", 0)))
            declarations = {}
            changed = False
            for request in requests:
                receipt = session.scalar(
                    select(ActionReceipt).where(
                        ActionReceipt.action_plan_id == request.action_plan_id
                    )
                )
                if receipt is None:
                    try:
                        with session.begin_nested():
                            declaration = project_request(session, request, now)
                        declarations[request.action_plan_id] = declaration
                        changed = True
                    except PolicyLifecycleError as error:
                        projection_errors.append(error)
                        failed_run = session.get(DecisionRun, run_id)
                        assert failed_run is not None
                        record_recovery_observed(
                            session,
                            failed_run,
                            now,
                            kind="PROJECTION_FAILED",
                            action_id=request.action_plan_id,
                            request=request,
                            error_code=error.code,
                            details={"bank_status": request.status},
                        )
            if changed:
                finalize_projections(session, user_id, now, run_id, declarations)
                row = session.get(DecisionRun, run_id)
                assert row is not None
                notifications = list(row.result.get("notifications", []))
                for request in requests:
                    if request.action_plan_id not in declarations:
                        continue
                    code = (
                        "PRINCIPAL_RECEIVED"
                        if request.status == "SETTLED"
                        else "REDEMPTION_ACCEPTED"
                    )
                    entry = {
                        "code": code,
                        "message": "模拟银行本金已到账并完成对账"
                        if code == "PRINCIPAL_RECEIVED"
                        else "模拟银行已受理，现金尚未到账",
                        "action_id": str(request.action_plan_id),
                    }
                    if entry not in notifications:
                        notifications.append(entry)
                row.result = {**row.result, "notifications": notifications}
                completed_ids = set(
                    session.scalars(
                        select(ActionReceipt.action_plan_id).where(
                            ActionReceipt.action_plan_id.in_(action_ids),
                            ActionReceipt.status == "SUCCEEDED",
                        )
                    )
                )
                if (
                    len(requests) == len(action_ids)
                    and completed_ids == set(action_ids)
                    and all(request.status == "SETTLED" for request in requests)
                    and not bank_errors
                    and not projection_errors
                ):
                    before_status = row.status
                    row.status, row.completed_at = "SUCCEEDED", now
                    if before_status != row.status:
                        record_recovery_observed(
                            session,
                            row,
                            now,
                            kind="RUN_COMPLETED",
                            details={
                                "action_ids": sorted(str(identity) for identity in action_ids)
                            },
                        )
            if bank_errors:
                row = session.get(DecisionRun, run_id)
                assert row is not None
                notifications = list(row.result.get("notifications", []))
                new_bank_errors: list[tuple[UUID, PolicyLifecycleError]] = []
                for action_id, bank_error in bank_errors:
                    entry = {
                        "code": "BANK_REQUEST_NOT_CONFIRMED",
                        "message": bank_error.message,
                        "action_id": str(action_id),
                    }
                    if entry not in notifications:
                        notifications.append(entry)
                        new_bank_errors.append((action_id, bank_error))
                if notifications != row.result.get("notifications", []):
                    row.result = {**row.result, "notifications": notifications}
                for action_id, bank_error in new_bank_errors:
                    known_request = next(
                        (request for request in requests if request.action_plan_id == action_id),
                        None,
                    )
                    record_recovery_observed(
                        session,
                        row,
                        now,
                        kind="BANK_ERROR",
                        action_id=action_id,
                        request=known_request,
                        error_code=bank_error.code,
                    )
    if projection_errors:
        raise projection_errors[0]
    with Session(engine) as session:
        return get_recovery_run(session, user_id, run_id, now)


def get_recovery_run(
    session: Session, user_id: UUID, run_id: UUID, now: datetime
) -> RecoveryRunResponse:
    run = session.scalar(
        select(DecisionRun).where(
            DecisionRun.id == run_id,
            DecisionRun.user_id == user_id,
            DecisionRun.trigger_type == "SAFETY_RECOVERY",
        )
    )
    if run is None:
        raise PolicyLifecycleError("NOT_FOUND", "恢复记录不存在", 404)
    if run.snapshot_hash != configuration_hash(run.input_snapshot):
        raise PolicyLifecycleError("INVALID_RECOVERY_RUN", "恢复记录的原始输入摘要不一致", 409)
    preview = RecoveryPreviewResponse.model_validate_json(json.dumps(run.input_snapshot["preview"]))
    actual = compute_user_boundary(session, user_id, now).boundary
    actions = []
    for row in session.scalars(
        select(ActionPlan).where(ActionPlan.decision_run_id == run.id).order_by(ActionPlan.id)
    ):
        request = session.scalar(
            select(SimulatedBankRedemption).where(SimulatedBankRedemption.action_plan_id == row.id)
        )
        receipt = session.scalar(
            select(ActionReceipt).where(ActionReceipt.action_plan_id == row.id)
        )
        assert row.position_id is not None
        actions.append(
            RecoveryActionStatus(
                action_id=row.id,
                position_id=row.position_id,
                status=row.status,
                bank_request_id=request.id if request else None,
                bank_status=request.status if request else None,
                receipt_id=receipt.id if receipt else None,
            )
        )
    status: str
    if not actions:
        status = (
            "INSUFFICIENT_EVIDENCE"
            if actual.status == "INSUFFICIENT_EVIDENCE"
            else (
                "NO_SAFE_RECOVERY"
                if preview.plan.status == "NO_RECOVERY_NEEDED" and actual.status == "LIQUIDITY_RISK"
                else preview.plan.status
            )
        )
    elif any(
        item.bank_status in {None, "UNKNOWN"}
        or (item.bank_status == "SETTLED" and item.receipt_id is None)
        for item in actions
    ):
        status = "RECONCILIATION_REQUIRED"
    elif any(item.bank_status == "ACCEPTED" for item in actions):
        status = "PENDING_SETTLEMENT"
    else:
        status = "RECOVERED" if actual.status == "READY" else "PARTIAL_RECOVERY"
    return RecoveryRunResponse(
        user_id=user_id,
        run_id=run_id,
        as_of=now,
        status=status,
        plan=preview.plan,
        actual_boundary=actual,
        actions=actions,
        notifications=[
            RecoveryNotification.model_validate(item)
            for item in run.result.get("notifications", [])
        ],
    )
