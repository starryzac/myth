"""Resolve small intents into exact simulated effects using current server facts."""

import json
from datetime import datetime, timedelta
from typing import Any, TypedDict
from uuid import UUID, uuid5

from app.db.models import Account, AssetPosition, AssetProduct, Goal, PolicyVersion
from app.domain.execution_types import BillReference, CashUse, ExecutionEffect, OccurrenceReference
from app.domain.income_ledger import IncomeUse
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ActionIntent
from app.services.asset_allocation import preview_asset_allocation
from app.services.asset_exposure_import import load_asset_exposure
from app.services.boundary import load_boundary_context
from app.services.execution_sources import (
    execution_return_account,
    load_execution_quote,
    resolve_payee_binding,
)
from app.services.goal_allocation import preview_goal_allocation
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from sqlalchemy import select
from sqlalchemy.orm import Session


class EffectIdentity(TypedDict):
    operation_id: UUID
    user_id: UUID
    valid_from: datetime
    expires_at: datetime


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("EXECUTION_NOT_READY", message, 409)


def _current(session: Session, user_id: UUID, policy_id: UUID, now: datetime) -> PolicyVersion:
    row = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == policy_id)
        .order_by(PolicyVersion.version_number.desc())
    )
    if row is None or not is_version_authorized(session, user_id, row.id, now):
        raise _error("需要当前有效、已明确确认的策略")
    return row


def funding_income(
    session: Session, user_id: UUID, uses: list[CashUse], now: datetime
) -> list[IncomeUse]:
    try:
        state = read_income_state(session, user_id, now)
    except PolicyLifecycleError as error:
        if error.code == "MISSING_NEW_FUNDS_LEDGER":
            return []
        raise
    context = load_boundary_context(session, user_id, now)
    exposure = load_asset_exposure(session, context, {"scope": "general_idle_funds"})
    if context.sources.issues:
        raise _error("资金来源或占用证明不完整")
    accounts = {a.account_id: a for a in context.snapshot.cash_accounts}
    origins = {o.origin_transaction_id: o for o in state.ledger.origins}
    result = []
    for use in uses:
        if use.account_id not in accounts:
            raise _error("资金账户不存在")
        fragments = [f for f in state.ledger.fragments if f.account_id == use.account_id]
        available = sum(f.available_cents for f in fragments)
        owned = sum(
            g.cash_owned_cents for g in context.snapshot.goals if g.account_id == use.account_id
        )
        occupied = exposure.reserved_cash_by_account.get(use.account_id, 0)
        legacy = sum(f.legacy_reserved_cents for f in fragments)
        old = accounts[use.account_id].balance_cents - owned - occupied - legacy - available
        if old < 0 or use.amount_cents > old + available:
            raise _error("资金效果会消耗已占用收入或使来源失去现金支持")
        needed = max(0, use.amount_cents - old)
        for fragment in sorted(
            fragments,
            key=lambda f: (origins[f.origin_transaction_id].occurred_at, f.origin_transaction_id),
        ):
            amount = min(needed, fragment.available_cents)
            if amount:
                result.append(
                    IncomeUse(
                        fragment_id=fragment.fragment_id,
                        origin_transaction_id=fragment.origin_transaction_id,
                        account_id=use.account_id,
                        amount_cents=amount,
                    )
                )
                needed -= amount
        if needed:
            raise _error("可用收入来源不足")
    return result


def _cash_funding(session: Session, user_id: UUID, amount: int, now: datetime) -> list[CashUse]:
    context = load_boundary_context(session, user_id, now)
    exposure = load_asset_exposure(session, context, {"scope": "general_idle_funds"})
    if context.sources.issues:
        raise _error("支付资金来源或占用证明不完整")
    remaining, uses = amount, []
    for account in sorted(context.snapshot.cash_accounts, key=lambda a: a.account_id):
        if account.account_type != "CASH":
            continue
        owned = sum(
            g.cash_owned_cents for g in context.snapshot.goals if g.account_id == account.account_id
        )
        capacity = max(
            0,
            account.balance_cents
            - owned
            - exposure.reserved_cash_by_account.get(account.account_id, 0),
        )
        take = min(remaining, capacity)
        if take:
            uses.append(CashUse(account_id=account.account_id, amount_cents=take))
            remaining -= take
    if remaining:
        raise _error("可用一般现金不足以支付明确义务")
    return uses


