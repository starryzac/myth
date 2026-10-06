"""Pure execution-time financial checks; adapters separately verify authority and consent."""

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from app.domain.asset_allocation import select_asset
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import BoundaryPosition, BoundarySnapshot, SettlementFact
from app.domain.execution_types import (
    BillReference,
    ConfirmationGrant,
    ExecutionContext,
    ExecutionEffect,
    ExecutionValidation,
    OccurrenceReference,
)
from app.domain.goal_allocation import plan_goal_allocation
from app.domain.income_ledger import location_id
from app.domain.policy_configuration import configuration_hash, validate_configuration

ALGORITHM_VERSION = "economic-effect-revalidation-v1"
ACTION_PLAN_TYPES = {
    "TRANSFER_INTERNAL": "TRANSFER_INTERNAL",
    "PAY_RECURRING": "PAY_RECURRING",
    "ALLOCATE_GOAL": "ALLOCATE_GOAL",
    "PURCHASE_ASSET": "ASSET_PURCHASE",
    "REDEEM_ASSET": "ASSET_REDEEM",
}


def execution_effect_hash(effect: ExecutionEffect) -> str:
    effect = ExecutionEffect.model_validate(effect.model_dump(warnings=False))
    value = effect.model_dump(mode="json")
    value["cash_uses"] = sorted(value["cash_uses"], key=lambda u: u["account_id"])
    value["income_uses"] = sorted(
        value["income_uses"], key=lambda u: (u["origin_transaction_id"], u["account_id"])
    )
    value["policy_version_ids"] = sorted(value["policy_version_ids"])
    if value["liability"] is not None:
        value["liability"]["evidence_ids"] = sorted(value["liability"]["evidence_ids"])
    return configuration_hash(value)


