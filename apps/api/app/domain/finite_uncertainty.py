"""Finite planning preferences, real engine outcomes, and one-step nonprobability minimax."""

import json
from collections.abc import Callable
from itertools import product
from math import prod
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.autonomy import economic_signature
from app.domain.autonomy_types import AutonomyDecision
from app.domain.boundary_types import BoundaryModel
from app.domain.execution_types import ExecutionEffect, ExecutionValidation
from app.domain.policy_configuration import MoneyCents, configuration_hash
from app.services.action_contracts import ActionIntent, PrepareActionRequest, TransferIntent
from pydantic import Field, model_validator

MAX_WORLDS = 128
Key = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]
VariableField = Literal[
    "ACTION_INTENT", "TRANSFER_AMOUNT", "TRANSFER_SOURCE", "TRANSFER_DESTINATION"
]


class MoneyChoice(BoundaryModel):
    kind: Literal["money"]
    amount_cents: Annotated[MoneyCents, Field(gt=0)]


class AccountChoice(BoundaryModel):
    kind: Literal["account"]
    account_id: UUID


class IntentChoice(BoundaryModel):
    kind: Literal["intent"]
    intent: ActionIntent


class FiniteChoice(BoundaryModel):
    key: Key
    value: Annotated[MoneyChoice | AccountChoice | IntentChoice, Field(discriminator="kind")]


class FinitePlanningVariable(BoundaryModel):
    variable_id: Key
    field: VariableField
    choices: Annotated[list[FiniteChoice], Field(min_length=2, max_length=8)]
    completeness: Literal["COMPLETE", "INCOMPLETE"] = "COMPLETE"
    source: Literal["USER_REQUEST", "REGISTERED_EVIDENCE"] = "USER_REQUEST"
    evidence_id: UUID | None = None
    impact_scope: Literal["ORIGINAL_MVP_PLANNING_REQUEST"] = "ORIGINAL_MVP_PLANNING_REQUEST"

    @model_validator(mode="after")
    def finite_user_domain(self) -> Self:
        if len({choice.key for choice in self.choices}) != len(self.choices):
            raise ValueError("Candidate keys must be unique")
        if len(
            {configuration_hash(choice.value.model_dump(mode="json")) for choice in self.choices}
        ) != len(self.choices):
            raise ValueError("Candidate values must be distinct")
        expected = (
            "intent"
            if self.field == "ACTION_INTENT"
            else "money"
            if self.field == "TRANSFER_AMOUNT"
            else "account"
        )
        if any(choice.value.kind != expected for choice in self.choices):
            raise ValueError("Only a typed small planning preference can vary")
        if (self.source == "REGISTERED_EVIDENCE") != (self.evidence_id is not None):
            raise ValueError("Registered declaration requires its exact original evidence ID")
        return self


class PlanningEngineOutcome(BoundaryModel):
    status: Literal["KNOWN", "UNKNOWN", "UNSUPPORTED"]
    decision: AutonomyDecision | None = None
    effect: ExecutionEffect | None = None
    validation: ExecutionValidation | None = None
    signature: str | None = None
    source_context_hash: str | None = None
    source_evidence_ids: list[UUID] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_requires_actual_shape(self) -> Self:
        if self.status == "KNOWN" and (
            self.decision is None
            or self.effect is None
            or self.validation is None
            or self.signature is None
            or self.source_context_hash is None
            or not self.source_evidence_ids
        ):
            raise ValueError(
                "Known world requires a full original engine result and source binding"
            )
        return self


class PlanningWorld(BoundaryModel):
    world_key: str
    assignments: dict[str, str]
    intent: ActionIntent | None
    outcome: PlanningEngineOutcome
    planning_only: Literal[True] = True
    execution_eligible: Literal[False] = False


class AnswerPartition(BoundaryModel):
    choice_key: str
    world_keys: list[str]
    signatures: list[str]
    residual_signature_count: int


class MinimaxCandidate(BoundaryModel):
    variable_id: str
    field: VariableField
    worst_residual_signature_count: int
    partitions: list[AnswerPartition]


class FinitePlanningResult(BoundaryModel):
    algorithm_version: Literal["full-finite-planning-minimax-v1"] = (
        "full-finite-planning-minimax-v1"
    )
    planning_only: Literal[True] = True
    authority_granted: Literal[False] = False
    execution_eligible: Literal[False] = False
    probability_model: Literal["NONE"] = "NONE"
    answer_state_machine: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    restart_deduplication: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    status: Literal["STABLE", "DIVERGENT", "ALL_WORLDS_BLOCKED", "UNKNOWN", "CAPACITY_EXCEEDED"]
    complete_within_declared_domain: bool
    expected_world_count: int
    evaluated_world_count: int
    known_world_count: int
    unknown_or_unsupported_world_count: int
    stable: bool | None
    should_ask: bool | None
    worlds: list[PlanningWorld]
    distinct_signatures: list[str]
    question: MinimaxCandidate | None
    minimax_candidates: list[MinimaxCandidate]
    full_confirmation_baseline_question_count: int
    affected_action_types: list[str]
    explanation: str
    reasons: list[str]


