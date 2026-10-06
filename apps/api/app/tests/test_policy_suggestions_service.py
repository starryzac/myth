"""Original ORM/evidence shape with in-memory SELECT doubles; not actual PG proof."""

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import Account, CreditCardBill, EvidenceItem, Transaction, User
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE, build_history_coverage
from app.domain.pattern_suggestions import PeriodicParameters, SeasonalParameters
from app.domain.policy_configuration import configuration_hash
from app.services import policy_suggestions as service
from app.services.living_reserve import CATEGORY_SOURCE_TYPE
from app.services.policy_discovery import _bill_payload, _transaction_payload
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy.orm import Session

USER = UUID(int=1)
NOW = datetime(2026, 10, 3, 16, tzinfo=UTC)


def item(
    identifier: int, source_type: str, content: dict[str, Any], level: str = "BANK_CONFIRMED"
) -> EvidenceItem:
    return EvidenceItem(
        id=UUID(int=identifier),
        user_id=USER,
        evidence_level=level,
        source_type=source_type,
        source_ref=f"fixture:{identifier}",
        content=content,
        content_hash=configuration_hash(content),
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        valid_to=None,
        observed_at=NOW,
        status="VALID",
    )


class MemorySession:
    def __init__(self) -> None:
        self.new: set[Any] = set()
        self.dirty: set[Any] = set()
        self.deleted: set[Any] = set()
        self.isolation = "repeatable read"
        self.read_only = "on"
        self.user = User(id=USER, is_simulated=True, timezone="Asia/Shanghai")
        self.accounts = [
            Account(id=UUID(int=2), user_id=USER, account_type="CASH"),
            Account(id=UUID(int=3), user_id=USER, account_type="CREDIT_CARD"),
        ]
        self.rows: list[Transaction] = []
        self.evidence: list[EvidenceItem] = []
        self.bills: list[CreditCardBill] = []
        self.selects = 0
        for index, (month, kind, amount, role, category) in enumerate(
            [
                (8, "transfer", 10_000, "INTERNAL_TRANSFER", "internal_transfer"),
                (9, "transfer", 10_200, "INTERNAL_TRANSFER", "internal_transfer"),
                (8, "rent", 18_000, "CONSUMPTION", "rent"),
                (9, "rent", 18_000, "CONSUMPTION", "rent"),
                (8, "ordinary", 1000, "CONSUMPTION", "food"),
                (9, "ordinary", 1000, "CONSUMPTION", "food"),
            ],
            10,
        ):
            row = Transaction(
                id=UUID(int=index),
                user_id=USER,
                account_id=UUID(int=2),
                evidence_id=UUID(int=index + 100),
                source_ref=f"bank-history:{index}",
                direction="DEBIT",
                amount_cents=amount,
                balance_after_cents=100_000,
                occurred_at=datetime(2026, month, 12 if kind == "transfer" else 28, tzinfo=UTC),
                observed_at=NOW,
                category=category,
                category_confirmed=True,
                counterparty_ref=f"actual-fixture-payee:{kind}",
                is_one_off=False,
            )
            bank = item(
                index + 100,
                "SIMULATED_BANK_TRANSACTION",
                {
                    **_transaction_payload(row),
                    "simulation": True,
                    "economic_role": role,
                },
            )
            self.rows.append(row)
            self.evidence.append(bank)
            self.evidence.append(
                item(
                    index + 200,
                    CATEGORY_SOURCE_TYPE,
                    {
                        "simulation": True,
                        "transaction_id": str(row.id),
                        "category": category,
                        "confirmed": True,
                        "actor": "synthetic_fixture_actor",
                    },
                    "USER_DECLARED",
                )
            )
        for identifier, month, total in [(30, 8, 12_000), (31, 9, 13_500)]:
            bill = CreditCardBill(
                id=UUID(int=identifier),
                user_id=USER,
                account_id=UUID(int=3),
                evidence_id=UUID(int=identifier + 100),
                source_ref=f"fixture:{identifier + 100}",
                statement_date=date(2026, month, 1),
                due_date=date(2026, month, 20),
                total_cents=total,
                minimum_due_cents=total // 10,
                paid_cents=0,
                status="UNPAID",
            )
            self.bills.append(bill)
            self.evidence.append(
                item(
                    identifier + 100,
                    "SIMULATED_CREDIT_CARD_BILL",
                    {
                        **_bill_payload(bill),
                        "minimum_due_cents": bill.minimum_due_cents,
                        "simulation": True,
                        "user_id": str(USER),
                        "bill_id": str(bill.id),
                        "account_id": str(bill.account_id),
                    },
                )
            )
        self.rebuild_coverage()

    @property
    def no_autoflush(self) -> nullcontext[None]:
        return nullcontext()

    def scalar(self, query: Any) -> Any:
        self.selects += 1
        if str(query) == "SHOW transaction_isolation":
            return self.isolation
        if str(query) == "SHOW transaction_read_only":
            return self.read_only
        return self.user

    def scalars(self, query: Any) -> list[Any]:
        self.selects += 1
        entity = query.column_descriptions[0]["entity"]
        rows: dict[Any, list[Any]] = {
            Account: self.accounts,
            Transaction: self.rows,
            EvidenceItem: self.evidence,
            CreditCardBill: self.bills,
        }
        return rows[entity]

    def rebuild_coverage(self) -> None:
        self.evidence = [row for row in self.evidence if row.source_type != COVERAGE_SOURCE_TYPE]
        self.evidence.append(
            item(
                999,
                COVERAGE_SOURCE_TYPE,
                build_history_coverage(
                    USER,
                    "Asia/Shanghai",
                    date(2026, 8, 9),
                    date(2026, 10, 3),
                    [row.id for row in self.accounts],
                    self.rows,
                    {row.id: row for row in self.evidence},
                ),
            )
        )

    def as_session(self) -> Session:
        return cast(Session, self)