def revalidate_execution(
    effect: ExecutionEffect,
    context: ExecutionContext,
    *,
    confirmation: ConfirmationGrant | None = None,
) -> ExecutionValidation:
    """Use context.snapshot.as_of as the supplied execution clock, never system time."""
    effect = ExecutionEffect.model_validate(effect.model_dump(warnings=False))
    context = ExecutionContext.model_validate(context.model_dump(warnings=False))
    snapshot = context.snapshot
    baseline = compute_boundary(
        snapshot, context.versions, context.positions, context.boundary_products
    )

    def blocked(
        code: str,
        status: Literal["BLOCKED", "CONFIRMATION_REQUIRED", "INSUFFICIENT_EVIDENCE"] = "BLOCKED",
    ) -> ExecutionValidation:
        return ExecutionValidation(
            status=status,
            effect_hash=execution_effect_hash(effect),
            baseline_boundary=baseline,
            reasons=[code],
        )

    if effect.user_id != context.user_id:
        return blocked("USER_MISMATCH")
    if not effect.valid_from <= snapshot.as_of < effect.expires_at:
        return blocked("EFFECT_OUTSIDE_VALIDITY")
    if context.source_issues or baseline.status == "INSUFFICIENT_EVIDENCE":
        return blocked("INCOMPLETE_EXECUTION_FACTS", "INSUFFICIENT_EVIDENCE")
    general_reserved, goal_reserved = _reservation_amounts(context)
    adjusted_baseline = (
        compute_boundary(
            _cash_reserved(snapshot, general_reserved),
            context.versions,
            context.positions,
            context.boundary_products,
        )
        if any(general_reserved.values())
        else baseline
    )
    accounts = {a.account_id: a for a in snapshot.cash_accounts}
    for use in effect.cash_uses:
        account = accounts.get(use.account_id)
        owned = sum(g.cash_owned_cents for g in snapshot.goals if g.account_id == use.account_id)
        goal_purchase = effect.action_type == "PURCHASE_ASSET" and effect.goal_id is not None
        reserved = (
            goal_reserved.get(effect.goal_id, 0)
            if goal_purchase and effect.goal_id is not None
            else general_reserved.get(use.account_id, 0)
        )
        goal = (
            next((g for g in snapshot.goals if g.goal_id == effect.goal_id), None)
            if goal_purchase
            else None
        )
        available = (
            (goal.cash_owned_cents if goal is not None and goal.account_id == use.account_id else 0)
            if goal_purchase
            else (account.balance_cents - owned if account else 0)
        )
        if (
            account is None
            or account.account_type not in ({"CASH", "GOAL"} if goal_purchase else {"CASH"})
            or use.amount_cents > available - reserved
        ):
            return blocked("UNAVAILABLE_SOURCE_CASH")
    try:
        _income_uses(effect, context, general_reserved)
    except Rejected as error:
        return blocked(str(error))
    if effect.action_type == "TRANSFER_INTERNAL":
        destination = (
            accounts.get(effect.destination_account_id)
            if effect.destination_account_id is not None
            else None
        )
        if destination is None or destination.account_type != "CASH":
            return blocked("INVALID_TRANSFER_DESTINATION")
        if any(u.account_id == effect.destination_account_id for u in effect.cash_uses):
            return blocked("TRANSFER_REQUIRES_DISTINCT_ACCOUNTS")
    if confirmation is not None:
        confirmation = ConfirmationGrant.model_validate(confirmation.model_dump(warnings=False))
        if (
            confirmation.user_id != effect.user_id
            or confirmation.operation_id != effect.operation_id
            or confirmation.effect_hash != execution_effect_hash(effect)
            or not effect.valid_from
            <= confirmation.confirmed_at
            <= snapshot.as_of
            < min(effect.expires_at, confirmation.expires_at)
        ):
            return blocked("CONFIRMATION_DOES_NOT_BIND_CURRENT_EFFECT")
    confirmation_required = confirmation is None and (
        context.requires_confirmation
        or effect.action_type == "TRANSFER_INTERNAL"
        or (effect.action_type == "REDEEM_ASSET" and bool(effect.fee_cents or effect.loss_cents))
    )
    if effect.action_type != "TRANSFER_INTERNAL":
        current = next(
            (
                v
                for v in context.versions
                if v.version_id == effect.policy_version_id and v.policy_id == effect.policy_id
            ),
            None,
        )
        if (
            current is None
            or not max(current.confirmed_at, current.valid_from) <= snapshot.as_of
            or (current.valid_until is not None and snapshot.as_of >= current.valid_until)
        ):
            return blocked("EXACT_CURRENT_POLICY_REQUIRED")
    projected = snapshot.model_copy(
        update={
            "cash_accounts": [
                a.model_copy(
                    update={
                        "balance_cents": a.balance_cents
                        - sum(
                            u.amount_cents for u in effect.cash_uses if u.account_id == a.account_id
                        )
                        + (
                            effect.amount_cents
                            if effect.action_type != "REDEEM_ASSET"
                            and a.account_id == effect.destination_account_id
                            else 0
                        )
                    }
                )
                for a in snapshot.cash_accounts
            ]
        }
    )
    projected_positions = list(context.positions)
    try:
        if effect.action_type == "PAY_RECURRING":
            projected = _payment(effect, context, projected)
        elif effect.action_type == "ALLOCATE_GOAL":
            projected = _goal(effect, context, projected)
        elif effect.action_type == "PURCHASE_ASSET":
            projected, projected_positions = _purchase(effect, context, projected)
        elif effect.action_type == "REDEEM_ASSET":
            projected, projected_positions = _redeem(effect, context)
    except Rejected as error:
        return blocked(str(error))
    checked = compute_boundary(
        projected, context.versions, projected_positions, context.boundary_products
    )
    if checked.status == "INSUFFICIENT_EVIDENCE":
        return blocked("PROJECTED_EVIDENCE_INCOMPLETE", "INSUFFICIENT_EVIDENCE")
    adjusted = (
        compute_boundary(
            _cash_reserved(projected, general_reserved),
            context.versions,
            projected_positions,
            context.boundary_products,
        )
        if any(general_reserved.values())
        else checked
    )
    if effect.action_type in {"ALLOCATE_GOAL", "PURCHASE_ASSET"} and (
        adjusted_baseline.status != "READY" or adjusted.status != "READY"
    ):
        if effect.purchase_exit is not None and effect.purchase_exit.kind == "PLANNED_REDEMPTION":
            return blocked("UNACCEPTED_PLANNED_EXIT_CANNOT_SUPPORT_CASH_BOUNDARY")
        return blocked("ORDINARY_ALLOCATION_REQUIRES_SAFE_BOUNDARY")
    if effect.action_type in {"TRANSFER_INTERNAL", "PAY_RECURRING"} and any(
        a.margin_cents < b.margin_cents
        for a, b in zip(
            adjusted.calculation_trace, adjusted_baseline.calculation_trace, strict=True
        )
    ):
        return blocked("ACTION_WORSENS_PROTECTED_MARGIN")
    if effect.action_type == "REDEEM_ASSET":
        pairs = list(
            zip(adjusted.calculation_trace, adjusted_baseline.calculation_trace, strict=True)
        )
        has_cost = bool(effect.fee_cents or effect.loss_cents)
        if any(
            after.margin_cents < (min(before.margin_cents, 0) if has_cost else before.margin_cents)
            for after, before in pairs
        ) or not any(
            before.margin_cents < 0 and after.margin_cents > before.margin_cents
            for after, before in pairs
        ):
            return blocked("REDEMPTION_DOES_NOT_SAFELY_IMPROVE_DEFICIT")
    return ExecutionValidation(
        status="CONFIRMATION_REQUIRED" if confirmation_required else "READY",
        effect_hash=execution_effect_hash(effect),
        baseline_boundary=baseline,
        projected_boundary=checked,
        reservation_adjusted_baseline=adjusted_baseline,
        reservation_adjusted_boundary=adjusted,
        projected_snapshot=projected,
        projected_positions=projected_positions,
        reasons=[_confirmation_reason(effect, context)] if confirmation_required else [],
    )


