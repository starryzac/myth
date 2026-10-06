"""Recheck the exact unexecuted combination, without choosing new money or products."""

from datetime import datetime, timedelta
from uuid import UUID

from app.domain.asset_allocation_types import FixedPrincipalTerms
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryModel, BoundaryPosition
from app.domain.execution import revalidate_execution
from app.domain.execution_types import ExecutionContext
from app.domain.full_asset_execution import FullAssetFrozenPortfolio, Hash
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from pydantic import StrictInt


class FullAssetRemainingCheck(BoundaryModel):
    remaining_batch_numbers: list[StrictInt]
    remaining_purchase_cents: StrictInt
    original_boundary_hash: Hash
    full_protection_hash: Hash
    checked_point_count: StrictInt


def validate_remaining_combination(
    portfolio: FullAssetFrozenPortfolio,
    context: ExecutionContext,
    remaining_batch_numbers: list[int],
    now: datetime,
    *,
    current_full_configuration_hash: str,
    full_sources: list[FullProtectionPolicySource],
    full_source_issues: list[str],
    full_inventory_complete: bool,
) -> FullAssetRemainingCheck:
    """The adapter may remove only the current action's verified own reservation.

    Previously settled positions remain in the actual context. All other
    reservations and unresolved effects remain there. Planned redemption never
    becomes a committed return; new fixed principal uses the actual accept time.
    """
    portfolio = FullAssetFrozenPortfolio.model_validate_json(portfolio.model_dump_json())
    context = ExecutionContext.model_validate_json(context.model_dump_json())
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or not portfolio.prepared_at <= now < portfolio.expires_at
        or (context.user_id, context.snapshot.as_of) != (portfolio.user_id, now)
        or context.source_issues
        or context.snapshot.source_issues
        or full_source_issues
        or not full_inventory_complete
        or current_full_configuration_hash != portfolio.full_policy_content_hash
        or not remaining_batch_numbers
        or remaining_batch_numbers != sorted(set(remaining_batch_numbers))
        or any(n < 1 or n > len(portfolio.batches) for n in remaining_batch_numbers)
    ):
        raise ValueError("FULL_ASSET_REMAINING_CURRENT_SOURCES_NOT_PROVEN")
    request = portfolio.original_request
    version = next(
        (v for v in context.versions if v.version_id == request.expected_mvp_policy_version_id),
        None,
    )
    if version is None or version.policy_id != request.mvp_asset_policy_id:
        raise ValueError("FULL_ASSET_REMAINING_MVP_PERMISSION_CHANGED")
    config = validate_configuration(version.configuration)
    if (
        configuration_hash(config) != portfolio.original_mvp_configuration_hash
        or context.exposure is None
        or context.exposure.goal_id != request.goal_id
    ):
        raise ValueError("FULL_ASSET_REMAINING_SCOPE_OR_PERMISSION_CHANGED")
    batches = [portfolio.batches[n - 1] for n in remaining_batch_numbers]
    amount = sum(b.command.effect.amount_cents for b in batches)
    if (
        context.exposure.managed_principal_cents + context.exposure.pending_purchase_cents + amount
        > min(config["max_auto_managed_cents"], portfolio.full_configuration.max_auto_managed_cents)
    ):
        raise ValueError("FULL_ASSET_REMAINING_AGGREGATE_CAP_EXCEEDED")
    snapshot, positions = (
        context.snapshot.model_copy(update={"horizon_days": 365}),
        list(context.positions),
    )
    products = {p.product_id: p for p in context.products}
    cash: dict[UUID, int] = {}
    income: dict[UUID, int] = {}
    for batch in batches:
        effect = batch.command.effect
        validation = revalidate_execution(effect, context)
        if validation.status not in {"READY", "CONFIRMATION_REQUIRED"}:
            raise ValueError("FULL_ASSET_REMAINING_ORIGINAL_REVALIDATION_REJECTED")
        for use in effect.cash_uses:
            cash[use.account_id] = cash.get(use.account_id, 0) + use.amount_cents
        for income_use in effect.income_uses:
            income[income_use.fragment_id] = (
                income.get(income_use.fragment_id, 0) + income_use.amount_cents
            )
        snapshot = snapshot.model_copy(
            update={
                "cash_accounts": [
                    a.model_copy(
                        update={
                            "balance_cents": a.balance_cents
                            - sum(
                                u.amount_cents
                                for u in effect.cash_uses
                                if u.account_id == a.account_id
                            )
                        }
                    )
                    for a in snapshot.cash_accounts
                ],
                "goals": [
                    g.model_copy(
                        update={
                            "cash_owned_cents": g.cash_owned_cents - effect.amount_cents,
                            "principal_owned_cents": g.principal_owned_cents + effect.amount_cents,
                        }
                    )
                    if g.goal_id == request.goal_id
                    else g
                    for g in snapshot.goals
                ],
            }
        )
        product = products.get(effect.product_id) if effect.product_id is not None else None
        if product is None or effect.purchase_exit is None or effect.position_id is None:
            raise ValueError("FULL_ASSET_REMAINING_ORIGINAL_PRODUCT_MISSING")
        available_at = None
        if effect.purchase_exit.kind == "FIXED_MATURITY":
            terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
            available_at = now + timedelta(days=terms.term_days + terms.settlement_delay_days)
        positions.append(
            BoundaryPosition(
                position_id=effect.position_id,
                goal_id=effect.goal_id,
                principal_cents=effect.amount_cents,
                status="HELD",
                principal_available_at=available_at,
            )
        )
    for account in context.snapshot.cash_accounts:
        owned = sum(
            g.cash_owned_cents for g in context.snapshot.goals if g.account_id == account.account_id
        )
        available = (
            account.balance_cents
            - owned
            - context.reserved_cash_by_account.get(account.account_id, 0)
        )
        if request.goal_id is not None:
            goal = next((g for g in context.snapshot.goals if g.goal_id == request.goal_id), None)
            available = (
                goal.cash_owned_cents if goal and goal.account_id == account.account_id else 0
            ) - context.reserved_goal_cash_by_goal.get(request.goal_id, 0)
        if cash.get(account.account_id, 0) > available:
            raise ValueError("FULL_ASSET_REMAINING_ORIGINAL_CASH_NOT_AVAILABLE")
    lots = {lot.fragment_id: lot.available_cents for lot in context.lots}
    if any(value > lots.get(identity, 0) for identity, value in income.items()):
        raise ValueError("FULL_ASSET_REMAINING_ORIGINAL_INCOME_NOT_AVAILABLE")
    baseline = compute_boundary(snapshot, context.versions, positions, context.boundary_products)
    protected = project_full_protection(
        FullProtectionProjectionInput(
            snapshot=snapshot,
            boundary_versions=context.versions,
            positions=positions,
            boundary_products=context.boundary_products,
            policies=full_sources,
            reserved_cash_by_account=context.reserved_cash_by_account,
            full_source_inventory_complete=full_inventory_complete,
            full_source_issues=full_source_issues,
        )
    )
    curve = protected.full_annual_projection
    if (
        baseline.status != "READY"
        or protected.status == "UNKNOWN"
        or protected.source_account_checks
        or curve is None
        or curve.minimum_margin_cents is None
        or curve.minimum_margin_cents < 0
        or len(curve.calculation_trace) != 1098
    ):
        raise ValueError("FULL_ASSET_REMAINING_WHOLE_PROTECTION_NOT_PROVEN")
    return FullAssetRemainingCheck(
        remaining_batch_numbers=remaining_batch_numbers,
        remaining_purchase_cents=amount,
        original_boundary_hash=baseline.boundary_hash,
        full_protection_hash=protected.input_hash,
        checked_point_count=len(curve.calculation_trace),
    )
