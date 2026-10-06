"""Pure new query boundary; no database or execution acceptance."""

import pytest
from app.api.v1.full_recovery_planning import RecoveryPlanningQuery
from pydantic import ValidationError


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "now",
        "amount_cents",
        "goal_id",
        "position_id",
        "max_loss_cents",
        "bank_authority",
        "accepted",
        "quote",
    ],
)
def test_recovery_query_rejects_financial_and_authority_overrides(field: str) -> None:
    with pytest.raises(ValidationError):
        RecoveryPlanningQuery.model_validate({field: "1"})


def test_planning_deadline_requires_aware_time() -> None:
    with pytest.raises(ValidationError):
        RecoveryPlanningQuery.model_validate({"planning_deadline_at": "2026-10-05T09:00:00"})
    query = RecoveryPlanningQuery.model_validate(
        {"planning_deadline_at": "2026-10-05T09:00:00+08:00"}
    )
    assert query.planning_deadline_at is not None
    assert RecoveryPlanningQuery().planning_deadline_at is None
