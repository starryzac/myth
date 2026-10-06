"""Reset exclusion spans logical commands without borrowing the business pool."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.domain.demo_identity import DEMO_USER_ID

DEMO_GATE_KEY = 0x424644454D4F
_held: ContextVar[frozenset[tuple[str, UUID]]] = ContextVar(
    "audit_command_gates", default=frozenset()
)


def gate_key(user_id: UUID) -> int:
    if user_id == DEMO_USER_ID:
        return DEMO_GATE_KEY
    return int.from_bytes(
        hashlib.sha256(b"bounded-audit-gate\0" + user_id.bytes).digest()[:8], "big", signed=True
    )


def _identity(engine: Engine, user_id: UUID) -> tuple[str, UUID]:
    return engine.url.render_as_string(hide_password=True), user_id


def transaction_gate(session: Session, user_id: UUID) -> None:
    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    if _identity(engine, user_id) in _held.get():
        return
    session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": gate_key(user_id)})


@contextmanager
def audit_command_guard(engine: Engine, user_id: UUID) -> Iterator[None]:
    identity = _identity(engine, user_id)
    if identity in _held.get():
        yield
        return
    guard_engine = create_engine(
        engine.url, poolclass=NullPool, connect_args={"options": "-c timezone=UTC"}
    )
    try:
        with guard_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(
                text("SELECT pg_advisory_lock_shared(:key)"), {"key": gate_key(user_id)}
            )
            token = _held.set(_held.get() | {identity})
            try:
                yield
            finally:
                _held.reset(token)
                connection.execute(
                    text("SELECT pg_advisory_unlock_shared(:key)"), {"key": gate_key(user_id)}
                )
    finally:
        guard_engine.dispose()
