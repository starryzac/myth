"""Durable USER/ASK joint workflow over the unchanged original finance pipeline."""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import cast
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.full_joint_goal_execution_models import (
    FullJointGoalExecutionChild,
    FullJointGoalExecutionConsent,
    FullJointGoalExecutionPlan,
)
from app.db.models import ActionPlan, BankOperation, EvidenceItem
from app.domain.decision_trace import build_trace
from app.domain.full_joint_goal_execution import (
    ALGORITHM,
    MARKER,
    FullJointFrozenPlan,
    FullJointGoalConfirmRequest,
    FullJointGoalExecuteRequest,
    FullJointGoalPrepareRequest,
    require_fixed_child,
    whole_consent_content,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import (
    ActionResponse,
    ConfirmActionRequest,
    GoalIntent,
    PrepareActionRequest,
)
from app.services.audit_chain import audit_read_scope, row_copy
from app.services.decision_trace import evidence_copy, get_decision_trace, record_trace
from app.services.execution import _lock_user, confirm_action, execute_action, prepare_action
from app.services.full_joint_goal_execution import capture_full_joint_goal_execution
from app.services.full_joint_goal_execution_store import (
    FullJointGoalExecutionResponse,
    child_binding,
    error,
    read_full_joint_goal_execution,
    read_joint_consent,
    read_joint_plan_original,
)
from app.services.policy_lifecycle import _now
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def require_original_joint_pipeline_hooks() -> None:
    from app.services import execution, execution_bank, execution_sources

    if "_full_joint_goal_request" not in inspect.signature(
        execution.prepare_action
    ).parameters or any(
        getattr(module, "FULL_JOINT_GOAL_GUARDS_VERSION", None) != ALGORITHM
        for module in (execution, execution_bank, execution_sources)
    ):
        raise error(
            "JOINT_ORIGINAL_PIPELINE_NOT_IMPLEMENTED",
            "原准备/阶段1/独立银行首次accept的联合门未接通",
        )


def _principal(principal: LocalActorPrincipal, user_id: UUID, now: datetime) -> None:
    if not isinstance(principal, LocalActorPrincipal):
        raise error("JOINT_CURRENT_SIGNED_USER_REQUIRED")
    try:
        require_local_user(principal, user_id, now)
    except ValueError as cause:
        raise error("JOINT_CURRENT_SIGNED_USER_REQUIRED", str(cause)) from cause


@contextmanager
def fresh_read(engine: Engine) -> Iterator[Session]:
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with Session(bind=connection) as session, audit_read_scope(session):
            yield session


def _result(
    engine: Engine, user_id: UUID, plan_id: UUID, now: datetime
) -> FullJointGoalExecutionResponse:
    with fresh_read(engine) as read:
        return read_full_joint_goal_execution(read, user_id, plan_id, now)


def _persist_original_plan(session: Session, plan: FullJointFrozenPlan) -> None:
    body = plan.inputs.request
    parent = FullJointGoalExecutionPlan(
        id=plan.plan_id,
        user_id=plan.user_id,
        created_at=plan.prepared_at,
        epoch_id=plan.epoch_id,
        full_policy_id=body.full_policy_id,
        full_policy_version_id=body.expected_full_policy_version_id,
        idempotency_key=body.idempotency_key,
        request=body.model_dump(mode="json"),
        request_hash=configuration_hash(body.model_dump(mode="json")),
        plan=plan.model_dump(mode="json"),
        plan_hash=plan.plan_hash,
        expires_at=plan.expires_at,
    )
    session.add(parent)
    session.flush()
    for child in plan.children:
        session.add(
            FullJointGoalExecutionChild(
                id=uuid5(plan.plan_id, f"binding:{child.child_number}"),
                user_id=plan.user_id,
                created_at=plan.prepared_at,
                plan_id=plan.plan_id,
                epoch_id=plan.epoch_id,
                child_number=child.child_number,
                goal_id=child.goal_id,
                original_mvp_version_id=child.command.effect.policy_version_id,
                action_plan_id=child.action_id,
                bank_idempotency_key=child.bank_idempotency_key,
                command=child.command.model_dump(mode="json"),
                command_hash=configuration_hash(child.command.model_dump(mode="json")),
            )
        )
    session.flush()


def prepare_full_joint_goal_execution(
    engine: Engine,
    user_id: UUID,
    body: FullJointGoalPrepareRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullJointGoalExecutionResponse:
    _principal(principal, user_id, now)
    require_original_joint_pipeline_hooks()
    body = FullJointGoalPrepareRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id):
        with Session(engine) as session, session.begin():
            _lock_user(session, user_id)
            conflict = session.scalar(
                select(FullJointGoalExecutionConsent.id).where(
                    FullJointGoalExecutionConsent.user_id == user_id,
                    FullJointGoalExecutionConsent.epoch_id == body.expected_epoch_id,
                    FullJointGoalExecutionConsent.idempotency_key == body.idempotency_key,
                )
            )
            if conflict is not None:
                raise error("IDEMPOTENCY_CONFLICT", "原确认键不能作为新准备键")
            previous = session.scalar(
                select(FullJointGoalExecutionPlan).where(
                    FullJointGoalExecutionPlan.user_id == user_id,
                    FullJointGoalExecutionPlan.epoch_id == body.expected_epoch_id,
                    FullJointGoalExecutionPlan.idempotency_key == body.idempotency_key,
                )
            )
            if previous is not None:
                if previous.request != body.model_dump(mode="json"):
                    raise error("IDEMPOTENCY_CONFLICT", "原联合准备键不能更换任何身份")
                _, plan, _, _ = read_joint_plan_original(session, user_id, previous.id, now)
            else:
                with fresh_read(engine) as read:
                    captured = capture_full_joint_goal_execution(read, user_id, body, now)
                if captured.preview.status != "READY_TO_REVIEW" or captured.preview.plan is None:
                    raise error(
                        "JOINT_CURRENT_COMPLETE_PLAN_NOT_READY", ",".join(captured.preview.reasons)
                    )
                plan = captured.preview.plan
                _persist_original_plan(session, plan)
                trace = build_trace(
                    run_id=uuid5(plan.plan_id, "planning-proof"),
                    user_id=user_id,
                    as_of=now,
                    phase="EVALUATION",
                    action_id=None,
                    parent_run_id=None,
                    algorithm_versions={MARKER: ALGORITHM},
                    inputs={"joint_execution_input": captured.inputs.model_dump(mode="json")},
                    sources=list(captured.originals.sources.values()),
                    policies=list(captured.originals.policies.values()),
                    constraints=[],
                    candidates=[],
                    outcome={
                        "joint_execution_plan": plan.model_dump(mode="json"),
                        "decision_status": "COMPUTED",
                    },
                )
                record_trace(session, trace)
        # No User row lock is retained here. Each original prepare owns its own
        # transaction; durable bindings already exist, even after a crash.
        call = cast(Callable[..., ActionResponse], prepare_action)
        for child in plan.children:
            with fresh_read(engine) as read:
                action = read.get(ActionPlan, child.action_id)
                if action is not None:
                    from app.services.full_joint_goal_execution_store import (
                        verify_original_joint_child,
                    )

                    verify_original_joint_child(read, plan, child.child_number, action, now)
                    continue
            call(
                engine,
                user_id,
                PrepareActionRequest(
                    idempotency_key=child.bank_idempotency_key,
                    intent=GoalIntent(kind="allocate_goal", goal_id=child.goal_id),
                ),
                now,
                _full_joint_goal_request=child_binding(plan, child.child_number),
            )
    return _result(engine, user_id, plan.plan_id, now)


def confirm_full_joint_goal_execution(
    engine: Engine,
    user_id: UUID,
    plan_id: UUID,
    body: FullJointGoalConfirmRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullJointGoalExecutionResponse:
    _principal(principal, user_id, now)
    require_original_joint_pipeline_hooks()
    body = FullJointGoalConfirmRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        _lock_user(session, user_id)
        _, plan, _, epoch = read_joint_plan_original(session, user_id, plan_id, now)
        if body.expected_epoch_id != epoch.id or body.reviewed_plan_hash != plan.plan_hash:
            raise error("JOINT_WHOLE_CONFIRMATION_MISMATCH")
        previous = read_joint_consent(session, plan, now, current=False)
        if previous is not None:
            if previous.original_request != body:
                raise error("IDEMPOTENCY_CONFLICT", "原完整确认不能换键或body")
        else:
            if epoch.status != "OPEN" or not plan.prepared_at <= now < plan.expires_at:
                raise error("JOINT_CURRENT_WHOLE_CONFIRMATION_WINDOW_REQUIRED")
            for model in (FullJointGoalExecutionPlan, FullJointGoalExecutionConsent):
                if (
                    session.scalar(
                        select(model.id).where(
                            model.user_id == user_id,
                            model.epoch_id == epoch.id,
                            model.idempotency_key == body.idempotency_key,
                        )
                    )
                    is not None
                ):
                    raise error("IDEMPOTENCY_CONFLICT", "准备与确认必须使用不同原键")
            with fresh_read(engine) as read:
                ready = read_full_joint_goal_execution(read, user_id, plan_id, now)
                if ready.state != "PREPARED_UNRESERVED":
                    raise error("JOINT_ALL_ORIGINAL_CHILDREN_REQUIRED_BEFORE_CONSENT")
                from app.services.full_joint_goal_execution_guards import _current

                for child in plan.children:
                    _current(read, user_id, plan, child.child_number, now, require_consent=False)
            identity = uuid5(plan_id, "whole-confirmation:" + plan.plan_hash)
            content = whole_consent_content(plan, body, principal, now).model_dump(mode="json")
            proof = EvidenceItem(
                id=identity,
                user_id=user_id,
                created_at=now,
                evidence_level="USER_CONFIRMED_ACTION",
                source_type="USER_JOINT_GOAL_CONFIRMATION",
                source_ref=str(plan_id),
                content=content,
                content_hash=configuration_hash(content),
                status="VALID",
                observed_at=now,
                valid_from=now,
                valid_to=plan.expires_at,
            )
            session.add(proof)
            session.flush()
            planning_trace = get_decision_trace(
                session, user_id, uuid5(plan.plan_id, "planning-proof"), now
            )
            if planning_trace.trace is None:
                raise error("JOINT_ORIGINAL_PLANNING_TRACE_REQUIRED_FOR_CONSENT")
            original_trace = build_trace(
                run_id=uuid5(plan.plan_id, "whole-confirmation-proof"),
                user_id=user_id,
                as_of=now,
                phase="EVALUATION",
                action_id=None,
                parent_run_id=uuid5(plan.plan_id, "planning-proof"),
                algorithm_versions={MARKER: ALGORITHM},
                inputs={
                    "joint_execution_input": plan.inputs.model_dump(mode="json"),
                    "joint_confirmation_request": body.model_dump(mode="json"),
                },
                sources=[*planning_trace.trace.sources, evidence_copy(proof)],
                policies=planning_trace.trace.policies,
                constraints=[],
                candidates=[],
                outcome={
                    "joint_execution_plan": plan.model_dump(mode="json"),
                    "joint_confirmation": content,
                    "decision_status": "COMPUTED",
                },
            )
            record_trace(session, original_trace)
            session.add(
                FullJointGoalExecutionConsent(
                    id=uuid5(plan_id, "consent"),
                    user_id=user_id,
                    created_at=now,
                    plan_id=plan_id,
                    epoch_id=epoch.id,
                    idempotency_key=body.idempotency_key,
                    request=body.model_dump(mode="json"),
                    request_hash=configuration_hash(body.model_dump(mode="json")),
                    plan_hash=plan.plan_hash,
                    evidence_id=identity,
                    evidence_hash=proof.content_hash,
                    original_evidence=row_copy(proof),
                )
            )
            session.flush()
    return _result(engine, user_id, plan_id, now)


def execute_full_joint_goal_child(
    engine: Engine,
    user_id: UUID,
    plan_id: UUID,
    body: FullJointGoalExecuteRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FullJointGoalExecutionResponse:
    _principal(principal, user_id, now)
    require_original_joint_pipeline_hooks()
    body = FullJointGoalExecuteRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id):
        with fresh_read(engine) as read:
            current = read_full_joint_goal_execution(read, user_id, plan_id, now)
            plan = current.original_plan
            child = require_fixed_child(plan, body, [row.state for row in current.children])
            view = current.children[child.child_number - 1]
            if view.state == "ORIGINAL_RECEIPT_VERIFIED":
                return current
            banks = list(
                read.scalars(
                    select(BankOperation).where(BankOperation.action_plan_id == child.action_id)
                )
            )
            if len(banks) > 1 or any(row.user_id != user_id for row in banks):
                raise error("JOINT_ORIGINAL_BANK_DENOMINATOR_CONFLICT")
            # Original recorded bank operations are recovered under their same
            # key; a new authorization cannot replace the settled fact.
            if not banks:
                if read_joint_consent(read, plan, now, current=True) is None:
                    raise error("JOINT_EXACT_WHOLE_USER_CONSENT_REQUIRED")
                from app.services.full_joint_goal_execution_guards import _current

                _current(read, user_id, plan, child.child_number, now, require_consent=True)
            needs_confirmation = not banks and view.state == "PLANNED_UNRESERVED"
        if needs_confirmation:
            confirm_action(
                engine,
                user_id,
                child.action_id,
                ConfirmActionRequest(accepted=True, effect_hash=child.command.effect_hash),
                now,
            )
        execute_action(engine, user_id, child.action_id, now)
    return _result(engine, user_id, plan_id, now)