def _confirmation_reason(effect: ExecutionEffect, context: ExecutionContext) -> str:
    if effect.action_type == "TRANSFER_INTERNAL":
        return "EXPLICIT_TRANSFER_CONFIRMATION_REQUIRED"
    if effect.action_type == "PAY_RECURRING":
        version = next(v for v in context.versions if v.version_id == effect.policy_version_id)
        if version.configuration["amount_rule"]["kind"] == "range":
            return "EXPLICIT_PERIOD_AMOUNT_CONFIRMATION_REQUIRED"
        return "EXPLICIT_PAYMENT_CONFIRMATION_REQUIRED"
    if effect.fee_cents or effect.loss_cents:
        return "EXPLICIT_COST_CONFIRMATION_REQUIRED"
    return "EXPLICIT_POLICY_CONFIRMATION_REQUIRED"


class Rejected(ValueError):
    """A complete but disallowed economic request, distinct from malformed DTO values."""


def _income_uses(
    effect: ExecutionEffect, context: ExecutionContext, reserved: dict[UUID, int]
) -> None:
    if effect.action_type == "REDEEM_ASSET":
        return
    if effect.action_type == "PURCHASE_ASSET" and effect.goal_id is not None:
        if effect.income_uses:
            raise Rejected("GOAL_PURCHASE_CANNOT_RECONSUME_INCOME")
        return
    locations = {(lot.origin_transaction_id, lot.account_id): lot for lot in context.lots}
    if len(locations) != len(context.lots):
        raise ValueError("Income location facts must be unique")
    declared: dict[UUID, int] = {}
    for use in effect.income_uses:
        lot = locations.get((use.origin_transaction_id, use.account_id))
        if (
            lot is None
            or use.amount_cents > lot.available_cents
            or lot.observed_at > context.snapshot.as_of
        ):
            raise Rejected("INCOME_USE_NOT_AVAILABLE_AT_EXECUTION")
        declared[use.account_id] = declared.get(use.account_id, 0) + use.amount_cents
    accounts = {a.account_id: a for a in context.snapshot.cash_accounts}
    for cash_use in effect.cash_uses:
        account = accounts[cash_use.account_id]
        owned = sum(
            g.cash_owned_cents
            for g in context.snapshot.goals
            if g.account_id == cash_use.account_id
        )
        available = sum(
            lot.available_cents for lot in context.lots if lot.account_id == cash_use.account_id
        )
        older_cash = (
            account.balance_cents - owned - reserved.get(cash_use.account_id, 0) - available
        )
        if older_cash < 0 or declared.get(cash_use.account_id, 0) < max(
            0, cash_use.amount_cents - older_cash
        ):
            raise Rejected("CASH_EFFECT_WOULD_LEAVE_UNBACKED_INCOME")


