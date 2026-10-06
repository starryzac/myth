"""Pure same-semantic current-source risks; fixtures are not observed financial results."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.db.full_models import InterventionInbox
from app.domain.decision_trace import build_trace
from app.domain.full_intervention import CONSUMER, InterventionReceipt, command_identity
from app.domain.question_workflow import make_revision
from app.services import full_intervention as service
from app.services import question_intervention_producer as producer
from app.services.decision_trace import DecisionTraceResponse
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.question_workflow import _response
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_full_intervention import row
from app.tests.test_question_intervention_producer import prepared, response, source
from app.tests.test_question_workflow import EPOCH
from sqlalchemy.orm import Session


def setup_current(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, dict[str, Any]]:
    first = response()
    second = prepared(2)
    workflow, original = source(2)
    assert original.trace is not None
    receipt = InterventionReceipt(
        kind="OBSERVE",
        user_id=USER,
        epoch_id=EPOCH,
        idempotency_key=second.body.idempotency_key,
        request_hash=second.request_hash,
        original_command={
            "kind": "OBSERVE",
            "user_id": str(USER),
            "request": second.body.model_dump(mode="json"),
        },
        message_id=first.original_receipt.message_id,
        payload_hash=first.original_receipt.payload_hash,
        recorded_at=NOW,
        duplicate_semantics=True,
    )
    identity = command_identity(USER, EPOCH, second.body.idempotency_key)
    trace = build_trace(
        run_id=identity,
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        parent_run_id=second.body.expected_run_id,
        action_id=None,
        algorithm_versions={"trace": "decision-trace-v1", "intervention": "full-intervention-v1"},
        inputs={
            "original_command": receipt.original_command,
            "source_trace_hash": second.body.reviewed_source_trace_hash,
        },
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={
            "receipt": receipt.model_dump(mode="json"),
            "message": first.message.original_message.model_dump(mode="json"),
        },
    )
    traces = {identity: trace, original.run_id: original.trace}
    monkeypatch.setattr(service, "_trace", lambda _s, _u, i, _n: traces[i])
    monkeypatch.setattr(service, "_message", lambda *_: first.message.original_message)
    monkeypatch.setattr(service, "_inbox", lambda *_: None)
    monkeypatch.setattr(service, "read_question_session", lambda *_: workflow)
    monkeypatch.setattr(
        service, "current_audit_epoch", lambda *_: SimpleNamespace(id=EPOCH, status="OPEN")
    )
    session = MagicMock(spec=Session)
    session.scalar.return_value = identity
    session.scalars.return_value = [identity]
    return session, {
        "workflow": workflow,
        "source": original,
        "command": trace,
        "receipt": receipt,
        "original": first,
        "traces": traces,
        "identity": identity,
    }


def test_current_original_observation_replaces_source_binding_without_rewriting_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, data = setup_current(monkeypatch)
    outbox = row()
    old = deepcopy((outbox.payload, outbox.source_run_id, outbox.payload_hash, outbox.state))
    view = service._view(session, outbox, NOW)
    proof = view.current_question_observation
    assert view.source_status == "CURRENT" and view.pending
    assert view.current_source_binding == "CURRENT_OBSERVATION" and proof is not None
    assert proof.source_run_id == data["workflow"].current_revision.run_id
    assert proof.source_run_id != view.original_message.source_run_id
    assert proof.current_question == view.current_question != view.original_message.question
    assert proof.original_receipt == data["receipt"] and not proof.authority_granted
    assert (outbox.payload, outbox.source_run_id, outbox.payload_hash, outbox.state) == old
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_without_new_original_observation_equal_economic_semantics_remain_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup_current(monkeypatch)
    session.scalars.return_value = []
    view = service._view(session, row(), NOW)
    assert view.source_status == "STALE" and not view.pending
    assert view.current_question_observation is None


@pytest.mark.parametrize(
    "mutation", ["parent", "source_hash", "receipt_hash", "revision", "owner", "message", "missing"]
)
def test_changed_observation_or_source_is_never_current(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, data = setup_current(monkeypatch)
    trace = data["command"]
    if mutation == "parent":
        trace = trace.model_copy(update={"parent_run_id": UUID(int=999)})
    elif mutation == "source_hash":
        trace = trace.model_copy(update={"inputs": trace.inputs | {"source_trace_hash": "0" * 64}})
    elif mutation == "receipt_hash":
        trace = trace.model_copy(
            update={
                "outcome": trace.outcome
                | {"receipt": data["receipt"].model_dump(mode="json") | {"request_hash": "0" * 64}}
            }
        )
    elif mutation in {"revision", "owner"}:
        source_trace = data["source"].trace
        assert source_trace is not None
        state = deepcopy(source_trace.outcome["question_revision"])
        state["revision" if mutation == "revision" else "user_id"] = (
            3 if mutation == "revision" else str(UUID(int=999))
        )
        data["traces"][data["source"].run_id] = source_trace.model_copy(
            update={"outcome": {"question_revision": state}}
        )
    elif mutation == "message":
        trace = trace.model_copy(update={"outcome": trace.outcome | {"message": {}}})
    else:
        session.scalar.return_value = None
    data["traces"][data["identity"]] = trace
    with pytest.raises(PolicyLifecycleError):
        service._view(session, row(), NOW)
    session.add.assert_not_called()


def test_new_economic_question_cannot_reuse_older_semantic_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup_current(monkeypatch)
    different, _ = source(2, answers={"amount": "v0"})
    monkeypatch.setattr(service, "read_question_session", lambda *_: different)
    view = service._view(session, row(), NOW)
    assert view.source_status == "STALE" and not view.pending
    session.add.assert_not_called()


def test_current_proof_inventory_overflow_refuses_truncated_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, data = setup_current(monkeypatch)
    session.scalars.return_value = [data["identity"]] * (service.MAX_MESSAGES + 1)
    with pytest.raises(PolicyLifecycleError, match="超过512"):
        service._view(session, row(), NOW)


def test_legacy_invalidated_row_is_not_revived_by_new_valid_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup_current(monkeypatch)
    outbox = row()
    outbox.state = "INVALIDATED"
    outbox.invalidation_reason = "QUESTION_REVISION_REPLACED"
    view = service._view(session, outbox, NOW)
    assert view.current_question_observation is not None and view.source_status == "CURRENT"
    assert view.current_source_binding == "LEGACY_TERMINAL_SOURCE"
    assert view.stored_state == view.effective_state == "INVALIDATED" and not view.pending
    assert outbox.state == "INVALIDATED"


def test_one_way_invalidated_consumer_cannot_be_claimed_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = row()
    existing = InterventionInbox(
        id=uuid5(outbox.id, CONSUMER),
        user_id=USER,
        outbox_id=outbox.id,
        consumer_ref=CONSUMER,
        payload_hash=outbox.payload_hash,
        state="INVALIDATED",
        created_at=NOW,
        received_at=NOW,
        updated_at=NOW,
    )
    monkeypatch.setattr(service, "_inbox", lambda *_: existing)
    monkeypatch.setattr(service, "_ack_receipt", lambda *_: None)
    session = MagicMock(spec=Session)
    claimed, present = service._claim(session, outbox, NOW)
    assert claimed is existing and not present
    assert existing.state == "INVALIDATED"
    session.add.assert_not_called()


def test_current_equivalent_session_is_proven_independently_from_payload_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, data = setup_current(monkeypatch)
    old = data["workflow"].current_revision
    new_session = UUID(int=99001)
    new_state = make_revision(
        user_id=USER,
        session_id=new_session,
        epoch_id=EPOCH,
        revision=1,
        run_id=uuid5(new_session, "revision:1"),
        previous_run_id=None,
        as_of=NOW,
        base_action_id=old.base_action_id,
        variables=old.variables,
        answers={},
        full=old.evaluation,
        command_kind="START",
        command_key="new-session-command",
        command_hash="e" * 64,
        source_fingerprint=old.source_fingerprint,
        answer_applied=False,
    )
    current = _response(new_state, new_state, None, replay=False).model_copy(
        update={
            "effective_state": "PENDING_ANSWER",
            "pending_question": new_state.pending_question,
            "current_source_fingerprint": new_state.source_fingerprint,
            "fresh_evaluation_at": NOW,
        }
    )
    source_trace = build_trace(
        run_id=new_state.run_id,
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        parent_run_id=None,
        action_id=None,
        algorithm_versions={"trace": "decision-trace-v1", "question": "full-one-question-v1"},
        inputs={},
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={"question_revision": new_state.model_dump(mode="json")},
    )
    original = DecisionTraceResponse(
        user_id=USER,
        run_id=new_state.run_id,
        as_of=NOW,
        read_at=NOW,
        completeness="COMPLETE",
        trace=source_trace,
        current_references=[],
        actions=[],
        children=[],
        audit_chain_status="VALID",
    )
    request = producer.prepare_question_observation(USER, new_session, current, original)
    receipt = data["receipt"].model_copy(
        update={
            "idempotency_key": request.body.idempotency_key,
            "request_hash": request.request_hash,
            "original_command": {
                "kind": "OBSERVE",
                "user_id": str(USER),
                "request": request.body.model_dump(mode="json"),
            },
        }
    )
    identity = command_identity(USER, EPOCH, request.body.idempotency_key)
    trace = build_trace(
        run_id=identity,
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        parent_run_id=new_state.run_id,
        action_id=None,
        algorithm_versions={"trace": "decision-trace-v1", "intervention": "full-intervention-v1"},
        inputs={
            "original_command": receipt.original_command,
            "source_trace_hash": source_trace.trace_hash,
        },
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={
            "receipt": receipt.model_dump(mode="json"),
            "message": data["original"].message.original_message.model_dump(mode="json"),
        },
    )
    data["traces"].update({identity: trace, new_state.run_id: source_trace})
    session.scalar.return_value = identity
    session.scalars.return_value = [identity]
    ended = data["workflow"].model_copy(
        update={"effective_state": "CLOSED", "pending_question": None}
    )
    monkeypatch.setattr(
        service,
        "read_question_session",
        lambda _s, _u, s, _n: current if s == new_session else ended,
    )
    view = service._view(session, row(), NOW)
    proof = view.current_question_observation
    assert view.source_status == "CURRENT" and view.pending and proof is not None
    assert proof.session_id == new_session != view.original_message.session_id
    assert proof.current_question == view.current_question == new_state.pending_question
    assert proof.semantic_key == view.original_message.semantic_key
    session.add.assert_not_called()
