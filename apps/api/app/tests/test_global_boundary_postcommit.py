"""Synthetic postcommit risks: never claim actual delivery, bank or participant proof."""

from datetime import datetime
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.api import global_boundary_postcommit as hook
from app.domain.full_action_set_boundary import GlobalBoundaryObservation
from app.domain.full_intervention import GlobalBoundaryObservationRequest
from app.services import global_boundary_intervention_producer as producer
from app.services.full_intervention import Source
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_full_action_set_boundary import fixture, trace_for
from fastapi import Response
from sqlalchemy.engine import Engine


def crossing() -> tuple[GlobalBoundaryObservation, Source]:
    first = trace_for(fixture())
    second = trace_for(fixture(200), key="postcommit-crossing", previous=first)
    from app.services.full_action_set_boundary import verify_frozen_action_set_trace

    value = verify_frozen_action_set_trace(second)
    assert value.semantic_key is not None and value.requires_user_attention
    return value, Source(
        second, value.semantic_key, None, None, None, value.model_dump(mode="json"), True
    )


def test_crossing_postcommit_failure_preserves_exact_original_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, _ = crossing()
    callback = MagicMock(side_effect=TimeoutError("synthetic metadata response lost"))
    monkeypatch.setattr(hook, "produce_global_boundary_intervention", callback)
    response = Response()
    returned = hook.postcommit_global_observation(
        cast(Engine, object()), USER, value, NOW, response
    )
    assert returned is value
    assert response.headers["X-Global-Intervention-Status"] == "SOURCE_UNVERIFIED"
    callback.assert_called_once()


@pytest.mark.parametrize("risk", ["initial", "numeric", "unknown", "incomplete", "replay"])
def test_no_notifications_for_non_crossing_incomplete_or_original_replay(
    monkeypatch: pytest.MonkeyPatch, risk: str
) -> None:
    value, _ = crossing()
    changes: dict[str, object] = {
        "initial": {"kind": "BoundaryObserved", "semantic_key": None},
        "numeric": {"kind": "BoundaryObserved", "requires_user_attention": False},
        "unknown": {"kind": None},
        "incomplete": {"global_action_set_complete": False},
        "replay": {"idempotent_replay": True},
    }
    changed = value.model_copy(update=cast(dict[str, object], changes[risk]))
    callback = MagicMock()
    monkeypatch.setattr(hook, "produce_global_boundary_intervention", callback)
    response = Response()
    assert (
        hook.postcommit_global_observation(cast(Engine, object()), USER, changed, NOW, response)
        is changed
    )
    callback.assert_not_called()


def test_original_source_unknown_never_observes_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = MagicMock()
    reader.__enter__.return_value = MagicMock()
    monkeypatch.setattr(producer, "_reader", lambda *args: reader)
    scope = MagicMock()
    monkeypatch.setattr(producer, "audit_read_scope", lambda *args: scope)
    monkeypatch.setattr(
        producer,
        "_global_boundary_source",
        MagicMock(side_effect=PolicyLifecycleError("SYNTHETIC_UNKNOWN", "原件未知", 409)),
    )
    observed = MagicMock()
    monkeypatch.setattr(producer, "observe_intervention", observed)
    result = producer.produce_global_boundary_intervention(
        cast(Engine, object()), USER, UUID(int=123), NOW
    )
    assert result.status == "SOURCE_UNVERIFIED" and result.error_code == "SYNTHETIC_UNKNOWN"
    assert not result.delivered and not result.acknowledged and not result.authority_granted
    observed.assert_not_called()


def test_verified_original_builds_stable_identity_only_command_after_read_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value, source = crossing()
    reader = MagicMock()
    reader.__enter__.return_value = MagicMock()
    monkeypatch.setattr(producer, "_reader", lambda *args: reader)
    monkeypatch.setattr(producer, "audit_read_scope", lambda *args: MagicMock())
    monkeypatch.setattr(producer, "_global_boundary_source", lambda *args: source)
    requests = []

    def observe(engine: Engine, user_id: UUID, body: object, now: datetime) -> None:
        reader.__exit__.assert_called()
        requests.append(body)
        raise PolicyLifecycleError("SYNTHETIC_WRITE_UNKNOWN", "保留原键", 409)

    monkeypatch.setattr(producer, "observe_intervention", observe)
    for _ in range(2):
        result = producer.produce_global_boundary_intervention(
            cast(Engine, object()), USER, value.observation_run_id, NOW
        )
        assert result.status == "SOURCE_UNVERIFIED"
    assert len(requests) == 2 and requests[0] == requests[1]
    body = requests[0]
    assert isinstance(body, GlobalBoundaryObservationRequest)
    assert body.kind == "GLOBAL_ACTION_SET_BOUNDARY"
    assert body.observation_run_id == value.observation_run_id
    assert body.reviewed_source_trace_hash == source.trace.trace_hash
    assert body.expected_epoch_id == value.epoch_id
    assert set(body.model_dump()) == {
        "kind",
        "observation_run_id",
        "reviewed_source_trace_hash",
        "expected_epoch_id",
        "intervention_policy_id",
        "idempotency_key",
    }