def periodic(memory: MemorySession) -> service.PeriodicSuggestions:
    return service.periodic_suggestions(memory.as_session(), USER, NOW, PeriodicParameters())


def test_readonly_original_source_and_full_candidate_not_ordinary_consumption() -> None:
    memory = MemorySession()
    result = periodic(memory)
    assert memory.selects == 7 and result.history_proof.verified
    assert {row.kind for row in result.patterns} == {"FIXED_TRANSFER", "RENT", "CREDIT_CARD_BILL"}
    assert all(row.status == "READY" for row in result.patterns)
    assert len(result.source_digest) == 64 and result.source_issues == []
    rent = next(row for row in result.patterns if row.kind == "RENT")
    assert len(rent.sources) == 4  # two original bank facts plus two actual category confirmations
    assert result.excluded_transaction_count == 2 and not result.hard_protection_changed
    assert (
        periodic(memory) == result and memory.selects == 14
    )  # fresh reads, no cross-request cache


@pytest.mark.parametrize(
    "case",
    [
        "coverage_missing",
        "coverage_hash",
        "deleted_row",
        "amount_changed",
        "bank_untrusted",
        "future",
        "unknown_role",
        "category_missing",
        "category_changed",
        "category_duplicate",
        "bill_hash",
        "bill_account",
        "bill_reused_evidence",
        "bill_future",
        "bill_level",
    ],
)
def test_bad_originals_never_yield_a_precise_candidate(case: str) -> None:
    memory = MemorySession()
    bank = next(row for row in memory.evidence if row.id == UUID(int=110))
    coverage = next(row for row in memory.evidence if row.source_type == COVERAGE_SOURCE_TYPE)
    category = next(row for row in memory.evidence if row.id == UUID(int=212))
    bill = next(row for row in memory.evidence if row.id == UUID(int=130))
    if case == "coverage_missing":
        memory.evidence.remove(coverage)
    elif case == "coverage_hash":
        coverage.content = {**coverage.content, "fake": True}
    elif case == "deleted_row":
        memory.rows.pop(0)
    elif case == "amount_changed":
        memory.rows[0].amount_cents += 1
    elif case == "bank_untrusted":
        bank.evidence_level = "USER_DECLARED"
    elif case == "future":
        bank.observed_at = NOW + timedelta(seconds=1)
    elif case == "unknown_role":
        bank.content = {**bank.content, "economic_role": "UNKNOWN"}
        bank.content_hash = configuration_hash(bank.content)
    elif case == "category_missing":
        memory.evidence.remove(category)
    elif case == "category_changed":
        memory.rows[2].category = "rent"
        category.content = {**category.content, "category": "food"}
        category.content_hash = configuration_hash(category.content)
    elif case == "category_duplicate":
        memory.evidence.append(item(800, CATEGORY_SOURCE_TYPE, category.content, "USER_DECLARED"))
    elif case == "bill_hash":
        bill.content = {**bill.content, "fake": True}
    elif case == "bill_account":
        bill.content = {**bill.content, "account_id": str(UUID(int=9999))}
        bill.content_hash = configuration_hash(bill.content)
    elif case == "bill_reused_evidence":
        memory.bills[1].evidence_id = memory.bills[0].evidence_id
    elif case == "bill_future":
        bill.observed_at = NOW + timedelta(seconds=1)
    else:
        bill.evidence_level = "BANK_OBSERVED"
    result = periodic(memory)
    assert not result.history_proof.verified and result.source_issues
    assert all(
        row.candidate_configuration is None and row.status == "UNKNOWN" for row in result.patterns
    )


