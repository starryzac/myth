"""Pure query constraints reject grants and original-source substitution."""

import pytest
from app.api.v1.full_asset_allocation import PlanningConstraintsQuery
from app.domain.full_asset_allocation import FullAssetPlanOptions


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "balance_cents",
        "available_cents",
        "bank_authority",
        "now",
        "terms_digest",
        "risk_level",
        "accepted",
    ],
)
def test_query_cannot_inject_principal_facts_or_authority(field: str) -> None:
    with pytest.raises(ValueError):
        PlanningConstraintsQuery.model_validate({field: "1"})


def test_planning_constraints_are_explicit_and_finite() -> None:
    value = PlanningConstraintsQuery.model_validate(
        {
            "planning_max_components": "2",
            "planning_max_turnover_cents": "20000",
            "planning_funds_use_date": "2026-10-06",
        }
    )
    assert value.planning_max_components == 2 and value.planning_max_turnover_cents == 20000
    with pytest.raises(ValueError):
        PlanningConstraintsQuery.model_validate({"planning_max_components": "5"})
    with pytest.raises(ValueError):
        FullAssetPlanOptions.model_validate({"max_turnover_cents": True})
