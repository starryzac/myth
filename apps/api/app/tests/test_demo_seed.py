"""Real PostgreSQL proof of repeatable, isolated synthetic account facts."""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.db.base import Base
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    AssetPosition,
    AssetProduct,
    AuditEvent,
    CreditCardBill,
    DecisionConstraint,
    DecisionRun,
    EvidenceItem,
    Goal,
    Policy,
    PolicyVersion,
    SimulatedBankPosting,
    Transaction,
    User,
)
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.domain.history_coverage import account_history_manifest
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import (
    DEMO_USER_ID,
    DEMO_USER_REF,
    SEED_AS_OF,
    SEED_END,
    SEED_START,
    SeedConflictError,
    seed_demo,
)
from app.services.simulated_bank import open_simulated_bank
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.integration


@pytest.fixture
def demo_engine() -> Iterator[Engine]:
    with temporary_database() as url:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


def database_snapshot(engine: Engine) -> str:
    with engine.connect() as connection:
        result = {
            table.name: [
                dict(row)
                for row in connection.execute(select(table).order_by(table.c.id)).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }
    return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)


def test_seed_opens_independent_bank_once_without_creating_recovery_authority(
    demo_engine: Engine,
) -> None:
    first = seed_demo(demo_engine)
    assert first.seed_version == "mvp-301-v6"
    assert first.counts["simulated_bank_redemptions"] == 0
    # Four non-credit-card cash ledgers and three exact existing positions.
    assert first.counts["simulated_bank_postings"] == 7
    assert first.cash_balance_cents == 3462400
    assert first.asset_principal_cents == 500000
    assert first.counts["policies"] == first.counts["action_plans"] == 0
    with Session(demo_engine) as session:
        accounts = session.scalars(select(Account)).all()
        positions = session.scalars(select(AssetPosition)).all()
        entries = session.scalars(select(SimulatedBankPosting)).all()
        expected = {
            f"CASH:{item.id}": item.balance_cents
            for item in accounts
            if item.account_type != "CREDIT_CARD"
        }
        expected.update({f"POSITION:{item.id}": item.principal_cents for item in positions})
        assert {entry.ledger_key: entry.balance_after_cents for entry in entries} == expected
        for entry in entries:
            assert entry.entry_kind == "OPENING"
            assert entry.sequence_number == 1 and entry.balance_before_cents == 0
            assert entry.delta_cents == entry.balance_after_cents
            assert entry.redemption_id is entry.previous_posting_id is None
            assert entry.created_at == entry.occurred_at == SEED_AS_OF
    before = database_snapshot(demo_engine)
    second = seed_demo(demo_engine)
    assert second == first
    assert database_snapshot(demo_engine) == before


def test_new_product_versions_have_explicit_principal_yield_and_zero_fee_contracts(
    demo_engine: Engine,
) -> None:
    summary = seed_demo(demo_engine)
    assert summary.seed_version == "mvp-301-v6"
    with Session(demo_engine) as session:
        products = session.scalars(select(AssetProduct)).all()
        old = {item.product_code: item for item in products if item.version_number == 1}
        current = {item.product_code: item for item in products if item.version_number == 2}
        assert len(old) == len(current) == 3
        assert set(old) == set(current)
        for code, product in current.items():
            assert product.id != old[code].id
            assert product.created_at == product.effective_from == SEED_AS_OF
            assert old[code].maturity_rule == {"kind": "RETURN_TO_CASH", "auto_rollover": False}
            terms = product.maturity_rule
            assert terms["guaranteed"] is True and terms["day_basis"] == "CALENDAR"
            assert terms["principal_return_bps"] == 10000
            assert terms["rollover"] is False and terms["auto_rollover"] is False
            assert terms["settlement_delay_days"] == product.redemption_delay_days
            fixed = product.asset_class == "FIXED_DEPOSIT"
            assert terms["protocol"] == (
                "fixed-principal-return-v1" if fixed else "planned-principal-return-v1"
            )
            if fixed:
                assert terms["term_days"] == product.lock_days == 30
            yield_rule = terms["yield_rule"]
            assert yield_rule == {
                "protocol": "simple-annual-yield-v1",
                "basis": "ACT_365",
                "annual_yield_bps": product.annual_yield_bps,
                "simulation": True,
                "fee_cents": 0,
                "purchase_fee_bps": 0,
                "redemption_fee_bps": 0,
                "accrual": "UNTIL_MATURITY" if fixed else "UNTIL_REDEMPTION_REQUEST",
            }
        assert {item.product_id for item in session.scalars(select(AssetPosition))} == {
            item.id for item in old.values()
        }


