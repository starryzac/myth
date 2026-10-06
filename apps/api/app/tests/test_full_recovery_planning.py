"""Literal pure recovery risks, not measured banking or formal FULL case evidence."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary_types import BoundaryPosition, GoalOwnership, SourceIssue
from app.domain.full_policy_configuration import RecoveryPolicy
from app.domain.full_recovery_planning import (
    FullRecoveryHolding,
    FullRecoveryPlanningInput,
    LinkedAssetPlanningPolicy,
    independent_early_loss,
    plan_full_recovery,
)
from app.domain.policy_configuration import configuration_hash
from app.domain.recovery_types import RecoveryQuote
from app.tests.test_recovery import (
    NOW,
    POLICY,
    USER,
    VERSION,
    authorization,
    position,
    reserve,
    snapshot,
)


def holding(
    identity: int = 10, principal: int = 50000, delay: int = 0
) -> tuple[BoundaryPosition, FullRecoveryHolding]:
    held, original = position(identity, principal, delay)
    return held, FullRecoveryHolding(
        original=original,
        catalogue_version_id=UUID(int=identity + 4000),
        product_record_hash="a" * 64,
        early_withdrawal_rule={},
        quote_source="BANK_CONFIRMED",
    )


def fixed_holding(
    identity: int = 20, *, early: bool = True, principal: int = 50000
) -> tuple[BoundaryPosition, FullRecoveryHolding]:
    held, metadata = holding(identity, principal)
    original = metadata.original
    purchased = NOW - timedelta(days=10 if early else 30)
    liquid_rule = original.product.maturity_rule
    rule = {
        **liquid_rule,
        "protocol": "fixed-principal-return-v1",
        "term_days": 30,
        "yield_rule": {**liquid_rule["yield_rule"], "accrual": "UNTIL_MATURITY"},
    }
    product = AssetProductTerms.model_validate(
        {
            **original.product.model_dump(),
            "asset_class": "FIXED_DEPOSIT",
            "lock_days": 30,
            "early_withdrawal_loss_bps": 20,
            "maturity_rule": rule,
            "terms_digest": configuration_hash(rule),
        }
    )
    loss = (principal * 20 + 9999) // 10000 if early else 0
    quote = RecoveryQuote(
        quote_id=UUID(int=identity + 2000),
        user_id=USER,
        position_id=held.position_id,
        product_id=product.product_id,
        product_version_number=product.version_number,
        terms_digest=product.terms_digest,
        kind="EARLY_WITHDRAW" if early else "MATURE",
        principal_cents=principal,
        fee_cents=0,
        loss_cents=loss,
        net_cents=principal - loss,
        request_at=NOW,
        principal_available_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        evidence_ids=[UUID(int=6)],
    )
    authority = original.original_authorization
    assert authority is not None
    original = original.model_copy(
        update={
            "product": product,
            "quote": quote,
            "purchased_at": purchased,
            "original_authorization": authority.model_copy(
                update={
                    "confirmed_at": NOW - timedelta(days=40),
                    "valid_from": NOW - timedelta(days=40),
                }
            ),
        }
    )
    return held, metadata.model_copy(
        update={
            "original": original,
            "maturity_at": purchased + timedelta(days=30),
            "early_withdrawal_rule": {
                "allowed": True,
                "requires_confirmation_if_loss": True,
                "loss_basis": "principal_cents",
                "simulation": True,
            },
        }
    )


def inputs(
    pairs: list[tuple[BoundaryPosition, FullRecoveryHolding]] | None = None,
    *,
    config_changes: dict[str, Any] | None = None,
) -> FullRecoveryPlanningInput:
    pairs = pairs if pairs is not None else [holding()]
    linked = authorization()
    return FullRecoveryPlanningInput(
        user_id=USER,
        policy_id=UUID(int=700),
        policy_version_id=UUID(int=701),
        configuration=RecoveryPolicy.model_validate(
            {
                "type": "recovery",
                "scope": "general_idle_funds",
                "asset_policy_id": str(POLICY),
                "triggers": ["LIQUIDITY_SHORTFALL"],
                "single_action_cap_cents": 1000000,
                "max_redemption_delay_days": 1,
                "allow_auto_recovery_without_penalty": True,
                **(config_changes or {}),
            }
        ),
        planning_confirmation_valid=True,
        confirmed_at=NOW - timedelta(days=5),
        valid_from=NOW - timedelta(days=5),
        linked_asset_policy=LinkedAssetPlanningPolicy(
            policy_id=POLICY,
            version_id=VERSION,
            kind="MVP_POLICY",
            configuration=linked.configuration,
            content_hash=linked.content_hash,
            confirmation_valid=True,
            confirmed_at=linked.confirmed_at,
            valid_from=linked.valid_from,
            evidence_ids=linked.evidence_ids,
        ),
        snapshot=snapshot().model_copy(update={"horizon_days": 365}),
        boundary_versions=[reserve()],
        positions=[row for row, _ in pairs],
        boundary_products=[],
        holdings=[row for _, row in pairs],
    )


def test_t0_conditional_plan_preserves_originals_and_full_365_curve() -> None:
    data = inputs()
    before = data.model_dump_json()
    result = plan_full_recovery(data)
    assert result.status == "CONDITIONAL_RECOVERY_PLAN"
    assert result.actual_boundary.minimum_margin_cents == -30000
    assert result.lossless_conditional_boundary is not None
    assert result.lossless_conditional_boundary.minimum_margin_cents == 20000
    assert len(result.actual_boundary.calculation_trace) == 1098
    assert result.actual_scope_cash_cents == 70000 and result.required_recovery_cents == 30000
    assert result.conditional_on_time_recovery_cents == 50000
    assert result.lossless_steps[0].decision == "ASK_ONCE"
    assert not result.bank_authority and result.execution_support == "NOT_IMPLEMENTED"
    assert result.lossless_steps[0].catalogue_version_id == data.holdings[0].catalogue_version_id
    assert data.model_dump_json() == before
    assert plan_full_recovery(data) == result


def test_t1_late_cash_keeps_real_early_shortfalls_and_reports_first_safe_point() -> None:
    result = plan_full_recovery(inputs([holding(delay=1, principal=30000)]))
    assert result.status == "LIQUIDITY_RISK" and len(result.lossless_steps) == 1
    assert result.conditional_on_time_recovery_cents == 0
    assert (
        result.first_sustained_safe_point is not None and result.first_sustained_safe_point.day == 1
    )
    assert len(result.uncovered_checkpoints) == 5
    assert result.candidates[0].on_time is False


def test_rank_t0_t1_mature_then_loss_and_no_redundant_step() -> None:
    data = inputs(
        [
            fixed_holding(40),
            holding(12, 10000, 1),
            fixed_holding(30, early=False),
            holding(11, 40000),
        ]
    )
    result = plan_full_recovery(data)
    assert [row.liquidity_rank for row in result.candidates] == [0, 1, 2, 3]
    assert [row.position_id for row in result.lossless_steps] == [UUID(int=11)]


def test_independent_early_price_is_ask_impact_not_lossless_or_actual_cash() -> None:
    result = plan_full_recovery(inputs([fixed_holding(principal=50001)]))
    candidate = result.candidates[0]
    assert candidate.decision == "ASK_ONCE" and candidate.independent_loss_cents == 101
    assert candidate.net_cents == 49900 and not candidate.within_full_planning_limits
    assert "FULL_LOSS_CAP_EXCEEDED" in candidate.reasons
    assert "CURRENT_EARLY_WITHDRAWAL_NOT_ALLOWED" in candidate.reasons
    assert candidate.conditional_impact_boundary is not None
    assert candidate.conditional_impact_boundary.calculation_trace[0].cash_cents == 119900
    assert result.actual_boundary.calculation_trace[0].cash_cents == 70000
    assert result.lossless_steps == [] and result.conditional_on_time_recovery_cents == 0
    assert result.status == "LIQUIDITY_RISK"


@pytest.mark.parametrize(
    "principal,bps", [(True, 20), (100, True), (-1, 20), (100, 10001), (1.5, 20)]
)
def test_independent_loss_rejects_noninteger_or_out_of_range(principal: Any, bps: Any) -> None:
    with pytest.raises(ValueError):
        independent_early_loss(principal, bps)


def test_max_principal_uses_exact_integer_ceiling() -> None:
    assert independent_early_loss(9223372036854775807, 20) == 18446744073709552
    assert independent_early_loss(1, 1) == 1


def test_valid_original_quote_is_not_retimed_and_forecast_never_uses_past_dispatch() -> None:
    held, row = holding()
    quote = row.original.quote
    assert quote is not None
    old = quote.model_copy(
        update={
            "request_at": NOW - timedelta(minutes=1),
            "principal_available_at": NOW - timedelta(minutes=1),
        }
    )
    row = row.model_copy(update={"original": row.original.model_copy(update={"quote": old})})
    candidate = plan_full_recovery(inputs([(held, row)])).candidates[0]
    assert candidate.original_quote == old
    assert candidate.earliest_conditional_cash_at == NOW
    assert candidate.lossless_eligible


@pytest.mark.parametrize("fault", ["expired", "early_hash", "early_loss", "early_rule", "no_quote"])
def test_unproven_price_never_supplies_recovery_money(fault: str) -> None:
    held, row = fixed_holding()
    quote = row.original.quote
    assert quote is not None
    if fault == "expired":
        quote = quote.model_copy(
            update={
                "request_at": NOW - timedelta(minutes=1),
                "principal_available_at": NOW - timedelta(minutes=1),
                "expires_at": NOW,
            }
        )
    elif fault == "early_hash":
        quote = quote.model_copy(update={"terms_digest": "b" * 64})
    elif fault == "early_loss":
        quote = quote.model_copy(
            update={"loss_cents": quote.loss_cents + 1, "net_cents": quote.net_cents - 1}
        )
    elif fault == "early_rule":
        row = row.model_copy(update={"early_withdrawal_rule": {"allowed": True}})
    else:
        quote = None
    row = row.model_copy(update={"original": row.original.model_copy(update={"quote": quote})})
    if fault == "early_hash":
        with pytest.raises(ValueError):
            plan_full_recovery(inputs([(held, row)]))
    else:
        result = plan_full_recovery(inputs([(held, row)]))
        assert result.candidates[0].decision == "UNKNOWN"
        assert result.candidates[0].net_cents is None
        assert result.candidates[0].conditional_impact_boundary is None
        assert result.lossless_steps == []


def test_full_single_action_cap_and_registered_trigger_are_hard_limits() -> None:
    capped = plan_full_recovery(inputs(config_changes={"single_action_cap_cents": 49999}))
    assert (
        capped.lossless_steps == []
        and "FULL_SINGLE_ACTION_CAP_EXCEEDED" in capped.candidates[0].reasons
    )
    no_trigger = plan_full_recovery(inputs(config_changes={"triggers": ["BOUNDARY_SHRINK"]}))
    assert no_trigger.status == "NOT_TRIGGERED" and no_trigger.lossless_steps == []
    assert "BOUNDARY_SHRINK" not in no_trigger.observed_triggers


def test_expired_full_confirmation_never_selects_a_step() -> None:
    result = plan_full_recovery(inputs().model_copy(update={"valid_until": NOW}))
    assert result.status == "INACTIVE_POLICY" and result.lossless_steps == []


def test_original_purchase_version_must_precede_purchase_and_match_scope() -> None:
    held, row = holding()
    version = row.original.original_authorization
    assert version is not None
    row = row.model_copy(
        update={
            "original": row.original.model_copy(
                update={
                    "original_authorization": version.model_copy(update={"confirmed_at": NOW}),
                }
            )
        }
    )
    candidate = plan_full_recovery(inputs([(held, row)])).candidates[0]
    assert candidate.decision == "UNKNOWN" and not candidate.lossless_eligible


def test_owned_goal_cannot_repair_general_gap_and_loss_never_preserves_gross_ownership() -> None:
    goal_id = UUID(int=80)
    held, row = fixed_holding()
    auth = authorization(scope="goal", goal_id=str(goal_id))
    old_version = row.original.original_authorization
    assert old_version is not None
    goal_version = old_version.model_copy(
        update={"configuration": auth.configuration, "content_hash": auth.content_hash}
    )
    held = held.model_copy(update={"goal_id": goal_id})
    row = row.model_copy(
        update={
            "original": row.original.model_copy(
                update={"goal_id": goal_id, "original_authorization": goal_version}
            )
        }
    )
    data = inputs([(held, row)])
    goal = GoalOwnership(
        goal_id=goal_id,
        policy_id=UUID(int=81),
        account_id=row.original.destination_account_id,
        cash_owned_cents=20000,
        principal_owned_cents=50000,
        allocated_cents=70000,
    )
    data = data.model_copy(update={"snapshot": data.snapshot.model_copy(update={"goals": [goal]})})
    general = plan_full_recovery(data)
    assert general.candidates[0].decision == "EXCLUDED_SCOPE" and general.lossless_steps == []
    config = RecoveryPolicy.model_validate(
        {**data.configuration.model_dump(), "scope": "goal", "goal_id": goal_id}
    )
    linked = data.linked_asset_policy.model_copy(
        update={"configuration": auth.configuration, "content_hash": auth.content_hash}
    )
    own = plan_full_recovery(
        data.model_copy(
            update={
                "configuration": config,
                "linked_asset_policy": linked,
                "goal_deadline_at": NOW + timedelta(days=5),
                "goal_reference_verified": True,
            }
        )
    )
    impact = own.candidates[0].conditional_impact_boundary
    assert impact is not None and impact.calculation_trace[0].cash_cents == 119900
    assert impact.protected_cents_by_reason["goal_cash"] == 69900
    assert impact.minimum_margin_cents == own.actual_boundary.minimum_margin_cents
    assert own.actual_scope_cash_cents == 20000 and own.required_recovery_cents == 50000


def test_source_unknown_never_turns_unmeasured_money_into_zero() -> None:
    data = inputs()
    result = plan_full_recovery(
        data.model_copy(
            update={
                "snapshot": data.snapshot.model_copy(
                    update={"source_issues": [SourceIssue(code="BANK_UNKNOWN", entity_type="bank")]}
                )
            }
        )
    )
    assert result.status == "UNKNOWN" and result.actual_scope_cash_cents is None
    assert (
        result.required_recovery_cents is None and result.conditional_on_time_recovery_cents is None
    )
    assert result.lossless_conditional_boundary is None and result.lossless_steps == []


def test_deadline_cannot_relax_original_required_time() -> None:
    with pytest.raises(ValueError, match="deadline"):
        plan_full_recovery(
            inputs().model_copy(update={"planning_deadline_at": NOW + timedelta(days=1)})
        )


def test_old_valid_t1_quote_preserves_price_but_dispatch_delay_is_not_shortened() -> None:
    held, row = holding(delay=1)
    quote = row.original.quote
    assert quote is not None
    old = quote.model_copy(
        update={
            "request_at": NOW - timedelta(minutes=1),
            "principal_available_at": NOW + timedelta(days=1) - timedelta(minutes=1),
        }
    )
    row = row.model_copy(update={"original": row.original.model_copy(update={"quote": old})})
    candidate = plan_full_recovery(inputs([(held, row)])).candidates[0]
    assert candidate.original_quote == old
    assert candidate.earliest_conditional_cash_at == NOW + timedelta(days=1)


def test_delayed_loss_projection_never_returns_gross_principal_as_future_cash() -> None:
    held, row = fixed_holding()
    original = row.original
    quote = original.quote
    assert quote is not None
    rule = {**original.product.maturity_rule, "settlement_delay_days": 1}
    product = AssetProductTerms.model_validate(
        {
            **original.product.model_dump(),
            "redemption_delay_days": 1,
            "maturity_rule": rule,
            "terms_digest": configuration_hash(rule),
        }
    )
    delayed = quote.model_copy(
        update={
            "principal_available_at": NOW + timedelta(days=1),
            "terms_digest": product.terms_digest,
        }
    )
    row = row.model_copy(
        update={"original": original.model_copy(update={"product": product, "quote": delayed})}
    )
    result = plan_full_recovery(inputs([(held, row)]))
    impact = result.candidates[0].conditional_impact_boundary
    assert impact is not None
    assert impact.calculation_trace[0].cash_cents == 70000
    assert impact.calculation_trace[5].cash_cents == 119900
    assert result.status == "LIQUIDITY_RISK" and not result.lossless_steps


def test_missing_current_linked_confirmation_is_unknown_with_nullable_money() -> None:
    data = inputs()
    data = data.model_copy(
        update={
            "linked_asset_policy": data.linked_asset_policy.model_copy(
                update={"confirmation_valid": False}
            )
        }
    )
    result = plan_full_recovery(data)
    assert result.status == "UNKNOWN" and result.required_recovery_cents is None
    assert result.actual_scope_cash_cents is None and result.lossless_steps == []
