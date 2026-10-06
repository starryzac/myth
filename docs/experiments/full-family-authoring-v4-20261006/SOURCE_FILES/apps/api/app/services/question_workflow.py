"""Append-only question receipts using actual fresh RO/RR worlds and existing typed audit."""

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.audit_guard import audit_command_guard
from app.db.models import ActionPlan, AuditEvent, DecisionRun
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence, TracePolicy
from app.domain.finite_uncertainty import FinitePlanningVariable
from app.domain.policy_configuration import configuration_hash
from app.domain.question_workflow import (
    CLOSE_ALGORITHM,
    MAX_REVISIONS,
    PROTOCOL,
    CommandKey,
    PendingPlanningQuestion,
    QuestionRevision,
    closed_revision,
    exact_answer,
    last_planning_reference,
    make_revision,
    restrict_fresh_worlds,
)
from app.services.action_contracts import IntentModel
from app.services.audit_chain import audit_read_scope, current_audit_epoch, verify_audit_chain
from app.services.autonomy_envelope import _snapshot
from app.services.decision_recording import DecisionCapture, capture_evidence, start_capture
from app.services.decision_trace import _stored_trace, record_trace
from app.services.finite_uncertainty import (
    FinitePlanningRequest,
    FinitePlanningResponse,
    analyze_finite_planning,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from pydantic import Field, StrictInt, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

NAMESPACE = UUID("d2c2c070-ed70-4d7e-969b-12d2a983b3df")
MAX_USER_WORKFLOW_RUNS = 2048


class QuestionStartRequest(FinitePlanningRequest):
    expected_epoch_id: UUID
    idempotency_key: CommandKey

    @field_validator("variables", mode="before")
    @classmethod
    def exact_json_variables(cls, value: Any) -> list[FinitePlanningVariable]:
        if type(value) is not list:
            raise ValueError("Variables must be a JSON array")
        return [
            FinitePlanningVariable.model_validate_json(
                row.model_dump_json()
                if isinstance(row, FinitePlanningVariable)
                else json.dumps(row, allow_nan=False)
            )
            for row in value
        ]


class QuestionAnswerRequest(IntentModel):
    expected_epoch_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS)]
    question_id: UUID
    choice_key: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]
    idempotency_key: CommandKey


class QuestionRefreshRequest(IntentModel):
    expected_epoch_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS)]
    idempotency_key: CommandKey


class QuestionCloseRequest(IntentModel):
    expected_epoch_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS + 1)]
    idempotency_key: CommandKey


class QuestionWorkflowResponse(BoundaryModel):
    protocol: Literal["full-one-question-v1"] = PROTOCOL
    simulation: Literal[True] = True
    planning_only: Literal[True] = True
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False
    original_receipt: QuestionRevision
    current_revision: QuestionRevision
    effective_state: Literal[
        "PENDING_ANSWER",
        "READY_FOR_REVIEW",
        "ALL_WORLDS_BLOCKED",
        "UNKNOWN",
        "STALE_RECOMPUTATION_REQUIRED",
        "ARCHIVED",
        "CLOSED",
    ]
    pending_question: PendingPlanningQuestion | None
    replayed_original_receipt: bool
    current_source_fingerprint: str | None
    fresh_evaluation_at: datetime | None
    persisted_workflow: Literal[True] = True
    legacy_analyzer_flags_apply_only_to_stateless_analysis: Literal[True] = True
    current_old_candidates_eligible: Literal[False] = False
    old_confirmation_inherited: Literal[False] = False
    bank_submission_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"

    @model_validator(mode="after")
    def closed_is_historical(self) -> "QuestionWorkflowResponse":
        if self.current_revision.state == "CLOSED" and (
            self.effective_state not in {"CLOSED", "ARCHIVED"}
            or self.pending_question is not None
            or self.current_source_fingerprint is not None
            or self.fresh_evaluation_at is not None
        ):
            raise ValueError("A closed receipt cannot claim a current financial evaluation")
        if self.effective_state == "CLOSED" and self.current_revision.state != "CLOSED":
            raise ValueError("Closed response requires the original closed receipt")
        return self