def _world_intent(
    base: ActionIntent, variables: list[FinitePlanningVariable], choices: tuple[FiniteChoice, ...]
) -> ActionIntent | None:
    selected = base
    for variable, choice in zip(variables, choices, strict=True):
        if variable.field == "ACTION_INTENT":
            assert isinstance(choice.value, IntentChoice)
            selected = choice.value.intent
    raw = selected.model_dump(mode="json")
    for variable, choice in zip(variables, choices, strict=True):
        if variable.field == "ACTION_INTENT":
            continue
        if not isinstance(selected, TransferIntent):
            return None
        if variable.field == "TRANSFER_AMOUNT":
            assert isinstance(choice.value, MoneyChoice)
            raw["amount_cents"] = choice.value.amount_cents
        else:
            assert isinstance(choice.value, AccountChoice)
            raw[
                "source_account_id"
                if variable.field == "TRANSFER_SOURCE"
                else "destination_account_id"
            ] = str(choice.value.account_id)
    return PrepareActionRequest.model_validate_json(
        json.dumps({"idempotency_key": "planning-only", "intent": raw})
    ).intent


def unknown_planning(
    variables: list[FinitePlanningVariable], reasons: list[str], *, capacity: bool = False
) -> FinitePlanningResult:
    expected = prod(len(variable.choices) for variable in variables)
    return FinitePlanningResult(
        status="CAPACITY_EXCEEDED" if capacity else "UNKNOWN",
        complete_within_declared_domain=False,
        expected_world_count=expected,
        evaluated_world_count=0,
        known_world_count=0,
        unknown_or_unsupported_world_count=expected,
        stable=None,
        should_ask=None,
        worlds=[],
        distinct_signatures=[],
        question=None,
        minimax_candidates=[],
        full_confirmation_baseline_question_count=len(variables),
        affected_action_types=[],
        reasons=reasons,
        explanation="原事实/候选域尚未完整核验，未生成稳定动作或问询最优结论。",
    )


def complete_planning_signature(
    effect: ExecutionEffect,
    decision: AutonomyDecision,
    validation: ExecutionValidation,
    authority_status: str,
) -> str:
    """Reuse original full economic signature, retaining original rejection/risk class."""
    return configuration_hash(
        {
            "economic_signature": economic_signature(effect),
            "action_type": effect.action_type,
            "level": decision.level,
            "financial_evaluation": decision.financial_evaluation,
            "authority_status": authority_status,
            "confirmation_required": decision.confirmation_required,
            "validation_status": validation.status,
            "validation_reasons": sorted(validation.reasons),
            "projected_risk": validation.projected_boundary.status
            if validation.projected_boundary
            else None,
        }
    )


def evaluate_finite_planning(
    base: ActionIntent,
    variables: list[FinitePlanningVariable],
    engine: Callable[[ActionIntent], PlanningEngineOutcome],
) -> FinitePlanningResult:
    """Exhaustive closed-domain engine calls; no probabilities, pruning or permission edits."""
    variables = [
        FinitePlanningVariable.model_validate_json(row.model_dump_json()) for row in variables
    ]
    if (
        not 1 <= len(variables) <= 3
        or len({row.variable_id for row in variables}) != len(variables)
        or len({row.field for row in variables}) != len(variables)
    ):
        raise ValueError("1 to 3 unique variables and unique affected fields required")
    variables.sort(key=lambda row: row.variable_id)
    if any(row.completeness != "COMPLETE" for row in variables):
        return unknown_planning(variables, ["DECLARED_CANDIDATE_DOMAIN_INCOMPLETE"])
    if prod(len(row.choices) for row in variables) > MAX_WORLDS:
        return unknown_planning(variables, ["DECLARED_WORLD_CAPACITY_EXCEEDED"], capacity=True)
    worlds = []
    for selected in product(
        *(sorted(row.choices, key=lambda choice: choice.key) for row in variables)
    ):
        assignments = {
            row.variable_id: choice.key for row, choice in zip(variables, selected, strict=True)
        }
        world_key = configuration_hash(assignments)
        intent = _world_intent(base, variables, selected)
        outcome = (
            engine(intent)
            if intent is not None
            else PlanningEngineOutcome(
                status="UNSUPPORTED", reasons=["VARIABLE_NOT_IMPLEMENTED_FOR_SELECTED_ACTION"]
            )
        )
        worlds.append(
            PlanningWorld(
                world_key=world_key, assignments=assignments, intent=intent, outcome=outcome
            )
        )
    known = [
        row for row in worlds if row.outcome.status == "KNOWN" and row.outcome.signature is not None
    ]
    unknown = len(worlds) - len(known)
    signatures = sorted(
        {row.outcome.signature for row in known if row.outcome.signature is not None}
    )
    candidates = []
    if not unknown:
        for variable in variables:
            partitions = []
            for choice in sorted(variable.choices, key=lambda value: value.key):
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
    types: list[str] = sorted(
        {row.outcome.effect.action_type for row in worlds if row.outcome.effect is not None}
    )
    explanation = (
        "全部已核候选产生同一完整金融动作后果，无需因本不确定域询问；原确切确认要求仍保留。"
        if status == "STABLE"
        else "所有候选均被原服务拒绝，回答这些变量尚不能产生可行动结果。"
        if all_blocked
        else "存在未知/未支持世界，不能断言动作稳定或给出完整 minimax 最优问题。"
        if unknown
        else (
            f"询问 {question.variable_id if question else ''} 后最多剩余 "
            f"{question.worst_residual_signature_count if question else 0} 种完整动作后果，"
            "一步 minimax 最小；同分按变量 ID。"
        )
    )
    explanation += (
        "每个候选的类别、金额、归属、费损及到账后果见原引擎 world.outcome.effect；"
        "均为规划，不代表已执行。"
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
        full_confirmation_baseline_question_count=len(variables),
        affected_action_types=types,
        explanation=explanation,
        reasons=["WORLD_OUTPUT_NOT_COMPLETE"] if unknown else [],
    )
