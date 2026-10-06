"""Private finite experimental mechanisms. No permission, bank, or SQL implementation.

The runtime must construct the complete input from retained current originals before
calling this module. Even a selected experimental proposal is not an executable grant.
Removed constraints affect this experiment only; common production guards remain.
"""

from collections import Counter
from datetime import datetime
from hashlib import sha256
from json import dumps
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

Money = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]
Arm = Literal["B0", "B1", "B2", "B3", "B4", "B5", "P"]
Ablation = Literal[
    "NONE",
    "EVIDENCE_LEVEL",
    "POLICY_VERSION",
    "DYNAMIC_LIVING_RESERVE",
    "MULTI_GOAL_CONSTRAINTS",
    "LIQUIDITY_FILTER",
    "MINIMUM_QUESTION",
    "SAFE_RECOVERY",
    "AUDIT_CHAIN",
]


class Model(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


def value_digest(value: object) -> str:
    return sha256(
        dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


class ConstraintFacts(Model):
    settled_cash_cents: Money
    active_reserved_cents: Money
    hard_obligation_cents: Money
    emergency_cents: Money
    dynamic_living_cents: Money
    registered_static_living_cents: Money
    other_goal_protection_cents: Money
    future_income_display_cents: Money = 0
    current_policy_version_id: UUID


class Candidate(Model):
    candidate_id: Identifier
    user_id: UUID
    epoch_id: UUID
    kind: Literal["PURCHASE_ASSET", "ALLOCATE_GOAL", "PAY_RECURRING", "REDEEM_ASSET"]
    amount_cents: Money
    cash_debit_cents: Money
    authority_cap_cents: Money
    policy_version_id: UUID
    source_goal_id: UUID | None = None
    destination_goal_id: UUID | None = None
    goal_allocation_permitted: StrictBool
    evidence_level: Literal["BANK_CONFIRMED", "USER_CONFIRMED", "USER_DECLARED", "INFERRED"]
    required_evidence_level: Literal["BANK_CONFIRMED", "USER_CONFIRMED"]
    available_at: datetime
    funds_use_at: datetime
    lock_until: datetime | None = None
    loss_cents: Money
    fee_cents: Money = 0
    simulated_annual_yield_basis_points: Annotated[StrictInt, Field(ge=0, le=1000000)]
    comparison_days: Annotated[StrictInt, Field(ge=1, le=365)]
    confirmation_required: StrictBool
    model_confidence_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)] | None
    model_confidence_original_sha256: Digest | None = None
    original_effect_hash: Digest | None = None
    original_refs: Annotated[list[Digest], Field(min_length=1)]

    @model_validator(mode="after")
    def clocks(self) -> Self:
        values = [self.available_at, self.funds_use_at]
        if self.lock_until is not None:
            values.append(self.lock_until)
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            raise ValueError("Candidate original clocks must be aware")
        if len(set(self.original_refs)) != len(self.original_refs):
            raise ValueError("Repeated candidate original reference")
        if (self.model_confidence_basis_points is None) != (
            self.model_confidence_original_sha256 is None
        ):
            raise ValueError("Model confidence requires its actual retained original")
        return self


class World(Model):
    world_id: Identifier
    complete_action_signature_sha256: Digest
    original_refs: Annotated[list[Digest], Field(min_length=1)]


class Question(Model):
    question_id: Identifier
    # Each actual response names its complete non-overlapping finite world subset.
    response_partitions: Annotated[list[list[Identifier]], Field(min_length=2, max_length=32)]
    original_refs: Annotated[list[Digest], Field(min_length=1)]


class Rule(Model):
    arm_id: Arm
    ablation: Ablation = "NONE"
    threshold_cents: Money = 0
    fixed_budget_cents: Money = 0
    fixed_candidate_id: Identifier | None = None
    manual_candidate_id: Identifier | None = None
    model_confidence_threshold_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)] = 9000

    @model_validator(mode="after")
    def applicable(self) -> Self:
        if self.ablation != "NONE" and self.arm_id != "P":
            raise ValueError("An ablation removes exactly one P mechanism")
        if self.arm_id == "B0" and self.manual_candidate_id is None:
            raise ValueError("B0 requires the original registered manual choice")
        if self.arm_id == "B2" and self.fixed_candidate_id is None:
            raise ValueError("B2 requires a registered fixed product/action choice")
        return self


