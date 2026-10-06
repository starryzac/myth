"""Recovery HTTP contract against PostgreSQL, without client-supplied money or authority."""

from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.api.dependencies import get_now
from app.db.models import (
    Account,
    AssetPosition,
    DecisionRun,
    EvidenceItem,
    SimulatedBankPosting,
    User,
)
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import SEED_AS_OF
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_goal_api import NOW, all_tables
from app.tests.test_goal_api import goal_client as goal_client
from app.tests.test_recovery_service import recovery_fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_recovery_preview_is_read_only_and_keeps_actual_and_projected_results_separate(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    response = client.get("/api/v1/recovery/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["simulation"] is True and body["user_id"] == str(DEMO_USER_ID)
    assert body["plan"]["preview_only"] is True
    assert body["plan"]["status"] == "NO_RECOVERY_NEEDED"
    assert body["plan"]["steps"] == []
    assert body["plan"]["actual_boundary"]["status"] == "READY"
    assert body["plan"]["actual_boundary"]["safe_idle_cents"] == 3157400
    assert client.get("/api/v1/recovery/preview").json() == body
    assert all_tables(engine) == before


def test_recovery_rejects_untrusted_override_fields_and_invalid_keys_without_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    for query in (
        "as_of=2027-01-01T00:00:00Z",
        "amount_cents=50000",
        "autonomy_level=AUTO_EXECUTE",
        f"position_id={uuid4()}",
        f"user_id={uuid4()}",
    ):
        assert client.get(f"/api/v1/recovery/preview?{query}").status_code == 422
        assert client.get(f"/api/v1/recovery/runs/{uuid4()}?{query}").status_code == 422
        assert (
            client.post(
                f"/api/v1/recovery/runs?{query}", json={"idempotency_key": "invalid-query"}
            ).status_code
            == 422
        )
    for key in (None, "", "   ", 123, True, "x" * 161):
        assert (
            client.post("/api/v1/recovery/runs", json={"idempotency_key": key}).status_code == 422
        )
    assert client.post("/api/v1/recovery/runs", json={}).status_code == 422
    for field, value in (
        ("amount_cents", 50000),
        ("position_id", str(uuid4())),
        ("user_id", str(uuid4())),
        ("as_of", "2027-01-01T00:00:00Z"),
        ("accepted", True),
        ("autonomy_level", "AUTO_EXECUTE"),
    ):
        assert (
            client.post(
                "/api/v1/recovery/runs", json={"idempotency_key": "invalid-body", field: value}
            ).status_code
            == 422
        )
    assert all_tables(engine) == before


def test_no_recovery_run_is_queryable_and_same_key_does_not_create_an_economic_effect(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    money = client.get("/api/v1/accounts/summary").json()
    positions = client.get("/api/v1/positions").json()
    response = client.post("/api/v1/recovery/runs", json={"idempotency_key": "no-risk-run"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NO_RECOVERY_NEEDED"
    assert body["actions"] == []
    assert body["simulation"] is True and body["user_id"] == str(DEMO_USER_ID)
    before_repeat = all_tables(engine)
    assert client.get(f"/api/v1/recovery/runs/{body['run_id']}").json() == body
    assert (
        client.post("/api/v1/recovery/runs", json={"idempotency_key": "no-risk-run"}).json() == body
    )
    assert all_tables(engine) == before_repeat
    assert client.get("/api/v1/accounts/summary").json() == money
    assert client.get("/api/v1/positions").json() == positions


def test_recovery_unknown_run_returns_the_standard_not_found_envelope(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    response = client.get(f"/api/v1/recovery/runs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["x-request-id"]
    assert all_tables(engine) == before


def test_recovery_cannot_read_another_users_run(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    other_id, run_id = uuid4(), uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            User(id=other_id, external_ref=f"recovery-other:{other_id}", display_name="Other")
        )
        session.flush()
        session.add(
            DecisionRun(
                id=run_id,
                user_id=other_id,
                idempotency_key="foreign-recovery-run",
                trigger_type="SAFETY_RECOVERY",
                algorithm_version="whole-position-recovery-v1",
                as_of=NOW,
                input_snapshot={},
                snapshot_hash=configuration_hash({}),
            )
        )
    before = all_tables(engine)
    response = client.get(f"/api/v1/recovery/runs/{run_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert all_tables(engine) == before


def test_recovery_maximum_length_key_is_replayable_without_database_overflow(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    payload = {"idempotency_key": "r" * 160}
    first = client.post("/api/v1/recovery/runs", json=payload)
    assert first.status_code == 200
    before = all_tables(engine)
    assert client.post("/api/v1/recovery/runs", json=payload).json() == first.json()
    assert all_tables(engine) == before


@pytest.mark.parametrize("change", ["new_protection", "missing_balance_proof"])
def test_historical_no_action_run_does_not_describe_new_risk_as_no_recovery_needed(
    goal_client: tuple[TestClient, Engine], change: str
) -> None:
    client, engine = goal_client
    payload = {"idempotency_key": "historical-no-risk"}
    initial = client.post("/api/v1/recovery/runs", json=payload)
    assert initial.status_code == 200
    run_id = initial.json()["run_id"]
    assert initial.json()["status"] == "NO_RECOVERY_NEEDED"
    with Session(engine) as session, session.begin():
        if change == "new_protection":
            confirmed_policy(session, {"type": "emergency_buffer", "amount_cents": 3187400})
            expected_status, expected_boundary = "NO_SAFE_RECOVERY", "LIQUIDITY_RISK"
        else:
            proof = session.scalars(
                select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_BANK_BALANCE")
            ).first()
            assert proof is not None
            session.delete(proof)
            expected_status = expected_boundary = "INSUFFICIENT_EVIDENCE"
    before = all_tables(engine)
    current = client.get(f"/api/v1/recovery/runs/{run_id}")
    assert current.status_code == 200
    body = current.json()
    assert body["status"] == expected_status
    assert body["actual_boundary"]["status"] == expected_boundary
    assert body["plan"]["status"] == "NO_RECOVERY_NEEDED"
    assert body["actions"] == []
    assert client.post("/api/v1/recovery/runs", json=payload).json() == body
    assert all_tables(engine) == before


def test_recovery_independent_bank_detects_consistently_modified_application_balance(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                EvidenceItem.content["account_id"].as_string() == str(account.id),
            )
        )
        assert proof is not None
        account.balance_cents += 50000
        proof.content = {**proof.content, "balance_cents": account.balance_cents}
        proof.content_hash = configuration_hash(proof.content)
    before = all_tables(engine)
    response = client.get("/api/v1/recovery/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert body["plan"]["steps"] == []
    assert "BANK_RECONCILIATION_REQUIRED" in {row["code"] for row in body["source_issues"]}
    assert all_tables(engine) == before


def test_http_t0_recovery_returns_bank_receipt_and_changes_cash_exactly_once(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    cast(FastAPI, client.app).dependency_overrides[get_now] = lambda: SEED_AS_OF
    _, position_id, principal = recovery_fixture(engine)
    before_preview = all_tables(engine)
    preview = client.get("/api/v1/recovery/preview")
    assert preview.status_code == 200
    assert preview.json()["plan"]["actual_boundary"]["minimum_margin_cents"] == -30000
    assert all_tables(engine) == before_preview
    with Session(engine) as session:
        cash_before = sum(row.balance_cents for row in session.scalars(select(Account)))
    response = client.post("/api/v1/recovery/runs", json={"idempotency_key": "http-t0"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RECOVERED"
    assert body["actual_boundary"]["minimum_margin_cents"] == principal - 30000
    assert body["plan"]["actual_boundary"]["minimum_margin_cents"] == -30000
    assert len(body["actions"]) == 1
    action = body["actions"][0]
    assert action["position_id"] == str(position_id)
    assert action["bank_status"] == "SETTLED" and action["receipt_id"] is not None
    assert "PRINCIPAL_RECEIVED" in {item["code"] for item in body["notifications"]}
    with Session(engine) as session:
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.status == "REDEEMED"
        assert sum(row.balance_cents for row in session.scalars(select(Account))) == (
            cash_before + principal
        )
        postings = session.scalars(
            select(SimulatedBankPosting).where(
                SimulatedBankPosting.redemption_id == UUID(action["bank_request_id"])
            )
        ).all()
        assert sorted(row.delta_cents for row in postings) == [-principal, principal]
    before_replay = all_tables(engine)
    assert client.get(f"/api/v1/recovery/runs/{body['run_id']}").json() == body
    assert client.post("/api/v1/recovery/runs", json={"idempotency_key": "http-t0"}).json() == body
    assert all_tables(engine) == before_replay


def test_http_t1_get_is_read_only_even_when_due_and_post_reconciles_original_request(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    api = cast(FastAPI, client.app)
    api.dependency_overrides[get_now] = lambda: SEED_AS_OF
    _, position_id, principal = recovery_fixture(engine, delay=1)
    with Session(engine) as session:
        cash_before = sum(row.balance_cents for row in session.scalars(select(Account)))
    response = client.post("/api/v1/recovery/runs", json={"idempotency_key": "http-t1"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING_SETTLEMENT"
    assert len(body["actions"]) == 1
    action = body["actions"][0]
    assert action["bank_status"] == "ACCEPTED" and action["receipt_id"] is None
    with Session(engine) as session:
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.status == "REDEEMING"
        assert sum(row.balance_cents for row in session.scalars(select(Account))) == cash_before
        assert (
            session.scalars(
                select(SimulatedBankPosting).where(
                    SimulatedBankPosting.redemption_id == UUID(action["bank_request_id"])
                )
            ).all()
            == []
        )
    api.dependency_overrides[get_now] = lambda: SEED_AS_OF + timedelta(days=1)
    before_get = all_tables(engine)
    due = client.get(f"/api/v1/recovery/runs/{body['run_id']}")
    assert due.status_code == 200
    assert due.json()["actions"][0]["bank_status"] == "ACCEPTED"
    assert due.json()["actions"][0]["receipt_id"] is None
    assert all_tables(engine) == before_get
    settled = client.post("/api/v1/recovery/runs", json={"idempotency_key": "http-t1"})
    assert settled.status_code == 200
    result = settled.json()
    assert result["run_id"] == body["run_id"]
    assert result["actions"][0]["bank_request_id"] == action["bank_request_id"]
    assert result["actions"][0]["bank_status"] == "SETTLED"
    assert result["actions"][0]["receipt_id"] is not None
    # Fixed historical seed coverage does not become fresh merely because time passed.
    if result["actual_boundary"]["status"] != "READY":
        assert result["status"] == "PARTIAL_RECOVERY"
    with Session(engine) as session:
        assert sum(row.balance_cents for row in session.scalars(select(Account))) == (
            cash_before + principal
        )
    before_repeat = all_tables(engine)
    assert (
        client.post("/api/v1/recovery/runs", json={"idempotency_key": "http-t1"}).json() == result
    )
    assert all_tables(engine) == before_repeat
