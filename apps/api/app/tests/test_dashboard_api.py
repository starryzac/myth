"""Real PostgreSQL same-snapshot homepage and fail-closed reading contracts."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.api.v1.accounts import account_summary
from app.db.models import Account, EvidenceItem, User
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.asset_exposure import EXPOSURE_SOURCE
from app.domain.demo_identity import DEMO_USER_ID
from app.main import create_app
from app.services import dashboard
from app.services.demo_seed import seed_demo
from app.tests.test_demo_seed import database_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
PATH = "/api/v1/dashboard"


@pytest.fixture(scope="module")
def dashboard_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def dashboard_client(dashboard_engine: Engine) -> Iterator[TestClient]:
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: dashboard_engine
    api.dependency_overrides[get_now] = lambda: NOW
    with TestClient(api) as client:
        yield client


def test_dashboard_shares_current_facts_and_never_writes(
    dashboard_client: TestClient,
    dashboard_engine: Engine,
) -> None:
    before = database_snapshot(dashboard_engine)
    result = dashboard_client.get(PATH)
    assert result.status_code == 200, result.text
    doc = result.json()
    assert doc["schema_version"] == "dashboard-v1"
    assert doc["simulation"] is True
    assert doc["user_id"] == str(DEMO_USER_ID)
    assert doc["as_of"] == "2026-10-04T01:00:00Z"
    assert doc["boundary"]["state"] == "PROVEN"
    assert doc["boundary"]["safe_idle_cents"] == 3_157_400
    assert doc["boundary"]["financial_only"] is True
    assert doc["boundary"]["window_end"] == "2027-01-02"
    assert len(doc["boundary"]["input_digest"]) == 64
    assert doc["account_facts"]["facts"]["cash_balance_cents"] == 3_462_400
    assert doc["account_facts"]["bank_projection_state"] == "MATCHED"
    # All three seed positions are manual. A policy ID cannot invent managed principal.
    assert doc["managed_assets"]["state"] == "PROVEN"
    assert doc["managed_assets"]["managed_current_principal_cents"] == 0
    assert doc["managed_assets"]["excluded_manual_count"] == 3
    assert doc["goal_ownership"]["unassigned_goal_cash_cents"] == 160_000
    assert doc["next_obligations"]["status"] == "PROVEN"
    assert doc["pending_actions"]["total"] == 0
    assert doc["recovery_proposals"]["total"] == 0
    assert doc["audit"]["status"] == "VALID"
    assert doc["intervention"]["status"] == "NONE"
    assert dashboard_client.get(PATH).json() == doc
    assert database_snapshot(dashboard_engine) == before


@pytest.mark.parametrize(
    "query",
    [
        {"user_id": "another"},
        {"as_of": "2099-01-01"},
        {"timezone": "UTC"},
        {"pending_limit": 0},
        {"pending_limit": 101},
        {"recovery_limit": 101},
    ],
)
def test_query_cannot_replace_identity_clock_or_capacity(
    dashboard_client: TestClient,
    query: dict[str, str | int],
) -> None:
    assert dashboard_client.get(PATH, params=query).status_code == 422


def test_dashboard_transaction_read_only_precedes_identity_select(
    dashboard_client: TestClient,
    dashboard_engine: Engine,
) -> None:
    statements: list[str] = []

    def capture(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: object,
    ) -> None:
        statements.append(statement)

    event.listen(dashboard_engine, "before_cursor_execute", capture)
    try:
        response = dashboard_client.get(PATH)
        assert response.status_code == 200, response.text
    finally:
        event.remove(dashboard_engine, "before_cursor_execute", capture)
    readonly_index = next(
        i for i, sql in enumerate(statements) if "SET TRANSACTION READ ONLY" in sql
    )
    first_select = next(
        i for i, sql in enumerate(statements) if sql.lstrip().upper().startswith("SELECT")
    )
    assert readonly_index < first_select


def test_postgres_rejects_accidental_dashboard_write_and_preserves_every_row(
    dashboard_client: TestClient,
    dashboard_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = database_snapshot(dashboard_engine)
    original = account_summary

    def forbidden_write(session: Session, user: User) -> object:
        session.add(
            User(
                id=uuid4(),
                external_ref="forbidden-dashboard-write",
                display_name="must rollback",
                timezone="UTC",
                is_simulated=True,
            )
        )
        session.flush()
        return original(session, user)

    monkeypatch.setattr(dashboard, "account_summary", forbidden_write)
    assert dashboard_client.get(PATH).status_code == 500
    assert database_snapshot(dashboard_engine) == before


def test_foreign_cash_is_never_aggregated(
    dashboard_client: TestClient,
    dashboard_engine: Engine,
) -> None:
    foreign_id, account_id = uuid4(), uuid4()
    with Session(dashboard_engine) as session, session.begin():
        session.add(
            User(
                id=foreign_id,
                external_ref=str(foreign_id),
                display_name="foreign",
                timezone="UTC",
                is_simulated=True,
            )
        )
        session.flush()
        session.add(
            Account(
                id=account_id,
                user_id=foreign_id,
                external_ref="foreign-cash",
                name="foreign secret",
                account_type="CASH",
                bank_code="ICBC",
                currency="CNY",
                balance_cents=999_999,
                observed_at=NOW,
            )
        )
    before = database_snapshot(dashboard_engine)
    result = dashboard_client.get(PATH)
    assert result.status_code == 200, result.text
    assert result.json()["account_facts"]["facts"]["cash_balance_cents"] == 3_462_400
    assert str(account_id) not in result.text
    assert "foreign secret" not in result.text
    assert database_snapshot(dashboard_engine) == before


def test_missing_complete_exposure_is_unknown_and_get_does_not_repair_it(
    dashboard_client: TestClient,
    dashboard_engine: Engine,
) -> None:
    with Session(dashboard_engine) as session, session.begin():
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.user_id == DEMO_USER_ID,
                EvidenceItem.source_type == EXPOSURE_SOURCE,
                EvidenceItem.status == "VALID",
            )
        )
        assert proof is not None
        proof.status = "SUPERSEDED"
    before = database_snapshot(dashboard_engine)
    response = dashboard_client.get(PATH)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["boundary"]["state"] == "NOT_PROVEN"
    assert doc["boundary"]["safe_idle_cents"] is None
    assert doc["managed_assets"]["managed_current_principal_cents"] is None
    assert doc["goal_ownership"]["allocated_cents"] is None
    assert doc["next_obligations"]["next_count"] is None
    assert doc["intervention"]["status"] == "NOT_PROVEN"
    assert doc["account_facts"]["facts"]["cash_balance_cents"] == 3_462_400
    assert database_snapshot(dashboard_engine) == before