class QuestionCommandLookupResponse(BoundaryModel):
    protocol: Literal["full-question-command-lookup-v1"] = "full-question-command-lookup-v1"
    simulation: Literal[True] = True
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    epoch_id: UUID
    idempotency_key: CommandKey
    session_id: UUID | None
    original_command: dict[str, Any] | None
    request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    original_receipt: QuestionRevision | None
    current_revision: QuestionRevision | None
    original_start_request: QuestionStartRequest | None
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False
    replacement_allowed: Literal[False] = False
    client_match_required: Literal[True] = True

    @model_validator(mode="after")
    def exact_original(self) -> "QuestionCommandLookupResponse":
        values = (
            self.session_id,
            self.original_command,
            self.request_hash,
            self.original_receipt,
            self.current_revision,
        )
        if self.status == "NOT_FOUND_NOT_FINAL":
            if (
                any(value is not None for value in values)
                or self.original_start_request is not None
            ):
                raise ValueError("An absent command cannot invent an original receipt")
            return self
        if any(value is None for value in values):
            raise ValueError("A recorded lookup requires its exact original command and receipt")
        assert self.original_command is not None and self.original_receipt is not None
        assert self.current_revision is not None
        command, receipt = self.original_command, self.original_receipt
        if (
            receipt.session_id != self.session_id
            or receipt.epoch_id != self.epoch_id
            or receipt.command_key != self.idempotency_key
            or receipt.command_hash != self.request_hash
            or configuration_hash(command) != self.request_hash
            or self.current_revision.session_id != self.session_id
            or self.current_revision.revision < receipt.revision
            or command.get("user_id") != str(receipt.user_id)
        ):
            raise ValueError("Original command hash, owner, epoch or receipt differs")
        if command.get("kind") == "START":
            if (
                receipt.command_kind != "START"
                or set(command) != {"kind", "user_id", "request"}
                or self.original_start_request is None
                or self.original_start_request.model_dump(mode="json") != command.get("request")
            ):
                raise ValueError("Start lookup must preserve the exact original request")
        elif (
            command.get("kind") not in {"ANSWER", "REFRESH", "CLOSE"}
            or self.original_start_request is not None
            or set(command) != {"kind", "user_id", "session_id", "request"}
            or command.get("session_id") != str(self.session_id)
        ):
            raise ValueError("Non-start command must belong to this exact session")
        return self


@dataclass
class FreshObservation:
    planning: FinitePlanningResponse
    capture: DecisionCapture
    basis: dict[str, Any]
    fingerprint: str


def source_fingerprint(
    basis: dict[str, Any],
    sources: list[TraceEvidence],
    policies: list[TracePolicy],
) -> str:
    """Ignore only recomputation-clock fields, retaining original source windows and amounts."""
    normalized = copy.deepcopy(basis)
    raw = normalized["autonomy_basis"]
    raw.pop("as_of", None)
    snapshot = raw["snapshot"]
    snapshot.pop("as_of", None)
    # Living amount remains original output; its recalculation-clock digest is not
    # a freshness identity. Every consulted original evidence and policy stays bound.
    for row in snapshot.get("living_reserves", []):
        row.pop("estimation_input_digest", None)
    for name in ("exposure", "income"):
        if isinstance(raw.get(name), dict):
            raw[name].pop("as_of", None)
    return configuration_hash(
        {
            "basis": normalized,
            "sources": [
                row.model_dump(mode="json") for row in sorted(sources, key=lambda row: row.id)
            ],
            "policies": [
                row.model_dump(mode="json") for row in sorted(policies, key=lambda row: row.id)
            ],
        }
    )


