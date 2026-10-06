"""Synthetic record seam checks; they do not substitute for actual financial integration."""

import copy
from datetime import timedelta
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.models import ActionPlan
from app.domain.full_maturity_execution import VALIDATION_INFO_KEY
from app.services import boundary
from app.services import decision_recording as recording
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_maturity_execution import history_fixture
from sqlalchemy.orm import Session


def test_bank_record_keeps_fresh_original_sources_and_consumes_handoff_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = history_fixture("BANK_ACCEPT")
    assert original.action_id is not None
    action = ActionPlan(
        id=original.action_id,
        user_id=original.user_id,
        decision_run_id=original.parent_run_id,
        autonomy_level="ASK_ONCE",
        request=original.inputs["action_request"],
        policy_version_id=UUID(int=1),
        idempotency_key="maturity:user:synthetic-bank-record",
    )
    double = MagicMock(spec=Session)
    double.info = {
        VALIDATION_INFO_KEY: original.inputs["validation_inputs"]["full_maturity_validation"]
    }
    session = cast(Session, double)
    monkeypatch.setattr(recording, "capture_versions", lambda *_: None)
    monkeypatch.setattr(recording, "capture_boundary", lambda *_: None)
    monkeypatch.setattr(boundary, "load_boundary_context", lambda *_: None)
    write = MagicMock()
    monkeypatch.setattr(recording, "record_trace", write)
    recording.record_recovery_bank_acceptance(
        session, action, original.as_of, {"contract_verified": True}, contract=True
    )
    write.assert_called_once()
    saved = write.call_args.args[1]
    assert saved.algorithm_versions["full_maturity_execution"] == "full-maturity-user-execution-v1"
    assert saved.sources == original.sources
    assert saved.outcome["autonomy_level"] == "ASK_ONCE" and saved.outcome["new_authority"] is False
    assert VALIDATION_INFO_KEY not in double.info
    with pytest.raises(PolicyLifecycleError):
        recording.record_recovery_bank_acceptance(
            session, action, original.as_of, {}, contract=True
        )
    assert write.call_count == 1


@pytest.mark.parametrize(
    "change", ["missing", "identity", "time", "owner", "contract", "proof", "consent"]
)
def test_bad_current_maturity_record_handoff_cannot_persist_success(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    original = history_fixture("BANK_ACCEPT")
    proof = copy.deepcopy(original.inputs["validation_inputs"]["full_maturity_validation"])
    action = ActionPlan(
        id=original.action_id,
        user_id=original.user_id,
        decision_run_id=original.parent_run_id,
        autonomy_level="ASK_ONCE",
        request=original.inputs["action_request"],
        policy_version_id=UUID(int=1),
        idempotency_key="maturity:user:synthetic-bank-record",
    )
    contract, now = change != "contract", original.as_of
    if change == "identity":
        proof["action_id"] = str(UUID(int=2))
    elif change == "time":
        now += timedelta(seconds=1)
    elif change == "owner":
        action.user_id = UUID(int=2)
    elif change == "proof":
        proof["proof"]["proof_hash"] = "0" * 64
    elif change == "consent":
        proof["consent_source"]["content"]["reviewed_command_hash"] = "0" * 64
    double = MagicMock(spec=Session)
    double.info = {} if change == "missing" else {VALIDATION_INFO_KEY: proof}
    session = cast(Session, double)
    monkeypatch.setattr(recording, "capture_versions", lambda *_: None)
    monkeypatch.setattr(recording, "capture_boundary", lambda *_: None)
    monkeypatch.setattr(boundary, "load_boundary_context", lambda *_: None)
    write = MagicMock()
    monkeypatch.setattr(recording, "record_trace", write)
    with pytest.raises(PolicyLifecycleError):
        recording.record_recovery_bank_acceptance(session, action, now, {}, contract=contract)
    write.assert_not_called()