def _reservation_amounts(context: ExecutionContext) -> tuple[dict[UUID, int], dict[UUID, int]]:
    # Asset exposure and execution claims describe overlapping reservations, not two debits.
    totals = dict(context.reserved_cash_by_account)
    goals = dict(context.reserved_goal_cash_by_goal)
    if context.exposure is not None:
        for identity, amount in context.exposure.reserved_cash_by_account.items():
            totals[identity] = max(totals.get(identity, 0), amount)
        for identity, amount in context.exposure.reserved_goal_cash_by_goal.items():
            goals[identity] = max(goals.get(identity, 0), amount)
    accounts = {a.account_id: a for a in context.snapshot.cash_accounts}
    ownership = {g.goal_id: g for g in context.snapshot.goals}
    grouped: dict[UUID, int] = {}
    for identity, amount in goals.items():
        goal = ownership.get(identity)
        if goal is None or goal.account_id is None or amount > goal.cash_owned_cents:
            raise ValueError("Goal reservations require existing owned cash")
        grouped[goal.account_id] = grouped.get(goal.account_id, 0) + amount
    general: dict[UUID, int] = {}
    for identity in totals.keys() | grouped.keys():
        account = accounts.get(identity)
        amount = totals.get(identity, 0) - grouped.get(identity, 0)
        owned = sum(g.cash_owned_cents for g in ownership.values() if g.account_id == identity)
        if (
            account is None
            or account.account_type not in {"CASH", "GOAL"}
            or totals.get(identity, 0) > account.balance_cents
            or amount < 0
            or amount > account.balance_cents - owned
            or (amount and account.account_type != "CASH")
        ):
            raise ValueError("Cash reservations contradict their exact account ownership")
        general[identity] = amount
    return general, goals


def _cash_reserved(snapshot: BoundarySnapshot, amounts: dict[UUID, int]) -> BoundarySnapshot:
    return snapshot.model_copy(
        update={
            "cash_accounts": [
                a.model_copy(
                    update={"balance_cents": a.balance_cents - amounts.get(a.account_id, 0)}
                )
                for a in snapshot.cash_accounts
            ]
        }
    )


