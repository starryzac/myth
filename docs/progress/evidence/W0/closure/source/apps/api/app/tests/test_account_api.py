"""Read real persisted facts, isolate the synthetic user, and avoid wealth double counting."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.models import Account, AssetPosition, AssetProduct, CreditCardBill, Transaction, User
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Select, event, update
from sqlalchemy.engine import Connection, Engine, ExecutionContext
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
DEMO_ID = UUID("271a9827-f82a-5be6-9e0a-50f721029fb0")
AS_OF = datetime(2026, 10, 3, 15, 59, 59, tzinfo=UTC)


@pytest.fixture
def fact_client(request: pytest.FixtureRequest) -> Iterator[tuple[TestClient, dict[str, UUID]]]:
    from app.api.dependencies import get_engine

    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        ids = {key: uuid4() for key in ("cash", "goal", "managed", "card", "other", "other_cash")}
        with Session(engine) as session, session.begin():
            session.add_all(
                [
                    User(
                        id=DEMO_ID,
                        external_ref=getattr(request, "param", "bounded-funds:demo:v1"),
                        display_name="演示",
                    ),
                    User(id=ids["other"], external_ref="another-user", display_name="另一个用户"),
                ]
            )
            session.flush()
            for key, kind, balance in [
                ("cash", "CASH", 1_000_000),
                ("goal", "GOAL", 200_000),
                ("managed", "CASH_MANAGEMENT", 100_000),
                ("card", "CREDIT_CARD", 0),
                ("other_cash", "CASH", 9_000_000),
            ]:
                session.add(
                    Account(
                        id=ids[key],
                        user_id=ids["other"] if key == "other_cash" else DEMO_ID,
                        external_ref=key,
                        name=key,
                        account_type=kind,
                        balance_cents=balance,
                        observed_at=AS_OF,
                    )
                )
            session.flush()
            for index, kind in enumerate(("CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT")):
                product_id = uuid4()
                session.add(
                    AssetProduct(
                        id=product_id,
                        product_code=f"TEST_{index}",
                        version_number=1,
                        name=kind,
                        asset_class=kind,
                        effective_from=AS_OF,
                        annual_yield_bps=100 + index,
                    )
                )
                session.flush()
                session.add(
                    AssetPosition(
                        user_id=DEMO_ID,
                        account_id=ids["managed"],
                        product_id=product_id,
                        principal_cents=(200_000, 50_000, 400_000)[index],
                        purchased_at=AS_OF,
                        status=("HELD", "UNKNOWN", "REDEEMED")[index],
                    )
                )
            session.add(
                CreditCardBill(
                    user_id=DEMO_ID,
                    account_id=ids["card"],
                    source_ref="statement-1",
                    statement_date=date(2026, 10, 1),
                    due_date=date(2026, 10, 20),
                    total_cents=90_000,
                    minimum_due_cents=9_000,
                    paid_cents=50_000,
                    status="PARTIALLY_PAID",
                )
            )
            for index, key in enumerate(("cash", "goal", "cash", "other_cash")):
                session.add(
                    Transaction(
                        id=UUID(int=index + 1),
                        user_id=ids["other"] if key == "other_cash" else DEMO_ID,
                        account_id=ids[key],
                        source_ref=f"tx-{index}",
                        direction="CREDIT",
                        amount_cents=100,
                        category="salary" if index == 0 else "food",
                        occurred_at=AS_OF,
                        observed_at=AS_OF,
                    )
                )
        api = create_app()

        api.dependency_overrides[get_engine] = lambda: engine
        try:
            with TestClient(api) as client:
                yield client, ids
        finally:
            engine.dispose()


def test_summary_separates_cash_positions_unknown_and_liability(
    fact_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, _ = fact_client
    response = client.get("/api/v1/accounts/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["simulation"] is True
    assert len(data["accounts"]) == 4
    assert data["cash_balance_cents"] == 1_300_000
    assert data["position_principal_cents"] == 200_000
    assert data["unknown_position_principal_cents"] == 50_000
    assert data["credit_card_unpaid_cents"] == 40_000
    assert data["oldest_account_observed_at"] == "2026-10-03T15:59:59Z"
    assert data["latest_account_observed_at"] == "2026-10-03T15:59:59Z"
    assert len(data["credit_card_bills"]) == 1
    assert "autonomous_funds_cents" not in data


def test_summary_keeps_one_snapshot_during_concurrent_cash_to_position_transfer(
    fact_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    from app.api.dependencies import get_engine

    client, ids = fact_client
    api = cast(FastAPI, client.app)
    engine = cast(Engine, api.dependency_overrides[get_engine]())
    transfer_committed = False

    def transfer_after_accounts_read(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        nonlocal transfer_committed
        compiled = context.compiled
        if (
            transfer_committed
            or compiled is None
            or not isinstance(compiled.statement, Select)
            or Account.__table__ not in compiled.statement.get_final_froms()
        ):
            return

        # The reader has completed its accounts SELECT. Commit a real transfer on
        # a second connection before it can issue its positions SELECT.
        with engine.begin() as writer:
            assert writer.connection.dbapi_connection is not connection.connection.dbapi_connection
            writer.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            writer.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            debit = writer.execute(
                update(Account)
                .where(Account.id == ids["cash"], Account.user_id == DEMO_ID)
                .values(balance_cents=Account.balance_cents - 100_000)
            )
            purchase = writer.execute(
                update(AssetPosition)
                .where(AssetPosition.user_id == DEMO_ID, AssetPosition.status == "HELD")
                .values(principal_cents=AssetPosition.principal_cents + 100_000)
            )
            assert debit.rowcount == 1
            assert purchase.rowcount == 1
        transfer_committed = True

    event.listen(engine, "after_cursor_execute", transfer_after_accounts_read)
    try:
        response = client.get("/api/v1/accounts/summary")
    finally:
        event.remove(engine, "after_cursor_execute", transfer_after_accounts_read)

    assert transfer_committed, "The concurrent database transfer must actually commit"
    assert response.status_code == 200
    original_snapshot = response.json()
    assert (
        original_snapshot["cash_balance_cents"],
        original_snapshot["position_principal_cents"],
    ) == (1_300_000, 200_000)

    # A fresh request observes the completed transfer rather than stale cached facts.
    response = client.get("/api/v1/accounts/summary")
    assert response.status_code == 200
    current_snapshot = response.json()
    assert (
        current_snapshot["cash_balance_cents"],
        current_snapshot["position_principal_cents"],
    ) == (1_200_000, 300_000)


def test_transactions_have_stable_pages_and_filters_without_other_user(
    fact_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, ids = fact_client
    response = client.get("/api/v1/transactions?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert [row["id"] for row in data["items"]] == [str(UUID(int=3)), str(UUID(int=2))]
    assert len(client.get("/api/v1/transactions?limit=2&offset=2").json()["items"]) == 1
    assert client.get("/api/v1/transactions?category=salary").json()["total"] == 1
    assert client.get(f"/api/v1/transactions?account_id={ids['cash']}").json()["total"] == 2
    assert client.get(f"/api/v1/transactions?account_id={ids['other_cash']}").status_code == 404


def test_product_terms_and_owned_position_state_are_queryable(
    fact_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, _ = fact_client
    products = client.get("/api/v1/products").json()
    assert products["simulation"] is True
    assert {row["asset_class"] for row in products["items"]} == {
        "CASH_MGMT_T0",
        "CASH_MGMT_T1",
        "FIXED_DEPOSIT",
    }
    assert all(isinstance(row["annual_yield_bps"], int) for row in products["items"])
    assert all(row["auto_purchase_allowed"] is False for row in products["items"])
    positions = client.get("/api/v1/positions").json()
    assert {row["status"] for row in positions["items"]} == {"HELD", "UNKNOWN", "REDEEMED"}


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "account_id=invalid"])
def test_invalid_transaction_query_uses_redacted_error(
    fact_client: tuple[TestClient, dict[str, UUID]], query: str
) -> None:
    client, _ = fact_client
    response = client.get(f"/api/v1/transactions?{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("fact_client", ["unexpected-demo-owner"], indirect=True)
def test_demo_id_collision_cannot_expose_another_identity(
    fact_client: tuple[TestClient, dict[str, UUID]],
) -> None:
    client, _ = fact_client
    for path in ("accounts/summary", "transactions", "products", "positions"):
        response = client.get(f"/api/v1/{path}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
