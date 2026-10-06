"""Pure workflow risks with original deterministic engine fixtures; not financial observations."""

import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid5

import pytest
from app.domain.decision_trace import build_trace
from app.domain.finite_uncertainty import evaluate_finite_planning, unknown_planning
from app.domain.policy_configuration import configuration_hash
from app.domain.question_workflow import (
    CLOSE_ALGORITHM,
    MAX_REVISIONS,
    QuestionRevision,
    closed_revision,
    exact_answer,
    last_planning_reference,
    make_revision,
    restrict_fresh_worlds,
)
from app.services import question_workflow as service
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.question_workflow import (
    MAX_USER_WORKFLOW_RUNS,
    NAMESPACE,
    QuestionCommandLookupResponse,
    QuestionStartRequest,
    _all_records,
    _chain,
    _lookup_response,
    _reserve_close_capacity,
    _response,
    read_question_session,
    source_fingerprint,
)
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_finite_uncertainty import (
    account_variable,
    amount_variable,
    intent,
    original_engine,
)
from sqlalchemy.orm import Session

EPOCH = UUID(int=701)
SESSION = uuid5(NAMESPACE, f"{USER}:{EPOCH}:command-1")


def revision(*, number: int = 1, answers: dict[str, str] | None = None) -> QuestionRevision:
    variables = [amount_variable(), account_variable()]
    full = evaluate_finite_planning(intent(), variables, original_engine)
    return make_revision(
        user_id=USER,
        session_id=SESSION,
        epoch_id=EPOCH,
        revision=number,
        run_id=uuid5(SESSION, f"revision:{number}"),
        previous_run_id=uuid5(SESSION, f"revision:{number - 1}") if number > 1 else None,
        as_of=NOW,
        base_action_id=UUID(int=702),
        variables=variables,
        answers=answers or {},
        full=full,
        command_kind="START" if number == 1 else "ANSWER",
        command_key=f"command-{number}",
        command_hash="a" * 64,
        source_fingerprint="b" * 64,
        answer_applied=number > 1,
    )


def test_exact_one_question_then_fresh_filtered_worlds_end_in_review_without_consent() -> None:
    first = revision()
    assert first.state == "PENDING_ANSWER" and first.pending_question is not None
    assert first.pending_question.variable_id == "amount"
    assert first.evaluation.expected_world_count == 4
    answer = exact_answer(first, 1, first.pending_question.question_id, "v0")
    second = revision(number=2, answers=answer)
    assert (
        second.pending_question is not None and second.pending_question.variable_id == "destination"
    )
    assert second.pending_question.question_id != first.pending_question.question_id
    assert second.evaluation.expected_world_count == second.evaluation.known_world_count == 2
    assert all(
        not row.outcome.decision.confirmation_satisfied
        for row in second.evaluation.worlds
        if row.outcome.decision
    )
    final = revision(
        number=3, answers=exact_answer(second, 2, second.pending_question.question_id, "v0")
    )
    assert final.state == "READY_FOR_REVIEW" and final.pending_question is None
    assert final.evaluation.expected_world_count == final.evaluation.known_world_count == 1
    assert (
        not final.inherited_confirmation
        and not final.execution_eligible
        and not final.authority_granted
    )
    assert not final.old_candidates_execution_eligible


@pytest.mark.parametrize("mutation", ["revision", "question", "choice"])
def test_stale_or_unregistered_answer_is_refused(mutation: str) -> None:
    first = revision()
    assert first.pending_question is not None
    with pytest.raises(ValueError):
        exact_answer(
            first,
            2 if mutation == "revision" else 1,
            UUID(int=999) if mutation == "question" else first.pending_question.question_id,
            "non-original" if mutation == "choice" else "v0",
        )


def test_completed_question_never_accepts_another_answer() -> None:
    final = revision(number=3, answers={"amount": "v0", "destination": "v0"})
    with pytest.raises(ValueError):
        exact_answer(final, 3, UUID(int=999), "v0")