def _payment(
    effect: ExecutionEffect, context: ExecutionContext, projected: BoundarySnapshot
) -> BoundarySnapshot:
    version = next(v for v in context.versions if v.version_id == effect.policy_version_id)
    config = validate_configuration(version.configuration)
    if config["type"] != "recurring_obligation" or config["payee_id"] != effect.payee_id:
        raise Rejected("PAYEE_OR_POLICY_MISMATCH")
    rule = config["amount_rule"]
    zone = UTC if context.snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
    today = context.snapshot.as_of.astimezone(zone).date()
    ref = effect.liability
    if isinstance(ref, BillReference):
        bill = next((b for b in projected.bills if b.bill_id == ref.bill_id), None)
        if (
            bill is None
            or rule["kind"] != "bill_balance"
            or rule["account_id"] != str(bill.account_id)
        ):
            raise Rejected("BILL_IDENTITY_MISMATCH")
        if bill.due_date > today or effect.amount_cents > bill.total_cents - bill.paid_cents:
            raise Rejected("BILL_NOT_PAYABLE")
        paid = bill.paid_cents + effect.amount_cents
        updated = bill.model_copy(
            update={
                "paid_cents": paid,
                "status": "PAID" if paid == bill.total_cents else "PARTIALLY_PAID",
            }
        )
        return projected.model_copy(
            update={"bills": [updated if b.bill_id == bill.bill_id else b for b in projected.bills]}
        )
    if (
        not isinstance(ref, OccurrenceReference)
        or ref.policy_id != version.policy_id
        or rule["kind"] == "bill_balance"
    ):
        raise Rejected("OCCURRENCE_IDENTITY_MISMATCH")
    month = date.fromisoformat(ref.period + "-01")
    due = month.replace(day=min(config["due_day"], monthrange(month.year, month.month)[1]))
    if due > today or due < max(version.confirmed_at, version.valid_from).astimezone(zone).date():
        raise Rejected("OCCURRENCE_NOT_DUE")
    old = next(
        (
            s
            for s in projected.occurrence_settlements
            if s.policy_id == ref.policy_id and s.period == ref.period
        ),
        None,
    )
    if due < today and old is None:
        raise Rejected("MISSING_PREVIOUS_SETTLEMENT")
    if rule["kind"] == "range":
        if (
            old is None
            or old.final_total_cents is None
            or ref.final_total_cents != old.final_total_cents
        ):
            raise Rejected("FINAL_OCCURRENCE_TOTAL_REQUIRED")
        total = old.final_total_cents
    else:
        total = rule["amount_cents"]
        if ref.final_total_cents is not None and ref.final_total_cents != total:
            raise Rejected("EXACT_OCCURRENCE_TOTAL_MISMATCH")
    paid = (old.paid_cents if old else 0) + effect.amount_cents
    if paid > total:
        raise Rejected("PAYMENT_EXCEEDS_UNPAID_OBLIGATION")
    settled = SettlementFact(
        policy_id=ref.policy_id,
        period=ref.period,
        paid_cents=paid,
        settled_at=context.snapshot.as_of,
        final_total_cents=old.final_total_cents if old else None,
        evidence_ids=old.evidence_ids if old else ref.evidence_ids,
    )
    others = [
        s
        for s in projected.occurrence_settlements
        if not (s.policy_id == ref.policy_id and s.period == ref.period)
    ]
    return projected.model_copy(update={"occurrence_settlements": [*others, settled]})


def _goal(
    effect: ExecutionEffect, context: ExecutionContext, projected: BoundarySnapshot
) -> BoundarySnapshot:
    if effect.goal_id is None or effect.policy_version_id is None:
        raise Rejected("GOAL_IDENTITY_REQUIRED")
    result = plan_goal_allocation(
        effect.goal_id,
        effect.policy_version_id,
        context.snapshot,
        context.versions,
        context.positions,
        context.boundary_products,
        context.lots,
        source_issues=context.source_issues,
    )
    if (
        result.status != "READY"
        or result.suggested_cents is None
        or not (result.remaining_min_cents or 0) <= effect.amount_cents <= result.suggested_cents
    ):
        raise Rejected("CURRENT_GOAL_PLAN_DISALLOWS_AMOUNT")
    goal = next(g for g in projected.goals if g.goal_id == effect.goal_id)
    if goal.account_id != effect.destination_account_id or goal.policy_id != effect.policy_id:
        raise Rejected("GOAL_DESTINATION_MISMATCH")
    version = next(v for v in context.versions if v.version_id == effect.policy_version_id)
    sources = {(lot.origin_transaction_id, lot.account_id): lot for lot in context.lots}
    by_account: dict[object, int] = {}
    for use in effect.income_uses:
        lot = sources.get((use.origin_transaction_id, use.account_id))
        if (
            use.fragment_id != location_id(use.origin_transaction_id, use.account_id)
            or lot is None
            or lot.occurred_at < max(version.confirmed_at, version.valid_from)
            or use.amount_cents > lot.available_cents
        ):
            raise Rejected("INCOME_SOURCE_NO_LONGER_ELIGIBLE")
        by_account[use.account_id] = by_account.get(use.account_id, 0) + use.amount_cents
    if by_account != {u.account_id: u.amount_cents for u in effect.cash_uses}:
        raise Rejected("GOAL_CASH_AND_INCOME_USES_MUST_MATCH")
    zone = UTC if projected.timezone == "UTC" else timezone(timedelta(hours=8))
    period = projected.as_of.astimezone(zone).strftime("%Y-%m")
    return projected.model_copy(
        update={
            "goals": [
                g.model_copy(
                    update={
                        "cash_owned_cents": g.cash_owned_cents + effect.amount_cents,
                        "allocated_cents": g.allocated_cents + effect.amount_cents,
                    }
                )
                if g.goal_id == effect.goal_id
                else g
                for g in projected.goals
            ],
            "goal_month_contributions": [
                c.model_copy(
                    update={"contributed_cents": c.contributed_cents + effect.amount_cents}
                )
                if c.goal_id == effect.goal_id and c.period == period
                else c
                for c in projected.goal_month_contributions
            ],
        }
    )