def test_seed_binds_boundary_facts_to_account_bill_and_position_identity(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        for account in session.scalars(select(Account)):
            proofs = [
                item
                for item in session.scalars(select(EvidenceItem))
                if item.source_type == "SIMULATED_BANK_BALANCE"
                and item.content.get("account_id") == str(account.id)
            ]
            assert len(proofs) == 1
            proof = proofs[0].content
            assert proof["user_id"] == str(account.user_id)
            assert proof["account_type"] == account.account_type
            assert proof["currency"] == account.currency
            assert proof["balance_cents"] == account.balance_cents
            assert proof["as_of"] == account.observed_at.isoformat()
        for bill in session.scalars(select(CreditCardBill)):
            item = session.get(EvidenceItem, bill.evidence_id)
            assert item is not None
            proof = item.content
            assert proof["user_id"] == str(bill.user_id)
            assert proof["bill_id"] == str(bill.id)
            assert proof["account_id"] == str(bill.account_id)
            assert proof["source_ref"] == bill.source_ref
            assert proof["minimum_due_cents"] == bill.minimum_due_cents
        for position in session.scalars(select(AssetPosition)):
            proofs = [
                item
                for item in session.scalars(select(EvidenceItem))
                if item.source_type == "SIMULATED_BANK_POSITION"
                and item.content.get("position_id") == str(position.id)
            ]
            assert len(proofs) == 1
            proof = proofs[0].content
            assert proof["user_id"] == str(position.user_id)
            assert proof["account_id"] == str(position.account_id)
            assert proof["product_id"] == str(position.product_id)
            assert proof["goal_id"] is None and position.goal_id is None
            assert proof["policy_version_id"] is None and position.policy_version_id is None
            assert proof["principal_cents"] == position.principal_cents
            assert proof["purchased_at"] == position.purchased_at.isoformat()
            assert proof["maturity_at"] == (
                position.maturity_at.isoformat() if position.maturity_at else None
            )
            assert proof["available_at"] is None and position.available_at is None
            assert proof["status"] == position.status
            assert proof["as_of"] == SEED_AS_OF.isoformat()
            assert proofs[0].observed_at == SEED_AS_OF


def test_seed_declares_complete_empty_automatic_exposure_without_claiming_authority(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        statements = session.scalars(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE")
        ).all()
        assert len(statements) == 1
        proof = statements[0]
        assert proof.evidence_level == "BANK_CONFIRMED"
        assert proof.observed_at == proof.valid_from == SEED_AS_OF
        payload = proof.content
        assert payload["simulation"] is True and payload["complete"] is True
        assert payload["protocol"] == "asset-exposure-v1"
        assert payload["user_id"] == str(DEMO_USER_ID)
        assert payload["as_of"] == SEED_AS_OF.isoformat()
        assert payload["actions"] == payload["receipts"] == payload["settlements"] == []
        assert {item["id"] for item in payload["accounts"]} == {
            str(item.id) for item in session.scalars(select(Account))
        }
        assert {item["id"] for item in payload["positions"]} == {
            str(item.id) for item in session.scalars(select(AssetPosition))
        }
        assert all(len(item["digest"]) == 64 for item in payload["positions"])
        assert session.scalars(select(Policy)).all() == []
        assert session.scalars(select(ActionPlan)).all() == []


def test_seed_repeats_exact_database_content_and_all_required_facts(demo_engine: Engine) -> None:
    first = seed_demo(demo_engine)
    snapshot = database_snapshot(demo_engine)
    second = seed_demo(demo_engine)
    assert first == second
    assert database_snapshot(demo_engine) == snapshot
    assert first.user_id == DEMO_USER_ID
    assert first.as_of == SEED_AS_OF
    assert first.days == 60
    assert first.counts["accounts"] == 5
    assert first.counts["asset_products"] == 6
    assert first.counts["policies"] == 0
    assert len(first.dataset_sha256) == 64
    with Session(demo_engine) as session:
        transactions = session.scalars(
            select(Transaction).where(Transaction.user_id == DEMO_USER_ID)
        ).all()
        local_days = {
            row.occurred_at.astimezone(timezone(timedelta(hours=8))).date() for row in transactions
        }
        assert local_days == {SEED_START + timedelta(days=day) for day in range(60)}
        assert max(local_days) == SEED_END
        assert {row.category for row in transactions} >= {
            "salary",
            "rent",
            "credit_card_payment",
            "utilities",
            "food",
            "transport",
            "daily_necessities",
            "one_off_purchase",
        }
        assert (
            sum(row.category == "one_off_purchase" and row.is_one_off for row in transactions) == 1
        )
        assert {row.status for row in session.scalars(select(CreditCardBill))} == {"PAID", "UNPAID"}
        assert len(session.scalars(select(Policy)).all()) == 0
        assert all(row.occurred_at <= row.observed_at <= SEED_AS_OF for row in transactions)
        confirmations = {
            evidence.content["transaction_id"]: evidence
            for evidence in session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.source_type == "SIMULATED_USER_CATEGORY_CONFIRMATION"
                )
            )
        }
        for transaction in transactions:
            if transaction.category_confirmed:
                evidence = confirmations[str(transaction.id)]
                assert evidence.evidence_level == "USER_DECLARED"
                assert evidence.content["category"] == transaction.category
                assert evidence.content["actor"] == "synthetic_user"
        for evidence in session.scalars(select(EvidenceItem)):
            canonical = json.dumps(
                evidence.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            assert evidence.content_hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            assert evidence.observed_at <= SEED_AS_OF


def test_every_cash_balance_follows_its_ledger_without_counting_positions_twice(
    demo_engine: Engine,
) -> None:
    summary = seed_demo(demo_engine)
    with Session(demo_engine) as session:
        accounts = session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID)).all()
        assert {row.account_type for row in accounts} == {
            "CASH",
            "GOAL",
            "CREDIT_CARD",
            "CASH_MANAGEMENT",
            "FIXED_DEPOSIT",
        }
        for account in accounts:
            balance = 0
            rows = session.scalars(
                select(Transaction)
                .where(Transaction.account_id == account.id)
                .order_by(Transaction.occurred_at)
            ).all()
            for row in rows:
                balance += row.amount_cents if row.direction == "CREDIT" else -row.amount_cents
                assert balance >= 0
                assert row.balance_after_cents == balance
            assert account.balance_cents == balance
        positions = session.scalars(select(AssetPosition)).all()
        assert summary.cash_balance_cents == sum(account.balance_cents for account in accounts)
        assert summary.asset_principal_cents == sum(
            position.principal_cents for position in positions
        )
        assert (
            summary.total_asset_cents
            == summary.cash_balance_cents
            + summary.asset_principal_cents
            + summary.asset_yield_cents
        )
        assert summary.credit_card_unpaid_cents == sum(
            bill.total_cents - bill.paid_cents for bill in session.scalars(select(CreditCardBill))
        )


