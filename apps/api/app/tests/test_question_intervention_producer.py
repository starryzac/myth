"""Synthetic direct risks with the original deterministic worlds; no bank/PG proof."""

from copy import deepcopy
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from app.domain.decision_trace import build_trace
from app.domain.full_intervention import (
    InterventionMessage,
    InterventionReceipt,
    command_identity,
    message_identity,
)
from app.domain.policy_configuration import configuration_hash
from app.services import question_intervention_producer as service
from app.services.decision_trace import DecisionTraceResponse
from app.services.full_intervention import (
    InterventionCommandLookup,
    InterventionCommandResponse,
    InterventionView,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.question_workflow import QuestionWorkflowResponse, _response
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_question_workflow import SESSION, revision
from sqlalchemy.engine import Engine


def source(
    number: int = 1, *, answers: dict[str, str] | None = None
) -> tuple[QuestionWorkflowResponse, DecisionTraceResponse]:
    state = revision(number=number, answers=answers)
    workflow = _response(state, state, None, replay=False).model_copy(
        update={
            "effective_state": state.state,
            "pending_question": state.pending_question,
            "current_source_fingerprint": state.source_fingerprint,
            "fresh_evaluation_at": NOW,
        }
    )
    trace = build_trace(
        run_id=state.run_id,
        user_id=USER,
        phase="EVALUATION",
        as_of=state.as_of,
        parent_run_id=state.previous_run_id,
        action_id=None,
        algorithm_versions={"trace": "decision-trace-v1", "question": "full-one-question-v1"},
        inputs={},
        sources=[],
        policies=[],
        constraints=[],
        candidates=[],
        outcome={"question_revision": state.model_dump(mode="json")},
    )
    original = DecisionTraceResponse(
        user_id=USER,
        run_id=state.run_id,
        as_of=NOW,
        read_at=NOW,
        completeness="COMPLETE",
        trace=trace,
        current_references=[],
        actions=[],
        children=[],
        audit_chain_status="VALID",
    )
    return workflow, original


def prepared(number: int = 1) -> service.PreparedQuestionObservation:
    workflow, trace = source(number)
    return service.prepare_question_observation(USER, SESSION, workflow, trace)


def response(
    value: service.PreparedQuestionObservation | None = None,
) -> InterventionCommandResponse:
    value = value or prepared()
    state = revision()
    message = InterventionMessage(
        message_id=message_identity(USER, state.epoch_id, value.semantic_key),
        user_id=USER,
        epoch_id=state.epoch_id,
        source_kind="QUESTION",
        source_run_id=state.run_id,
        source_trace_hash=value.body.reviewed_source_trace_hash,
        semantic_key=value.semantic_key,
        creation_command_run_id=command_identity(USER, state.epoch_id, value.body.idempotency_key),
        session_id=SESSION,
        question_revision=1,
        question=state.pending_question,
        boundary_observation=None,
        intervention_policy_binding=None,
        requires_user_attention=True,
        created_at=NOW,
    )
    receipt = InterventionReceipt(
        kind="OBSERVE",
        user_id=USER,
        epoch_id=state.epoch_id,
        idempotency_key=value.body.idempotency_key,
        request_hash=value.request_hash,
        original_command={
            "kind": "OBSERVE",
            "user_id": str(USER),
            "request": value.body.model_dump(mode="json"),
        },
        message_id=message.message_id,
        payload_hash=configuration_hash(message.model_dump(mode="json")),
        recorded_at=NOW,
        duplicate_semantics=False,
    )
    view = InterventionView(
        original_message=message,
        payload_hash=receipt.payload_hash,
        stored_state="PENDING",
        effective_state="PENDING",
        source_status="CURRENT",
        available_at=NOW,
        pending=True,
        previously_claimed=False,
        original_inbox_claim=None,
        current_question=state.pending_question,
        original_acknowledgment=None,
    )
    return InterventionCommandResponse(
        original_receipt=receipt, message=view, replayed_original_receipt=False
    )


def test_exact_server_source_has_stable_key_and_preserves_originals() -> None:
    workflow, original = source()
    before = deepcopy((workflow.model_dump(mode="json"), original.model_dump(mode="json")))
    first = service.prepare_question_observation(USER, SESSION, workflow, original)
    second = service.prepare_question_observation(USER, SESSION, workflow, original)
    assert first == second and first.body.idempotency_key.startswith("question-producer:")
    assert len(first.body.idempotency_key) < 160
    assert first.request_hash == configuration_hash(
        {"kind": "OBSERVE", "user_id": str(USER), "request": first.body.model_dump(mode="json")}
    )
    assert (workflow.model_dump(mode="json"), original.model_dump(mode="json")) == before


def test_refresh_key_changes_but_economic_semantics_and_original_hash_do_not() -> None:
    first, second = prepared(), prepared(2)
    assert first.body.idempotency_key != second.body.idempotency_key
    assert first.question_hash != second.question_hash
    assert first.semantic_key == second.semantic_key
    assert (
        service.prepare_question_observation(
            USER, SESSION, *source(), UUID(int=999)
        ).body.idempotency_key
        == first.body.idempotency_key
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective_state", "UNKNOWN"),
        ("pending_question", None),
        ("current_source_fingerprint", "0" * 64),
        ("fresh_evaluation_at", None),
    ],
)
def test_unfresh_workflow_cannot_prepare(field: str, value: Any) -> None:
    workflow, original = source()
    with pytest.raises(PolicyLifecycleError):
        service.prepare_question_observation(
            USER, SESSION, workflow.model_copy(update={field: value}), original
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("completeness", "LEGACY_PARTIAL"),
        ("audit_chain_status", "INVALID"),
        ("trace", None),
        ("run_id", UUID(int=9)),
        ("user_id", UUID(int=9)),
        ("as_of", NOW + timedelta(seconds=1)),
    ],
)
def test_partial_wrong_owner_or_audit_trace_cannot_prepare(field: str, value: Any) -> None:
    workflow, original = source()
    with pytest.raises(PolicyLifecycleError):
        service.prepare_question_observation(
            USER, SESSION, workflow, original.model_copy(update={field: value})
        )