def test_missing_similar_years_and_raw_coverage_do_not_manufacture_a_seasonal_amount() -> None:
    memory = MemorySession()
    result = service.seasonal_suggestions(
        memory.as_session(), USER, NOW, SeasonalParameters(window_id="CN-2026-NATIONAL_DAY")
    )
    assert result.history_proof.verified and memory.selects == 6
    assert result.suggestion.status == "INSUFFICIENT_HISTORY"
    assert result.suggestion.window_count == 0
    assert result.suggestion.required_adjustment_cents is None
    assert result.sources and result.sources[0].fact_type == "evidence_items"
    assert result.calendar_extraction_hash and not result.bank_authority


def test_non_simulated_or_invalid_server_time_cannot_supply_candidate_source() -> None:
    memory = MemorySession()
    memory.user.is_simulated = False
    with pytest.raises(PolicyLifecycleError, match="模拟用户不存在"):
        periodic(memory)
    memory.user.is_simulated = True
    with pytest.raises(PolicyLifecycleError, match="服务器时间必须带时区"):
        service.periodic_suggestions(
            memory.as_session(), USER, NOW.replace(tzinfo=None), PeriodicParameters()
        )


def test_bank_payee_identifier_is_not_silently_trimmed_or_guessed() -> None:
    memory = MemorySession()
    row = memory.rows[0]
    bank = next(item for item in memory.evidence if item.id == row.evidence_id)
    row.counterparty_ref = "  bank-exact-identifier  "
    bank.content = {**bank.content, "counterparty_ref": row.counterparty_ref}
    bank.content_hash = configuration_hash(bank.content)
    memory.rebuild_coverage()
    result = periodic(memory)
    assert not result.history_proof.verified
    assert any(issue.code == "INVALID_PAYEE_REFERENCE" for issue in result.source_issues)
    assert all(row.candidate_configuration is None for row in result.patterns)


@pytest.mark.parametrize("case", ["dirty", "isolation", "writeable", "owner"])
def test_service_rejects_dirty_inconsistent_writeable_or_wrong_owner_context(case: str) -> None:
    memory = MemorySession()
    if case == "dirty":
        memory.dirty.add(object())
    elif case == "isolation":
        memory.isolation = "read committed"
    elif case == "writeable":
        memory.read_only = "off"
    else:
        memory.user.id = UUID(int=9999)
    with pytest.raises(PolicyLifecycleError) as caught:
        periodic(memory)
    assert caught.value.code in {"DIRTY_READ_SESSION", "READ_ONLY_SNAPSHOT_REQUIRED", "NOT_FOUND"}


def test_public_calendar_requires_its_actual_china_local_date_contract() -> None:
    memory = MemorySession()
    as_of = datetime(2026, 10, 4, tzinfo=UTC)
    memory.user.timezone = "UTC"
    coverage = next(row for row in memory.evidence if row.source_type == COVERAGE_SOURCE_TYPE)
    coverage.content = build_history_coverage(
        USER,
        "UTC",
        date(2026, 8, 9),
        date(2026, 10, 3),
        [row.id for row in memory.accounts],
        memory.rows,
        {row.id: row for row in memory.evidence},
    )
    coverage.content_hash = configuration_hash(coverage.content)
    coverage.observed_at = as_of
    result = service.seasonal_suggestions(
        memory.as_session(), USER, as_of, SeasonalParameters(window_id="CN-2026-NATIONAL_DAY")
    )
    assert result.suggestion.status == "UNKNOWN"
    assert "CALENDAR_TIMEZONE_UNSUPPORTED" in result.suggestion.reason_codes
    assert result.suggestion.required_adjustment_cents is None


