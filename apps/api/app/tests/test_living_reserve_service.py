"""Read-only living reserve estimates from verified PostgreSQL facts and coverage."""

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.models import Account, EvidenceItem, Transaction, User
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.living_reserve import estimate_living_reserve
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.integration
CONFIGURATION: dict[str, Any] = {
    "type": "living_reserve",
    "horizon_days": 14,
    "method": {
        "name": "rolling_window_quantile",
        "lookback_days": 56,
        "quantile": 0.8,
        "essential_categories": ["food", "transport", "daily_necessities"],
        "exclude_one_off": True,
    },
    "extra_buffer_cents": 50000,
    "reconfirm_on_boundary_crossing": True,
}


@pytest.fixture
def reserve_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            yield engine
        finally:
            engine.dispose()


def snapshot(engine: Engine) -> str:
    with engine.connect() as connection:
        content = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
    return json.dumps(content, sort_keys=True, default=str)


def test_seed_estimate_uses_complete_days_and_writes_nothing(reserve_engine: Engine) -> None:
    before = snapshot(reserve_engine)
    with Session(reserve_engine) as session:
        result = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert result.simulation is True and result.user_id == DEMO_USER_ID
        assert result.estimation.status == "READY"
        assert result.estimation.history_start.isoformat() == "2026-08-09"
        assert result.estimation.history_end.isoformat() == "2026-10-03"
        assert result.estimation.window_count == 43 and result.estimation.rank == 35
        assert result.estimation.base_reserve_cents == 77900
        assert result.estimation.recommended_reserve_cents == 127900
        assert result.candidate_configuration == validate_configuration(CONFIGURATION)
        assert result.candidate_configuration_hash == configuration_hash(
            result.candidate_configuration
        )
        assert result.source_evidence_ids and len(result.input_digest) == 64
        assert result.source_issues == []
        repeated = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert repeated == result
    assert snapshot(reserve_engine) == before


def assert_insufficient(engine: Engine) -> None:
    before = snapshot(engine)
    with Session(engine) as session:
        result = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert result.estimation.status == "INSUFFICIENT_HISTORY"
        assert result.estimation.base_reserve_cents is None
        assert result.estimation.recommended_reserve_cents is None
        assert (
            result.candidate_configuration is None and result.candidate_configuration_hash is None
        )
        assert result.source_issues
    assert snapshot(engine) == before


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "hash",
        "unknown",
        "future",
        "unclosed_day",
        "timezone",
        "user_id",
        "scope",
        "shortened_period",
        "duplicate",
        "deleted_transaction",
        "changed_bank_amount",
        "changed_bank_amount_rehashed",
        "unknown_role",
    ],
)
def test_untrusted_or_incomplete_coverage_never_returns_precise_amount(
    reserve_engine: Engine, case: str
) -> None:
    with Session(reserve_engine) as session, session.begin():
        coverage = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == COVERAGE_SOURCE_TYPE)
        )
        assert coverage is not None
        if case == "missing":
            session.delete(coverage)
        elif case == "hash":
            coverage.content = {**coverage.content, "changed": True}
        elif case == "unknown":
            coverage.status = "UNKNOWN"
        elif case == "future":
            coverage.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif case == "unclosed_day":
            coverage.observed_at = SEED_AS_OF - timedelta(seconds=1)
        elif case in {"timezone", "user_id", "scope", "shortened_period"}:
            key, value = {
                "timezone": ("timezone", "UTC"),
                "user_id": ("user_id", str(uuid4())),
                "scope": ("scope_account_ids", coverage.content["scope_account_ids"][:-1]),
                "shortened_period": ("period_end", "2026-10-02"),
            }[case]
            coverage.content = {**coverage.content, key: value}
            coverage.content_hash = configuration_hash(coverage.content)
        elif case == "duplicate":
            session.add(
                EvidenceItem(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    evidence_level=coverage.evidence_level,
                    source_type=coverage.source_type,
                    source_ref=coverage.source_ref,
                    content=coverage.content,
                    content_hash=coverage.content_hash,
                    valid_from=coverage.valid_from,
                    observed_at=coverage.observed_at,
                    status="VALID",
                )
            )
        else:
            row = session.scalar(
                select(Transaction)
                .where(Transaction.category == "food")
                .order_by(Transaction.occurred_at.desc())
            )
            assert row is not None
            bank = session.get(EvidenceItem, row.evidence_id)
            assert bank is not None
            if case == "deleted_transaction":
                session.delete(row)
            elif case == "unknown_role":
                bank.content = {**bank.content, "economic_role": "UNKNOWN"}
                bank.content_hash = configuration_hash(bank.content)
            else:
                row.amount_cents += 1
                if case == "changed_bank_amount_rehashed":
                    bank.content = {**bank.content, "amount_cents": row.amount_cents}
                    bank.content_hash = configuration_hash(bank.content)
    assert_insufficient(reserve_engine)


