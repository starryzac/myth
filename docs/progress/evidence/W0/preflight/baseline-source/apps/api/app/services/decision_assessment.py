"""An explicit save command; advisory assessments and all reads remain read-only."""

import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid5

from app.db.audit_guard import transaction_gate
from app.db.models import DecisionRun, User
from app.domain.autonomy import ALGORITHM_VERSION
from app.domain.decision_trace import build_trace
from app.domain.execution_types import ExecutionValidation
from app.domain.policy_configuration import MoneyCents, configuration_hash
from app.services.action_contracts import PrepareActionRequest
from app.services.autonomy import assess_intent, assess_transfer_preferences
from app.services.decision_recording import (
    boundary_constraints,
    start_capture,
    validation_constraints,
)
from app.services.decision_trace import DecisionTraceResponse, get_decision_trace, record_trace
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class SaveAssessmentRequest(PrepareActionRequest):
    amount_options_cents: Annotated[list[MoneyCents], Field(min_length=2, max_length=8)] | None = (
        None
    )


def save_assessment(
    engine: Engine, user_id: UUID, request: SaveAssessmentRequest, now: datetime
) -> DecisionTraceResponse:
    request = SaveAssessmentRequest.model_validate_json(request.model_dump_json())
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时钟必须带时区")
    now = now.astimezone(UTC)
    key = "saved-assessment:" + configuration_hash({"key": request.idempotency_key})
    original = {
        "intent": request.intent.model_dump(mode="json"),
        "amount_options_cents": request.amount_options_cents,
    }
    with Session(engine) as session, session.begin():
        transaction_gate(session, user_id)
        user = session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        existing = session.scalar(
            select(DecisionRun).where(
                DecisionRun.user_id == user_id,
                DecisionRun.idempotency_key == key,
            )
        )
        if existing is not None:
            if any(existing.input_snapshot.get(k) != v for k, v in original.items()):
                raise PolicyLifecycleError(
                    "IDEMPOTENCY_CONFLICT", "同一记录键不能改变原始评估", 409
                )
            return get_decision_trace(session, user_id, existing.id, now)
        capture = start_capture(session)
        if request.amount_options_cents is None:
            assessed = assess_intent(session, user_id, request.intent, now)
        else:
            if request.intent.kind != "transfer_internal":
                raise PolicyLifecycleError(
                    "INVALID_USER_OPTIONS", "金额偏好仅支持同一对本人账户", 422
                )
            assessed = assess_transfer_preferences(
                session,
                user_id,
                request.intent.source_account_id,
                request.intent.destination_account_id,
                request.amount_options_cents,
                now,
            )
        from app.domain.boundary_types import BoundaryResult

        constraints = boundary_constraints(
            "baseline_boundary",
            BoundaryResult.model_validate_json(json.dumps(capture.inputs["autonomy_baseline"])),
        )
        facts = capture.inputs["autonomy_facts"]
        if facts.get("validation") is not None:
            constraints = validation_constraints(
                ExecutionValidation.model_validate_json(json.dumps(facts["validation"]))
            )
        run = DecisionRun(
            id=uuid5(user_id, key),
            user_id=user_id,
            created_at=now,
            idempotency_key=key,
            trigger_type="SAVED_ASSESSMENT",
            algorithm_version=ALGORITHM_VERSION,
            as_of=now,
            input_snapshot=original,
            snapshot_hash=configuration_hash(original),
            policy_version_ids=[],
            evidence_ids=[],
            result={},
            status="SUCCEEDED",
            completed_at=now,
        )
        session.add(run)
        session.flush()
        trace = build_trace(
            run_id=run.id,
            user_id=user_id,
            action_id=None,
            parent_run_id=None,
            phase="EVALUATION",
            as_of=now,
            algorithm_versions={**capture.algorithms, "autonomy": ALGORITHM_VERSION},
            inputs={**original, **capture.inputs},
            sources=list(capture.sources.values()),
            policies=list(capture.policies.values()),
            constraints=constraints,
            candidates=capture.candidates,
            outcome={
                "decision": assessed.decision.model_dump(mode="json"),
                "effect": assessed.effect.model_dump(mode="json") if assessed.effect else None,
                "decision_status": "COMPUTED",
            },
        )
        record_trace(session, trace, existing_run=run)
        return get_decision_trace(session, user_id, run.id, now)
