"""Read-only finite preferences bound to an actual current action and financial sources."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.db.models import ActionPlan, BankOperation, EvidenceItem
from app.domain.autonomy import classify_autonomy
from app.domain.autonomy_types import AutonomyDecision
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand
from app.domain.finite_uncertainty import (
    FinitePlanningResult,
    FinitePlanningVariable,
    PlanningEngineOutcome,
    complete_planning_signature,
    evaluate_finite_planning,
    unknown_planning,
)
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionIntent, IntentModel, PrepareActionRequest
from app.services.autonomy import _basis, _clock, _facts
from app.services.autonomy_envelope import EnvelopeSource, _snapshot, _sources
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.decision_trace import get_decision_trace
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session


class FinitePlanningRequest(IntentModel):
    base_action_id: UUID
    variables: Annotated[list[FinitePlanningVariable], Field(min_length=1, max_length=3)]

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

    @model_validator(mode="after")
    def unique_variable_bindings(self) -> Self:
        if len({row.variable_id for row in self.variables}) != len(self.variables) or len(
            {row.field for row in self.variables}
        ) != len(self.variables):
            raise ValueError("Variable IDs and affected fields must be unique")
        return self


class VariableProvenance(BoundaryModel):
    variable_id: str
    source: Literal["USER_REQUEST", "REGISTERED_EVIDENCE"]
    status: Literal["READ_ONLY_USER_REQUEST", "VERIFIED_DECLARATION", "MISSING_OR_INVALID"]
    source_ref: str
    evidence_id: UUID | None
    original_content_hash: str | None
    original_evidence_level: str | None
    bank_fact: Literal[False] = False
    authorization: Literal[False] = False
    reasons: list[str]


class FinitePlanningResponse(BoundaryModel):
    simulation: Literal[True] = True
    planning_only: Literal[True] = True
    user_id: UUID
    as_of: datetime
    base_action_id: UUID
    original_run_id: UUID
    original_trace_hash: str
    request_hash: str
    current_source_context_hash: str
    base_decision: AutonomyDecision
    audit: DashboardAuditCard
    sources: list[EnvelopeSource]
    declarations: list[VariableProvenance]
    result: FinitePlanningResult


def _original_action(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> tuple[ActionPlan, BankCommand, ActionIntent, str]:
    row = session.get(ActionPlan, action_id)
    if row is None or row.user_id != user_id or "execution" not in row.request:
        raise PolicyLifecycleError("NOT_FOUND", "原准备行动不存在", 404)
    if (
        row.status not in {"PLANNED", "AUTHORIZED"}
        or session.scalar(select(BankOperation.id).where(BankOperation.action_plan_id == action_id))
        is not None
    ):
        raise PolicyLifecycleError(
            "NO_PLANNING_AFTER_ACCEPTANCE", "已提交/未知/终态行动必须读取原结果", 409
        )
    try:
        command = BankCommand.model_validate_json(json.dumps(row.request["execution"]))
        if (
            configuration_hash(row.request) != row.request_hash
            or command.effect.operation_id != action_id
            or command.effect.user_id != user_id
            or execution_effect_hash(command.effect) != command.effect_hash
        ):
            raise ValueError("Original action bytes or identity changed")
        intent = PrepareActionRequest.model_validate_json(
            json.dumps({"idempotency_key": "planning-only", "intent": row.request["intent"]})
        ).intent
    except (ValueError, TypeError, KeyError) as error:
        raise PolicyLifecycleError(
            "INVALID_EXECUTION_SOURCE", "原动作请求/哈希无效", 409
        ) from error
    original = get_decision_trace(session, user_id, row.decision_run_id, now)
    if (
        original.completeness != "COMPLETE"
        or original.trace is None
        or original.audit_chain_status != "VALID"
        or original.trace.action_id != action_id
    ):
        raise PolicyLifecycleError(
            "ORIGINAL_DECISION_NOT_VERIFIED", "原行动需要完整原决策与审计", 409
        )
    return row, command, intent, original.trace.trace_hash


def _declarations(
    session: Session,
    user_id: UUID,
    variables: list[FinitePlanningVariable],
    now: datetime,
    request_hash: str,
) -> list[VariableProvenance]:
    refs = []
    for variable in variables:
        if variable.source == "USER_REQUEST":
            refs.append(
                VariableProvenance(
                    variable_id=variable.variable_id,
                    source=variable.source,
                    status="READ_ONLY_USER_REQUEST",
                    source_ref=f"request:{request_hash}#{variable.variable_id}",
                    evidence_id=None,
                    original_content_hash=None,
                    original_evidence_level=None,
                    reasons=[],
                )
            )
            continue
        row = session.get(EvidenceItem, variable.evidence_id)
        reasons = []
        if row is None or row.user_id != user_id:
            reasons.append("ORIGINAL_DECLARATION_NOT_FOUND")
        elif (
            row.evidence_level not in {"USER_DECLARED", "MODEL_INFERRED"}
            or row.source_type != "FULL_FINITE_USER_VARIABLE"
            or row.status != "VALID"
            or configuration_hash(row.content) != row.content_hash
            or not row.observed_at <= now
            or not row.valid_from <= now
            or (row.valid_to is not None and now >= row.valid_to)
            or row.content.get("simulation") is not True
            or row.content.get("user_id") != str(user_id)
            or row.content.get("protocol") != "full-finite-user-variable-v1"
            or not isinstance(row.content.get("variable"), dict)
            or configuration_hash(row.content["variable"])
            != configuration_hash(
                variable.model_dump(mode="json", exclude={"source", "evidence_id"})
            )
        ):
            reasons.append("ORIGINAL_DECLARATION_CONTENT_OR_WINDOW_INVALID")
        owned = row is not None and row.user_id == user_id
        refs.append(
            VariableProvenance(
                variable_id=variable.variable_id,
                source=variable.source,
                status="MISSING_OR_INVALID" if reasons else "VERIFIED_DECLARATION",
                source_ref=row.source_ref if owned and row is not None else "MISSING",
                evidence_id=variable.evidence_id,
                original_content_hash=row.content_hash if owned and row is not None else None,
                original_evidence_level=row.evidence_level if owned and row is not None else None,
                reasons=reasons,
            )
        )
    return refs


def analyze_finite_planning(
    session: Session, user_id: UUID, body: FinitePlanningRequest, now: datetime
) -> FinitePlanningResponse:
    _snapshot(session)
    now = _clock(now)
    with session.no_autoflush:
        row, command, base_intent, trace_hash = _original_action(
            session, user_id, body.base_action_id, now
        )
        basis = _basis(session, user_id, now)
        base_facts = _facts(
            session, user_id, base_intent, now, basis, effect=command.effect, action_id=row.id
        )
        base_sources, base_facts = _sources(session, base_facts)
        base_decision = classify_autonomy(base_facts)
        audit = current_epoch_audit(session, user_id, [])
        request_hash = configuration_hash(
            {
                "user_id": str(user_id),
                "as_of": now.isoformat(),
                "request": body.model_dump(mode="json"),
            }
        )
        declarations = _declarations(session, user_id, body.variables, now, request_hash)
        source_map = {item.evidence_id: item for item in base_sources}
        invalid = [reason for ref in declarations for reason in ref.reasons]
        if not audit.complete or audit.status != "VALID":
            invalid.append("CURRENT_TYPED_AUDIT_NOT_VERIFIED")
        if (
            base_facts.source_issues
            or not base_facts.source_evidence_ids
            or base_facts.validation is None
            or base_facts.validation.status == "INSUFFICIENT_EVIDENCE"
            or base_facts.authority.status in {"MISSING_EVIDENCE", "STALE_VERSION"}
            or base_facts.hard_block_reasons
        ):
            invalid.append("ACTUAL_BASE_CONTEXT_NOT_VERIFIED")

        def engine(intent: ActionIntent) -> PlanningEngineOutcome:
            try:
                # Candidates never inherit command.effect, original action ID or exact consent.
                facts = _facts(session, user_id, intent, now, basis)
                sources, facts = _sources(session, facts)
                source_map.update({item.evidence_id: item for item in sources})
                decision = classify_autonomy(facts)
            except PolicyLifecycleError as error:
                return PlanningEngineOutcome(status="UNKNOWN", reasons=[error.code])
            if (
                facts.source_issues
                or not facts.source_evidence_ids
                or not facts.authority.evidence_ids
                or facts.authority.status in {"MISSING_EVIDENCE", "STALE_VERSION"}
                or facts.effect is None
                or facts.validation is None
                or facts.validation.status == "INSUFFICIENT_EVIDENCE"
                or facts.source_context_hash != basis.digest
            ):
                return PlanningEngineOutcome(
                    status="UNKNOWN",
                    decision=decision,
                    effect=facts.effect,
                    validation=facts.validation,
                    source_context_hash=facts.source_context_hash,
                    source_evidence_ids=facts.source_evidence_ids,
                    reasons=["ACTUAL_WORLD_CONTEXT_INCOMPLETE"],
                )
            signature = complete_planning_signature(
                facts.effect, decision, facts.validation, facts.authority.status
            )
            return PlanningEngineOutcome(
                status="KNOWN",
                decision=decision,
                effect=facts.effect,
                validation=facts.validation,
                signature=signature,
                source_context_hash=facts.source_context_hash,
                source_evidence_ids=facts.source_evidence_ids,
                reasons=decision.reasons,
            )

        result = (
            unknown_planning(body.variables, sorted(set(invalid)))
            if invalid
            else evaluate_finite_planning(base_intent, body.variables, engine)
        )
        return FinitePlanningResponse(
            user_id=user_id,
            as_of=now,
            base_action_id=row.id,
            original_run_id=row.decision_run_id,
            original_trace_hash=trace_hash,
            request_hash=request_hash,
            current_source_context_hash=basis.digest,
            base_decision=base_decision,
            audit=audit,
            sources=[source_map[key] for key in sorted(source_map)],
            declarations=declarations,
            result=result,
        )
