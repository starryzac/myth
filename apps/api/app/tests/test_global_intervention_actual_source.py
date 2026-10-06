"""Actual-v2 notification routing risks with synthetic frozen originals only."""

import copy
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.api import global_boundary_postcommit as hook
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_boundary import CandidateInput
from app.domain.full_action_set_boundary_actual import (
    ActualActionSetInput,
    ActualActionSetSnapshot,
    ActualGlobalBoundaryObservation,
)
from app.domain.full_intervention import InterventionMessage, command_identity, message_identity
from app.services import full_action_set_boundary as legacy
from app.services import full_action_set_boundary_actual as actual
from app.services import full_action_set_boundary_full as full
from app.services import full_intervention as service
from app.services import global_boundary_intervention_producer as producer
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_action_set_boundary_actual import coverage, fixture, trace_for
from app.tests.test_full_dynamic_goal_execution import NOW, USER
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Originals:
    value: ActualGlobalBoundaryObservation
    message: InterventionMessage
    inputs: ActualActionSetInput
    traces: dict[UUID, DecisionTrace]


def source_originals(kind: Literal["numeric", "crossed"] = "crossed") -> Originals:
    """TOOL_ONLY sources; no real confirmation, financial outcome or persisted trace."""
    data = fixture()
    before = trace_for(data)
    raw = copy.deepcopy(data.base.original_inventory)
    if kind == "numeric":
        raw["users"][0]["display_name"] = "TOOL_ONLY non-economic source delta"
        changed = data.model_copy(
            update={
                "base": data.base.model_copy(update={"original_inventory": raw}),
                "table_coverage": coverage(raw),
            }
        )
    else:
        raw["policies"][0]["status"] = "SUSPENDED"
        changed = data.model_copy(
            update={
                "base": data.base.model_copy(
                    update={
                        "original_inventory": raw,
                        "candidates": [
                            CandidateInput(
                                candidate_key=data.base.expected_candidate_keys[0],
                                excluded_by_current_policy=True,
                            )
                        ],
                    }
                ),
                "table_coverage": coverage(raw),
                "dynamic_goals": [
                    data.dynamic_goals[0].model_copy(
                        update={"data": None, "authority": None, "excluded_by_current_policy": True}
                    )
                ],
            }
        )
    after = trace_for(changed, before)
    value = actual.verify_frozen_actual_action_set_trace(after)
    assert value.global_action_set_complete and value.semantic_key is not None
    assert value.kind == ("BoundaryCrossed" if kind == "crossed" else "BoundaryObserved")
    message = InterventionMessage(
        message_id=message_identity(value.user_id, value.epoch_id, value.semantic_key),
        user_id=value.user_id,
        epoch_id=value.epoch_id,
        source_kind="GLOBAL_ACTION_SET_BOUNDARY",
        source_run_id=after.run_id,
        source_trace_hash=after.trace_hash,
        semantic_key=value.semantic_key,
        creation_command_run_id=command_identity(
            value.user_id, value.epoch_id, "TOOL_ONLY_ACTUAL_V2"
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
    return Originals(value, message, changed, {row.run_id: row for row in (before, after)})


@pytest.fixture(scope="module")
def crossed() -> Originals:
    return source_originals("crossed")


@pytest.fixture(scope="module")
def numeric() -> Originals:
    return source_originals("numeric")


def forbid_old_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any) -> Any:
        raise AssertionError("Actual-v2 may not call legacy/full-v1 original or current helpers")

    for module, source, getter in (
        (legacy, "global_boundary_intervention_source", "read_current_action_set"),
        (full, "full_global_boundary_intervention_source", "read_current_full_action_set"),
    ):
        monkeypatch.setattr(module, source, refuse)
        monkeypatch.setattr(module, getter, refuse)


def test_actual_original_source_and_current_only_dispatch_exact_new_version(
    monkeypatch: pytest.MonkeyPatch, crossed: Originals
) -> None:
    forbid_old_helpers(monkeypatch)

    def original(_: Any, __: Any, identity: UUID, ___: Any) -> Any:
        return SimpleNamespace(
            trace=crossed.traces[identity], completeness="COMPLETE", audit_chain_status="VALID"
        )

    monkeypatch.setattr(service, "get_decision_trace", original)
    monkeypatch.setattr(actual, "get_decision_trace", original)
    session = MagicMock(spec=Session)
    source = service._global_boundary_source(session, USER, crossed.value.observation_run_id, NOW)
    assert source.boundary == crossed.value.model_dump(mode="json")
    assert source.trace == crossed.traces[crossed.value.observation_run_id]
    assert source.semantic_key == crossed.value.semantic_key and source.attention is True
    current_calls: list[tuple[Any, ...]] = []

    def current(*args: Any) -> ActualActionSetSnapshot:
        current_calls.append(args)
        return crossed.value.snapshot

    monkeypatch.setattr(actual, "read_current_actual_action_set", current)
    assert service._global_source_status(session, crossed.message, NOW) == "CURRENT"
    assert current_calls == [(session, USER, NOW)]
    assert (
        InterventionMessage.model_validate_json(crossed.message.model_dump_json())
        == crossed.message
    )
    assert crossed.message.boundary_observation == crossed.value.model_dump(mode="json")
    assert (
        not crossed.message.bank_authority
        and not crossed.message.execution_eligible
        and not crossed.message.answers_question
    )
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "risk", ["missing", "incomplete", "audit", "unsupported", "missing_parent"]
)
def test_unknown_original_never_downgrades_or_produces_grant(
    monkeypatch: pytest.MonkeyPatch, crossed: Originals, risk: str
) -> None:
    forbid_old_helpers(monkeypatch)

    def original(_: Any, __: Any, identity: UUID, ___: Any) -> Any:
        trace = crossed.traces.get(identity)
        complete, status = "COMPLETE", "VALID"
        if risk == "missing":
            trace = None
        elif risk == "incomplete":
            complete = "INCOMPLETE"
        elif risk == "audit":
            status = "INVALID"
        elif risk == "unsupported":
            assert trace is not None
            trace = trace.model_copy(
                update={"algorithm_versions": {"global_action_set": "unknown-version"}}
            )
        elif risk == "missing_parent" and identity != crossed.value.observation_run_id:
            trace = None
        return SimpleNamespace(trace=trace, completeness=complete, audit_chain_status=status)

    monkeypatch.setattr(service, "get_decision_trace", original)
    monkeypatch.setattr(actual, "get_decision_trace", original)
    session = MagicMock(spec=Session)
    with pytest.raises(PolicyLifecycleError):
        service._global_boundary_source(session, USER, crossed.value.observation_run_id, NOW)
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "risk", ["incomplete", "unknown", "owner", "epoch", "version", "scope", "signature"]
)
def test_current_actual_missing_binding_is_unknown_and_real_different_set_is_stale(
    monkeypatch: pytest.MonkeyPatch, crossed: Originals, numeric: Originals, risk: str
) -> None:
    forbid_old_helpers(monkeypatch)
    updates: dict[str, Any] = {
        "incomplete": {"global_action_set_complete": False},
        "unknown": {"status": "UNKNOWN", "action_set_signature": None},
        "owner": {"user_id": UUID(int=800)},
        "epoch": {"epoch_id": UUID(int=800)},
        "version": {"algorithm_version": "full-policy-action-set-boundary-full-v1"},
        "scope": {"scope": "POLICY_BACKED_SERVER_PRODUCERS_V1"},
    }
    snapshot = (
        numeric.value.snapshot
        if risk == "signature"
        else crossed.value.snapshot.model_copy(update=updates[risk])
    )
    calls = MagicMock(return_value=snapshot)
    monkeypatch.setattr(actual, "read_current_actual_action_set", calls)
    result = service._global_source_status(MagicMock(spec=Session), crossed.message, NOW)
    assert result == ("STALE" if risk == "signature" else "UNKNOWN")
    calls.assert_called_once()


