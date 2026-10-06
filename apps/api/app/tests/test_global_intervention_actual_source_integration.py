"""Actual HTTP crossing must append one original notification after commit, with no money writes."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import Account, Policy, PolicyVersion
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.full_action_set_boundary_actual import ActualGlobalBoundaryObservation
from app.main import create_app
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.external_bank_facts import ingest_external_fact
from app.services.policy_lifecycle import suspend_policy
from app.tests.test_full_dynamic_goal_reserve_api import full_dynamic_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/interventions"


def financial_originals(engine: Engine) -> dict[str, Any]:
    metadata = {
        "decision_runs",
        "audit_events",
        "audit_subject_snapshots",
        "audit_heads",
        "audit_epochs",
        "intervention_outbox",
        "intervention_inbox",
    }
    return {
        name: rows
        for name, rows in json.loads(physical_snapshot(engine)).items()
        if name not in metadata
    }


def test_actual_v2_http_crossing_original_notification_delivery_ack_and_zero_money_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, _ = full_dynamic_goal(client, engine)
    model = client.get(f"/api/v1/goals/{goal_id}/full-model").json()
    assert model["status"] == "VERIFIED"
    goal = {
        "id": goal_id,
        "policy_id": model["policy_id"],
        "policy_version_id": model["base_policy_version_id"],
    }
    with Session(engine) as session, session.begin():
        for policy in session.scalars(select(Policy).where(Policy.user_id == DEMO_USER_ID)):
            version = session.scalar(
                select(PolicyVersion)
                .where(PolicyVersion.policy_id == policy.id)
                .order_by(PolicyVersion.version_number.desc())
                .limit(1)
            )
            assert version is not None
            if (
                str(policy.id) != goal["policy_id"]
                and version.configuration["type"]
                in {"recurring_obligation", "goal_saving", "asset_authorization"}
                and policy.status == "ACTIVE"
            ):
                suspend_policy(session, DEMO_USER_ID, policy.id, version.id, NOW)
        cash = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert cash is not None and epoch is not None
        cash_id, epoch_id = cash.id, epoch.id
    income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="global-notification-actual-income",
            idempotency_key="global-notification-actual-income",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    initial_body = {
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "postcommit-global-initial",
    }
    initial = client.post("/api/v1/boundary/actual-action-set/observe", json=initial_body)
    assert initial.status_code == 200, initial.text
    assert initial.headers["X-Global-Intervention-Status"] == "NOT_CROSSED"
    first = ActualGlobalBoundaryObservation.model_validate_json(initial.text)
    target = [row for row in first.snapshot.candidates if row.candidate_key == "goal:" + goal_id]
    assert len(target) == 1 and target[0].state == "INCLUDED" and target[0].amount_cents == 20000
    assert first.snapshot.algorithm_version == "full-policy-action-set-boundary-actual-v2"
    assert all(
        row.complete and row.actual_count == row.captured_count
        for row in first.snapshot.table_coverage
    )
    assert not first.bank_authority and not first.financial_write
    assert first.global_action_set_complete and first.semantic_key is None
    with Session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM intervention_outbox")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM intervention_inbox")).scalar_one() == 0
    # Distinct business instants preserve the original FIFO funding source.
    # Same-instant incomes may legitimately change economic source by UUID order.
    numeric_now = NOW + timedelta(seconds=1)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_now] = lambda: numeric_now
    numeric_income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=100000,
            external_ref="actual-v2-notification-numeric",
            idempotency_key="actual-v2-notification-numeric",
            counterparty_ref="payroll",
            occurred_at=numeric_now,
        ),
        numeric_now,
    )
    assert (
        numeric_income.bank_status == "SETTLED" and numeric_income.projection_status == "PROJECTED"
    )
    numeric_before = financial_originals(engine)
    numeric_body = {
        "expected_epoch_id": str(epoch_id),
        "previous_observation_run_id": str(first.observation_run_id),
        "idempotency_key": "actual-v2-postcommit-numeric",
    }
    numeric_http = client.post("/api/v1/boundary/actual-action-set/observe", json=numeric_body)
    assert numeric_http.status_code == 200, numeric_http.text
    assert numeric_http.headers["X-Global-Intervention-Status"] == "NOT_CROSSED"
    numeric_value = ActualGlobalBoundaryObservation.model_validate_json(numeric_http.text)
    assert numeric_value.kind == "BoundaryObserved" and not numeric_value.requires_user_attention
    assert numeric_value.snapshot.action_set_signature == first.snapshot.action_set_signature
    assert numeric_value.snapshot.financial_input_hash != first.snapshot.financial_input_hash
    with Session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM intervention_outbox")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM intervention_inbox")).scalar_one() == 0
    assert financial_originals(engine) == numeric_before
    first = numeric_value
    with Session(engine) as session, session.begin():
        suspend_policy(
            session,
            DEMO_USER_ID,
            UUID(goal["policy_id"]),
            UUID(goal["policy_version_id"]),
            numeric_now,
        )
    before_crossing = financial_originals(engine)
    crossing_body = {
        "expected_epoch_id": str(epoch_id),
        "previous_observation_run_id": str(first.observation_run_id),
        "idempotency_key": "postcommit-global-crossed",
    }
    crossed = client.post("/api/v1/boundary/actual-action-set/observe", json=crossing_body)
    assert crossed.status_code == 200, crossed.text
    assert crossed.headers["X-Global-Intervention-Status"] == "OBSERVED"
    crossing = ActualGlobalBoundaryObservation.model_validate_json(crossed.text)
    with Session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM intervention_outbox")).scalar_one() == 1
        assert session.execute(text("SELECT count(*) FROM intervention_inbox")).scalar_one() == 0
    retained_crossing = physical_snapshot(engine)
    repeated = client.post("/api/v1/boundary/actual-action-set/observe", json=crossing_body)
    assert repeated.status_code == 200 and repeated.json()["idempotent_replay"], repeated.text
    assert repeated.headers["X-Global-Intervention-Status"] == "NOT_CROSSED"
    assert physical_snapshot(engine) == retained_crossing
    assert financial_originals(engine) == before_crossing
    assert crossing.kind == "BoundaryCrossed" and crossing.global_action_set_complete
    assert crossing.requires_user_attention and crossing.semantic_key is not None
    source = client.get(f"/api/v1/decisions/{crossing.observation_run_id}")
    assert source.status_code == 200, source.text
    assert (
        source.json()["completeness"] == "COMPLETE"
        and source.json()["audit_chain_status"] == "VALID"
    )
    body = {
        "kind": "GLOBAL_ACTION_SET_BOUNDARY",
        "observation_run_id": str(crossing.observation_run_id),
        "reviewed_source_trace_hash": source.json()["trace"]["trace_hash"],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "observe-global-crossed-notification",
    }
    observed = client.post(URL + "/observe", json=body)
    assert observed.status_code == 200, observed.text
    value = observed.json()
    message, receipt = value["message"], value["original_receipt"]
    original = message["original_message"]
    assert (
        original["source_kind"] == body["kind"] and original["global_action_set_complete"] is True
    )
    assert original["boundary_observation"] == crossing.model_dump(mode="json")
    assert (
        original["boundary_observation"]["snapshot"]["algorithm_version"]
        == "full-policy-action-set-boundary-actual-v2"
    )
    assert (
        not original["bank_authority"]
        and not original["execution_eligible"]
        and not original["answers_question"]
    )
    assert message["source_status"] == "CURRENT" and message["pending"]
    assert not value["authority_granted"] and not value["execution_eligible"]
    identity, digest = receipt["message_id"], receipt["payload_hash"]
    retained = physical_snapshot(engine)
    replay = client.post(URL + "/observe", json=body)
    assert replay.status_code == 200 and replay.json()["original_receipt"] == receipt, replay.text
    assert physical_snapshot(engine) == retained
    duplicate = client.post(
        URL + "/observe", json=body | {"idempotency_key": "same-global-semantics"}
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["original_receipt"]["message_id"] == identity
    assert duplicate.json()["original_receipt"]["duplicate_semantics"] is True
    for injected in (
        "payload",
        "bank_authority",
        "global_action_set_complete",
        "amount_cents",
        "now",
    ):
        assert client.post(URL + "/observe", json=body | {injected: True}).status_code == 422
    assert (
        client.post(
            URL + "/observe", json=body | {"reviewed_source_trace_hash": "0" * 64}
        ).status_code
        == 409
    )
    delivery = {"expected_epoch_id": str(epoch_id), "reviewed_payload_hash": digest}
    first_claim = client.post(f"{URL}/{identity}/deliveries", json=delivery)
    assert first_claim.status_code == 200 and first_claim.json()["present_once"], first_claim.text
    second_claim = client.post(f"{URL}/{identity}/deliveries", json=delivery)
    assert second_claim.status_code == 200 and not second_claim.json()["present_once"], (
        second_claim.text
    )
    assert second_claim.json()["inbox_id"] == first_claim.json()["inbox_id"]
    ack = client.post(
        f"{URL}/{identity}/acknowledgements",
        json=delivery | {"acknowledged": True, "idempotency_key": "ack-global-original"},
    )
    assert ack.status_code == 200 and not ack.json()["authority_granted"], ack.text
    acknowledged = physical_snapshot(engine)
    lookup = client.get(f"{URL}/commands/{epoch_id}/by-key/ack-global-original")
    assert (
        lookup.status_code == 200
        and lookup.json()["original_receipt"] == ack.json()["original_receipt"]
    ), lookup.text
    restarted = create_app()
    restarted.dependency_overrides[get_engine] = lambda: engine
    restarted.dependency_overrides[get_now] = lambda: numeric_now
    with TestClient(restarted) as reader:
        read = reader.get(f"{URL}/{identity}")
        assert read.status_code == 200 and not read.json()["pending"], read.text
        assert read.json()["original_acknowledgment"] == ack.json()["original_receipt"]
        assert read.json()["original_inbox_claim"]["inbox_id"] == first_claim.json()["inbox_id"]
    assert physical_snapshot(engine) == acknowledged
    assert financial_originals(engine) == before_crossing
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        connection.execute(text("SET TRANSACTION READ ONLY"))
        with Session(bind=connection) as session:
            result = verify_audit_chain(session, DEMO_USER_ID, mode="EXACT")
            assert result.status == "VALID" and result.errors == []
