"""Read-only FULL five-set envelope using the original MVP assessment pipeline."""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from app.db.models import ActionPlan, BankOperation, EvidenceItem
from app.domain.autonomy_envelope import (
    SET_NAMES,
    EnvelopeEvaluation,
    EnvelopeSet,
    evaluate_envelope,
)
from app.domain.autonomy_types import AutonomyFacts
from app.domain.boundary_types import BoundaryModel, SourceIssue
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import BankCommand, ExecutionEffect
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionIntent, IntentModel, PrepareActionRequest
from app.services.autonomy import _basis, _clock, _facts
from app.services.dashboard_helpers import current_epoch_audit
from app.services.dashboard_types import DashboardAuditCard
from app.services.full_policy_lifecycle import FULL_TEMPLATES, read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session


class FullTemplateIntent(IntentModel):
    kind: Literal["full_template"]
    template_name: Literal[
        "DatedExpensePolicy",
        "PeriodicTransferPolicy",
        "AssetAuthorizationPolicy",
        "RecoveryPolicy",
        "GoalAllocationPolicy",
        "CrossGoalReallocationPolicy",
        "SeasonalReservePolicy",
        "InterventionPolicy",
    ]
    policy_id: UUID


class EnvelopeRequest(IntentModel):
    intent: Annotated[ActionIntent | FullTemplateIntent, Field(discriminator="kind")]


class EnvelopeSource(BoundaryModel):
    evidence_id: UUID
    content_hash: str
    source_type: str
    source_ref: str
    evidence_level: str
    status: str
    observed_at: datetime


class EnvelopeResponse(BoundaryModel):
    simulation: Literal[True] = True
    evaluation_only: Literal[True] = True
    authority_granted: Literal[False] = False
    user_id: UUID
    as_of: datetime
    action_id: UUID | None
    action_type: str
    effect: ExecutionEffect | None
    amount_cents: int | None
    assessment: EnvelopeEvaluation
    sources: list[EnvelopeSource]
    audit: DashboardAuditCard


def _snapshot(session: Session) -> None:
    if (
        session.new
        or session.dirty
        or session.deleted
        or session.connection().get_isolation_level() != "REPEATABLE READ"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError("INVALID_READ_SNAPSHOT", "自主包络需要干净的RR只读事务", 409)


def _sources(session: Session, facts: AutonomyFacts) -> tuple[list[EnvelopeSource], AutonomyFacts]:
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == facts.user_id,
                EvidenceItem.id.in_(facts.source_evidence_ids),
            )
            .order_by(EvidenceItem.id)
        )
    )
    issues = list(facts.source_issues)
    if {row.id for row in rows} != set(facts.source_evidence_ids):
        issues.append(SourceIssue(code="ORIGINAL_SOURCE_ROW_MISSING", entity_type="evidence"))
    for row in rows:
        if (
            configuration_hash(row.content) != row.content_hash
            or row.status != "VALID"
            or not row.observed_at <= facts.as_of
            or not row.valid_from <= facts.as_of
            or (row.valid_to is not None and facts.as_of >= row.valid_to)
        ):
            issues.append(
                SourceIssue(
                    code="ORIGINAL_SOURCE_ROW_NOT_CURRENT",
                    entity_type="evidence",
                    entity_id=str(row.id),
                )
            )
    return (
        [
            EnvelopeSource(
                evidence_id=row.id,
                content_hash=row.content_hash,
                source_type=row.source_type,
                source_ref=row.source_ref,
                evidence_level=row.evidence_level,
                status=row.status,
                observed_at=row.observed_at,
            )
            for row in rows
        ],
        facts.model_copy(update={"source_issues": issues}),
    )


def _response(session: Session, facts: AutonomyFacts, action_id: UUID | None) -> EnvelopeResponse:
    sources, facts = _sources(session, facts)
    audit = current_epoch_audit(session, facts.user_id, [])
    result = evaluate_envelope(facts, audit_verified=audit.complete and audit.status == "VALID")
    return EnvelopeResponse(
        user_id=facts.user_id,
        as_of=facts.as_of,
        action_id=action_id,
        action_type=facts.action_type,
        effect=facts.effect,
        amount_cents=facts.effect.amount_cents if facts.effect else None,
        assessment=result,
        sources=sources,
        audit=audit,
    )