def seasonal_memory() -> tuple[MemorySession, datetime]:
    memory = MemorySession()
    memory.rows = []
    memory.evidence = []
    as_of = datetime(2026, 2, 14, tzinfo=UTC)
    for identifier, day, amount in [
        (50, "2024-02-10", 800),
        (51, "2025-01-27", 800),
        (52, "2025-01-28", 2400),
    ]:
        row = Transaction(
            id=UUID(int=identifier),
            user_id=USER,
            account_id=UUID(int=2),
            evidence_id=UUID(int=identifier + 100),
            source_ref=f"historic:{identifier}",
            direction="DEBIT",
            amount_cents=amount,
            balance_after_cents=100_000,
            occurred_at=datetime.fromisoformat(day).replace(tzinfo=UTC),
            observed_at=as_of,
            category="food",
            category_confirmed=True,
            counterparty_ref="bank-known-food-payee",
            is_one_off=False,
        )
        memory.rows.append(row)
        bank = item(
            identifier + 100,
            "SIMULATED_BANK_TRANSACTION",
            {
                **_transaction_payload(row),
                "simulation": True,
                "economic_role": "CONSUMPTION",
            },
        )
        category = item(
            identifier + 200,
            CATEGORY_SOURCE_TYPE,
            {
                "simulation": True,
                "transaction_id": str(row.id),
                "category": "food",
                "confirmed": True,
            },
            "USER_DECLARED",
        )
        bank.observed_at = category.observed_at = as_of
        memory.evidence.extend([bank, category])
    coverage = item(
        999,
        COVERAGE_SOURCE_TYPE,
        build_history_coverage(
            USER,
            "Asia/Shanghai",
            date(2024, 1, 1),
            date(2026, 2, 13),
            [row.id for row in memory.accounts],
            memory.rows,
            {row.id: row for row in memory.evidence},
        ),
    )
    coverage.observed_at = as_of
    memory.evidence.append(coverage)
    return memory, as_of


def test_full_old_window_originals_and_confirmed_category_produce_a_real_calculated_candidate() -> (
    None
):
    memory, as_of = seasonal_memory()
    result = service.seasonal_suggestions(
        memory.as_session(), USER, as_of, SeasonalParameters(window_id="CN-2026-SPRING_FESTIVAL")
    )
    assert result.history_proof.verified and result.suggestion.status == "READY"
    assert result.suggestion.required_adjustment_cents == 1800
    assert len(result.sources) == 7  # original three facts, three category proofs and coverage
    assert result.suggestion.window_count == 2 and not result.hard_protection_changed
    assert result.source_issues == []


@pytest.mark.parametrize(
    "case", ["missing", "duplicate", "foreign", "future", "mismatch", "bank_amount", "bill_minimum"]
)
def test_seasonal_or_bill_precise_field_binding_cannot_drop_an_invalid_original(case: str) -> None:
    if case == "bill_minimum":
        memory = MemorySession()
        memory.bills[0].minimum_due_cents += 1
        assert not periodic(memory).history_proof.verified
        return
    memory, as_of = seasonal_memory()
    category = next(row for row in memory.evidence if row.id == UUID(int=250))
    if case == "missing":
        memory.evidence.remove(category)
    elif case == "duplicate":
        duplicate = item(800, CATEGORY_SOURCE_TYPE, category.content, "USER_DECLARED")
        duplicate.observed_at = as_of
        memory.evidence.append(duplicate)
    elif case == "foreign":
        category.user_id = UUID(int=9999)
    elif case == "future":
        category.observed_at = as_of + timedelta(seconds=1)
    elif case == "mismatch":
        category.content = {**category.content, "category": "transport"}
        category.content_hash = configuration_hash(category.content)
    else:
        memory.rows[0].amount_cents += 1
    result = service.seasonal_suggestions(
        memory.as_session(), USER, as_of, SeasonalParameters(window_id="CN-2026-SPRING_FESTIVAL")
    )
    assert result.suggestion.status == "INSUFFICIENT_HISTORY"
    assert result.suggestion.required_adjustment_cents is None
    assert result.suggestion.candidate_configuration is None
    assert result.source_issues
