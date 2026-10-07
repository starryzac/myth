"""Strict Shanghai grant date bounds; this predicate supplies no financial authority."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.policy_lifecycle import PolicyLifecycleError

FIELDS = frozenset({"deadline", "valid_until"})
CALENDAR = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _invalid() -> PolicyLifecycleError:
    return PolicyLifecycleError(
        "INVALID_AUTHORIZATION_WINDOW", "持续授权日期范围无法核验，请重新审阅", 409
    )


def _calendar(value: object) -> date:
    if type(value) is not str or CALENDAR.fullmatch(value) is None:
        raise _invalid()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _invalid() from None


def grant_dates_active(fields: object, now: object) -> bool:
    """Accept exactly two explicit dates and a server-owned aware clock.

    Both dates are inclusive Shanghai calendar bounds. The earlier date expires
    at the following local midnight. Missing bounds never imply unlimited consent.
    A true result says only that these dates have not expired; callers must verify
    the actual owner, epoch, source, confirmation and current policy separately.
    """
    if type(fields) is not dict or fields.keys() != FIELDS:
        raise _invalid()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise _invalid()
    deadline = _calendar(fields["deadline"])
    valid_until = _calendar(fields["valid_until"])
    try:
        exclusive_end = datetime.combine(
            min(deadline, valid_until) + timedelta(days=1), time.min, SHANGHAI
        )
        return now.astimezone(UTC) < exclusive_end.astimezone(UTC)
    except (ValueError, OverflowError):
        raise _invalid() from None
