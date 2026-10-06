"""Durable whole consent and ordered original purchase dispatch, simulated only."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.full_models import (
    FullAssetExecutionBatch,
    FullAssetExecutionConsent,
    FullAssetExecutionPortfolio,
)
from app.db.models import ActionPlan, ActionReceipt, BankOperation, EvidenceItem
from app.domain.decision_trace import build_trace
from app.domain.execution import revalidate_execution
from app.domain.execution_types import BankCommand
from app.domain.full_asset_execution import (
    FullAssetBatchBinding,
    FullAssetConfirmRequest,
    FullAssetExecuteRequest,
    FullAssetFrozenPortfolio,
    FullAssetPrepareRequest,
)
from app.domain.full_asset_execution_guard import (
    FullAssetRemainingCheck,
    validate_remaining_combination,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import audit_read_scope, current_audit_epoch, row_copy
from app.services.audit_recording import (
    audit_subject_data,
    record_action_created,
    record_action_transition,
)
from app.services.decision_recording import current_capture, start_capture, validation_constraints
from app.services.decision_trace import record_trace
from app.services.execution import _lock_user, execute_action
from app.services.execution_context import load_execution_context
from app.services.execution_exposure import refresh_execution_exposure
from app.services.execution_sources import read_execution_confirmation
from app.services.full_asset_execution import preview_full_asset_execution
from app.services.full_asset_execution_store import (
    FullAssetExecutionResponse,
    batch_binding,
    error,
    read_consent_original,
    read_full_asset_execution,
    read_portfolio_original,
    select_original_batch,
    verify_original_batch_action,
)
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_protection_projection import compute_full_annual_protection
from app.services.policy_lifecycle import _now
from app.services.product_catalog import verified_catalog_products
from app.services.recovery_projection import _epochs
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

GUARD_VERSION = "full-asset-batch-guards-v1"


def require_original_pipeline_guards() -> None:
    from app.services import execution, execution_bank

    if (
        getattr(execution, "FULL_ASSET_BATCH_GUARDS_VERSION", None) != GUARD_VERSION
        or getattr(execution_bank, "FULL_ASSET_BATCH_GUARDS_VERSION", None) != GUARD_VERSION
    ):
        raise error(
            "完整组合原phase1与银行首次accept门尚未接通", "FULL_ASSET_EXECUTION_NOT_IMPLEMENTED"
        )


@contextmanager
def fresh_read(engine: Engine) -> Iterator[Session]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session, audit_read_scope(session):
                yield session


def _result(
    engine: Engine, user_id: UUID, portfolio_id: UUID, now: datetime
) -> FullAssetExecutionResponse:
    with fresh_read(engine) as session:
        return read_full_asset_execution(session, user_id, portfolio_id, now)


def _fresh_whole(
    read: Session,
    user_id: UUID,
    portfolio_id: UUID,
    number: int,
    now: datetime,
    *,
    require_consent: bool,
) -> FullAssetRemainingCheck:
    parent, portfolio, _, epoch = read_portfolio_original(read, user_id, portfolio_id, now)
    current_epoch = current_audit_epoch(read, user_id)
    if epoch.status != "OPEN" or current_epoch is None or current_epoch.id != epoch.id:
        raise error("原组合epoch已封存", "FULL_ASSET_EPOCH_CHANGED")
    if not 1 <= number <= len(portfolio.batches):
        raise error("原组合批次不在完整分母中")
    if (
        require_consent
        and read_consent_original(read, parent, portfolio, now, current=True) is None
    ):
        raise error("执行前需要原整体明确确认", "FULL_ASSET_CONFIRMATION_REQUIRED")
    full = read_full_policy(read, user_id, portfolio.original_request.full_policy_id, now)
    if (
        full.current_version.version_id
        != portfolio.original_request.expected_full_policy_version_id
        or full.current_version.content_hash != portfolio.full_policy_content_hash
        or not full.planning_confirmation_valid
        or full.reference_validation != "CURRENT"
    ):
        raise error("当前原Full版本、scope或确认已变化", "FULL_ASSET_CURRENT_PERMISSION_CHANGED")
    catalogue = verified_catalog_products(read, now)
    bindings = {b.product_id: b.model_dump(mode="json") for b in catalogue.bindings}
    if catalogue.status != "VERIFIED" or any(
        b.command.effect.product_id is None
        or bindings.get(b.command.effect.product_id) != b.catalogue.model_dump(mode="json")
        for b in portfolio.batches
    ):
        raise error("原目录/产品条款已变化", "FULL_ASSET_CURRENT_CATALOGUE_CHANGED")
    for batch in portfolio.batches:
        action = read.get(ActionPlan, batch.action_id)
        if action is None:
            raise error("当前完整原子动作分母缺失")
        original = verify_original_batch_action(read, portfolio, batch.batch_number, action, now)
        operations = list(
            read.scalars(select(BankOperation).where(BankOperation.action_plan_id == action.id))
        )
        receipts = list(
            read.scalars(select(ActionReceipt).where(ActionReceipt.action_plan_id == action.id))
        )
        if len(operations) > 1 or len(receipts) > 1:
            raise error("原操作或回执分母冲突")
        if batch.batch_number < number:
            if (
                original.status not in {"SUCCEEDED", "RECONCILED"}
                or original.bank_status != "SETTLED"
                or original.receipt is None
            ):
                raise error("未决或未核前批阻断后批", "FULL_ASSET_PREDECESSOR_UNRESOLVED")
        elif batch.batch_number > number and (
            operations or receipts or original.status not in {"PLANNED", "AUTHORIZED"}
        ):
            raise error("后批已越过当前原顺序", "FULL_ASSET_ORDER_VIOLATION")
        elif batch.batch_number == number and (operations or receipts):
            raise error(
                "原已有银行操作只允许同键恢复，不能新accept", "FULL_ASSET_ORIGINAL_OPERATION_EXISTS"
            )
    batch = portfolio.batches[number - 1]
    context = load_execution_context(
        read, user_id, batch.command.effect, now, own_action_id=batch.action_id
    )
    protection = compute_full_annual_protection(read, user_id, now)
    try:
        return validate_remaining_combination(
            portfolio,
            context,
            list(range(number, len(portfolio.batches) + 1)),
            now,
            current_full_configuration_hash=full.current_version.content_hash,
            full_sources=protection.full_policy_sources,
            full_source_issues=[row.code for row in protection.source_issues],
            full_inventory_complete=protection.projection.full_obligations_complete_within_registered_current_scope,
        )
    except (ValueError, TypeError) as cause:
        raise error(str(cause), "FULL_ASSET_REMAINING_COMBINATION_NOT_READY") from cause


def _pipeline_guard(
    engine: Engine,
    session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
) -> FullAssetRemainingCheck | None:
    marker = action.request.get("full_asset_execution")
    if marker is None:
        # The original pipeline calls this only for a marker/new key or an actual
        # immutable Batch.action_plan_id association. Stripping marker and key
        # cannot turn that persisted identity into a legacy purchase.
        raise error("原已绑定完整组合的动作marker缺失，不能降为legacy")
    try:
        binding = FullAssetBatchBinding.model_validate_json(json.dumps(marker))
    except (ValueError, TypeError) as cause:
        raise error("原子动作marker合同已变化") from cause
    if (
        action.user_id != command.effect.user_id
        or action.id != command.effect.operation_id
        or configuration_hash(action.request) != action.request_hash
        or configuration_hash(command.model_dump(mode="json")) != binding.command_hash
    ):
        raise error("调用者原command/user/hash与组合marker不同")
    # The caller already holds the original user lock. The new RR/RO transaction
    # sees only committed source facts, including this child's phase1 reservation.
    with fresh_read(engine) as read:
        parent, portfolio, _, _ = read_portfolio_original(
            read, action.user_id, binding.portfolio_id, _now(now)
        )
        if (
            binding != batch_binding(portfolio, binding.batch_number)
            or portfolio.batches[binding.batch_number - 1].action_id != action.id
            or parent.epoch_id != binding.epoch_id
        ):
            raise error("原parent/批序/action/hash绑定不同")
        return _fresh_whole(
            read, action.user_id, parent.id, binding.batch_number, _now(now), require_consent=True
        )


def enforce_full_asset_batch_phase1(
    engine: Engine,
    session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
) -> FullAssetRemainingCheck | None:
    return _pipeline_guard(engine, session, action, command, now)


def enforce_full_asset_batch_acceptance(
    engine: Engine,
    session: Session,
    action: ActionPlan,
    command: BankCommand,
    now: datetime,
) -> FullAssetRemainingCheck | None:
    return _pipeline_guard(engine, session, action, command, now)


def prepare_full_asset_execution(
    engine: Engine,
    user_id: UUID,
    body: FullAssetPrepareRequest,
    now: datetime,
) -> FullAssetExecutionResponse:
    require_original_pipeline_guards()
    body = FullAssetPrepareRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        if (
            session.scalar(
                select(FullAssetExecutionConsent.id).where(
                    FullAssetExecutionConsent.user_id == user_id,
                    FullAssetExecutionConsent.epoch_id == body.expected_epoch_id,
                    FullAssetExecutionConsent.idempotency_key == body.idempotency_key,
                )
            )
            is not None
        ):
            raise error("准备与确认不能复用同一原键", "IDEMPOTENCY_CONFLICT")
        existing = session.scalar(
            select(FullAssetExecutionPortfolio).where(
                FullAssetExecutionPortfolio.user_id == user_id,
                FullAssetExecutionPortfolio.epoch_id == body.expected_epoch_id,
                FullAssetExecutionPortfolio.idempotency_key == body.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request != body.model_dump(mode="json"):
                raise error("同原组合键不能改变原意图", "IDEMPOTENCY_CONFLICT")
            portfolio_id = existing.id
        else:
            with fresh_read(engine) as read:
                preview = preview_full_asset_execution(read, user_id, body, now)
            if preview.portfolio is None or preview.state != "READY_TO_REVIEW":
                raise error(
                    "当前组合不可准备：" + ",".join(preview.reasons),
                    "FULL_ASSET_EXECUTION_NOT_READY",
                )
            portfolio = preview.portfolio
            portfolio_id = portfolio.portfolio_id
            parent = FullAssetExecutionPortfolio(
                id=portfolio_id,
                user_id=user_id,
                created_at=now,
                epoch_id=portfolio.epoch_id,
                idempotency_key=body.idempotency_key,
                request=body.model_dump(mode="json"),
                request_hash=portfolio.client_request_hash,
                portfolio=portfolio.model_dump(mode="json"),
                portfolio_hash=portfolio.portfolio_hash,
                expires_at=portfolio.expires_at,
            )
            session.add(parent)
            session.flush()
            # Register every immutable binding before any child action is available.
            for batch in portfolio.batches:
                session.add(
                    FullAssetExecutionBatch(
                        id=uuid5(portfolio_id, f"binding:{batch.batch_number}"),
                        user_id=user_id,
                        created_at=now,
                        portfolio_id=portfolio_id,
                        epoch_id=portfolio.epoch_id,
                        batch_number=batch.batch_number,
                        action_plan_id=batch.action_id,
                        bank_idempotency_key=batch.bank_idempotency_key,
                        command=batch.command.model_dump(mode="json"),
                        command_hash=configuration_hash(batch.command.model_dump(mode="json")),
                        catalogue_version_id=batch.catalogue.catalogue_version_id,
                        product_record_hash=batch.catalogue.product_record_hash,
                    )
                )
            session.flush()
            for batch in portfolio.batches:
                _prepare_original_child(session, portfolio, batch.batch_number, now)
    return _result(engine, user_id, portfolio_id, now)


def _prepare_original_child(
    session: Session, portfolio: FullAssetFrozenPortfolio, number: int, now: datetime
) -> None:
    batch = portfolio.batches[number - 1]
    effect = batch.command.effect
    if session.get(ActionPlan, batch.action_id) is not None:
        raise error("原子动作UUID已存在，不能复用其它请求")
    start_capture(session)
    context = load_execution_context(session, portfolio.user_id, effect, now)
    validation = revalidate_execution(effect, context)
    if validation.status not in {"READY", "CONFIRMATION_REQUIRED"}:
        raise error("准备时原MVP重验未通过", "FULL_ASSET_ORIGINAL_REVALIDATION_REJECTED")
    intent = {"kind": "purchase_asset", "policy_id": str(effect.policy_id)}
    payload = {
        "intent": intent,
        "execution": batch.command.model_dump(mode="json"),
        "confirmation_evidence_id": str(
            uuid5(batch.action_id, "confirmation:" + batch.command.effect_hash)
        ),
        "prepared_validation": validation.model_dump(mode="json"),
        "full_asset_execution": batch_binding(portfolio, number).model_dump(mode="json"),
    }
    if effect.income_uses:
        from app.services.income_ledger import read_income_state

        income = read_income_state(session, portfolio.user_id, now)
        payload["income_evidence"] = {"id": str(income.evidence_id), "hash": income.evidence_hash}
    capture = current_capture(session)
    assert capture is not None
    run_id = uuid5(batch.action_id, "decision")
    # Keep the original supported algorithm. Its top-level action_request binds
    # the whole marker and original payload, so deleting it cannot become legacy.
    trace = build_trace(
        run_id=run_id,
        user_id=portfolio.user_id,
        action_id=batch.action_id,
        parent_run_id=None,
        phase="PREPARE",
        as_of=now,
        algorithm_versions={
            "execution": "economic-effect-revalidation-v1",
            "boundary": validation.baseline_boundary.algorithm_version,
        },
        inputs={
            "effect": effect.model_dump(mode="json"),
            "execution_context": context.model_dump(mode="json"),
            "planning": capture.inputs,
            "intent": intent,
            "confirmation": None,
            "action_request": payload,
        },
        sources=list(capture.sources.values()),
        policies=list(capture.policies.values()),
        constraints=validation_constraints(validation),
        candidates=capture.candidates,
        outcome={
            "validation": validation.model_dump(mode="json"),
            "autonomy_level": "ASK_ONCE",
            "decision_status": "COMPUTED",
        },
    )
    # The trace writer checks its current action relation, so persist the run and
    # child identities first, then record its actual complete typed snapshot.
    from app.db.models import DecisionRun

    run = DecisionRun(
        id=run_id,
        user_id=portfolio.user_id,
        created_at=now,
        idempotency_key=batch.bank_idempotency_key,
        trigger_type="ACTION_PREPARE",
        algorithm_version="economic-effect-revalidation-v1",
        as_of=now,
        input_snapshot={},
        snapshot_hash=configuration_hash({}),
        policy_version_ids=[],
        evidence_ids=[],
        result={},
        status="PENDING",
        completed_at=None,
    )
    session.add(run)
    session.flush()
    action = ActionPlan(
        id=batch.action_id,
        user_id=portfolio.user_id,
        created_at=now,
        decision_run_id=run_id,
        policy_version_id=effect.policy_version_id,
        source_account_id=effect.cash_uses[0].account_id,
        destination_account_id=None,
        goal_id=effect.goal_id,
        product_id=effect.product_id,
        position_id=None,
        action_type="ASSET_PURCHASE",
        amount_cents=effect.amount_cents,
        autonomy_level="ASK_ONCE",
        status="PLANNED",
        idempotency_key=batch.bank_idempotency_key,
        request=payload,
        request_hash=configuration_hash(payload),
        authorized_at=None,
        expires_at=effect.expires_at,
    )
    session.add(action)
    session.flush()
    record_trace(session, trace, existing_run=run)
    _epochs(session, portfolio.user_id, now, action.id)
    refresh_execution_exposure(session, portfolio.user_id, now, action.id)
    session.flush()
    record_action_created(session, action, now)
    # This grouped ASK operation is never published to an automatic worker.


def confirm_full_asset_execution(
    engine: Engine,
    user_id: UUID,
    portfolio_id: UUID,
    body: FullAssetConfirmRequest,
    now: datetime,
) -> FullAssetExecutionResponse:
    require_original_pipeline_guards()
    body = FullAssetConfirmRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        parent, portfolio, _, epoch = read_portfolio_original(session, user_id, portfolio_id, now)
        if (
            session.scalar(
                select(FullAssetExecutionPortfolio.id).where(
                    FullAssetExecutionPortfolio.user_id == user_id,
                    FullAssetExecutionPortfolio.epoch_id == body.expected_epoch_id,
                    FullAssetExecutionPortfolio.idempotency_key == body.idempotency_key,
                )
            )
            is not None
            or session.scalar(
                select(FullAssetExecutionConsent.id).where(
                    FullAssetExecutionConsent.user_id == user_id,
                    FullAssetExecutionConsent.epoch_id == body.expected_epoch_id,
                    FullAssetExecutionConsent.idempotency_key == body.idempotency_key,
                    FullAssetExecutionConsent.portfolio_id != portfolio_id,
                )
            )
            is not None
        ):
            raise error("原确认键不能指向其它准备或确认", "IDEMPOTENCY_CONFLICT")
        if (
            body.expected_epoch_id != epoch.id
            or body.reviewed_portfolio_hash != portfolio.portfolio_hash
        ):
            raise error("确认必须绑定完整原组合/epoch/hash", "FULL_ASSET_CONFIRMATION_MISMATCH")
        previous = read_consent_original(session, parent, portfolio, now, current=False)
        if previous is not None:
            if previous.request != body.model_dump(mode="json"):
                raise error("原整体确认不能换键或换body", "IDEMPOTENCY_CONFLICT")
        else:
            with fresh_read(engine) as read:
                _fresh_whole(read, user_id, portfolio_id, 1, now, require_consent=False)
            identity = uuid5(portfolio_id, "whole-confirmation:" + portfolio.portfolio_hash)
            content = {
                "protocol": "full-asset-portfolio-consent-v1",
                "simulation": True,
                "user_id": str(user_id),
                "portfolio_id": str(portfolio_id),
                "epoch_id": str(epoch.id),
                "portfolio_hash": portfolio.portfolio_hash,
                "client_request_hash": portfolio.client_request_hash,
                "accepted": True,
                "confirmed_at": now.isoformat(),
                "valid_until": portfolio.expires_at.isoformat(),
            }
            proof = EvidenceItem(
                id=identity,
                user_id=user_id,
                created_at=now,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type="USER_FULL_ASSET_PORTFOLIO_CONFIRMATION",
                source_ref=str(portfolio_id),
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
                observed_at=now,
                valid_from=now,
                valid_to=portfolio.expires_at,
            )
            session.add(proof)
            session.flush()
            session.add(
                FullAssetExecutionConsent(
                    id=uuid5(portfolio_id, "consent"),
                    user_id=user_id,
                    created_at=now,
                    portfolio_id=portfolio_id,
                    epoch_id=epoch.id,
                    idempotency_key=body.idempotency_key,
                    request=body.model_dump(mode="json"),
                    request_hash=configuration_hash(body.model_dump(mode="json")),
                    portfolio_hash=portfolio.portfolio_hash,
                    evidence_id=identity,
                    evidence_hash=proof.content_hash,
                    original_evidence=row_copy(proof),
                )
            )
            session.flush()
            for batch in portfolio.batches:
                _confirm_original_child(session, portfolio, batch.batch_number, identity, now)
    return _result(engine, user_id, portfolio_id, now)


def _confirm_original_child(
    session: Session,
    portfolio: FullAssetFrozenPortfolio,
    number: int,
    whole_evidence_id: UUID,
    now: datetime,
) -> None:
    batch = portfolio.batches[number - 1]
    action = session.get(ActionPlan, batch.action_id)
    if action is None or action.status not in {"PLANNED", "AUTHORIZED"}:
        raise error("原子动作不可追加组合确认")
    before_status, before = action.status, audit_subject_data(action)
    identity = UUID(action.request["confirmation_evidence_id"])
    if session.get(EvidenceItem, identity) is not None:
        raise error("子动作已经存在独立确认，不能静默归入新整体确认")
    content = {
        "simulation": True,
        "user_id": str(portfolio.user_id),
        "action_id": str(action.id),
        "effect_hash": batch.command.effect_hash,
        "accepted": True,
        "confirmed_at": now.isoformat(),
        "valid_until": portfolio.expires_at.isoformat(),
    }
    session.add(
        EvidenceItem(
            id=identity,
            user_id=portfolio.user_id,
            created_at=now,
            evidence_level="USER_CONFIRMED_ACTION",
            source_type="USER_ACTION_CONFIRMATION",
            source_ref=str(action.id),
            content=content,
            content_hash=configuration_hash(content),
            status="VALID",
            observed_at=now,
            valid_from=now,
            valid_to=portfolio.expires_at,
        )
    )
    session.flush()
    from app.services.decision_recording import capture_evidence, record_execution_trace

    start_capture(session)
    context = load_execution_context(session, portfolio.user_id, batch.command.effect, now)
    confirmation = read_execution_confirmation(session, batch.command.effect, now)
    validation = revalidate_execution(batch.command.effect, context, confirmation=confirmation)
    if validation.status != "READY":
        raise error("原明确确认时当前MVP重验未通过")
    capture_evidence(session, portfolio.user_id, [whole_evidence_id])
    record_execution_trace(
        session,
        batch.command.effect,
        validation,
        now,
        "CONFIRM",
        parent_run_id=action.decision_run_id,
        autonomy_level="ASK_ONCE",
        confirmation=confirmation,
    )
    action.status, action.authorized_at = "AUTHORIZED", now
    _epochs(session, portfolio.user_id, now, action.id)
    refresh_execution_exposure(session, portfolio.user_id, now, action.id)
    session.flush()
    record_action_transition(
        session,
        action,
        before_status,
        now,
        reason_code="USER_ACTION_CONFIRMED",
        cause_ref=str(identity),
        details={
            "confirmation_evidence_id": str(identity),
            "effect_hash": batch.command.effect_hash,
            "whole_confirmation_evidence_id": str(whole_evidence_id),
        },
        before_data=before,
    )


def execute_full_asset_execution(
    engine: Engine,
    user_id: UUID,
    portfolio_id: UUID,
    body: FullAssetExecuteRequest,
    now: datetime,
) -> FullAssetExecutionResponse:
    require_original_pipeline_guards()
    body = FullAssetExecuteRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    # Never hold an outer user row lock while calling the original three-session
    # executor. Its own user lock and both guards serialize independent accepts.
    with fresh_read(engine) as read:
        parent, portfolio, _, epoch = read_portfolio_original(read, user_id, portfolio_id, now)
        if (
            body.expected_epoch_id != epoch.id
            or body.reviewed_portfolio_hash != portfolio.portfolio_hash
        ):
            raise error("执行必须绑定同一原整体hash与epoch")
        if read_consent_original(read, parent, portfolio, now, current=False) is None:
            raise error("原整体确认不存在", "FULL_ASSET_CONFIRMATION_REQUIRED")
        original = read_full_asset_execution(read, user_id, portfolio_id, now)
        if original.state == "RETAINED_HISTORY":
            raise error("封存历史不重建当前动作", "FULL_ASSET_EPOCH_CHANGED")
        next_action = select_original_batch(original, body)
        assert next_action.original_action is not None
        if next_action.original_action.receipt is not None:
            if (
                next_action.original_action.status not in {"SUCCEEDED", "RECONCILED"}
                or next_action.original_action.bank_status != "SETTLED"
            ):
                raise error("原批次回执与银行状态不一致")
            return original
        if next_action.original_action.status in {"FAILED", "INVALIDATED", "CANCELLED"}:
            raise error("原批次已停止；不能更换原key跳到后批", "FULL_ASSET_PORTFOLIO_STOPPED")
        operation = read.scalar(
            select(BankOperation).where(BankOperation.action_plan_id == next_action.action_id)
        )
        if operation is None:
            _fresh_whole(
                read, user_id, portfolio_id, next_action.batch_number, now, require_consent=True
            )
        # Existing bank operation uses its original key/identity recovery. It does
        # not create a new grant, new amount or new operation after revocation.
        action_id = next_action.action_id
    execute_action(engine, user_id, action_id, now)
    return _result(engine, user_id, portfolio_id, now)
