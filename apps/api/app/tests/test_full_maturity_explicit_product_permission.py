"""Explicit matured USER return never enables legacy automatic or early redemption."""

import pytest
from app.domain.full_maturity_execution import derive_maturity_proof, explicit_mature_ask_eligible
from app.domain.full_recovery_planning import _candidate, _linked_configuration
from app.tests.test_full_maturity_execution import fixture


def test_actual_fixed_product_auto_flag_remains_raw_and_old_planner_blocked() -> None:
    data = fixture()
    holding = data.planning.holdings[0]
    product = holding.original.product.model_copy(update={"auto_redeem_allowed": False})
    changed = holding.model_copy(
        update={"original": holding.original.model_copy(update={"product": product})}
    )
    plan = data.planning.model_copy(update={"holdings": [changed]})
    data = data.model_copy(update={"planning": plan})
    before = data.model_dump(mode="json")
    legacy = _candidate(plan, changed, _linked_configuration(plan), None)
    assert legacy.decision == "BLOCKED" and not legacy.lossless_eligible
    assert legacy.reasons == ["ORIGINAL_PRODUCT_REDEMPTION_PERMISSION_MISSING"]
    assert explicit_mature_ask_eligible(legacy)
    result = derive_maturity_proof(data)
    assert result.status == "VERIFIED_SCOPE", result.reasons
    assert result.command is not None and result.command.kind == "MATURE"
    assert data.model_dump(mode="json") == before
    assert not data.planning.holdings[0].original.product.auto_redeem_allowed


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "UNKNOWN"},
        {"liquidity_rank": 3},
        {"within_full_planning_limits": False},
        {"fee_cents": 1},
        {"independent_loss_cents": 1},
        {"net_cents": 1},
        {
            "reasons": [
                "ORIGINAL_PRODUCT_REDEMPTION_PERMISSION_MISSING",
                "CURRENT_ASSET_RISK_CAP_EXCEEDED",
            ]
        },
    ],
)
def test_auto_flag_is_the_only_irrelevant_manual_maturity_rejection(
    change: dict[str, object],
) -> None:
    data = fixture()
    held = data.planning.holdings[0]
    candidate = _candidate(data.planning, held, _linked_configuration(data.planning), None)
    rejected = candidate.model_copy(
        update={
            "decision": "BLOCKED",
            "lossless_eligible": False,
            "reasons": ["ORIGINAL_PRODUCT_REDEMPTION_PERMISSION_MISSING"],
            **change,
        }
    )
    assert not explicit_mature_ask_eligible(rejected)