def _fresh(
    session: Session, user: UUID, body: FinitePlanningRequest, now: datetime
) -> FreshObservation:
    _snapshot(session)
    capture = start_capture(session)
    planning = analyze_finite_planning(session, user, body, now)
    capture_evidence(
        session,
        user,
        [
            row.evidence_id
            for row in planning.declarations
            if row.evidence_id is not None and row.status == "VERIFIED_DECLARATION"
        ],
    )
    actual = session.get(ActionPlan, body.base_action_id)
    if actual is None or actual.user_id != user or "autonomy_basis" not in capture.inputs:
        raise PolicyLifecycleError("CURRENT_QUESTION_BASIS_MISSING", "当前真实规划原件不完整", 409)
    raw = capture.inputs["autonomy_basis"]
    basis = {
        "autonomy_basis": raw,
        "epoch_id": str(planning.audit.epoch_id),
        "local_date": now.astimezone(ZoneInfo(raw["snapshot"]["timezone"])).date().isoformat(),
        "original_action_status": actual.status,
        "original_action_request_hash": actual.request_hash,
        "base_authority": planning.base_decision.model_dump(
            mode="json", exclude={"effect_hash", "economic_signature", "candidate_signatures"}
        ),
    }
    return FreshObservation(
        planning,
        capture,
        basis,
        source_fingerprint(basis, list(capture.sources.values()), list(capture.policies.values())),
    )


def _fresh_reader(
    engine: Engine, user: UUID, body: FinitePlanningRequest, now: datetime
) -> FreshObservation:
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        with audit_read_scope(session):
            return _fresh(session, user, body, now)


