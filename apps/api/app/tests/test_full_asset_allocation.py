"""Direct finite portfolio risks; these literal inputs are not product experiments."""

from datetime import timedelta
from itertools import product as cartesian_product
from typing import Any
from uuid import UUID

import pytest
from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary_types import BoundaryPolicyVersion, GoalOwnership, SourceIssue
from app.domain.full_asset_allocation import (
    FullAssetPlanningInput,
    FullAssetPlanOptions,
    plan_full_assets,
)
from app.domain.full_policy_configuration import AssetAuthorizationPolicy
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.tests.test_asset_allocation import (
    NOW,
    exposure,
    financial_products,
    product,
    snapshot,
)


def request(
    products: list[AssetProductTerms] | None = None,
    *,
    cash: int = 1000000,
    cap: int = 1000000,
    **changes: Any,
) -> FullAssetPlanningInput:
    products = [product(10), product(11, delay=1, bps=180)] if products is None else products
    return FullAssetPlanningInput(
        **{
            "snapshot": snapshot(cash).model_copy(update={"horizon_days": 365}),
            "boundary_versions": [],
            "positions": [],
            "boundary_products": financial_products(products),
            "products": products,
            "exposure": exposure(),
            "policy_id": UUID(int=2),
            "policy_version_id": UUID(int=3),
            "configuration": AssetAuthorizationPolicy(
                type="asset_authorization",
                scope="general_idle_funds",
                allowed_asset_classes=[
                    "CASH",
                    "CASH_MGMT_T0",
                    "CASH_MGMT_T1",
                    "FIXED_DEPOSIT_7D",
                    "FIXED_DEPOSIT_30D",
                    "FIXED_DEPOSIT_90D",
                    "LOW_RISK_TERM",
                ],
                max_auto_managed_cents=cash,
                single_action_cap_cents=cap,
                max_lock_days=365,
                max_redemption_delay_days=1,
                allow_auto_recovery_without_penalty=True,
            ),
            "planning_confirmation_valid": True,
            "confirmed_at": NOW - timedelta(days=1),
            "valid_from": NOW - timedelta(days=1),
            **changes,
        }
    )


def changed_product(p: AssetProductTerms, **changes: Any) -> AssetProductTerms:
    value = p.model_dump()
    value.update(changes)
    value["terms_digest"] = configuration_hash(value["maturity_rule"])
    return AssetProductTerms.model_validate(value)


def test_realistic_money_scale_does_not_enumerate_every_cent() -> None:
    result = plan_full_assets(request(cash=20000000, cap=20000000))
    assert result.status == "OPTIMAL"
    assert result.search_nodes < 100
    assert result.net_simulated_yield_cents == 20000000 * 180 * 88 // 3650000
    assert len(result.batches) == 1
    assert result.batches[0].product_id == UUID(int=11)
    assert result.planning_only and not result.bank_authority
    assert not any(candidate.bank_auto_eligible for candidate in result.candidates)
    assert result.projected_boundary is not None and result.projected_boundary.status == "READY"


@pytest.mark.parametrize("cash,cap", [(19, 11), (23, 12), (31, 15)])
def test_integer_floor_and_limits_match_an_independent_small_exhaustive_domain(
    cash: int,
    cap: int,
) -> None:
    products = [
        product(10, bps=10000),
        product(11, delay=1, bps=9000),
        product(12, term=30, bps=10000),
    ]
    req = request(products, cash=cash, cap=cap)
    result = plan_full_assets(req)
    oracle = []
    for amounts in cartesian_product(range(cap + 1), repeat=3):
        if sum(amounts) > cash or sum(amount > 0 for amount in amounts) > 3:
            continue
        net = sum(
            amounts[i] * products[i].annual_yield_bps * days // 3650000
            for i, days in enumerate((89, 88, 30))
        )
        oracle.append(
            (
                -net,
                sum(a > 0 for a in amounts),
                sum(amounts),
                amounts[1] + amounts[2] * 30,
                amounts[1],
            )
        )
    expected = min(oracle)
    actual = (
        -result.net_simulated_yield_cents if result.net_simulated_yield_cents is not None else 1,
        result.purchase_count,
        result.total_purchase_cents,
        sum(batch.amount_cents * batch.exit_plan.liquidity_days for batch in result.batches),
        sum(batch.amount_cents for batch in result.batches if batch.product_id == UUID(int=11)),
    )
    assert result.status == "OPTIMAL" and actual == expected


