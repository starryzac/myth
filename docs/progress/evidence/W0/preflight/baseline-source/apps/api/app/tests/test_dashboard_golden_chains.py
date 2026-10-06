"""Salary, first goal ownership and consumption/recovery use real persisted bank effects."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.models import ExternalBankFact, SimulatedBankPosting
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.demo_identity import DEMO_USER_ID
from app.main import create_app
from app.tests.dashboard_scenario import STAGES, DashboardScenario
from app.tests.test_demo_seed import database_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def golden_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


def test_three_homepage_chains_keep_original_bank_effects_and_readonly_snapshots(
    golden_engine: Engine,
) -> None:
    flow = DashboardScenario(golden_engine)
    flow.initialize()
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: golden_engine
    api.dependency_overrides[get_now] = lambda: flow.now
    # Literal hand-worked oracle: fixed initial cash 3462400 protects unassigned
    # goal cash 160000 + actual unpaid bill 145000. New goal minimum is zero.
    expected = {
        "initial": (3_462_400, 3_157_400, 0, 0),
        "goal_confirmed": (3_462_400, None, None, None),
        "goal_created": (3_462_400, 3_157_400, 0, 0),
        "salary": (3_662_400, 3_357_400, 0, 0),
        "goal_allocated": (3_662_400, 3_347_400, 10_000, 0),
        "purchase_prepared": (3_662_400, 3_347_400, 10_000, 0),
        "purchased": (3_412_400, 3_097_400, 10_000, 250_000),
        "consumption": (280_000, 0, 10_000, 250_000),
        "redeem_prepared": (280_000, 0, 10_000, 250_000),
        "redeemed": (530_000, 215_000, 10_000, 0),
    }
    with TestClient(api) as client:
        for stage in STAGES:
            if stage != "initial":
                flow.advance(stage)
            before = database_snapshot(golden_engine)
            response = client.get("/api/v1/dashboard")
            assert response.status_code == 200, (stage, response.text)
            doc = response.json()
            cash, safe, owned, managed = expected[stage]
            assert doc["account_facts"]["facts"]["cash_balance_cents"] == cash, stage
            assert doc["boundary"]["safe_idle_cents"] == safe, (stage, doc["boundary"])
            assert doc["goal_ownership"]["allocated_cents"] == owned, stage
            assert doc["managed_assets"]["managed_current_principal_cents"] == managed, stage
            assert doc["audit"]["status"] == "VALID", (stage, doc["audit"])
            if stage in {"consumption", "redeem_prepared"}:
                assert doc["boundary"]["minimum_margin_cents"] == -35_000
                assert doc["boundary"]["deficit_cents"] == 35_000
            if stage.endswith("prepared"):
                assert doc["pending_actions"]["total"] == 1
                assert (
                    doc["pending_actions"]["items"][0]["current_decision"]["level"]
                    == "AUTO_EXECUTE"
                )
            assert client.get("/api/v1/dashboard").json() == doc
            assert database_snapshot(golden_engine) == before
    with Session(golden_engine) as session:
        facts = list(
            session.scalars(
                select(ExternalBankFact).where(
                    ExternalBankFact.user_id == DEMO_USER_ID,
                )
            )
        )
        assert len(facts) == 2
        for fact in facts:
            legs = list(
                session.scalars(
                    select(SimulatedBankPosting).where(
                        SimulatedBankPosting.external_fact_id == fact.id,
                        SimulatedBankPosting.ledger_dimension == "ECONOMIC",
                    )
                )
            )
            assert len(legs) == 2 and sum(leg.delta_cents for leg in legs) == 0
            assert {abs(leg.delta_cents) for leg in legs} == {fact.amount_cents}