@pytest.mark.parametrize(
    "mutation", ["delete_world", "duplicate_world", "unknown_variable", "unknown_choice"]
)
def test_complete_declared_world_inventory_cannot_be_truncated_or_changed(mutation: str) -> None:
    variables = [amount_variable(), account_variable()]
    full = evaluate_finite_planning(intent(), variables, original_engine)
    if mutation == "delete_world":
        full = full.model_copy(update={"worlds": full.worlds[:-1]})
    elif mutation == "duplicate_world":
        full = full.model_copy(
            update={"worlds": [full.worlds[0], *full.worlds[1:-1], full.worlds[0]]}
        )
    answers = (
        {"unknown": "v0"}
        if mutation == "unknown_variable"
        else {"amount": "unknown"}
        if mutation == "unknown_choice"
        else {}
    )
    with pytest.raises(ValueError):
        restrict_fresh_worlds(full, variables, answers)


def test_unknown_keeps_full_denominator_and_never_constructs_an_empty_success() -> None:
    variables = [amount_variable()]
    result = restrict_fresh_worlds(
        unknown_planning(variables, ["ACTUAL_CONTEXT_MISSING"]), variables, {}
    )
    assert result.status == "UNKNOWN" and result.should_ask is None
    assert result.expected_world_count == result.unknown_or_unsupported_world_count == 2
    assert result.known_world_count == result.evaluated_world_count == 0
    assert result.question is None


def test_answer_does_not_mutate_original_full_worlds_or_prior_receipt() -> None:
    first = revision()
    saved = deepcopy(first.model_dump(mode="json"))
    assert first.pending_question is not None
    selected = exact_answer(first, 1, first.pending_question.question_id, "v0")
    revision(number=2, answers=selected)
    assert first.model_dump(mode="json") == saved


def raw_basis() -> dict[str, Any]:
    return {
        "autonomy_basis": {
            "as_of": NOW.isoformat(),
            "snapshot": {
                "as_of": NOW.isoformat(),
                "cash_accounts": [{"balance_cents": 1000, "observed_at": NOW.isoformat()}],
                "living_reserves": [{"amount_cents": 100, "estimation_input_digest": "clock-1"}],
            },
            "exposure": {"as_of": NOW.isoformat(), "pending_purchase_cents": 0},
            "income": {"as_of": NOW.isoformat(), "origins": []},
        },
        "local_date": "2026-10-04",
        "epoch_id": str(EPOCH),
    }


def test_only_recomputation_clock_changes_are_ignored_not_original_cash_or_observation_time() -> (
    None
):
    raw = raw_basis()
    before = deepcopy(raw)
    later = deepcopy(raw)
    later["autonomy_basis"]["as_of"] = (NOW + timedelta(seconds=1)).isoformat()
    later["autonomy_basis"]["snapshot"]["as_of"] = later["autonomy_basis"]["as_of"]
    later["autonomy_basis"]["snapshot"]["living_reserves"][0]["estimation_input_digest"] = "clock-2"
    later["autonomy_basis"]["income"]["as_of"] = later["autonomy_basis"]["as_of"]
    later["autonomy_basis"]["exposure"]["as_of"] = later["autonomy_basis"]["as_of"]
    assert source_fingerprint(raw, [], []) == source_fingerprint(later, [], [])
    for key, value in (("balance_cents", 1001), ("observed_at", later["autonomy_basis"]["as_of"])):
        changed = deepcopy(raw)
        changed["autonomy_basis"]["snapshot"]["cash_accounts"][0][key] = value
        assert source_fingerprint(raw, [], []) != source_fingerprint(changed, [], [])
    assert raw == before


def test_missing_or_duplicate_revision_or_wrong_parent_cannot_restore_pending() -> None:
    records = []
    original_question = revision().pending_question
    assert original_question is not None
    for number in (1, 2):
        state = revision(number=number, answers={} if number == 1 else {"amount": "v0"})
        trace = build_trace(
            run_id=state.run_id,
            user_id=USER,
            phase="EVALUATION",
            as_of=NOW,
            parent_run_id=state.previous_run_id,
            algorithm_versions={"trace": "decision-trace-v1"},
            inputs={
                "original_command": {
                    "kind": "START" if number == 1 else "ANSWER",
                    "user_id": str(USER),
                    **({"session_id": str(SESSION)} if number > 1 else {}),
                    "request": {
                        "expected_epoch_id": str(EPOCH),
                        "idempotency_key": f"command-{number}",
                        **(
                            {
                                "base_action_id": str(state.base_action_id),
                                "variables": [
                                    row.model_dump(mode="json") for row in state.variables
                                ],
                            }
                            if number == 1
                            else {
                                "expected_revision": 1,
                                "question_id": str(original_question.question_id),
                                "choice_key": "v0",
                            }
                        ),
                    },
                }
            },
            outcome={},
        )
        records.append((trace, state))
    assert _chain(records, SESSION)[-1][1].revision == 2
    for invalid in (
        [records[1]],
        [records[0], records[0]],
        [
            (records[0][0], records[0][1]),
            (records[1][0].model_copy(update={"parent_run_id": UUID(int=999)}), records[1][1]),
        ],
    ):
        with pytest.raises(PolicyLifecycleError, match="revision"):
            _chain(invalid, SESSION)


