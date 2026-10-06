"""Synthetic deterministic report risks only, not bank or economic measurements."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.full_reconciliation import compare_amount, head_reference, report_state
from app.services.full_reconciliation import _issue
from pydantic import ValidationError

NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
USER = UUID(int=7001)
ENTITY = UUID(int=7002)


@pytest.mark.parametrize("application,bank,difference", [(0, 0, 0), (107, 100, 7), (100, 107, -7)])
def test_integer_difference_is_application_minus_independent_original(
    application: int, bank: int, difference: int
) -> None:
    head = head_reference(UUID(int=7003), f"CASH:{ENTITY}", 1, NOW)
    value = compare_amount(ENTITY, "ACCOUNT_CASH", application, bank, head)
    assert value.difference_cents == difference
    assert value.state == ("MATCHED" if difference == 0 else "DIFFERENCE")


@pytest.mark.parametrize(
    "application,bank,head_missing", [(None, 100, False), (100, None, False), (100, 100, True)]
)
def test_missing_original_or_head_is_null_never_zero_or_matched(
    application: int | None, bank: int | None, head_missing: bool
) -> None:
    head = None if head_missing else head_reference(UUID(int=7003), f"CASH:{ENTITY}", 1, NOW)
    value = compare_amount(ENTITY, "ACCOUNT_CASH", application, bank, head)
    assert value.state == "MISSING" and value.difference_cents is None
    if head_missing:
        assert value.bank_cents is None


@pytest.mark.parametrize("kind", ["MISSING", "UNSUPPORTED", "PENDING"])
def test_incomplete_unknown_denominator_never_becomes_success(kind: str) -> None:
    issue = _issue("SYNTHETIC_MISSING", ENTITY, "synthetic missing original", kind)  # type: ignore[arg-type]
    assert report_state([issue], complete=True) == "UNKNOWN"
    assert report_state([], complete=False) == "UNKNOWN"


@pytest.mark.parametrize("kind", ["INTEGRITY", "DIFFERENCE"])
def test_definite_difference_requires_manual_review_without_repair(kind: str) -> None:
    issue = _issue("SYNTHETIC_DRIFT", ENTITY, "synthetic contradiction", kind)  # type: ignore[arg-type]
    assert report_state([issue], complete=False) == "MANUAL_REVIEW_REQUIRED"
    assert report_state([], complete=True) == "MATCHED"


@pytest.mark.parametrize("wrong", [True, 1.5, "100"])
def test_money_does_not_coerce_clientish_values(wrong: object) -> None:
    with pytest.raises(ValidationError):
        compare_amount(ENTITY, "ACCOUNT_CASH", wrong, 100, None)  # type: ignore[arg-type]
