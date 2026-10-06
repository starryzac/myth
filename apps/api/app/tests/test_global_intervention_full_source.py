"""Exact FULL version routing risks with synthetic originals; no financial execution."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.domain.full_action_set_boundary_full import derive_full_action_set
from app.domain.full_intervention import InterventionMessage, command_identity, message_identity
from app.services import full_action_set_boundary as legacy
from app.services import full_action_set_boundary_full as full
from app.services import full_intervention as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_action_set_boundary_full import fixture, trace_for
from app.tests.test_full_dynamic_goal_execution import NOW
from pydantic import ValidationError
from sqlalchemy.orm import Session


def message_and_traces() -> tuple[InterventionMessage, dict[Any, Any]]:
    before = trace_for(fixture())
    data = fixture().model_copy(deep=True)
    # A distinct synthetic raw inventory produces a distinct original run;
    # its label does not claim an economic effect or monetary result.
    data.base.original_inventory["users"][0]["display_name"] = "synthetic second source"
    after = trace_for(data, before)
    value = full.verify_frozen_full_action_set_trace(after)
    assert value.semantic_key is not None and value.global_action_set_complete
    message = InterventionMessage(
        message_id=message_identity(value.user_id, value.epoch_id, value.semantic_key),
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        source_kind="GLOBAL_ACTION_SET_BOUNDARY",
        source_run_id=after.run_id,
        source_trace_hash=after.trace_hash,
        semantic_key=value.semantic_key,
        creation_command_run_id=command_identity(
            value.user_id, value.epoch_id, "full-notification-test"
        ),
        session_id=None,
        question_revision=None,
        question=None,
        boundary_observation=value.model_dump(mode="json"),
        intervention_policy_binding=None,
        requires_user_attention=value.requires_user_attention,
        created_at=NOW,
        global_action_set_complete=True,
    )
    return message, {row.run_id: row for row in (before, after)}


def test_full_original_uses_exact_full_helper_and_full_current_getter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message, traces = message_and_traces()

    def original(_: Any, __: Any, identity: Any, ___: Any) -> Any:
        return SimpleNamespace(
            trace=traces[identity], completeness="COMPLETE", audit_chain_status="VALID"
        )

    def refuse_legacy(*_: Any) -> Any:
        raise AssertionError("FULL original must not be downgraded to legacy V1")

    monkeypatch.setattr(service, "get_decision_trace", original)
    monkeypatch.setattr(full, "get_decision_trace", original)
    monkeypatch.setattr(legacy, "global_boundary_intervention_source", refuse_legacy)
    session = MagicMock(spec=Session)
    source = service._global_boundary_source(session, message.user_id, message.source_run_id, NOW)
    assert (
        source.boundary == message.boundary_observation
        and source.semantic_key == message.semantic_key
    )
    data = fixture().model_copy(deep=True)
    data.base.original_inventory["users"][0]["display_name"] = "synthetic second source"
    snapshot = derive_full_action_set(data)
    calls: list[Any] = []

    def current(*args: Any) -> Any:
        calls.append(args)
        return snapshot

    monkeypatch.setattr(legacy, "read_current_action_set", refuse_legacy)
    monkeypatch.setattr(full, "read_current_full_action_set", current)
    assert service._global_source_status(session, message, NOW) == "CURRENT"
    assert calls == [(session, message.user_id, NOW)]
    assert InterventionMessage.model_validate_json(message.model_dump_json()) == message


def test_unknown_trace_or_snapshot_algorithm_is_never_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message, traces = message_and_traces()
    trace = traces[message.source_run_id].model_copy(
        update={"algorithm_versions": {"global_action_set": "unknown-version"}}
    )
    monkeypatch.setattr(
        service,
        "get_decision_trace",
        lambda *_: SimpleNamespace(
            trace=trace, completeness="COMPLETE", audit_chain_status="VALID"
        ),
    )
    with pytest.raises(PolicyLifecycleError, match="不能降格"):
        service._global_boundary_source(MagicMock(spec=Session), message.user_id, trace.run_id, NOW)
    payload = message.model_dump(mode="json")
    payload["boundary_observation"]["snapshot"]["algorithm_version"] = "unknown-version"
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(json.dumps(payload))


def test_full_current_unknown_sources_cannot_present_as_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message, _ = message_and_traces()
    snapshot = derive_full_action_set(fixture()).model_copy(
        update={
            "global_action_set_complete": False,
            "status": "UNKNOWN",
            "action_set_signature": None,
        }
    )
    monkeypatch.setattr(full, "read_current_full_action_set", lambda *_: snapshot)
    assert service._global_source_status(MagicMock(spec=Session), message, NOW) == "UNKNOWN"