class MechanismInput(Model):
    protocol: Literal["full-finite-mechanism-input-v1"] = "full-finite-mechanism-input-v1"
    user_id: UUID
    epoch_id: UUID
    opportunity_id: Identifier
    as_of: datetime
    source_status: Literal["CURRENT_COMPLETE", "UNKNOWN"]
    # These are retained source/calculation identities, not a proof of source authenticity.
    source_inventory_sha256: Digest
    source_refs: Annotated[list[Digest], Field(min_length=1)]
    facts: ConstraintFacts
    candidates: Annotated[list[Candidate], Field(max_length=256)]
    worlds: Annotated[list[World], Field(min_length=1, max_length=256)]
    questions: Annotated[list[Question], Field(max_length=32)]
    rule: Rule
    prior_experiment_audit_hash: Digest | None = None
    original_unknown_action_id: UUID | None = None
    original_unknown_key: Identifier | None = None

    @model_validator(mode="after")
    def complete_partition(self) -> Self:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("Current original clock must be aware")
        for rows, attribute in (
            (self.candidates, "candidate_id"),
            (self.worlds, "world_id"),
            (self.questions, "question_id"),
        ):
            if len({getattr(row, attribute) for row in rows}) != len(rows):
                raise ValueError("Repeated finite mechanism identity")
        if any(
            row.user_id != self.user_id or row.epoch_id != self.epoch_id for row in self.candidates
        ):
            raise ValueError("Candidate owner/epoch differs from the complete input")
        world_ids = {row.world_id for row in self.worlds}
        for question in self.questions:
            flattened = [identity for part in question.response_partitions for identity in part]
            if (
                any(not part for part in question.response_partitions)
                or len(flattened) != len(set(flattened))
                or set(flattened) != world_ids
            ):
                raise ValueError("Every question must partition all original worlds exactly once")
        if (self.original_unknown_action_id is None) != (self.original_unknown_key is None):
            raise ValueError("An unresolved operation requires both original identity and key")
        return self


class CandidateAssessment(Model):
    candidate_id: str
    considered: StrictBool
    rejection_reasons: list[str]
    ignored_constraints: list[str]


class MechanismDecision(Model):
    protocol: Literal["full-finite-mechanism-decision-v1"] = "full-finite-mechanism-decision-v1"
    status: Literal["PROPOSAL", "QUESTION", "NO_CANDIDATE", "UNKNOWN", "UNRESOLVED"]
    candidate_id: str | None
    proposal_amount_cents: StrictInt | None
    proposal_net_simulated_yield_cents: StrictInt | None
    original_effect_hash: str | None
    question_id: str | None
    question_worst_remaining_disagreement_pairs: StrictInt | None
    confirmation_directive: Literal["PER_ACTION", "ORIGINAL_REQUIREMENT", "NONE"]
    recovery_directive: Literal["NONE", "RECONCILE_SAME_OPERATION", "STOP_UNRESOLVED"]
    original_unknown_action_id: UUID | None
    original_unknown_key: str | None
    assessments: list[CandidateAssessment]
    input_sha256: str
    experiment_audit_record: dict[str, str | None] | None
    future_income_included_cents: Literal[0] = 0
    bank_authority: Literal[False] = False
    execution_status: Literal["NOT_RUN"] = "NOT_RUN"
    opportunity_denominator_retained: Literal[True] = True
    actual_safety_metrics: Literal[None] = None
    runtime_adapter_status: Literal["NOT_CONNECTED"] = "NOT_CONNECTED"


def _budget(value: MechanismInput) -> int:
    facts, rule = value.facts, value.rule
    cash = max(0, facts.settled_cash_cents - facts.active_reserved_cents)
    if rule.arm_id == "B1":
        return max(0, cash - rule.threshold_cents)
    if rule.arm_id == "B2":
        return max(0, cash - rule.fixed_budget_cents)
    living = (
        facts.registered_static_living_cents
        if rule.ablation == "DYNAMIC_LIVING_RESERVE"
        else facts.dynamic_living_cents
    )
    goals = 0 if rule.ablation == "MULTI_GOAL_CONSTRAINTS" else facts.other_goal_protection_cents
    return max(0, cash - facts.hard_obligation_cents - facts.emergency_cents - living - goals)


