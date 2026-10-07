"""Trusted server clock for historical receipt reads only; never grants authority."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_receipt_read_clock: ContextVar[datetime | None] = ContextVar(
    "trusted_server_receipt_read_clock", default=None
)


def _utc_snapshot(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Trusted receipt read clock must be a timezone-aware server datetime")
    value = now.astimezone(UTC)
    # Detach any datetime subclass; the cache identity is a native immutable UTC scalar.
    return datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=UTC,
    )


@contextmanager
def server_receipt_read_clock_scope(now: datetime) -> Iterator[None]:
    """One trusted server invocation; reset on normal return, error or cancellation."""
    token = _receipt_read_clock.set(_utc_snapshot(now))
    try:
        yield
    finally:
        _receipt_read_clock.reset(token)


def historical_receipt_read_clock() -> datetime:
    """Read horizon for existing receipts; outside a scope preserve the original UTC wall clock."""
    value = _receipt_read_clock.get()
    return value if value is not None else datetime.now(UTC)


def receipt_read_clock_key() -> datetime | None:
    """Stable scoped identity; the default wall clock deliberately remains uncached None."""
    return _receipt_read_clock.get()