def test_v2_seed_declares_closed_history_scope_and_immutable_economic_roles(
    demo_engine: Engine,
) -> None:
    summary = seed_demo(demo_engine)
    with Session(demo_engine) as session:
        coverage = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
            )
        )
        assert coverage is not None
        assert summary.seed_version == "mvp-301-v6"
        assert summary.as_of == datetime(2026, 10, 3, 16, tzinfo=UTC)
        assert coverage.evidence_level == "BANK_CONFIRMED"
        assert coverage.valid_from == coverage.observed_at == summary.as_of
        content = coverage.content
        assert content["protocol"] == "transaction-history-coverage-v1"
        assert content["simulation"] is True
        assert content["user_id"] == str(DEMO_USER_ID)
        assert content["timezone"] == "Asia/Shanghai"
        assert content["period_start"] == "2026-08-05"
        assert content["period_end"] == "2026-10-03"
        accounts = session.scalars(select(Account).where(Account.user_id == DEMO_USER_ID)).all()
        assert content["scope_account_ids"] == sorted(str(row.id) for row in accounts)
        manifests = {item["account_id"]: item for item in content["accounts"]}
        counts = {
            "CASH": 127,
            "GOAL": 2,
            "CREDIT_CARD": 0,
            "CASH_MANAGEMENT": 0,
            "FIXED_DEPOSIT": 0,
        }
        for account in accounts:
            assert manifests[str(account.id)]["transaction_count"] == counts[account.account_type]
            assert len(manifests[str(account.id)]["bank_fact_digest"]) == 64
        roles = {
            "opening_balance": "OPENING",
            "salary": "INCOME",
            "internal_transfer": "INTERNAL_TRANSFER",
            "asset_purchase": "ASSET_PURCHASE",
            "credit_card_payment": "CREDIT_CARD_PAYMENT",
        }
        for row in session.scalars(select(Transaction)):
            evidence = session.get(EvidenceItem, row.evidence_id)
            assert evidence is not None
            assert evidence.content["economic_role"] == roles.get(row.category, "CONSUMPTION")


