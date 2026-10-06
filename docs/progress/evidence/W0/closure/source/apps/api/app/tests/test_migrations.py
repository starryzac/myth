"""Exercise real PostgreSQL migrations; never downgrade the demonstration database."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.models import Account, ActionPlan, DecisionRun, Policy, PolicyVersion, Transaction, User
from app.db.session import create_database_engine
from app.db.settings import DatabaseSettings
from app.db.testing import require_test_database, temporary_database
from sqlalchemy import BigInteger, CheckConstraint, DateTime, insert, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
EXPECTED_TABLES = {
    "users",
    "accounts",
    "transactions",
    "credit_card_bills",
    "asset_products",
    "asset_positions",
    "evidence_items",
    "policies",
    "policy_versions",
    "goals",
    "policy_proposals",
    "decision_runs",
    "decision_constraints",
    "action_plans",
    "action_receipts",
    "audit_events",
    "simulated_bank_redemptions",
    "simulated_bank_postings",
    "bank_operations",
    "action_resource_reservations",
    "audit_epochs",
    "audit_subject_snapshots",
}


@pytest.mark.integration
def test_legacy_bank_migration_preserves_original_requests_and_posting_amounts(
    migrated_database: tuple[Engine, Config],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import audit_recording, demo_seed
    from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
    from app.services.simulated_bank import process_redemption
    from app.tests.test_simulated_bank import bank_action

    engine, config = migrated_database
    # This probes the pre-audit 0004 bank migration. Construct its original fixed
    # financial fixture on 0005 without deleting any retained 0006 history.
    command.downgrade(config, "0005_decision_trace")
    with Session(engine) as session, session.begin():
        demo_seed._ensure_products(session)
        demo_seed._insert_facts(session)
        demo_seed._open_seed_bank(session)
    monkeypatch.setattr(audit_recording, "record_bank_accepted", lambda *args: None)
    monkeypatch.setattr(audit_recording, "record_bank_settled", lambda *args: None)
    monkeypatch.setattr(audit_recording, "record_policy_version", lambda *args: None)
    monkeypatch.setattr(audit_recording, "record_decision", lambda *args: None)
    action_id, _, _, _ = bank_action(engine)
    result = process_redemption(engine, DEMO_USER_ID, action_id, SEED_AS_OF)
    sql = (
        "SELECT id,ledger_key,redemption_id,previous_posting_id,sequence_number,"
        "entry_kind,balance_before_cents,delta_cents,balance_after_cents,occurred_at "
        "FROM simulated_bank_postings ORDER BY id"
    )
    with engine.connect() as connection:
        before = connection.execute(text(sql)).all()
        request = connection.execute(text("SELECT * FROM simulated_bank_redemptions")).all()
    command.downgrade(config, "0003_simulated_bank")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text(sql)).all() == before
        assert connection.execute(text("SELECT * FROM simulated_bank_redemptions")).all() == request
        assert (
            connection.execute(text("SELECT id FROM bank_operations")).scalar() == result.request_id
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM simulated_bank_postings "
                    "WHERE operation_id = redemption_id AND operation_id IS NOT NULL"
                )
            ).scalar()
            == 2
        )


def migration_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def schema_snapshot(engine: Engine) -> dict[str, Any]:
    inspector = inspect(engine)
    return {
        table: {
            "columns": [
                (column["name"], str(column["type"]), column["nullable"], column["default"])
                for column in inspector.get_columns(table)
            ],
            "checks": sorted(
                inspector.get_check_constraints(table), key=lambda item: str(item["name"])
            ),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(table), key=lambda item: str(item["name"])
            ),
            "unique": sorted(
                inspector.get_unique_constraints(table), key=lambda item: str(item["name"])
            ),
            "indexes": sorted(inspector.get_indexes(table), key=lambda item: str(item["name"])),
        }
        for table in sorted(EXPECTED_TABLES)
    }


@pytest.fixture
def migrated_database() -> Iterator[tuple[Engine, Config]]:
    with temporary_database() as url:
        config = migration_config(url)
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            yield engine, config
        finally:
            engine.dispose()


@pytest.mark.integration
def test_fresh_upgrade_downgrade_upgrade_matches_all_twenty_two_models() -> None:
    with temporary_database() as url:
        config = migration_config(url)
        engine = create_database_engine(url)
        try:
            with engine.connect() as connection:
                assert connection.dialect.name == "postgresql"
                assert connection.dialect.server_version_info is not None
                assert connection.dialect.server_version_info[0] == 16
            first_snapshot: dict[str, Any] | None = None
            for _ in range(2):
                command.upgrade(config, "head")
                assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {
                    "alembic_version"
                }
                with engine.connect() as connection:
                    context = MigrationContext.configure(
                        connection, opts={"compare_type": True, "compare_server_default": True}
                    )
                    assert compare_metadata(context, Base.metadata) == []
                snapshot = schema_snapshot(engine)
                if first_snapshot is None:
                    first_snapshot = snapshot
                else:
                    assert snapshot == first_snapshot
                inspector = inspect(engine)
                for table in EXPECTED_TABLES:
                    for column in inspector.get_columns(table):
                        if column["name"].endswith("_cents"):
                            assert isinstance(column["type"], BigInteger)
                        if isinstance(column["type"], DateTime):
                            assert column["type"].timezone is True
                    expected_checks = {
                        constraint.name
                        for constraint in Base.metadata.tables[table].constraints
                        if isinstance(constraint, CheckConstraint)
                    }
                    actual_checks = {
                        item["name"] for item in inspector.get_check_constraints(table)
                    }
                    assert actual_checks == expected_checks
                command.downgrade(config, "base")
                assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
            command.upgrade(config, "head")
        finally:
            engine.dispose()


@pytest.mark.parametrize("database", ["bounded_funds", "postgres", "bf_test_demo", ""])
def test_destructive_migration_requires_a_generated_test_database(database: str) -> None:
    with pytest.raises(ValueError, match="bf_test_"):
        require_test_database(database)


def test_offline_downgrade_cannot_bypass_database_safety_guard() -> None:
    config = migration_config(DatabaseSettings().database_url)
    with pytest.raises(ValueError, match="Offline downgrade"):
        command.downgrade(config, "0001_mvp_tables:base", sql=True)


def test_settings_reject_non_simulation_and_non_postgresql() -> None:
    with pytest.raises(ValueError, match="simulation environment"):
        DatabaseSettings(simulation_mode=False)
    with pytest.raises(ValueError, match="Only PostgreSQL"):
        create_database_engine("sqlite:///:memory:")


@pytest.mark.integration
def test_database_constraints_reject_negative_balance_and_cross_user_version(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, _ = migrated_database
    first, second = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all(
            [
                User(id=first, external_ref="one", display_name="One"),
                User(id=second, external_ref="two", display_name="Two"),
            ]
        )
        session.commit()
        session.add(Account(user_id=first, external_ref="negative", name="Bad", balance_cents=-1))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        policy = Policy(user_id=first, name="Reserve", policy_type="living_reserve")
        session.add(policy)
        session.commit()
        session.add(
            PolicyVersion(
                user_id=second,
                policy_id=policy.id,
                version_number=1,
                configuration={},
                content_hash="a" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.integration
def test_integer_money_and_timezone_boundary(migrated_database: tuple[Engine, Config]) -> None:
    engine, _ = migrated_database
    with Session(engine) as session:
        user = User(external_ref="clock", display_name="Clock")
        session.add(user)
        session.commit()
        account = Account(
            user_id=user.id,
            external_ref="large",
            name="Large",
            balance_cents=9_000_000_000,
            observed_at=datetime(2026, 10, 3, 12, tzinfo=timezone(timedelta(hours=8))),
        )
        session.add(account)
        session.commit()
        assert session.scalar(select(Account.balance_cents)) == 9_000_000_000
        assert account.observed_at == datetime(2026, 10, 3, 4, tzinfo=UTC)
        assert account.observed_at.utcoffset() == timedelta(0)
        account.observed_at = datetime(2026, 10, 3, 12)
        with pytest.raises(StatementError, match="timezone-aware"):
            session.commit()
        session.rollback()
        with pytest.raises(StatementError, match="integer cents"):
            session.execute(
                insert(Account).values(
                    user_id=user.id,
                    external_ref="float",
                    name="Float",
                    balance_cents=1.5,
                )
            )


@pytest.mark.integration
def test_source_and_action_idempotency_are_enforced_in_postgresql(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, _ = migrated_database
    user_id, account_id, run_id = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 10, 3, tzinfo=UTC)
    with Session(engine) as session:
        session.add(User(id=user_id, external_ref="idempotency", display_name="Synthetic"))
        session.commit()
        session.add(Account(id=account_id, user_id=user_id, external_ref="main", name="Main"))
        session.commit()
        session.add(
            DecisionRun(
                id=run_id,
                user_id=user_id,
                idempotency_key="run-1",
                trigger_type="TEST",
                algorithm_version="fixture-v1",
                as_of=now,
                input_snapshot={},
                snapshot_hash="a" * 64,
            )
        )
        session.commit()
        for attempt in range(2):
            session.add(
                Transaction(
                    user_id=user_id,
                    account_id=account_id,
                    source_ref="bank-source-1",
                    direction="CREDIT",
                    amount_cents=100,
                    category="salary",
                    occurred_at=now,
                )
            )
            if attempt == 0:
                session.commit()
            else:
                with pytest.raises(IntegrityError, match="uq_transactions_account_source"):
                    session.commit()
                session.rollback()
        for attempt in range(2):
            session.add(
                ActionPlan(
                    user_id=user_id,
                    decision_run_id=run_id,
                    source_account_id=account_id,
                    action_type="ASSET_PURCHASE",
                    amount_cents=100,
                    idempotency_key="same-action",
                    request={},
                    request_hash="b" * 64,
                )
            )
            if attempt == 0:
                session.commit()
            else:
                with pytest.raises(IntegrityError, match="uq_action_plans_user_idempotency"):
                    session.commit()
                session.rollback()
        assert len(session.scalars(select(Transaction)).all()) == 1
        assert len(session.scalars(select(ActionPlan)).all()) == 1


@pytest.mark.integration
def test_policy_version_number_is_unique_and_configuration_is_an_object(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, _ = migrated_database
    user_id, policy_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, external_ref="versioned", display_name="Versioned"))
        session.commit()
        session.add(
            Policy(id=policy_id, user_id=user_id, name="Reserve", policy_type="living_reserve")
        )
        session.commit()
        values = {
            "user_id": user_id,
            "policy_id": policy_id,
            "version_number": 1,
            "configuration": {},
            "content_hash": "c" * 64,
        }
        session.execute(insert(PolicyVersion).values(**values))
        session.commit()
        with pytest.raises(IntegrityError, match="uq_policy_versions_policy_version"):
            session.execute(insert(PolicyVersion).values(**values))
        session.rollback()
        with pytest.raises(IntegrityError, match="ck_policy_versions_configuration_object"):
            session.execute(
                insert(PolicyVersion).values(
                    user_id=user_id,
                    policy_id=policy_id,
                    version_number=2,
                    configuration=[],
                    content_hash="d" * 64,
                )
            )
        session.rollback()
