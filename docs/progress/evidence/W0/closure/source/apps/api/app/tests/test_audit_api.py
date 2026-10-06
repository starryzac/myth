"""Audit HTTP reads use actual PostgreSQL snapshots and never append or repair."""

from typing import Literal
from uuid import UUID

import pytest
from app.api.v1 import audit
from app.domain.audit_chain_types import AuditCheckpoint, AuditVerification
from app.services.audit_chain import verify_audit_chain
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_audit_list_head_and_verify_are_read_only(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    response = client.get("/api/v1/audit/events?limit=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulation"] is True
    assert len(body["items"]) == 1
    event = body["items"][0]
    assert event["completeness"] == "COMPLETE"
    assert event["envelope"]["simulation"] is True
    assert event["envelope"]["epoch_id"] == body["epoch_id"]
    head = client.get("/api/v1/audit/head")
    assert head.status_code == 200, head.text
    assert head.json()["head"]["epoch_id"] == body["epoch_id"]
    verification = client.post("/api/v1/audit/verify", json={})
    assert verification.status_code == 200, verification.text
    assert verification.json()["status"] == "VALID"
    assert verification.json()["actual_count"] == verification.json()["expected_count"]
    assert verification.json()["actual_count"] >= 1
    assert all_tables(engine) == before


def test_audit_http_rejects_authority_repair_and_unbounded_queries(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    for query in ["user_id=other", "as_of=2030-01-01", "repair=true", "authorized=true"]:
        assert client.get("/api/v1/audit/events?" + query).status_code == 422
        assert client.get("/api/v1/audit/head?" + query).status_code == 422
    for query in ["limit=0", "limit=101", "cursor=invalid"]:
        assert client.get("/api/v1/audit/events?" + query).status_code == 422
    for body in [{"repair": True}, {"user_id": "other"}, {"mode": "SKIP"}]:
        assert client.post("/api/v1/audit/verify", json=body).status_code == 422
    assert all_tables(engine) == before


def test_audit_verify_repeats_without_changing_head(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    first = client.post("/api/v1/audit/verify", json={})
    assert first.status_code == 200, first.text
    before = all_tables(engine)
    assert client.post("/api/v1/audit/verify", json={}).json() == first.json()
    assert all_tables(engine) == before


def test_audit_verify_uses_real_repeatable_read_and_read_only_transaction(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = goal_client
    original = verify_audit_chain
    observed: list[tuple[str, str]] = []

    def inspect_transaction(
        session: Session,
        user_id: UUID,
        epoch_id: UUID | None = None,
        checkpoint: AuditCheckpoint | None = None,
        mode: Literal["PREFIX", "EXACT"] = "PREFIX",
    ) -> AuditVerification:
        observed.append(
            (
                str(session.scalar(text("SHOW transaction_isolation"))),
                str(session.scalar(text("SHOW transaction_read_only"))),
            )
        )
        return original(session, user_id, epoch_id, checkpoint, mode)

    monkeypatch.setattr(audit, "verify_audit_chain", inspect_transaction)
    before = all_tables(engine)
    response = client.post("/api/v1/audit/verify", json={})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "VALID"
    assert observed == [("repeatable read", "on")]
    assert all_tables(engine) == before
