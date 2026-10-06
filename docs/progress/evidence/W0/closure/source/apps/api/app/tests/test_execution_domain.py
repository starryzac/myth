"""Public economic effects and independent literal cash/obligation expectations."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.asset_allocation_types import PlannedExit
from app.domain.asset_exposure import AssetExposure
from app.domain.boundary import compute_boundary
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundarySnapshot,
    CashFact,
    GoalMonthFact,
    GoalOwnership,
    SettlementFact,
)
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import (
    BankCommand,
    CashUse,
    ConfirmationGrant,
    ExecutionContext,
    ExecutionEffect,
    OccurrenceReference,
)
from app.domain.goal_allocation import IncomeLot
from app.domain.income_ledger import IncomeUse, location_id
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.recovery_types import RecoveryQuote
from app.tests.test_asset_allocation import product

NOW = datetime(2026, 10, 4, 8, tzinfo=UTC)
USER, A, B, OP = (UUID(int=i) for i in range(1, 5))
POLICY, VERSION, EVIDENCE = (UUID(int=i) for i in range(10, 13))


def transfer() -> ExecutionEffect:
    return ExecutionEffect(
        operation_id=OP,
        user_id=USER,
        business_key="transfer:once",
        action_type="TRANSFER_INTERNAL",
        amount_cents=300,
        cash_uses=[CashUse(account_id=A, amount_cents=300)],
        destination_account_id=B,
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def context(now: datetime = NOW) -> ExecutionContext:
    return ExecutionContext(
        user_id=USER,
        snapshot=BoundarySnapshot(
            as_of=now,
            timezone="UTC",
            cash_accounts=[
                CashFact(account_id=A, account_type="CASH", balance_cents=1000, observed_at=NOW),
                CashFact(account_id=B, account_type="CASH", balance_cents=200, observed_at=NOW),
            ],
        ),
        versions=[],
        positions=[],
        boundary_products=[],
    )


def test_transfer_preserves_total_cash_and_hash_survives_execution_clock() -> None:
    effect = transfer()
    before_hash = execution_effect_hash(effect)
    checked = revalidate_execution(
        effect, context(NOW + timedelta(seconds=10)), confirmation=grant(effect)
    )
    assert checked.status == "READY"
    assert checked.effect_hash == before_hash
    assert checked.projected_snapshot is not None
    assert [c.balance_cents for c in checked.projected_snapshot.cash_accounts] == [700, 500]
    assert checked.projected_boundary is not None
    assert checked.projected_boundary.minimum_margin_cents == 1200
    assert checked.financial_only is True


def grant(effect: ExecutionEffect) -> ConfirmationGrant:
    return ConfirmationGrant(
        user_id=USER,
        operation_id=OP,
        effect_hash=execution_effect_hash(effect),
        evidence_id=UUID(int=50),
        confirmed_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def test_internal_transfer_needs_exact_one_shot_confirmation() -> None:
    checked = revalidate_execution(transfer(), context())
    assert checked.status == "CONFIRMATION_REQUIRED"
    assert (
        revalidate_execution(
            transfer(),
            context(),
            confirmation=grant(transfer()).model_copy(update={"effect_hash": "f" * 64}),
        ).status
        == "BLOCKED"
    )


@pytest.mark.parametrize("change", ["expired", "wrong_user", "reserved", "missing_destination"])
def test_fresh_facts_block_old_or_unfunded_transfer(change: str) -> None:
    state = context()
    if change == "expired":
        state = context(NOW + timedelta(minutes=15))
    elif change == "wrong_user":
        state = state.model_copy(update={"user_id": UUID(int=99)})
    elif change == "reserved":
        state = state.model_copy(update={"reserved_cash_by_account": {A: 900}})
    else:
        state = state.model_copy(
            update={
                "snapshot": state.snapshot.model_copy(
                    update={"cash_accounts": [state.snapshot.cash_accounts[0]]}
                )
            }
        )
    assert (
        revalidate_execution(transfer(), state, confirmation=grant(transfer())).status == "BLOCKED"
    )


def policy(config: dict[str, Any]) -> BoundaryPolicyVersion:
    normalized = validate_configuration(config)
    return BoundaryPolicyVersion(
        policy_id=POLICY,
        version_id=VERSION,
        configuration=normalized,
        content_hash=configuration_hash(normalized),
        confirmed_at=NOW - timedelta(days=2),
        valid_from=NOW - timedelta(days=2),
        valid_until=NOW + timedelta(days=10),
        evidence_ids=[EVIDENCE],
    )


def recurring(range_amount: bool = False, due_day: int = 4) -> BoundaryPolicyVersion:
    rule = (
        {"kind": "range", "min_cents": 200, "max_cents": 500}
        if range_amount
        else {"kind": "exact", "amount_cents": 300}
    )
    return policy(
        {
            "type": "recurring_obligation",
            "payee_id": "landlord-fixed",
            "amount_rule": rule,
            "due_day": due_day,
            "auto_execute": True,
        }
    )


def test_only_confirmed_final_range_total_replaces_protected_ceiling() -> None:
    state = context().snapshot
    assert (
        compute_boundary(state, [recurring(True)], [], []).protected_cents_by_reason["obligations"]
        == 500
    )
    final = SettlementFact.model_validate(
        {
            "policy_id": POLICY,
            "period": "2026-10",
            "paid_cents": 0,
            "settled_at": NOW,
            "final_total_cents": 300,
            "evidence_ids": [EVIDENCE],
        }
    )
    known = state.model_copy(update={"occurrence_settlements": [final]})
    assert (
        compute_boundary(known, [recurring(True)], [], []).protected_cents_by_reason["obligations"]
        == 300
    )
    with pytest.raises(ValueError):
        compute_boundary(
            known.model_copy(
                update={
                    "occurrence_settlements": [final.model_copy(update={"final_total_cents": 199})]
                }
            ),
            [recurring(True)],
            [],
            [],
        )


def payment(*, final: int | None = None) -> ExecutionEffect:
    return ExecutionEffect(
        operation_id=OP,
        user_id=USER,
        business_key="policy:10:2026-10",
        action_type="PAY_RECURRING",
        amount_cents=300,
        cash_uses=[CashUse(account_id=A, amount_cents=300)],
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        liability=OccurrenceReference(
            policy_id=POLICY, period="2026-10", final_total_cents=final, evidence_ids=[EVIDENCE]
        ),
        payee_id="landlord-fixed",
        payee_evidence_id=EVIDENCE,
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def test_paid_occurrence_is_not_debited_a_second_time_in_future_projection() -> None:
    state = context().model_copy(update={"versions": [recurring()]})
    checked = revalidate_execution(payment(), state)
    assert checked.status == "READY"
    assert checked.baseline_boundary.minimum_margin_cents == 900
    assert checked.projected_boundary is not None
    assert checked.projected_boundary.minimum_margin_cents == 900
    assert checked.projected_snapshot is not None
    assert checked.projected_snapshot.occurrence_settlements[0].paid_cents == 300
    assert checked.projected_boundary.protected_cents_by_reason["obligations"] == 0


def test_trusted_policy_confirmation_requirement_preserves_reviewable_projection() -> None:
    state = context().model_copy(update={"versions": [recurring()], "requires_confirmation": True})
    checked = revalidate_execution(payment(), state)
    assert checked.status == "CONFIRMATION_REQUIRED"
    assert checked.reasons == ["EXPLICIT_PAYMENT_CONFIRMATION_REQUIRED"]
    assert checked.projected_boundary is not None
    assert checked.projected_boundary.minimum_margin_cents == 900
    assert revalidate_execution(payment(), state, confirmation=grant(payment())).status == "READY"


def test_prepare_window_and_range_ceiling_are_not_payment_permission() -> None:
    for version in [recurring(due_day=5), recurring(True)]:
        assert (
            revalidate_execution(
                payment(), context().model_copy(update={"versions": [version]})
            ).status
            != "READY"
        )


def goal_example() -> tuple[ExecutionEffect, ExecutionContext]:
    identity, origin = UUID(int=100), UUID(int=101)
    version = policy(
        {
            "type": "goal_saving",
            "target_cents": 2000,
            "deadline": "2026-10-31",
            "monthly_contribution": {"min_cents": 100, "target_cents": 300, "max_cents": 500},
        }
    )
    base = context()
    state = base.snapshot.model_copy(
        update={
            "goals": [
                GoalOwnership(
                    goal_id=identity,
                    policy_id=POLICY,
                    account_id=B,
                    cash_owned_cents=200,
                    principal_owned_cents=0,
                    allocated_cents=200,
                )
            ],
            "goal_month_contributions": [
                GoalMonthFact(goal_id=identity, period="2026-10", contributed_cents=0)
            ],
        }
    )
    lot = IncomeLot(
        origin_transaction_id=origin,
        account_id=A,
        amount_cents=500,
        available_cents=500,
        occurred_at=NOW - timedelta(days=1),
        observed_at=NOW,
    )
    effect = ExecutionEffect(
        operation_id=OP,
        user_id=USER,
        business_key="goal:100:once",
        action_type="ALLOCATE_GOAL",
        amount_cents=300,
        cash_uses=[CashUse(account_id=A, amount_cents=300)],
        income_uses=[
            IncomeUse(
                fragment_id=location_id(origin, A),
                origin_transaction_id=origin,
                account_id=A,
                amount_cents=300,
            )
        ],
        destination_account_id=B,
        goal_id=identity,
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    return effect, base.model_copy(update={"snapshot": state, "versions": [version], "lots": [lot]})


def test_goal_allocation_changes_ownership_and_contribution_without_creating_cash() -> None:
    effect, state = goal_example()
    checked = revalidate_execution(effect, state)
    assert checked.status == "READY"
    assert checked.projected_snapshot is not None and checked.projected_boundary is not None
    assert [c.balance_cents for c in checked.projected_snapshot.cash_accounts] == [700, 500]
    assert checked.projected_snapshot.goals[0].allocated_cents == 500
    assert checked.projected_snapshot.goal_month_contributions[0].contributed_cents == 300
    assert checked.projected_boundary.minimum_margin_cents == 700
    assert state.lots[0].available_cents == 500
    assert revalidate_execution(effect, state.model_copy(update={"lots": []})).status != "READY"


def purchase_example() -> tuple[ExecutionEffect, ExecutionContext]:
    p = product(200)
    version = policy(
        {
            "type": "asset_authorization",
            "scope": "general_idle_funds",
            "allowed_asset_classes": ["CASH_MGMT_T0"],
            "max_auto_managed_cents": 1000,
            "single_action_cap_cents": 500,
            "max_redemption_delay_days": 1,
            "max_lock_days": 90,
            "allow_auto_recovery_without_penalty": True,
        }
    )
    version = version.model_copy(update={"valid_until": None})
    effect = ExecutionEffect(
        operation_id=OP,
        user_id=USER,
        business_key="purchase:once",
        action_type="PURCHASE_ASSET",
        amount_cents=300,
        cash_uses=[CashUse(account_id=A, amount_cents=300)],
        policy_id=POLICY,
        policy_version_id=VERSION,
        policy_version_ids=[VERSION],
        product_id=p.product_id,
        product_version_number=p.version_number,
        terms_digest=p.terms_digest,
        position_id=UUID(int=201),
        position_account_id=UUID(int=202),
        return_account_id=A,
        purchase_exit=PlannedExit(
            kind="PLANNED_REDEMPTION",
            request_at=NOW + timedelta(days=90),
            principal_available_at=NOW + timedelta(days=90),
            earning_days=90,
            liquidity_days=0,
            terms_digest=p.terms_digest,
        ),
        latest_arrival_at=NOW + timedelta(days=90, minutes=15),
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    state = context().model_copy(
        update={
            "versions": [version],
            "products": [p],
            "exposure": AssetExposure(
                as_of=NOW,
                scope="general_idle_funds",
                managed_principal_cents=0,
                pending_purchase_cents=0,
            ),
        }
    )
    return effect, state


def test_purchase_creates_exact_principal_and_rechecks_public_financial_cap() -> None:
    effect, state = purchase_example()
    checked = revalidate_execution(effect, state)
    assert checked.status == "READY"
    assert checked.projected_positions[0].position_id == effect.position_id
    assert checked.projected_positions[0].principal_cents == 300
    assert checked.projected_boundary is not None
    assert checked.projected_boundary.minimum_margin_cents == 900
    assert state.exposure is not None


def test_purchase_return_identity_is_hash_bound_independently_of_funding_order() -> None:
    effect, state = purchase_example()
    split = [CashUse(account_id=A, amount_cents=150), CashUse(account_id=B, amount_cents=150)]
    first = effect.model_copy(update={"cash_uses": split, "return_account_id": A})
    second = first.model_copy(update={"return_account_id": B})
    assert execution_effect_hash(first) != execution_effect_hash(second)
    assert execution_effect_hash(first) == execution_effect_hash(
        first.model_copy(update={"cash_uses": list(reversed(split))})
    )
    for bad in [None, UUID(int=999)]:
        with pytest.raises(ValueError):
            execution_effect_hash(first.model_copy(update={"return_account_id": bad}))
    assert state.exposure is not None
    assert (
        revalidate_execution(
            effect,
            state.model_copy(
                update={
                    "exposure": state.exposure.model_copy(
                        update={"pending_purchase_cents": 800, "reserved_cash_by_account": {A: 800}}
                    )
                }
            ),
        ).status
        != "READY"
    )


def test_unsubmitted_future_redemption_cannot_fund_actual_purchase_safety() -> None:
    effect, state = purchase_example()
    authorization = state.versions[0].model_copy(update={"valid_until": NOW + timedelta(days=10)})
    reserve = policy({"type": "emergency_buffer", "amount_cents": 1000}).model_copy(
        update={
            "policy_id": UUID(int=70),
            "version_id": UUID(int=71),
            "valid_from": NOW + timedelta(days=20),
            "valid_until": None,
        }
    )
    state = state.model_copy(update={"versions": [authorization, reserve]})
    assert effect.purchase_exit is not None
    effect = effect.model_copy(
        update={
            "purchase_exit": effect.purchase_exit.model_copy(
                update={
                    "request_at": NOW + timedelta(days=9),
                    "principal_available_at": NOW + timedelta(days=9),
                    "earning_days": 9,
                }
            )
        }
    )
    checked = revalidate_execution(effect, state)
    # 1,200 cash - 300 new principal = 900 actual cash; day20 requires 1,000.
    # An unsubmitted day9 redemption plan is not a bank principal-return commitment.
    assert checked.status == "BLOCKED"
    assert checked.reasons == ["UNACCEPTED_PLANNED_EXIT_CANNOT_SUPPORT_CASH_BOUNDARY"]


def test_fixed_purchase_uses_actual_execution_time_and_confirmed_arrival_upper_bound() -> None:
    effect, state = purchase_example()
    fixed = product(220, term=30)
    config = dict(state.versions[0].configuration, allowed_asset_classes=["FIXED_DEPOSIT"])
    version = state.versions[0].model_copy(
        update={
            "configuration": config,
            "content_hash": configuration_hash(config),
        }
    )
    effect = effect.model_copy(
        update={
            "product_id": fixed.product_id,
            "terms_digest": fixed.terms_digest,
            "purchase_exit": PlannedExit(
                kind="FIXED_MATURITY",
                principal_available_at=NOW + timedelta(days=30),
                earning_days=30,
                liquidity_days=30,
                terms_digest=fixed.terms_digest,
            ),
            "latest_arrival_at": NOW + timedelta(days=30, minutes=15),
        }
    )
    state = state.model_copy(
        update={
            "versions": [version],
            "products": [fixed],
            "snapshot": state.snapshot.model_copy(update={"as_of": NOW + timedelta(minutes=10)}),
        }
    )
    checked = revalidate_execution(effect, state)
    assert checked.status == "READY"
    assert checked.projected_positions[0].principal_available_at == NOW + timedelta(
        days=30, minutes=10
    )
    assert (
        revalidate_execution(
            effect.model_copy(
                update={
                    "latest_arrival_at": NOW + timedelta(days=30),
                }
            ),
            state,
        ).status
        == "BLOCKED"
    )


def loss_example(delay: int = 0, loss: int = 50) -> tuple[ExecutionEffect, ExecutionContext]:
    purchase, state = purchase_example()
    reserve = policy({"type": "emergency_buffer", "amount_cents": 1000}).model_copy(
        update={"policy_id": UUID(int=70), "version_id": UUID(int=71), "valid_until": None}
    )
    snap = state.snapshot.model_copy(
        update={
            "cash_accounts": [
                state.snapshot.cash_accounts[0].model_copy(update={"balance_cents": 500}),
                state.snapshot.cash_accounts[1],
            ]
        }
    )
    quote = RecoveryQuote(
        quote_id=UUID(int=250),
        user_id=USER,
        position_id=UUID(int=201),
        product_id=state.products[0].product_id,
        product_version_number=2,
        terms_digest=state.products[0].terms_digest,
        kind="EARLY_WITHDRAW",
        principal_cents=500,
        fee_cents=0,
        loss_cents=loss,
        net_cents=500 - loss,
        request_at=NOW,
        principal_available_at=NOW + timedelta(days=delay),
        expires_at=NOW + timedelta(minutes=15),
    )
    values = purchase.model_dump()
    values.update(
        action_type="REDEEM_ASSET",
        return_account_id=None,
        purchase_exit=None,
        amount_cents=500,
        cash_uses=[],
        destination_account_id=A,
        original_policy_version_id=VERSION,
        fee_cents=0,
        loss_cents=loss,
        net_cents=500 - loss,
        quote_id=quote.quote_id,
        settlement_delay_days=delay,
        latest_arrival_at=quote.expires_at + timedelta(days=delay),
    )
    effect = ExecutionEffect.model_validate(values)
    position = BoundaryPosition(
        position_id=UUID(int=201),
        principal_cents=500,
        status="HELD",
        principal_available_at=NOW + timedelta(days=5),
    )
    return effect, state.model_copy(
        update={
            "snapshot": snap,
            "versions": [*state.versions, reserve],
            "positions": [position],
            "redemption_quote": quote,
        }
    )


def test_confirmed_loss_uses_net_return_and_can_spend_positive_future_buffer() -> None:
    effect, state = loss_example()
    assert revalidate_execution(effect, state).status == "CONFIRMATION_REQUIRED"
    checked = revalidate_execution(effect, state, confirmation=grant(effect))
    assert checked.status == "READY"
    assert checked.baseline_boundary.minimum_margin_cents == -300
    assert checked.projected_boundary is not None and checked.projected_snapshot is not None
    assert checked.projected_boundary.minimum_margin_cents == 150
    assert checked.projected_boundary.calculation_trace[-1].cash_cents == 1150
    assert checked.projected_positions[0].status == "REDEEMED"
    assert state.positions[0].status == "HELD"


def test_t1_loss_preserves_actual_risk_and_fresh_clock_without_double_original_return() -> None:
    effect, state = loss_example(delay=1)
    state = state.model_copy(
        update={
            "snapshot": state.snapshot.model_copy(update={"as_of": NOW + timedelta(seconds=10)})
        }
    )
    checked = revalidate_execution(effect, state, confirmation=grant(effect))
    assert checked.status == "READY"
    assert checked.projected_boundary is not None
    margins = [p.margin_cents for p in checked.projected_boundary.calculation_trace]
    assert margins[:5] == [-300] * 5
    assert margins[5:] == [150] * 268
    assert checked.projected_positions[0].principal_available_at == NOW + timedelta(
        days=1, seconds=10
    )
    assert checked.projected_positions[0].principal_cents == 450
    assert checked.baseline_boundary.status == "LIQUIDITY_RISK"


def test_confirmation_never_permits_new_future_shortfall_or_late_arrival() -> None:
    effect, state = loss_example(loss=401)
    assert revalidate_execution(effect, state, confirmation=grant(effect)).status == "BLOCKED"


@pytest.mark.parametrize("bad", [True, False, -1, 0, 1.2, "300", 9223372036854775808])
def test_effect_money_is_strict_bounded_positive_integer(bad: Any) -> None:
    raw = transfer().model_dump()
    raw["amount_cents"] = bad
    with pytest.raises(ValueError):
        ExecutionEffect.model_validate(raw)


def test_bank_command_rejects_tampered_economic_hash() -> None:
    effect = transfer()
    BankCommand(effect=effect, effect_hash=execution_effect_hash(effect))
    with pytest.raises(ValueError):
        BankCommand(effect=effect, effect_hash="f" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("settlement_delay_days", 1),
        ("quote_id", UUID(int=999)),
        ("original_policy_version_id", VERSION),
        ("latest_arrival_at", NOW + timedelta(days=1)),
        ("policy_id", POLICY),
    ],
)
def test_transfer_cannot_hide_unrelated_permission_or_settlement_fields(
    field: str, value: Any
) -> None:
    raw = transfer().model_dump()
    raw[field] = value
    with pytest.raises(ValueError):
        ExecutionEffect.model_validate(raw)


def test_general_action_cannot_spend_cash_owned_by_a_goal() -> None:
    _, state = goal_example()
    effect = transfer().model_copy(
        update={"cash_uses": [CashUse(account_id=B, amount_cents=300)], "destination_account_id": A}
    )
    assert revalidate_execution(effect, state, confirmation=grant(effect)).status == "BLOCKED"


def test_partial_range_payment_keeps_the_known_remainder_and_future_periods() -> None:
    version = recurring(True).model_copy(update={"valid_until": NOW + timedelta(days=40)})
    known = SettlementFact(
        policy_id=POLICY,
        period="2026-10",
        paid_cents=0,
        final_total_cents=400,
        settled_at=NOW,
        evidence_ids=[EVIDENCE],
    )
    state = context().model_copy(
        update={
            "versions": [version],
            "snapshot": context().snapshot.model_copy(update={"occurrence_settlements": [known]}),
        }
    )
    checked = revalidate_execution(payment(final=400), state)
    assert checked.status == "READY" and checked.projected_boundary is not None
    assert checked.baseline_boundary.protected_cents_by_reason["obligations"] == 900
    assert checked.projected_boundary.protected_cents_by_reason["obligations"] == 600
    assert (
        checked.baseline_boundary.minimum_margin_cents
        == checked.projected_boundary.minimum_margin_cents
        == 300
    )


def test_other_account_reservation_cannot_be_reused_to_fund_safety_floor() -> None:
    effect, state = purchase_example()
    floor = policy({"type": "emergency_buffer", "amount_cents": 800}).model_copy(
        update={"policy_id": UUID(int=80), "version_id": UUID(int=81), "valid_until": None}
    )
    state = state.model_copy(
        update={"versions": [*state.versions, floor], "reserved_cash_by_account": {B: 200}}
    )
    assert revalidate_execution(effect, state).status == "BLOCKED"


def goal_purchase_example() -> tuple[ExecutionEffect, ExecutionContext]:
    effect, state = purchase_example()
    goal_id, goal_policy_id, goal_version_id = (UUID(int=i) for i in (500, 501, 502))
    goal_policy = policy(
        {
            "type": "goal_saving",
            "target_cents": 200,
            "deadline": "2026-10-31",
            "monthly_contribution": {"min_cents": 0, "target_cents": 0, "max_cents": 0},
            "asset_policy_id": str(POLICY),
        }
    ).model_copy(update={"policy_id": goal_policy_id, "version_id": goal_version_id})
    auth_config = dict(state.versions[0].configuration, scope="goal", goal_id=str(goal_id))
    auth = state.versions[0].model_copy(
        update={"configuration": auth_config, "content_hash": configuration_hash(auth_config)}
    )
    snap = state.snapshot.model_copy(
        update={
            "cash_accounts": [
                state.snapshot.cash_accounts[0].model_copy(update={"balance_cents": 0}),
                state.snapshot.cash_accounts[1].model_copy(update={"account_type": "GOAL"}),
            ],
            "goals": [
                GoalOwnership(
                    goal_id=goal_id,
                    policy_id=goal_policy_id,
                    account_id=B,
                    cash_owned_cents=200,
                    principal_owned_cents=0,
                    allocated_cents=200,
                )
            ],
            "goal_month_contributions": [
                GoalMonthFact(goal_id=goal_id, period="2026-10", contributed_cents=200)
            ],
        }
    )
    effect = effect.model_copy(
        update={
            "amount_cents": 100,
            "goal_id": goal_id,
            "cash_uses": [CashUse(account_id=B, amount_cents=100)],
            "return_account_id": B,
            "purchase_exit": PlannedExit(
                kind="PLANNED_REDEMPTION",
                request_at=NOW + timedelta(days=26),
                principal_available_at=NOW + timedelta(days=26),
                earning_days=26,
                liquidity_days=0,
                terms_digest=effect.terms_digest or "",
            ),
            "policy_version_ids": [VERSION, goal_version_id],
        }
    )
    return effect, state.model_copy(
        update={
            "snapshot": snap,
            "versions": [auth, goal_policy],
            "exposure": AssetExposure(
                as_of=NOW,
                scope="goal",
                goal_id=goal_id,
                managed_principal_cents=0,
                pending_purchase_cents=0,
            ),
        }
    )


def test_goal_purchase_uses_owned_cash_when_general_idle_is_zero() -> None:
    effect, state = goal_purchase_example()
    checked = revalidate_execution(effect, state)
    assert checked.status == "READY"
    assert checked.baseline_boundary.safe_idle_cents == 0
    assert checked.projected_snapshot is not None and checked.projected_boundary is not None
    owned = checked.projected_snapshot.goals[0]
    assert (owned.cash_owned_cents, owned.principal_owned_cents, owned.allocated_cents) == (
        100,
        100,
        200,
    )
    assert checked.projected_snapshot.goal_month_contributions[0].contributed_cents == 200
    assert checked.projected_boundary.minimum_margin_cents == 0


def test_prepared_loss_is_reviewable_before_confirmation_but_never_ready() -> None:
    effect, state = loss_example()
    result = revalidate_execution(effect, state)
    assert result.status == "CONFIRMATION_REQUIRED"
    assert result.projected_boundary is not None
    assert result.projected_boundary.minimum_margin_cents == 150


def test_bill_payment_updates_only_bill_and_preserves_same_margin() -> None:
    from app.domain.boundary_types import BillFact
    from app.domain.execution_types import BillReference

    card, bill = UUID(int=600), UUID(int=601)
    version = policy(
        {
            "type": "recurring_obligation",
            "payee_id": "card-fixed",
            "due_day": 4,
            "auto_execute": True,
            "amount_rule": {"kind": "bill_balance", "account_id": str(card)},
        }
    )
    snap = context().snapshot.model_copy(
        update={
            "cash_accounts": [
                *context().snapshot.cash_accounts,
                CashFact(
                    account_id=card, account_type="CREDIT_CARD", balance_cents=0, observed_at=NOW
                ),
            ],
            "bills": [
                BillFact(
                    bill_id=bill,
                    account_id=card,
                    statement_date=NOW.date(),
                    due_date=NOW.date(),
                    total_cents=500,
                    paid_cents=200,
                    status="PARTIALLY_PAID",
                    evidence_ids=[EVIDENCE],
                )
            ],
        }
    )
    effect = payment().model_copy(
        update={
            "payee_id": "card-fixed",
            "liability": BillReference(bill_id=bill, evidence_ids=[EVIDENCE]),
        }
    )
    checked = revalidate_execution(
        effect, context().model_copy(update={"snapshot": snap, "versions": [version]})
    )
    assert (
        checked.status == "READY"
        and checked.projected_snapshot is not None
        and checked.projected_boundary is not None
    )
    assert checked.projected_snapshot.bills[0].paid_cents == 500
    assert checked.projected_snapshot.bills[0].status == "PAID"
    assert checked.projected_snapshot.occurrence_settlements == []
    assert (
        checked.projected_boundary.minimum_margin_cents
        == checked.baseline_boundary.minimum_margin_cents
        == 900
    )


@pytest.mark.parametrize(
    "change",
    [
        "expired_quote",
        "changed_net",
        "reserved_position",
        "future_quote",
        "redeeming",
        "goal_scope",
    ],
)
def test_loss_confirmation_cannot_override_changed_economic_facts(change: str) -> None:
    effect, state = loss_example()
    assert state.redemption_quote is not None
    quote = state.redemption_quote
    if change == "expired_quote":
        state = state.model_copy(
            update={"snapshot": state.snapshot.model_copy(update={"as_of": quote.expires_at})}
        )
    elif change == "changed_net":
        state = state.model_copy(
            update={
                "redemption_quote": quote.model_copy(update={"net_cents": 449, "loss_cents": 51})
            }
        )
    elif change == "future_quote":
        state = state.model_copy(
            update={
                "redemption_quote": quote.model_copy(
                    update={
                        "request_at": NOW + timedelta(seconds=1),
                        "principal_available_at": NOW + timedelta(seconds=1),
                    }
                )
            }
        )
    elif change == "reserved_position":
        state = state.model_copy(update={"reserved_position_ids": [state.positions[0].position_id]})
    elif change == "redeeming":
        state = state.model_copy(
            update={"positions": [state.positions[0].model_copy(update={"status": "REDEEMING"})]}
        )
    else:
        effect = effect.model_copy(update={"goal_id": UUID(int=777)})
    assert revalidate_execution(effect, state, confirmation=grant(effect)).status == "BLOCKED"


def test_cash_spend_must_identify_income_that_would_otherwise_exceed_remaining_cash() -> None:
    effect, state = purchase_example()
    origin = UUID(int=850)
    lot = IncomeLot(
        origin_transaction_id=origin,
        account_id=A,
        amount_cents=900,
        available_cents=900,
        occurred_at=NOW - timedelta(days=1),
        observed_at=NOW,
    )
    state = state.model_copy(update={"lots": [lot]})
    assert revalidate_execution(effect, state).status == "BLOCKED"
    effect = effect.model_copy(
        update={
            "income_uses": [
                IncomeUse(
                    fragment_id=location_id(origin, A),
                    origin_transaction_id=origin,
                    account_id=A,
                    amount_cents=200,
                )
            ]
        }
    )
    checked = revalidate_execution(effect, state)
    assert checked.status == "READY"
    assert checked.projected_snapshot is not None
    assert checked.projected_snapshot.cash_accounts[0].balance_cents == 700
    assert state.lots[0].available_cents == 900
    effect, state = loss_example(delay=1)
    effect = effect.model_copy(update={"latest_arrival_at": NOW + timedelta(days=1)})
    state = state.model_copy(
        update={"snapshot": state.snapshot.model_copy(update={"as_of": NOW + timedelta(seconds=1)})}
    )
    assert revalidate_execution(effect, state, confirmation=grant(effect)).status == "BLOCKED"