def _integrity(detail: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("QUESTION_WORKFLOW_INTEGRITY_ERROR", detail, 409)


def _all_records(
    session: Session, user: UUID, now: datetime
) -> list[tuple[DecisionTrace, QuestionRevision]]:
    _snapshot(session)
    rows = list(
        session.scalars(
            select(DecisionRun)
            .where(
                DecisionRun.user_id == user,
                DecisionRun.input_snapshot["decision_trace"]["inputs"]["question_workflow"].astext
                == PROTOCOL,
            )
            .order_by(DecisionRun.as_of, DecisionRun.id)
            .limit(MAX_USER_WORKFLOW_RUNS + 1)
        )
    )
    if len(rows) > MAX_USER_WORKFLOW_RUNS:
        raise PolicyLifecycleError(
            "QUESTION_WORKFLOW_CAPACITY_EXCEEDED", "持久问答原件超过明确读取容量", 409
        )
    if not rows:
        return []
    epoch = current_audit_epoch(session, user)
    if epoch is None or verify_audit_chain(session, user, epoch.id).status != "VALID":
        raise _integrity("原工作流的完整 typed 审计未验真")
    # One complete verifier call above checks every original subject, source,
    # constraint and action link. Reuse only within this clean RO/RR invocation.
    anchors: dict[UUID, int] = {}
    for identity in session.scalars(
        select(AuditEvent.decision_run_id).where(
            AuditEvent.user_id == user,
            AuditEvent.epoch_id == epoch.id,
            AuditEvent.event_type == "DECISION_RECORDED",
            AuditEvent.decision_run_id.in_([row.id for row in rows]),
        )
    ):
        if identity is not None:
            anchors[identity] = anchors.get(identity, 0) + 1
    result = []
    for row in rows:
        if now < row.as_of:
            raise PolicyLifecycleError("DECISION_NOT_YET_OCCURRED", "原问题尚未发生", 409)
        completeness, trace = _stored_trace(session, row)
        if completeness != "COMPLETE" or trace is None or anchors.get(row.id) != 1:
            raise _integrity("问答原轨迹或 typed 审计未验真")
        try:
            state = QuestionRevision.model_validate_json(
                json.dumps(trace.outcome["question_revision"])
            )
            if (
                state.user_id != user
                or state.run_id != row.id
                or state.as_of != trace.as_of
                or trace.action_id is not None
                or state.epoch_id != epoch.id
                or configuration_hash(trace.inputs["original_command"]) != state.command_hash
            ):
                raise ValueError("Original workflow bindings differ")
            if state.command_kind == "CLOSE":
                if (
                    trace.algorithm_versions
                    != {"trace": "decision-trace-v1", "question": CLOSE_ALGORITHM}
                    or trace.sources
                    or trace.policies
                    or trace.constraints
                    or set(trace.inputs) != {"question_workflow", "session_id", "original_command"}
                    or set(trace.outcome) != {"question_revision", "last_planning_reference"}
                ):
                    raise ValueError("Close receipt cannot claim fresh financial evaluation")
            else:
                full = FinitePlanningResponse.model_validate_json(
                    json.dumps(trace.outcome["full_fresh_evaluation"])
                )
                if (
                    state.base_action_id != full.base_action_id
                    or state.epoch_id != full.audit.epoch_id
                    or (
                        state.command_kind == "START"
                        and trace.parent_run_id != full.original_run_id
                    )
                    or state.evaluation
                    != restrict_fresh_worlds(full.result, state.variables, state.answers)
                    or state.source_fingerprint
                    != source_fingerprint(
                        trace.inputs["observed_basis"], trace.sources, trace.policies
                    )
                ):
                    raise ValueError("Fresh original worlds or source bindings differ")
        except (ValueError, TypeError, KeyError) as error:
            raise _integrity("问答原状态、全候选或来源绑定不一致") from error
        result.append((trace, state))
    return result


def _records_reader(
    engine: Engine, user: UUID, now: datetime
) -> list[tuple[DecisionTrace, QuestionRevision]]:
    with Session(engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        with audit_read_scope(session):
            return _all_records(session, user, now)


def _chain(
    records: list[tuple[DecisionTrace, QuestionRevision]], identity: UUID
) -> list[tuple[DecisionTrace, QuestionRevision]]:
    chain = sorted(
        [row for row in records if row[1].session_id == identity], key=lambda row: row[1].revision
    )
    if not chain:
        raise PolicyLifecycleError("NOT_FOUND", "问答会话不存在", 404)
    first = chain[0][1]
    keys: set[str] = set()
    for index, (trace, state) in enumerate(chain):
        previous = chain[index - 1][1] if index else None
        if (
            state.revision != index + 1
            or state.epoch_id != first.epoch_id
            or state.base_action_id != first.base_action_id
            or state.variables != first.variables
            or state.previous_run_id != (previous.run_id if previous else None)
            or (previous is not None and trace.parent_run_id != previous.run_id)
            or (index == 0 and state.command_kind != "START")
            or state.run_id != uuid5(identity, f"revision:{state.revision}")
            or state.command_key in keys
            or (previous is not None and state.as_of < previous.as_of)
        ):
            raise _integrity("问答 revision 父链、原候选或周期不一致")
        keys.add(state.command_key)
        try:
            original = trace.inputs["original_command"]
            if original["user_id"] != str(state.user_id):
                raise ValueError("Command owner differs")
            if index == 0:
                body = QuestionStartRequest.model_validate_json(json.dumps(original["request"]))
                if (
                    set(original) != {"kind", "user_id", "request"}
                    or original["kind"] != "START"
                    or body.variables != state.variables
                    or body.base_action_id != state.base_action_id
                    or state.answers
                    or state.answer_applied
                    or body.expected_epoch_id != state.epoch_id
                    or body.idempotency_key != state.command_key
                    or state.session_id
                    != uuid5(NAMESPACE, f"{state.user_id}:{state.epoch_id}:{state.command_key}")
                ):
                    raise ValueError("Start original differs")
            else:
                assert previous is not None
                if previous.state == "CLOSED":
                    raise ValueError("A closed session cannot append another command")
                if set(original) != {"kind", "user_id", "session_id", "request"} or original[
                    "session_id"
                ] != str(identity):
                    raise ValueError("Command scope differs")
                if original["kind"] == "CLOSE":
                    close = QuestionCloseRequest.model_validate_json(
                        json.dumps(original["request"])
                    )
                    if (
                        close.expected_revision != previous.revision
                        or close.expected_epoch_id != state.epoch_id
                        or close.idempotency_key != state.command_key
                        or state
                        != closed_revision(
                            previous,
                            as_of=state.as_of,
                            command_key=state.command_key,
                            command_hash=state.command_hash,
                        )
                        or trace.outcome.get("last_planning_reference")
                        != last_planning_reference(previous)
                    ):
                        raise ValueError("Close receipt must preserve its exact previous planning")
                    continue
                if original["kind"] == "ANSWER":
                    answer = QuestionAnswerRequest.model_validate_json(
                        json.dumps(original["request"])
                    )
                    selected = exact_answer(
                        previous, answer.expected_revision, answer.question_id, answer.choice_key
                    )
                    expected, key = answer.expected_epoch_id, answer.idempotency_key
                elif original["kind"] == "REFRESH":
                    refresh = QuestionRefreshRequest.model_validate_json(
                        json.dumps(original["request"])
                    )
                    if refresh.expected_revision != previous.revision:
                        raise ValueError("Refresh revision differs")
                    selected = previous.answers
                    expected, key = refresh.expected_epoch_id, refresh.idempotency_key
                else:
                    raise ValueError("Unknown original command")
                if expected != state.epoch_id or key != state.command_key:
                    raise ValueError("Command exact key or epoch differs")
                changed = state.source_fingerprint != previous.source_fingerprint
                if changed:
                    if state.command_kind != "REBASE" or state.answers or state.answer_applied:
                        raise ValueError("Changed original sources cannot carry answers")
                elif (
                    state.command_kind != original["kind"]
                    or state.answers != selected
                    or state.answer_applied != (original["kind"] == "ANSWER")
                ):
                    raise ValueError("Answer or refresh differs from original pending choice")
        except (KeyError, ValueError, TypeError, AssertionError) as error:
            raise _integrity("问答 revision 原命令或精确答案不一致") from error
    return chain


def _heads(records: list[tuple[DecisionTrace, QuestionRevision]]) -> list[QuestionRevision]:
    return [
        _chain(records, identity)[-1][1]
        for identity in sorted({state.session_id for _, state in records})
    ]


def _reserve_close_capacity(records: list[tuple[DecisionTrace, QuestionRevision]]) -> None:
    if len(records) >= MAX_USER_WORKFLOW_RUNS - 1:
        raise PolicyLifecycleError(
            "QUESTION_WORKFLOW_CAPACITY_EXCEEDED", "保留最后一个原件槽供显式关闭，不截断原父链", 409
        )


def _response(
    receipt: QuestionRevision,
    latest: QuestionRevision,
    fresh: FreshObservation | None,
    *,
    replay: bool,
    archived: bool = False,
) -> QuestionWorkflowResponse:
    effective: Literal[
        "PENDING_ANSWER",
        "READY_FOR_REVIEW",
        "ALL_WORLDS_BLOCKED",
        "UNKNOWN",
        "STALE_RECOMPUTATION_REQUIRED",
        "ARCHIVED",
        "CLOSED",
    ] = latest.state
    if archived:
        effective = "ARCHIVED"
    elif latest.state == "CLOSED":
        effective = "CLOSED"
        fresh = None
    elif fresh is None:
        effective = "UNKNOWN"
    elif fresh.fingerprint != latest.source_fingerprint:
        effective = "STALE_RECOMPUTATION_REQUIRED"
    elif fresh.planning.result.status in {"UNKNOWN", "CAPACITY_EXCEEDED"}:
        effective = "UNKNOWN"
    return QuestionWorkflowResponse(
        original_receipt=receipt,
        current_revision=latest,
        effective_state=effective,
        pending_question=latest.pending_question if effective == "PENDING_ANSWER" else None,
        replayed_original_receipt=replay,
        current_source_fingerprint=fresh.fingerprint if fresh else None,
        fresh_evaluation_at=fresh.planning.as_of if fresh else None,
    )


def _epoch(session: Session, user: UUID, expected: UUID) -> None:
    actual = current_audit_epoch(session, user)
    if actual is None or actual.id != expected or actual.status != "OPEN":
        raise PolicyLifecycleError("STALE_QUESTION_EPOCH", "原问答周期已变化，不能接受旧答案", 409)


def _body(state: QuestionRevision) -> FinitePlanningRequest:
    return FinitePlanningRequest(base_action_id=state.base_action_id, variables=state.variables)


def _append(
    session: Session, state: QuestionRevision, fresh: FreshObservation, original: dict[str, Any]
) -> None:
    trace = build_trace(
        run_id=state.run_id,
        user_id=state.user_id,
        phase="EVALUATION",
        as_of=state.as_of,
        parent_run_id=state.previous_run_id or fresh.planning.original_run_id,
        action_id=None,
        algorithm_versions={
            "trace": "decision-trace-v1",
            "question": PROTOCOL,
            "finite": fresh.planning.result.algorithm_version,
        },
        inputs={
            "question_workflow": PROTOCOL,
            "session_id": str(state.session_id),
            "original_command": original,
            "observed_basis": fresh.basis,
        },
        sources=list(fresh.capture.sources.values()),
        policies=list(fresh.capture.policies.values()),
        outcome={
            "question_revision": state.model_dump(mode="json"),
            "full_fresh_evaluation": fresh.planning.model_dump(mode="json"),
        },
    )
    record_trace(session, trace)


def start_question_session(
    engine: Engine, user: UUID, body: QuestionStartRequest, now: datetime
) -> QuestionWorkflowResponse:
    now = _now(now)
    identity = uuid5(NAMESPACE, f"{user}:{body.expected_epoch_id}:{body.idempotency_key}")
    original = {"kind": "START", "user_id": str(user), "request": body.model_dump(mode="json")}
    digest = configuration_hash(original)
    with audit_command_guard(engine, user), Session(engine) as session, session.begin():
        _user(session, user)
        _epoch(session, user, body.expected_epoch_id)
        records = _records_reader(engine, user, now)
        existing = [
            state
            for _, state in records
            if state.session_id == identity and state.command_kind == "START"
        ]
        if existing:
            if len(existing) != 1 or existing[0].command_hash != digest:
                raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "原会话键不能换请求", 409)
            latest = _chain(records, identity)[-1][1]
            fresh = (
                None
                if latest.state == "CLOSED"
                else _fresh_reader(engine, user, _body(latest), now)
            )
            return _response(existing[0], latest, fresh, replay=True)
        if any(
            state.state == "PENDING_ANSWER" and state.epoch_id == body.expected_epoch_id
            for state in _heads(records)
        ):
            raise PolicyLifecycleError(
                "ACTIVE_QUESTION_SESSION_EXISTS", "该用户已有待答会话，请恢复原问题", 409
            )
        _reserve_close_capacity(records)
        fresh = _fresh_reader(
            engine,
            user,
            FinitePlanningRequest(base_action_id=body.base_action_id, variables=body.variables),
            now,
        )
        state = make_revision(
            user_id=user,
            session_id=identity,
            epoch_id=body.expected_epoch_id,
            revision=1,
            run_id=uuid5(identity, "revision:1"),
            previous_run_id=None,
            as_of=now,
            base_action_id=body.base_action_id,
            variables=body.variables,
            answers={},
            full=fresh.planning.result,
            command_kind="START",
            command_key=body.idempotency_key,
            command_hash=digest,
            source_fingerprint=fresh.fingerprint,
            answer_applied=False,
        )
        _append(session, state, fresh, original)
        return _response(state, state, fresh, replay=False)


def answer_question_session(
    engine: Engine,
    user: UUID,
    identity: UUID,
    body: QuestionAnswerRequest | QuestionRefreshRequest,
    now: datetime,
) -> QuestionWorkflowResponse:
    now = _now(now)
    requested_kind = "ANSWER" if isinstance(body, QuestionAnswerRequest) else "REFRESH"
    original = {
        "kind": requested_kind,
        "user_id": str(user),
        "session_id": str(identity),
        "request": body.model_dump(mode="json"),
    }
    digest = configuration_hash(original)
    with audit_command_guard(engine, user), Session(engine) as session, session.begin():
        _user(session, user)
        _epoch(session, user, body.expected_epoch_id)
        records = _records_reader(engine, user, now)
        chain = _chain(records, identity)
        latest = chain[-1][1]
        if latest.epoch_id != body.expected_epoch_id:
            raise PolicyLifecycleError("STALE_QUESTION_EPOCH", "旧会话不能接受新周期答案", 409)
        repeated = [state for _, state in chain if state.command_key == body.idempotency_key]
        if repeated:
            if len(repeated) != 1 or repeated[0].command_hash != digest:
                raise PolicyLifecycleError(
                    "IDEMPOTENCY_CONFLICT", "同一问答命令键不能换答案或操作", 409
                )
            fresh = (
                None
                if latest.state == "CLOSED"
                else _fresh_reader(engine, user, _body(latest), now)
            )
            return _response(repeated[0], latest, fresh, replay=True)
        if latest.state == "CLOSED":
            raise PolicyLifecycleError(
                "QUESTION_SESSION_CLOSED", "原会话已明确关闭，不能继续回答或刷新", 409
            )
        _reserve_close_capacity(records)
        if body.expected_revision != latest.revision:
            raise PolicyLifecycleError("STALE_QUESTION_REVISION", "请恢复当前原问题后再回答", 409)
        if latest.revision >= MAX_REVISIONS:
            raise PolicyLifecycleError(
                "QUESTION_REVISION_CAPACITY_EXCEEDED",
                "问答 revision 达到明确容量，请人工重新核对",
                409,
            )
        try:
            answers = (
                exact_answer(latest, body.expected_revision, body.question_id, body.choice_key)
                if isinstance(body, QuestionAnswerRequest)
                else dict(latest.answers)
            )
        except ValueError as error:
            raise PolicyLifecycleError(
                "INVALID_PENDING_ANSWER", "答案必须属于确切待答问题和原选项", 409
            ) from error
        fresh = _fresh_reader(engine, user, _body(latest), now)
        changed = fresh.fingerprint != latest.source_fingerprint
        if changed:
            answers = {}
        kind: Literal["ANSWER", "REBASE", "REFRESH"] = (
            "REBASE"
            if changed
            else "ANSWER"
            if isinstance(body, QuestionAnswerRequest)
            else "REFRESH"
        )
        revision = latest.revision + 1
        state = make_revision(
            user_id=user,
            session_id=identity,
            epoch_id=latest.epoch_id,
            revision=revision,
            run_id=uuid5(identity, f"revision:{revision}"),
            previous_run_id=latest.run_id,
            as_of=now,
            base_action_id=latest.base_action_id,
            variables=latest.variables,
            answers=answers,
            full=fresh.planning.result,
            command_kind=kind,
            command_key=body.idempotency_key,
            command_hash=digest,
            source_fingerprint=fresh.fingerprint,
            answer_applied=isinstance(body, QuestionAnswerRequest) and not changed,
        )
        if state.pending_question is not None and any(
            other.session_id != identity
            and other.epoch_id == latest.epoch_id
            and other.state == "PENDING_ANSWER"
            for other in _heads(records)
        ):
            raise PolicyLifecycleError(
                "ACTIVE_QUESTION_SESSION_EXISTS",
                "该用户已有另一待答会话，不能同时呈现第二个问题",
                409,
            )
        _append(session, state, fresh, original)
        return _response(state, state, fresh, replay=False)


def close_question_session(
    engine: Engine, user: UUID, identity: UUID, body: QuestionCloseRequest, now: datetime
) -> QuestionWorkflowResponse:
    now = _now(now)
    original = {
        "kind": "CLOSE",
        "user_id": str(user),
        "session_id": str(identity),
        "request": body.model_dump(mode="json"),
    }
    digest = configuration_hash(original)
    with audit_command_guard(engine, user), Session(engine) as session, session.begin():
        _user(session, user)
        _epoch(session, user, body.expected_epoch_id)
        records = _records_reader(engine, user, now)
        chain = _chain(records, identity)
        latest = chain[-1][1]
        if latest.epoch_id != body.expected_epoch_id:
            raise PolicyLifecycleError("STALE_QUESTION_EPOCH", "不能关闭另一周期的会话", 409)
        repeated = [state for _, state in chain if state.command_key == body.idempotency_key]
        if repeated:
            if len(repeated) != 1 or repeated[0].command_hash != digest:
                raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "原命令键不能换成关闭请求", 409)
            return _response(repeated[0], latest, None, replay=True)
        if latest.state == "CLOSED":
            raise PolicyLifecycleError("QUESTION_SESSION_CLOSED", "原会话已明确关闭", 409)
        if len(records) >= MAX_USER_WORKFLOW_RUNS:
            raise PolicyLifecycleError(
                "QUESTION_WORKFLOW_CAPACITY_EXCEEDED", "原件已达读取容量，不能追加不可重放回执", 409
            )
        if latest.revision != body.expected_revision:
            raise PolicyLifecycleError("STALE_QUESTION_REVISION", "关闭必须绑定确切原revision", 409)
        state = closed_revision(
            latest, as_of=now, command_key=body.idempotency_key, command_hash=digest
        )
        trace = build_trace(
            run_id=state.run_id,
            user_id=user,
            phase="EVALUATION",
            as_of=now,
            parent_run_id=latest.run_id,
            action_id=None,
            algorithm_versions={"trace": "decision-trace-v1", "question": CLOSE_ALGORITHM},
            inputs={
                "question_workflow": PROTOCOL,
                "session_id": str(identity),
                "original_command": original,
            },
            outcome={
                "question_revision": state.model_dump(mode="json"),
                "last_planning_reference": last_planning_reference(latest),
            },
        )
        record_trace(session, trace)
        return _response(state, state, None, replay=False)


