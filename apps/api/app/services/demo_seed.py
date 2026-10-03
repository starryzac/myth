"""Deterministic synthetic facts. Reset only the reserved demo tenant in one transaction."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid5

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
    PolicyProposal,
    PolicyVersion,
    Transaction,
    User,
)
from app.domain.demo_identity import DEMO_USER_ID as DEMO_USER_ID
from app.domain.demo_identity import DEMO_USER_REF as DEMO_USER_REF
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

SEED_VERSION = "mvp-102-v1"
SEED_START = date(2026, 8, 5)
SEED_END = date(2026, 10, 3)
SEED_AS_OF = datetime(2026, 10, 3, 15, 59, 59, tzinfo=UTC)
SEED_NAMESPACE = UUID("8758cb2f-60ec-5703-a3df-1b64ca068e12")
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
ESSENTIAL_CATEGORIES = frozenset({"food", "transport", "daily_necessities"})


class SeedConflictError(ValueError):
    """A reserved identity or immutable product is occupied by different data."""


class SeedSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    seed_version: str
    user_id: UUID
    as_of: datetime
    period_start: date
    period_end: date
    days: int
    counts: dict[str, int]
    cash_balance_cents: int
    asset_principal_cents: int
    asset_yield_cents: int
    total_asset_cents: int
    credit_card_unpaid_cents: int
    dataset_sha256: str


def _id(key: str) -> UUID:
    return uuid5(SEED_NAMESPACE, key)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), LOCAL_TIMEZONE).astimezone(UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    key: str
    account: str
    category: str
    direction: Literal["CREDIT", "DEBIT"]
    amount_cents: int
    occurred_at: datetime
    counterparty_ref: str
    is_one_off: bool = False


def _ledger() -> list[LedgerEntry]:
    entries = [
        LedgerEntry(
            "opening",
            "cash",
            "opening_balance",
            "CREDIT",
            3_000_000,
            _at(SEED_START, 0),
            "synthetic-opening-balance",
        )
    ]
    for index in range(60):
        day = SEED_START + timedelta(days=index)
        entries.append(
            LedgerEntry(
                f"{day}:food",
                "cash",
                "food",
                "DEBIT",
                3_500 + (index % 7) * 200,
                _at(day, 12),
                "synthetic-food-category",
            )
        )
        if day.weekday() < 5:
            entries.append(
                LedgerEntry(
                    f"{day}:transport",
                    "cash",
                    "transport",
                    "DEBIT",
                    600,
                    _at(day, 18),
                    "synthetic-transport-category",
                )
            )
        if index % 7 == 0:
            entries.append(
                LedgerEntry(
                    f"{day}:necessities",
                    "cash",
                    "daily_necessities",
                    "DEBIT",
                    6_500 + (index % 3) * 500,
                    _at(day, 20),
                    "synthetic-necessities-category",
                )
            )
        if day.day == 10:
            entries.append(
                LedgerEntry(
                    f"{day}:salary",
                    "cash",
                    "salary",
                    "CREDIT",
                    1_200_000,
                    _at(day, 9),
                    "synthetic-employer-001",
                )
            )
        if day.day == 28:
            entries.append(
                LedgerEntry(
                    f"{day}:rent",
                    "cash",
                    "rent",
                    "DEBIT",
                    180_000,
                    _at(day, 10),
                    "synthetic-landlord-001",
                )
            )
        if day.day == 15:
            entries.append(
                LedgerEntry(
                    f"{day}:utilities",
                    "cash",
                    "utilities",
                    "DEBIT",
                    18_000 if day.month == 8 else 21_000,
                    _at(day, 10),
                    "synthetic-utilities-001",
                )
            )
        if day.day == 20:
            entries.append(
                LedgerEntry(
                    f"{day}:card-payment",
                    "cash",
                    "credit_card_payment",
                    "DEBIT",
                    120_000 if day.month == 8 else 135_000,
                    _at(day, 10),
                    "synthetic-credit-card-001",
                )
            )
        if day.day == 12:
            for account, direction, other in [
                ("cash", "DEBIT", "goal"),
                ("goal", "CREDIT", "cash"),
            ]:
                entries.append(
                    LedgerEntry(
                        f"{day}:goal-transfer:{account}",
                        account,
                        "internal_transfer",
                        "DEBIT" if direction == "DEBIT" else "CREDIT",
                        80_000,
                        _at(day, 10),
                        f"synthetic-account:{other}",
                    )
                )
    entries.append(
        LedgerEntry(
            "2026-09-21:one-off",
            "cash",
            "one_off_purchase",
            "DEBIT",
            450_000,
            _at(date(2026, 9, 21), 15),
            "synthetic-one-off-purchase",
            True,
        )
    )
    for key, amount, day in [
        ("t0", 250_000, date(2026, 9, 12)),
        ("t1", 150_000, date(2026, 9, 13)),
        ("fixed", 100_000, date(2026, 9, 25)),
    ]:
        entries.append(
            LedgerEntry(
                f"{day}:purchase:{key}",
                "cash",
                "asset_purchase",
                "DEBIT",
                amount,
                _at(day, 11),
                f"synthetic-product:{key}",
            )
        )
    return sorted(entries, key=lambda entry: (entry.occurred_at, entry.key))


def _product_values() -> list[dict[str, Any]]:
    result = []
    for key, code, name, asset_class, lock_days, delay, yield_bps, loss_bps in [
        ("t0", "BF_DEMO_T0", "模拟现金管理 T+0", "CASH_MGMT_T0", 0, 0, 150, 0),
        ("t1", "BF_DEMO_T1", "模拟现金管理 T+1", "CASH_MGMT_T1", 0, 1, 180, 0),
        ("fixed", "BF_DEMO_FIXED_30D", "模拟三十天定存", "FIXED_DEPOSIT", 30, 0, 220, 20),
    ]:
        result.append(
            {
                "id": _id(f"product:{key}"),
                "created_at": SEED_AS_OF,
                "product_code": code,
                "version_number": 1,
                "name": name,
                "asset_class": asset_class,
                "risk_level": 0,
                "principal_fluctuation": False,
                "minimum_purchase_cents": 10_000,
                "lock_days": lock_days,
                "redemption_delay_days": delay,
                "annual_yield_bps": yield_bps,
                "early_withdrawal_loss_bps": loss_bps,
                "maturity_rule": {"kind": "RETURN_TO_CASH", "auto_rollover": False},
                "early_withdrawal_rule": {
                    "allowed": True,
                    "requires_confirmation_if_loss": True,
                    "loss_basis": "principal_cents",
                    "simulation": True,
                },
                "auto_purchase_allowed": True,
                "auto_redeem_allowed": key != "fixed",
                "effective_from": _at(SEED_START, 0),
                "effective_until": None,
            }
        )
    return result


def _check_identity(session: Session) -> None:
    occupants = session.scalars(
        select(User)
        .where(
            or_(
                User.id == DEMO_USER_ID,
                User.external_ref == DEMO_USER_REF,
            )
        )
        .with_for_update()
    ).all()
    if any(
        user.id != DEMO_USER_ID or user.external_ref != DEMO_USER_REF or not user.is_simulated
        for user in occupants
    ):
        raise SeedConflictError("Reserved demo identity is occupied or is not simulated")


def _ensure_products(session: Session) -> None:
    for expected in _product_values():
        occupants = session.scalars(
            select(AssetProduct)
            .where(
                or_(
                    AssetProduct.id == expected["id"],
                    (AssetProduct.product_code == expected["product_code"])
                    & (AssetProduct.version_number == expected["version_number"]),
                )
            )
            .with_for_update()
        ).all()
        if not occupants:
            session.add(AssetProduct(**expected))
        elif len(occupants) != 1 or any(
            getattr(occupants[0], field) != value for field, value in expected.items()
        ):
            raise SeedConflictError(
                "Reserved synthetic product differs; existing products are preserved"
            )
    session.flush()


def _clear_demo(session: Session) -> None:
    # Existing self-references are severed only inside this tenant's atomic reset.
    # A later append-only audit service must give reset its own audited mechanism.
    session.execute(
        update(AuditEvent).where(AuditEvent.user_id == DEMO_USER_ID).values(causation_id=None)
    )
    session.execute(
        update(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID).values(supersedes_id=None)
    )
    for model in [
        AuditEvent,
        ActionReceipt,
        ActionPlan,
        DecisionConstraint,
        AssetPosition,
        Goal,
        Transaction,
        CreditCardBill,
        PolicyProposal,
        PolicyVersion,
        Policy,
        DecisionRun,
        EvidenceItem,
        Account,
    ]:
        session.execute(delete(model).where(model.__table__.c.user_id == DEMO_USER_ID))
    session.execute(delete(User).where(User.id == DEMO_USER_ID))


def _evidence(
    key: str,
    source_type: str,
    content: dict[str, Any],
    valid_from: datetime,
    level: str = "BANK_CONFIRMED",
) -> EvidenceItem:
    return EvidenceItem(
        id=_id(f"evidence:{key}"),
        user_id=DEMO_USER_ID,
        created_at=SEED_AS_OF,
        evidence_level=level,
        source_type=source_type,
        source_ref=f"{SEED_VERSION}:{key}",
        content=content,
        content_hash=_hash(content),
        valid_from=valid_from,
        observed_at=valid_from,
        status="VALID",
    )


def _insert_facts(session: Session) -> None:
    session.add(
        User(
            id=DEMO_USER_ID,
            external_ref=DEMO_USER_REF,
            display_name="小钱（合成演示用户）",
            timezone="Asia/Shanghai",
            is_simulated=True,
            created_at=SEED_AS_OF,
        )
    )
    session.flush()
    accounts: dict[str, Account] = {}
    for key, kind, name in [
        ("cash", "CASH", "工行模拟活期账户"),
        ("goal", "GOAL", "目标储备（尚未配置策略）"),
        ("card", "CREDIT_CARD", "工行模拟信用卡"),
        ("management", "CASH_MANAGEMENT", "模拟现金管理持仓账户"),
        ("fixed", "FIXED_DEPOSIT", "模拟定存持仓账户"),
    ]:
        account = Account(
            id=_id(f"account:{key}"),
            user_id=DEMO_USER_ID,
            created_at=SEED_AS_OF,
            external_ref=f"{SEED_VERSION}:account:{key}",
            name=name,
            account_type=kind,
            balance_cents=0,
            observed_at=SEED_AS_OF,
        )
        accounts[key] = account
        session.add(account)
    session.flush()
    for entry in _ledger():
        account = accounts[entry.account]
        account.balance_cents += (
            entry.amount_cents if entry.direction == "CREDIT" else -entry.amount_cents
        )
        if account.balance_cents < 0:
            raise ValueError("Synthetic ledger overdraws an account")
        transaction_id = _id(f"transaction:{entry.key}")
        bank_fact = _evidence(
            f"transaction:{entry.key}",
            "SIMULATED_BANK_TRANSACTION",
            {
                "simulation": True,
                "transaction_id": str(transaction_id),
                "account_id": str(account.id),
                "direction": entry.direction,
                "amount_cents": entry.amount_cents,
                "balance_after_cents": account.balance_cents,
                "occurred_at": entry.occurred_at.isoformat(),
                "counterparty_ref": entry.counterparty_ref,
            },
            entry.occurred_at,
        )
        session.add(bank_fact)
        confirmed = entry.category in ESSENTIAL_CATEGORIES
        if confirmed:
            session.add(
                _evidence(
                    f"category-confirmation:{entry.key}",
                    "SIMULATED_USER_CATEGORY_CONFIRMATION",
                    {
                        "simulation": True,
                        "transaction_id": str(transaction_id),
                        "category": entry.category,
                        "confirmed": True,
                        "actor": "synthetic_user",
                        "basis": "explicit_demo_category_selection",
                    },
                    entry.occurred_at + timedelta(minutes=1),
                    "USER_DECLARED",
                )
            )
        session.flush()
        session.add(
            Transaction(
                id=transaction_id,
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                account_id=account.id,
                evidence_id=bank_fact.id,
                source_ref=f"{SEED_VERSION}:{entry.key}",
                direction=entry.direction,
                amount_cents=entry.amount_cents,
                balance_after_cents=account.balance_cents,
                category=entry.category,
                counterparty_ref=entry.counterparty_ref,
                is_one_off=entry.is_one_off,
                category_confirmed=confirmed,
                occurred_at=entry.occurred_at,
                observed_at=entry.occurred_at,
            )
        )
    session.flush()
    for key, account in accounts.items():
        session.add(
            _evidence(
                f"account:{key}",
                "SIMULATED_BANK_BALANCE",
                {
                    "simulation": True,
                    "account_id": str(account.id),
                    "balance_cents": account.balance_cents,
                    "currency": "CNY",
                    "as_of": SEED_AS_OF.isoformat(),
                },
                SEED_AS_OF,
            )
        )
    for month, total, paid in [(8, 120_000, 120_000), (9, 135_000, 135_000), (10, 145_000, 0)]:
        statement_day = date(2026, month, 1) if month < 10 else date(2026, 9, 30)
        evidence = _evidence(
            f"bill:{month}",
            "SIMULATED_CREDIT_CARD_BILL",
            {
                "simulation": True,
                "total_cents": total,
                "paid_cents": paid,
                "statement_date": statement_day.isoformat(),
                "due_date": date(2026, month, 20).isoformat(),
                "status": "PAID" if paid else "UNPAID",
            },
            SEED_AS_OF,
        )
        session.add(evidence)
        session.flush()
        session.add(
            CreditCardBill(
                id=_id(f"bill:{month}"),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                account_id=accounts["card"].id,
                evidence_id=evidence.id,
                source_ref=f"{SEED_VERSION}:bill:{month}",
                statement_date=statement_day,
                due_date=date(2026, month, 20),
                total_cents=total,
                minimum_due_cents=total // 10,
                paid_cents=paid,
                status="PAID" if paid else "UNPAID",
            )
        )
    for key, amount, day in [
        ("t0", 250_000, date(2026, 9, 12)),
        ("t1", 150_000, date(2026, 9, 13)),
        ("fixed", 100_000, date(2026, 9, 25)),
    ]:
        purchased_at = _at(day, 11)
        # No redemption has happened: available_at remains unknown, including T+0.
        session.add(
            AssetPosition(
                id=_id(f"position:{key}"),
                user_id=DEMO_USER_ID,
                created_at=SEED_AS_OF,
                account_id=accounts["fixed" if key == "fixed" else "management"].id,
                product_id=_id(f"product:{key}"),
                principal_cents=amount,
                accrued_yield_cents=0,
                purchased_at=purchased_at,
                maturity_at=purchased_at + timedelta(days=30) if key == "fixed" else None,
                available_at=None,
                status="HELD",
            )
        )
        session.add(
            _evidence(
                f"position:{key}",
                "SIMULATED_BANK_POSITION",
                {
                    "simulation": True,
                    "position_id": str(_id(f"position:{key}")),
                    "product_id": str(_id(f"product:{key}")),
                    "principal_cents": amount,
                    "purchase_transaction_id": str(_id(f"transaction:{day}:purchase:{key}")),
                    "acquisition": "synthetic_user_manual_purchase",
                    "status": "HELD",
                },
                purchased_at,
            )
        )
    session.flush()


def _summary(session: Session) -> SeedSummary:
    dataset: dict[str, list[dict[str, Any]]] = {}
    product_ids = [values["id"] for values in _product_values()]
    for table in Base.metadata.sorted_tables:
        if table.name == "users":
            predicate = table.c.id == DEMO_USER_ID
        elif table.name == "asset_products":
            predicate = table.c.id.in_(product_ids)
        else:
            predicate = table.c.user_id == DEMO_USER_ID
        dataset[table.name] = [
            dict(row)
            for row in session.execute(
                select(table).where(predicate).order_by(table.c.id)
            ).mappings()
        ]
    cash = sum(row["balance_cents"] for row in dataset["accounts"])
    principal = sum(row["principal_cents"] for row in dataset["asset_positions"])
    yield_cents = sum(row["accrued_yield_cents"] for row in dataset["asset_positions"])
    unpaid = sum(row["total_cents"] - row["paid_cents"] for row in dataset["credit_card_bills"])
    return SeedSummary(
        seed_version=SEED_VERSION,
        user_id=DEMO_USER_ID,
        as_of=SEED_AS_OF,
        period_start=SEED_START,
        period_end=SEED_END,
        days=60,
        counts={table: len(rows) for table, rows in dataset.items()},
        cash_balance_cents=cash,
        asset_principal_cents=principal,
        asset_yield_cents=yield_cents,
        total_asset_cents=cash + principal + yield_cents,
        credit_card_unpaid_cents=unpaid,
        dataset_sha256=_hash(dataset),
    )


def seed_demo(engine: Engine) -> SeedSummary:
    """Atomically replace demo facts in an already migrated PostgreSQL database."""
    if engine.dialect.name != "postgresql":
        raise ValueError("Demo seeding requires PostgreSQL")
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 0x424644454D4F})
        _check_identity(session)
        _ensure_products(session)
        _clear_demo(session)
        _insert_facts(session)
        return _summary(session)
