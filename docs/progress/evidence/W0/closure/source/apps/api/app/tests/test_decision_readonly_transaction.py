"""Decision and receipt GETs protect the transaction before the first user lookup."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import app.tests.test_goal_api as goal_fixture
import pytest
from alembic import command
from app.db.models import DecisionRun
from app.db.testing import temporary_database
from app.services.demo_seed import seed_demo
from app.tests.test_autonomy_api import transfer_intent
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExecutionContext
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _timed[**P, T](phase: str, function: Callable[P, T]) -> Callable[P, T]:
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            print(f"ACTUAL_PHASE {phase} {perf_counter() - started:.3f}s")

    return call


@pytest.fixture(autouse=True)
def actual_setup_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transparent timing around the actual guarded fixture; results are not substituted."""
    original_database = temporary_database

    @contextmanager
    def database() -> Iterator[str]:
        started = perf_counter()
        with original_database() as url:
            print(f"ACTUAL_PHASE create_test_database {perf_counter() - started:.3f}s")
            try:
                yield url
            finally:
                started = perf_counter()
        print(f"ACTUAL_PHASE cleanup_test_database {perf_counter() - started:.3f}s")

    monkeypatch.setattr(goal_fixture, "temporary_database", database)
    monkeypatch.setattr(command, "upgrade", _timed("migrate_test_database", command.upgrade))
    monkeypatch.setattr(goal_fixture, "seed_demo", _timed("native_seed", seed_demo))


@contextmanager
def _first_user_query(engine: Engine) -> Iterator[list[tuple[str, str]]]:
    observed: list[tuple[str, str]] = []

    def inspect_transaction(
        connection: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        query = " ".join(statement.lower().split())
        if not observed and query.startswith("select ") and " from users " in query:
            observed.append(
                (
                    str(connection.exec_driver_sql("SHOW transaction_isolation").scalar_one()),
                    str(connection.exec_driver_sql("SHOW transaction_read_only").scalar_one()),
                )
            )

    event.listen(engine, "before_cursor_execute", inspect_transaction)
    try:
        yield observed
    finally:
        event.remove(engine, "before_cursor_execute", inspect_transaction)


def test_history_and_receipt_gets_are_readonly_before_the_first_user_select(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    missing = uuid4()
    paths = [
        ("/api/v1/decisions", 200),
        (f"/api/v1/decisions/{missing}", 404),
        (f"/api/v1/decisions/{missing}/explanation", 404),
        (f"/api/v1/actions/{missing}/decision", 404),
        (f"/api/v1/actions/{missing}/receipt", 404),
    ]
    before = all_tables(engine)
    with _first_user_query(engine) as observed:
        for path, status in paths:
            observed.clear()
            response = _timed(f"readonly_get:{path}", client.get)(path)
            assert response.status_code == status, response.text
            assert observed == [("repeatable read", "on")], path
    assert all_tables(engine) == before


def test_history_errors_discard_previous_success_and_never_repair_or_make_commands_readonly(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    intent = transfer_intent(engine)
    with _first_user_query(engine) as observed:
        response = _timed("actual_prepare", client.post)(
            "/api/v1/actions/prepare",
            json={"idempotency_key": "readonly-transaction-prepare", "intent": intent},
        )
        assert response.status_code == 200, response.text
        action = response.json()
        assert observed == [("read committed", "off")]
    action_path = f"/api/v1/actions/{action['action_id']}"
    before = all_tables(engine)
    with _first_user_query(engine) as observed:
        original = client.get(action_path + "/decision")
        assert original.status_code == 200, original.text
        assert observed == [("repeatable read", "on")]
        run_id = UUID(original.json()["run_id"])
        observed.clear()
        missing = client.get(f"/api/v1/decisions/{uuid4()}")
        assert missing.status_code == 404
        assert set(missing.json()) == {"error"}
        assert observed == [("repeatable read", "on")]
        observed.clear()
        receipt = client.get(action_path + "/receipt")
        assert receipt.status_code == 409
        assert receipt.json()["error"]["code"] == "RECEIPT_NOT_READY"
        assert set(receipt.json()) == {"error"}
        assert observed == [("repeatable read", "on")]
    assert all_tables(engine) == before

    with Session(engine) as session, session.begin():
        run = session.get(DecisionRun, run_id)
        assert run is not None
        run.snapshot_hash = "0" * 64
    before = all_tables(engine)
    with _first_user_query(engine) as observed:
        for path in [
            f"/api/v1/decisions/{run_id}",
            f"/api/v1/decisions/{run_id}/explanation",
            action_path + "/decision",
        ]:
            observed.clear()
            response = _timed(f"integrity_error_get:{path}", client.get)(path)
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "DECISION_TRACE_INTEGRITY_ERROR"
            assert set(response.json()) == {"error"}
            assert observed == [("repeatable read", "on")]
    assert all_tables(engine) == before
