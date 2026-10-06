"""One pending planning question, with exact answers and complete finite denominators."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from app.domain.boundary_types import BoundaryModel
from app.domain.finite_uncertainty import (
    AnswerPartition,
    FiniteChoice,
    FinitePlanningResult,
    FinitePlanningVariable,
    MinimaxCandidate,
    unknown_planning,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictBool, StrictInt, model_validator

PROTOCOL: Literal["full-one-question-v1"] = "full-one-question-v1"
MAX_REVISIONS = 16
CLOSE_ALGORITHM = "full-one-question-close-v1"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommandKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]


class PendingPlanningQuestion(BoundaryModel):
    question_id: UUID
    variable_id: str
    choices: list[FiniteChoice]
    worst_residual_signature_count: Annotated[StrictInt, Field(ge=1)]
    affected_action_types: list[str]
    bank_authority: Literal[False] = False


class QuestionRevision(BoundaryModel):
    protocol: Literal["full-one-question-v1"] = PROTOCOL
    user_id: UUID
    session_id: UUID
    epoch_id: UUID
    revision: Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS + 1)]
    run_id: UUID
    previous_run_id: UUID | None
    as_of: datetime
    base_action_id: UUID
    variables: list[FinitePlanningVariable]
    answers: dict[str, str]
    evaluation: FinitePlanningResult
    pending_question: PendingPlanningQuestion | None
    state: Literal["PENDING_ANSWER", "READY_FOR_REVIEW", "ALL_WORLDS_BLOCKED", "UNKNOWN", "CLOSED"]
    command_kind: Literal["START", "ANSWER", "REBASE", "REFRESH", "CLOSE"]
    command_key: CommandKey
    command_hash: Hash
    source_fingerprint: Hash
    answer_applied: StrictBool
    old_candidates_execution_eligible: Literal[False] = False
    inherited_confirmation: Literal[False] = False
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False

    @model_validator(mode="after")
    def one_pending_question(self) -> Self:
        if (self.state == "CLOSED") != (self.command_kind == "CLOSE") or (
            self.state != "CLOSED" and self.revision > MAX_REVISIONS
        ):
            raise ValueError("Only a closed receipt can use the final reserved revision")
        if self.state == "CLOSED" and self.answer_applied:
            raise ValueError("Closing cannot apply a preference answer")
        if (self.state == "PENDING_ANSWER") != (self.pending_question is not None):
            raise ValueError("Only the pending state has exactly one question")
        if len({row.variable_id for row in self.variables}) != len(self.variables):
            raise ValueError("Original variable IDs must be unique")
        if self.pending_question is not None:
            question = self.pending_question
            candidates = {row.variable_id: row for row in self.variables}
            if (
                question.question_id != uuid5(self.run_id, "question:" + question.variable_id)
                or question.variable_id in self.answers
                or question.variable_id not in candidates
                or question.choices
                != sorted(candidates[question.variable_id].choices, key=lambda row: row.key)
                or self.evaluation.question is None
                or self.evaluation.question.variable_id != question.variable_id
                or self.evaluation.question.worst_residual_signature_count
                != question.worst_residual_signature_count
            ):
                raise ValueError("Pending question must be the exact current minimax choice")
        return self


def restrict_fresh_worlds(
    full: FinitePlanningResult,
    variables: list[FinitePlanningVariable],
    answers: dict[str, str],
) -> FinitePlanningResult:
    """Restrict only newly computed worlds; preference answers grant no confirmation."""
    variables = [
        FinitePlanningVariable.model_validate_json(row.model_dump_json()) for row in variables
    ]
    available = {row.variable_id: {choice.key for choice in row.choices} for row in variables}
    if len(available) != len(variables) or any(
        key not in available or choice not in available[key] for key, choice in answers.items()
    ):
        raise ValueError("Answer must select an exact declared choice")
    if not full.worlds:
        return unknown_planning(
            variables,
            full.reasons or ["CURRENT_COMPLETE_WORLDS_NOT_PROVEN"],
            capacity=full.status == "CAPACITY_EXCEEDED",
        )
    expected_full = 1
    for row in variables:
        expected_full *= len(row.choices)
    assignments = {configuration_hash(row.assignments) for row in full.worlds}
    if (
        len(full.worlds) != expected_full
        or len(assignments) != expected_full
        or any(
            row.world_key != configuration_hash(row.assignments)
            or set(row.assignments) != set(available)
            or any(value not in available[key] for key, value in row.assignments.items())
            for row in full.worlds
        )
    ):
        raise ValueError("Fresh evaluation must retain the complete declared world inventory")
    worlds = [
        row
        for row in full.worlds
        if all(row.assignments[key] == choice for key, choice in answers.items())
    ]
    known = [
        row for row in worlds if row.outcome.status == "KNOWN" and row.outcome.signature is not None
    ]
    unknown = len(worlds) - len(known)
    signatures = sorted(
        {row.outcome.signature for row in known if row.outcome.signature is not None}
    )
    candidates: list[MinimaxCandidate] = []
    if not unknown:
        for variable in sorted(variables, key=lambda row: row.variable_id):
            if variable.variable_id in answers:
                continue
            partitions = []
            for choice in sorted(variable.choices, key=lambda row: row.key):
                members = [
                    row for row in worlds if row.assignments[variable.variable_id] == choice.key
                ]
                distinct = sorted(
                    {row.outcome.signature for row in members if row.outcome.signature is not None}
                )
                partitions.append(
                    AnswerPartition(
                        choice_key=choice.key,
                        world_keys=[row.world_key for row in members],
                        signatures=distinct,
                        residual_signature_count=len(distinct),
                    )
                )
            candidates.append(
                MinimaxCandidate(
                    variable_id=variable.variable_id,
                    field=variable.field,
                    worst_residual_signature_count=max(
                        row.residual_signature_count for row in partitions
                    ),
                    partitions=partitions,
                )
            )
    all_blocked = not unknown and all(
        row.outcome.decision is not None and row.outcome.decision.level == "BLOCKED"
        for row in worlds
    )
    status: Literal["STABLE", "DIVERGENT", "ALL_WORLDS_BLOCKED", "UNKNOWN", "CAPACITY_EXCEEDED"] = (
        "UNKNOWN"
        if unknown
        else "ALL_WORLDS_BLOCKED"
        if all_blocked
        else "STABLE"
        if len(signatures) == 1
        else "DIVERGENT"
    )
    question = (
        min(candidates, key=lambda row: (row.worst_residual_signature_count, row.variable_id))
        if status == "DIVERGENT"
        else None
    )
    return FinitePlanningResult(
        status=status,
        complete_within_declared_domain=not unknown,
        expected_world_count=len(worlds),
        evaluated_world_count=len(worlds),
        known_world_count=len(known),
        unknown_or_unsupported_world_count=unknown,
        stable=None if unknown else len(signatures) == 1,
        should_ask=None if unknown else status == "DIVERGENT",
        worlds=worlds,
        distinct_signatures=signatures,
        question=question,
        minimax_candidates=candidates,
        full_confirmation_baseline_question_count=len(variables) - len(answers),
        affected_action_types=sorted(
            {row.outcome.effect.action_type for row in worlds if row.outcome.effect is not None}
        ),
        explanation="本轮使用当前真实事实重算的完整世界，再按原用户偏好答案限制候选；回答不构成行动确认。",
        reasons=["CURRENT_REMAINING_WORLD_UNKNOWN"] if unknown else [],
    )


def make_revision(
    *,
    user_id: UUID,
    session_id: UUID,
    epoch_id: UUID,
    revision: int,
    run_id: UUID,
    previous_run_id: UUID | None,
    as_of: datetime,
    base_action_id: UUID,
    variables: list[FinitePlanningVariable],
    answers: dict[str, str],
    full: FinitePlanningResult,
    command_kind: Literal["START", "ANSWER", "REBASE", "REFRESH"],
    command_key: str,
    command_hash: str,
    source_fingerprint: str,
    answer_applied: bool,
) -> QuestionRevision:
    result = restrict_fresh_worlds(full, variables, answers)
    question = result.question
    variable = next(
        (
            row
            for row in variables
            if question is not None and row.variable_id == question.variable_id
        ),
        None,
    )
    pending = (
        PendingPlanningQuestion(
            question_id=uuid5(run_id, "question:" + question.variable_id),
            variable_id=question.variable_id,
            choices=sorted(variable.choices, key=lambda row: row.key),
            worst_residual_signature_count=question.worst_residual_signature_count,
            affected_action_types=result.affected_action_types,
        )
        if question is not None and variable is not None
        else None
    )
    state: Literal["PENDING_ANSWER", "READY_FOR_REVIEW", "ALL_WORLDS_BLOCKED", "UNKNOWN"] = (
        "UNKNOWN"
        if result.status in {"UNKNOWN", "CAPACITY_EXCEEDED"}
        else "ALL_WORLDS_BLOCKED"
        if result.status == "ALL_WORLDS_BLOCKED"
        else "PENDING_ANSWER"
        if pending is not None
        else "READY_FOR_REVIEW"
    )
    return QuestionRevision(
        user_id=user_id,
        session_id=session_id,
        epoch_id=epoch_id,
        revision=revision,
        run_id=run_id,
        previous_run_id=previous_run_id,
        as_of=as_of,
        base_action_id=base_action_id,
        variables=variables,
        answers=answers,
        evaluation=result,
        pending_question=pending,
        state=state,
        command_kind=command_kind,
        command_key=command_key,
        command_hash=command_hash,
        source_fingerprint=source_fingerprint,
        answer_applied=answer_applied,
    )


def exact_answer(
    state: QuestionRevision, expected_revision: int, question_id: UUID, choice_key: str
) -> dict[str, str]:
    if (
        expected_revision != state.revision
        or state.pending_question is None
        or state.pending_question.question_id != question_id
    ):
        raise ValueError("Answer is not for the exact pending revision and question")
    if choice_key not in {row.key for row in state.pending_question.choices}:
        raise ValueError("Answer must be an exact original pending choice")
    return {**state.answers, state.pending_question.variable_id: choice_key}


def closed_revision(
    previous: QuestionRevision, *, as_of: datetime, command_key: str, command_hash: str
) -> QuestionRevision:
    """Append a close receipt; previous planning remains historical and grants nothing."""
    if previous.state == "CLOSED":
        raise ValueError("A closed session cannot be opened or closed again")
    return QuestionRevision(
        **{
            **previous.model_dump(),
            "revision": previous.revision + 1,
            "run_id": uuid5(previous.session_id, f"revision:{previous.revision + 1}"),
            "previous_run_id": previous.run_id,
            "as_of": as_of,
            "state": "CLOSED",
            "pending_question": None,
            "command_kind": "CLOSE",
            "command_key": command_key,
            "command_hash": command_hash,
            "answer_applied": False,
        }
    )


def last_planning_reference(previous: QuestionRevision) -> dict[str, str]:
    return {
        "run_id": str(previous.run_id),
        "evaluation_hash": configuration_hash(previous.evaluation.model_dump(mode="json")),
        "source_fingerprint": previous.source_fingerprint,
    }
