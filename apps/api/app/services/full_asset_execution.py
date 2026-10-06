"""Read current portfolio consumption facts without granting or writing money.

Durable preparation, whole confirmation and dispatch are installed only after
the immutable parent store and both original pipeline guards are present.
"""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from app.db.models import Account, Goal
from app.domain.execution_types import CashUse
from app.domain.full_asset_allocation import FullAssetPlanOptions
from app.domain.full_asset_execution import (
    FullAssetCatalogueReference,
    FullAssetExecutionBasis,
    FullAssetFrozenPortfolio,
    FullAssetPrepareRequest,
    build_frozen_portfolio,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.income_ledger import IncomeUse
from app.services.audit_chain import current_audit_epoch
from app.services.execution_context import load_execution_context
from app.services.execution_planning import _current, funding_income
from app.services.full_asset_allocation import (
    FullAssetAllocationResponse,
    read_full_asset_allocation,
)
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_protection_projection import (
    FullAnnualProtectionResponse,
    compute_full_annual_protection,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class FullAssetExecutionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["full-asset-execution-preview-v1"] = "full-asset-execution-preview-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    original_request: FullAssetPrepareRequest
    state: Literal["READY_TO_REVIEW", "BLOCKED", "UNKNOWN"]
    original_full_planning: FullAssetAllocationResponse
    original_full_protection: FullAnnualProtectionResponse
    portfolio: FullAssetFrozenPortfolio | None
    reasons: list[str]
    bank_authority: Literal[False] = False
    funds_reserved: Literal[False] = False
    persisted: Literal[False] = False
    financial_experiment_verified: Literal[False] = False
    execution_support: Literal["DURABLE_ORIGINAL_PURCHASE_CONSUMER_REQUIRES_SHARED_GUARDS"] = (
        "DURABLE_ORIGINAL_PURCHASE_CONSUMER_REQUIRES_SHARED_GUARDS"
    )
    limitations: list[str]


def partition_original_income(
    batch_cash: list[list[CashUse]], original: list[IncomeUse]
) -> list[list[IncomeUse]]:
    """Partition one actual aggregate funding result, never query per batch twice.

    Original old cash is used first in original batch order; verified original
    fragments are then divided without increasing their total assigned amount.
    No source amount is an HTTP input or a newly manufactured income fact.
    """
    totals: dict[UUID, int] = {}
    incoming: dict[UUID, int] = {}
    residual = [row.amount_cents for row in original]
    for cash in batch_cash:
        for row in cash:
            totals[row.account_id] = totals.get(row.account_id, 0) + row.amount_cents
    for income in original:
        incoming[income.account_id] = incoming.get(income.account_id, 0) + income.amount_cents
    old = {account: value - incoming.get(account, 0) for account, value in totals.items()}
    if any(value < 0 for value in old.values()) or set(incoming) - set(totals):
        raise ValueError("ORIGINAL_AGGREGATE_INCOME_DOES_NOT_MATCH_CASH")
    result: list[list[IncomeUse]] = []
    for cash in batch_cash:
        parts: list[IncomeUse] = []
        for use in cash:
            from_old = min(old[use.account_id], use.amount_cents)
            old[use.account_id] -= from_old
            needed = use.amount_cents - from_old
            for number, income in enumerate(original):
                if income.account_id != use.account_id:
                    continue
                take = min(needed, residual[number])
                if take:
                    parts.append(income.model_copy(update={"amount_cents": take}))
                    residual[number] -= take
                    needed -= take
            if needed:
                raise ValueError("ORIGINAL_AGGREGATE_INCOME_FRAGMENT_SHORTFALL")
        result.append(parts)
    if any(residual):
        raise ValueError("ORIGINAL_AGGREGATE_INCOME_DENOMINATOR_NOT_CONSUMED")
    return result


def preview_full_asset_execution(
    session: Session,
    user_id: UUID,
    body: FullAssetPrepareRequest,
    now: datetime,
) -> FullAssetExecutionPreview:
    _read_snapshot(session)
    body = FullAssetPrepareRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
        raise PolicyLifecycleError("FULL_ASSET_EPOCH_CHANGED", "需要当前原OPEN epoch", 409)
    planning = read_full_asset_allocation(
        session, user_id, body.full_policy_id, now, FullAssetPlanOptions(mode=body.planning_mode)
    )
    protection = compute_full_annual_protection(session, user_id, now)
    full = read_full_policy(session, user_id, body.full_policy_id, now)
    config = AssetAuthorizationPolicy.model_validate(full.current_version.configuration)
    reasons = [row.code for row in planning.source_issues]
    reasons.extend(row.code for row in protection.source_issues)
    if full.current_version.version_id != body.expected_full_policy_version_id:
        raise PolicyLifecycleError("FULL_ASSET_VERSION_CHANGED", "原Full资产版本已经变化", 409)
    if config.goal_id != body.goal_id:
        raise PolicyLifecycleError("FULL_ASSET_SCOPE_CHANGED", "原目标或一般资金scope不一致", 409)
    if not full.planning_confirmation_valid or full.reference_validation != "CURRENT":
        reasons.append("CURRENT_FULL_PLANNING_CONFIRMATION_NOT_VERIFIED")
    if planning.catalogue.status != "VERIFIED":
        reasons.append("CURRENT_IMMUTABLE_CATALOGUE_NOT_VERIFIED")
    if not protection.projection.full_obligations_complete_within_registered_current_scope:
        reasons.append("CURRENT_FULL_PROTECTION_INVENTORY_NOT_VERIFIED")
    allocation = planning.allocation
    if allocation is None or allocation.status != "OPTIMAL" or not allocation.batches:
        reasons.append("NO_CURRENT_OPTIMAL_NONEMPTY_PORTFOLIO")
    portfolio = None
    if not reasons and allocation is not None:
        try:
            version = _current(session, user_id, body.mvp_asset_policy_id, now)
            if version.id != body.expected_mvp_policy_version_id:
                raise ValueError("CURRENT_MVP_ASSET_PERMISSION_VERSION_CHANGED")
            goal = session.get(Goal, body.goal_id) if body.goal_id is not None else None
            if body.goal_id is not None and (
                goal is None
                or goal.user_id != user_id
                or goal.policy_version_id != body.expected_goal_policy_version_id
                or goal.asset_policy_id != body.mvp_asset_policy_id
            ):
                raise ValueError("CURRENT_ORIGINAL_GOAL_ASSET_LINK_NOT_VERIFIED")
            endpoints = [now + timedelta(minutes=15)]
            if full.current_version.valid_until is not None:
                endpoints.append(full.current_version.valid_until)
            if version.valid_until is not None:
                endpoints.append(version.valid_until)
            accounts = list(
                session.scalars(
                    select(Account)
                    .where(Account.user_id == user_id, Account.currency == "CNY")
                    .order_by(Account.id)
                )
            )
            positions: dict[UUID, UUID] = {}
            batch_cash = [
                [CashUse(account_id=u.account_id, amount_cents=u.amount_cents) for u in b.cash_uses]
                for b in allocation.batches
            ]
            cash_totals: dict[UUID, int] = {}
            for cash in batch_cash:
                for use in cash:
                    cash_totals[use.account_id] = (
                        cash_totals.get(use.account_id, 0) + use.amount_cents
                    )
            income = (
                []
                if body.goal_id
                else funding_income(
                    session,
                    user_id,
                    [CashUse(account_id=k, amount_cents=v) for k, v in sorted(cash_totals.items())],
                    now,
                )
            )
            income_parts = partition_original_income(batch_cash, income)
            for product in planning.catalogue.products:
                kind = (
                    "FIXED_DEPOSIT" if product.asset_class == "FIXED_DEPOSIT" else "CASH_MANAGEMENT"
                )
                account = next((a for a in accounts if a.account_type == kind), None)
                if account is not None:
                    positions[product.product_id] = account.id
            # The original context reader requires an exact effect. Construct its first
            # identity from the real planned batch, then re-read all original permissions.
            from uuid import uuid5

            from app.domain.execution_types import ExecutionEffect
            from app.domain.full_asset_execution import portfolio_identity

            first = allocation.batches[0]
            action_id = uuid5(portfolio_identity(user_id, body), "batch:1")
            if first.product_id not in positions:
                raise ValueError("CURRENT_OWNED_POSITION_ACCOUNT_MISSING")
            preview_effect = ExecutionEffect(
                operation_id=action_id,
                user_id=user_id,
                business_key=f"purchase:{action_id}",
                action_type="PURCHASE_ASSET",
                amount_cents=first.amount_cents,
                cash_uses=batch_cash[0],
                income_uses=income_parts[0],
                goal_id=body.goal_id,
                policy_id=body.mvp_asset_policy_id,
                policy_version_id=version.id,
                policy_version_ids=[version.id] + ([goal.policy_version_id] if goal else []),
                product_id=first.product_id,
                product_version_number=first.version_number,
                terms_digest=first.terms_digest,
                position_id=uuid5(action_id, "position"),
                position_account_id=positions[first.product_id],
                return_account_id=batch_cash[0][0].account_id,
                purchase_exit=first.exit_plan,
                latest_arrival_at=first.principal_available_at + timedelta(minutes=15),
                valid_from=now,
                expires_at=min(endpoints),
            )
            # Loading is the current permission/fact check, not acceptance of a dummy run.
            context = load_execution_context(session, user_id, preview_effect, now)
            basis = FullAssetExecutionBasis(
                user_id=user_id,
                epoch_id=epoch.id,
                as_of=now,
                full_policy_configuration=config,
                full_policy_content_hash=full.current_version.content_hash,
                full_planning_confirmation_valid=full.planning_confirmation_valid,
                original_planning_response_hash=planning.input_hash,
                planning=allocation,
                context=context,
                catalogue=[
                    FullAssetCatalogueReference.model_validate(r.model_dump())
                    for r in planning.catalogue.bindings
                ],
                position_accounts=positions,
                batch_income_uses=income_parts,
                full_protection_sources=protection.full_policy_sources,
                full_protection_inventory_complete=protection.projection.full_obligations_complete_within_registered_current_scope,
                full_source_issues=reasons,
                source_evidence_ids=sorted(
                    set(planning.source_evidence_ids + protection.source_evidence_ids)
                ),
                expires_at=min(endpoints),
            )
            portfolio = build_frozen_portfolio(body, basis)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reasons.append(str(error))
        except PolicyLifecycleError as error:
            reasons.append(error.code)
    return FullAssetExecutionPreview(
        user_id=user_id,
        epoch_id=epoch.id,
        as_of=now,
        original_request=body,
        state="READY_TO_REVIEW" if portfolio is not None else "UNKNOWN",
        original_full_planning=planning,
        original_full_protection=protection,
        portfolio=portfolio,
        reasons=sorted(set(reasons)),
        limitations=[
            "当前preview仅展示由真实来源计算的完整组合，没有持久或银行授权。",
            "每批仍需当前原MVP权限；Full候选规划确认不能替代金融权限。",
            "实际执行须经过独立不可变组合store及原phase1/bank顺序guard，缺接线即拒绝。",
            "未决前批阻断后批；已提交银行批次不能承诺跨操作整体回滚。",
            "未来工资为0；计划赎回不当成已承诺到款，到期后不自动滚存。",
        ],
    )
