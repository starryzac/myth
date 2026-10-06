"""Synthetic new-protocol integration risks; actual PostgreSQL proof is separate."""

import copy
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.db.models import DecisionRun
from app.domain.boundary_types import BoundaryPolicyVersion
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext
from app.domain.full_recovery_execution import ALGORITHM, CONSENT_SOURCE, MARKER
from app.domain.full_recovery_execution_trace import verify_frozen_full_recovery_trace
from app.domain.policy_configuration import configuration_hash
from app.services import decision_trace as history
from app.services.decision_recording import current_capture, start_capture
from app.services.execution_context import require_full_recovery_confirmation_context
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_action_set_boundary import fixture as global_fixture
from app.tests.test_full_action_set_boundary import trace_for
from app.tests.test_full_recovery_execution import consent_fixture, frozen_fixture
from app.tests.test_full_recovery_planning import holding
from app.tests.test_recovery import reserve, snapshot
from sqlalchemy.orm import Session


def rebuild(trace: DecisionTrace, **changes: Any) -> DecisionTrace:
    values = trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
    return build_trace(**{**values, **changes})


def actual_frozen_financial_fixture() -> tuple[DecisionTrace, ExecutionContext]:
    trace, data, command = frozen_fixture()
    held, metadata = holding()
    original = metadata.original.original_authorization
    assert original is not None
    context = ExecutionContext(
        user_id=data.user_id,
        snapshot=snapshot(),
        versions=[
            BoundaryPolicyVersion.model_validate(
                original.model_dump(exclude={"user_id", "policy_status", "latest_version_id"})
            ),
            reserve(),
        ],
        positions=[held],
        boundary_products=[],
        products=[metadata.original.product],
        redemption_quote=data.candidate.original_quote,
        requires_confirmation=True,
    )
    result = revalidate_execution(command.effect, context)
    assert result.status == "CONFIRMATION_REQUIRED", result.reasons
    return (
        rebuild(
            trace,
            inputs={**trace.inputs, "execution_context": context.model_dump(mode="json")},
            outcome={"validation": result.model_dump(mode="json"), "autonomy_level": "ASK_ONCE"},
        ),
        context,
    )


def stored_row(trace: DecisionTrace) -> DecisionRun:
    row = MagicMock(spec=DecisionRun)
    row.id, row.user_id, row.as_of = trace.run_id, trace.user_id, trace.as_of
    row.parent_run_id, row.subject_action_plan_id = trace.parent_run_id, trace.action_id
    row.policy_version_ids = [str(item.id) for item in trace.policies]
    row.evidence_ids = [str(item.id) for item in trace.sources]
    row.input_snapshot = {
        "decision_trace": trace.model_dump(mode="json"),
        "decision_trace_indexes": {
            "policy_version_ids": row.policy_version_ids,
            "evidence_ids": row.evidence_ids,
        },
    }
    row.snapshot_hash = configuration_hash(row.input_snapshot)
    return cast(DecisionRun, row)


def test_new_required_confirmation_preserves_legacy_context_and_actual_capture() -> None:
    _, context = actual_frozen_financial_fixture()
    legacy = context.model_copy(update={"requires_confirmation": False})
    session = Session()
    start_capture(session)
    required = require_full_recovery_confirmation_context(session, legacy)
    assert required.requires_confirmation and not legacy.requires_confirmation
    capture = current_capture(session)
    assert capture is not None
    assert capture.inputs["execution_context"] == required.model_dump(mode="json")
    session.close()


@pytest.mark.parametrize("risk", ["validation", "mandatory-ask", "autonomy", "source"])
def test_historical_reader_recomputes_recovery_even_after_rehash(
    monkeypatch: pytest.MonkeyPatch, risk: str
) -> None:
    trace, _ = actual_frozen_financial_fixture()
    verify_frozen_full_recovery_trace(trace)
    monkeypatch.setattr(history, "_relations", lambda *args: None)
    monkeypatch.setattr(history, "_verify_constraints", lambda *args: None)
    session = cast(Session, MagicMock(spec=Session))
    assert history._stored_trace(session, stored_row(trace))[0] == "COMPLETE"
    fields = trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
    if risk == "validation":
        fields["outcome"]["validation"]["status"] = "READY"
    elif risk == "mandatory-ask":
        fields["inputs"]["execution_context"]["requires_confirmation"] = False
    elif risk == "autonomy":
        fields["outcome"]["autonomy_level"] = "AUTO_EXECUTE"
    else:
        fields["sources"] = []
    changed = build_trace(**fields)
    with pytest.raises(PolicyLifecycleError):
        history._stored_trace(session, stored_row(changed))


def consent_trace() -> DecisionTrace:
    consent, effect = consent_fixture()
    content = consent.model_dump(mode="json")
    digest = configuration_hash(content)
    source = TraceEvidence(
        id=uuid5(consent.action_id, "full-recovery-user-consent"),
        user_id=consent.user_id,
        evidence_level="USER_CONFIRMED_ACTION",
        source_type=CONSENT_SOURCE,
        source_ref=str(consent.action_id),
        content=content,
        content_hash=digest,
        captured_content_hash=digest,
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=consent.confirmed_at,
        valid_from=consent.confirmed_at,
        valid_to=effect.expires_at,
    )
    return build_trace(
        run_id=uuid5(consent.action_id, "full-recovery-user-consent-trace"),
        user_id=consent.user_id,
        phase="EVALUATION",
        action_id=consent.action_id,
        as_of=consent.confirmed_at,
        parent_run_id=UUID(int=822),
        algorithm_versions={"trace": "decision-trace-v1", MARKER: ALGORITHM},
        inputs={
            "expected_epoch_id": str(consent.epoch_id),
            "reviewed_effect_hash": consent.original_effect_hash,
            "accepted": True,
        },
        sources=[source],
        policies=[],
        outcome={"full_recovery_user_consent": content},
    )


@pytest.mark.parametrize("risk", ["role", "epoch", "hash", "source"])
def test_dedicated_user_consent_is_separate_typed_history_with_no_legacy_downgrade(
    risk: str,
) -> None:
    trace = consent_trace()
    verify_frozen_full_recovery_trace(trace)
    fields = trace.model_dump(mode="python", exclude={"input_hash", "trace_hash"})
    if risk == "role":
        fields["outcome"]["full_recovery_user_consent"]["principal_at_confirmation"]["role"] = (
            "AGENT"
        )
    elif risk == "epoch":
        fields["inputs"]["expected_epoch_id"] = str(UUID(int=999))
    elif risk == "hash":
        fields["inputs"]["reviewed_effect_hash"] = "b" * 64
    else:
        fields["sources"] = []
    with pytest.raises(ValueError):
        verify_frozen_full_recovery_trace(build_trace(**fields))


def test_original_global_history_rejects_rehashed_action_set_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = trace_for(global_fixture())
    monkeypatch.setattr(history, "_relations", lambda *args: None)
    monkeypatch.setattr(history, "_verify_constraints", lambda *args: None)
    session = cast(Session, MagicMock(spec=Session))
    assert history._stored_trace(session, stored_row(trace))[0] == "COMPLETE"
    outcome = copy.deepcopy(trace.outcome)
    outcome["global_boundary_observation"]["snapshot"]["candidates"][0]["amount_cents"] = 999
    with pytest.raises(PolicyLifecycleError):
        history._stored_trace(session, stored_row(rebuild(trace, outcome=outcome)))