@pytest.mark.parametrize("violation", ["new", "dirty", "deleted", "isolation", "read_only"])
def test_refresh_get_refuses_dirty_or_non_rr_ro_session(violation: str) -> None:
    session = MagicMock(spec=Session)
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    session.connection.return_value.get_isolation_level.return_value = "REPEATABLE READ"
    session.scalar.return_value = "on"
    if violation in {"new", "dirty", "deleted"}:
        setattr(session, violation, {object()})
    elif violation == "isolation":
        session.connection.return_value.get_isolation_level.return_value = "READ COMMITTED"
    else:
        session.scalar.return_value = "off"
    with pytest.raises(PolicyLifecycleError) as error:
        read_question_session(session, USER, SESSION, NOW)
    assert error.value.code == "INVALID_READ_SNAPSHOT"


def original_records() -> list[tuple[Any, QuestionRevision]]:
    state = revision()
    body = QuestionStartRequest(
        base_action_id=state.base_action_id,
        variables=state.variables,
        expected_epoch_id=EPOCH,
        idempotency_key=state.command_key,
    )
    command = {"kind": "START", "user_id": str(USER), "request": body.model_dump(mode="json")}
    state = state.model_copy(update={"command_hash": configuration_hash(command)})
    trace = build_trace(
        run_id=state.run_id,
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        algorithm_versions={"trace": "decision-trace-v1"},
        inputs={"original_command": command},
        outcome={},
    )
    command2 = {
        "kind": "CLOSE",
        "user_id": str(USER),
        "session_id": str(SESSION),
        "request": {
            "expected_epoch_id": str(EPOCH),
            "expected_revision": 1,
            "idempotency_key": "close-original",
        },
    }
    closed = closed_revision(
        state, as_of=NOW, command_key="close-original", command_hash=configuration_hash(command2)
    )
    trace2 = build_trace(
        run_id=closed.run_id,
        user_id=USER,
        phase="EVALUATION",
        as_of=NOW,
        parent_run_id=state.run_id,
        algorithm_versions={"trace": "decision-trace-v1", "question": CLOSE_ALGORITHM},
        inputs={
            "question_workflow": "full-one-question-v1",
            "session_id": str(SESSION),
            "original_command": command2,
        },
        outcome={
            "question_revision": closed.model_dump(mode="json"),
            "last_planning_reference": last_planning_reference(state),
        },
    )
    return [(trace, state), (trace2, closed)]


def test_close_preserves_original_planning_and_never_claims_fresh_or_authority() -> None:
    records = original_records()
    saved = records[0][1].model_dump(mode="json")
    closed = _chain(records, SESSION)[-1][1]
    result = _response(closed, closed, None, replay=False)
    assert result.effective_state == "CLOSED" and result.pending_question is None
    assert result.fresh_evaluation_at is None and result.current_source_fingerprint is None
    assert closed.evaluation == records[0][1].evaluation and closed.answers == records[0][1].answers
    assert records[0][1].model_dump(mode="json") == saved
    assert not result.authority_granted and not result.execution_eligible
    pending = records[0][1].pending_question
    assert pending is not None
    with pytest.raises(ValueError):
        exact_answer(closed, 2, pending.question_id, "v0")
    with pytest.raises(ValueError):
        closed_revision(closed, as_of=NOW, command_key="another-close", command_hash="a" * 64)


def test_close_has_a_reserved_final_revision_even_when_questions_hit_capacity() -> None:
    previous = revision().model_copy(
        update={"revision": MAX_REVISIONS, "run_id": uuid5(SESSION, f"revision:{MAX_REVISIONS}")}
    )
    closed = closed_revision(
        previous, as_of=NOW, command_key="capacity-close", command_hash="a" * 64
    )
    assert closed.revision == MAX_REVISIONS + 1 and closed.state == "CLOSED"
    assert QuestionRevision.model_validate_json(closed.model_dump_json()) == closed


