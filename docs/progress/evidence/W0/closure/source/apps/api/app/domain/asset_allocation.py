"""Single-product simulated selection with explicit principal exit plans."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, timedelta, timezone
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from app.domain.asset_allocation_types import (
    AssetAllocationResult as AssetAllocationResult,
)
from app.domain.asset_allocation_types import (
    AssetCandidate as AssetCandidate,
)
from app.domain.asset_allocation_types import (
    AssetCashUse as AssetCashUse,
)
from app.domain.asset_allocation_types import (
    AssetProductTerms as AssetProductTerms,
)
from app.domain.asset_allocation_types import FixedPrincipalTerms, PlannedPrincipalTerms
from app.domain.asset_allocation_types import (
    PlannedExit as PlannedExit,
)
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundarySnapshot,
    SourceIssue,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration

ALGORITHM_VERSION = "single-product-allocation-v1"


def select_asset(
    snapshot: BoundarySnapshot,
    boundary_versions: Sequence[BoundaryPolicyVersion],
    positions: Sequence[BoundaryPosition],
    boundary_products: Sequence[BoundaryProduct],
    authorization: BoundaryPolicyVersion,
    products: Sequence[AssetProductTerms],
    exposure: AssetExposure,
    *,
    source_issues: Sequence[SourceIssue] = (),
) -> AssetAllocationResult:
    """Select at most one hypothetical position; never issue or reserve a purchase."""
    if len(products) > 100 or len(source_issues) > 1000:
        raise ValueError("Allocation capacity is 100 products and 1000 source issues")
    snapshot = BoundarySnapshot.model_validate(snapshot.model_dump(warnings=False))
    authorization = BoundaryPolicyVersion.model_validate(authorization.model_dump(warnings=False))
    authorization = authorization.model_copy(
        update={"evidence_ids": sorted(authorization.evidence_ids)}
    )
    exposure = AssetExposure.model_validate(exposure.model_dump(warnings=False))
    products = [AssetProductTerms.model_validate(p.model_dump(warnings=False)) for p in products]
    source_issues = sorted(
        (SourceIssue.model_validate(item.model_dump(warnings=False)) for item in source_issues),
        key=lambda item: (item.code, item.entity_type, item.entity_id or ""),
    )
    if len({p.product_id for p in products}) != len(products) or len(
        {(p.product_code, p.version_number) for p in products}
    ) != len(products):
        raise ValueError("Product identities and code/version pairs must be unique")
    exposure_lists = {
        name: getattr(exposure, name)
        for name in (
            "counted_position_ids",
            "counted_action_ids",
            "excluded_manual_position_ids",
            "evidence_ids",
        )
    }
    for values in exposure_lists.values():
        if len(values) > 100000 or len(set(values)) != len(values):
            raise ValueError("Exposure identities must be unique and within capacity")
    exposure = exposure.model_copy(
        update={name: sorted(values) for name, values in exposure_lists.items()}
    )
    config = validate_configuration(authorization.configuration)
    if config["type"] != "asset_authorization":
        raise ValueError("An asset_authorization configuration is required")
    if configuration_hash(config) != authorization.content_hash:
        raise ValueError("Asset authorization hash does not match its configuration")
    baseline = compute_boundary(snapshot, boundary_versions, positions, boundary_products)
    goal_id = UUID(config["goal_id"]) if config["scope"] == "goal" else None
    input_hash = configuration_hash(
        {
            "baseline": baseline.boundary_hash,
            "authorization": authorization.model_dump(mode="json"),
            "exposure": exposure.model_dump(mode="json"),
            "products": [
                p.model_dump(mode="json") for p in sorted(products, key=lambda p: p.product_id)
            ],
            "source_issues": [issue.model_dump(mode="json") for issue in source_issues],
        }
    )

    def blocked(
        status: Literal["INSUFFICIENT_EVIDENCE", "LIQUIDITY_RISK", "INACTIVE_POLICY"], reason: str
    ) -> AssetAllocationResult:
        return AssetAllocationResult(
            algorithm_version=ALGORITHM_VERSION,
            policy_id=authorization.policy_id,
            policy_version_id=authorization.version_id,
            scope=config["scope"],
            goal_id=goal_id,
            status=status,
            baseline_boundary=baseline,
            selection_hash=input_hash,
            reasons=[reason],
        )

    if source_issues:
        return blocked("INSUFFICIENT_EVIDENCE", "SOURCE_EVIDENCE_INCOMPLETE")
    if baseline.status != "READY":
        return blocked(baseline.status, "BASELINE_" + baseline.status)
    by_position = {item.position_id: item for item in positions}
    counted = set(exposure.counted_position_ids)
    excluded = set(exposure.excluded_manual_position_ids)
    if not (counted | excluded) <= set(by_position) or counted & excluded:
        raise ValueError("Exposure position identities contradict the financial snapshot")
    scope_positions = {
        item.position_id
        for item in positions
        if item.goal_id == goal_id and item.status != "REDEEMED"
    }
    if not scope_positions <= counted | excluded:
        raise ValueError(
            "Every existing scope position needs an explicit acquisition classification"
        )
    if (
        any(
            by_position[identifier].goal_id != goal_id
            or by_position[identifier].status not in {"HELD", "REDEEMING", "MATURED"}
            for identifier in counted
        )
        or sum(by_position[identifier].principal_cents for identifier in counted)
        != exposure.managed_principal_cents
    ):
        raise ValueError("Managed exposure must equal the identified unsettled principal")
    if not _active_now(authorization, snapshot):
        return blocked("INACTIVE_POLICY", "ASSET_AUTHORIZATION_NOT_CURRENTLY_EFFECTIVE")
    if (
        exposure.as_of > snapshot.as_of
        or any(a.observed_at > exposure.as_of for a in snapshot.cash_accounts)
        or exposure.scope != config["scope"]
        or exposure.goal_id != goal_id
    ):
        return blocked("INSUFFICIENT_EVIDENCE", "EXPOSURE_SCOPE_OR_EPOCH_MISMATCH")
    working = _reserve_snapshot(snapshot, exposure)
    reserved_baseline = compute_boundary(working, boundary_versions, positions, boundary_products)
    if reserved_baseline.status != "READY":
        return blocked(
            reserved_baseline.status, "RESERVED_CASH_" + reserved_baseline.status
        ).model_copy(update={"reservation_adjusted_boundary": reserved_baseline})
    comparison_days = 90
    owned = {
        account.account_id: sum(
            g.cash_owned_cents for g in snapshot.goals if g.account_id == account.account_id
        )
        for account in snapshot.cash_accounts
    }
    funding = {
        account.account_id: account.balance_cents - owned[account.account_id]
        for account in sorted(working.cash_accounts, key=lambda a: a.account_id)
        if account.account_type == "CASH"
    }
    if goal_id is not None:
        goal = next((g for g in snapshot.goals if g.goal_id == goal_id), None)
        if goal is None or goal.account_id is None:
            return blocked("INSUFFICIENT_EVIDENCE", "MISSING_GOAL_CASH_OWNERSHIP")
        goal_version = next((v for v in boundary_versions if v.policy_id == goal.policy_id), None)
        if goal_version is None or not _active_now(goal_version, snapshot):
            return blocked("INACTIVE_POLICY", "GOAL_AUTHORIZATION_NOT_CURRENTLY_EFFECTIVE")
        goal_config = validate_configuration(goal_version.configuration)
        if goal_config["type"] != "goal_saving" or goal_config["asset_policy_id"] != str(
            authorization.policy_id
        ):
            return blocked("INACTIVE_POLICY", "GOAL_ASSET_AUTHORIZATION_REFERENCE_MISMATCH")
        zone = UTC if snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
        if date.fromisoformat(goal_config["deadline"]) < snapshot.as_of.astimezone(zone).date():
            return blocked("INACTIVE_POLICY", "GOAL_DEADLINE_PASSED")
        comparison_days = min(
            90,
            max(
                0,
                (
                    date.fromisoformat(goal_version.configuration["deadline"])
                    - snapshot.as_of.astimezone(zone).date()
                ).days
                - 1,
            ),
        )
        if goal.account_id is None:
            raise ValueError("A goal cash account is required")
        funding = {
            goal.account_id: goal.cash_owned_cents
            - exposure.reserved_goal_cash_by_goal.get(goal_id, 0)
        }
    scope_cash = sum(funding.values())
    remaining = max(
        0,
        config["max_auto_managed_cents"]
        - exposure.managed_principal_cents
        - exposure.pending_purchase_cents,
    )
    candidates = []
    states = {}
    current_versions: dict[str, int] = {}
    for product in products:
        if _known_product(product, snapshot):
            current_versions[product.product_code] = max(
                current_versions.get(product.product_code, 0), product.version_number
            )
    for product in sorted(products, key=lambda p: (p.product_code, p.version_number, p.product_id)):
        try:
            if not _known_product(product, snapshot):
                raise ValueError("PRODUCT_NOT_CURRENTLY_KNOWN_AND_EFFECTIVE")
            if product.version_number != current_versions[product.product_code]:
                raise ValueError("SUPERSEDED_PRODUCT_VERSION")
            plan = _exit_plan(product, snapshot, authorization, config, comparison_days)
        except ValueError as error:
            candidates.append(
                AssetCandidate(
                    product_id=product.product_id,
                    product_code=product.product_code,
                    version_number=product.version_number,
                    asset_class=product.asset_class,
                    status="REJECTED",
                    reasons=[str(error)],
                )
            )
            continue
        lower, upper = 0, scope_cash
        while lower < upper:
            trial = (lower + upper + 1) // 2
            projected, projected_positions, _ = _project(
                working, positions, product, plan, trial, funding, goal_id
            )
            checked = compute_boundary(
                projected, boundary_versions, projected_positions, boundary_products
            )
            if checked.status == "READY":
                lower = trial
            else:
                upper = trial - 1
        amount = min(lower, remaining, config["single_action_cap_cents"])
        if amount == 0 or amount < product.minimum_purchase_cents:
            candidates.append(
                AssetCandidate(
                    product_id=product.product_id,
                    product_code=product.product_code,
                    version_number=product.version_number,
                    asset_class=product.asset_class,
                    status="REJECTED",
                    financial_cap_cents=lower,
                    max_allocatable_cents=0,
                    exit_plan=plan,
                    reasons=["BELOW_MINIMUM_PURCHASE"],
                )
            )
            continue
        projected, projected_positions, uses = _project(
            working, positions, product, plan, amount, funding, goal_id
        )
        checked = compute_boundary(
            projected, boundary_versions, projected_positions, boundary_products
        )
        candidate = AssetCandidate(
            product_id=product.product_id,
            product_code=product.product_code,
            version_number=product.version_number,
            asset_class=product.asset_class,
            status="FEASIBLE",
            financial_cap_cents=lower,
            max_allocatable_cents=amount,
            net_simulated_yield_cents=amount
            * product.annual_yield_bps
            * plan.earning_days
            // (10000 * 365),
            exit_plan=plan,
            candidate_boundary_hash=checked.boundary_hash,
        )
        candidates.append(candidate)
        states[product.product_id] = (checked, uses)
    profitable = [
        candidate for candidate in candidates if (candidate.net_simulated_yield_cents or 0) > 0
    ]
    product_delays = {p.product_id: p.redemption_delay_days for p in products}
    best = (
        min(
            profitable,
            key=lambda c: (
                -(c.net_simulated_yield_cents or 0),
                c.exit_plan.liquidity_days if c.exit_plan else 0,
                product_delays[c.product_id],
                c.product_code,
                c.version_number,
                c.product_id,
            ),
        )
        if profitable
        else None
    )
    checked, uses = states[best.product_id] if best is not None else (reserved_baseline, [])
    amount = (best.max_allocatable_cents or 0) if best is not None else 0
    return AssetAllocationResult(
        algorithm_version=ALGORITHM_VERSION,
        policy_id=authorization.policy_id,
        policy_version_id=authorization.version_id,
        scope=config["scope"],
        goal_id=goal_id,
        status="READY",
        selected_asset_class=best.asset_class if best is not None else "CASH",
        selected_product_id=best.product_id if best is not None else None,
        suggested_cents=amount,
        retained_cash_cents=scope_cash - amount,
        comparison_days=comparison_days,
        scope_cash_cents=scope_cash,
        remaining_managed_cents=remaining,
        net_simulated_yield_cents=best.net_simulated_yield_cents if best is not None else 0,
        source_cash_uses=uses,
        candidates=candidates,
        baseline_boundary=baseline,
        reservation_adjusted_boundary=reserved_baseline,
        candidate_boundary=checked,
        selection_hash=configuration_hash(
            {"input": input_hash, "candidates": [c.model_dump(mode="json") for c in candidates]}
        ),
    )


def _reserve_snapshot(snapshot: BoundarySnapshot, exposure: AssetExposure) -> BoundarySnapshot:
    accounts = {account.account_id: account for account in snapshot.cash_accounts}
    goals = {goal.goal_id: goal for goal in snapshot.goals}
    goal_reserves: dict[UUID, int] = {}
    for goal_id, amount in exposure.reserved_goal_cash_by_goal.items():
        goal = goals.get(goal_id)
        if goal is None or goal.account_id not in accounts or amount > goal.cash_owned_cents:
            raise ValueError("Reserved goal cash must be backed by an identified goal cash account")
        assert goal.account_id is not None
        goal_reserves[goal.account_id] = goal_reserves.get(goal.account_id, 0) + amount
    general: dict[UUID, int] = {}
    for identity in set(exposure.reserved_cash_by_account) | set(goal_reserves):
        account = accounts.get(identity)
        total = exposure.reserved_cash_by_account.get(identity, 0)
        if (
            account is None
            or account.account_type not in {"CASH", "GOAL"}
            or total > account.balance_cents
        ):
            raise ValueError("Pending cash reservations exceed their actual source account")
        amount = total - goal_reserves.get(identity, 0)
        owned = sum(goal.cash_owned_cents for goal in snapshot.goals if goal.account_id == identity)
        if (
            amount < 0
            or amount > account.balance_cents - owned
            or (amount and account.account_type != "CASH")
        ):
            raise ValueError("General reservations must use unowned CASH, not protected goal funds")
        general[identity] = amount
    if exposure.scope == "general_idle_funds":
        expected = sum(general.values())
    else:
        if exposure.goal_id is None:
            raise ValueError("Goal exposure needs an identified goal")
        expected = exposure.reserved_goal_cash_by_goal.get(exposure.goal_id, 0)
    if expected != exposure.pending_purchase_cents:
        raise ValueError("Pending managed exposure must match the selected scope reservations")
    return snapshot.model_copy(
        update={
            "cash_accounts": [
                account.model_copy(
                    update={
                        "balance_cents": account.balance_cents - general.get(account.account_id, 0)
                    }
                )
                for account in snapshot.cash_accounts
            ]
        }
    )


def _known_product(product: AssetProductTerms, snapshot: BoundarySnapshot) -> bool:
    return (
        product.created_at <= snapshot.as_of
        and product.effective_from <= snapshot.as_of
        and (product.effective_until is None or snapshot.as_of < product.effective_until)
    )


def _active_now(version: BoundaryPolicyVersion, snapshot: BoundarySnapshot) -> bool:
    return max(version.confirmed_at, version.valid_from) <= snapshot.as_of and (
        version.valid_until is None or snapshot.as_of < version.valid_until
    )


def _exit_plan(
    product: AssetProductTerms,
    snapshot: BoundarySnapshot,
    authorization: BoundaryPolicyVersion,
    config: dict[str, Any],
    horizon: int,
) -> PlannedExit:
    if product.asset_class not in config["allowed_asset_classes"] or product.asset_class == "CASH":
        raise ValueError("ASSET_CLASS_NOT_AUTHORIZED_FOR_PURCHASE")
    if product.principal_fluctuation or product.risk_level > config["max_principal_risk_level"]:
        raise ValueError("PRINCIPAL_RISK_NOT_AUTHORIZED")
    if not product.auto_purchase_allowed:
        raise ValueError("PRODUCT_AUTO_PURCHASE_DISABLED")
    if (
        product.lock_days > config["max_lock_days"]
        or product.redemption_delay_days > config["max_redemption_delay_days"]
    ):
        raise ValueError("LOCK_OR_REDEMPTION_EXCEEDS_AUTHORIZATION")
    try:
        terms: FixedPrincipalTerms | PlannedPrincipalTerms
        if product.maturity_rule.get("protocol") == "fixed-principal-return-v1":
            terms = FixedPrincipalTerms.model_validate(product.maturity_rule)
        else:
            terms = PlannedPrincipalTerms.model_validate(product.maturity_rule)
    except ValueError as error:
        raise ValueError("INCOMPLETE_OR_UNSUPPORTED_PRODUCT_TERMS") from error
    if (
        terms.yield_rule.annual_yield_bps != product.annual_yield_bps
        or terms.settlement_delay_days != product.redemption_delay_days
    ):
        raise ValueError("PRODUCT_TERMS_CONTRADICT_CATALOG")
    if isinstance(terms, FixedPrincipalTerms):
        if (
            product.asset_class != "FIXED_DEPOSIT"
            or product.risk_level != 0
            or terms.term_days < product.lock_days
            or terms.yield_rule.accrual != "UNTIL_MATURITY"
        ):
            raise ValueError("FIXED_TERMS_CONTRADICT_CATALOG")
        earning = terms.term_days
        request_at = None
        kind: Literal["FIXED_MATURITY", "PLANNED_REDEMPTION"] = "FIXED_MATURITY"
        liquidity = terms.term_days + terms.settlement_delay_days
    else:
        if (
            product.asset_class not in {"CASH_MGMT_T0", "CASH_MGMT_T1"}
            or terms.yield_rule.accrual != "UNTIL_REDEMPTION_REQUEST"
        ):
            raise ValueError("PLANNED_TERMS_CONTRADICT_CATALOG")
        if not product.auto_redeem_allowed or not config["allow_auto_recovery_without_penalty"]:
            raise ValueError("PLANNED_REDEMPTION_NOT_AUTHORIZED")
        earning = horizon - terms.settlement_delay_days
        if authorization.valid_until is not None:
            delta = authorization.valid_until - snapshot.as_of
            # Exact integer microseconds; the request must strictly precede expiry.
            microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
            earning = min(earning, (microseconds - 1) // (86400 * 1_000_000))
        if earning < product.lock_days:
            raise ValueError("NO_AUTHORIZED_REQUEST_AFTER_LOCK")
        request_at = snapshot.as_of + timedelta(days=earning)
        kind = "PLANNED_REDEMPTION"
        liquidity = product.lock_days + terms.settlement_delay_days
    available_days = earning + terms.settlement_delay_days
    if horizon <= 0 or available_days <= 0 or available_days > horizon:
        raise ValueError("EXIT_OUTSIDE_COMPARISON_WINDOW")
    return PlannedExit(
        kind=kind,
        request_at=request_at,
        principal_available_at=snapshot.as_of + timedelta(days=available_days),
        earning_days=earning,
        liquidity_days=liquidity,
        terms_digest=product.terms_digest,
    )


def _project(
    snapshot: BoundarySnapshot,
    positions: Sequence[BoundaryPosition],
    product: AssetProductTerms,
    plan: PlannedExit,
    amount: int,
    funding: dict[UUID, int],
    goal_id: UUID | None = None,
) -> tuple[BoundarySnapshot, list[BoundaryPosition], list[AssetCashUse]]:
    remaining = amount
    uses = []
    for identity, available in sorted(funding.items()):
        used = min(remaining, available)
        if used:
            uses.append(AssetCashUse(account_id=identity, amount_cents=used))
            remaining -= used
    if remaining:
        raise ValueError("Purchase exceeds its cash funding scope")
    debits = {use.account_id: use.amount_cents for use in uses}
    projected = snapshot.model_copy(
        update={
            "cash_accounts": [
                account.model_copy(
                    update={
                        "balance_cents": account.balance_cents - debits.get(account.account_id, 0)
                    }
                )
                for account in snapshot.cash_accounts
            ],
            "goals": [
                goal.model_copy(
                    update={
                        "cash_owned_cents": goal.cash_owned_cents - amount,
                        "principal_owned_cents": goal.principal_owned_cents + amount,
                    }
                )
                if goal.goal_id == goal_id
                else goal
                for goal in snapshot.goals
            ],
        }
    )
    identity = uuid5(
        NAMESPACE_URL,
        f"bounded-funds:asset-preview:{snapshot.as_of.isoformat()}:{product.product_id}:{goal_id}:{amount}:{plan.principal_available_at.isoformat()}",
    )
    projected_positions = list(positions)
    if amount:
        projected_positions.append(
            BoundaryPosition(
                position_id=identity,
                principal_cents=amount,
                goal_id=goal_id,
                status="HELD",
                principal_available_at=plan.principal_available_at,
            )
        )
    return projected, projected_positions, uses
