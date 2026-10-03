"""Account facts for the local demonstration. No inferred spending authority."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.api.dependencies import DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.db.models import Account, AssetPosition, AssetProduct, CreditCardBill, Transaction
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

router = APIRouter(
    prefix="/api/v1", tags=["模拟账户事实"], responses={404: {"model": ErrorEnvelope}}
)


class FactModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountView(FactModel):
    id: UUID
    external_ref: str
    name: str
    account_type: str
    bank_code: str
    currency: Literal["CNY"]
    balance_cents: int
    observed_at: datetime


class BillView(FactModel):
    id: UUID
    account_id: UUID
    source_ref: str
    evidence_id: UUID | None
    statement_date: date
    due_date: date
    total_cents: int
    minimum_due_cents: int
    paid_cents: int
    status: str


class AccountSummary(BaseModel):
    simulation: Literal[True] = True
    user_id: UUID
    timezone: str
    oldest_account_observed_at: datetime | None
    latest_account_observed_at: datetime | None
    accounts: list[AccountView]
    credit_card_bills: list[BillView]
    cash_balance_cents: int
    position_principal_cents: int
    unknown_position_principal_cents: int
    credit_card_unpaid_cents: int


class TransactionView(FactModel):
    id: UUID
    account_id: UUID
    source_ref: str
    evidence_id: UUID | None
    direction: Literal["CREDIT", "DEBIT"]
    amount_cents: int
    balance_after_cents: int | None
    category: str
    counterparty_ref: str | None
    is_one_off: bool
    category_confirmed: bool
    occurred_at: datetime
    observed_at: datetime


class TransactionPage(BaseModel):
    simulation: Literal[True] = True
    items: list[TransactionView]
    total: int
    limit: int
    offset: int


class ProductView(FactModel):
    id: UUID
    product_code: str
    version_number: int
    name: str
    asset_class: str
    risk_level: int
    principal_fluctuation: bool
    minimum_purchase_cents: int
    lock_days: int
    redemption_delay_days: int
    annual_yield_bps: int
    early_withdrawal_loss_bps: int
    maturity_rule: dict[str, Any]
    early_withdrawal_rule: dict[str, Any]
    auto_purchase_allowed: bool
    auto_redeem_allowed: bool
    effective_from: datetime
    effective_until: datetime | None


class ProductList(BaseModel):
    simulation: Literal[True] = True
    items: list[ProductView]


class PositionView(FactModel):
    id: UUID
    account_id: UUID
    product_id: UUID
    goal_id: UUID | None
    policy_version_id: UUID | None
    principal_cents: int
    accrued_yield_cents: int
    purchased_at: datetime
    maturity_at: datetime | None
    available_at: datetime | None
    status: str


class PositionList(BaseModel):
    simulation: Literal[True] = True
    items: list[PositionView]


@router.get("/accounts/summary", response_model=AccountSummary, operation_id="account_summary")
def account_summary(session: SessionDependency, user: DemoUserDependency) -> AccountSummary:
    accounts = list(
        session.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.id))
    )
    bills = list(
        session.scalars(
            select(CreditCardBill)
            .where(CreditCardBill.user_id == user.id)
            .order_by(CreditCardBill.due_date, CreditCardBill.id)
        )
    )
    positions = list(session.scalars(select(AssetPosition).where(AssetPosition.user_id == user.id)))
    return AccountSummary(
        user_id=user.id,
        timezone=user.timezone,
        oldest_account_observed_at=min((account.observed_at for account in accounts), default=None),
        latest_account_observed_at=max((account.observed_at for account in accounts), default=None),
        accounts=[AccountView.model_validate(account) for account in accounts],
        credit_card_bills=[BillView.model_validate(bill) for bill in bills],
        cash_balance_cents=sum(
            account.balance_cents for account in accounts if account.account_type != "CREDIT_CARD"
        ),
        position_principal_cents=sum(
            position.principal_cents
            for position in positions
            if position.status in {"HELD", "REDEEMING", "MATURED"}
        ),
        unknown_position_principal_cents=sum(
            position.principal_cents for position in positions if position.status == "UNKNOWN"
        ),
        credit_card_unpaid_cents=sum(bill.total_cents - bill.paid_cents for bill in bills),
    )


@router.get("/transactions", response_model=TransactionPage, operation_id="list_transactions")
def list_transactions(
    session: SessionDependency,
    user: DemoUserDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    account_id: UUID | None = None,
    category: Annotated[str | None, Query(min_length=1, max_length=48)] = None,
) -> TransactionPage:
    query = select(Transaction).where(Transaction.user_id == user.id)
    if account_id is not None:
        account = session.get(Account, account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status_code=404)
        query = query.where(Transaction.account_id == account_id)
    if category is not None:
        query = query.where(Transaction.category == category)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    records = session.scalars(
        query.order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return TransactionPage(
        items=[TransactionView.model_validate(row) for row in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/products", response_model=ProductList, operation_id="list_products")
def list_products(session: SessionDependency, user: DemoUserDependency) -> ProductList:
    records = session.scalars(
        select(AssetProduct).order_by(AssetProduct.product_code, AssetProduct.version_number)
    )
    return ProductList(items=[ProductView.model_validate(row) for row in records])


@router.get("/positions", response_model=PositionList, operation_id="list_positions")
def list_positions(session: SessionDependency, user: DemoUserDependency) -> PositionList:
    records = session.scalars(
        select(AssetPosition)
        .where(AssetPosition.user_id == user.id)
        .order_by(AssetPosition.purchased_at, AssetPosition.id)
    )
    return PositionList(items=[PositionView.model_validate(row) for row in records])
