"""One isolated actual-PG candidate. Fixed trusted fixture clock; root runs it."""

import json
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.full_models import InterventionInbox, InterventionOutbox
from app.db.models import Account
from app.domain.finite_uncertainty import AccountChoice, FiniteChoice, FinitePlanningVariable
from app.main import create_app
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import prepare_action
from app.services.question_intervention_producer import produce_current_question_intervention
from app.services.question_workflow import (
    QuestionRefreshRequest,
    QuestionStartRequest,
    answer_question_session,
    start_question_session,
)
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_finite_uncertainty import amount_variable
from app.tests.test_full_intervention_api import financial_snapshot
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
QUESTION = "/api/v1/finite-planning/sessions"
INTERVENTION = "/api/v1/interventions"


def test_actual_postcommit_producer_refresh_same_semantics_deliver_ack_once_and_restart(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    source, target = transfer_accounts(demo_engine)
    with Session(demo_engine) as session:
        goal = session.scalar(
            select(Account.id).where(
                Account.user_id == DEMO_USER_ID, Account.account_type == "GOAL"
            )
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert goal is not None and epoch is not None
        epoch_id = epoch.id
    base = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="question-producer-original-base",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source,
                destination_account_id=target,
                amount_cents=100,
            ),
        ),
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
    body: dict[str, Any] = {
        "base_action_id": str(base.action_id),
        "variables": [
            amount_variable().model_dump(mode="json"),
            destination.model_dump(mode="json"),
        ],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "question-producer-start",
    }
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: demo_engine
    app.dependency_overrides[get_now] = lambda: SEED_AS_OF
    initial_financial = financial_snapshot(demo_engine)
    with TestClient(app) as client:
        # Invoke committed original services directly, independently of Root's
        # subsequent endpoint hook. The next explicit producer call is observable.
        started = start_question_session(
            demo_engine,
            DEMO_USER_ID,
            QuestionStartRequest.model_validate_json(json.dumps(body)),
            SEED_AS_OF,
        )
        first_state = started.current_revision.model_dump(mode="json")
        session_id = UUID(first_state["session_id"])
        first = produce_current_question_intervention(
            demo_engine, DEMO_USER_ID, session_id, SEED_AS_OF
        )
        assert first.status == "OBSERVED" and first.original_response is not None
        assert first.original_response.message.pending
        original = first.original_response.message.original_message.model_dump(mode="json")
        payload_hash = first.original_response.message.payload_hash
        message_id = first.original_response.original_receipt.message_id
        before = physical_snapshot(demo_engine)
        repeated = produce_current_question_intervention(
            demo_engine, DEMO_USER_ID, session_id, SEED_AS_OF
        )
        assert repeated.original_response is not None
        assert (
            repeated.original_response.original_receipt == first.original_response.original_receipt
        )
        assert physical_snapshot(demo_engine) == before
        refreshed = answer_question_session(
            demo_engine,
            DEMO_USER_ID,
            session_id,
            QuestionRefreshRequest(
                expected_epoch_id=epoch_id,
                expected_revision=first_state["revision"],
                idempotency_key="question-producer-same-semantics-refresh",
            ),
            SEED_AS_OF,
        )
        current = refreshed.current_revision.model_dump(mode="json")
        assert current["run_id"] != first_state["run_id"]
        # Before a new actual observation, equal semantics alone are insufficient.
        stale = client.get(f"{INTERVENTION}/{message_id}")
        assert stale.status_code == 200 and not stale.json()["pending"], stale.text
        new = produce_current_question_intervention(
            demo_engine, DEMO_USER_ID, session_id, SEED_AS_OF
        )
        assert new.status == "OBSERVED" and new.original_response is not None
        observed = new.original_response
        assert observed.original_receipt.duplicate_semantics
        assert observed.original_receipt.message_id == message_id
        assert (
            observed.message.pending
            and observed.message.current_source_binding == "CURRENT_OBSERVATION"
        )
        proof = observed.message.current_question_observation
        assert proof is not None and str(proof.source_run_id) == current["run_id"]
        assert observed.message.original_message.model_dump(mode="json") == original
        assert observed.message.payload_hash == payload_hash
        with Session(demo_engine) as session:
            assert session.scalar(select(func.count()).select_from(InterventionOutbox)) == 1
            assert session.scalar(select(func.count()).select_from(InterventionInbox)) == 0
        before_tamper = physical_snapshot(demo_engine)
        assert new.request is not None
        bad = client.post(
            INTERVENTION + "/observe",
            json=new.request.model_dump(mode="json")
            | {
                "idempotency_key": "question-producer-wrong-current-hash",
                "reviewed_source_trace_hash": first.request.reviewed_source_trace_hash
                if first.request
                else "0" * 64,
            },
        )
        assert bad.status_code == 409, bad.text
        assert physical_snapshot(demo_engine) == before_tamper
        delivery = {"expected_epoch_id": str(epoch_id), "reviewed_payload_hash": payload_hash}
        claimed = client.post(f"{INTERVENTION}/{message_id}/deliveries", json=delivery)
        assert claimed.status_code == 200 and claimed.json()["present_once"], claimed.text
        claimed_again = client.post(f"{INTERVENTION}/{message_id}/deliveries", json=delivery)
        assert claimed_again.status_code == 200 and not claimed_again.json()["present_once"], (
            claimed_again.text
        )
        assert claimed_again.json()["inbox_id"] == claimed.json()["inbox_id"]
        acknowledged = client.post(
            f"{INTERVENTION}/{message_id}/acknowledgements",
            json=delivery
            | {
                "acknowledged": True,
                "idempotency_key": "question-producer-original-ack",
            },
        )
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["message"]["effective_state"] == "ACKNOWLEDGED"
        retained = physical_snapshot(demo_engine)
        # Real immutable trigger negative. Rollback retains the complete original.
        with pytest.raises(DBAPIError, match="terminal state is immutable"):
            with demo_engine.begin() as connection:
                connection.execute(
                    text("UPDATE intervention_outbox SET state='PENDING' WHERE id=:id"),
                    {"id": message_id},
                )
        assert physical_snapshot(demo_engine) == retained
    with TestClient(app) as restarted:
        restored = restarted.get(f"{INTERVENTION}/{message_id}")
        assert restored.status_code == 200, restored.text
        assert restored.json()["original_message"] == original
        assert restored.json()["payload_hash"] == payload_hash
        assert restored.json()["previously_claimed"] and not restored.json()["pending"]
        assert restored.json()["effective_state"] == "ACKNOWLEDGED"
        lookup = restarted.get(
            f"{INTERVENTION}/commands/{epoch_id}/by-key/question-producer-original-ack"
        )
        assert (
            lookup.status_code == 200
            and lookup.json()["original_receipt"] == acknowledged.json()["original_receipt"]
        )
        assert (
            restarted.post(f"{INTERVENTION}/{message_id}/deliveries", json=delivery).status_code
            == 409
        )
        assert physical_snapshot(demo_engine) == retained
    assert financial_snapshot(demo_engine) == initial_financial
