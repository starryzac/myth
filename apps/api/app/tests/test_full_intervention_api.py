"""One isolated production intervention risk candidate; root runs PG, not formal HITL proof."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import Account
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.finite_uncertainty import AccountChoice, FiniteChoice, FinitePlanningVariable
from app.main import create_app
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_finite_uncertainty import amount_variable
from app.tests.test_full_policy_lifecycle_integration import body as full_body
from app.tests.test_full_policy_lifecycle_integration import create as create_full
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_question_workflow_api import answer_body
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
QUESTION = "/api/v1/finite-planning/sessions"
URL = "/api/v1/interventions"


def financial_snapshot(engine: Engine) -> dict[str, Any]:
    originals = json.loads(physical_snapshot(engine))
    metadata = {
        "decision_runs",
        "decision_constraints",
        "audit_events",
        "audit_epochs",
        "intervention_outbox",
        "intervention_inbox",
    }
    return {name: rows for name, rows in originals.items() if name not in metadata}


def observation_body(
    client: TestClient,
    response: dict[str, Any],
    policy: UUID,
    key: str,
) -> dict[str, Any]:
    current = response["current_revision"]
    original = client.get(f"/api/v1/decisions/{current['run_id']}")
    assert original.status_code == 200, original.text
    trace = original.json()
    assert trace["completeness"] == "COMPLETE" and trace["audit_chain_status"] == "VALID"
    return {
        "kind": "QUESTION",
        "session_id": current["session_id"],
        "expected_revision": current["revision"],
        "expected_run_id": current["run_id"],
        "reviewed_source_trace_hash": trace["trace"]["trace_hash"],
        "expected_epoch_id": current["epoch_id"],
        "intervention_policy_id": str(policy),
        "idempotency_key": key,
    }


def test_actual_persistent_question_delivery_dedup_stale_sources_and_ack_keys(
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
    policy = create_full(
        demo_engine,
        full_body(
            configuration={"type": "intervention", "minimum_reask_interval_seconds": 86400},
            key="intervention-current-settings",
        ),
    )
    base = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="intervention-original-base",
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
    start = {
        "base_action_id": str(base.action_id),
        "variables": [
            amount_variable().model_dump(mode="json"),
            destination.model_dump(mode="json"),
        ],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "intervention-question-start",
    }
    clock = [SEED_AS_OF]
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: demo_engine
    app.dependency_overrides[get_now] = lambda: clock[0]
    initial_financial = financial_snapshot(demo_engine)
    with TestClient(app) as client:
        response = client.post(QUESTION, json=start)
        assert response.status_code == 200, response.text
        first_question = response.json()
        assert first_question["effective_state"] == "PENDING_ANSWER"
        request = observation_body(client, first_question, policy.policy_id, "observe-original")
        observed = client.post(URL + "/observe", json=request)
        assert observed.status_code == 200, observed.text
        first = observed.json()
        receipt = first["original_receipt"]
        assert receipt["duplicate_semantics"] is False
        assert first["message"]["pending"] is True
        assert (
            first["message"]["original_message"]["intervention_policy_binding"]["configuration"][
                "minimum_reask_interval_seconds"
            ]
            == 86400
        )
        identity, digest = receipt["message_id"], receipt["payload_hash"]
        original_before = physical_snapshot(demo_engine)
        repeated = client.post(URL + "/observe", json=request)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["original_receipt"] == receipt
        assert physical_snapshot(demo_engine) == original_before
        semantic_duplicate = client.post(
            URL + "/observe",
            json=request
            | {
                "idempotency_key": "another-observation-same-question",
            },
        )
        assert semantic_duplicate.status_code == 200, semantic_duplicate.text
        assert semantic_duplicate.json()["original_receipt"]["duplicate_semantics"] is True
        assert semantic_duplicate.json()["original_receipt"]["message_id"] == identity
        delivery = {"expected_epoch_id": str(epoch_id), "reviewed_payload_hash": digest}
        claimed = client.post(f"{URL}/{identity}/deliveries", json=delivery)
        assert claimed.status_code == 200 and claimed.json()["present_once"] is True, claimed.text
        claimed_again = client.post(f"{URL}/{identity}/deliveries", json=delivery)
        assert claimed_again.status_code == 200 and claimed_again.json()["present_once"] is False
        assert claimed_again.json()["inbox_id"] == claimed.json()["inbox_id"]
        read_before = physical_snapshot(demo_engine)
        assert client.get(f"{URL}/{identity}").json()["previously_claimed"] is True
        lookup = client.get(f"{URL}/commands/{epoch_id}/by-key/observe-original")
        assert lookup.status_code == 200 and lookup.json()["original_receipt"] == receipt
        assert (
            client.get(f"{URL}/commands/{epoch_id}/by-key/unknown-key").json()["status"]
            == "NOT_FOUND_NOT_FINAL"
        )
        assert physical_snapshot(demo_engine) == read_before
        assert financial_snapshot(demo_engine) == initial_financial
        assert (
            client.post(
                URL + "/observe",
                json=request
                | {
                    "reviewed_source_trace_hash": "0" * 64,
                },
            ).status_code
            == 409
        )
        # Actual native external income, including its independent clearing leg.
        # It changes sources, not a fabricated planning result or notification body.
        clock[0] += timedelta(seconds=1)
        fact = ingest_external_fact(
            demo_engine,
            DEMO_USER_ID,
            ExternalFactRequest(
                user_id=DEMO_USER_ID,
                idempotency_key="intervention-new-bank-source",
                external_ref="intervention-income-1",
                kind="INCOME",
                account_id=source,
                amount_cents=1,
                counterparty_ref="payroll",
                occurred_at=clock[0],
            ),
            clock[0],
        )
        assert fact.bank_status == "SETTLED" and fact.projection_status == "PROJECTED"
        current_financial = financial_snapshot(demo_engine)
        stale = client.get(f"{URL}/{identity}")
        assert stale.status_code == 200, stale.text
        assert stale.json()["source_status"] == "STALE" and not stale.json()["pending"]
        assert client.post(f"{URL}/{identity}/deliveries", json=delivery).status_code == 409
        path = f"{QUESTION}/{first_question['current_revision']['session_id']}"
        refreshed = client.post(
            path + "/refresh",
            json={
                "expected_epoch_id": str(epoch_id),
                "expected_revision": 1,
                "idempotency_key": "question-actual-source-refresh",
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        refresh = refreshed.json()
        assert refresh["effective_state"] == "PENDING_ANSWER"
        assert refresh["current_revision"]["inherited_confirmation"] is False
        answered = client.post(path + "/answers", json=answer_body(refresh, "choose-amount"))
        assert answered.status_code == 200, answered.text
        second_question = answered.json()
        assert second_question["pending_question"]["variable_id"] == "destination"
        new_request = observation_body(
            client, second_question, policy.policy_id, "observe-new-economics"
        )
        second = client.post(URL + "/observe", json=new_request)
        assert second.status_code == 200, second.text
        new_receipt = second.json()["original_receipt"]
        assert new_receipt["message_id"] != identity
        assert not new_receipt["duplicate_semantics"] and second.json()["message"]["pending"]
        assert client.get(f"{URL}/{identity}").json()["stored_state"] == "INVALIDATED"
        second_id = new_receipt["message_id"]
        acknowledge = {
            "expected_epoch_id": str(epoch_id),
            "reviewed_payload_hash": new_receipt["payload_hash"],
            "idempotency_key": "explicit-ack-original",
            "acknowledged": True,
        }
        acknowledged = client.post(f"{URL}/{second_id}/acknowledgements", json=acknowledge)
        assert acknowledged.status_code == 200, acknowledged.text
        ack_receipt = acknowledged.json()["original_receipt"]
        assert (
            not acknowledged.json()["authority_granted"]
            and not acknowledged.json()["message"]["pending"]
        )
        assert acknowledged.json()["message"]["original_acknowledgment"] == ack_receipt
        acknowledged_before = physical_snapshot(demo_engine)
        replay = client.post(f"{URL}/{second_id}/acknowledgements", json=acknowledge)
        assert replay.status_code == 200 and replay.json()["original_receipt"] == ack_receipt
        recovered = client.get(f"{URL}/commands/{epoch_id}/by-key/explicit-ack-original")
        assert recovered.status_code == 200 and recovered.json()["original_receipt"] == ack_receipt
        assert client.get(URL).json()["actual_message_count"] == 2
        assert physical_snapshot(demo_engine) == acknowledged_before
        assert (
            client.post(
                f"{URL}/{second_id}/acknowledgements",
                json=acknowledge
                | {
                    "reviewed_payload_hash": "0" * 64,
                },
            ).status_code
            == 409
        )
        assert client.get(URL + "?clock=2030-01-01").status_code == 422
    restarted = create_app()
    restarted.dependency_overrides[get_engine] = lambda: demo_engine
    restarted.dependency_overrides[get_now] = lambda: clock[0]
    with TestClient(restarted) as client:
        recovered = client.get(f"{URL}/{second_id}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["original_acknowledgment"] == ack_receipt
        assert not recovered.json()["pending"]
    assert financial_snapshot(demo_engine) == current_financial
