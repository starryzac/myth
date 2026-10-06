"""One isolated real PostgreSQL risk node; root runs it, not a formal annual acceptance suite."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.models import Account
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.main import create_app
from app.services.demo_seed import seed_demo
from app.tests.test_full_projection import NOW
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def annual_client() -> Iterator[tuple[TestClient, Engine]]:
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


def physical_snapshot(engine: Engine) -> str:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        metadata = MetaData()
        metadata.reflect(connection)
        assert "alembic_version" in metadata.tables
        originals = {
            name: [
                dict(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for name, table in sorted(metadata.tables.items())
        }
        return json.dumps(originals, sort_keys=True, default=str)


def test_actual_annual_get_is_verified_complete_and_readonly_with_conservative_tamper_failure(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    before = physical_snapshot(engine)
    mvp = client.get("/api/v1/boundary").json()["boundary"]
    response = client.get("/api/v1/planning/annual")
    assert response.status_code == 200
    value = response.json()
    assert value["source_issues"] == []
    assert value["source_evidence_ids"]
    assert value["audit"]["status"] == "VALID" and value["audit"]["complete"] is True
    assert value["execution_view"] == mvp
    assert value["initial_checkpoint"]["day"] == 0
    assert [point["day"] for point in value["daily_checkpoints"]] == list(range(1, 366))
    assert len(value["annual_projection"]["calculation_trace"]) == 366 * 3
    assert value["future_income"]["included_in_execution_cents"] == 0
    assert value["future_income"]["status"] == "NOT_IMPLEMENTED_NO_REGISTERED_SOURCE"
    assert value["grants_authority"] is False
    assert client.get("/api/v1/planning/annual").json() == value
    for params in (
        {"future_income_cents": "999999999"},
        {"as_of": "2099-01-01"},
        {"user_id": value["user_id"]},
        {"horizon_days": "365"},
    ):
        assert client.get("/api/v1/planning/annual", params=params).status_code == 422
    assert client.get("/api/v1/boundary").json()["boundary"] == mvp
    assert physical_snapshot(engine) == before

    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        account.balance_cents += 1
    tampered_original = physical_snapshot(engine)
    result = client.get("/api/v1/planning/annual")
    assert result.status_code == 200
    failure = result.json()
    assert failure["annual_projection"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert failure["annual_projection"]["safe_idle_cents"] is None
    assert failure["source_issues"]
    assert len(failure["daily_checkpoints"]) == 365
    assert all(point["after_principal"] is None for point in failure["daily_checkpoints"])
    assert physical_snapshot(engine) == tampered_original
