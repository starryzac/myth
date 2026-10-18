"""Staged Next business adapter for the original persistent MVP question workflow.

This adapter is imported only by the independent Next factory.
It creates no financial authority and calls
no prepare/confirm/execute/bank service. FULL parent effects remain explicitly unsupported.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.v1.question_workflow import _postcommit_question_observation
from app.api.v1.zhiyu_catalog import identity, isolated
from app.api.v1.zhiyu_next import EngineDependency
from app.api.v1.zhiyu_policy_review import PrincipalDependency
from app.db.models import ActionPlan, Goal, Policy, PolicyVersion
from app.domain.finite_uncertainty import FiniteChoice, FinitePlanningVariable, IntentChoice
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import GoalIntent, IntentModel, PurchaseIntent
from app.services.audit_chain import audit_read_scope
from app.services.autonomy_envelope import _snapshot
from app.services.demo_console import _epoch
from app.services.execution_bank import has_full_asset_batch_binding
from app.services.finite_uncertainty import _original_action
from app.services.full_dynamic_goal_execution import has_full_dynamic_goal_binding
from app.services.full_experiment_asset_execution import has_full_experiment_asset_binding
from app.services.full_joint_goal_execution_guards import has_full_joint_goal_binding
from app.services.full_recovery_execution import has_full_recovery_binding
from app.services.policy_lifecycle import (
    PolicyLifecycleError,
    effective_status,
    is_version_authorized,
)
from app.services.question_workflow import (
    QuestionAnswerRequest,
    QuestionCloseRequest,
    QuestionCommandLookupResponse,
    QuestionRefreshRequest,
    QuestionStartRequest,
    QuestionWorkflowResponse,
    answer_question_session,
    close_question_session,
    read_question_command,
    read_question_session,
    read_question_start_command,
    start_question_session,
)
from app.services.zhiyu_orchestration import _audit_healthy, serial_user
from fastapi import APIRouter, Depends, Response
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

BusinessScope = Literal["MVP_NOMINAL_GOALS", "MVP_ASSET_PURCHASE"]
SCOPE_VARIABLE: dict[BusinessScope, str] = {
    "MVP_NOMINAL_GOALS": "current_goal_preference",
    "MVP_ASSET_PURCHASE": "current_asset_preference",
}
PREFIX = "/api/v1/zhiyu-next/questions"
router = APIRouter(
    prefix=PREFIX, tags=["当前目标与资产的一次一问"], dependencies=[Depends(isolated)]
)


class StartRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    base_action_id: UUID
    business_scope: BusinessScope
    candidate_ids: Annotated[list[UUID], Field(min_length=2, max_length=8)]

    @model_validator(mode="after")
    def unique_candidates(self) -> "StartRequest":
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("The declared finite candidate IDs must be distinct")
        return self


class RevisionRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=16)]


class AnswerRequest(RevisionRequest):
    question_id: UUID
    choice_key: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]


class CloseRequest(IntentModel):
    expected_epoch_id: UUID
    client_request_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=17)]


def command_key(request_id: UUID) -> str:
    return "x05:" + request_id.hex


def require_question_user(principal: LocalActorPrincipal, user: UUID, now: datetime) -> None:
    try:
        require_local_user(principal, user, now)
    except ValueError:
        raise PolicyLifecycleError(
            "CURRENT_SIGNED_USER_REQUIRED", "需要本人当前USER确认会话", 403
        ) from None


def scope_error() -> PolicyLifecycleError:
    return PolicyLifecycleError(
        "UNSUPPORTED_BUSINESS_SCOPE",
        "当前入口仅核验原MVP单效果；FULL父计划必须比较整组效果",
        409,
    )


def native_start(body: StartRequest) -> QuestionStartRequest:
    """Only server-known reference IDs vary; all amounts and terms come from native sources."""
    choices = [
        FiniteChoice(
            key="candidate_" + target.hex,
            value=IntentChoice(
                kind="intent",
                intent=(
                    GoalIntent(kind="allocate_goal", goal_id=target)
                    if body.business_scope == "MVP_NOMINAL_GOALS"
                    else PurchaseIntent(kind="purchase_asset", policy_id=target)
                ),
            ),
        )
        for target in body.candidate_ids
    ]
    return QuestionStartRequest(
        expected_epoch_id=body.expected_epoch_id,
        idempotency_key=command_key(body.client_request_id),
        base_action_id=body.base_action_id,
        variables=[
            FinitePlanningVariable(
                variable_id=SCOPE_VARIABLE[body.business_scope],
                field="ACTION_INTENT",
                choices=choices,
                completeness="COMPLETE",
                source="USER_REQUEST",
                impact_scope="ORIGINAL_MVP_PLANNING_REQUEST",
            )
        ],
    )


def original_start(body: QuestionStartRequest) -> StartRequest:
    """Recover the exact Next declaration from the audited native original, never from fresh UI."""
    if len(body.variables) != 1 or not body.idempotency_key.startswith("x05:"):
        raise scope_error()
    variable = body.variables[0]
    scopes = [scope for scope, name in SCOPE_VARIABLE.items() if name == variable.variable_id]
    if len(scopes) != 1:
        raise scope_error()
    scope = scopes[0]
    targets: list[UUID] = []
    for choice in variable.choices:
        if not isinstance(choice.value, IntentChoice):
            raise scope_error()
        intent = choice.value.intent
        if scope == "MVP_NOMINAL_GOALS" and isinstance(intent, GoalIntent):
            target = intent.goal_id
        elif scope == "MVP_ASSET_PURCHASE" and isinstance(intent, PurchaseIntent):
            target = intent.policy_id
        else:
            raise scope_error()
        if choice.key != "candidate_" + target.hex:
            raise scope_error()
        targets.append(target)
    try:
        recovered = StartRequest(
            expected_epoch_id=body.expected_epoch_id,
            client_request_id=UUID(body.idempotency_key[4:]),
            base_action_id=body.base_action_id,
            business_scope=scope,
            candidate_ids=targets,
        )
    except ValueError:
        raise scope_error() from None
    if native_start(recovered).model_dump(mode="json") != body.model_dump(mode="json"):
        raise scope_error()
    return recovered


@contextmanager
def owned_read(engine: Engine) -> Iterator[Session]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with Session(bind=connection) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            with audit_read_scope(session):
                yield session


def validate_mvp_original(session: Session, user: UUID, action: UUID, now: datetime) -> UUID:
    row, _, intent, _ = _original_action(session, user, action, now)
    # These native guards also inspect immutable parent bindings; stripped marker is insufficient.
    if any(
        guard(session, row)
        for guard in (
            has_full_joint_goal_binding,
            has_full_dynamic_goal_binding,
            has_full_asset_batch_binding,
            has_full_experiment_asset_binding,
            has_full_recovery_binding,
        )
    ):
        raise scope_error()
    if isinstance(intent, GoalIntent):
        return intent.goal_id
    if isinstance(intent, PurchaseIntent):
        return intent.policy_id
    raise scope_error()


def members(session: Session, user: UUID, now: datetime) -> list[dict[str, Any]]:
    _snapshot(session)
    _audit_healthy(session, user)
    result: list[dict[str, Any]] = []
    policies = list(
        session.scalars(
            select(Policy)
            .where(
                Policy.user_id == user,
                Policy.policy_type.in_(("goal_saving", "asset_authorization")),
            )
            .order_by(Policy.id)
            .limit(10001)
        )
    )
    if len(policies) > 10000:
        raise PolicyLifecycleError("INPUT_LIMIT_EXCEEDED", "当前候选超过读取容量", 409)
    for policy in policies:
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.user_id == user, PolicyVersion.policy_id == policy.id)
            .order_by(PolicyVersion.version_number.desc())
            .limit(1)
        )
        if (
            version is None
            or effective_status(policy, version, now) != "ACTIVE"
            or not is_version_authorized(session, user, version.id, now)
        ):
            continue
        if configuration_hash(version.configuration) != version.content_hash:
            raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "当前候选策略原件不一致", 409)
        if policy.policy_type == "goal_saving":
            goal = session.scalar(
                select(Goal).where(Goal.user_id == user, Goal.policy_id == policy.id)
            )
            if goal is None or goal.policy_version_id != version.id:
                continue
            target, scope, title = goal.id, "MVP_NOMINAL_GOALS", goal.name
        else:
            target, scope, title = policy.id, "MVP_ASSET_PURCHASE", policy.name
        result.append(
            {
                "candidate_id": str(target),
                "business_scope": scope,
                "title": title,
                "policy_id": str(policy.id),
                "current_version_id": str(version.id),
                "configuration_hash": version.content_hash,
                "nominal_mvp_effect_only": True,
                "full_parent_effect_covered": False,
                "bank_authority": False,
            }
        )
    return result


def validated_start(
    session: Session, user: UUID, body: StartRequest, now: datetime
) -> QuestionStartRequest:
    target = validate_mvp_original(session, user, body.base_action_id, now)
    row = session.get(ActionPlan, body.base_action_id)
    assert row is not None
    kind = row.request["intent"]["kind"]
    if (kind == "allocate_goal") != (body.business_scope == "MVP_NOMINAL_GOALS"):
        raise scope_error()
    if target not in body.candidate_ids:
        raise PolicyLifecycleError("ORIGINAL_CANDIDATE_REQUIRED", "声明必须保留原行动目标", 409)
    available = {
        UUID(item["candidate_id"])
        for item in members(session, user, now)
        if item["business_scope"] == body.business_scope
    }
    if not set(body.candidate_ids) <= available:
        raise PolicyLifecycleError(
            "CANDIDATE_NOT_CURRENT_OR_OWNED", "候选必须是本人当前已确认原件", 409
        )
    return native_start(body)


def workflow(result: QuestionWorkflowResponse) -> dict[str, Any]:
    state = result.current_revision
    # The native immutable declaration defines scope even after rebase, close or archival.
    base = QuestionStartRequest(
        expected_epoch_id=state.epoch_id,
        idempotency_key="x05:" + UUID(int=0).hex,
        base_action_id=state.base_action_id,
        variables=state.variables,
    )
    declared = original_start(base)
    return {
        **result.model_dump(mode="json"),
        "business_scope": declared.business_scope,
        "candidate_ids": [str(value) for value in declared.candidate_ids],
        "bank_authority": False,
        "full_parent_effect_covered": False,
        "finite_domain_scope": "ORIGINAL_EXPLICIT_REFERENCE_SET",
        "requires_separate_current_financial_confirmation": True,
    }


def lookup(result: QuestionCommandLookupResponse) -> dict[str, Any]:
    mapped = result.model_dump(mode="json")
    if result.status == "RECORDED":
        assert result.original_command is not None
        command = result.original_command
        if result.original_start_request is not None:
            body = original_start(result.original_start_request)
            path = PREFIX + "/sessions"
            wire = body.model_dump(mode="json")
        else:
            wire = dict(command["request"])
            key = wire.pop("idempotency_key")
            if not isinstance(key, str) or not key.startswith("x05:"):
                raise scope_error()
            wire["client_request_id"] = str(UUID(key[4:]))
            tail = {"ANSWER": "answers", "REFRESH": "refresh", "CLOSE": "close"}.get(
                command["kind"]
            )
            if tail is None:
                raise scope_error()
            path = PREFIX + f"/sessions/{result.session_id}/{tail}"
        mapped["original_request"] = {"path": path, "body": wire}
    else:
        mapped["original_request"] = None
    mapped["bank_authority"] = False
    return mapped


@router.get("/context")
def context(
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return {
            **identity(session, user.id, engine),
            "protocol": "zhiyu-next-mvp-question-context-v1",
            "candidates": members(session, user.id, now),
            "max_declared_candidates": 8,
            "requires_original_unsubmitted_mvp_action": True,
            "full_parent_effect_status": "NOT_IMPLEMENTED",
            "bank_authority": False,
            "grants_authority": False,
        }


@router.post("/sessions")
def start(
    body: StartRequest,
    response: Response,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    with serial_user(engine, user.id):
        with owned_read(engine) as session:
            previous = read_question_start_command(
                session, user.id, body.expected_epoch_id, command_key(body.client_request_id), now
            )
            if previous.status == "RECORDED":
                assert previous.original_start_request is not None
                if original_start(previous.original_start_request) != body:
                    raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "原提问键不能替换声明", 409)
                native = previous.original_start_request
            else:
                from app.services.full_asset_mode_workflow import require_no_pending_vector

                require_no_pending_vector(session, user.id, body.expected_epoch_id)
                native = validated_start(session, user.id, body, now)
            context_identity = identity(session, user.id, engine)
        actual = start_question_session(engine, user.id, native, now)
        actual = _postcommit_question_observation(engine, user.id, actual, now, response)
        return {**context_identity, **workflow(actual)}


@router.get("/commands/{epoch_id}/by-start-key/{client_request_id}")
def start_command(
    epoch_id: UUID,
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return {
            **identity(session, user.id, engine),
            **lookup(
                read_question_start_command(
                    session, user.id, epoch_id, command_key(client_request_id), now
                )
            ),
        }


@router.get("/sessions/{session_id}")
def read(
    session_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        return {
            **identity(session, user.id, engine),
            **workflow(read_question_session(session, user.id, session_id, now)),
        }


@router.get("/sessions/{session_id}/commands/by-key/{client_request_id}")
def command(
    session_id: UUID,
    client_request_id: UUID,
    session: SessionDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    with audit_read_scope(session):
        # Validate that this persisted session belongs to this business adapter.
        workflow(read_question_session(session, user.id, session_id, now))
        return {
            **identity(session, user.id, engine),
            **lookup(
                read_question_command(
                    session, user.id, session_id, command_key(client_request_id), now
                )
            ),
        }


def mutation_scope(
    engine: Engine,
    user: UUID,
    session_id: UUID,
    now: datetime,
    *,
    new_question_possible: bool = True,
    original_key: UUID | None = None,
) -> dict[str, Any]:
    with owned_read(engine) as session:
        workflow(read_question_session(session, user, session_id, now))
        repeated = (
            original_key is not None
            and read_question_command(
                session, user, session_id, command_key(original_key), now
            ).status
            == "RECORDED"
        )
        if new_question_possible and not repeated:
            from app.services.full_asset_mode_workflow import require_no_pending_vector

            require_no_pending_vector(session, user, _epoch(session, user))
        return identity(session, user, engine)


@router.post("/sessions/{session_id}/answers")
def answer(
    session_id: UUID,
    body: AnswerRequest,
    response: Response,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    with serial_user(engine, user.id):
        context_identity = mutation_scope(
            engine, user.id, session_id, now, original_key=body.client_request_id
        )
        native = QuestionAnswerRequest(
            **body.model_dump(exclude={"client_request_id"}),
            idempotency_key=command_key(body.client_request_id),
        )
        actual = answer_question_session(engine, user.id, session_id, native, now)
        actual = _postcommit_question_observation(engine, user.id, actual, now, response)
        return {**context_identity, **workflow(actual)}


@router.post("/sessions/{session_id}/refresh")
def refresh(
    session_id: UUID,
    body: RevisionRequest,
    response: Response,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    with serial_user(engine, user.id):
        context_identity = mutation_scope(
            engine, user.id, session_id, now, original_key=body.client_request_id
        )
        native = QuestionRefreshRequest(
            **body.model_dump(exclude={"client_request_id"}),
            idempotency_key=command_key(body.client_request_id),
        )
        actual = answer_question_session(engine, user.id, session_id, native, now)
        actual = _postcommit_question_observation(engine, user.id, actual, now, response)
        return {**context_identity, **workflow(actual)}


@router.post("/sessions/{session_id}/close")
def close(
    session_id: UUID,
    body: CloseRequest,
    response: Response,
    principal: PrincipalDependency,
    engine: EngineDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> dict[str, Any]:
    require_question_user(principal, user.id, now)
    with serial_user(engine, user.id):
        context_identity = mutation_scope(
            engine, user.id, session_id, now, new_question_possible=False
        )
        native = QuestionCloseRequest(
            **body.model_dump(exclude={"client_request_id"}),
            idempotency_key=command_key(body.client_request_id),
        )
        actual = close_question_session(engine, user.id, session_id, native, now)
        actual = _postcommit_question_observation(engine, user.id, actual, now, response)
        return {**context_identity, **workflow(actual)}