def test_other_user_and_referenced_global_product_survive_reset(demo_engine: Engine) -> None:
    seed_demo(demo_engine)
    other_id, account_id, position_id = uuid4(), uuid4(), uuid4()
    with Session(demo_engine) as session:
        product = session.scalars(select(AssetProduct).order_by(AssetProduct.product_code)).first()
        assert product is not None
        product_id = product.id
        session.add(User(id=other_id, external_ref="another-simulation", display_name="Other"))
        session.commit()
        session.add(
            Account(
                id=account_id,
                user_id=other_id,
                external_ref="other-cash",
                name="Other",
                balance_cents=123,
            )
        )
        session.commit()
        session.add(
            AssetPosition(
                id=position_id,
                user_id=other_id,
                account_id=account_id,
                product_id=product_id,
                principal_cents=10000,
                purchased_at=SEED_AS_OF,
            )
        )
        session.commit()
        other_posting_ids = open_simulated_bank(
            session,
            other_id,
            SEED_AS_OF,
            cash_balances={account_id: 123},
            position_principals={position_id: 10000},
        )
        session.commit()
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        assert session.get(User, other_id) is not None
        account = session.get(Account, account_id)
        assert account is not None and account.balance_cents == 123
        position = session.get(AssetPosition, position_id)
        assert position is not None and position.product_id == product_id
        assert session.get(AssetProduct, product_id) is not None
        other_postings = session.scalars(
            select(SimulatedBankPosting).where(SimulatedBankPosting.user_id == other_id)
        ).all()
        assert {row.id for row in other_postings} == set(other_posting_ids)
        assert {row.ledger_key: row.balance_after_cents for row in other_postings} == {
            f"CASH:{account_id}": 123,
            f"POSITION:{position_id}": 10000,
        }


def test_v2_reuses_real_v1_catalog_without_overwriting_shared_products(demo_engine: Engine) -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "seed-v1-products.json").read_text(encoding="utf-8")
    )
    assert fixture["source_revision"] == "cbe6fff" and fixture["seed_version"] == "mvp-102-v1"
    other_id, account_id, position_id = uuid4(), uuid4(), uuid4()
    with Session(demo_engine) as session, session.begin():
        for raw in fixture["products"]:
            values = dict(raw)
            values["id"] = UUID(values["id"])
            for field in ("created_at", "effective_from"):
                values[field] = datetime.fromisoformat(values[field])
            session.add(AssetProduct(**values))
        session.add(User(id=other_id, external_ref="v1-shared-holder", display_name="Other"))
        session.flush()
        session.add(Account(id=account_id, user_id=other_id, external_ref="v1-cash", name="Other"))
        session.flush()
        session.add(
            AssetPosition(
                id=position_id,
                user_id=other_id,
                account_id=account_id,
                product_id=UUID(fixture["products"][0]["id"]),
                principal_cents=12345,
                purchased_at=datetime(2026, 10, 3, 15, 59, 59, tzinfo=UTC),
            )
        )
    with demo_engine.connect() as connection:
        before = [
            dict(row)
            for row in connection.execute(
                select(AssetProduct.__table__).order_by(AssetProduct.id)
            ).mappings()
        ]
    summary = seed_demo(demo_engine)
    assert summary.seed_version == "mvp-301-v6"
    with demo_engine.connect() as connection:
        after = [
            dict(row)
            for row in connection.execute(
                select(AssetProduct.__table__)
                .where(AssetProduct.version_number == 1)
                .order_by(AssetProduct.id)
            ).mappings()
        ]
    assert after == before
    assert {row["created_at"] for row in after} == {datetime(2026, 10, 3, 15, 59, 59, tzinfo=UTC)}
    with Session(demo_engine) as session:
        held = session.get(AssetPosition, position_id)
        assert held is not None and held.principal_cents == 12345
        assert held.user_id == other_id and held.account_id == account_id


