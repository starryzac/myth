"""One owned real-PG risk candidate; Root schedules it. No financial execution.

Uses an actual current OPEN epoch and one fixed trusted server instant. This
does not prove dynamic wall-clock expiry or production/browser acceptance.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import AuditEpoch, DecisionRun, EvidenceItem
from app.domain.future_income_planning import FutureIncomeCandidateRequest
from app.domain.policy_configuration import configuration_hash
from app.services import future_income_planning as planning
from app.services.audit_chain import verify_audit_chain
from app.services.decision_trace import get_decision_trace
from app.services.demo_seed import DEMO_USER_ID
from app.services.local_actor_sessions import COOKIE_NAME, verify_local_actor_session
from app.tests.test_full_asset_allocation_api import actual_income
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def originals(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    # Existing helper independently reflects every physical table, including
    # migration metadata, in one actual REPEATABLE READ / READ ONLY transaction.
    return dict(json.loads(physical_snapshot(engine)))


def retained_traces(engine: Engine, now: datetime) -> dict[str, dict[str, Any]]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        with Session(bind=connection) as read:
            return {
                str(run.id): get_decision_trace(read, DEMO_USER_ID, run.id, now).model_dump(
                    mode="json"
                )
                for run in read.scalars(
                    select(DecisionRun).where(DecisionRun.user_id == DEMO_USER_ID)
                )
            }


def assert_only_two_metadata_rows_added(
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
    candidate_id: str,
    confirmation_id: str,
) -> None:
    assert before.keys() == after.keys()
    for table in before:
        if table != "evidence_items":
            assert after[table] == before[table], table
    old = {str(row["id"]): row for row in before["evidence_items"]}
    new = {str(row["id"]): row for row in after["evidence_items"]}
    assert all(new[identity] == row for identity, row in old.items())
    assert new.keys() - old.keys() == {candidate_id, confirmation_id}
    for identifier, source in (
        (candidate_id, "FUTURE_INCOME_PLANNING_CANDIDATE"),
        (confirmation_id, "FUTURE_INCOME_PLANNING_CONFIRM"),
    ):
        row = new[identifier]
        assert row["evidence_level"] == "USER_DECLARED" and row["source_type"] == source
        assert row["content_hash"] == configuration_hash(row["content"])
        assert row["content"]["grants_authority"] is False


def test_actual_signed_user_condition_is_original_recoverable_and_never_executable_money(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = annual_client
    now = datetime.now(UTC)
    assert isinstance(client.app, FastAPI)
    app = client.app
    with Session(engine) as read:
        epoch = read.scalar(
            select(AuditEpoch).where(
                AuditEpoch.user_id == DEMO_USER_ID, AuditEpoch.status == "OPEN"
            )
        )
        assert epoch is not None and now >= epoch.opened_at
        epoch_id = str(epoch.id)
    app.dependency_overrides[get_now] = lambda: now
    # Real existing independent simulated-bank ingress creates this original
    # income. No outcome/receipt is inserted or substituted by this candidate.
    actual_income(engine, now=now)
    secret = "SYNTHETIC_LOCAL_203_OWNED_PG_USER"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "c7" * 32)
    signed = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
    )
    assert signed.status_code == 200, signed.text
    token = client.cookies.get(COOKIE_NAME)
    assert token is not None
    principal = verify_local_actor_session(token, DEMO_USER_ID, now)
    base = "/api/v1/planning/future-income"
    read_sources = client.get(base + "/sources")
    assert read_sources.status_code == 200, read_sources.text
    sources = read_sources.json()
    assert sources["complete"] and sources["status"] == "VERIFIED_ORIGINAL_INCOME_SOURCES"
    assert sources["original_origin_count"] == sources["captured_origin_count"]
    assert sources["captured_origin_count"] == len(sources["sources"]) > 0
    source = next(row for row in sources["sources"] if row["origin"]["amount_cents"] == 5000000)
    assert not source["implies_recurring_salary"] and not source["bank_promises_future_payment"]
    body = {
        "expected_epoch_id": epoch_id,
        "origin_transaction_id": source["origin"]["origin_transaction_id"],
        "expected_origin_hash": source["origin_hash"],
        "idempotency_key": "203-original-monthly-condition",
    }
    boundary_before = client.get("/api/v1/boundary")
    assert boundary_before.status_code == 200, boundary_before.text
    financial_before = boundary_before.json()["boundary"]
    before = originals(engine)
    traces_before = retained_traces(engine, now)
    unknown = client.get(base)
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["status"] == "NO_CONFIRMED_REGISTERED_SOURCE"
    assert unknown.json()["total_conditional_income_cents"] is None
    assert all(row["conditional_income_cents"] is None for row in unknown.json()["daily_schedule"])
    assert originals(engine) == before

    # Discard each real completed HTTP response after the committed command.
    # This is explicit response-loss injection; no API or business result mock.
    def discard_committed_response(path: str, request: dict[str, Any]) -> None:
        committed = client.post(path, json=request)
        assert committed.status_code == 200, committed.text
        raise TimeoutError("203_ACTUAL_COMMITTED_METADATA_RESPONSE_DISCARDED")

    with pytest.raises(TimeoutError, match="ACTUAL_COMMITTED_METADATA"):
        discard_committed_response(base + "/candidates", body)
    lookup_path = base + f"/commands/{epoch_id}/by-key/{body['idempotency_key']}"
    original = client.get(lookup_path)
    assert original.status_code == 200, original.text
    lookup = original.json()
    assert lookup["status"] == "RECORDED" and lookup["command_kind"] == "CANDIDATE"
    assert lookup["original_request"] == body
    assert lookup["request_hash"] == configuration_hash(body)
    candidate = lookup["candidate"]
    assert candidate["source"] == source and candidate["state"] == "REQUIRES_EXPLICIT_CONFIRMATION"
    assert candidate["candidate_hash"] == configuration_hash(candidate["assumption"])
    assert candidate["assumption"]["conditional_amount_cents"] == source["origin"]["amount_cents"]
    assert candidate["assumption"]["included_in_current_cash_cents"] == 0
    confirm = {
        "expected_epoch_id": epoch_id,
        "candidate_id": candidate["candidate_id"],
        "reviewed_candidate_hash": candidate["candidate_hash"],
        "accepted": True,
        "idempotency_key": "203-original-explicit-condition-confirmation",
    }
    with pytest.raises(TimeoutError, match="ACTUAL_COMMITTED_METADATA"):
        discard_committed_response(base + "/confirm", confirm)
    confirm_lookup = base + f"/commands/{epoch_id}/by-key/{confirm['idempotency_key']}"
    registered = originals(engine)
    found = client.get(confirm_lookup)
    assert found.status_code == 200, found.text
    record = found.json()
    assert record["command_kind"] == "CONFIRM" and record["original_request"] == confirm
    assert record["request_hash"] == configuration_hash(confirm)
    receipt = record["confirmation"]
    assert receipt["original_request"] == confirm and not receipt["confirms_financial_action"]
    assert not receipt["grants_authority"] and not receipt["receipt_is_current_authority"]
    assert_only_two_metadata_rows_added(
        before, registered, candidate["candidate_id"], receipt["confirmation_id"]
    )
    # New HTTP client consumes only the original signed cookie; no role override.
    with TestClient(app) as reopened:
        reopened.cookies.set(COOKIE_NAME, token, path="/api/v1")
        recovered = reopened.get(confirm_lookup)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == record
    assert client.post(base + "/candidates", json=body).status_code == 200
    assert client.post(base + "/confirm", json=confirm).status_code == 200
    assert originals(engine) == registered
    projected = client.get(base)
    assert projected.status_code == 200, projected.text
    result = projected.json()
    assert result["status"] == "CONDITIONAL_PLANNING", projected.text
    assert result["horizon_days"] == len(result["daily_schedule"]) == 365
    assert result["total_conditional_income_cents"] > 0
    assert result["included_in_current_cash_cents"] == result["included_in_execution_cents"] == 0
    assert not result["writes_financial_facts"] and not result["original_execution_view_changed"]
    assert client.get("/api/v1/boundary").json()["boundary"] == financial_before
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        with Session(bind=connection) as read:
            audit = verify_audit_chain(read, DEMO_USER_ID)
            assert audit.status == audit.chain_status == audit.reference_status == "VALID"
            assert audit.actual_count == audit.expected_count == audit.verified_through_sequence
            assert audit.actual_tail_id == audit.expected_tail_id
            assert audit.actual_tail_hash == audit.expected_tail_hash
            assert audit.errors == [] and not audit.errors_truncated
            for identifier in (candidate["candidate_id"], receipt["confirmation_id"]):
                row = read.get(EvidenceItem, UUID(identifier))
                assert row is not None and row.content_hash == configuration_hash(row.content)
    # Preserve each original trace's actual completeness label, including any
    # legacy scope; do not promote it to COMPLETE or manufacture a new run.
    assert retained_traces(engine, now) == traces_before
    assert originals(engine) == registered
    for altered in (
        body | {"expected_origin_hash": "f" * 64, "idempotency_key": "203-source-drift"},
        body | {"amount_cents": 1},
        confirm | {"reviewed_candidate_hash": "f" * 64, "idempotency_key": "203-wrong-review"},
        confirm | {"accepted": 1},
    ):
        path = "/confirm" if "candidate_id" in altered else "/candidates"
        assert client.post(base + path, json=altered).status_code in {409, 422}
    with Session(engine) as write, write.begin(), pytest.raises(ValueError):
        planning.create_future_income_candidate(
            write,
            DEMO_USER_ID,
            FutureIncomeCandidateRequest.model_validate(body | {"idempotency_key": "203-agent"}),
            principal.model_copy(update={"role": "AGENT"}),
            now,
        )
    assert originals(engine) == registered
    # The original bank fact's status is changed only in the owned negative
    # fixture. Keep it conflicted; never restore/delete old evidence or hashes.
    with Session(engine) as write, write.begin():
        bank = write.get(EvidenceItem, UUID(source["origin"]["bank_evidence_id"]))
        assert bank is not None
        bank.status = "CONFLICTED"
    retained_negative = originals(engine)
    drift = client.get(base)
    assert drift.status_code == 200, drift.text
    assert drift.json()["status"] == "UNKNOWN" and drift.json()["issues"]
    assert drift.json()["total_conditional_income_cents"] is None
    assert all(row["conditional_income_cents"] is None for row in drift.json()["daily_schedule"])
    assert (
        drift.json()["included_in_current_cash_cents"]
        == drift.json()["included_in_execution_cents"]
        == 0
    )
    assert originals(engine) == retained_negative
