"""Synthetic notification contract risks, not actual global or financial measurements."""

import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.db.full_models import InterventionOutbox
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace
from app.domain.full_action_set_boundary import derive_action_set
from app.domain.full_intervention import (
    ALGORITHM,
    GlobalBoundaryObservationRequest,
    InterventionMessage,
    InterventionReceipt,
    ObserveRequest,
    command_identity,
    message_identity,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_boundary as producer
from app.services import full_intervention as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_full_action_set_boundary import EPOCH, fixture, trace_for
from app.tests.test_full_intervention import message as old_question_message
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session


def originals() -> tuple[DecisionTrace, DecisionTrace, InterventionMessage, InterventionReceipt]:
    first = trace_for(fixture())
    source = trace_for(fixture(200), key="new-global-source", previous=first)
    observed = producer.verify_frozen_action_set_trace(source)
    assert observed.semantic_key is not None
    body = GlobalBoundaryObservationRequest(
        kind="GLOBAL_ACTION_SET_BOUNDARY",
        observation_run_id=source.run_id,
        reviewed_source_trace_hash=source.trace_hash,
        expected_epoch_id=EPOCH,
        idempotency_key="global-message-command",
    )
    command = {"kind": "OBSERVE", "user_id": str(USER), "request": body.model_dump(mode="json")}
    identity = command_identity(USER, EPOCH, body.idempotency_key)
    message = InterventionMessage(
        message_id=message_identity(USER, EPOCH, observed.semantic_key),
        user_id=USER,
        epoch_id=EPOCH,
        source_kind=body.kind,
        source_run_id=source.run_id,
        source_trace_hash=source.trace_hash,
        semantic_key=observed.semantic_key,
        creation_command_run_id=identity,
        session_id=None,
        question_revision=None,
        question=None,
        boundary_observation=observed.model_dump(mode="json"),
        intervention_policy_binding=None,
        requires_user_attention=True,
        created_at=NOW,
        global_action_set_complete=True,
    )
    payload = message.model_dump(mode="json")
    receipt = InterventionReceipt(
        kind="OBSERVE",
        user_id=USER,
        epoch_id=EPOCH,
        idempotency_key=body.idempotency_key,
        request_hash=configuration_hash(command),
        original_command=command,
        message_id=message.message_id,
        payload_hash=configuration_hash(payload),
        recorded_at=NOW,
        duplicate_semantics=False,
    )
    return first, source, message, receipt


def install_originals(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Session, InterventionOutbox, InterventionMessage]:
    first, source, message, receipt = originals()
    command = build_trace(
        run_id=message.creation_command_run_id,
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        parent_run_id=source.run_id,
        action_id=None,
        algorithm_versions={"trace": "decision-trace-v1", "intervention": ALGORITHM},
        inputs={
            "original_command": receipt.original_command,
            "source_trace_hash": source.trace_hash,
        },
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={
            "receipt": receipt.model_dump(mode="json"),
            "message": message.model_dump(mode="json"),
        },
    )
    traces = {row.run_id: row for row in (first, source, command)}

    def read(_: Any, user: UUID, identity: UUID, __: Any) -> Any:
        assert user == USER
        return SimpleNamespace(
            trace=traces[identity], completeness="COMPLETE", audit_chain_status="VALID"
        )

    monkeypatch.setattr(service, "get_decision_trace", read)
    monkeypatch.setattr(producer, "get_decision_trace", read)
    session = MagicMock(spec=Session)
    session.scalar.return_value = command.run_id
    row = InterventionOutbox(
        id=message.message_id,
        user_id=USER,
        created_at=NOW,
        epoch_id=EPOCH,
        protocol_version=message.protocol,
        source_kind=message.source_kind,
        source_run_id=message.source_run_id,
        source_trace_hash=message.source_trace_hash,
        semantic_key=message.semantic_key,
        session_id=None,
        question_id=None,
        question_revision=None,
        payload=message.model_dump(mode="json"),
        payload_hash=receipt.payload_hash,
        state="PENDING",
        available_at=NOW,
        updated_at=NOW,
    )
    return session, row, message


def test_global_message_reads_actual_producer_contract_and_original_ancestry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, row, message = install_originals(monkeypatch)
    assert service._message(session, row, NOW) == message
    session.add.assert_not_called()  # type: ignore[attr-defined]
    assert message.global_action_set_complete and not message.bank_authority


@pytest.mark.parametrize(
    "field", ["amount_cents", "clock", "role", "payload", "global_action_set_complete"]
)
def test_global_request_accepts_only_reviewed_original_identity(field: str) -> None:
    body = originals()[3].original_command["request"] | {field: 1}
    with pytest.raises(ValidationError):
        TypeAdapter(ObserveRequest).validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "epoch_id",
        "observation_run_id",
        "semantic_key",
        "global_action_set_complete",
        "requires_user_attention",
        "previous_observation_run_id",
    ],
)
def test_global_payload_cannot_upgrade_incomplete_or_rebind_original(field: str) -> None:
    value = originals()[2].model_dump(mode="json")
    original = value["boundary_observation"]
    original[field] = (
        False
        if field in {"global_action_set_complete", "requires_user_attention"}
        else None
        if field == "previous_observation_run_id"
        else "0" * 64
        if field == "semantic_key"
        else str(UUID(int=991))
    )
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("value", [1, "true", False])
def test_global_completion_requires_exact_true_for_new_kind(value: Any) -> None:
    payload = originals()[2].model_dump(mode="json") | {"global_action_set_complete": value}
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(json.dumps(payload))


