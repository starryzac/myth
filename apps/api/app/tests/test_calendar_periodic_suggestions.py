"""Synthetic module/HTTP risk cases; no actual bank, database or human findings."""

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import calendar_periodic_suggestions as api
from app.db.models import AuditEpoch, EvidenceItem, Transaction
from app.domain.calendar_periodic_suggestions import (
    CalendarPeriodicParameters,
    calendar_periodic_patterns,
)
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE, build_history_coverage
from app.domain.pattern_suggestions import HistoryProof, PeriodicFact, SuggestionSource
from app.domain.policy_configuration import configuration_hash
from app.services.calendar_periodic_suggestions import calendar_periodic_suggestions
from app.services.policy_discovery import _transaction_payload
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_policy_suggestions_service import NOW, USER, MemorySession, item
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

TODAY = date(2026, 4, 10)


def facts(days: list[str], amounts: list[int] | None = None) -> list[PeriodicFact]:
    result = []
    for index, day in enumerate(days):
        amount = (amounts or [10001] * len(days))[index]
        result.append(
            PeriodicFact(
                kind="FIXED_TRANSFER",
                account_id=UUID(int=2),
                payee_ref="actual-synthetic-payee",
                occurred_on=date.fromisoformat(day),
                amount_cents=amount,
                source=SuggestionSource(
                    fact_type="transactions",
                    fact_id=UUID(int=index + 10),
                    account_id=UUID(int=2),
                    source_ref=f"synthetic:{index}",
                    evidence_id=UUID(int=index + 100),
                    evidence_hash="a" * 64,
                    evidence_source_type="SIMULATED_BANK_TRANSACTION",
                    evidence_source_ref=f"synthetic:{index}",
                    evidence_observed_at=NOW.isoformat(),
                    fact={"synthetic_fixture": True},
                ),
            )
        )
    return result


def patterns(rows: list[PeriodicFact], **options: Any) -> dict[str, Any]:
    proof = HistoryProof(
        verified=True,
        account_ids=[UUID(int=2)],
        evidence_ids=[],
        reason_codes=[],
        period_start=date(2025, 4, 10),
        period_end=TODAY - timedelta(days=1),
    )
    return {
        item.schedule.cadence: item
        for item in calendar_periodic_patterns(
            rows, CalendarPeriodicParameters(**options), proof, TODAY
        )
    }


def test_month_end_short_february_exact_dsl_and_all_samples_retained() -> None:
    result = patterns(facts(["2026-01-31", "2026-02-28", "2026-03-31"]))["MONTH_END"]
    assert (
        result.status == "READY_DISCOVERY"
        and result.candidate_support == "AVAILABLE_FOR_USER_REVIEW"
    )
    assert result.schedule.days_before_month_end == 0 and result.schedule.next_occurrence == date(
        2026, 4, 30
    )
    assert result.sample_count == result.cycle_count == len(result.samples) == 3
    assert result.candidate_configuration is not None
    assert result.candidate_configuration["due_day"] == 31
    assert result.candidate_configuration["auto_execute"] is False
    assert result.candidate_configuration_hash == configuration_hash(result.candidate_configuration)
    assert result.advice_only and result.requires_confirmation and not result.bank_authority


def test_leap_february_and_nonzero_offset_never_supply_confirmable_configuration() -> None:
    result = calendar_periodic_patterns(
        facts(["2024-01-30", "2024-02-28", "2024-03-30"]),
        CalendarPeriodicParameters(),
        HistoryProof(verified=True, account_ids=[], evidence_ids=[], reason_codes=[]),
        date(2024, 4, 1),
    )
    end = next(item for item in result if item.schedule.cadence == "MONTH_END")
    assert end.status == "READY_DISCOVERY" and end.schedule.days_before_month_end == 1
    assert end.candidate_support == "DSL_UNSUPPORTED"
    assert end.candidate_configuration is None
    assert end.candidate_configuration_hash is None
    assert end.template_name is None