@pytest.mark.parametrize(
    "risk",
    ["version", "bank_authority", "complete", "attention", "source_run", "epoch", "unknown_field"],
)
def test_typed_original_message_cannot_relabel_unknown_old_scope_or_grant(
    crossed: Originals, risk: str
) -> None:
    raw = crossed.message.model_dump(mode="json")
    if risk == "version":
        raw["boundary_observation"]["snapshot"]["algorithm_version"] = (
            "full-policy-action-set-boundary-full-v1"
        )
    elif risk == "bank_authority":
        raw["bank_authority"] = True
    elif risk == "complete":
        raw["boundary_observation"]["global_action_set_complete"] = False
    elif risk == "attention":
        raw["requires_user_attention"] = False
    elif risk == "source_run":
        raw["source_run_id"] = str(UUID(int=800))
    elif risk == "epoch":
        raw["boundary_observation"]["snapshot"]["epoch_id"] = str(UUID(int=800))
    elif risk == "unknown_field":
        raw["boundary_observation"]["bank_result"] = "success"
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(json.dumps(raw))


def test_postcommit_true_actual_crossing_once_and_numeric_initial_replay_none(
    monkeypatch: pytest.MonkeyPatch, crossed: Originals, numeric: Originals
) -> None:
    callback = MagicMock(return_value=SimpleNamespace(status="OBSERVED"))
    monkeypatch.setattr(hook, "produce_global_boundary_intervention", callback)
    response = Response()
    assert (
        hook.postcommit_global_observation(
            cast(Engine, object()), USER, crossed.value, NOW, response
        )
        is crossed.value
    )
    callback.assert_called_once_with(
        callback.call_args.args[0], USER, crossed.value.observation_run_id, NOW
    )
    assert response.headers["X-Global-Intervention-Status"] == "OBSERVED"
    callback.reset_mock()
    initial = actual.verify_frozen_actual_action_set_trace(next(iter(numeric.traces.values())))
    for value in (
        numeric.value,
        initial,
        crossed.value.model_copy(update={"idempotent_replay": True}),
        crossed.value.model_copy(update={"global_action_set_complete": False}),
        crossed.value.model_copy(update={"kind": None, "requires_user_attention": False}),
    ):
        result = Response()
        assert (
            hook.postcommit_global_observation(cast(Engine, object()), USER, value, NOW, result)
            is value
        )
        assert result.headers["X-Global-Intervention-Status"] == "NOT_CROSSED"
    callback.assert_not_called()
    assert numeric.value.snapshot.action_set_signature == initial.snapshot.action_set_signature
    assert numeric.value.snapshot.input_hash != initial.snapshot.input_hash