@pytest.mark.parametrize("mutation", ["hash", "outcome", "algorithm", "owner", "run"])
def test_changed_typed_original_trace_is_refused(mutation: str) -> None:
    workflow, original = source()
    assert original.trace is not None
    mutations: dict[str, dict[str, Any]] = {
        "hash": {"trace_hash": "0" * 64},
        "outcome": {"outcome": {}},
        "algorithm": {"algorithm_versions": {"question": "fake"}},
        "owner": {"user_id": UUID(int=9)},
        "run": {"run_id": UUID(int=9)},
    }
    updates = mutations[mutation]
    with pytest.raises(ValueError):
        service.prepare_question_observation(
            USER,
            SESSION,
            workflow,
            original.model_copy(update={"trace": original.trace.model_copy(update=updates)}),
        )


@pytest.mark.parametrize(
    "state",
    [
        "READY_FOR_REVIEW",
        "ALL_WORLDS_BLOCKED",
        "UNKNOWN",
        "STALE_RECOMPUTATION_REQUIRED",
        "ARCHIVED",
        "CLOSED",
    ],
)
def test_nonasking_never_attempts_observation(state: str, monkeypatch: pytest.MonkeyPatch) -> None:
    read = MagicMock(return_value=(state, None))
    observe = MagicMock()
    monkeypatch.setattr(service, "_read_current", read)
    monkeypatch.setattr(service, "observe_intervention", observe)
    result = service.produce_current_question_intervention(
        MagicMock(spec=Engine), USER, SESSION, NOW
    )
    assert result.status == "NOT_PENDING" and not result.observation_attempted
    observe.assert_not_called()


