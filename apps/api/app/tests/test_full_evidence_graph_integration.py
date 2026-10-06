"""Root-run single actual PostgreSQL candidate; collection is not a financial result."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.api.dependencies import get_engine, get_now
from app.db.models import Account, EvidenceItem
from app.domain.demo_identity import DEMO_USER_ID
from app.main import create_app
from app.services.demo_seed import seed_demo
from app.services.full_policy_lifecycle import confirm_full_policy
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_full_policy_lifecycle_integration import body
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_all_registered_sources_reverse_links_and_bank_tamper_are_read_only(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        cash = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        )
        assert cash is not None
        identity = str(cash.id)
        evidence = session.scalar(select(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID))
        assert evidence is not None
        evidence_id = str(evidence.id)
        full = confirm_full_policy(
            session,
            DEMO_USER_ID,
            body("InterventionPolicy", key="actual-full-evidence-source"),
            datetime.now(UTC),
        )
        full_id = str(full.policy_id)
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: demo_engine
    app.dependency_overrides[get_now] = lambda: datetime.now(UTC)
    before = physical_snapshot(demo_engine)
    with TestClient(app) as client:
        path = "/api/v1/evidence/full-graph/ACCOUNT/" + identity
        response = client.get(path)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["complete_registered_inventory"]
        assert len(result["inventory"]) == 35
        assert {
            "ACCOUNT",
            "TRANSACTION",
            "POSTING",
            "AUDIT_EVENT",
            "AUDIT_SNAPSHOT",
            "AUDIT_EPOCH",
        } <= {row["kind"] for row in result["nodes"]}
        assert result["audit_proof"]["state"] == "VERIFIED"
        assert result["bank_proof"]["state"] == "VERIFIED"
        assert not result["grants_authority"] and not result["financial_success_inferred"]
        backward = client.get("/api/v1/evidence/full-graph/EVIDENCE/" + evidence_id)
        assert backward.status_code == 200, backward.text
        assert any(row["kind"] == "ACCOUNT" for row in backward.json()["nodes"])
        full_response = client.get("/api/v1/evidence/full-graph/FULL_POLICY/" + full_id)
        assert full_response.status_code == 200, full_response.text
        full_graph = full_response.json()
        assert {
            "FULL_POLICY",
            "FULL_POLICY_VERSION",
            "FULL_POLICY_COMMAND",
            "EVIDENCE",
            "AUDIT_EPOCH",
        } <= {row["kind"] for row in full_graph["nodes"]}
        source = next(row for row in full_graph["nodes"] if row["key"] == "FULL_POLICY:" + full_id)
        assert source["proof"]["check"] == "ORIGINAL_FULL_POLICY_VERSION_COMMAND_CHAIN"
        assert source["proof"]["state"] == "VERIFIED" and not source["execution_authority"]
        assert client.get("/api/v1/evidence/full-graph/ACCOUNT/" + str(uuid4())).status_code == 404
        assert client.get(path + "?known_at=2099-01-01T00%3A00%3A00Z").status_code == 422
        assert client.get(path + "?actor=USER").status_code == 422
        assert physical_snapshot(demo_engine) == before
        with Session(demo_engine) as session, session.begin():
            actual = session.get(Account, UUID(identity))
            assert actual is not None
            actual.balance_cents += 1
        tampered = physical_snapshot(demo_engine)
        rejected = client.get(path)
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["state"] == "UNKNOWN"
        account = next(
            row for row in rejected.json()["nodes"] if row["key"] == "ACCOUNT:" + identity
        )
        assert account["proof"]["state"] == "UNKNOWN"
        assert physical_snapshot(demo_engine) == tampered
