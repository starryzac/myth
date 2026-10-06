"""New consumer and frozen-reader risks; declared synthetic fixtures, no PG proof."""

import json
from datetime import timedelta
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.models import DecisionRun
from app.domain.execution import revalidate_execution
from app.domain.full_dynamic_goal_execution import build_full_dynamic_goal_effect
from app.services import decision_trace as history
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_dynamic_goal_execution import fixture
from app.tests.test_full_dynamic_goal_execution_history import frozen_trace, rebuild
from sqlalchemy.orm import Session


def test_new_private_proof_allows_confirmed_max_and_preserves_legacy_rejection() -> None:
    data = fixture()
    effect, proof = build_full_dynamic_goal_effect(data, UUID(int=200))
    assert revalidate_execution(effect, data.context).status == "BLOCKED"
    result = revalidate_execution(effect, data.context, full_dynamic_goal_proof=proof)
    assert result.status == "READY" and effect.amount_cents == 150000
    assert result.projected_boundary is not None
    assert proof.bank_authority is False


@pytest.mark.parametrize("change", ["cap", "clock", "effect"])
def test_new_consumer_never_accepts_only_numeric_or_stale_proof(change: str) -> None:
    data = fixture()
    effect, proof = build_full_dynamic_goal_effect(data, UUID(int=200))
    context = data.context
    if change == "cap":
        proof = proof.model_copy(update={"dynamic_cap_cents": 200000})
    elif change == "clock":
        context = context.model_copy(
            update={
                "snapshot": context.snapshot.model_copy(
                    update={"as_of": context.snapshot.as_of + timedelta(seconds=1)}
                )
            }
        )
    else:
        effect = effect.model_copy(
            update={
                "amount_cents": 160000,
                "cash_uses": [effect.cash_uses[0].model_copy(update={"amount_cents": 160000})],
                "income_uses": [effect.income_uses[0].model_copy(update={"amount_cents": 160000})],
            }
        )
    assert revalidate_execution(effect, context, full_dynamic_goal_proof=proof).status == "BLOCKED"


def test_actual_historical_reader_rejects_rehashed_dynamic_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.execution_types import ExecutionContext, ExecutionEffect
    from app.domain.full_dynamic_goal_execution import read_frozen_full_dynamic_goal_proof

    original = frozen_trace()
    effect = ExecutionEffect.model_validate_json(json.dumps(original.inputs["effect"]))
    context = ExecutionContext.model_validate_json(json.dumps(original.inputs["execution_context"]))
    validation = revalidate_execution(
        effect, context, full_dynamic_goal_proof=read_frozen_full_dynamic_goal_proof(original)
    )
    trace = rebuild(original, outcome={"validation": validation.model_dump(mode="json")})
    monkeypatch.setattr(history, "_relations", lambda *args: None)
    monkeypatch.setattr(history, "_verify_constraints", lambda *args: None)

    def row_for(value: Any) -> DecisionRun:
        from app.domain.policy_configuration import configuration_hash

        row = MagicMock(spec=DecisionRun)
        row.id, row.user_id, row.as_of = value.run_id, value.user_id, value.as_of
        row.parent_run_id, row.subject_action_plan_id = value.parent_run_id, value.action_id
        row.policy_version_ids = [str(p.id) for p in value.policies]
        row.evidence_ids = [str(s.id) for s in value.sources]
        row.input_snapshot = {
            "decision_trace": value.model_dump(mode="json"),
            "decision_trace_indexes": {
                "policy_version_ids": row.policy_version_ids,
                "evidence_ids": row.evidence_ids,
            },
        }
        row.snapshot_hash = configuration_hash(row.input_snapshot)
        return cast(DecisionRun, row)

    session = cast(Session, MagicMock(spec=Session))
    assert history._stored_trace(session, row_for(trace))[0] == "COMPLETE"
    changed = rebuild(trace, outcome={"validation": {**trace.outcome["validation"], "reasons": []}})
    # Alter a result field even though every caller-visible trace/run digest was rebuilt.
    changed = rebuild(
        changed,
        outcome={"validation": {**changed.outcome["validation"], "status": "BLOCKED"}},
    )
    with pytest.raises(PolicyLifecycleError):
        history._stored_trace(session, row_for(changed))
