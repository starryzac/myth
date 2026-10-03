"""Read-only HTTP projections consume verified seed facts and confirmed policies."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.base import Base
from app.db.models import Account
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.main import create_app
from app.services.demo_seed import seed_demo
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
PATH = "/api/v1/boundary"
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


@pytest.fixture
def boundary_client() -> Iterator[tuple[TestClient, Engine]]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        seed_demo(engine)
        api = create_app()
        api.dependency_overrides[get_engine] = lambda: engine
        api.dependency_overrides[get_now] = lambda: NOW
        try:
            with TestClient(api) as client:
                yield client, engine
        finally:
            engine.dispose()


def snapshot(engine: Engine) -> str:
    with engine.connect() as connection:
        result = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
    return json.dumps(result, sort_keys=True, default=str)


def test_seed_boundary_explains_ninety_days_without_writing_or_granting_authority(
    boundary_client: tuple[TestClient, Engine],
) -> None:
    client, engine = boundary_client
    before = snapshot(engine)
    response = client.get(PATH)
    assert response.status_code == 200
    result = response.json()
    boundary = result["boundary"]
    assert result["simulation"] is True
    assert result["source_issues"] == []
    assert result["source_evidence_ids"]
    assert boundary["status"] == "READY"
    assert boundary["financial_only"] is True
    # Cash already excludes all 500000 position principal; protect the 160000
    # unassigned GOAL account and 145000 actual bill, each exactly once.
    assert boundary["safe_idle_cents"] == 3157400
    assert boundary["minimum_margin_cents"] == 3157400
    assert boundary["deficit_cents"] == 0
    assert len(boundary["max_allocatable_by_product"]) == 3
    assert set(boundary["max_allocatable_by_product"].values()) == {3157400}
    assert len(boundary["boundary_hash"]) == 64
    points = boundary["calculation_trace"]
    assert {point["day"] for point in points} == set(range(91))
    assert points[0]["date"] == "2026-10-04"
    assert points[-1]["date"] == "2027-01-02"
    assert client.get(PATH).json() == result
    for invalid in ({"horizon_days": "1"}, {"as_of": "2099-01-01"}, {"user_id": "another"}):
        assert client.get(PATH, params=invalid).status_code == 422
    assert snapshot(engine) == before


def test_only_confirmation_adds_recurring_commitments_and_bill_policy_does_not_double_count(
    boundary_client: tuple[TestClient, Engine],
) -> None:
    client, engine = boundary_client
    initial = client.get(PATH).json()["boundary"]["safe_idle_cents"]
    assert initial == 3157400
    assert client.post("/api/v1/policies/discover").status_code == 200
    proposals = client.get("/api/v1/policy-proposals").json()["items"]
    assert client.get(PATH).json()["boundary"]["safe_idle_cents"] == initial
    for kind, expected in (("bill_balance", 3157400), ("range", 2617400)):
        proposal = next(
            row for row in proposals if row["configuration"]["amount_rule"]["kind"] == kind
        )
        confirmed = client.post(
            f"/api/v1/policy-proposals/{proposal['id']}/confirm",
            json={"accepted": True, "reviewed_hash": proposal["configuration_hash"]},
        )
        assert confirmed.status_code == 200
        before = snapshot(engine)
        result = client.get(PATH).json()["boundary"]
        assert result["status"] == "READY"
        assert result["safe_idle_cents"] == expected
        assert snapshot(engine) == before


def test_unverified_goal_account_relabeling_cannot_release_protected_funds(
    boundary_client: tuple[TestClient, Engine],
) -> None:
    client, engine = boundary_client
    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "GOAL"))
        assert account is not None
        account.account_type = "CASH"
    before = snapshot(engine)
    result = client.get(PATH).json()
    assert result["boundary"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["boundary"]["safe_idle_cents"] is None
    assert result["source_issues"]
    assert snapshot(engine) == before
