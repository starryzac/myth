"""Literal financial and permission cases for the public single-product preview."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.asset_allocation import AssetProductTerms, select_asset
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundaryProduct,
    BoundarySnapshot,
    CashFact,
    FixedReturnTerms,
    GoalMonthFact,
    GoalOwnership,
    SourceIssue,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration

NOW = datetime(2026, 10, 4, 8, tzinfo=UTC)
ACCOUNT, POLICY, VERSION = (UUID(int=n) for n in (1, 2, 3))


def authorization(**changes: Any) -> BoundaryPolicyVersion:
    config = validate_configuration(
        {
            "type": "asset_authorization",
            "scope": "general_idle_funds",
            "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
            "max_auto_managed_cents": 1_000_000,
            "single_action_cap_cents": 1_000_000,
            "max_redemption_delay_days": 1,
            "max_lock_days": 90,
            "allow_auto_recovery_without_penalty": True,
            **changes,
        }
    )
    return BoundaryPolicyVersion(
        policy_id=POLICY,
        version_id=VERSION,
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=NOW - timedelta(days=1),
        valid_from=NOW - timedelta(days=1),
    )


def product(
    identity: int, *, delay: int = 0, term: int | None = None, bps: int = 150
) -> AssetProductTerms:
    rule: dict[str, Any] = {
        "protocol": "planned-principal-return-v1" if term is None else "fixed-principal-return-v1",
        "day_basis": "CALENDAR",
        "guaranteed": True,
        "settlement_delay_days": delay,
        "principal_return_bps": 10000,
        "rollover": False,
        "auto_rollover": False,
        "yield_rule": {
            "protocol": "simple-annual-yield-v1",
            "basis": "ACT_365",
            "annual_yield_bps": bps,
            "simulation": True,
            "fee_cents": 0,
            "purchase_fee_bps": 0,
            "redemption_fee_bps": 0,
            "accrual": "UNTIL_REDEMPTION_REQUEST" if term is None else "UNTIL_MATURITY",
        },
    }
    if term is not None:
        rule["term_days"] = term
    return AssetProductTerms(
        product_id=UUID(int=identity),
        product_code=f"P{identity}",
        version_number=2,
        asset_class="FIXED_DEPOSIT"
        if term is not None
        else ("CASH_MGMT_T1" if delay else "CASH_MGMT_T0"),
        risk_level=0,
        principal_fluctuation=False,
        minimum_purchase_cents=1,
        lock_days=term or 0,
        redemption_delay_days=delay,
        annual_yield_bps=bps,
        early_withdrawal_loss_bps=20 if term else 0,
        auto_purchase_allowed=True,
        auto_redeem_allowed=term is None,
        created_at=NOW - timedelta(days=1),
        effective_from=NOW - timedelta(days=1),
        maturity_rule=rule,
        terms_digest=configuration_hash(rule),
    )


def financial_products(products: list[AssetProductTerms]) -> list[BoundaryProduct]:
    return [
        BoundaryProduct(
            product_id=p.product_id,
            version_number=p.version_number,
            asset_class=p.asset_class,
            minimum_purchase_cents=p.minimum_purchase_cents,
            fixed_return=FixedReturnTerms(
                term_days=p.maturity_rule["term_days"],
                settlement_delay_days=p.redemption_delay_days,
            )
            if "term_days" in p.maturity_rule
            else None,
            terms_digest="financial-terms",
        )
        for p in products
    ]


def snapshot(cash: int = 1_000_000) -> BoundarySnapshot:
    return BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(account_id=ACCOUNT, account_type="CASH", balance_cents=cash, observed_at=NOW)
        ],
    )


def exposure(**changes: Any) -> AssetExposure:
    return AssetExposure(
        **{
            "as_of": NOW,
            "scope": "general_idle_funds",
            "goal_id": None,
            "managed_principal_cents": 0,
            "pending_purchase_cents": 0,
            **changes,
        }
    )


def test_common_ninety_day_window_selects_one_product_using_its_actual_exit() -> None:
    products = [product(10), product(11, delay=1, bps=180), product(12, term=30, bps=220)]
    result = select_asset(
        snapshot(), [], [], financial_products(products), authorization(), products, exposure()
    )
    assert result.status == "READY"
    assert result.preview_only and result.financial_only
    assert result.selected_product_id == UUID(int=11)
    assert result.suggested_cents == 1_000_000
    assert result.net_simulated_yield_cents == 4389
    assert {
        candidate.product_id.int: candidate.net_simulated_yield_cents
        for candidate in result.candidates
    } == {
        10: 3698,
        11: 4389,
        12: 1808,
    }
    assert result.candidate_boundary is not None
    assert len(result.candidate_boundary.calculation_trace) == 273
    assert result.candidate_boundary.calculation_trace[0].cash_cents == 0
    assert result.candidate_boundary.calculation_trace[-1].cash_cents == 1_000_000
    assert result.source_cash_uses[0].amount_cents == 1_000_000


def goal_context(
    days: int = 5,
) -> tuple[BoundarySnapshot, list[BoundaryPolicyVersion], BoundaryPolicyVersion, AssetExposure]:
    goal_id, goal_policy_id, goal_version_id = (UUID(int=n) for n in (50, 51, 52))
    config = validate_configuration(
        {
            "type": "goal_saving",
            "target_cents": 2_000_000,
            "deadline": (NOW + timedelta(days=days + 1)).date().isoformat(),
            "monthly_contribution": {"min_cents": 0, "target_cents": 0, "max_cents": 0},
            "asset_policy_id": str(POLICY),
        }
    )
    version = BoundaryPolicyVersion(
        policy_id=goal_policy_id,
        version_id=goal_version_id,
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=NOW - timedelta(days=1),
        valid_from=NOW - timedelta(days=1),
    )
    base = snapshot().model_copy(
        update={
            "goals": [
                GoalOwnership(
                    goal_id=goal_id,
                    policy_id=goal_policy_id,
                    account_id=ACCOUNT,
                    cash_owned_cents=1_000_000,
                    principal_owned_cents=0,
                    allocated_cents=1_000_000,
                )
            ],
            "goal_month_contributions": [
                GoalMonthFact(goal_id=goal_id, period="2026-10", contributed_cents=0)
            ],
        }
    )
    return (
        base,
        [version],
        authorization(scope="goal", goal_id=str(goal_id)),
        exposure(scope="goal", goal_id=goal_id),
    )


def test_goal_owned_cash_can_be_placed_without_borrowing_the_general_idle_cap() -> None:
    base, versions, auth, scope_exposure = goal_context()
    products = [product(10), product(11, delay=1, bps=180), product(12, term=30, bps=220)]
    result = select_asset(
        base, versions, [], financial_products(products), auth, products, scope_exposure
    )
    assert result.baseline_boundary.safe_idle_cents == 0
    assert result.status == "READY" and result.selected_product_id == UUID(int=10)
    assert result.comparison_days == 5 and result.suggested_cents == 1_000_000
    assert result.net_simulated_yield_cents == 205
    assert result.candidates[1].net_simulated_yield_cents == 197
    assert result.candidates[2].status == "REJECTED"
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.minimum_margin_cents == 0
    assert (
        result.candidate_boundary.calculation_trace[0].protected_cents_by_reason["goal_cash"] == 0
    )
    assert (
        result.candidate_boundary.calculation_trace[-1].protected_cents_by_reason["goal_cash"]
        == 1_000_000
    )
    assert base.goals[0].allocated_cents == base.goals[0].cash_owned_cents == 1_000_000


def test_zero_rounded_yield_prefers_cash_as_a_zero_operation() -> None:
    products = [product(10, bps=1)]
    result = select_asset(
        snapshot(1), [], [], financial_products(products), authorization(), products, exposure()
    )
    assert result.status == "READY" and result.selected_asset_class == "CASH"
    assert result.selected_product_id is None and result.suggested_cents == 0
    assert result.retained_cash_cents == 1 and result.source_cash_uses == []


@pytest.mark.parametrize(
    "defect",
    [
        "legacy",
        "fee",
        "float_guarantee",
        "risk",
        "fluctuation",
        "purchase",
        "redeem",
        "lock",
        "delay",
        "minimum",
    ],
)
def test_product_constraints_reject_individual_candidates_without_forcing_a_purchase(
    defect: str,
) -> None:
    p = product(10)
    if defect in {"legacy", "fee", "float_guarantee"}:
        rule = {**p.maturity_rule, "yield_rule": dict(p.maturity_rule["yield_rule"])}
        if defect == "legacy":
            rule = {"kind": "RETURN_TO_CASH", "auto_rollover": False}
        elif defect == "fee":
            rule["yield_rule"]["fee_cents"] = 1
        else:
            rule["principal_return_bps"] = 10000.0
        p = p.model_copy(update={"maturity_rule": rule, "terms_digest": configuration_hash(rule)})
    else:
        changes_by_defect: dict[str, dict[str, Any]] = {
            "risk": {"risk_level": 1},
            "fluctuation": {"principal_fluctuation": True},
            "purchase": {"auto_purchase_allowed": False},
            "redeem": {"auto_redeem_allowed": False},
            "lock": {"lock_days": 91},
            "delay": {"redemption_delay_days": 2},
            "minimum": {"minimum_purchase_cents": 1_000_001},
        }
        p = p.model_copy(update=changes_by_defect[defect])
    result = select_asset(
        snapshot(), [], [], financial_products([p]), authorization(), [p], exposure()
    )
    assert result.status == "READY" and result.selected_asset_class == "CASH"
    assert result.suggested_cents == 0
    assert result.candidates[0].status == "REJECTED"
    assert result.candidates[0].reasons


def test_planned_request_before_authorization_expiry_is_used_for_both_safety_and_yield() -> None:
    auth = authorization().model_copy(update={"valid_until": NOW + timedelta(days=6)})
    rule = validate_configuration({"type": "emergency_buffer", "amount_cents": 500_000})
    future_floor = BoundaryPolicyVersion(
        policy_id=UUID(int=100),
        version_id=UUID(int=101),
        configuration=rule,
        content_hash=configuration_hash(rule),
        confirmed_at=NOW,
        valid_from=NOW + timedelta(days=7),
    )
    p = product(11, delay=1, bps=180)
    result = select_asset(
        snapshot(), [future_floor], [], financial_products([p]), auth, [p], exposure()
    )
    assert result.suggested_cents == 1_000_000
    assert result.net_simulated_yield_cents == 246
    assert result.candidates[0].exit_plan is not None
    assert result.candidates[0].exit_plan.request_at == NOW + timedelta(days=5)
    assert result.candidates[0].exit_plan.principal_available_at == NOW + timedelta(days=6)


@pytest.mark.parametrize("failure", ["sources", "risk", "inactive", "goal_reference"])
def test_missing_or_unauthorized_context_cannot_return_precise_asset_amounts(failure: str) -> None:
    base, versions, auth, proof = snapshot(), [], authorization(), exposure()
    issues = []
    if failure == "sources":
        issues = [SourceIssue(code="MISSING_EXPOSURE", entity_type="exposure")]
    elif failure == "risk":
        config = validate_configuration({"type": "emergency_buffer", "amount_cents": 2_000_000})
        versions = [
            BoundaryPolicyVersion(
                policy_id=UUID(int=100),
                version_id=UUID(int=101),
                configuration=config,
                content_hash=configuration_hash(config),
                confirmed_at=NOW,
                valid_from=NOW,
            )
        ]
    elif failure == "inactive":
        auth = auth.model_copy(update={"valid_until": NOW})
    else:
        base, versions, auth, proof = goal_context()
        config = {**versions[0].configuration, "asset_policy_id": str(UUID(int=999))}
        versions[0] = versions[0].model_copy(
            update={"configuration": config, "content_hash": configuration_hash(config)}
        )
    p = product(10)
    result = select_asset(
        base, versions, [], financial_products([p]), auth, [p], proof, source_issues=issues
    )
    assert (
        result.status
        == {
            "sources": "INSUFFICIENT_EVIDENCE",
            "risk": "LIQUIDITY_RISK",
            "inactive": "INACTIVE_POLICY",
            "goal_reference": "INACTIVE_POLICY",
        }[failure]
    )
    assert result.suggested_cents is None and result.selected_product_id is None
    assert result.candidate_boundary is None


def test_immutable_legacy_catalog_is_superseded_only_by_currently_known_effective_version() -> None:
    current = product(10)
    legacy_rule = {"kind": "RETURN_TO_CASH", "auto_rollover": False}
    old = current.model_copy(
        update={
            "product_id": UUID(int=9),
            "version_number": 1,
            "maturity_rule": legacy_rule,
            "terms_digest": configuration_hash(legacy_rule),
        }
    )
    future = current.model_copy(
        update={
            "product_id": UUID(int=11),
            "version_number": 3,
            "created_at": NOW + timedelta(seconds=1),
        }
    )
    rows = [future, old, current]
    result = select_asset(
        snapshot(), [], [], financial_products(rows), authorization(), rows, exposure()
    )
    assert result.selected_product_id == current.product_id
    rejected = {c.product_id: c for c in result.candidates if c.status == "REJECTED"}
    assert "SUPERSEDED_PRODUCT_VERSION" in rejected[old.product_id].reasons
    assert "PRODUCT_NOT_CURRENTLY_KNOWN_AND_EFFECTIVE" in rejected[future.product_id].reasons
    assert old.maturity_rule == legacy_rule


def test_general_pending_cash_is_reserved_across_the_whole_window() -> None:
    config = validate_configuration({"type": "emergency_buffer", "amount_cents": 150_000})
    version = BoundaryPolicyVersion(
        policy_id=UUID(int=100),
        version_id=UUID(int=101),
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=NOW,
        valid_from=NOW,
    )
    proof = exposure(
        pending_purchase_cents=200_000,
        reserved_cash_by_account={ACCOUNT: 200_000},
        counted_action_ids=[UUID(int=20)],
    )
    p = product(10)
    result = select_asset(
        snapshot(), [version], [], financial_products([p]), authorization(), [p], proof
    )
    assert result.suggested_cents == 650_000
    assert result.reservation_adjusted_boundary is not None
    assert result.reservation_adjusted_boundary.calculation_trace[0].cash_cents == 800_000
    assert result.baseline_boundary.calculation_trace[0].cash_cents == 1_000_000


def test_pending_goal_purchase_is_not_reserved_twice_as_goal_cash_and_general_cash() -> None:
    base, versions, auth, proof = goal_context()
    proof = proof.model_copy(
        update={
            "pending_purchase_cents": 200_000,
            "reserved_cash_by_account": {ACCOUNT: 200_000},
            "reserved_goal_cash_by_goal": {UUID(int=50): 200_000},
            "counted_action_ids": [UUID(int=20)],
        }
    )
    p = product(10)
    result = select_asset(base, versions, [], financial_products([p]), auth, [p], proof)
    assert result.suggested_cents == result.scope_cash_cents == 800_000
    assert result.reservation_adjusted_boundary == result.baseline_boundary
    assert result.candidate_boundary is not None
    assert result.candidate_boundary.minimum_margin_cents == 0


@pytest.mark.parametrize("epoch_offset,expected", [(0, "READY"), (-1, "INSUFFICIENT_EVIDENCE")])
def test_exposure_epoch_covers_economic_facts_without_following_the_read_clock(
    epoch_offset: int, expected: str
) -> None:
    base = snapshot().model_copy(update={"as_of": NOW + timedelta(hours=1)})
    proof = exposure().model_copy(update={"as_of": NOW + timedelta(seconds=epoch_offset)})
    p = product(10)
    result = select_asset(base, [], [], financial_products([p]), authorization(), [p], proof)
    assert result.status == expected
    if expected == "READY":
        assert result.suggested_cents == 1_000_000
    else:
        assert result.suggested_cents is None


@pytest.mark.parametrize("term,kind", [(None, "REDEEM_ON_REQUEST"), (30, "RETURN_TO_CASH")])
def test_explicit_catalog_kind_matches_the_principal_protocol(term: int | None, kind: str) -> None:
    p = product(10, term=term)
    rule = {**p.maturity_rule, "kind": kind}
    p = p.model_copy(update={"maturity_rule": rule, "terms_digest": configuration_hash(rule)})
    result = select_asset(
        snapshot(), [], [], financial_products([p]), authorization(), [p], exposure()
    )
    assert result.selected_product_id == p.product_id


def test_equal_profit_and_liquidity_break_ties_by_shorter_redemption_delay() -> None:
    planned = product(10, delay=1, bps=1231).model_copy(update={"lock_days": 29})
    fixed = product(11, term=30, bps=3650)
    products = [planned, fixed]
    result = select_asset(
        snapshot(100), [], [], financial_products(products), authorization(), products, exposure()
    )
    assert [c.net_simulated_yield_cents for c in result.candidates] == [3, 3]
    assert result.selected_product_id == fixed.product_id


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "version_collision",
        "bool",
        "float",
        "capacity",
        "reserved_negative",
        "managed_understates_position",
    ],
)
def test_untrusted_or_contradictory_input_is_rejected_before_optimization(defect: str) -> None:
    products = [product(10)]
    proof = exposure()
    positions = []
    if defect == "duplicate":
        products *= 2
    elif defect == "version_collision":
        products.append(products[0].model_copy(update={"product_id": UUID(int=11)}))
    elif defect in {"bool", "float"}:
        products[0] = products[0].model_copy(
            update={"minimum_purchase_cents": True if defect == "bool" else 1.0}
        )
    elif defect == "capacity":
        products *= 101
    elif defect == "reserved_negative":
        proof = proof.model_copy(update={"reserved_cash_by_account": {ACCOUNT: -1}})
    else:
        positions = [
            BoundaryPosition(position_id=UUID(int=100), principal_cents=100_000, status="HELD")
        ]
        proof = proof.model_copy(update={"counted_position_ids": [UUID(int=100)]})
    with pytest.raises(ValueError):
        select_asset(
            snapshot(),
            [],
            positions,
            financial_products([product(10)]),
            authorization(),
            products,
            proof,
        )


def test_exposure_identity_order_does_not_change_the_selection_hash() -> None:
    p = product(10)
    proof = exposure(evidence_ids=[UUID(int=20), UUID(int=21)])
    first = select_asset(snapshot(), [], [], financial_products([p]), authorization(), [p], proof)
    proof = proof.model_copy(update={"evidence_ids": list(reversed(proof.evidence_ids))})
    second = select_asset(snapshot(), [], [], financial_products([p]), authorization(), [p], proof)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_unclassified_existing_scope_principal_cannot_silently_free_managed_capacity() -> None:
    held = BoundaryPosition(position_id=UUID(int=100), principal_cents=100000, status="HELD")
    p = product(10)
    with pytest.raises(ValueError, match="classif"):
        select_asset(
            snapshot(), [], [held], financial_products([p]), authorization(), [p], exposure()
        )