def test_weekly_same_payee_exact_dates_proven_but_dsl_unsupported() -> None:
    result = patterns(facts(["2026-03-02", "2026-03-09", "2026-03-16"]))["WEEKLY"]
    assert result.status == "READY_DISCOVERY" and result.schedule.weekday == 0
    assert result.schedule.next_occurrence == date(2026, 4, 13)
    assert result.candidate_support == "DSL_UNSUPPORTED" and result.candidate_configuration is None
    assert "CURRENT_POLICY_DSL_CANNOT_EXPRESS_THIS_SCHEDULE" in result.reason_codes


def test_weekday_jitter_circular_boundary_keeps_all_sources() -> None:
    result = patterns(facts(["2026-03-01", "2026-03-07", "2026-03-14"]))["WEEKLY"]
    assert result.schedule.day_spread == 1 and result.sample_count == 3
    assert result.status == "READY_DISCOVERY"


def test_monthly_exact_variance_has_no_float_and_no_outlier_removal() -> None:
    result = patterns(facts(["2026-01-12", "2026-02-12", "2026-03-12"], [10000, 10001, 10002]))[
        "MONTHLY_DATE"
    ]
    assert result.status == "READY_DISCOVERY" and result.mean_fraction_cents == "10001"
    assert result.variance_fraction_cents_squared == "2/3"
    assert result.cv_squared_fraction == "2/300060003"
    bad = patterns(facts(["2026-01-12", "2026-02-12", "2026-03-12"], [10000, 10000, 20000]))[
        "MONTHLY_DATE"
    ]
    assert (
        bad.status == "UNSTABLE" and bad.sample_count == 3 and bad.candidate_configuration is None
    )


@pytest.mark.parametrize(
    "days,cadence,reason",
    [
        (["2026-01-12", "2026-03-12", "2026-04-12"], "MONTHLY_DATE", "NONCONSECUTIVE_CYCLES"),
        (
            ["2026-01-12", "2026-02-12", "2026-02-13"],
            "MONTHLY_DATE",
            "MULTIPLE_OCCURRENCES_IN_CYCLE",
        ),
        (["2026-03-02", "2026-03-16", "2026-03-23"], "WEEKLY", "NONCONSECUTIVE_CYCLES"),
        (
            ["2026-01-31", "2026-02-27", "2026-03-31"],
            "MONTH_END",
            "CURRENT_POLICY_DSL_CANNOT_EXPRESS_THIS_SCHEDULE",
        ),
        (["2026-01-12", "2026-02-15", "2026-03-12"], "MONTHLY_DATE", "DATE_SPREAD_EXCEEDED"),
    ],
)
def test_irregular_or_unexpressible_schedule_never_confirmable(
    days: list[str], cadence: str, reason: str
) -> None:
    item = patterns(facts(days))[cadence]
    assert reason in item.reason_codes and item.candidate_configuration is None


def test_empty_history_and_unverified_coverage_do_not_mean_no_future_obligation() -> None:
    proof = HistoryProof(
        verified=False, account_ids=[], evidence_ids=[], reason_codes=["MISSING_HISTORY_COVERAGE"]
    )
    assert calendar_periodic_patterns([], CalendarPeriodicParameters(), proof, TODAY) == []
    result = calendar_periodic_patterns(
        facts(["2026-01-12", "2026-02-12", "2026-03-12"]),
        CalendarPeriodicParameters(),
        proof,
        TODAY,
    )
    assert all(item.status == "UNKNOWN" and item.candidate_configuration is None for item in result)
    assert all(item.schedule.next_occurrence is None for item in result)


def test_grouping_does_not_mix_payees_accounts_or_types_to_make_minimum_cycles() -> None:
    rows = facts(["2026-01-12", "2026-02-12", "2026-03-12"])
    rows[1] = rows[1].model_copy(update={"payee_ref": "other-payee"})
    rows[2] = rows[2].model_copy(update={"kind": "RENT"})
    result = patterns(rows)
    assert all(item.status == "INSUFFICIENT_HISTORY" for item in result.values())
    raw = calendar_periodic_patterns(
        rows,
        CalendarPeriodicParameters(),
        HistoryProof(verified=True, account_ids=[], evidence_ids=[], reason_codes=[]),
        TODAY,
    )
    assert len(raw) == 9


