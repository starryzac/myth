"""Deterministic current repair math and fail-closed authority, not financial proof."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.full_goal_reallocation import (
    CONDITIONS,
    ReallocationDecisionInput,
    ReallocationPreviewRequest,
    RepairFinancialFacts,
    RepairGoalFacts,
    RepairPolicyFacts,
    RepairUsageFacts,
    decide_cash_reallocation,
)
from app.domain.full_policy_configuration import CrossGoalReallocationPolicy
from app.domain.policy_configuration import configuration_hash
from pydantic import ValidationError

NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)
USER, EPOCH, POLICY, VERSION, GOAL, GOAL_VERSION = [UUID(int=n) for n in range(7101, 7107)]


def configuration(**changes: Any) -> CrossGoalReallocationPolicy:
    return CrossGoalReallocationPolicy.model_validate(
        {
            "type": "cross_goal_reallocation",
            "enabled": True,
            "source_goal_ids": [str(GOAL)],
            "emergency_conditions": list(CONDITIONS),
            "single_action_cap_cents": 30000,
            "total_cap_cents": 50000,
            "valid_from": "2026-10-01",
            "valid_until": "2026-12-01",
        }
        | changes
    )


def facts() -> ReallocationDecisionInput:
    config = configuration()
    return ReallocationDecisionInput(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        timezone="Asia/Shanghai",
        policy=RepairPolicyFacts(
            policy_id=POLICY,
            version_id=VERSION,
            epoch_id=EPOCH,
            content_hash=configuration_hash(config.model_dump(mode="json")),
            configuration=config,
            current_confirmed=True,
            references_current=True,
            effective_status="ACTIVE",
        ),
        goal=RepairGoalFacts(
            goal_id=GOAL,
            account_id=UUID(int=7107),
            original_policy_version_id=GOAL_VERSION,
            cash_owned_cents=50000,
            principal_owned_cents=10000,
            minimum_guarantee_cents=30000,
            reserved_goal_cash_cents=0,
            ownership_verified=True,
            model_verified=True,
        ),
        financial=RepairFinancialFacts(
            cash_cents=100000,
            locked_goal_cash_cents=80000,
            reserved_cash_cents=0,
            required_by_condition=dict(zip(CONDITIONS, [20000, 10000, 15000], strict=True)),
            other_full_protection_cents=0,
            verified=True,
        ),
        usage=RepairUsageFacts(status="MISSING", consumed_cents=None, source_refs=[]),
        source_issues=[],
    )


def test_exact_current_minimum_has_no_principal_future_income_or_authority() -> None:
    data = facts()
    result = decide_cash_reallocation(data)
    assert result.math.status == "COMPUTED"
    assert result.math.unowned_unreserved_cash_cents == 20000
    assert result.math.shortfall_by_condition == dict(
        zip(CONDITIONS, [0, 10000, 15000], strict=True)
    )
    assert result.math.minimum_repair_cents == 25000
    assert result.math.source_cash_releasable_above_minimum_cents == 30000
    assert result.state == "UNKNOWN" and result.candidate_amount_cents is None
    assert result.cumulative_used_cents is result.cumulative_remaining_cents is None
    assert not result.bank_authority and not result.repairs_performed
    assert result.math.principal_release_cents == result.math.future_income_used_cents == 0
    assert result.input_hash == configuration_hash(data.model_dump(mode="json"))
    # Release one cent less still leaves exactly one cent unprotected; no larger
    # amount is necessary. This is a finite current-scope mathematical statement.
    available = result.math.unowned_unreserved_cash_cents
    minimum = result.math.minimum_repair_cents
    assert available is not None and minimum is not None
    required = sum(data.financial.required_by_condition.values())
    assert available + minimum == required and available + minimum - 1 < required


@pytest.mark.parametrize(
    "change,reason",
    [
        ("disabled", "CROSS_GOAL_REALLOCATION_DEFAULT_DISABLED"),
        ("revoked", "CURRENT_PLANNING_CONFIRMATION_NOT_VALID"),
        ("suspended", "CURRENT_PLANNING_CONFIRMATION_NOT_VALID"),
        ("ref", "POLICY_GOAL_REFERENCE_CHANGED"),
        ("epoch", "POLICY_EPOCH_NOT_CURRENT"),
        ("unlisted", "SOURCE_GOAL_NOT_EXPLICITLY_LISTED"),
        ("condition", "ACTUAL_EMERGENCY_CONDITION_NOT_ALLOWED"),
        ("cap", "MINIMUM_REPAIR_EXCEEDS_SINGLE_CAP"),
    ],
)
def test_definite_policy_denials_still_show_verified_mathematical_deficit(
    change: str, reason: str
) -> None:
    data = facts()
    policy = data.policy
    if change == "disabled":
        policy = policy.model_copy(
            update={
                "configuration": configuration(
                    enabled=False,
                    emergency_conditions=[],
                    single_action_cap_cents=0,
                    total_cap_cents=0,
                )
            }
        )
    elif change in {"revoked", "suspended"}:
        policy = policy.model_copy(
            update={"effective_status": change.upper(), "current_confirmed": False}
        )
    elif change == "ref":
        policy = policy.model_copy(update={"references_current": False})
    elif change == "epoch":
        policy = policy.model_copy(update={"epoch_id": UUID(int=7199)})
    elif change == "unlisted":
        policy = policy.model_copy(
            update={"configuration": configuration(source_goal_ids=[str(UUID(int=7199))])}
        )
    elif change == "condition":
        policy = policy.model_copy(
            update={
                "configuration": configuration(emergency_conditions=["HARD_OBLIGATION_SHORTFALL"])
            }
        )
    else:
        policy = policy.model_copy(
            update={"configuration": configuration(single_action_cap_cents=24999)}
        )
    result = decide_cash_reallocation(data.model_copy(update={"policy": policy}))
    assert result.state == "BLOCKED" and reason in result.reasons
    assert result.math.minimum_repair_cents == 25000 and result.candidate_amount_cents is None


def test_no_emergency_never_creates_an_action_or_relabels_cash_placement() -> None:
    data = facts()
    result = decide_cash_reallocation(
        data.model_copy(
            update={"financial": data.financial.model_copy(update={"cash_cents": 125000})}
        )
    )
    assert result.state == "NO_EMERGENCY" and result.triggered_conditions == []
    assert result.math.minimum_repair_cents == 0 and result.candidate_amount_cents is None
    assert result.preserves_principal_placement and not result.bank_authority


@pytest.mark.parametrize(
    "cash,principal,minimum,reserved",
    [(0, 60000, 30000, 0), (50000, 10000, 50000, 0), (50000, 10000, 30000, 30000)],
)
def test_cash_only_repair_cannot_borrow_principal_minimum_or_reserved_goal_funds(
    cash: int, principal: int, minimum: int, reserved: int
) -> None:
    data = facts()
    result = decide_cash_reallocation(
        data.model_copy(
            update={
                "goal": data.goal.model_copy(
                    update={
                        "cash_owned_cents": cash,
                        "principal_owned_cents": principal,
                        "minimum_guarantee_cents": minimum,
                        "reserved_goal_cash_cents": reserved,
                    }
                )
            }
        )
    )
    assert (
        result.state == "BLOCKED"
        and "SOURCE_CASH_OR_MINIMUM_GUARANTEE_INSUFFICIENT" in result.reasons
    )
    assert result.math.principal_release_cents == 0


def test_lifetime_cap_includes_previous_versions_and_unknown_is_not_zero() -> None:
    data = facts()
    usage = RepairUsageFacts(
        status="VERIFIED",
        consumed_cents=35000,
        source_refs=["synthetic:complete-lifetime-policy-originals"],
    )
    result = decide_cash_reallocation(data.model_copy(update={"usage": usage}))
    assert result.state == "BLOCKED" and result.cumulative_remaining_cents == 15000
    assert "MINIMUM_REPAIR_EXCEEDS_LIFETIME_POLICY_CAP" in result.reasons
    assert usage.policy_lifetime_across_versions
    for wrong in (
        {"status": "VERIFIED", "consumed_cents": 0, "source_refs": []},
        {"status": "MISSING", "consumed_cents": 0, "source_refs": []},
    ):
        with pytest.raises(ValidationError):
            RepairUsageFacts.model_validate(wrong)


@pytest.mark.parametrize("change", ["source", "inconsistent_cash"])
def test_missing_or_inconsistent_financial_source_keeps_math_null(change: str) -> None:
    data = facts()
    financial = data.financial.model_copy(
        update={"verified": False} if change == "source" else {"locked_goal_cash_cents": 100001}
    )
    result = decide_cash_reallocation(data.model_copy(update={"financial": financial}))
    assert result.math.status == "UNKNOWN" and result.math.minimum_repair_cents is None
    assert result.math.required_by_condition is None and result.triggered_conditions == []
    assert not result.math.unique_minimum_for_registered_current_scope


def test_goal_model_missing_does_not_hide_current_financial_math_or_invent_releasable_cash() -> (
    None
):
    data = facts()
    result = decide_cash_reallocation(
        data.model_copy(
            update={
                "goal": data.goal.model_copy(
                    update={"model_verified": False, "minimum_guarantee_cents": None}
                )
            }
        )
    )
    assert result.math.minimum_repair_cents == 25000
    assert result.math.source_cash_releasable_above_minimum_cents is None
    assert result.state == "UNKNOWN" and not result.bank_authority


def test_calendar_valid_until_matches_original_inclusive_calendar_day_contract() -> None:
    data = facts()
    last = datetime(2026, 12, 1, 15, tzinfo=UTC)
    assert (
        "POLICY_OUTSIDE_CURRENT_VALIDITY"
        not in decide_cash_reallocation(data.model_copy(update={"as_of": last})).reasons
    )
    assert (
        "POLICY_OUTSIDE_CURRENT_VALIDITY"
        in decide_cash_reallocation(
            data.model_copy(update={"as_of": last + timedelta(hours=1)})
        ).reasons
    )


@pytest.mark.parametrize(
    "extra",
    [
        "amount_cents",
        "emergency",
        "bank_balance",
        "bank_authority",
        "now",
        "user_id",
        "grant",
        "accepted",
        "income_uses",
    ],
)
def test_preview_accepts_only_exact_identity_fields(extra: str) -> None:
    request = ReallocationPreviewRequest(
        policy_id=POLICY,
        source_goal_id=GOAL,
        expected_policy_version_id=VERSION,
        expected_goal_policy_version_id=GOAL_VERSION,
        expected_epoch_id=EPOCH,
    )
    with pytest.raises(ValidationError):
        ReallocationPreviewRequest.model_validate_json(
            json.dumps(request.model_dump(mode="json") | {extra: True})
        )


def test_old_bridge_cross_enablement_stays_explicitly_rejected() -> None:
    from app.services.full_goals import canonical_goal_bridge
    from app.services.policy_lifecycle import PolicyLifecycleError

    with pytest.raises(PolicyLifecycleError) as error:
        canonical_goal_bridge(
            {
                "type": "long_term_goal",
                "name": "locked",
                "target_cents": 10000,
                "deadline": "2027-10-01",
                "monthly_contribution": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
                "cross_goal_reallocation_allowed": True,
                "cross_goal_reallocation_policy_id": str(POLICY),
            }
        )
    assert error.value.code == "CROSS_GOAL_AUTHORITY_NOT_IMPLEMENTED"