def test_user_read_capacity_reserves_one_explicit_close_receipt_instead_of_truncating() -> None:
    row = original_records()[0]
    _reserve_close_capacity([row] * (MAX_USER_WORKFLOW_RUNS - 2))
    with pytest.raises(PolicyLifecycleError) as error:
        _reserve_close_capacity([row] * (MAX_USER_WORKFLOW_RUNS - 1))
    assert error.value.code == "QUESTION_WORKFLOW_CAPACITY_EXCEEDED"


@pytest.mark.parametrize("mutation", ["evaluation", "answers", "source", "reference", "applied"])
def test_close_cannot_rewrite_previous_planning_or_claim_answer_applied(mutation: str) -> None:
    records = original_records()
    trace, state = records[1]
    if mutation == "reference":
        trace = trace.model_copy(
            update={"outcome": {**trace.outcome, "last_planning_reference": {"run_id": "wrong"}}}
        )
    else:
        update: dict[str, Any] = (
            {"evaluation": state.evaluation.model_copy(update={"known_world_count": 0})}
            if mutation == "evaluation"
            else {"answers": {"amount": "v0"}}
            if mutation == "answers"
            else {"source_fingerprint": "c" * 64}
            if mutation == "source"
            else {"answer_applied": True}
        )
        state = state.model_copy(update=update)
    with pytest.raises(PolicyLifecycleError):
        _chain([records[0], (trace, state)], SESSION)


def test_lookup_restores_exact_original_command_not_later_closed_state() -> None:
    records = original_records()
    start = _lookup_response(records, USER, EPOCH, "command-1", None)
    close = _lookup_response(records, USER, EPOCH, "close-original", SESSION)
    assert start.status == close.status == "RECORDED"
    assert start.original_receipt == records[0][1] and start.current_revision == records[1][1]
    assert start.original_start_request is not None
    assert start.original_command == records[0][0].inputs["original_command"]
    assert close.original_receipt == records[1][1] and close.original_start_request is None
    absent = _lookup_response(records, USER, EPOCH, "not-recorded", SESSION)
    foreign = _lookup_response(records, USER, UUID(int=999), "command-1", None)
    assert absent.status == foreign.status == "NOT_FOUND_NOT_FINAL"
    assert absent.original_receipt is None and not absent.replacement_allowed
    assert not close.execution_eligible and close.client_match_required
    broken = close.model_dump(mode="json")
    broken["request_hash"] = "f" * 64
    with pytest.raises(ValueError):
        QuestionCommandLookupResponse.model_validate_json(json.dumps(broken))


@pytest.mark.parametrize("broken", ["none", "anchor", "completeness", "audit"])
def test_one_complete_audit_per_records_call_still_checks_every_anchor_and_run(
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
) -> None:
    _, closed = original_records()[1]
    trace = original_records()[1][0]
    other = closed.model_copy(update={"run_id": UUID(int=998)})
    trace2 = trace.model_copy(
        update={
            "run_id": other.run_id,
            "outcome": {**trace.outcome, "question_revision": other.model_dump(mode="json")},
        }
    )
    rows = [
        SimpleNamespace(id=closed.run_id, as_of=NOW),
        SimpleNamespace(id=other.run_id, as_of=NOW),
    ]
    session = MagicMock(spec=Session)
    session.scalars.side_effect = [
        rows,
        [closed.run_id] if broken == "anchor" else [closed.run_id, other.run_id],
    ]
    monkeypatch.setattr(service, "_snapshot", lambda value: None)
    monkeypatch.setattr(service, "current_audit_epoch", lambda *args: SimpleNamespace(id=EPOCH))
    audit = MagicMock(
        return_value=SimpleNamespace(status="INTEGRITY_ERROR" if broken == "audit" else "VALID")
    )
    monkeypatch.setattr(service, "verify_audit_chain", audit)
    read = MagicMock(
        side_effect=[
            ("UNSUPPORTED_VERSION" if broken == "completeness" else "COMPLETE", trace),
            ("COMPLETE", trace2),
        ]
    )
    monkeypatch.setattr(service, "_stored_trace", read)
    if broken != "none":
        with pytest.raises(PolicyLifecycleError):
            _all_records(session, USER, NOW)
    else:
        assert len(_all_records(session, USER, NOW)) == read.call_count == 2
    assert audit.call_count == 1