def _purchase(
    effect: ExecutionEffect, context: ExecutionContext, projected: BoundarySnapshot
) -> tuple[BoundarySnapshot, list[BoundaryPosition]]:
    if context.exposure is None:
        raise Rejected("COMPLETE_ASSET_EXPOSURE_REQUIRED")
    version = next(v for v in context.versions if v.version_id == effect.policy_version_id)
    result = select_asset(
        context.snapshot,
        context.versions,
        context.positions,
        context.boundary_products,
        version,
        context.products,
        context.exposure,
        source_issues=context.source_issues,
    )
    candidate = next((c for c in result.candidates if c.product_id == effect.product_id), None)
    product = next((p for p in context.products if p.product_id == effect.product_id), None)
    if (
        result.status != "READY"
        or candidate is None
        or candidate.status != "FEASIBLE"
        or product is None
        or product.version_number != effect.product_version_number
        or product.terms_digest != effect.terms_digest
        or candidate.exit_plan is None
        or effect.amount_cents > (candidate.max_allocatable_cents or 0)
        or effect.amount_cents < product.minimum_purchase_cents
        or result.goal_id != effect.goal_id
    ):
        raise Rejected("CURRENT_ASSET_PLAN_DISALLOWS_EFFECT")
    if effect.position_id is None or any(
        p.position_id == effect.position_id for p in context.positions
    ):
        raise Rejected("PURCHASE_POSITION_ID_MUST_BE_NEW")
    plan = effect.purchase_exit
    if plan is None or plan.kind != candidate.exit_plan.kind:
        raise Rejected("EXACT_PURCHASE_EXIT_PLAN_REQUIRED")
    available: datetime | None = candidate.exit_plan.principal_available_at
    if (
        effect.latest_arrival_at is None
        or candidate.exit_plan.principal_available_at > effect.latest_arrival_at
    ):
        if plan.kind == "FIXED_MATURITY":
            raise Rejected("PRINCIPAL_ARRIVES_AFTER_CONFIRMED_BOUND")
    if plan.kind == "PLANNED_REDEMPTION":
        if (
            plan.request_at is None
            or plan.request_at < context.snapshot.as_of + timedelta(days=product.lock_days)
            or plan.principal_available_at
            != plan.request_at + timedelta(days=product.redemption_delay_days)
            or plan.principal_available_at > candidate.exit_plan.principal_available_at
            or (version.valid_until is not None and plan.request_at >= version.valid_until)
        ):
            raise Rejected("CONFIRMED_EXIT_OUTSIDE_CURRENT_CONTRACT_AND_AUTHORITY")
        # A future request plan is not a bank commitment to return principal.
        available = None
    if effect.goal_id is not None:
        goal = next((g for g in projected.goals if g.goal_id == effect.goal_id), None)
        if goal is None or goal.account_id != effect.return_account_id:
            raise Rejected("GOAL_PURCHASE_MUST_RETURN_TO_SAME_GOAL_ACCOUNT")
        projected = projected.model_copy(
            update={
                "goals": [
                    g.model_copy(
                        update={
                            "cash_owned_cents": g.cash_owned_cents - effect.amount_cents,
                            "principal_owned_cents": g.principal_owned_cents + effect.amount_cents,
                        }
                    )
                    if g.goal_id == effect.goal_id
                    else g
                    for g in projected.goals
                ]
            }
        )
    position = BoundaryPosition(
        position_id=effect.position_id,
        goal_id=effect.goal_id,
        principal_cents=effect.amount_cents,
        status="HELD",
        principal_available_at=available,
    )
    return projected, [*context.positions, position]


