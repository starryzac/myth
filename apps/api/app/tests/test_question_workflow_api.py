"""One isolated PG workflow risk candidate; root executes it, not formal HITL acceptance."""

import json
from typing import Any

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import Account, SimulatedBankPosting
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.finite_uncertainty import AccountChoice, FiniteChoice, FinitePlanningVariable
from app.domain.policy_configuration import configuration_hash
from app.main import create_app
from app.services.action_contracts import ConfirmActionRequest, PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_finite_uncertainty import amount_variable
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/finite-planning/sessions"


def answer_body(value: dict[str, Any], key: str) -> dict[str, Any]:
    current = value["current_revision"]
    pending = value["pending_question"]
    assert pending is not None
    return {
        "expected_epoch_id": current["epoch_id"],
        "expected_revision": current["revision"],
        "question_id": pending["question_id"],
        "choice_key": pending["choices"][0]["key"],
        "idempotency_key": key,
    }


def test_actual_question_session_restarts_rebases_and_replays_without_financial_writes(
    demo_engine: Engine,
) -> None:
    # Native seed establishes explicit payroll clearing before its first genesis.
    # The old legacy fixture deliberately omitted it; its legitimate rejection is retained.
    seed_demo(demo_engine)
    boundary_engine = demo_engine
    with Session(boundary_engine) as session:
        opening = session.scalars(
            select(SimulatedBankPosting).where(
                SimulatedBankPosting.user_id == DEMO_USER_ID,
                SimulatedBankPosting.ledger_key == "CLEARING:bounded-funds-external-v1:payroll",
                SimulatedBankPosting.sequence_number == 1,
            )
        ).one()
        assert opening.delta_cents == opening.balance_after_cents == 100000000
    source, target = transfer_accounts(boundary_engine)
    with Session(boundary_engine) as session:
        goal = session.scalar(
            select(Account.id).where(
                Account.user_id == DEMO_USER_ID, Account.account_type == "GOAL"
            )
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert goal is not None and epoch is not None
        epoch_id = epoch.id
    base = prepare_action(
        boundary_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="question-base-original",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=100,
            ),
        ),
        SEED_AS_OF,
    )
    confirm_action(
        boundary_engine,
        DEMO_USER_ID,
        base.action_id,
        ConfirmActionRequest(effect_hash=base.effect_hash, accepted=True),
        SEED_AS_OF,
    )
    destination = FinitePlanningVariable(
        variable_id="destination",
        field="TRANSFER_DESTINATION",
        choices=[
            FiniteChoice(key="cash", value=AccountChoice(kind="account", account_id=target)),
            FiniteChoice(key="goal", value=AccountChoice(kind="account", account_id=goal)),
        ],
    )
    start = {
        "base_action_id": str(base.action_id),
        "variables": [
            amount_variable().model_dump(mode="json"),
            destination.model_dump(mode="json"),
        ],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "question-start-original",
    }
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: boundary_engine
    app.dependency_overrides[get_now] = lambda: SEED_AS_OF
    before = json.loads(physical_snapshot(boundary_engine))
    with TestClient(app) as client:
        started = client.post(URL, json=start)
        assert started.status_code == 200, started.text
        first = started.json()
        state = first["current_revision"]
        path = f"{URL}/{state['session_id']}"
        assert first["effective_state"] == "PENDING_ANSWER"
        assert first["pending_question"]["variable_id"] == "amount"
        assert (
            state["evaluation"]["expected_world_count"]
            == state["evaluation"]["known_world_count"]
            == 4
        )
        assert all(
            not row["outcome"]["decision"]["confirmation_satisfied"]
            for row in state["evaluation"]["worlds"]
        )
        assert all(
            row["outcome"]["effect"]["operation_id"] != str(base.action_id)
            for row in state["evaluation"]["worlds"]
        )
        duplicate = client.post(URL, json=start)
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["original_receipt"] == first["original_receipt"]
        assert duplicate.json()["replayed_original_receipt"] is True
        second_session = client.post(URL, json=start | {"idempotency_key": "different-start"})
        assert (
            second_session.status_code == 409
            and second_session.json()["error"]["code"] == "ACTIVE_QUESTION_SESSION_EXISTS"
        )
        read_before = physical_snapshot(boundary_engine)
        restored = client.get(path)
        assert restored.status_code == 200, restored.text
        assert restored.json()["current_revision"] == state
        assert restored.json()["pending_question"] == first["pending_question"]
        assert physical_snapshot(boundary_engine) == read_before
        assert (
            client.post(
                path + "/answers",
                json=answer_body(first, "wrong-answer") | {"choice_key": "not-original"},
            ).status_code
            == 409
        )
        answered = client.post(path + "/answers", json=answer_body(first, "answer-amount"))
        assert answered.status_code == 200, answered.text
        second = answered.json()
        assert second["current_revision"]["revision"] == 2
        assert second["current_revision"]["answer_applied"] is True
        assert second["current_revision"]["previous_run_id"] == state["run_id"]
        assert second["pending_question"]["variable_id"] == "destination"
        assert second["current_revision"]["evaluation"]["known_world_count"] == 2
        retry_before = physical_snapshot(boundary_engine)
        repeated = client.post(path + "/answers", json=answer_body(first, "answer-amount"))
        assert (
            repeated.status_code == 200
            and repeated.json()["original_receipt"] == second["original_receipt"]
        )
        assert physical_snapshot(boundary_engine) == retry_before
        assert (
            client.post(path + "/answers", json=answer_body(first, "new-stale-key")).status_code
            == 409
        )
        for injection in (
            {"expected_revision": True},
            {"bank_authority": True},
            {"user_id": str(DEMO_USER_ID)},
        ):
            assert (
                client.post(
                    path + "/answers", json=answer_body(second, "bad-injection") | injection
                ).status_code
                == 422
            )
        after = json.loads(physical_snapshot(boundary_engine))
        for name in before:
            if name not in {
                "decision_runs",
                "audit_events",
                "audit_epochs",
                "audit_subject_snapshots",
            }:
                assert after[name] == before[name], name
        assert all(row in after["decision_runs"] for row in before["decision_runs"])
        assert all(row in after["audit_events"] for row in before["audit_events"])

    # New app/client actually restores the durable question; no browser storage is needed.
    restarted = create_app()
    restarted.dependency_overrides[get_engine] = lambda: boundary_engine
    restarted.dependency_overrides[get_now] = lambda: SEED_AS_OF
    with TestClient(restarted) as client:
        restored = client.get(path)
        assert restored.status_code == 200, restored.text
        assert restored.json()["current_revision"] == second["current_revision"]
        pending_answer = answer_body(restored.json(), "answer-after-new-bank-fact")
        fact = ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=source,
            kind="INCOME",
            amount_cents=1,
            counterparty_ref="payroll",
            external_ref="question-current-real-income",
            idempotency_key="question-current-real-income",
            occurred_at=SEED_AS_OF,
        )
        bank = ingest_external_fact(boundary_engine, DEMO_USER_ID, fact, SEED_AS_OF)
        assert bank.bank_status == "SETTLED" and bank.projection_status == "PROJECTED"
        assert len(bank.economic_posting_ids) == 2 and bank.transaction_id is not None
        current_originals = physical_snapshot(boundary_engine)
        stale = client.get(path)
        assert stale.status_code == 200, stale.text
        assert stale.json()["effective_state"] == "STALE_RECOMPUTATION_REQUIRED"
        assert stale.json()["pending_question"] is None
        assert physical_snapshot(boundary_engine) == current_originals
        rebased = client.post(path + "/answers", json=pending_answer)
        assert rebased.status_code == 200, rebased.text
        third = rebased.json()
        assert third["current_revision"]["command_kind"] == "REBASE"
        assert third["current_revision"]["answer_applied"] is False
        assert third["current_revision"]["answers"] == {}
        assert third["current_revision"]["revision"] == 3
        assert third["pending_question"]["variable_id"] == "amount"
        assert third["pending_question"]["question_id"] != second["pending_question"]["question_id"]
        assert (
            client.post(path + "/answers", json=pending_answer).json()["original_receipt"]
            == third["original_receipt"]
        )
        finish_one = client.post(
            path + "/answers", json=answer_body(third, "answer-rebased-amount")
        )
        assert finish_one.status_code == 200, finish_one.text
        finish_two = client.post(
            path + "/answers", json=answer_body(finish_one.json(), "answer-rebased-destination")
        )
        assert finish_two.status_code == 200, finish_two.text
        final = finish_two.json()
        assert final["effective_state"] == "READY_FOR_REVIEW" and final["pending_question"] is None
        assert final["current_revision"]["evaluation"]["known_world_count"] == 1
        assert final["authority_granted"] is False and final["execution_eligible"] is False
        assert final["old_confirmation_inherited"] is False
        assert client.get(path + "?execute=true").status_code == 422
        refresh_body = {
            "expected_epoch_id": str(epoch_id),
            "expected_revision": final["current_revision"]["revision"],
            "idempotency_key": "refresh-original",
        }
        refreshed = client.post(path + "/refresh", json=refresh_body)
        assert refreshed.status_code == 200, refreshed.text
        final = refreshed.json()
        assert final["current_revision"]["command_kind"] == "REFRESH"
        assert final["current_revision"]["answer_applied"] is False
        # Lost answer response must restore the exact original key, even after later revisions.
        lookup_before = physical_snapshot(boundary_engine)
        old_answer = client.get(path + "/commands/by-key/answer-amount")
        assert old_answer.status_code == 200, old_answer.text
        assert old_answer.json()["original_receipt"] == second["original_receipt"]
        assert old_answer.json()["current_revision"] == final["current_revision"]
        assert old_answer.json()["original_command"]["request"] == answer_body(
            first, "answer-amount"
        )
        assert old_answer.json()["request_hash"] == second["original_receipt"]["command_hash"]
        unknown_command = client.get(path + "/commands/by-key/not-recorded")
        assert unknown_command.status_code == 200
        assert unknown_command.json()["status"] == "NOT_FOUND_NOT_FINAL"
        assert unknown_command.json()["replacement_allowed"] is False
        refresh_lookup = client.get(path + "/commands/by-key/refresh-original")
        assert refresh_lookup.status_code == 200
        assert refresh_lookup.json()["original_command"]["request"] == refresh_body
        assert refresh_lookup.json()["original_receipt"] == final["original_receipt"]
        assert refresh_lookup.json()["request_hash"] == configuration_hash(
            refresh_lookup.json()["original_command"]
        )
        assert physical_snapshot(boundary_engine) == lookup_before
        # A second real base is accepted after its question; close must not re-evaluate it.
        new_base = prepare_action(
            boundary_engine,
            DEMO_USER_ID,
            PrepareActionRequest(
                idempotency_key="question-close-original-base",
                intent=TransferIntent(
                    kind="transfer_internal",
                    source_account_id=source,
                    destination_account_id=target,
                    amount_cents=100,
                ),
            ),
            SEED_AS_OF,
        )
        confirm_action(
            boundary_engine,
            DEMO_USER_ID,
            new_base.action_id,
            ConfirmActionRequest(effect_hash=new_base.effect_hash, accepted=True),
            SEED_AS_OF,
        )
        close_start = start | {
            "base_action_id": str(new_base.action_id),
            "idempotency_key": "close-session-start",
        }
        close_pending = client.post(URL, json=close_start)
        assert close_pending.status_code == 200, close_pending.text
        close_state = close_pending.json()["current_revision"]
        close_path = f"{URL}/{close_state['session_id']}"
        submitted = execute_action(boundary_engine, DEMO_USER_ID, new_base.action_id, SEED_AS_OF)
        assert submitted.status == "SUCCEEDED" and submitted.receipt is not None
        assert client.get(close_path).json()["effective_state"] == "UNKNOWN"
        close_body = {
            "expected_epoch_id": str(epoch_id),
            "expected_revision": 1,
            "idempotency_key": "close-original",
        }
        before_close_financial = json.loads(physical_snapshot(boundary_engine))
        stale_close = client.post(close_path + "/close", json=close_body | {"expected_revision": 2})
        assert stale_close.status_code == 409
        closed = client.post(close_path + "/close", json=close_body)
        assert closed.status_code == 200, closed.text
        closed_value = closed.json()
        assert (
            closed_value["effective_state"] == closed_value["current_revision"]["state"] == "CLOSED"
        )
        assert (
            closed_value["pending_question"] is None and closed_value["fresh_evaluation_at"] is None
        )
        assert closed_value["current_source_fingerprint"] is None
        assert closed_value["current_revision"]["evaluation"] == close_state["evaluation"]
        closed_originals = physical_snapshot(boundary_engine)
        after_close_financial = json.loads(closed_originals)
        for name in before_close_financial:
            if name not in {
                "decision_runs",
                "audit_events",
                "audit_epochs",
                "audit_subject_snapshots",
            }:
                assert after_close_financial[name] == before_close_financial[name], name
        close_replay = client.post(close_path + "/close", json=close_body)
        assert (
            close_replay.status_code == 200
            and close_replay.json()["original_receipt"] == closed_value["original_receipt"]
        )
        assert client.get(close_path).json()["current_revision"] == closed_value["current_revision"]
        original_start_replay = client.post(URL, json=close_start)
        assert original_start_replay.status_code == 200
        assert original_start_replay.json()["original_receipt"] == close_state
        assert original_start_replay.json()["effective_state"] == "CLOSED"
        start_lookup = client.get(f"{URL}/commands/{epoch_id}/by-start-key/close-session-start")
        assert start_lookup.status_code == 200, start_lookup.text
        assert start_lookup.json()["original_start_request"] == close_start
        assert start_lookup.json()["original_receipt"] == close_state
        assert start_lookup.json()["current_revision"] == closed_value["current_revision"]
        close_lookup = client.get(close_path + "/commands/by-key/close-original")
        assert close_lookup.status_code == 200
        assert close_lookup.json()["original_command"]["request"] == close_body
        assert close_lookup.json()["original_receipt"] == closed_value["original_receipt"]
        assert close_lookup.json()["request_hash"] == configuration_hash(
            close_lookup.json()["original_command"]
        )
        absent_start = client.get(f"{URL}/commands/{epoch_id}/by-start-key/not-recorded")
        assert (
            absent_start.status_code == 200
            and absent_start.json()["status"] == "NOT_FOUND_NOT_FINAL"
        )
        assert (
            absent_start.json()["session_id"] is None
            and absent_start.json()["replacement_allowed"] is False
        )
        conflict_close = client.post(
            close_path + "/close", json=close_body | {"expected_revision": 2}
        )
        assert conflict_close.status_code == 409
        for response in (start_lookup.json(), close_lookup.json()):
            assert (
                response["execution_eligible"] is False and response["authority_granted"] is False
            )
            assert response["replacement_allowed"] is False
        assert (
            client.post(
                close_path + "/answers", json=answer_body(close_pending.json(), "after-close")
            ).status_code
            == 409
        )
        assert (
            client.post(
                close_path + "/refresh",
                json=close_body | {"expected_revision": 2, "idempotency_key": "refresh-closed"},
            ).status_code
            == 409
        )
        assert (
            client.get(close_path + "/commands/by-key/close-original?result=success").status_code
            == 422
        )
        assert physical_snapshot(boundary_engine) == closed_originals
        # Closing releases the persistent activity slot, without reviving the accepted base.
        replacement = client.post(URL, json=start | {"idempotency_key": "new-session-after-close"})
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["effective_state"] == "PENDING_ANSWER"
        with Session(boundary_engine) as session:
            audit = current_audit_epoch(session, DEMO_USER_ID)
            assert audit is not None and audit.id == epoch_id
