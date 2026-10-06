"""TOOL_TEST_ONLY synthetic observation flow; no DB, financial evaluator, or product evidence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.db.models import User
from app.services import execution_observations as obs
from sqlalchemy import event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

NOW = datetime(2026, 10, 5, 8, 15, tzinfo=UTC)
OWNER, ACTION, EPOCH = UUID(int=1), UUID(int=2), UUID(int=3)
NAME = "bf_test_" + "a" * 32
URL = make_url("postgresql+psycopg://bounded@127.0.0.1:54329/" + NAME)


class FakeDatabaseError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("TOOL_TEST_ONLY PostgreSQL status error")
        self.sqlstate = sqlstate


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    callbacks: dict[str, Any] = {}
    outputs: list[dict[str, Any]] = []
    guard_calls: list[Any] = []
    engine = SimpleNamespace(url=URL)

    def guard(session: Any) -> None:
        guard_calls.append(session)

    def capture(raw: bytes) -> None:
        outputs.append(json.loads(raw))

    binding = obs.Binding(
        cast(Engine, engine),
        OWNER,
        ACTION,
        EPOCH,
        NOW,
        uuid4(),
        b'{"purpose":"PURE_TOOL_TEST_ONLY"}',
        guard,
        capture,
    )
    token = obs._BINDING.set(binding)
    monkeypatch.setattr(event, "listen", lambda session, name, fn: callbacks.__setitem__(name, fn))
    monkeypatch.setattr(event, "remove", lambda session, name, fn: callbacks.pop(name))

    class FakeSession:
        @contextmanager
        def begin(self) -> Iterator[None]:
            try:
                yield
            except BaseException:
                if "after_rollback" in callbacks:
                    callbacks["after_rollback"](self)
                raise
            else:
                if "after_commit" in callbacks:
                    callbacks["after_commit"](self)

        def in_nested_transaction(self) -> bool:
            return False

    session = FakeSession()

    def live(phase: Any) -> dict[str, Any]:
        phase.tx = {"database_name": NAME, "backend_pid": 11, "transaction_id": "58554"}
        return {"sql_identity": phase.tx, "tables": {}, "nested_savepoint_active": False}

    monkeypatch.setattr(obs, "_live", live)
    try:
        yield SimpleNamespace(
            binding=binding,
            engine=engine,
            session=session,
            outputs=outputs,
            guard_calls=guard_calls,
            callbacks=callbacks,
        )
    finally:
        obs._BINDING.reset(token)
        assert obs._PHASE.get() is None


def terminal(setup: Any, *, committed: bool = True, rolled_back: bool = False) -> tuple[Any, bytes]:
    phase = obs.Phase(
        setup.binding, cast(Session, setup.session), "APPLICATION_RESERVATION", str(uuid4())
    )
    phase.tx = {"database_name": NAME, "backend_pid": 11, "transaction_id": "58554"}
    phase.outer_committed, phase.outer_rolled_back = committed, rolled_back
    state = "COMMITTED" if committed and not rolled_back else obs._terminal_state(phase)
    raw = obs._emit(phase, state, None)
    return phase, raw


def probe_double(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: Any = "committed",
    capability: bool = True,
    missing_user: bool = False,
    simulated: bool = True,
    epoch_status: str = "OPEN",
    identity_overrides: dict[str, Any] | None = None,
) -> Any:
    created: list[Any] = []
    sql: list[str] = []
    disposals: list[bool] = []
    identity = {
        "database_name": NAME,
        "backend_pid": 12,
        "transaction_id": "58555",
        "isolation": "repeatable read",
        "read_only": "on",
        "server_version_num": "160015",
    }
    identity.update(identity_overrides or {})

    def create(url: Any, **options: Any) -> Any:
        created.append((url, options))
        return SimpleNamespace(dispose=lambda: disposals.append(True))

    class Result:
        def __init__(self, row: dict[str, Any]) -> None:
            self.row = row

        def mappings(self) -> Result:
            return self

        def one(self) -> dict[str, Any]:
            return self.row

    class ReadSession:
        no_autoflush = nullcontext()

        def __enter__(self) -> ReadSession:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def begin(self) -> Any:
            return nullcontext()

        def begin_nested(self) -> Any:
            return nullcontext()

        def get(self, model: Any, key: Any) -> Any:
            if model is User:
                return None if missing_user else SimpleNamespace(id=OWNER, is_simulated=simulated)
            return SimpleNamespace(user_id=OWNER, status=epoch_status)

        def execute(self, statement: Any, parameters: Any = None) -> Result:
            value = str(statement)
            sql.append(value)
            if value == obs.RECHECK_SQL_IDENTITY:
                return Result(identity)
            if value == obs.RECHECK_SQL_CAPABILITY:
                return Result(
                    {
                        "procedure": "pg_xact_status(xid8)" if capability else None,
                        "may_execute": capability,
                    }
                )
            if value == obs.RECHECK_SQL_STATUS:
                assert parameters == {"target_xid": "58554"}
                if isinstance(status, Exception):
                    raise status
                return Result({"target_status": status})
            assert value == "SET TRANSACTION READ ONLY"
            return Result({})

    monkeypatch.setattr(obs, "create_engine", create)
    monkeypatch.setattr(obs, "Session", lambda engine: ReadSession())
    monkeypatch.setattr(
        obs,
        "_persistent_tables",
        lambda session, binding: (
            {"users": [{"id": str(OWNER), "is_simulated": True}]},
            [],
        ),
    )
    return SimpleNamespace(created=created, sql=sql, disposals=disposals)


@pytest.mark.parametrize(
    "bad", [None, 58554, True, "", "0", "058554", "-1", "1.2", "١٢", str(2**64)]
)
def test_full_xid_rejects_missing_noncanonical_and_overflow(bad: Any) -> None:
    with pytest.raises(obs.ObservationFailure):
        obs._target_xid({"transaction_id": bad})


def test_full_xid_retains_epoch_bits() -> None:
    assert obs._target_xid({"transaction_id": str(2**32 + 71)}) == "4294967367"


@pytest.mark.parametrize(
    "overrides",
    [
        {"transaction_id": "58554"},
        {"database_name": "bounded_funds"},
        {"read_only": "off"},
        {"isolation": "read committed"},
        {"backend_pid": 11},
        {"backend_pid": 0},
        {"backend_pid": True},
    ],
)
def test_independent_identity_rejects_same_tx_or_scope(
    setup: Any, overrides: dict[str, Any]
) -> None:
    phase, _ = terminal(setup)
    identity = {
        "database_name": NAME,
        "transaction_id": "58555",
        "backend_pid": 12,
        "read_only": "on",
        "isolation": "repeatable read",
    }
    identity.update(overrides)
    with pytest.raises(obs.ObservationFailure):
        obs._recheck_identity(phase, identity)


def test_probe_new_connection_readonly_current_guard_and_raw_rows(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    phase, raw = terminal(setup)
    fake = probe_double(monkeypatch)
    result = obs._persistent_probe(phase, raw)
    assert fake.created[0][0] == URL
    assert fake.created[0][1]["poolclass"] is NullPool
    assert fake.created[0][1]["isolation_level"] == "REPEATABLE READ"
    assert fake.sql[0] == "SET TRANSACTION READ ONLY"
    assert len(setup.guard_calls) == 1 and fake.disposals == [True]
    assert result["target_status"] == "committed"
    assert (
        result["probe_sql_identity"]["transaction_id"]
        != result["target_sql_identity"]["transaction_id"]
    )
    assert result["table_inventory"]["users"]["row_ids"] == [str(OWNER)]
    assert result["service_call_id"] == str(setup.binding.service_call_id)
    assert result["financial_effect_verified"] is False
    assert result["independent_phase_proof_verified"] is False
    assert result["capture_state"] == "CAPTURED_VALIDATOR_PENDING"


@pytest.mark.parametrize("status", [None, "in progress", "unrecognized"])
def test_nonfinal_status_remains_missing(
    setup: Any, monkeypatch: pytest.MonkeyPatch, status: Any
) -> None:
    phase, raw = terminal(setup)
    probe_double(monkeypatch, status=status)
    result = obs._persistent_probe(phase, raw)
    assert result["capture_state"] == "MISSING"
    assert result["target_status"] == status
    assert result["independent_phase_proof_verified"] is False


def test_missing_function_preserves_rows_without_call(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    phase, raw = terminal(setup)
    fake = probe_double(monkeypatch, capability=False)
    result = obs._persistent_probe(phase, raw)
    assert result["capture_state"] == "MISSING" and result["tables"] is not None
    assert obs.RECHECK_SQL_STATUS not in fake.sql


@pytest.mark.parametrize("sqlstate", ["22023", "22003", "42704", "42883", "42501"])
def test_expired_or_unavailable_xid_remains_missing(
    setup: Any, monkeypatch: pytest.MonkeyPatch, sqlstate: str
) -> None:
    phase, raw = terminal(setup)
    error = DBAPIError(None, None, FakeDatabaseError(sqlstate))
    fake = probe_double(monkeypatch, status=error)
    result = obs._persistent_probe(phase, raw)
    assert result["capture_state"] == "MISSING" and result["target_status"] is None
    assert result["target_status_error"]["sqlstate"] == sqlstate
    assert result["tables"]["users"] and fake.disposals == [True]


def test_network_probe_error_disposes_and_propagates(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    phase, raw = terminal(setup)
    error = DBAPIError(None, None, FakeDatabaseError("08006"))
    fake = probe_double(monkeypatch, status=error)
    with pytest.raises(DBAPIError) as caught:
        obs._persistent_probe(phase, raw)
    assert caught.value is error and fake.disposals == [True]


@pytest.mark.parametrize(
    "options", [{"missing_user": True}, {"simulated": False}, {"epoch_status": "CLOSED"}]
)
def test_probe_rechecks_actual_user_and_open_epoch(
    setup: Any, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    phase, raw = terminal(setup)
    fake = probe_double(monkeypatch, **options)
    with pytest.raises(obs.ObservationFailure):
        obs._persistent_probe(phase, raw)
    assert fake.disposals == [True]


def test_aftercommit_probe_failure_does_not_emit_rollback(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = RuntimeError("TOOL_TEST_ONLY probe capture failure")

    def fail(*args: Any) -> None:
        raise error

    monkeypatch.setattr(obs, "_capture_recheck", fail)
    with (
        pytest.raises(RuntimeError) as caught,
        obs.observed_begin(setup.session, setup.engine, OWNER, ACTION, NOW, "INDEPENDENT_BANK"),
    ):
        pass
    assert caught.value is error
    assert any("OBSERVATION_FAILED_AFTER_COMMIT" in n for n in error.__notes__)
    assert setup.outputs[-1]["state"] == "COMMITTED"
    assert setup.outputs[-1]["outer_after_commit_observed"] is True
    assert all(row["state"] != "ROLLED_BACK" for row in setup.outputs)
    assert not setup.callbacks


def test_rollback_probe_error_preserves_business_firstcause(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = ValueError("TOOL_TEST_ONLY original business refusal")

    def fail(*args: Any) -> None:
        raise RuntimeError("TOOL_TEST_ONLY secondary probe error")

    monkeypatch.setattr(obs, "_capture_recheck", fail)
    with (
        pytest.raises(ValueError) as caught,
        obs.observed_begin(
            setup.session, setup.engine, OWNER, ACTION, NOW, "APPLICATION_RESERVATION"
        ),
    ):
        raise primary
    assert caught.value is primary
    assert setup.outputs[-1]["state"] == "ROLLED_BACK"
    assert setup.outputs[-1]["error"]["message"] == str(primary)
    assert any("secondary probe error" in n for n in primary.__notes__)
    assert not setup.callbacks


def test_terminal_state_never_calls_committed_rollback(setup: Any) -> None:
    phase, _ = terminal(setup)
    assert obs._terminal_state(phase) == "COMMITTED_THEN_ERROR"
    phase.outer_rolled_back = True
    assert obs._terminal_state(phase) == "UNKNOWN_TRANSACTION_OUTCOME"
    phase.outer_committed = False
    assert obs._terminal_state(phase) == "ROLLED_BACK"


def test_missing_target_no_sql_attempt(setup: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    phase, _ = terminal(setup)
    phase.tx = None
    raw = obs._emit(phase, "COMMITTED", None)
    fake = probe_double(monkeypatch)
    assert (
        obs._persistent_probe(phase, raw)["missing_reason"]
        == "ORIGINAL_TARGET_SQL_IDENTITY_MISSING"
    )
    assert not fake.created


def test_unknown_outer_outcome_no_independent_final_status(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    phase, raw = terminal(setup, committed=False, rolled_back=False)
    fake = probe_double(monkeypatch)
    assert obs._persistent_probe(phase, raw)["capture_state"] == "MISSING"
    assert not fake.created


def test_no_observer_original_begin_only_zero_observation_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    class DefaultSession:
        @contextmanager
        def begin(self) -> Iterator[None]:
            calls.append("original_begin")
            yield
            calls.append("original_commit")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Default path must never create a probe or observation query")

    monkeypatch.setattr(obs, "create_engine", forbidden)
    monkeypatch.setattr(obs, "_live", forbidden)
    assert obs._BINDING.get() is None
    with obs.observed_begin(
        cast(Session, DefaultSession()), cast(Engine, None), OWNER, ACTION, NOW, "INDEPENDENT_BANK"
    ):
        calls.append("original_body")
    assert calls == ["original_begin", "original_body", "original_commit"]


def test_arbitrary_callback_cannot_install_observer() -> None:
    with (
        pytest.raises(obs.ObservationFailure),
        obs.observation_scope(
            cast(Engine, SimpleNamespace(url=URL)),
            OWNER,
            ACTION,
            EPOCH,
            NOW,
            {},
            service_call_id=uuid4(),
            guard=lambda session: None,
            callback=lambda raw: None,
        ),
    ):
        raise AssertionError("Arbitrary callback must fail before body or SQL")


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_call_id", str(UUID(int=99))),
        ("phase_id", str(UUID(int=99))),
        ("user_id", str(UUID(int=99))),
        ("action_id", str(UUID(int=99))),
        ("epoch_id", str(UUID(int=99))),
        ("phase", "INDEPENDENT_BANK"),
        ("state", "ROLLED_BACK"),
        ("financial_effect_verified", True),
        ("outer_after_commit_observed", False),
        ("business_clock", "2026-10-06T08:15:00+00:00"),
    ],
)
def test_terminal_splicing_rejected_before_probe_sql(
    setup: Any, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    phase, raw = terminal(setup)
    body = json.loads(raw)
    body[field] = value
    fake = probe_double(monkeypatch)
    with pytest.raises(obs.ObservationFailure):
        obs._persistent_probe(phase, json.dumps(body).encode())
    assert not fake.created


def test_target_transaction_from_other_database_rejected(setup: Any) -> None:
    phase, _ = terminal(setup)
    phase.tx["database_name"] = "bf_test_" + "b" * 32
    with pytest.raises(obs.ObservationFailure):
        obs._recheck_identity(
            phase,
            {
                "database_name": NAME,
                "backend_pid": 12,
                "transaction_id": "58555",
                "read_only": "on",
                "isolation": "repeatable read",
            },
        )


def test_rollback_recheck_preserves_actual_aborted_status(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    phase, raw = terminal(setup, committed=False, rolled_back=True)
    probe_double(monkeypatch, status="aborted")
    result = obs._persistent_probe(phase, raw)
    assert result["expected_target_status"] == result["target_status"] == "aborted"
    assert result["independent_phase_proof_verified"] is False


def test_every_probe_uses_new_connection_and_current_guard(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    phase, raw = terminal(setup)
    fake = probe_double(monkeypatch)
    obs._persistent_probe(phase, raw)
    obs._persistent_probe(phase, raw)
    assert len(fake.created) == 2 and fake.disposals == [True, True]
    assert len(setup.guard_calls) == 2
    assert setup.guard_calls[0] is not setup.guard_calls[1]


def test_persistent_original_queries_cover_seven_local_tables_and_owned_user(setup: Any) -> None:
    statements: list[Any] = []

    class Rows:
        def mappings(self) -> list[dict[str, Any]]:
            return []

    class QuerySession:
        no_autoflush = nullcontext()

        def execute(self, statement: Any) -> Rows:
            statements.append(statement)
            return Rows()

    tables, queries = obs._persistent_tables(cast(Session, QuerySession()), setup.binding)
    assert set(tables) == {
        "action_plans",
        "action_resource_reservations",
        "bank_operations",
        "action_receipts",
        "simulated_bank_postings",
        "accounts",
        "evidence_items",
        "users",
    }
    assert len(statements) == len(queries) == 8
    for statement, query in zip(statements, queries, strict=True):
        assert str(statement).startswith("SELECT ")
        parameters = statement.compile().params
        assert OWNER in parameters.values()
        if query["table"] in {
            "action_plans",
            "action_resource_reservations",
            "bank_operations",
            "action_receipts",
            "simulated_bank_postings",
        }:
            assert ACTION in parameters.values()
        elif query["table"] == "users":
            assert all(value == OWNER for value in parameters.values())
