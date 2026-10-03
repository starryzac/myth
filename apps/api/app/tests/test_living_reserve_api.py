"""Read-only reserve estimation from a real, covered synthetic history snapshot."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.base import Base
from app.db.models import EvidenceItem
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.policy_configuration import configuration_hash
from app.main import create_app
from app.services.demo_seed import seed_demo
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
PATH = "/api/v1/living-reserve/estimate"
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


@pytest.fixture
def reserve_client() -> Iterator[tuple[TestClient, Engine]]:
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
        data = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
    return json.dumps(data, sort_keys=True, default=str)


def test_covered_seed_estimate_is_repeatable_explained_and_writes_nothing(
    reserve_client: tuple[TestClient, Engine],
) -> None:
    client, engine = reserve_client
    before = snapshot(engine)
    response = client.get(PATH)
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    assert result["simulation"] is True
    assert result["source_issues"] == []
    assert result["source_evidence_ids"]
    assert len(result["input_digest"]) == 64
    estimate = result["estimation"]
    assert estimate["status"] == "READY"
    assert (estimate["history_start"], estimate["history_end"]) == ("2026-08-09", "2026-10-03")
    assert estimate["window_count"] == 43 and estimate["rank"] == 35
    assert estimate["quantile_fraction"] == "4/5"
    assert estimate["base_reserve_cents"] == 77900
    assert estimate["extra_buffer_cents"] == 50000
    assert estimate["recommended_reserve_cents"] == 127900
    assert len(estimate["daily_amounts"]) == 56 and len(estimate["windows"]) == 43
    assert result["candidate_configuration"]["type"] == "living_reserve"
    assert result["candidate_configuration_hash"] == configuration_hash(
        result["candidate_configuration"]
    )
    assert client.get(PATH).json() == result
    assert snapshot(engine) == before
    assert client.get("/api/v1/policies").json()["items"] == []
    assert client.get("/api/v1/policy-proposals").json()["items"] == []


def test_query_parameters_change_only_estimation_and_reject_client_proof(
    reserve_client: tuple[TestClient, Engine],
) -> None:
    client, engine = reserve_client
    before = snapshot(engine)
    params = [("essential_categories", "food"), ("extra_buffer_cents", "0")]
    food = client.get(PATH, params=params)
    assert food.status_code == 200
    assert food.json()["estimation"]["recommended_reserve_cents"] == 57400
    repeated_category = client.get(PATH, params=params + [("essential_categories", "food")])
    assert repeated_category.status_code == 200
    assert repeated_category.json()["estimation"]["base_reserve_cents"] == 57400
    for invalid in (
        {"quantile": "0"},
        {"quantile": "nan"},
        {"lookback_days": "367"},
        {"horizon_days": "60", "lookback_days": "56"},
        {"extra_buffer_cents": "1.5"},
        {"extra_buffer_cents": "-1"},
        {"as_of": "2099-01-01"},
        {"complete": "true"},
        {"user_id": "another"},
    ):
        assert client.get(PATH, params=invalid).status_code == 422
    assert snapshot(engine) == before


def test_invalid_coverage_never_produces_zero_or_buffer_only_advice(
    reserve_client: tuple[TestClient, Engine],
) -> None:
    client, engine = reserve_client
    with Session(engine) as session, session.begin():
        coverage = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
            )
        )
        assert coverage is not None
        coverage.status = "UNKNOWN"
    before = snapshot(engine)
    response = client.get(PATH)
    assert response.status_code == 200
    result = response.json()
    assert result["estimation"]["status"] == "INSUFFICIENT_HISTORY"
    assert result["estimation"]["base_reserve_cents"] is None
    assert result["estimation"]["recommended_reserve_cents"] is None
    assert result["source_issues"]
    assert result["candidate_configuration"] is None
    assert result["candidate_configuration_hash"] is None
    assert snapshot(engine) == before