@pytest.mark.parametrize(
    "case",
    ["missing", "hash", "future", "false_confirmation", "category_mismatch", "foreign_owner"],
)
def test_confirmed_category_requires_independent_matching_user_evidence(
    reserve_engine: Engine, case: str
) -> None:
    with Session(reserve_engine) as session, session.begin():
        row = session.scalar(
            select(Transaction)
            .where(Transaction.category == "food")
            .order_by(Transaction.occurred_at.desc())
        )
        assert row is not None
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_USER_CATEGORY_CONFIRMATION",
                EvidenceItem.content["transaction_id"].as_string() == str(row.id),
            )
        )
        assert proof is not None
        if case == "missing":
            session.delete(proof)
        elif case == "hash":
            proof.content = {**proof.content, "changed": True}
        elif case == "future":
            proof.observed_at = SEED_AS_OF + timedelta(seconds=1)
        elif case == "false_confirmation":
            proof.content = {**proof.content, "confirmed": False}
            proof.content_hash = configuration_hash(proof.content)
        elif case == "category_mismatch":
            row.category = "transport"
        else:
            other = User(id=uuid4(), external_ref=str(uuid4()), display_name="Other")
            session.add(other)
            session.flush()
            proof.user_id = other.id
    assert_insufficient(reserve_engine)


def confirmation(session: Session, row: Transaction) -> None:
    content = {
        "simulation": True,
        "transaction_id": str(row.id),
        "category": row.category,
        "confirmed": True,
        "actor": "synthetic_user",
        "basis": "test_explicit_selection",
    }
    session.add(
        EvidenceItem(
            id=uuid4(),
            user_id=row.user_id,
            evidence_level="USER_DECLARED",
            source_type="SIMULATED_USER_CATEGORY_CONFIRMATION",
            source_ref=f"test-category:{row.id}",
            content=content,
            content_hash=configuration_hash(content),
            valid_from=SEED_AS_OF,
            observed_at=SEED_AS_OF,
            status="VALID",
        )
    )


def test_conflicting_category_confirmations_require_explicit_supersession(
    reserve_engine: Engine,
) -> None:
    with Session(reserve_engine) as session, session.begin():
        row = session.scalar(
            select(Transaction)
            .where(Transaction.category == "food")
            .order_by(Transaction.occurred_at.desc())
        )
        assert row is not None
        identifier = row.id
        original = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_USER_CATEGORY_CONFIRMATION",
                EvidenceItem.content["transaction_id"].as_string() == str(identifier),
            )
        )
        assert original is not None
        original_id = original.id
        row.category = "transport"
        confirmation(session, row)
        row.category = "food"
    assert_insufficient(reserve_engine)
    with Session(reserve_engine) as session, session.begin():
        original = session.get(EvidenceItem, original_id)
        row = session.get(Transaction, identifier)
        assert original is not None and row is not None
        original.status = "SUPERSEDED"
        row.category = "transport"
    before = snapshot(reserve_engine)
    with Session(reserve_engine) as session:
        result = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert result.estimation.status == "READY"
        assert result.estimation.base_reserve_cents == 77900
        assert identifier in result.estimation.included_transaction_ids
        assert result.source_issues == []
    assert snapshot(reserve_engine) == before


def test_one_off_exclusion_survives_a_confirmed_category_change(reserve_engine: Engine) -> None:
    with Session(reserve_engine) as session, session.begin():
        row = session.scalar(select(Transaction).where(Transaction.is_one_off.is_(True)))
        assert row is not None and row.amount_cents == 450000
        one_off_id = row.id
        row.category, row.category_confirmed = "food", True
        confirmation(session, row)
    with Session(reserve_engine) as session:
        excluded = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert excluded.estimation.status == "READY"
        assert excluded.estimation.base_reserve_cents == 77900
        assert one_off_id not in excluded.estimation.included_transaction_ids
        assert any(
            item.transaction_id == one_off_id for item in excluded.estimation.excluded_transactions
        )
        included = estimate_living_reserve(
            session,
            DEMO_USER_ID,
            SEED_AS_OF,
            {**CONFIGURATION, "method": {**CONFIGURATION["method"], "exclude_one_off": False}},
        )
        assert included.estimation.status == "READY"
        assert one_off_id in included.estimation.included_transaction_ids
        assert included.estimation.base_reserve_cents is not None
        assert included.estimation.base_reserve_cents > 400000


