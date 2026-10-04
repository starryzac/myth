"""Deterministic synthetic facts. Reset only the reserved demo tenant in one transaction."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid4, uuid5

from app.db.audit_guard import DEMO_GATE_KEY
from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    ActionResourceReservation,
    AssetPosition,
    AssetProduct,
    AuditEvent,
    BankOperation,
    CreditCardBill,
    DecisionConstraint,
    DecisionRun,
    EvidenceItem,
    ExternalBankFact,
    Goal,
    Policy,
    PolicyProposal,
    PolicyVersion,
    SimulatedBankPosting,
    SimulatedBankRedemption,
    Transaction,
    User,
)
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.audit_chain import parse_event
from app.domain.audit_chain_types import AuditEnvelope
from app.domain.demo_identity import DEMO_USER_ID as DEMO_USER_ID
from app.domain.demo_identity import DEMO_USER_REF as DEMO_USER_REF
from app.domain.history_coverage import COVERAGE_SOURCE_TYPE, build_history_coverage
from app.services.audit_chain import (
    SUBJECT_MODELS,
    can_continue_audit,
    current_audit_epoch,
    ensure_audit_epoch,
    has_business_history,
    reset_archive_epoch,
    start_seed_epoch,
    verify_audit_chain,
)
from app.services.simulated_bank import open_simulated_bank
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.domain.income_ledger import IncomeLedger

SEED_VERSION = "mvp-301-v6"
SUMMARY_VERSION = "seed-summary-v3"
SEED_START = date(2026, 8, 5)
SEED_END = date(2026, 10, 3)
SEED_AS_OF = datetime(2026, 10, 3, 16, tzinfo=UTC)
PRODUCT_CATALOG_CREATED_AT = datetime(2026, 10, 3, 15, 59, 59, tzinfo=UTC)
SEED_NAMESPACE = UUID("8758cb2f-60ec-5703-a3df-1b64ca068e12")
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
ESSENTIAL_CATEGORIES = frozenset({"food", "transport", "daily_necessities"})


class SeedConflictError(ValueError):
    """A reserved identity or immutable product is occupied by different data."""


class SeedSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    summary_version: Literal["seed-summary-v2", "seed-summary-v3"] = "seed-summary-v3"
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

    @property
    def economic_role(self) -> str:
        """Importer-owned role from the fixed generator, never from an editable database row."""
        return {
            "opening_balance": "OPENING",
            "salary": "INCOME",
            "internal_transfer": "INTERNAL_TRANSFER",
            "asset_purchase": "ASSET_PURCHASE",
            "credit_card_payment": "CREDIT_CARD_PAYMENT",
        }.get(self.category, "CONSUMPTION")


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
                "created_at": PRODUCT_CATALOG_CREATED_AT,
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
    # Existing v1 records are shared by historical positions and never rewritten.
    for original in list(result):
        fixed = original["asset_class"] == "FIXED_DEPOSIT"
        terms = {
            "protocol": "fixed-principal-return-v1" if fixed else "planned-principal-return-v1",
            "kind": "RETURN_TO_CASH" if fixed else "REDEEM_ON_REQUEST",
            "day_basis": "CALENDAR",
            "guaranteed": True,
            "settlement_delay_days": original["redemption_delay_days"],
            "principal_return_bps": 10000,
            "rollover": False,
            "auto_rollover": False,
            "yield_rule": {
                "protocol": "simple-annual-yield-v1",
                "basis": "ACT_365",
                "annual_yield_bps": original["annual_yield_bps"],
                "simulation": True,
                "fee_cents": 0,
                "purchase_fee_bps": 0,
                "redemption_fee_bps": 0,
                "accrual": "UNTIL_MATURITY" if fixed else "UNTIL_REDEMPTION_REQUEST",
            },
        }
        if fixed:
            terms["term_days"] = original["lock_days"]
        result.append(
            {
                **original,
                "id": _id(f"product:{original['product_code']}:v2"),
                "version_number": 2,
                "created_at": SEED_AS_OF,
                "effective_from": SEED_AS_OF,
                "maturity_rule": terms,
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
    # The original graph has already been sealed; retained audit objects are untouched.
    session.execute(
        update(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID).values(supersedes_id=None)
    )
    session.execute(
        update(DecisionRun)
        .where(DecisionRun.user_id == DEMO_USER_ID)
        .values(parent_run_id=None, subject_action_plan_id=None)
    )
    for model in [
        SimulatedBankPosting,
        ExternalBankFact,
        BankOperation,
        SimulatedBankRedemption,
        ActionResourceReservation,
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
    if session.get(User, DEMO_USER_ID) is None:
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
                "economic_role": entry.economic_role,
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
    session.add(
        _evidence(
            "transaction-history-coverage",
            COVERAGE_SOURCE_TYPE,
            build_history_coverage(
                DEMO_USER_ID,
                "Asia/Shanghai",
                SEED_START,
                SEED_END,
                [account.id for account in accounts.values()],
                session.scalars(select(Transaction).where(Transaction.user_id == DEMO_USER_ID)),
                {
                    item.id: item
                    for item in session.scalars(
                        select(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID)
                    )
                },
            ),
            SEED_AS_OF,
        )
    )
    for key, account in accounts.items():
        session.add(
            _evidence(
                f"account:{key}",
                "SIMULATED_BANK_BALANCE",
                {
                    "simulation": True,
                    "user_id": str(DEMO_USER_ID),
                    "account_id": str(account.id),
                    "account_type": account.account_type,
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
                "user_id": str(DEMO_USER_ID),
                "bill_id": str(_id(f"bill:{month}")),
                "account_id": str(accounts["card"].id),
                "source_ref": f"{SEED_VERSION}:bill:{month}",
                "total_cents": total,
                "minimum_due_cents": total // 10,
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
                    "user_id": str(DEMO_USER_ID),
                    "position_id": str(_id(f"position:{key}")),
                    "account_id": str(accounts["fixed" if key == "fixed" else "management"].id),
                    "product_id": str(_id(f"product:{key}")),
                    "goal_id": None,
                    "policy_version_id": None,
                    "principal_cents": amount,
                    "purchased_at": purchased_at.isoformat(),
                    "maturity_at": (purchased_at + timedelta(days=30)).isoformat()
                    if key == "fixed"
                    else None,
                    "available_at": None,
                    "as_of": SEED_AS_OF.isoformat(),
                    "purchase_transaction_id": str(_id(f"transaction:{day}:purchase:{key}")),
                    "acquisition": "synthetic_user_manual_purchase",
                    "status": "HELD",
                },
                SEED_AS_OF,
            )
        )
    session.flush()


def _summary(session: Session) -> SeedSummary:
    dataset: dict[str, list[dict[str, Any]]] = {}
    product_ids = [values["id"] for values in _product_values()]
    for table in sorted(
        (model.__table__ for model in SUBJECT_MODELS.values()), key=lambda t: t.name
    ):
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


def _open_seed_bank(session: Session) -> None:
    from app.services.execution_bank import open_execution_anchors
    from app.services.external_bank_facts import open_external_clearing

    # Independent openings are reconstructed from the fixed synthetic seed
    # instructions, never copied from mutable application account projections.
    balances = {key: 0 for key in ("cash", "goal", "management", "fixed")}
    for entry in _ledger():
        if entry.account in balances:
            balances[entry.account] += (
                entry.amount_cents if entry.direction == "CREDIT" else -entry.amount_cents
            )
    open_simulated_bank(
        session,
        DEMO_USER_ID,
        SEED_AS_OF,
        cash_balances={_id(f"account:{key}"): amount for key, amount in balances.items()},
        position_principals={
            _id("position:t0"): 250000,
            _id("position:t1"): 150000,
            _id("position:fixed"): 100000,
        },
    )
    open_external_clearing(
        session,
        DEMO_USER_ID,
        SEED_AS_OF,
        counterparty_reserves={"payroll": 100000000, "merchant": 0},
    )
    income = _seed_income_ledger(session)
    open_execution_anchors(session, DEMO_USER_ID, SEED_AS_OF, income_ledger=income)


def _seed_income_ledger(session: Session) -> "IncomeLedger":
    """Replay fixed bank instructions FIFO; SPENT means exited current CASH availability."""
    from app.domain.history_coverage import bank_fact_snapshot
    from app.domain.income_ledger import (
        LEDGER_SOURCE,
        IncomeFragment,
        IncomeLedger,
        IncomeOrigin,
        location_id,
    )

    if current_audit_epoch(session, DEMO_USER_ID) is not None:
        raise SeedConflictError("Historical income import is restricted to trusted seed genesis")
    transactions = {
        row.id: row
        for row in session.scalars(select(Transaction).where(Transaction.user_id == DEMO_USER_ID))
    }
    proofs = {
        row.id: row
        for row in session.scalars(select(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID))
    }
    cash_id = _id("account:cash")
    origins: list[IncomeOrigin] = []
    available: dict[UUID, int] = {}
    debits: list[dict[str, Any]] = []
    untracked = 0
    source: list[dict[str, Any]] = []
    known_debits = {
        "food",
        "transport",
        "daily_necessities",
        "rent",
        "utilities",
        "one_off_purchase",
        "internal_transfer",
        "asset_purchase",
        "credit_card_payment",
    }
    for instruction in _ledger():
        if instruction.account != "cash":
            continue
        transaction = transactions.get(_id(f"transaction:{instruction.key}"))
        proof = (
            proofs.get(transaction.evidence_id)
            if transaction is not None and transaction.evidence_id is not None
            else None
        )
        if (
            transaction is None
            or proof is None
            or (
                transaction.account_id != cash_id
                or transaction.amount_cents != instruction.amount_cents
                or transaction.direction != instruction.direction
                or transaction.occurred_at != instruction.occurred_at
                or transaction.counterparty_ref != instruction.counterparty_ref
                or proof.content.get("economic_role") != instruction.economic_role
            )
        ):
            raise SeedConflictError(
                "Trusted income instructions differ from their actual bank originals"
            )
        bank_fact_snapshot(transaction, proof)
        source.append(
            {
                "key": instruction.key,
                "transaction_id": str(transaction.id),
                "evidence_id": str(proof.id),
                "evidence_hash": proof.content_hash,
                "direction": instruction.direction,
                "amount_cents": instruction.amount_cents,
                "occurred_at": instruction.occurred_at.isoformat(),
                "economic_role": instruction.economic_role,
            }
        )
        if instruction.direction == "CREDIT":
            if instruction.economic_role == "INCOME":
                origins.append(
                    IncomeOrigin(
                        origin_transaction_id=transaction.id,
                        origin_account_id=cash_id,
                        amount_cents=instruction.amount_cents,
                        occurred_at=transaction.occurred_at,
                        observed_at=transaction.observed_at,
                        bank_evidence_id=proof.id,
                        bank_evidence_hash=proof.content_hash,
                    )
                )
                available[transaction.id] = instruction.amount_cents
            elif instruction.category == "opening_balance":
                untracked += instruction.amount_cents
            else:
                raise SeedConflictError("Unknown trusted CASH credit source")
            continue
        if instruction.category not in known_debits:
            raise SeedConflictError("Unknown trusted CASH debit attribution")
        remaining = instruction.amount_cents
        uses = []
        for origin in origins:
            consumed = min(remaining, available[origin.origin_transaction_id])
            if consumed:
                available[origin.origin_transaction_id] -= consumed
                uses.append(
                    {
                        "origin_transaction_id": str(origin.origin_transaction_id),
                        "amount_cents": consumed,
                    }
                )
                remaining -= consumed
            if not remaining:
                break
        untracked -= remaining
        if untracked < 0:
            raise SeedConflictError(
                "Historical CASH debit lacks actual prior income or opening capital"
            )
        debits.append(
            {
                "transaction_id": str(transaction.id),
                "economic_role": instruction.economic_role,
                "income_uses": uses,
                "untracked_spent_cents": remaining,
            }
        )
    ledger = IncomeLedger(
        user_id=DEMO_USER_ID,
        as_of=SEED_AS_OF,
        scope_account_ids=(cash_id,),
        origins=tuple(origins),
        fragments=tuple(
            IncomeFragment(
                fragment_id=location_id(origin.origin_transaction_id, cash_id),
                origin_transaction_id=origin.origin_transaction_id,
                account_id=cash_id,
                spent_cents=origin.amount_cents - available[origin.origin_transaction_id],
                available_cents=available[origin.origin_transaction_id],
            )
            for origin in origins
        ),
    )
    content = ledger.model_dump(mode="json")
    session.add(_evidence("seed-income-ledger", LEDGER_SOURCE, content, SEED_AS_OF))
    session.add(
        _evidence(
            "seed-income-fifo-import",
            "SIMULATED_SEED_INCOME_IMPORT",
            {
                "simulation": True,
                "user_id": str(DEMO_USER_ID),
                "protocol_version": "seed-income-cash-fifo-v1",
                "source_digest": _hash(source),
                "sources": source,
                "debits": debits,
                "ledger_content_hash": _hash(content),
                "untracked_remaining_cents": untracked,
                "spent_semantics": "EXITED_CURRENT_CASH_AVAILABLE_SCOPE",
                "ordering": "occurred_at_then_stable_instruction_key",
            },
            SEED_AS_OF,
        )
    )
    session.flush()
    return ledger


def seed_demo(
    engine: Engine,
    *,
    reset_key: str | None = None,
    reason: str = "SYNTHETIC_DEMO_RESET",
    principal: str = "demo-seed",
) -> SeedSummary:
    """Atomically replace demo facts in an already migrated PostgreSQL database."""
    if engine.dialect.name != "postgresql":
        raise ValueError("Demo seeding requires PostgreSQL")
    if any(
        type(value) is not str or not 1 <= len(value) <= 160 for value in [reason, principal]
    ) or (reset_key is not None and (type(reset_key) is not str or not 1 <= len(reset_key) <= 160)):
        raise SeedConflictError("reset_key, reason and principal must be nonempty bounded strings")
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DEMO_GATE_KEY})
        _check_identity(session)
        _ensure_products(session)
        if reset_key is not None:
            replay = session.scalar(
                select(AuditEvent).where(
                    AuditEvent.user_id == DEMO_USER_ID,
                    AuditEvent.event_type == "EPOCH_STARTED",
                    AuditEvent.payload["epoch_transition"]["reset_key"].astext == reset_key,
                )
            )
            if replay is not None:
                original = parse_event(replay.canonical_text or "")
                transition = cast(AuditEnvelope, original).payload.epoch_transition
                if (
                    transition is None
                    or transition.reason != reason
                    or transition.principal != principal
                ):
                    raise SeedConflictError("reset_key cannot change original reset intent")
                if not can_continue_audit(
                    verify_audit_chain(session, DEMO_USER_ID, replay.epoch_id)
                ):
                    raise SeedConflictError("reset_key original epoch cannot pass verification")
                return SeedSummary.model_validate_json(
                    json.dumps(original.payload.context.details.get("seed_summary"))
                )
        previous = current_audit_epoch(session, DEMO_USER_ID)
        if previous is None and has_business_history(session, DEMO_USER_ID):
            # First 304 reset archives surviving pre-activation facts before clearing them.
            previous = ensure_audit_epoch(session, DEMO_USER_ID, datetime.now(UTC))
        key = reset_key or str(uuid4())
        if previous is not None:
            reset_archive_epoch(session, previous, key, reason, principal)
        _clear_demo(session)
        _insert_facts(session)
        _open_seed_bank(session)
        session.add(
            _evidence(
                "asset-exposure",
                EXPOSURE_SOURCE,
                asset_exposure_snapshot(
                    DEMO_USER_ID,
                    SEED_AS_OF,
                    accounts=session.scalars(
                        select(Account).where(Account.user_id == DEMO_USER_ID)
                    ),
                    positions=session.scalars(
                        select(AssetPosition).where(AssetPosition.user_id == DEMO_USER_ID)
                    ),
                    actions=[],
                    receipts=[],
                    evidence=session.scalars(
                        select(EvidenceItem).where(EvidenceItem.user_id == DEMO_USER_ID)
                    ),
                    settlements=[],
                    bank_requests=session.scalars(
                        select(SimulatedBankRedemption).where(
                            SimulatedBankRedemption.user_id == DEMO_USER_ID
                        )
                    ),
                    bank_postings=session.scalars(
                        select(SimulatedBankPosting).where(
                            SimulatedBankPosting.user_id == DEMO_USER_ID
                        )
                    ),
                    bank_operations=session.scalars(
                        select(BankOperation).where(BankOperation.user_id == DEMO_USER_ID)
                    ),
                    resource_reservations=session.scalars(
                        select(ActionResourceReservation).where(
                            ActionResourceReservation.user_id == DEMO_USER_ID
                        )
                    ),
                    external_bank_facts=session.scalars(
                        select(ExternalBankFact).where(ExternalBankFact.user_id == DEMO_USER_ID)
                    ),
                ),
                SEED_AS_OF,
            )
        )
        session.flush()
        summary = _summary(session)
        start_seed_epoch(
            session,
            DEMO_USER_ID,
            key,
            reason,
            principal,
            SEED_VERSION,
            SUMMARY_VERSION,
            summary.dataset_sha256,
            previous,
            summary.model_dump(mode="json"),
        )
        return summary