def test_complexity_turnover_and_each_batch_cap_can_create_a_real_combination() -> None:
    result = plan_full_assets(request(cash=1000000, cap=300000))
    assert result.purchase_count == 2
    assert all(batch.amount_cents <= 300000 for batch in result.batches)
    one = plan_full_assets(
        request(cash=1000000, cap=300000, options=FullAssetPlanOptions(max_components=1))
    )
    assert one.purchase_count == 1 and one.total_purchase_cents is not None
    tightened = plan_full_assets(
        request(cash=1000000, cap=300000, options=FullAssetPlanOptions(max_turnover_cents=100000))
    )
    assert tightened.total_purchase_cents is not None and tightened.total_purchase_cents <= 100000
    assert (
        result.net_simulated_yield_cents is not None and one.net_simulated_yield_cents is not None
    )
    assert result.net_simulated_yield_cents > one.net_simulated_yield_cents


def test_joint_locked_principal_respects_future_hard_floor_without_counting_future_income() -> None:
    products = [product(10, term=7), product(11, delay=1, bps=180)]
    config = validate_configuration({"type": "emergency_buffer", "amount_cents": 180000})
    future_floor = BoundaryPolicyVersion(
        policy_id=UUID(int=80),
        version_id=UUID(int=81),
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=NOW - timedelta(days=1),
        valid_from=NOW + timedelta(days=15),
    )
    req = request(products, cash=200000, cap=200000, boundary_versions=[future_floor])
    result = plan_full_assets(req)
    assert result.status == "OPTIMAL" and result.purchase_count == 2
    long = next(batch for batch in result.batches if batch.product_id == UUID(int=11))
    assert long.amount_cents <= 20000
    assert (
        result.projected_boundary is not None
        and result.projected_boundary.minimum_margin_cents is not None
    )
    assert result.projected_boundary.minimum_margin_cents >= 0
    assert result.future_income_included_cents == 0
    soon = plan_full_assets(
        request(
            [product(12, term=30)],
            cash=200000,
            cap=200000,
            options=FullAssetPlanOptions(funds_use_date=(NOW + timedelta(days=5)).date()),
        )
    )
    assert (
        not soon.batches
        and "PRINCIPAL_NOT_AVAILABLE_BEFORE_FUNDS_USE" in soon.candidates[0].reasons
    )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("principal_fluctuation", True, "PRINCIPAL_FLUCTUATION_NEVER_AUTO"),
        ("risk_level", 1, "RISK_LEVEL_EXCEEDS_CONFIRMED_CAP"),
        ("lock_days", 366, "LOCK_EXCEEDS_CONFIRMED_CAP"),
        ("redemption_delay_days", 2, "REDEMPTION_DELAY_EXCEEDS_CONFIRMED_CAP"),
        ("auto_purchase_allowed", False, "PRODUCT_AUTO_PURCHASE_DISABLED"),
        ("minimum_purchase_cents", 1000001, "BELOW_MINIMUM_OR_CONFIRMED_FINANCIAL_CAP"),
    ],
)
def test_each_product_rejection_is_explicit(field: str, value: object, reason: str) -> None:
    result = plan_full_assets(request([changed_product(product(10), **{field: value})]))
    assert result.status == "NO_PURCHASE"
    assert reason in result.candidates[0].reasons
    assert not result.candidates[0].bank_auto_eligible


def test_multiple_reasons_and_unsupported_fee_never_become_cash_or_recovery() -> None:
    p = product(10, term=30)
    rule = p.maturity_rule | {"yield_rule": p.maturity_rule["yield_rule"] | {"fee_cents": 1}}
    bad = changed_product(
        p, risk_level=3, principal_fluctuation=True, lock_days=400, maturity_rule=rule
    )
    result = plan_full_assets(request([bad]))
    reasons = result.candidates[0].reasons
    assert "PRINCIPAL_FLUCTUATION_NEVER_AUTO" in reasons
    assert "LOCK_EXCEEDS_CONFIRMED_CAP" in reasons and "RISK_LEVEL_EXCEEDS_CONFIRMED_CAP" in reasons
    assert len(reasons) >= 4 and not result.batches


def test_tied_yield_turnover_prefers_the_more_liquid_original_product() -> None:
    # Equal earning rate: 88 days * 8900 bps == 89 days * 8800 bps.
    products = [product(10, bps=8800), product(11, delay=1, bps=8900)]
    result = plan_full_assets(request(products, cash=2000000, cap=2000000))
    assert result.purchase_count == 1
    assert result.batches[0].product_id == UUID(int=10)