def test_nonconsumption_cannot_be_disguised_by_editing_category(reserve_engine: Engine) -> None:
    with Session(reserve_engine) as session, session.begin():
        rows = session.scalars(
            select(Transaction).where(
                Transaction.category.in_(
                    ["internal_transfer", "asset_purchase", "credit_card_payment"]
                )
            )
        ).all()
        disguised_ids = {row.id for row in rows}
        assert len(disguised_ids) > 3
        for row in rows:
            row.category, row.category_confirmed = "food", True
            confirmation(session, row)
    with Session(reserve_engine) as session:
        result = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert result.estimation.status == "READY"
        assert result.estimation.base_reserve_cents == 77900
        assert disguised_ids.isdisjoint(result.estimation.included_transaction_ids)


def test_explicitly_unconfirmed_consumption_is_explained_and_excluded(
    reserve_engine: Engine,
) -> None:
    with Session(reserve_engine) as session, session.begin():
        row = session.scalar(
            select(Transaction)
            .where(Transaction.category == "food")
            .order_by(Transaction.occurred_at.desc())
        )
        assert row is not None
        row.category_confirmed = False
        identifier = row.id
    with Session(reserve_engine) as session:
        result = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert result.estimation.status == "READY"
        assert identifier not in result.estimation.included_transaction_ids
        assert any(
            item.transaction_id == identifier for item in result.estimation.excluded_transactions
        )


def test_complete_day_boundary_and_current_day_do_not_fabricate_history(
    reserve_engine: Engine,
) -> None:
    with Session(reserve_engine) as session:
        before_closed = estimate_living_reserve(
            session, DEMO_USER_ID, SEED_AS_OF - timedelta(seconds=1), CONFIGURATION
        )
        assert before_closed.estimation.status == "INSUFFICIENT_HISTORY"
        baseline = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        tomorrow = estimate_living_reserve(
            session, DEMO_USER_ID, SEED_AS_OF + timedelta(days=1), CONFIGURATION
        )
        assert tomorrow.estimation.status == "INSUFFICIENT_HISTORY"
    with Session(reserve_engine) as session, session.begin():
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        session.add(
            Transaction(
                id=uuid4(),
                user_id=DEMO_USER_ID,
                account_id=cash.id,
                source_ref="today-no-historical-effect",
                direction="DEBIT",
                amount_cents=900000,
                category="food",
                category_confirmed=True,
                is_one_off=False,
                occurred_at=SEED_AS_OF,
                observed_at=SEED_AS_OF,
            )
        )
    with Session(reserve_engine) as session:
        after = estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert after == baseline


def test_empty_other_user_cannot_borrow_demo_history(reserve_engine: Engine) -> None:
    identifier = uuid4()
    with Session(reserve_engine) as session, session.begin():
        session.add(User(id=identifier, external_ref=str(identifier), display_name="Other"))
    with Session(reserve_engine) as session:
        result = estimate_living_reserve(session, identifier, SEED_AS_OF, CONFIGURATION)
        assert result.estimation.status == "INSUFFICIENT_HISTORY"
        assert result.estimation.recommended_reserve_cents is None
        assert result.source_evidence_ids == []
        assert result.candidate_configuration is None


def test_invalid_configuration_clock_and_account_capacity_are_explicit(
    reserve_engine: Engine,
) -> None:
    with Session(reserve_engine) as session:
        for configuration in (
            {"type": "emergency_buffer", "amount_cents": 1},
            {**CONFIGURATION, "method": {**CONFIGURATION["method"], "lookback_days": 367}},
        ):
            with pytest.raises(PolicyLifecycleError) as error:
                estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, configuration)
            assert error.value.status_code == 422
        with pytest.raises(PolicyLifecycleError) as error:
            estimate_living_reserve(
                session, DEMO_USER_ID, SEED_AS_OF.replace(tzinfo=None), CONFIGURATION
            )
        assert error.value.code == "INVALID_CLOCK"
        with pytest.raises(PolicyLifecycleError) as error:
            estimate_living_reserve(session, uuid4(), SEED_AS_OF, CONFIGURATION)
        assert error.value.status_code == 404
    with Session(reserve_engine) as session, session.begin():
        session.add_all(
            [
                Account(
                    id=uuid4(),
                    user_id=DEMO_USER_ID,
                    external_ref=str(uuid4()),
                    name="capacity-test",
                    account_type="CASH",
                    balance_cents=0,
                    observed_at=SEED_AS_OF,
                )
                for _ in range(96)
            ]
        )
    with Session(reserve_engine) as session:
        with pytest.raises(PolicyLifecycleError) as error:
            estimate_living_reserve(session, DEMO_USER_ID, SEED_AS_OF, CONFIGURATION)
        assert error.value.code == "INPUT_LIMIT_EXCEEDED"