def test_old_question_bytes_and_false_global_flag_are_preserved() -> None:
    old = old_question_message().model_dump(mode="json")
    # Recomputed from the exact Root pre-change module (SHA 46d31891...) and
    # this existing synthetic question original, before adding the new kind.
    assert (
        configuration_hash(old)
        == "aa27695b040ef4a75d4b35dab8060892f274cb38ab444fdce935439b5b9c52de"
    )
    assert old["global_action_set_complete"] is False
    assert InterventionMessage.model_validate_json(json.dumps(old)).model_dump(mode="json") == old
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(
            json.dumps(old | {"global_action_set_complete": True})
        )


def test_old_single_action_serialization_hash_and_false_flag_are_preserved() -> None:
    original = old_question_message().model_dump(mode="json")
    original.update(
        source_kind="SINGLE_ACTION_BOUNDARY",
        session_id=None,
        question_revision=None,
        question=None,
        boundary_observation={"SYNTHETIC_NOTIFICATION_ONLY": True},
        requires_user_attention=False,
    )
    value = InterventionMessage.model_validate_json(json.dumps(original)).model_dump(mode="json")
    # Same synthetic schema original parsed by Root's exact pre-change module.
    assert value == original and value["global_action_set_complete"] is False
    assert (
        configuration_hash(value)
        == "c574c3e6a590252ed4c0d4908dddf6826f95c11232eda9de70217b4a28dec9c5"
    )
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(
            json.dumps(value | {"global_action_set_complete": True})
        )


def test_rehashed_global_payload_still_requires_original_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, row, _ = install_originals(monkeypatch)
    row.payload = deepcopy(row.payload)
    row.payload["boundary_observation"]["snapshot"]["financial_input_hash"] = "f" * 64
    row.payload_hash = configuration_hash(row.payload)
    with pytest.raises(PolicyLifecycleError):
        service._message(session, row, NOW)


@pytest.mark.parametrize("mode", ["current", "changed", "unknown", "owner", "epoch"])
def test_current_global_source_is_not_implied_by_historical_valid_trace(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = originals()[2]
    current = derive_action_set(fixture(200 if mode != "changed" else 300))
    changes: dict[str, Any] = {"as_of": NOW + timedelta(seconds=1)}
    if mode == "unknown":
        changes.update(
            status="UNKNOWN", global_action_set_complete=False, action_set_signature=None
        )
    elif mode in {"owner", "epoch"}:
        changes["user_id" if mode == "owner" else "epoch_id"] = UUID(int=987)
    monkeypatch.setattr(
        producer, "read_current_action_set", lambda *_: current.model_copy(update=changes)
    )
    assert (
        service._global_source_status(MagicMock(spec=Session), message, NOW)
        == {
            "current": "CURRENT",
            "changed": "STALE",
            "unknown": "UNKNOWN",
            "owner": "UNKNOWN",
            "epoch": "UNKNOWN",
        }[mode]
    )


def test_initial_global_observation_has_no_comparison_to_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = trace_for(fixture())
    monkeypatch.setattr(
        producer,
        "get_decision_trace",
        lambda *_: SimpleNamespace(
            trace=initial, completeness="COMPLETE", audit_chain_status="VALID"
        ),
    )
    with pytest.raises(PolicyLifecycleError, match="不完整或初始观察"):
        service._global_boundary_source(MagicMock(spec=Session), USER, initial.run_id, NOW)