def _amount(value: MechanismInput, candidate: Candidate) -> int:
    if value.rule.arm_id in {"B1", "B2"}:
        return _budget(value)
    return candidate.amount_cents


def _net_yield(value: MechanismInput, candidate: Candidate) -> int:
    return (
        _amount(value, candidate)
        * candidate.simulated_annual_yield_basis_points
        * candidate.comparison_days
        // (10000 * 365)
        - candidate.fee_cents
        - candidate.loss_cents
    )


def _assess(value: MechanismInput, candidate: Candidate) -> CandidateAssessment:
    rule = value.rule
    rejected: list[str] = []
    ignored: list[str] = []
    # A structural current source/cash bound is never optional or model-generated authority.
    cash_debit = (
        _amount(value, candidate) if rule.arm_id in {"B1", "B2"} else candidate.cash_debit_cents
    )
    if cash_debit > max(0, value.facts.settled_cash_cents - value.facts.active_reserved_cents):
        rejected.append("ACTUAL_CASH_AND_EXISTING_RESERVATIONS")
    checks = {
        "AUTHORITY_CAP": _amount(value, candidate) <= candidate.authority_cap_cents,
        "EVIDENCE_LEVEL": (
            candidate.evidence_level == candidate.required_evidence_level
            or (
                candidate.required_evidence_level == "USER_CONFIRMED"
                and candidate.evidence_level == "BANK_CONFIRMED"
            )
        ),
        "POLICY_VERSION": candidate.policy_version_id == value.facts.current_policy_version_id,
        "MULTI_GOAL_CONSTRAINTS": (
            candidate.goal_allocation_permitted
            and (
                candidate.source_goal_id is None
                or candidate.source_goal_id == candidate.destination_goal_id
            )
        ),
        "LIQUIDITY_FILTER": (
            candidate.available_at <= candidate.funds_use_at
            and (candidate.lock_until is None or candidate.lock_until <= candidate.funds_use_at)
        ),
        "PROTECTED_BUDGET": cash_debit <= _budget(value),
    }
    for name, satisfied in checks.items():
        skip = rule.arm_id == "B4" or name == rule.ablation
        skip |= rule.arm_id in {"B1", "B2"} and name != "PROTECTED_BUDGET"
        skip |= rule.arm_id == "B5" and name == "LIQUIDITY_FILTER"
        if skip:
            ignored.append(name)
        elif not satisfied:
            rejected.append(name)
    if rule.arm_id in {"B0", "B2"}:
        identity = rule.manual_candidate_id if rule.arm_id == "B0" else rule.fixed_candidate_id
        if candidate.candidate_id != identity:
            rejected.append("REGISTERED_CHOICE")
    if rule.arm_id in {"B1", "B2"}:
        if (
            candidate.kind != "PURCHASE_ASSET"
            or candidate.source_goal_id is not None
            or candidate.destination_goal_id is not None
        ):
            rejected.append("SKIP_BY_REGISTERED_GENERAL_PURCHASE_MECHANISM")
        if _amount(value, candidate) == 0:
            rejected.append("EMPTY_REGISTERED_RULE_AMOUNT")
    if rule.arm_id == "B4" and (
        candidate.model_confidence_basis_points is None
        or candidate.model_confidence_basis_points < rule.model_confidence_threshold_basis_points
    ):
        rejected.append("MISSING_OR_LOW_ORIGINAL_MODEL_CONFIDENCE")
    if rule.ablation == "SAFE_RECOVERY" and candidate.kind == "REDEEM_ASSET":
        rejected.append("RECOVERY_MECHANISM_REMOVED")
    return CandidateAssessment(
        candidate_id=candidate.candidate_id,
        considered=not rejected,
        rejection_reasons=rejected,
        ignored_constraints=ignored,
    )


def _disagreements(signatures: list[str]) -> int:
    counts = Counter(signatures)
    return len(signatures) * (len(signatures) - 1) // 2 - sum(
        count * (count - 1) // 2 for count in counts.values()
    )