@pytest.mark.parametrize("money", [0, 9_223_372_036_854_775_807])
def test_integer_amount_edge_and_zero_never_become_guessed_positive_budget(money: int) -> None:
    result = patterns(facts(["2026-01-12", "2026-02-12", "2026-03-12"], [money] * 3))[
        "MONTHLY_DATE"
    ]
    assert result.amount_max_cents == money and result.variance_fraction_cents_squared == "0"
    assert (result.candidate_configuration is None) == (money == 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lookback_days", 366),
        ("minimum_cycles", 2),
        ("maximum_day_spread", 3),
        ("maximum_cv_bps", 1001),
        ("lookback_days", True),
    ],
)
def test_parameters_cannot_expand_registered_rule_or_money_cast(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CalendarPeriodicParameters.model_validate({field: value})


class CalendarMemory(MemorySession):
    def __init__(self) -> None:
        super().__init__()
        self.epoch = AuditEpoch(
            id=UUID(int=800),
            user_id=USER,
            epoch_number=1,
            status="OPEN",
            opened_at=NOW - timedelta(days=1),
            schema_version="audit-head-v1",
            canonical_version="audit-canonical-json-v1",
            created_at=NOW,
            event_count=0,
            last_sequence=0,
        )

    def scalars(self, query: Any) -> list[Any]:
        if query.column_descriptions[0]["entity"] is AuditEpoch:
            self.selects += 1
            return [self.epoch]
        return super().scalars(query)


def service_result(memory: CalendarMemory, lookback: int = 56) -> Any:
    return calendar_periodic_suggestions(
        memory.as_session(), USER, NOW, CalendarPeriodicParameters(lookback_days=lookback)
    )


def test_service_original_history_sources_epoch_hash_and_no_writes_fresh_every_read() -> None:
    memory = CalendarMemory()
    first = service_result(memory)
    count = memory.selects
    assert first.history_proof.verified and len(first.patterns) == 9
    assert all(item.status == "INSUFFICIENT_HISTORY" for item in first.patterns)
    assert first.epoch_id == memory.epoch.id and len(first.source_digest) == 64
    assert first.source_evidence_ids and not first.writes_performed
    assert service_result(memory) == first and memory.selects == count * 2
    assert not memory.new and not memory.dirty and not memory.deleted


@pytest.mark.parametrize(
    "fault",
    [
        "coverage",
        "bank",
        "category",
        "bill",
        "foreign_epoch",
        "closed_epoch",
        "future_epoch",
        "dirty",
        "readwrite",
        "readcommitted",
    ],
)
def test_bad_originals_coverage_epoch_or_transaction_never_confirmable(fault: str) -> None:
    memory = CalendarMemory()
    if fault == "coverage":
        memory.evidence = [item for item in memory.evidence if item.id != UUID(int=999)]
    elif fault == "bank":
        memory.rows[0].amount_cents += 1
    elif fault == "category":
        memory.rows[2].category_confirmed = False
    elif fault == "bill":
        memory.bills[0].total_cents += 1
    elif fault == "foreign_epoch":
        memory.epoch.user_id = UUID(int=99999)
    elif fault == "closed_epoch":
        memory.epoch.status = "SEALED"
    elif fault == "future_epoch":
        memory.epoch.opened_at = NOW + timedelta(seconds=1)
    elif fault == "dirty":
        memory.dirty.add(EvidenceItem())
    elif fault == "readwrite":
        memory.read_only = "off"
    else:
        memory.isolation = "read committed"
    if fault.endswith("epoch") or fault in {"dirty", "readwrite", "readcommitted"}:
        with pytest.raises(PolicyLifecycleError):
            service_result(memory)
    else:
        result = service_result(memory)
        assert not result.history_proof.verified
        assert all(
            item.candidate_configuration is None and item.status == "UNKNOWN"
            for item in result.patterns
        )


def test_365_window_requires_actual_full_coverage_not_two_observed_months() -> None:
    result = service_result(CalendarMemory(), 365)
    assert not result.history_proof.verified
    assert all(
        item.status == "UNKNOWN" and item.candidate_configuration is None
        for item in result.patterns
    )


@pytest.mark.parametrize(
    "days,cadence,lookback,has_config",
    [
        (["2026-07-31", "2026-08-31", "2026-09-30"], "MONTH_END", 95, True),
        (["2026-09-07", "2026-09-14", "2026-09-21"], "WEEKLY", 56, False),
        (["2026-07-30", "2026-08-30", "2026-09-29"], "MONTH_END", 95, False),
    ],
)
def test_original_shaped_bank_coverage_yields_three_cycle_readonly_discovery(
    days: list[str],
    cadence: str,
    lookback: int,
    has_config: bool,
) -> None:
    """Fixture authors create synthetic originals, never product or real-bank observations."""
    memory = CalendarMemory()
    transfer = [row for row in memory.rows if row.category == "internal_transfer"]
    third = Transaction(
        id=UUID(int=40),
        user_id=USER,
        account_id=UUID(int=2),
        evidence_id=UUID(int=140),
        source_ref="synthetic-native-shape:40",
        direction="DEBIT",
        amount_cents=10001,
        balance_after_cents=100000,
        observed_at=NOW,
        category="internal_transfer",
        category_confirmed=False,
        counterparty_ref="actual-fixture-payee:transfer",
        is_one_off=False,
    )
    transfer.append(third)
    memory.rows.append(third)
    for row, day in zip(transfer, days, strict=True):
        row.occurred_at = datetime.fromisoformat(day).replace(tzinfo=NOW.tzinfo)
        row.amount_cents = 10001
        assert row.evidence_id is not None
        original = item(
            row.evidence_id.int,
            "SIMULATED_BANK_TRANSACTION",
            {
                **_transaction_payload(row),
                "simulation": True,
                "economic_role": "INTERNAL_TRANSFER",
            },
        )
        memory.evidence = [proof for proof in memory.evidence if proof.id != row.evidence_id]
        memory.evidence.append(original)
    memory.evidence = [proof for proof in memory.evidence if proof.id != UUID(int=999)]
    memory.evidence.append(
        item(
            999,
            COVERAGE_SOURCE_TYPE,
            build_history_coverage(
                USER,
                "Asia/Shanghai",
                date(2026, 7, 1),
                date(2026, 10, 3),
                [account.id for account in memory.accounts],
                memory.rows,
                {proof.id: proof for proof in memory.evidence},
            ),
        )
    )
    report = service_result(memory, lookback)
    selected = next(
        pattern
        for pattern in report.patterns
        if pattern.kind == "FIXED_TRANSFER" and pattern.schedule.cadence == cadence
    )
    assert report.history_proof.verified and selected.status == "READY_DISCOVERY"
    assert selected.sample_count == selected.cycle_count == 3
    assert (selected.candidate_configuration is not None) == has_config
    assert len({sample.original.source.evidence_id for sample in selected.samples}) == 3
    assert not memory.new and not memory.dirty and not memory.deleted


@pytest.fixture
def client() -> TestClient:
    memory = CalendarMemory()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: memory.as_session()
    app.dependency_overrides[get_demo_user] = lambda: memory.user
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_actual_http_query_codec_calls_original_read_service_not_client_facts(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/policy-suggestions/calendar-periodic?lookback_days=56")
    assert response.status_code == 200
    result = response.json()
    assert result["protocol"] == "full-calendar-periodic-discovery-v2"
    assert result["user_id"] == str(USER) and result["as_of"] == NOW.isoformat().replace(
        "+00:00", "Z"
    )
    assert result["parameters"]["minimum_cycles"] == 3
    assert result["bank_authority"] is False and result["future_obligation_created"] is False


@pytest.mark.parametrize(
    "field",
    [
        "now",
        "user_id",
        "epoch_id",
        "facts",
        "coverage",
        "amount_cents",
        "receipt",
        "auto_confirm",
        "result",
    ],
)
def test_http_rejects_facts_time_identity_or_permissions(client: TestClient, field: str) -> None:
    assert (
        client.get("/api/v1/policy-suggestions/calendar-periodic", params={field: "1"}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("lookback_days", "366"),
        ("minimum_cycles", "2"),
        ("maximum_day_spread", "3"),
        ("maximum_cv_bps", "1001"),
    ],
)
def test_http_option_limits_are_actual_server_codec(
    client: TestClient, field: str, value: str
) -> None:
    assert (
        client.get(
            "/api/v1/policy-suggestions/calendar-periodic", params={field: value}
        ).status_code
        == 422
    )
