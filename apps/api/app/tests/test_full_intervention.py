"""Direct notification risks with existing pure worlds; these are not financial observations."""

import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1.full_intervention import router
from app.db.full_models import InterventionInbox, InterventionOutbox
from app.domain.full_intervention import (
    CONSUMER,
    AcknowledgmentRequest,
    InterventionMessage,
    InterventionReceipt,
    QuestionObservationRequest,
    boundary_semantics,
    command_identity,
    effective_state,
    message_identity,
    question_semantics,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_intervention as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.question_workflow import _response
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_question_workflow import EPOCH, SESSION, revision
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session


def message() -> InterventionMessage:
    state = revision()
    return InterventionMessage(
        message_id=message_identity(USER, EPOCH, question_semantics(state)),
        user_id=USER,
        epoch_id=EPOCH,
        source_kind="QUESTION",
        source_run_id=state.run_id,
        source_trace_hash="c" * 64,
        semantic_key=question_semantics(state),
        creation_command_run_id=command_identity(USER, EPOCH, "observe-1"),
        session_id=SESSION,
        question_revision=1,
        question=state.pending_question,
        boundary_observation=None,
        intervention_policy_binding=None,
        requires_user_attention=True,
        created_at=NOW,
    )


def row() -> InterventionOutbox:
    value = message()
    return InterventionOutbox(
        id=value.message_id,
        user_id=USER,
        created_at=NOW,
        epoch_id=EPOCH,
        protocol_version=value.protocol,
        source_kind=value.source_kind,
        source_run_id=value.source_run_id,
        source_trace_hash=value.source_trace_hash,
        semantic_key=value.semantic_key,
        session_id=SESSION,
        question_id=value.question.question_id if value.question else None,
        question_revision=1,
        payload=value.model_dump(mode="json"),
        payload_hash=configuration_hash(value.model_dump(mode="json")),
        state="PENDING",
        available_at=NOW,
        updated_at=NOW,
    )


def ack() -> InterventionReceipt:
    value = message()
    request: dict[str, Any] = {
        "expected_epoch_id": str(EPOCH),
        "reviewed_payload_hash": configuration_hash(value.model_dump(mode="json")),
        "idempotency_key": "ack-1",
        "acknowledged": True,
    }
    command = {
        "kind": "ACKNOWLEDGE",
        "user_id": str(USER),
        "message_id": str(value.message_id),
        "request": request,
    }
    return InterventionReceipt(
        kind="ACKNOWLEDGE",
        user_id=USER,
        epoch_id=EPOCH,
        idempotency_key="ack-1",
        request_hash=configuration_hash(command),
        original_command=command,
        message_id=value.message_id,
        payload_hash=request["reviewed_payload_hash"],
        recorded_at=NOW,
        duplicate_semantics=False,
    )


def test_equivalent_worlds_and_question_do_not_depend_on_generated_revision_or_time() -> None:
    first = revision()
    second = revision(number=2)
    assert first.run_id != second.run_id
    assert first.pending_question != second.pending_question
    assert question_semantics(first) == question_semantics(second)
    assert message_identity(USER, EPOCH, question_semantics(first)) == message_identity(
        USER, EPOCH, question_semantics(second)
    )
    assert message_identity(UUID(int=99), EPOCH, question_semantics(first)) != message().message_id


def test_next_preference_and_new_economic_consequence_are_distinct_not_throttled() -> None:
    first = revision()
    second = revision(number=2, answers={"amount": "v0"})
    assert (
        second.pending_question is not None and second.pending_question.variable_id == "destination"
    )
    assert question_semantics(first) != question_semantics(second)
    assert boundary_semantics("a" * 64, "a" * 64) != boundary_semantics("a" * 64, "b" * 64)


@pytest.mark.parametrize("mutation", ["count", "unknown", "duplicate", "signature", "pending"])
def test_incomplete_or_mutated_worlds_cannot_be_equated(mutation: str) -> None:
    first = revision()
    result = first.evaluation
    if mutation == "count":
        result = result.model_copy(update={"expected_world_count": 5})
    elif mutation == "unknown":
        result = result.model_copy(update={"unknown_or_unsupported_world_count": 1})
    elif mutation == "duplicate":
        result = result.model_copy(update={"worlds": [result.worlds[0]] * len(result.worlds)})
    elif mutation == "signature":
        result = result.model_copy(update={"distinct_signatures": []})
    else:
        first = first.model_copy(update={"pending_question": None})
    with pytest.raises(ValueError):
        question_semantics(first.model_copy(update={"evaluation": result}))


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_notification_ack_requires_exact_true_boolean(value: Any) -> None:
    request = deepcopy(ack().original_command["request"])
    request["acknowledged"] = value
    with pytest.raises(ValidationError):
        AcknowledgmentRequest.model_validate_json(json.dumps(request))


@pytest.mark.parametrize("injection", ["amount_cents", "clock", "bank_authority", "payload"])
def test_observation_does_not_accept_financial_facts_or_notification_contents(
    injection: str,
) -> None:
    value = {
        "kind": "QUESTION",
        "session_id": str(SESSION),
        "expected_revision": 1,
        "expected_run_id": str(revision().run_id),
        "reviewed_source_trace_hash": "c" * 64,
        "expected_epoch_id": str(EPOCH),
        "idempotency_key": "observe-1",
        injection: 1,
    }
    with pytest.raises(ValidationError):
        QuestionObservationRequest.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("mutation", ["identity", "authority", "kind"])
def test_immutable_payload_cannot_change_identity_or_gain_authority(mutation: str) -> None:
    value = message().model_dump(mode="json")
    value[
        {"identity": "message_id", "authority": "bank_authority", "kind": "source_kind"}[mutation]
    ] = (
        str(UUID(int=99))
        if mutation == "identity"
        else True
        if mutation == "authority"
        else "SINGLE_ACTION_BOUNDARY"
    )
    with pytest.raises(ValidationError):
        InterventionMessage.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("mutation", ["hash", "key", "epoch", "owner"])
def test_receipts_are_exact_original_commands_not_success_flags(mutation: str) -> None:
    value = ack().model_dump(mode="json")
    if mutation == "hash":
        value["request_hash"] = "0" * 64
    else:
        value["original_command"]["request"][
            {"key": "idempotency_key", "epoch": "expected_epoch_id", "owner": "user_id"}[mutation]
        ] = "changed"
        value["request_hash"] = configuration_hash(value["original_command"])
    with pytest.raises(ValidationError):
        InterventionReceipt.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("source", ["STALE", "UNKNOWN", "ARCHIVED"])
def test_pending_original_cannot_present_as_current_after_source_change(source: Any) -> None:
    assert effective_state("PENDING", source_status=source) != "PENDING"
    assert effective_state("ACKNOWLEDGED", source_status=source) == "ACKNOWLEDGED"
    assert effective_state("RECORDED_ONLY", source_status=source) == "RECORDED_ONLY"


def test_no_original_ack_receipt_can_be_implied_by_outbox_state() -> None:
    outbox = row()
    outbox.state = "ACKNOWLEDGED"
    with pytest.raises(PolicyLifecycleError, match="缺原收件回执"):
        service._ack_receipt(None, outbox)


def test_ack_receipt_binding_and_nested_request_tamper_are_verified() -> None:
    outbox = row()
    outbox.state = "ACKNOWLEDGED"
    receipt = ack()
    inbox = InterventionInbox(
        id=uuid5(outbox.id, CONSUMER),
        user_id=USER,
        outbox_id=outbox.id,
        created_at=NOW,
        consumer_ref=CONSUMER,
        payload_hash=outbox.payload_hash,
        state="ACKNOWLEDGED",
        received_at=NOW,
        updated_at=NOW,
        acknowledged_at=NOW,
        acknowledgment_key=receipt.idempotency_key,
        acknowledgment_request=receipt.original_command,
        acknowledgment_request_hash=receipt.request_hash,
        original_receipt=receipt.model_dump(mode="json"),
    )
    assert service._ack_receipt(inbox, outbox) == receipt
    assert inbox.acknowledgment_request is not None
    inbox.acknowledgment_request["request"]["reviewed_payload_hash"] = "d" * 64
    with pytest.raises(PolicyLifecycleError):
        service._ack_receipt(inbox, outbox)


def test_repeated_claim_never_creates_a_second_inbox_or_reprompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = row()
    existing = InterventionInbox(
        id=uuid5(outbox.id, CONSUMER),
        user_id=USER,
        created_at=NOW,
        outbox_id=outbox.id,
        consumer_ref=CONSUMER,
        payload_hash=outbox.payload_hash,
        state="RECEIVED",
        received_at=NOW,
        updated_at=NOW,
    )
    monkeypatch.setattr(service, "_inbox", lambda *_: existing)
    session = MagicMock(spec=Session)
    value, first = service._claim(session, outbox, NOW + timedelta(seconds=1))
    assert value is existing and not first
    session.add.assert_not_called()


def test_actual_claim_original_can_recover_identity_but_never_proves_human_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = row()
    inbox = InterventionInbox(
        id=uuid5(outbox.id, CONSUMER),
        user_id=USER,
        created_at=NOW,
        outbox_id=outbox.id,
        consumer_ref=CONSUMER,
        payload_hash=outbox.payload_hash,
        state="RECEIVED",
        received_at=NOW,
        updated_at=NOW,
    )
    state = revision()
    monkeypatch.setattr(service, "_message", lambda *_: message())
    monkeypatch.setattr(service, "_inbox", lambda *_: inbox)
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *_: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    monkeypatch.setattr(
        service, "read_question_session", lambda *_: _response(state, state, None, replay=False)
    )
    session = MagicMock(spec=Session)
    original = service._view(session, outbox, NOW).original_inbox_claim
    assert original is not None
    assert original.inbox_id == inbox.id and original.message_id == outbox.id
    assert original.payload_hash == outbox.payload_hash and original.received_at == NOW
    assert original.user_id == USER and original.epoch_id == EPOCH
    assert original.consumer_ref == CONSUMER and not original.actual_human_view_verified
    session.add.assert_not_called()


@pytest.mark.parametrize("status", ["STALE_RECOMPUTATION_REQUIRED", "UNKNOWN", "CLOSED"])
def test_current_source_no_longer_pending_is_readonly_and_has_no_popup(
    status: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = row()
    state = revision()
    response = _response(state, state, None, replay=False).model_copy(
        update={
            "effective_state": status,
            "pending_question": None,
        }
    )
    monkeypatch.setattr(service, "_message", lambda *_: message())
    monkeypatch.setattr(service, "_inbox", lambda *_: None)
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *_: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    monkeypatch.setattr(service, "read_question_session", lambda *_: response)
    session = MagicMock(spec=Session)
    result = service._view(session, outbox, NOW)
    assert not result.pending and result.current_question is None
    assert result.effective_state == ("UNKNOWN" if status == "UNKNOWN" else "INVALIDATED")
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_public_invalid_body_and_unknown_query_fail_without_a_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_engine] = lambda: MagicMock()
    app.dependency_overrides[get_session] = lambda: MagicMock()
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    call = MagicMock()
    monkeypatch.setattr(service, "observe_intervention", call)
    monkeypatch.setattr(service, "acknowledge_intervention", call)
    monkeypatch.setattr(service, "list_interventions", call)
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/interventions/observe", json={"kind": "QUESTION"}).status_code
            == 422
        )
        assert client.get("/api/v1/interventions?clock=2026-01-01").status_code == 422
        assert client.get("/api/v1/interventions?limit=1&limit=2").status_code == 422
        assert (
            client.post(
                f"/api/v1/interventions/{message().message_id}/acknowledgements",
                json=ack().original_command["request"] | {"acknowledged": 1},
            ).status_code
            == 422
        )
    call.assert_not_called()


def test_writable_or_dirty_context_never_enters_readonly_service() -> None:
    session = MagicMock(spec=Session)
    session.new = [object()]
    with pytest.raises(PolicyLifecycleError):
        service.read_intervention(cast(Session, session), USER, message().message_id, NOW)