def _redeem(
    effect: ExecutionEffect, context: ExecutionContext
) -> tuple[BoundarySnapshot, list[BoundaryPosition]]:
    now = context.snapshot.as_of
    quote = context.redemption_quote
    position = next((p for p in context.positions if p.position_id == effect.position_id), None)
    product = next((p for p in context.products if p.product_id == effect.product_id), None)
    if (
        position is None
        or position.status not in {"HELD", "MATURED"}
        or position.principal_cents != effect.amount_cents
        or position.goal_id != effect.goal_id
        or effect.original_policy_version_id is None
        or effect.position_id in context.reserved_position_ids
    ):
        raise Rejected("WHOLE_ORIGINAL_POSITION_REQUIRED")
    if (
        quote is None
        or product is None
        or quote.quote_id != effect.quote_id
        or quote.user_id != effect.user_id
        or quote.position_id != effect.position_id
        or quote.product_id != effect.product_id
        or quote.product_version_number != effect.product_version_number
        or product.version_number != effect.product_version_number
        or quote.terms_digest != effect.terms_digest
        or product.terms_digest != effect.terms_digest
        or quote.principal_cents != effect.amount_cents
        or quote.fee_cents != effect.fee_cents
        or quote.loss_cents != effect.loss_cents
        or quote.net_cents != effect.net_cents
        or not quote.request_at <= now < quote.expires_at
        or quote.principal_available_at - quote.request_at
        != timedelta(days=effect.settlement_delay_days)
    ):
        raise Rejected("EXACT_VALID_REDEMPTION_QUOTE_REQUIRED")
    if quote.kind == "MATURE":
        raise Rejected("ORIGINAL_MATURITY_REQUIRES_RECONCILIATION")
    arrival = now + timedelta(days=effect.settlement_delay_days)
    if effect.latest_arrival_at is None or arrival > effect.latest_arrival_at:
        raise Rejected("PRINCIPAL_ARRIVES_AFTER_CONFIRMED_BOUND")
    if position.principal_available_at is not None and position.principal_available_at <= now:
        raise Rejected("ORIGINAL_PRINCIPAL_REQUIRES_RECONCILIATION")
    destination = next(
        (
            a
            for a in context.snapshot.cash_accounts
            if a.account_id == effect.destination_account_id
        ),
        None,
    )
    if destination is None or destination.account_type not in (
        {"CASH", "GOAL"} if effect.goal_id else {"CASH"}
    ):
        raise Rejected("INVALID_PRINCIPAL_DESTINATION")
    goal = next((g for g in context.snapshot.goals if g.goal_id == effect.goal_id), None)
    if effect.goal_id is not None and (
        goal is None or goal.account_id != effect.destination_account_id
    ):
        raise Rejected("ORIGINAL_GOAL_DESTINATION_REQUIRED")
    instant = effect.settlement_delay_days == 0
    net = quote.net_cents
    cost = quote.fee_cents + quote.loss_cents
    updated = position.model_copy(
        update={
            "status": "REDEEMED" if instant else "REDEEMING",
            "principal_cents": position.principal_cents if instant else net,
            "principal_available_at": None if instant else arrival,
        }
    )
    projected = context.snapshot.model_copy(
        update={
            "cash_accounts": [
                a.model_copy(update={"balance_cents": a.balance_cents + (net if instant else 0)})
                if a.account_id == effect.destination_account_id
                else a
                for a in context.snapshot.cash_accounts
            ],
            "goals": [
                g.model_copy(
                    update={
                        "cash_owned_cents": g.cash_owned_cents + (net if instant else 0),
                        "principal_owned_cents": g.principal_owned_cents
                        - (effect.amount_cents if instant else cost),
                        "allocated_cents": g.allocated_cents - cost,
                    }
                )
                if g.goal_id == effect.goal_id
                else g
                for g in context.snapshot.goals
            ],
        }
    )
    return projected, [
        updated if p.position_id == effect.position_id else p for p in context.positions
    ]