def _question(value: MechanismInput) -> tuple[Question | None, int | None]:
    signatures = {world.world_id: world.complete_action_signature_sha256 for world in value.worlds}
    if len(set(signatures.values())) <= 1:
        return None, None
    scored = [
        (
            question,
            [
                _disagreements([signatures[i] for i in part])
                for part in question.response_partitions
            ],
        )
        for question in value.questions
    ]
    if not scored:
        return None, None
    if value.rule.ablation == "MINIMUM_QUESTION":
        chosen, residuals = min(scored, key=lambda row: row[0].question_id)
    else:
        chosen, residuals = min(
            scored, key=lambda row: (max(row[1]), sum(row[1]), row[0].question_id)
        )
    return chosen, max(residuals)


def decide_full_mechanism(value: MechanismInput) -> MechanismDecision:
    """Select a finite experimental proposal; never use this result as execution authority."""
    input_hash = value_digest(value.model_dump(mode="json"))
    assessments = [_assess(value, candidate) for candidate in value.candidates]
    status: Literal["PROPOSAL", "QUESTION", "NO_CANDIDATE", "UNKNOWN", "UNRESOLVED"]
    candidate: Candidate | None = None
    question: Question | None = None
    residual: int | None = None
    recovery: Literal["NONE", "RECONCILE_SAME_OPERATION", "STOP_UNRESOLVED"] = "NONE"
    if value.source_status != "CURRENT_COMPLETE":
        status = "UNKNOWN"
    elif value.original_unknown_action_id is not None:
        status = "UNRESOLVED"
        recovery = (
            "STOP_UNRESOLVED"
            if value.rule.ablation == "SAFE_RECOVERY"
            else "RECONCILE_SAME_OPERATION"
        )
    else:
        if value.rule.arm_id in {"P", "B3"}:
            question, residual = _question(value)
            unstable = len({world.complete_action_signature_sha256 for world in value.worlds}) > 1
        else:
            unstable = False
        candidates = [
            row
            for row, check in zip(value.candidates, assessments, strict=True)
            if check.considered
        ]
        if question is not None:
            status = "QUESTION"
        elif unstable:
            status = "UNKNOWN"
        elif not candidates:
            status = "NO_CANDIDATE"
        else:
            if value.rule.arm_id == "B4":
                candidate = min(
                    candidates,
                    key=lambda row: (-(row.model_confidence_basis_points or 0), row.candidate_id),
                )
            else:
                candidate = min(
                    candidates,
                    key=lambda row: (-_net_yield(value, row), row.available_at, row.candidate_id),
                )
            status = "PROPOSAL"
    per_action = candidate is not None and (
        value.rule.arm_id in {"B0", "B3"}
        or candidate.confirmation_required
        or candidate.loss_cents > 0
    )
    output = MechanismDecision(
        status=status,
        candidate_id=candidate.candidate_id if candidate else None,
        proposal_amount_cents=_amount(value, candidate) if candidate else None,
        proposal_net_simulated_yield_cents=_net_yield(value, candidate) if candidate else None,
        original_effect_hash=(
            candidate.original_effect_hash
            if candidate is not None and _amount(value, candidate) == candidate.amount_cents
            else None
        ),
        question_id=question.question_id if question else None,
        question_worst_remaining_disagreement_pairs=residual,
        confirmation_directive="PER_ACTION"
        if per_action
        else ("ORIGINAL_REQUIREMENT" if candidate else "NONE"),
        recovery_directive=recovery,
        original_unknown_action_id=value.original_unknown_action_id,
        original_unknown_key=value.original_unknown_key,
        assessments=assessments,
        input_sha256=input_hash,
        experiment_audit_record=None,
    )
    if value.rule.ablation == "AUDIT_CHAIN":
        return output
    record = {
        "protocol": "full-experiment-decision-chain-v1",
        "input_sha256": input_hash,
        "previous_hash": value.prior_experiment_audit_hash,
        "decision_sha256": value_digest(output.model_dump(mode="json")),
    }
    return output.model_copy(
        update={"experiment_audit_record": {**record, "record_sha256": value_digest(record)}}
    )