def _lookup_response(
    records: list[tuple[DecisionTrace, QuestionRevision]],
    user: UUID,
    epoch: UUID,
    key: str,
    identity: UUID | None,
) -> QuestionCommandLookupResponse:
    # Validate every restored session chain before selecting the exact command.
    for existing in {state.session_id for _, state in records}:
        _chain(records, existing)
    matches = [
        (trace, state)
        for trace, state in records
        if state.user_id == user
        and state.epoch_id == epoch
        and state.command_key == key
        and (
            state.session_id == identity if identity is not None else state.command_kind == "START"
        )
    ]
    if len(matches) > 1:
        raise _integrity("同一原命令键匹配多个持久回执")
    if not matches:
        return QuestionCommandLookupResponse(
            status="NOT_FOUND_NOT_FINAL",
            epoch_id=epoch,
            idempotency_key=key,
            session_id=None,
            original_command=None,
            request_hash=None,
            original_receipt=None,
            current_revision=None,
            original_start_request=None,
        )
    trace, receipt = matches[0]
    command = copy.deepcopy(trace.inputs["original_command"])
    latest = _chain(records, receipt.session_id)[-1][1]
    return QuestionCommandLookupResponse(
        status="RECORDED",
        epoch_id=epoch,
        idempotency_key=key,
        session_id=receipt.session_id,
        original_command=command,
        request_hash=receipt.command_hash,
        original_receipt=receipt,
        current_revision=latest,
        original_start_request=QuestionStartRequest.model_validate_json(
            json.dumps(command["request"])
        )
        if command["kind"] == "START"
        else None,
    )