def assess_envelope_intent(
    session: Session, user_id: UUID, intent: ActionIntent | FullTemplateIntent, now: datetime
) -> EnvelopeResponse:
    _snapshot(session)
    now = _clock(now)
    with session.no_autoflush:
        if isinstance(intent, FullTemplateIntent):
            declaration = read_full_policy(session, user_id, intent.policy_id, now)
            if (
                declaration.template_name != intent.template_name
                or intent.template_name not in FULL_TEMPLATES
            ):
                raise PolicyLifecycleError("FULL_TEMPLATE_MISMATCH", "模板与原声明不匹配", 409)
            audit = current_epoch_audit(session, user_id, [])
            context_hash = configuration_hash(declaration.model_dump(mode="json"))
            sets = [
                EnvelopeSet(
                    name=name,
                    membership="OUT" if name == "SupportedActionSet" else "UNKNOWN",
                    reasons=["FULL_TEMPLATE_EXECUTION_NOT_IMPLEMENTED"]
                    if name == "SupportedActionSet"
                    else ["PLANNING_CONFIRMATION_IS_NOT_ACTION_AUTHORITY"],
                    evidence_ids=declaration.current_version.evidence_ids,
                    policy_version_ids=[declaration.current_version.version_id],
                    source_context_hash=context_hash,
                    effect_hash=None,
                )
                for name in SET_NAMES
            ]
            return EnvelopeResponse(
                user_id=user_id,
                as_of=now,
                action_id=None,
                action_type=intent.template_name,
                effect=None,
                amount_cents=None,
                sources=[],
                audit=audit,
                assessment=EnvelopeEvaluation(
                    sets=sets,
                    intersection="OUT",
                    execution_eligible=False,
                    automatic_execution_allowed=False,
                    original_decision=None,
                    facts_hash=context_hash,
                ),
            )
        basis = _basis(session, user_id, now)
        facts = _facts(session, user_id, intent, now, basis)
        return _response(session, facts, None)


def assess_envelope_action(
    session: Session, user_id: UUID, action_id: UUID, now: datetime
) -> EnvelopeResponse:
    """Read original bytes/identity exactly as assess_action; evaluate original facts.

    No accepted action is reclassified and no new ActionPlan is prepared here.
    This small loader reuses the original _facts, planner and financial revalidator.
    """
    _snapshot(session)
    now = _clock(now)
    with session.no_autoflush:
        row = session.get(ActionPlan, action_id)
        if row is None or row.user_id != user_id or "execution" not in row.request:
            raise PolicyLifecycleError("NOT_FOUND", "执行动作不存在", 404)
        operation = session.scalar(
            select(BankOperation).where(BankOperation.action_plan_id == action_id)
        )
        if operation is not None or row.status in {
            "SUBMITTED",
            "UNKNOWN",
            "SUCCEEDED",
            "RECONCILED",
        }:
            raise PolicyLifecycleError(
                "NO_RECLASSIFICATION_AFTER_ACCEPTANCE", "已提交动作须读取原银行结果", 409
            )
        try:
            command = BankCommand.model_validate_json(json.dumps(row.request["execution"]))
            if (
                configuration_hash(row.request) != row.request_hash
                or command.effect_hash != execution_effect_hash(command.effect)
                or command.effect.operation_id != action_id
                or command.effect.user_id != user_id
            ):
                raise ValueError("Original action identity or hash mismatch")
            intent = PrepareActionRequest.model_validate_json(
                json.dumps({"idempotency_key": "assessment", "intent": row.request["intent"]})
            ).intent
        except (TypeError, ValueError, KeyError) as error:
            raise PolicyLifecycleError(
                "INVALID_EXECUTION_SOURCE", "原动作载荷已变化或不完整", 409
            ) from error
        basis = _basis(session, user_id, now)
        facts = _facts(
            session, user_id, intent, now, basis, effect=command.effect, action_id=action_id
        )
        if row.status not in {"PLANNED", "AUTHORIZED"}:
            facts = facts.model_copy(
                update={"hard_block_reasons": ["OLD_ACTION_REQUIRES_NEW_PREPARATION"]}
            )
        return _response(session, facts, action_id)