def test_old_version_rejected_and_original_digest_kept_without_catalog_rewrite() -> None:
    newer = product(10, term=30)
    old = changed_product(
        newer, product_id=UUID(int=20), version_number=1, maturity_rule={"kind": "RETURN_TO_CASH"}
    )
    original = [p.model_dump(mode="json") for p in (old, newer)]
    result = plan_full_assets(request([old, newer]))
    assert "SUPERSEDED_PRODUCT_VERSION" in result.candidates[0].reasons
    assert result.batches[0].terms_digest == newer.terms_digest
    assert [p.model_dump(mode="json") for p in (old, newer)] == original


def goal_request(days: int = 100) -> FullAssetPlanningInput:
    products = [
        product(10, term=7, bps=150),
        product(11, term=30, bps=200),
        product(12, term=90, bps=250),
    ]
    req = request(products, cash=300000, cap=100000)
    goal_id = UUID(int=50)
    owned = GoalOwnership(
        goal_id=goal_id,
        policy_id=UUID(int=51),
        account_id=UUID(int=1),
        cash_owned_cents=300000,
        principal_owned_cents=0,
        allocated_cents=300000,
    )
    return req.model_copy(
        update={
            "snapshot": req.snapshot.model_copy(update={"goals": [owned]}),
            "configuration": req.configuration.model_copy(
                update={"scope": "goal", "goal_id": goal_id}
            ),
            "exposure": exposure(scope="goal", goal_id=goal_id),
            "goal_deadline": (NOW + timedelta(days=days)).date(),
            "goal_policy_version_id": UUID(int=52),
            "goal_reference_verified": True,
            "options": FullAssetPlanOptions(mode="FIXED_LADDER", comparison_days=365),
        }
    )


def test_goal_ladder_matches_distinct_actual_maturities_and_each_batch_authorized_cap() -> None:
    result = plan_full_assets(goal_request())
    assert result.status == "OPTIMAL" and result.ladder_status == "MATCHED_MULTI_MATURITY"
    assert len(result.batches) == 3
    assert all(batch.amount_cents <= 100000 for batch in result.batches)
    assert all(
        batch.principal_available_at.date() <= (NOW + timedelta(days=100)).date()
        for batch in result.batches
    )
    assert result.projected_boundary is not None and result.projected_boundary.status == "READY"
    short = plan_full_assets(goal_request(5))
    assert not short.batches and short.ladder_status == "NO_VALID_FIXED_BATCHES"
    assert all("PRINCIPAL_NOT_AVAILABLE_BEFORE_FUNDS_USE" in c.reasons for c in short.candidates)


def test_partial_ladder_does_not_invent_unavailable_product_maturities() -> None:
    req = goal_request().model_copy(update={"products": [product(11, term=30)]})
    result = plan_full_assets(req)
    assert result.ladder_status == "SINGLE_MATURITY_AVAILABLE" and result.purchase_count == 1
    assert result.future_income_included_cents == 0


def test_source_failure_inactive_consent_and_capacity_produce_no_actionable_incumbent() -> None:
    req = request()
    bad = req.model_copy(
        update={
            "snapshot": req.snapshot.model_copy(
                update={
                    "source_issues": [SourceIssue(code="MISSING", entity_type="bank")],
                }
            )
        }
    )
    assert plan_full_assets(bad).status == "INSUFFICIENT_EVIDENCE"
    assert (
        plan_full_assets(req.model_copy(update={"planning_confirmation_valid": False})).status
        == "INACTIVE_POLICY"
    )
    capped = plan_full_assets(req.model_copy(update={"search_node_budget": 1}))
    assert capped.status == "UNKNOWN" and capped.total_purchase_cents is None and not capped.batches


def test_longer_use_window_and_old_exposure_cannot_expand_verified_current_cash() -> None:
    req = request(options=FullAssetPlanOptions(comparison_days=365))
    short = req.model_copy(
        update={"snapshot": req.snapshot.model_copy(update={"horizon_days": 90})}
    )
    result = plan_full_assets(short)
    assert result.status == "UNKNOWN" and result.total_purchase_cents is None
    stale = req.model_copy(
        update={"exposure": req.exposure.model_copy(update={"as_of": NOW - timedelta(days=1)})}
    )
    result = plan_full_assets(stale)
    assert result.status == "INSUFFICIENT_EVIDENCE" and not result.batches


def test_principal_terms_hash_mismatch_and_authority_override_are_rejected() -> None:
    with pytest.raises(ValueError):
        changed_product(product(10), terms_digest="0" * 64).model_validate(
            product(10).model_dump() | {"terms_digest": "0" * 64}
        )
    with pytest.raises(ValueError):
        FullAssetPlanOptions.model_validate({"max_components": True})
    with pytest.raises(ValueError):
        FullAssetPlanOptions.model_validate({"bank_authority": True})