def test_coverage_digest_ignores_user_annotations_but_detects_bank_changes(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        rows = list(session.scalars(select(Transaction)))
        evidence = {item.id: item for item in session.scalars(select(EvidenceItem))}
        coverage = next(
            item
            for item in evidence.values()
            if item.source_type == "SIMULATED_TRANSACTION_HISTORY_COVERAGE"
        )
        original = next(
            item for item in coverage.content["accounts"] if item["account_id"] == str(account.id)
        )
        assert account_history_manifest(account.id, reversed(rows), evidence) == original
        changed = next(row for row in rows if row.category == "asset_purchase")
        changed.category, changed.category_confirmed, changed.is_one_off = "food", True, True
        assert account_history_manifest(account.id, rows, evidence) == original
        without_row = account_history_manifest(
            account.id, [r for r in rows if r.id != changed.id], evidence
        )
        assert without_row["transaction_count"] == original["transaction_count"] - 1
        assert without_row["bank_fact_digest"] != original["bank_fact_digest"]
        assert changed.evidence_id is not None
        bank = evidence[changed.evidence_id]
        assert bank.content["economic_role"] == "ASSET_PURCHASE"
        changed.amount_cents += 1
        bank.content = {**bank.content, "amount_cents": changed.amount_cents}
        bank.content_hash = configuration_hash(bank.content)
        assert (
            account_history_manifest(account.id, rows, evidence)["bank_fact_digest"]
            != original["bank_fact_digest"]
        )


@pytest.mark.parametrize("collision", ["id", "external_ref"])
def test_demo_identity_collision_is_rejected_without_modification(
    demo_engine: Engine, collision: str
) -> None:
    with Session(demo_engine) as session:
        session.add(
            User(
                id=DEMO_USER_ID if collision == "id" else uuid4(),
                external_ref="occupied" if collision == "id" else DEMO_USER_REF,
                display_name="Must survive",
            )
        )
        session.commit()
    before = database_snapshot(demo_engine)
    with pytest.raises(SeedConflictError):
        seed_demo(demo_engine)
    assert database_snapshot(demo_engine) == before


def test_failed_insert_rolls_back_the_whole_reset(demo_engine: Engine) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        account = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        account.name = "Existing state must survive a failed reset"
        session.commit()
    before = database_snapshot(demo_engine)
    # This real database fault occurs after the deletion stage, during salary insertion.
    with demo_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_seed_salary() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.category = 'salary' THEN RAISE EXCEPTION 'forced seed failure'; END IF;
              RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER reject_seed_salary BEFORE INSERT ON transactions
            FOR EACH ROW EXECUTE FUNCTION reject_seed_salary()
        """)
        )
    with pytest.raises(DBAPIError, match="forced seed failure"):
        seed_demo(demo_engine)
    assert database_snapshot(demo_engine) == before


def test_non_simulated_reserved_identity_is_never_reset(demo_engine: Engine) -> None:
    # Corrupt only this disposable database to prove the application guard independently
    # from the production CHECK that normally makes such a row impossible.
    with demo_engine.begin() as connection:
        connection.execute(text("ALTER TABLE users DROP CONSTRAINT ck_users_simulation_only"))
    with Session(demo_engine) as session:
        session.add(
            User(
                id=DEMO_USER_ID,
                external_ref=DEMO_USER_REF,
                display_name="Protected",
                is_simulated=False,
            )
        )
        session.commit()
    before = database_snapshot(demo_engine)
    with pytest.raises(SeedConflictError, match="not simulated"):
        seed_demo(demo_engine)
    assert database_snapshot(demo_engine) == before


def test_existing_product_drift_is_rejected_instead_of_overwritten(demo_engine: Engine) -> None:
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        product = session.scalars(select(AssetProduct).order_by(AssetProduct.product_code)).first()
        assert product is not None
        product.annual_yield_bps += 1
        session.commit()
    before = database_snapshot(demo_engine)
    with pytest.raises(SeedConflictError, match="products are preserved"):
        seed_demo(demo_engine)
    assert database_snapshot(demo_engine) == before


def test_reset_clears_the_demo_dependency_graph_including_self_references(
    demo_engine: Engine,
) -> None:
    baseline = seed_demo(demo_engine)
    with Session(demo_engine) as session:
        policy_id, version_id, goal_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
        plan_id, receipt_id, first_event_id = uuid4(), uuid4(), uuid4()
        account = session.scalars(select(Account).where(Account.account_type == "CASH")).one()
        position = session.scalars(select(AssetPosition).order_by(AssetPosition.id)).first()
        assert position is not None
        session.add(
            Policy(
                id=policy_id, user_id=DEMO_USER_ID, name="Test proposed", policy_type="goal_saving"
            )
        )
        session.flush()
        session.add(
            PolicyVersion(
                id=version_id,
                user_id=DEMO_USER_ID,
                policy_id=policy_id,
                version_number=1,
                configuration={},
                content_hash="a" * 64,
            )
        )
        session.flush()
        session.add(
            Goal(
                id=goal_id,
                user_id=DEMO_USER_ID,
                policy_id=policy_id,
                policy_version_id=version_id,
                account_id=account.id,
                name="Test goal",
                target_cents=10000,
                deadline=SEED_END,
                monthly_min_cents=0,
                monthly_target_cents=100,
                monthly_max_cents=200,
            )
        )
        session.add(
            DecisionRun(
                id=run_id,
                user_id=DEMO_USER_ID,
                idempotency_key="test-run",
                trigger_type="TEST",
                algorithm_version="fixture",
                as_of=SEED_AS_OF,
                input_snapshot={},
                snapshot_hash="b" * 64,
            )
        )
        session.flush()
        position.goal_id = goal_id
        position.policy_version_id = version_id
        session.add(
            DecisionConstraint(
                user_id=DEMO_USER_ID,
                decision_run_id=run_id,
                policy_version_id=version_id,
                constraint_key="test-constraint",
                is_hard=True,
                calculation={},
                reason_code="TEST",
            )
        )
        session.add(
            ActionPlan(
                id=plan_id,
                user_id=DEMO_USER_ID,
                decision_run_id=run_id,
                policy_version_id=version_id,
                source_account_id=account.id,
                goal_id=goal_id,
                position_id=position.id,
                action_type="TEST",
                amount_cents=100,
                idempotency_key="test-plan",
                request={},
                request_hash="c" * 64,
            )
        )
        session.flush()
        session.add(
            ActionReceipt(
                id=receipt_id,
                user_id=DEMO_USER_ID,
                action_plan_id=plan_id,
                attempt_number=1,
                receipt_ref="test-receipt",
                status="UNKNOWN",
                response={},
                occurred_at=SEED_AS_OF,
            )
        )
        session.flush()
        for sequence in (1, 2):
            session.add(
                AuditEvent(
                    id=first_event_id if sequence == 1 else uuid4(),
                    user_id=DEMO_USER_ID,
                    sequence_number=sequence,
                    event_type="TestEvent",
                    aggregate_type="Action",
                    aggregate_id=plan_id,
                    correlation_id=run_id,
                    causation_id=None if sequence == 1 else first_event_id,
                    decision_run_id=run_id,
                    action_plan_id=plan_id,
                    action_receipt_id=receipt_id,
                    idempotency_key=f"test-event-{sequence}",
                    payload={},
                    event_hash=str(sequence) * 64,
                    occurred_at=SEED_AS_OF,
                )
            )
            session.flush()
        evidence = session.scalars(select(EvidenceItem).order_by(EvidenceItem.id)).first()
        assert evidence is not None
        session.add(
            EvidenceItem(
                user_id=DEMO_USER_ID,
                evidence_level="BANK_OBSERVED",
                source_type="TEST",
                source_ref="test-superseding",
                content={},
                content_hash="d" * 64,
                valid_from=SEED_AS_OF,
                observed_at=SEED_AS_OF,
                supersedes_id=evidence.id,
            )
        )
        session.commit()
    assert seed_demo(demo_engine) == baseline
