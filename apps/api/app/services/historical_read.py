"""Invocation-local reuse, enabled only for a clean PostgreSQL RR/read-only snapshot."""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID

from app.db.models import SimulatedBankPosting
from app.domain.audit_chain import canonical_bytes
from app.domain.bank_posting_codec import POSTING_V2_FIELDS
from sqlalchemy import text
from sqlalchemy.orm import Session, SessionTransaction


def stable_read_snapshot(session: Session) -> bool:
    if session.new or session.dirty or session.deleted:
        return False
    isolation, readonly = session.execute(
        text(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')"
        )
    ).one()
    return isolation in {"repeatable read", "serializable"} and readonly == "on"


@dataclass
class _LedgerScope:
    session: Session
    transaction: SessionTransaction | None
    nested: SessionTransaction | None
    verified_users: set[UUID] = field(default_factory=set)
    originals: dict[UUID, dict[UUID, str]] = field(default_factory=dict)

    def matches(self, session: Session) -> bool:
        clean = not (session.new or session.dirty or session.deleted)
        same = (
            self.session is session
            and self.transaction is session.get_transaction()
            and self.nested is session.get_nested_transaction()
        )
        if not clean or not same:
            self.verified_users.clear()
            self.originals.clear()
        return clean and same

    def unchanged(self, session: Session, user_id: UUID) -> bool:
        originals = self.originals.get(user_id, {})
        for row in session.identity_map.values():
            # Known IDs must also be checked after an unflushed owner rebind.
            if isinstance(row, SimulatedBankPosting) and (
                row.id in originals or row.user_id == user_id
            ):
                try:
                    if originals.get(row.id) == _posting_digest(row):
                        continue
                except (TypeError, ValueError):
                    pass
                self.verified_users.discard(user_id)
                self.originals.pop(user_id, None)
                return False
        return True


def _posting_digest(row: SimulatedBankPosting) -> str:
    return hashlib.sha256(
        canonical_bytes({key: getattr(row, key) for key in POSTING_V2_FIELDS}, raw=True)
    ).hexdigest()


_scope: ContextVar[_LedgerScope | None] = ContextVar("historical_ledger_scope", default=None)


@contextmanager
def historical_ledger_scope(session: Session) -> Iterator[None]:
    """Never persist a result on a Session, ORM row, head hash or cross-request cache."""
    value = (
        _LedgerScope(session, session.get_transaction(), session.get_nested_transaction())
        if stable_read_snapshot(session)
        else None
    )
    token = _scope.set(value)
    try:
        yield
    finally:
        _scope.reset(token)


def verify_historical_ledger(session: Session, user_id: UUID) -> None:
    from app.services.simulated_bank import ledger_heads

    value = _scope.get()
    reusable = value is not None and value.matches(session)
    if (
        reusable
        and value is not None
        and user_id in value.verified_users
        and value.unchanged(session, user_id)
    ):
        return
    # The first call always verifies every original posting, including non-head rows.
    if reusable and value is not None:
        originals: dict[UUID, str] = {}
        representable = True

        def retain_original(row: SimulatedBankPosting) -> None:
            nonlocal representable
            try:
                originals[row.id] = _posting_digest(row)
            except (TypeError, ValueError):
                representable = False

        ledger_heads(session, user_id, _verified_posting=retain_original)
        if representable:
            value.originals[user_id] = originals
            value.verified_users.add(user_id)
    else:
        ledger_heads(session, user_id)
