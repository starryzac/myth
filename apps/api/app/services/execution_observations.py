"""Per-invocation live SQL/outer transaction observations; independent proof is separate."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from types import CodeType, FunctionType
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AuditEpoch,
    BankOperation,
    EvidenceItem,
    SimulatedBankPosting,
    User,
)
from app.db.settings import REPOSITORY_ROOT
from app.db.testing import require_test_database
from sqlalchemy import Table, create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

PhaseName = Literal["APPLICATION_RESERVATION", "INDEPENDENT_BANK", "APPLICATION_PROJECTION"]
Callback = Callable[[bytes], None]
Guard = Callable[[Session], None]


class ObservationFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class Binding:
    engine: Engine
    user_id: UUID
    action_id: UUID
    epoch_id: UUID
    now: datetime
    service_call_id: UUID
    bindings_json: bytes
    guard: Guard
    callback: Callback


@dataclass
class Phase:
    binding: Binding
    session: Session
    name: PhaseName
    identity: str
    tx: dict[str, Any] | None = None
    before_commit: dict[str, Any] | None = None
    outer_committed: bool = False
    outer_rolled_back: bool = False


_BINDING: ContextVar[Binding | None] = ContextVar(
    "execution_original_observation_binding", default=None
)
_PHASE: ContextVar[Phase | None] = ContextVar("execution_original_observation_phase", default=None)


def _provider_callback(function: Callable[..., Any]) -> None:
    original = REPOSITORY_ROOT / "apps/api/app/services/experiment_arms.py"
    if (
        not isinstance(function, FunctionType)
        or function.__module__ != "app.services.experiment_arms"
        or Path(inspect.getsourcefile(function) or "").resolve() != original.resolve()
    ):
        raise ObservationFailure("Only the actual byte-bound provider can install an observer")
    compiled = compile(original.read_bytes(), str(original), "exec", dont_inherit=True)

    def nested(code: CodeType) -> Iterator[CodeType]:
        yield code
        for constant in code.co_consts:
            if isinstance(constant, CodeType):
                yield from nested(constant)

    if not any(function.__code__ == code for code in nested(compiled)):
        raise ObservationFailure("Loaded provider callback differs from current original source")


@contextmanager
def observation_scope(
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    epoch_id: UUID,
    now: datetime,
    bindings: dict[str, str],
    *,
    service_call_id: UUID,
    guard: Guard,
    callback: Callback,
) -> Iterator[None]:
    if not isinstance(service_call_id, UUID):
        raise ObservationFailure("Actual invocation UUID is required")
    if _BINDING.get() is not None or now.tzinfo is None or now.utcoffset() is None:
        raise ObservationFailure("Nested observer invocation or untrusted clock")
    if (
        engine.url.get_backend_name() != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.port != 54329
    ):
        raise ObservationFailure("Observer only supports the original local simulation endpoint")
    require_test_database(engine.url.database)
    _provider_callback(guard)
    _provider_callback(callback)
    frozen = json.dumps(bindings, sort_keys=True, allow_nan=False).encode("utf-8")
    binding = Binding(
        engine,
        user_id,
        action_id,
        epoch_id,
        now.astimezone(UTC),
        service_call_id,
        frozen,
        guard,
        callback,
    )
    token = _BINDING.set(binding)
    try:
        yield
    finally:
        _BINDING.reset(token)


def _primitive(value: Any) -> Any:
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError("Unknown original SQL value type: " + type(value).__name__)


def _live(phase: Phase) -> dict[str, Any]:
    binding, session = phase.binding, phase.session
    if session.get_bind() is not binding.engine:
        raise ObservationFailure("Actual live Session engine differs")
    # Observation reads do not trigger an application flush.
    with session.no_autoflush:
        binding.guard(session)  # Re-read source/context/registration; no authorization cache.
        owner = session.get(User, binding.user_id)
        epoch = session.get(AuditEpoch, binding.epoch_id)
    if (
        owner is None
        or not owner.is_simulated
        or epoch is None
        or epoch.user_id != binding.user_id
        or epoch.status != "OPEN"
    ):
        raise ObservationFailure("Actual simulation user/open original epoch differs")
    identity = dict(
        session.execute(
            text(
                "SELECT current_database() AS database_name, pg_backend_pid() AS backend_pid, "
                "txid_current()::text AS transaction_id"
            )
        )
        .mappings()
        .one()
    )
    if identity["database_name"] != require_test_database(binding.engine.url.database):
        raise ObservationFailure("Actual transaction database differs")
    if phase.tx is None:
        phase.tx = identity
    elif identity != phase.tx:
        raise ObservationFailure("Original phase changed its actual SQL transaction identity")
    tables = {}
    filters = (
        (ActionPlan, ActionPlan.id == binding.action_id),
        (ActionResourceReservation, ActionResourceReservation.action_plan_id == binding.action_id),
        (BankOperation, BankOperation.action_plan_id == binding.action_id),
        (ActionReceipt, ActionReceipt.action_plan_id == binding.action_id),
        (SimulatedBankPosting, SimulatedBankPosting.operation_id == binding.action_id),
        (Account, Account.user_id == binding.user_id),
        (EvidenceItem, EvidenceItem.user_id == binding.user_id),
    )
    with session.no_autoflush:
        for model, clause in filters:
            table = cast(Table, model.__table__)
            rows = [
                dict(row)
                for row in session.execute(
                    select(table)
                    .where(table.c.user_id == binding.user_id, clause)
                    .order_by(table.c.id)
                ).mappings()
            ]
            tables[table.name] = rows
    return cast(
        dict[str, Any],
        json.loads(
            json.dumps(
                {
                    "sql_identity": identity,
                    "tables": tables,
                    "nested_savepoint_active": session.in_nested_transaction(),
                },
                default=_primitive,
                sort_keys=True,
                allow_nan=False,
            )
        ),
    )


def _emit(
    phase: Phase, state: str, live: dict[str, Any] | None, error: BaseException | None = None
) -> bytes:
    body = {
        "protocol": "execution-live-phase-observation-v2",
        "service_call_id": str(phase.binding.service_call_id),
        "bindings": json.loads(phase.binding.bindings_json),
        "user_id": str(phase.binding.user_id),
        "action_id": str(phase.binding.action_id),
        "epoch_id": str(phase.binding.epoch_id),
        "business_clock": phase.binding.now.isoformat(),
        "observed_at": datetime.now(UTC).isoformat(),
        "perf_counter_ns": perf_counter_ns(),
        "phase": phase.name,
        "phase_id": phase.identity,
        "state": state,
        "sql_identity": phase.tx,
        "live": live,
        "outer_after_commit_observed": phase.outer_committed,
        "outer_after_rollback_observed": phase.outer_rolled_back,
        "error": {"type": type(error).__name__, "message": str(error)} if error else None,
        "financial_effect_verified": False,
    }
    # Caller gets no mutable ORM state or Session; bytes cannot rewrite an economic request.
    raw = json.dumps(body, sort_keys=True, allow_nan=False).encode("utf-8")
    phase.binding.callback(raw)
    return raw


RECHECK_SQL_IDENTITY = (
    "SELECT current_database() AS database_name, pg_backend_pid() AS backend_pid, "
    "txid_current()::text AS transaction_id, "
    "current_setting('transaction_isolation') AS isolation, "
    "current_setting('transaction_read_only') AS read_only, "
    "current_setting('server_version_num') AS server_version_num"
)
RECHECK_SQL_CAPABILITY = (
    "SELECT to_regprocedure('pg_xact_status(xid8)')::text AS procedure, "
    "CASE WHEN to_regprocedure('pg_xact_status(xid8)') IS NULL THEN NULL ELSE "
    "has_function_privilege(current_user, to_regprocedure('pg_xact_status(xid8)'), 'EXECUTE') "
    "END AS may_execute"
)
RECHECK_SQL_STATUS = "SELECT pg_xact_status(CAST(:target_xid AS xid8)) AS target_status"


def _terminal_state(phase: Phase) -> str:
    if phase.outer_committed and not phase.outer_rolled_back:
        return "COMMITTED_THEN_ERROR"
    if phase.outer_rolled_back and not phase.outer_committed:
        return "ROLLED_BACK"
    return "UNKNOWN_TRANSACTION_OUTCOME"


def _target_xid(identity: dict[str, Any] | None) -> str:
    value = identity.get("transaction_id") if isinstance(identity, dict) else None
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > 20
        or value != str(int(value))
        or not 0 < int(value) < 2**64
    ):
        raise ObservationFailure("Original full xid8 identity is missing or invalid")
    return value


def _recheck_identity(phase: Phase, identity: dict[str, Any]) -> None:
    expected_database = require_test_database(phase.binding.engine.url.database)
    if (
        identity.get("database_name") != expected_database
        or phase.tx is None
        or phase.tx.get("database_name") != expected_database
        or identity.get("isolation") != "repeatable read"
        or identity.get("read_only") != "on"
        or _target_xid(identity) == _target_xid(phase.tx)
        or not isinstance(identity.get("backend_pid"), int)
        or isinstance(identity.get("backend_pid"), bool)
        or identity["backend_pid"] <= 0
        or identity["backend_pid"] == phase.tx.get("backend_pid")
    ):
        raise ObservationFailure("Independent readonly transaction identity differs")


def _terminal_binding(phase: Phase, body: dict[str, Any]) -> None:
    binding = phase.binding
    expected_state = (
        {"COMMITTED", "COMMITTED_THEN_ERROR"}
        if phase.outer_committed and not phase.outer_rolled_back
        else {"ROLLED_BACK"}
        if phase.outer_rolled_back and not phase.outer_committed
        else {"UNKNOWN_TRANSACTION_OUTCOME"}
    )
    expected = {
        "protocol": "execution-live-phase-observation-v2",
        "service_call_id": str(binding.service_call_id),
        "phase_id": phase.identity,
        "phase": phase.name,
        "user_id": str(binding.user_id),
        "action_id": str(binding.action_id),
        "epoch_id": str(binding.epoch_id),
        "business_clock": binding.now.isoformat(),
        "bindings": json.loads(binding.bindings_json),
        "sql_identity": phase.tx,
        "outer_after_commit_observed": phase.outer_committed,
        "outer_after_rollback_observed": phase.outer_rolled_back,
        "financial_effect_verified": False,
    }
    if (
        any(body.get(name) != value for name, value in expected.items())
        or body.get("state") not in expected_state
    ):
        raise ObservationFailure(
            "Original terminal observation is rebound or contradicts its phase"
        )


def _persistent_tables(
    session: Session, binding: Binding
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    filters = (
        (ActionPlan, ActionPlan.id == binding.action_id),
        (ActionResourceReservation, ActionResourceReservation.action_plan_id == binding.action_id),
        (BankOperation, BankOperation.action_plan_id == binding.action_id),
        (ActionReceipt, ActionReceipt.action_plan_id == binding.action_id),
        (SimulatedBankPosting, SimulatedBankPosting.operation_id == binding.action_id),
        (Account, Account.user_id == binding.user_id),
        (EvidenceItem, EvidenceItem.user_id == binding.user_id),
        (User, User.id == binding.user_id),
    )
    tables: dict[str, Any] = {}
    queries = []
    with session.no_autoflush:
        for model, clause in filters:
            table = cast(Table, model.__table__)
            owner_clause = (
                table.c.id == binding.user_id
                if table.name == "users"
                else table.c.user_id == binding.user_id
            )
            statement = select(table).where(owner_clause, clause).order_by(table.c.id)
            queries.append(
                {
                    "table": table.name,
                    "sql": str(statement),
                    "parameters": dict(statement.compile().params),
                }
            )
            tables[table.name] = [dict(row) for row in session.execute(statement).mappings()]
    return tables, queries


def _persistent_probe(phase: Phase, terminal: bytes) -> dict[str, Any]:
    binding = phase.binding
    terminal_body = json.loads(terminal)
    _terminal_binding(phase, terminal_body)
    body: dict[str, Any] = {
        "protocol": "execution-independent-phase-recheck-v2",
        "bindings": json.loads(binding.bindings_json),
        "service_call_id": str(binding.service_call_id),
        "phase_id": phase.identity,
        "phase": phase.name,
        "user_id": str(binding.user_id),
        "action_id": str(binding.action_id),
        "epoch_id": str(binding.epoch_id),
        "business_clock": binding.now.isoformat(),
        "terminal_state": terminal_body["state"],
        "terminal_value_sha256": hashlib.sha256(
            json.dumps(
                terminal_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "target_sql_identity": phase.tx,
        "expected_target_status": "committed"
        if phase.outer_committed and not phase.outer_rolled_back
        else "aborted"
        if phase.outer_rolled_back and not phase.outer_committed
        else None,
        "target_status": None,
        "probe_sql_identity": None,
        "capability": None,
        "tables": None,
        "table_inventory": None,
        "queries": [],
        "capture_state": "MISSING",
        "missing_reason": None,
        "financial_effect_verified": False,
        "independent_phase_proof_verified": False,
        "started_at": datetime.now(UTC).isoformat(),
    }
    if body["expected_target_status"] is None:
        body["missing_reason"] = "ORIGINAL_OUTER_TRANSACTION_OUTCOME_UNKNOWN"
        body["finished_at"] = datetime.now(UTC).isoformat()
        return body
    if phase.tx is None:
        body["missing_reason"] = "ORIGINAL_TARGET_SQL_IDENTITY_MISSING"
        body["finished_at"] = datetime.now(UTC).isoformat()
        return body
    target = _target_xid(phase.tx)
    independent = create_engine(
        binding.engine.url,
        poolclass=NullPool,
        isolation_level="REPEATABLE READ",
        connect_args={"options": "-c timezone=UTC -c statement_timeout=5000 -c lock_timeout=1000"},
    )
    try:
        with Session(independent) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            with session.no_autoflush:
                binding.guard(
                    session
                )  # Current source, actual context/owner/OPEN epoch every time.
                user = session.get(User, binding.user_id)
                epoch = session.get(AuditEpoch, binding.epoch_id)
            if (
                user is None
                or user.is_simulated is not True
                or epoch is None
                or epoch.user_id != binding.user_id
                or epoch.status != "OPEN"
            ):
                raise ObservationFailure("Independent current user/open epoch is rebound")
            identity = dict(session.execute(text(RECHECK_SQL_IDENTITY)).mappings().one())
            _recheck_identity(phase, identity)
            body["probe_sql_identity"] = identity
            body["queries"].extend(
                [
                    {"sql": "SET TRANSACTION READ ONLY", "parameters": {}},
                    {"sql": RECHECK_SQL_IDENTITY, "parameters": {}},
                    {"sql": RECHECK_SQL_CAPABILITY, "parameters": {}},
                ]
            )
            body["capability"] = dict(
                session.execute(text(RECHECK_SQL_CAPABILITY)).mappings().one()
            )
            tables, queries = _persistent_tables(session, binding)
            tables = json.loads(
                json.dumps(tables, default=_primitive, sort_keys=True, allow_nan=False)
            )
            body["tables"] = tables
            body["queries"].extend(queries)
            body["table_inventory"] = {
                name: {
                    "row_ids": [row["id"] for row in rows],
                    "sha256": hashlib.sha256(
                        json.dumps(
                            rows,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                for name, rows in tables.items()
            }
            if (
                body["capability"]["procedure"] is None
                or body["capability"]["may_execute"] is not True
            ):
                body["missing_reason"] = "PG_XACT_STATUS_CAPABILITY_MISSING"
            else:
                body["queries"].append(
                    {"sql": RECHECK_SQL_STATUS, "parameters": {"target_xid": target}}
                )
                try:
                    # An unavailable xid query cannot poison subsequent original readonly capture.
                    with session.begin_nested():
                        body["target_status"] = (
                            session.execute(text(RECHECK_SQL_STATUS), {"target_xid": target})
                            .mappings()
                            .one()["target_status"]
                        )
                except DBAPIError as error:
                    state = getattr(error.orig, "sqlstate", None)
                    if state not in {"22023", "22003", "42704", "42883", "42501"}:
                        raise
                    body["target_status_error"] = {"type": type(error).__name__, "sqlstate": state}
                    body["missing_reason"] = "PG_XACT_STATUS_UNAVAILABLE_FOR_ORIGINAL_XID"
                if body["target_status"] in {"committed", "aborted"}:
                    body["capture_state"] = "CAPTURED_VALIDATOR_PENDING"
                elif body["missing_reason"] is None:
                    body["missing_reason"] = "TARGET_STATUS_NOT_FINAL_OR_UNAVAILABLE"
        body["readonly_outer_context_exited"] = "NORMAL"
    finally:
        independent.dispose()
    body["finished_at"] = datetime.now(UTC).isoformat()
    return body


def _capture_recheck(phase: Phase, terminal: bytes) -> None:
    body = _persistent_probe(phase, terminal)
    phase.binding.callback(
        json.dumps(body, default=_primitive, sort_keys=True, allow_nan=False).encode("utf-8")
    )


@contextmanager
def observed_begin(
    session: Session,
    engine: Engine,
    user_id: UUID,
    action_id: UUID,
    now: datetime,
    phase_name: PhaseName,
) -> Iterator[None]:
    binding = _BINDING.get()
    if binding is None:
        with session.begin():
            yield
        return
    if (
        engine is not binding.engine
        or user_id != binding.user_id
        or action_id != binding.action_id
        or now.tzinfo is None
        or now.utcoffset() is None
        or now.astimezone(UTC) != binding.now
        or _PHASE.get() is not None
    ):
        raise ObservationFailure("Actual phase invocation is rebound or nested")
    phase = Phase(binding, session, phase_name, str(uuid4()))

    def committed(actual: Session) -> None:
        if actual is session and not actual.in_nested_transaction():
            phase.outer_committed = True

    def rolled_back(actual: Session) -> None:
        if actual is session and not actual.in_nested_transaction():
            phase.outer_rolled_back = True

    event.listen(session, "after_commit", committed)
    event.listen(session, "after_rollback", rolled_back)
    token = _PHASE.set(phase)
    try:
        try:
            with session.begin():
                _emit(phase, "LIVE_TRANSACTION_BEGIN", _live(phase))
                yield
                phase.before_commit = _live(phase)
                _emit(phase, "BEFORE_OUTER_COMMIT", phase.before_commit)
        except BaseException as error:
            state = _terminal_state(phase)
            try:
                terminal = _emit(phase, state, phase.before_commit, error)
                _capture_recheck(phase, terminal)
            except Exception as capture_error:
                error.add_note("Original transaction observation failed: " + str(capture_error))
            raise
        else:
            if not phase.outer_committed or phase.outer_rolled_back:
                raise ObservationFailure(
                    "Normal original transaction exit lacks outer commit proof"
                )
            try:
                terminal = _emit(phase, "COMMITTED", phase.before_commit)
                _capture_recheck(phase, terminal)
            except Exception as capture_error:
                capture_error.add_note(
                    "OBSERVATION_FAILED_AFTER_COMMIT: original SQL effect is committed"
                )
                raise
    finally:
        _PHASE.reset(token)
        event.remove(session, "after_commit", committed)
        event.remove(session, "after_rollback", rolled_back)


def observe_checkpoint(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    phase_name: PhaseName,
    checkpoint: Literal["USER_LOCK_RETURNED", "APPLICATION_SAVEPOINT_APPLIED"],
    now: datetime,
) -> None:
    if _BINDING.get() is None:
        return
    phase = _PHASE.get()
    if (
        phase is None
        or phase.session is not session
        or phase.name != phase_name
        or phase.binding.user_id != user_id
        or phase.binding.action_id != action_id
        or now.tzinfo is None
        or now.utcoffset() is None
        or now.astimezone(UTC) != phase.binding.now
    ):
        raise ObservationFailure("Actual observation checkpoint has no matching live phase")
    _emit(phase, checkpoint, _live(phase))