def plan_execution_effect(
    session: Session,
    user_id: UUID,
    action_id: UUID,
    intent: ActionIntent,
    now: datetime,
    *,
    _experiment_candidate: dict[str, Any] | None = None,
) -> ExecutionEffect:
    if _experiment_candidate is not None and intent.kind != "purchase_asset":
        raise PolicyLifecycleError("EXPERIMENT_NOT_IMPLEMENTED", "候选替换仅支持一般资金申购", 409)
    common: EffectIdentity = dict(
        operation_id=action_id,
        user_id=user_id,
        valid_from=now,
        expires_at=now + timedelta(minutes=15),
    )
    if intent.kind == "transfer_internal":
        cash = [CashUse(account_id=intent.source_account_id, amount_cents=intent.amount_cents)]
        return ExecutionEffect(
            **common,
            business_key="transfer:" + str(action_id),
            action_type="TRANSFER_INTERNAL",
            amount_cents=intent.amount_cents,
            cash_uses=cash,
            income_uses=funding_income(session, user_id, cash, now),
            destination_account_id=intent.destination_account_id,
        )
    if intent.kind == "allocate_goal":
        preview = preview_goal_allocation(session, user_id, intent.goal_id, now)
        result = preview.allocation
        if result.status != "READY" or not result.suggested_cents:
            raise _error("目标储备尚不可执行：" + ",".join(result.reasons))
        goal = session.get(Goal, intent.goal_id)
        assert goal is not None
        amounts: dict[UUID, int] = {}
        for use in result.lot_allocations:
            amounts[use.account_id] = amounts.get(use.account_id, 0) + use.amount_cents
        return ExecutionEffect(
            **common,
            business_key="goal:" + str(action_id),
            action_type="ALLOCATE_GOAL",
            amount_cents=result.suggested_cents,
            cash_uses=[CashUse(account_id=k, amount_cents=v) for k, v in sorted(amounts.items())],
            income_uses=[
                IncomeUse(
                    fragment_id=u.fragment_id,
                    origin_transaction_id=u.origin_transaction_id,
                    account_id=u.account_id,
                    amount_cents=u.amount_cents,
                )
                for u in result.lot_allocations
            ],
            destination_account_id=goal.account_id,
            goal_id=goal.id,
            policy_id=goal.policy_id,
            policy_version_id=goal.policy_version_id,
            policy_version_ids=[goal.policy_version_id],
        )
    if intent.kind == "purchase_asset":
        asset = preview_asset_allocation(session, user_id, intent.policy_id, now).allocation
        if (
            asset.status != "READY"
            or not asset.suggested_cents
            or asset.selected_product_id is None
        ):
            raise _error("资产申购尚不可执行：" + ",".join(asset.reasons))
        product = session.get(AssetProduct, asset.selected_product_id)
        assert product is not None
        candidate = next(c for c in asset.candidates if c.product_id == product.id)
        assert candidate.exit_plan is not None
        cash = [
            CashUse(account_id=u.account_id, amount_cents=u.amount_cents)
            for u in asset.source_cash_uses
        ]
        amount = asset.suggested_cents
        if _experiment_candidate is not None:
            if asset.goal_id is not None:
                raise PolicyLifecycleError(
                    "EXPERIMENT_NOT_IMPLEMENTED", "目标收入份额替换尚未实现", 409
                )
            if (
                set(_experiment_candidate) != {"amount_cents", "cash_uses"}
                or type(_experiment_candidate["amount_cents"]) is not int
                or not 0 < _experiment_candidate["amount_cents"] <= 9_223_372_036_854_775_807
                or not isinstance(_experiment_candidate["cash_uses"], list)
                or not 0 < len(_experiment_candidate["cash_uses"]) <= 100
            ):
                raise PolicyLifecycleError(
                    "EXPERIMENT_CANDIDATE_INVALID", "候选必须是严格金额及现金来源", 409
                )
            raw_uses = _experiment_candidate["cash_uses"]
            if any(
                not isinstance(use, dict)
                or set(use) != {"account_id", "amount_cents"}
                or type(use["amount_cents"]) is not int
                for use in raw_uses
            ):
                raise PolicyLifecycleError("EXPERIMENT_CANDIDATE_INVALID", "现金来源类型不符", 409)
            try:
                cash = [CashUse.model_validate_json(json.dumps(use)) for use in raw_uses]
            except ValueError as error:
                raise PolicyLifecycleError(
                    "EXPERIMENT_CANDIDATE_INVALID", "现金来源无效", 409
                ) from error
            if (
                len({use.account_id for use in cash}) != len(cash)
                or sum(use.amount_cents for use in cash) != _experiment_candidate["amount_cents"]
            ):
                raise PolicyLifecycleError(
                    "EXPERIMENT_CANDIDATE_INVALID", "现金来源必须唯一且守恒", 409
                )
            for cash_use in cash:
                account = session.get(Account, cash_use.account_id)
                if (
                    account is None
                    or account.user_id != user_id
                    or account.account_type != "CASH"
                    or account.currency != "CNY"
                ):
                    raise PolicyLifecycleError(
                        "EXPERIMENT_CANDIDATE_INVALID", "现金来源归属或类型不符", 409
                    )
            amount = _experiment_candidate["amount_cents"]
        position_account = session.scalar(
            select(Account)
            .where(
                Account.user_id == user_id,
                Account.account_type
                == (
                    "FIXED_DEPOSIT" if product.asset_class == "FIXED_DEPOSIT" else "CASH_MANAGEMENT"
                ),
            )
            .order_by(Account.id)
        )
        if position_account is None:
            raise _error("缺少所属模拟持仓账户")
        versions = [asset.policy_version_id]
        if asset.goal_id is not None:
            goal = session.get(Goal, asset.goal_id)
            assert goal is not None
            versions.append(goal.policy_version_id)
        return ExecutionEffect(
            **common,
            business_key="purchase:" + str(action_id),
            action_type="PURCHASE_ASSET",
            amount_cents=amount,
            cash_uses=cash,
            income_uses=[] if asset.goal_id else funding_income(session, user_id, cash, now),
            goal_id=asset.goal_id,
            policy_id=intent.policy_id,
            policy_version_id=asset.policy_version_id,
            policy_version_ids=versions,
            product_id=product.id,
            product_version_number=product.version_number,
            terms_digest=configuration_hash(product.maturity_rule),
            position_id=uuid5(action_id, "position"),
            position_account_id=position_account.id,
            return_account_id=cash[0].account_id,
            purchase_exit=candidate.exit_plan,
            latest_arrival_at=candidate.exit_plan.principal_available_at
            + (
                timedelta(minutes=15)
                if candidate.exit_plan.kind == "FIXED_MATURITY"
                else timedelta()
            ),
        )
    if intent.kind == "redeem_asset":
        position = session.get(AssetPosition, intent.position_id)
        if position is None or position.user_id != user_id or position.policy_version_id is None:
            raise _error("必须保留原自动购买仓位与历史权限")
        original = session.get(PolicyVersion, position.policy_version_id)
        assert original is not None
        version = _current(session, user_id, original.policy_id, now)
        quote = load_execution_quote(session, user_id, position.id, now)
        delay = (quote.principal_available_at - quote.request_at).days
        common["expires_at"] = min(common["expires_at"], quote.expires_at)
        return ExecutionEffect(
            **common,
            business_key=f"redeem:{position.id}",
            action_type="REDEEM_ASSET",
            amount_cents=position.principal_cents,
            destination_account_id=execution_return_account(session, user_id, position, now),
            goal_id=position.goal_id,
            policy_id=version.policy_id,
            policy_version_id=version.id,
            policy_version_ids=[version.id],
            product_id=quote.product_id,
            product_version_number=quote.product_version_number,
            terms_digest=quote.terms_digest,
            position_id=position.id,
            position_account_id=position.account_id,
            original_policy_version_id=position.policy_version_id,
            fee_cents=quote.fee_cents,
            loss_cents=quote.loss_cents,
            net_cents=quote.net_cents,
            quote_id=quote.quote_id,
            settlement_delay_days=delay,
            latest_arrival_at=quote.expires_at + timedelta(days=delay),
        )
    version = _current(session, user_id, intent.policy_id, now)
    config = version.configuration
    if config["type"] != "recurring_obligation":
        raise _error("此策略不是周期付款义务")
    context = load_boundary_context(session, user_id, now)
    rule = config["amount_rule"]
    liability: BillReference | OccurrenceReference
    if rule["kind"] == "bill_balance":
        if intent.bill_id is None or intent.period is not None:
            raise _error("真实账单付款只接受精确bill_id")
        bill = next((b for b in context.snapshot.bills if b.bill_id == intent.bill_id), None)
        if bill is None or str(bill.account_id) != rule["account_id"]:
            raise _error("账单与本策略原卡账户不匹配")
        amount = bill.total_cents - bill.paid_cents
        liability = BillReference(bill_id=bill.bill_id, evidence_ids=bill.evidence_ids)
        key = f"bill:{bill.bill_id}"
    else:
        if intent.period is None or intent.bill_id is not None:
            raise _error("周期付款只接受精确自然月")
        old = next(
            (
                s
                for s in context.snapshot.occurrence_settlements
                if s.policy_id == version.policy_id and s.period == intent.period
            ),
            None,
        )
        if old is None:
            raise _error("缺少该期完整已付金额证明，不能推定为零")
        total = rule["amount_cents"] if rule["kind"] == "exact" else old.final_total_cents
        if total is None:
            raise _error("范围上限不能当作实际应付金额，需本期最终金额")
        amount = total - old.paid_cents
        liability = OccurrenceReference(
            policy_id=version.policy_id,
            period=intent.period,
            final_total_cents=old.final_total_cents,
            evidence_ids=old.evidence_ids,
        )
        key = f"recurring:{version.policy_id}:{intent.period}"
    if amount <= 0:
        raise _error("该项义务已无未付金额")
    cash = _cash_funding(session, user_id, amount, now)
    return ExecutionEffect(
        **common,
        business_key=key,
        action_type="PAY_RECURRING",
        amount_cents=amount,
        cash_uses=cash,
        income_uses=funding_income(session, user_id, cash, now),
        policy_id=version.policy_id,
        policy_version_id=version.id,
        policy_version_ids=[version.id],
        liability=liability,
        payee_id=config["payee_id"],
        payee_evidence_id=resolve_payee_binding(
            session, user_id, config["payee_id"], now, bill_id=intent.bill_id
        ),
    )