@pytest.mark.parametrize("changed", [None, "refresh", "fingerprint"])
def test_second_fresh_transaction_change_never_posts(
    changed: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = prepared()
    second = (
        None
        if changed is None
        else prepared(2)
        if changed == "refresh"
        else service.PreparedQuestionObservation(
            first.body, first.request_hash, first.question_hash, "0" * 64, first.semantic_key
        )
    )
    monkeypatch.setattr(
        service,
        "_read_current",
        MagicMock(side_effect=[("PENDING_ANSWER", first), ("UNKNOWN", second)]),
    )
    observe = MagicMock()
    monkeypatch.setattr(service, "observe_intervention", observe)
    result = service.produce_current_question_intervention(
        MagicMock(spec=Engine), USER, SESSION, NOW
    )
    assert result.status == "STALE_BEFORE_OBSERVE" and result.request == first.body
    observe.assert_not_called()


def test_producer_calls_only_original_observe_once_and_never_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = prepared()
    read = MagicMock(return_value=("PENDING_ANSWER", first))
    observe = MagicMock(return_value=response())
    monkeypatch.setattr(service, "_read_current", read)
    monkeypatch.setattr(service, "observe_intervention", observe)
    result = service.produce_current_question_intervention(
        MagicMock(spec=Engine), USER, SESSION, NOW
    )
    assert result.status == "OBSERVED" and result.observation_attempted
    assert not result.delivered and not result.acknowledged and not result.authority_granted
    assert not result.actual_human_view_verified and not result.execution_eligible
    assert read.call_count == 2 and observe.call_count == 1
    assert observe.call_args.args[2] == first.body


@pytest.mark.parametrize("recorded", [False, True])
def test_response_loss_only_reads_same_key_without_reposting(
    recorded: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = prepared()
    original = response()
    monkeypatch.setattr(service, "_read_current", MagicMock(return_value=("PENDING_ANSWER", first)))
    observe = MagicMock(side_effect=RuntimeError("private-dsn-must-not-be-emitted"))
    monkeypatch.setattr(service, "observe_intervention", observe)
    reader = MagicMock()
    monkeypatch.setattr(service, "_reader", reader)
    monkeypatch.setattr(service, "audit_read_scope", MagicMock())
    lookup = MagicMock(
        return_value=InterventionCommandLookup(
            status="RECORDED" if recorded else "NOT_FOUND_NOT_FINAL",
            epoch_id=first.body.expected_epoch_id,
            idempotency_key=first.body.idempotency_key,
            original_receipt=original.original_receipt if recorded else None,
            message=original.message if recorded else None,
        )
    )
    monkeypatch.setattr(service, "read_intervention_command", lookup)
    result = service.produce_current_question_intervention(
        MagicMock(spec=Engine), USER, SESSION, NOW
    )
    assert result.status == ("ORIGINAL_RECOVERED" if recorded else "OBSERVATION_OUTCOME_UNKNOWN")
    assert result.request == first.body and observe.call_count == 1
    assert lookup.call_args.args[3] == first.body.idempotency_key
    assert "private-dsn" not in result.model_dump_json()


def test_failed_source_read_does_not_mask_prior_question_receipt_or_emit_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service, "_read_current", MagicMock(side_effect=RuntimeError("password=private"))
    )
    observe = MagicMock()
    monkeypatch.setattr(service, "observe_intervention", observe)
    result = service.produce_current_question_intervention(
        MagicMock(spec=Engine), USER, SESSION, NOW
    )
    assert result.status == "SOURCE_UNVERIFIED" and result.error_code == "RuntimeError"
    assert result.request is None and "private" not in result.model_dump_json()
    observe.assert_not_called()


def test_fresh_reader_sets_and_checks_actual_ro_rr_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow, original = source()
    session = MagicMock()
    reader = MagicMock()
    reader.return_value.__enter__.return_value = session
    monkeypatch.setattr(service, "_reader", reader)
    monkeypatch.setattr(service, "audit_read_scope", MagicMock())
    snapshot = MagicMock()
    monkeypatch.setattr(service, "_snapshot", snapshot)
    monkeypatch.setattr(service, "read_question_session", lambda *_: workflow)
    monkeypatch.setattr(service, "get_decision_trace", lambda *_: original)
    result = service._read_current(MagicMock(spec=Engine), USER, SESSION, NOW, None)
    assert result[1] == prepared() and snapshot.call_count == 2


def test_original_recorded_result_with_old_source_is_explicitly_not_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, observed = prepared(), response()
    observed = observed.model_copy(
        update={
            "message": observed.message.model_copy(
                update={"source_status": "STALE", "pending": False}
            )
        }
    )
    monkeypatch.setattr(service, "_read_current", MagicMock(return_value=("PENDING_ANSWER", first)))
    monkeypatch.setattr(service, "observe_intervention", MagicMock(return_value=observed))
    result = service.produce_current_question_intervention(
        MagicMock(spec=Engine), USER, SESSION, NOW
    )
    assert result.status == "ORIGINAL_MESSAGE_NOT_CURRENT" and result.original_response == observed


@pytest.mark.parametrize("mutation", ["body", "hash", "message", "semantic"])
def test_matching_success_flags_cannot_replace_exact_original_request(mutation: str) -> None:
    first, observed = prepared(), response()
    receipt = observed.original_receipt
    if mutation == "body":
        receipt = receipt.model_copy(update={"original_command": {}})
    elif mutation == "hash":
        receipt = receipt.model_copy(update={"request_hash": "0" * 64})
    elif mutation == "message":
        receipt = receipt.model_copy(update={"message_id": UUID(int=99)})
    else:
        observed = observed.model_copy(
            update={
                "message": observed.message.model_copy(
                    update={
                        "original_message": observed.message.original_message.model_copy(
                            update={"semantic_key": "0" * 64}
                        )
                    }
                )
            }
        )
    with pytest.raises(PolicyLifecycleError):
        service._match_response(
            USER, first, observed.model_copy(update={"original_receipt": receipt})
        )