def read_question_start_command(
    session: Session, user: UUID, epoch: UUID, key: str, now: datetime
) -> QuestionCommandLookupResponse:
    _snapshot(session)
    _epoch(session, user, epoch)
    return _lookup_response(_all_records(session, user, _now(now)), user, epoch, key, None)


def read_question_command(
    session: Session, user: UUID, identity: UUID, key: str, now: datetime
) -> QuestionCommandLookupResponse:
    _snapshot(session)
    records = _all_records(session, user, _now(now))
    latest = _chain(records, identity)[-1][1]
    _epoch(session, user, latest.epoch_id)
    return _lookup_response(records, user, latest.epoch_id, key, identity)


def read_question_session(
    session: Session, user: UUID, identity: UUID, now: datetime
) -> QuestionWorkflowResponse:
    _snapshot(session)
    with audit_read_scope(session):
        return _read_question_session(session, user, identity, now)


def _read_question_session(
    session: Session, user: UUID, identity: UUID, now: datetime
) -> QuestionWorkflowResponse:
    now = _now(now)
    chain = _chain(_all_records(session, user, now), identity)
    latest = chain[-1][1]
    epoch = current_audit_epoch(session, user)
    if epoch is None or epoch.id != latest.epoch_id:
        return _response(latest, latest, None, replay=False, archived=True)
    if latest.state == "CLOSED":
        return _response(latest, latest, None, replay=False)
    try:
        fresh = _fresh(session, user, _body(latest), now)
    except PolicyLifecycleError:
        return _response(latest, latest, None, replay=False)
    return _response(latest, latest, fresh, replay=False)