def test_postcommit_error_preserves_actual_original_and_producer_unknown_never_writes(
    monkeypatch: pytest.MonkeyPatch, crossed: Originals
) -> None:
    callback = MagicMock(side_effect=TimeoutError("TOOL_ONLY metadata response loss"))
    monkeypatch.setattr(hook, "produce_global_boundary_intervention", callback)
    response = Response()
    assert (
        hook.postcommit_global_observation(
            cast(Engine, object()), USER, crossed.value, NOW, response
        )
        is crossed.value
    )
    assert response.headers["X-Global-Intervention-Status"] == "SOURCE_UNVERIFIED"
    reader = MagicMock()
    reader.__enter__.return_value = MagicMock(spec=Session)
    monkeypatch.setattr(producer, "_reader", lambda *_: reader)
    monkeypatch.setattr(producer, "audit_read_scope", lambda *_: MagicMock())
    monkeypatch.setattr(
        producer,
        "_global_boundary_source",
        MagicMock(side_effect=PolicyLifecycleError("ACTUAL_V2_SOURCE_UNKNOWN", "原证据未知", 409)),
    )
    observed = MagicMock()
    monkeypatch.setattr(producer, "observe_intervention", observed)
    result = producer.produce_global_boundary_intervention(
        cast(Engine, object()), USER, crossed.value.observation_run_id, NOW
    )
    assert result.status == "SOURCE_UNVERIFIED" and result.error_code == "ACTUAL_V2_SOURCE_UNKNOWN"
    assert not result.delivered and not result.acknowledged and not result.authority_granted
    observed.assert_not_called()
