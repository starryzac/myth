"""Discovery creates reviewable candidates without changing money or authority."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.main import create_app
from app.services.demo_seed import seed_demo
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def discovery_client() -> Iterator[TestClient]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        seed_demo(engine)
        api = create_app()
        api.dependency_overrides[get_engine] = lambda: engine
        api.dependency_overrides[get_now] = lambda: datetime(2026, 10, 4, 1, tzinfo=UTC)
        try:
            with TestClient(api) as client:
                yield client
        finally:
            engine.dispose()


def financial_facts(client: TestClient) -> dict[str, Any]:
    return {
        path: client.get("/api/v1/" + path).json()
        for path in (
            "accounts/summary",
            "transactions?limit=200",
            "positions",
            "products",
        )
    }


def test_discovery_is_repeatable_and_requires_explicit_user_confirmation(
    discovery_client: TestClient,
) -> None:
    client = discovery_client
    original = financial_facts(client)
    assert client.get("/api/v1/policies").json()["items"] == []
    response = client.post("/api/v1/policies/discover", json={})
    assert response.status_code == 200
    first = response.json()
    assert first["simulation"] is True
    assert len(first["created_proposal_ids"]) == 2
    proposals = client.get("/api/v1/policy-proposals").json()["items"]
    assert len(proposals) == 2
    assert all(row["status"] == "PROPOSED" and row["validation_ready"] for row in proposals)
    assert all(row["configuration"]["auto_execute"] is False for row in proposals)
    assert {row["configuration"]["amount_rule"]["kind"] for row in proposals} == {
        "range",
        "bill_balance",
    }
    assert client.get("/api/v1/policies").json()["items"] == []
    assert financial_facts(client) == original
    repeated = client.post("/api/v1/policies/discover").json()
    assert repeated["created_proposal_ids"] == []
    assert set(repeated["reused_proposal_ids"]) == set(first["created_proposal_ids"])
    assert client.get("/api/v1/policy-proposals").json()["items"] == proposals
    chosen = proposals[0]
    confirmed = client.post(
        f"/api/v1/policy-proposals/{chosen['id']}/confirm",
        json={
            "accepted": True,
            "reviewed_hash": chosen["configuration_hash"],
        },
    )
    assert confirmed.status_code == 200
    policies = client.get("/api/v1/policies").json()["items"]
    assert len(policies) == 1 and policies[0]["version_authorized"] is True
    assert financial_facts(client) == original
    assert client.post("/api/v1/policies/discover").json()["created_proposal_ids"] == []
    assert len(client.get("/api/v1/policies").json()["items"]) == 1


def test_discovery_clock_and_authority_cannot_be_injected(discovery_client: TestClient) -> None:
    response = discovery_client.post(
        "/api/v1/policies/discover",
        json={
            "as_of": "2099-01-01T00:00:00Z",
            "auto_execute": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert discovery_client.get("/api/v1/policy-proposals").json()["items"] == []
