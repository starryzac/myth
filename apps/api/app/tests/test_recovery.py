"""Literal ADR 0008 examples through the pure public recovery planner."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.asset_allocation_types import AssetProductTerms
from app.domain.boundary_types import (
    BoundaryPolicyVersion,
    BoundaryPosition,
    BoundarySnapshot,
    CashFact,
    GoalOwnership,
    SourceIssue,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.domain.recovery import (
    RecoveryAuthorization,
    RecoveryPosition,
    RecoveryQuote,
    plan_recovery,
)

NOW = datetime(2026, 10, 4, 8, tzinfo=UTC)
USER, CASH, ASSET_ACCOUNT, POLICY, VERSION, EVIDENCE = (UUID(int=i) for i in range(1, 7))


def version(identity: int, configuration: dict[str, Any]) -> BoundaryPolicyVersion:
    normalized = validate_configuration(configuration)
    return BoundaryPolicyVersion(
        policy_id=UUID(int=identity),
        version_id=UUID(int=identity + 100),
        configuration=normalized,
        content_hash=configuration_hash(normalized),
        confirmed_at=NOW - timedelta(days=20),
        valid_from=NOW - timedelta(days=20),
        evidence_ids=[EVIDENCE],
    )


def authorization(**changes: Any) -> RecoveryAuthorization:
    config = validate_configuration(
        {
            "type": "asset_authorization",
            "scope": "general_idle_funds",
            "allowed_asset_classes": ["CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
            "max_auto_managed_cents": 1000000,
            "single_action_cap_cents": 1000000,
            "max_redemption_delay_days": 1,
            "max_lock_days": 90,
            "allow_auto_recovery_without_penalty": True,
            **changes,
        }
    )
    return RecoveryAuthorization(
        user_id=USER,
        policy_id=POLICY,
        version_id=VERSION,
        latest_version_id=VERSION,
        policy_status="ACTIVE",
        configuration=config,
        content_hash=configuration_hash(config),
        confirmed_at=NOW - timedelta(days=20),
        valid_from=NOW - timedelta(days=20),
        evidence_ids=[EVIDENCE],
    )


def snapshot(cash: int = 70000) -> BoundarySnapshot:
    return BoundarySnapshot(
        as_of=NOW,
        timezone="Asia/Shanghai",
        cash_accounts=[
            CashFact(
                account_id=CASH,
                account_type="CASH",
                balance_cents=cash,
                observed_at=NOW,
                evidence_ids=[EVIDENCE],
            )
        ],
    )


def reserve(amount: int = 100000) -> BoundaryPolicyVersion:
    return version(50, {"type": "emergency_buffer", "amount_cents": amount})


def position(
    identity: int = 10, principal: int = 50000, delay: int = 0
) -> tuple[BoundaryPosition, RecoveryPosition]:
    auth = authorization()
    original = BoundaryPolicyVersion.model_validate(
        auth.model_dump(exclude={"user_id", "policy_status", "latest_version_id"})
    )
    rule: dict[str, Any] = {
        "protocol": "planned-principal-return-v1",
        "day_basis": "CALENDAR",
        "guaranteed": True,
        "settlement_delay_days": delay,
        "principal_return_bps": 10000,
        "rollover": False,
        "auto_rollover": False,
        "yield_rule": {
            "protocol": "simple-annual-yield-v1",
            "basis": "ACT_365",
            "annual_yield_bps": 150,
            "simulation": True,
            "fee_cents": 0,
            "purchase_fee_bps": 0,
            "redemption_fee_bps": 0,
            "accrual": "UNTIL_REDEMPTION_REQUEST",
        },
    }
    product = AssetProductTerms(
        product_id=UUID(int=identity + 1000),
        product_code=f"P{identity}",
        version_number=2,
        asset_class="CASH_MGMT_T1" if delay else "CASH_MGMT_T0",
        risk_level=0,
        principal_fluctuation=False,
        minimum_purchase_cents=1,
        lock_days=0,
        redemption_delay_days=delay,
        annual_yield_bps=150,
        early_withdrawal_loss_bps=0,
        auto_purchase_allowed=True,
        auto_redeem_allowed=True,
        created_at=NOW - timedelta(days=30),
        effective_from=NOW - timedelta(days=30),
        maturity_rule=rule,
        terms_digest=configuration_hash(rule),
    )
    quote = RecoveryQuote(
        quote_id=UUID(int=identity + 2000),
        user_id=USER,
        position_id=UUID(int=identity),
        product_id=product.product_id,
        product_version_number=2,
        terms_digest=product.terms_digest,
        kind="REDEEM",
        principal_cents=principal,
        fee_cents=0,
        loss_cents=0,
        net_cents=principal,
        request_at=NOW,
        principal_available_at=NOW + timedelta(days=delay),
        expires_at=NOW + timedelta(minutes=5),
        evidence_ids=[EVIDENCE],
    )
    return (
        BoundaryPosition(
            position_id=UUID(int=identity),
            principal_cents=principal,
            status="HELD",
            evidence_ids=[EVIDENCE],
        ),
        RecoveryPosition(
            position_id=UUID(int=identity),
            account_id=ASSET_ACCOUNT,
            destination_account_id=CASH,
            purchased_at=NOW - timedelta(days=10),
            acquisition="AUTHORIZED_PURCHASE",
            product=product,
            original_authorization=original,
            quote=quote,
            evidence_ids=[EVIDENCE],
        ),
    )


def test_whole_t0_improves_conditional_cash_but_never_claims_actual_recovery() -> None:
    held, candidate = position()
    before = snapshot()
    original_json = before.model_dump_json()
    result = plan_recovery(
        before, [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert result.status == "AUTO_RECOVERY_AVAILABLE"
    assert result.simulation and result.preview_only
    assert result.actual_boundary.status == "LIQUIDITY_RISK"
    assert result.actual_boundary.minimum_margin_cents == -30000
    assert result.projected_boundary is not None
    assert result.projected_boundary.status == "READY"
    assert result.projected_boundary.calculation_trace[0].cash_cents == 120000
    assert result.projected_boundary.minimum_margin_cents == 20000
    assert len(result.projected_boundary.calculation_trace) == 273
    assert result.uncovered_checkpoints == []
    assert len(result.steps) == 1 and result.steps[0].quote.principal_cents == 50000
    assert result.steps[0].autonomy_level == "AUTO_EXECUTE"
    assert before.model_dump_json() == original_json


def test_t1_improves_later_negative_points_without_erasing_the_original_minimum() -> None:
    held, candidate = position(principal=30000, delay=1)
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert len(result.steps) == 1
    assert result.status == "PARTIAL_RECOVERY_AVAILABLE"
    assert result.actual_boundary.minimum_margin_cents == -30000
    assert result.projected_boundary is not None
    assert result.projected_boundary.minimum_margin_cents == -30000
    trace = result.projected_boundary.calculation_trace
    assert [p.margin_cents for p in trace[:6]] == [-30000] * 5 + [0]
    assert len(result.uncovered_checkpoints) == 5
    assert result.first_sustained_safe_point == trace[5]


def test_cumulative_plan_uses_t0_then_t1_and_does_not_redeem_a_redundant_third_warehouse() -> None:
    pairs = [position(12, 50000, 1), position(11, 20000, 1), position(10, 10000)]
    result = plan_recovery(
        snapshot(),
        [reserve()],
        [p for p, _ in pairs],
        [],
        [c for _, c in pairs],
        [authorization()],
        user_id=USER,
    )
    assert [step.position_id.int for step in result.steps] == [10, 11]
    assert result.projected_boundary is not None
    assert [point.cash_cents for point in result.projected_boundary.calculation_trace[:6]] == [
        80000,
        80000,
        80000,
        80000,
        80000,
        100000,
    ]
    assert result.candidates[-1].position_id == UUID(int=12)
    assert result.candidates[-1].reasons == ["NO_NEGATIVE_POINT_IMPROVED"]


def test_ready_baseline_never_redeems_to_increase_positive_margin() -> None:
    held, candidate = position()
    result = plan_recovery(
        snapshot(100001), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert result.status == "NO_RECOVERY_NEEDED"
    assert result.steps == []
    assert result.actual_boundary.minimum_margin_cents == 1


def test_early_whole_return_replaces_the_old_maturity_event() -> None:
    held, candidate = position(principal=30000)
    held = held.model_copy(
        update={
            "principal_available_at": NOW + timedelta(days=5),
            "availability_evidence_ids": [EVIDENCE],
        }
    )
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert result.projected_boundary is not None
    assert {p.cash_cents for p in result.projected_boundary.calculation_trace} == {100000}
    assert result.actual_boundary.calculation_trace[17].cash_cents == 100000


def test_goal_return_preserves_ownership_and_cannot_repair_a_general_cash_gap() -> None:
    goal_id = UUID(int=80)
    held, candidate = position(principal=30000)
    auth = authorization(scope="goal", goal_id=str(goal_id))
    original = BoundaryPolicyVersion.model_validate(
        auth.model_dump(exclude={"user_id", "policy_status", "latest_version_id"})
    )
    held = held.model_copy(update={"goal_id": goal_id})
    candidate = candidate.model_copy(
        update={"goal_id": goal_id, "original_authorization": original}
    )
    base = snapshot().model_copy(
        update={
            "goals": [
                GoalOwnership(
                    goal_id=goal_id,
                    policy_id=UUID(int=81),
                    account_id=CASH,
                    cash_owned_cents=20000,
                    principal_owned_cents=30000,
                    allocated_cents=50000,
                )
            ]
        }
    )
    result = plan_recovery(base, [reserve(80000)], [held], [], [candidate], [auth], user_id=USER)
    assert result.steps == []
    assert result.status == "NO_SAFE_RECOVERY"
    assert result.candidates[0].reasons == ["NO_NEGATIVE_POINT_IMPROVED"]
    conditional = result.candidates[0].projected_boundary
    assert conditional is not None
    assert conditional.calculation_trace[0].cash_cents == 100000
    assert conditional.protected_cents_by_reason["goal_cash"] == 50000
    assert conditional.minimum_margin_cents == -30000
    assert base.goals[0].allocated_cents == 50000


def test_complete_manual_acquisition_is_advice_only_and_needs_no_fabricated_authorization() -> None:
    held, candidate = position()
    candidate = candidate.model_copy(
        update={"acquisition": "MANUAL", "original_authorization": None, "quote": None}
    )
    result = plan_recovery(snapshot(), [reserve()], [held], [], [candidate], [], user_id=USER)
    assert result.status == "ADVISE_ONLY"
    assert result.steps == []
    assert result.candidates[0].decision == "ADVISE_ONLY"


def test_source_gap_closes_recovery_instead_of_returning_a_precise_zero_or_action() -> None:
    held, candidate = position()
    result = plan_recovery(
        snapshot(),
        [reserve()],
        [held],
        [],
        [candidate],
        [authorization()],
        user_id=USER,
        source_issues=[SourceIssue(code="BANK_PROOF_MISSING", entity_type="position")],
    )
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.steps == []
    assert result.projected_boundary is None


@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "suspended",
        "expired",
        "old_version",
        "not_yet_effective",
        "expired_window",
        "current_switch",
        "current_cap",
        "current_class",
        "original_switch",
        "original_cap",
        "original_not_confirmed_at_purchase",
    ],
)
def test_new_requests_need_both_the_original_and_current_exact_authorization(change: str) -> None:
    held, candidate = position()
    current = authorization()
    if change in {"revoked", "suspended", "expired"}:
        current = current.model_copy(update={"policy_status": change.upper()})
    elif change == "old_version":
        current = current.model_copy(update={"latest_version_id": UUID(int=999)})
    elif change == "not_yet_effective":
        current = current.model_copy(update={"valid_from": NOW + timedelta(seconds=1)})
    elif change == "expired_window":
        current = current.model_copy(update={"valid_until": NOW})
    elif change == "current_switch":
        current = authorization(allow_auto_recovery_without_penalty=False)
    elif change == "current_cap":
        current = authorization(single_action_cap_cents=49999)
    elif change == "current_class":
        current = authorization(allowed_asset_classes=["CASH"])
    elif change in {"original_switch", "original_cap"}:
        restrictive = authorization(
            **(
                {"allow_auto_recovery_without_penalty": False}
                if change == "original_switch"
                else {"single_action_cap_cents": 49999}
            )
        )
        candidate = candidate.model_copy(
            update={
                "original_authorization": BoundaryPolicyVersion.model_validate(
                    restrictive.model_dump(
                        exclude={"user_id", "policy_status", "latest_version_id"}
                    )
                )
            }
        )
    else:
        assert candidate.original_authorization is not None
        candidate = candidate.model_copy(
            update={
                "original_authorization": candidate.original_authorization.model_copy(
                    update={"confirmed_at": NOW - timedelta(days=5)}
                )
            }
        )
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [current], user_id=USER
    )
    assert result.steps == []
    assert result.candidates[0].decision == "ADVISE_ONLY"


@pytest.mark.parametrize("fee,loss", [(100, 0), (0, 100), (37, 63)])
def test_positive_fee_or_loss_proposal_binds_consequences_without_auto_step(
    fee: int, loss: int
) -> None:
    held, candidate = position(principal=30000)
    assert candidate.quote is not None
    candidate = candidate.model_copy(
        update={
            "quote": candidate.quote.model_copy(
                update={
                    "fee_cents": fee,
                    "loss_cents": loss,
                    "net_cents": 29900,
                }
            )
        }
    )
    current = authorization(allow_early_withdrawal_with_penalty=True)
    current = current.model_copy(update={"valid_until": NOW + timedelta(minutes=1)})
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [current], user_id=USER
    )
    assert result.status == "ASK_ONCE"
    assert result.steps == []
    assert result.projected_boundary is not None
    assert result.projected_boundary.minimum_margin_cents == -30000
    action = result.candidates[0].action
    assert action is not None and action.autonomy_level == "ASK_ONCE"
    assert action.quote.net_cents == 29900
    assert action.expires_at == NOW + timedelta(minutes=1)
    assert action.user_id == USER and action.destination_account_id == CASH
    assert action.request_hash == configuration_hash(
        action.model_dump(mode="json", exclude={"request_hash"})
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", UUID(int=900)),
        ("position_id", UUID(int=900)),
        ("product_id", UUID(int=900)),
        ("product_version_number", 1),
        ("terms_digest", "wrong-version-terms"),
        ("principal_cents", 50001),
        ("principal_cents", True),
        ("fee_cents", -1),
    ],
)
def test_contradictory_or_non_integer_quote_is_rejected(field: str, value: Any) -> None:
    held, candidate = position()
    assert candidate.quote is not None
    changes = {field: value}
    if field == "principal_cents" and value is not True:
        changes["net_cents"] = value
    candidate = candidate.model_copy(update={"quote": candidate.quote.model_copy(update=changes)})
    with pytest.raises(ValueError):
        plan_recovery(
            snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
        )


@pytest.mark.parametrize(
    "change",
    ["missing_quote", "unknown_acquisition", "missing_original", "stale_quote", "legacy_terms"],
)
def test_missing_or_stale_facts_never_create_a_recovery_request(change: str) -> None:
    held, candidate = position()
    initial_quote = candidate.quote
    assert initial_quote is not None
    if change == "missing_quote":
        candidate = candidate.model_copy(update={"quote": None})
    elif change == "unknown_acquisition":
        candidate = candidate.model_copy(update={"acquisition": "UNKNOWN"})
    elif change == "missing_original":
        candidate = candidate.model_copy(update={"original_authorization": None})
    elif change == "stale_quote":
        candidate = candidate.model_copy(
            update={
                "quote": initial_quote.model_copy(
                    update={
                        "request_at": NOW - timedelta(seconds=1),
                        "principal_available_at": NOW - timedelta(seconds=1),
                    }
                )
            }
        )
    else:
        legacy = {"kind": "REDEEM_ON_REQUEST"}
        product = candidate.product.model_copy(
            update={
                "version_number": 1,
                "maturity_rule": legacy,
                "terms_digest": configuration_hash(legacy),
            }
        )
        quote = initial_quote.model_copy(
            update={"product_version_number": 1, "terms_digest": product.terms_digest}
        )
        candidate = candidate.model_copy(update={"product": product, "quote": quote})
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert result.steps == []
    assert result.candidates[0].decision == "ASK_ONCE"


@pytest.mark.parametrize("change", ["inflight", "reserved", "already_redeemed"])
def test_already_committed_principal_cannot_be_requested_again(change: str) -> None:
    held, candidate = position()
    if change == "inflight":
        held = held.model_copy(update={"status": "REDEEMING"})
    elif change == "reserved":
        candidate = candidate.model_copy(update={"reserved_principal_cents": 50000})
    else:
        held = held.model_copy(update={"status": "REDEEMED"})
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert result.steps == []
    assert result.candidates[0].decision == "BLOCKED"


def test_due_unreconciled_original_principal_requires_bank_reconciliation_first() -> None:
    held, candidate = position()
    held = held.model_copy(update={"status": "MATURED", "principal_available_at": NOW})
    current = authorization().model_copy(update={"policy_status": "REVOKED"})
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [current], user_id=USER
    )
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.steps == []
    assert "RECONCILIATION_REQUIRED" in result.reasons


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_metadata",
        "duplicate_authority",
        "different_user",
        "missing_destination",
        "credit_destination",
        "future_purchase",
        "future_catalog",
        "original_hash",
        "current_hash",
        "negative_reserve",
        "bool_reserve",
        "duplicate_evidence",
        "capacity",
    ],
)
def test_untrusted_linked_input_cannot_silently_change_the_selected_effect(change: str) -> None:
    held, candidate = position()
    current = authorization()
    base = snapshot()
    candidates = [candidate]
    authorities = [current]
    if change == "duplicate_metadata":
        candidates.append(candidate)
    elif change == "duplicate_authority":
        authorities.append(current)
    elif change == "different_user":
        authorities = [current.model_copy(update={"user_id": UUID(int=999)})]
    elif change == "missing_destination":
        candidates = [candidate.model_copy(update={"destination_account_id": UUID(int=999)})]
    elif change == "credit_destination":
        base = base.model_copy(
            update={
                "cash_accounts": [
                    *base.cash_accounts,
                    CashFact(
                        account_id=UUID(int=999),
                        account_type="CREDIT_CARD",
                        balance_cents=0,
                        observed_at=NOW,
                    ),
                ]
            }
        )
        candidates = [candidate.model_copy(update={"destination_account_id": UUID(int=999)})]
    elif change == "future_purchase":
        candidates = [candidate.model_copy(update={"purchased_at": NOW + timedelta(seconds=1)})]
    elif change == "future_catalog":
        candidates = [
            candidate.model_copy(
                update={
                    "product": candidate.product.model_copy(
                        update={"created_at": NOW + timedelta(seconds=1)}
                    )
                }
            )
        ]
    elif change == "original_hash":
        assert candidate.original_authorization is not None
        candidates = [
            candidate.model_copy(
                update={
                    "original_authorization": candidate.original_authorization.model_copy(
                        update={"content_hash": "changed"}
                    )
                }
            )
        ]
    elif change == "current_hash":
        authorities = [current.model_copy(update={"content_hash": "changed"})]
    elif change in {"negative_reserve", "bool_reserve"}:
        candidates = [
            candidate.model_copy(
                update={"reserved_principal_cents": -1 if change == "negative_reserve" else True}
            )
        ]
    elif change == "duplicate_evidence":
        candidates = [candidate.model_copy(update={"evidence_ids": [EVIDENCE, EVIDENCE]})]
    else:
        candidates = [candidate] * 101
    with pytest.raises(ValueError):
        plan_recovery(base, [reserve()], [held], [], candidates, authorities, user_id=USER)


@pytest.mark.parametrize(
    "change",
    [
        "current_delay",
        "original_delay",
        "current_lock",
        "product_no_auto",
        "product_lock",
        "arrival_mismatch",
        "early_zero_quote",
    ],
)
def test_product_and_authorization_timing_limits_are_not_bypassed_by_a_quote(change: str) -> None:
    held, candidate = position(delay=1 if "delay" in change else 0)
    current = authorization()
    if change == "current_delay":
        current = authorization(max_redemption_delay_days=0)
    elif change == "original_delay":
        original = authorization(max_redemption_delay_days=0)
        candidate = candidate.model_copy(
            update={
                "original_authorization": BoundaryPolicyVersion.model_validate(
                    original.model_dump(exclude={"user_id", "policy_status", "latest_version_id"})
                )
            }
        )
    elif change == "current_lock":
        current = authorization(max_lock_days=0)
        candidate = candidate.model_copy(
            update={"product": candidate.product.model_copy(update={"lock_days": 5})}
        )
    elif change == "product_no_auto":
        candidate = candidate.model_copy(
            update={"product": candidate.product.model_copy(update={"auto_redeem_allowed": False})}
        )
    elif change == "product_lock":
        candidate = candidate.model_copy(
            update={"product": candidate.product.model_copy(update={"lock_days": 30})}
        )
    elif change == "arrival_mismatch":
        assert candidate.quote is not None
        candidate = candidate.model_copy(
            update={
                "quote": candidate.quote.model_copy(
                    update={"principal_available_at": NOW + timedelta(days=1)}
                )
            }
        )
    else:
        assert candidate.quote is not None
        candidate = candidate.model_copy(
            update={"quote": candidate.quote.model_copy(update={"kind": "EARLY_WITHDRAW"})}
        )
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [current], user_id=USER
    )
    assert result.steps == []
    assert result.candidates[0].decision != "AUTO_EXECUTE"


def test_historical_product_import_after_purchase_is_not_a_future_fact() -> None:
    held, candidate = position()
    candidate = candidate.model_copy(
        update={"product": candidate.product.model_copy(update={"created_at": NOW})}
    )
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert len(result.steps) == 1
    manual = candidate.model_copy(
        update={"acquisition": "MANUAL", "original_authorization": None, "quote": None}
    )
    advice = plan_recovery(snapshot(), [reserve()], [held], [], [manual], [], user_id=USER)
    assert advice.status == "ADVISE_ONLY" and advice.steps == []
    healthy = plan_recovery(snapshot(100001), [reserve()], [held], [], [manual], [], user_id=USER)
    assert healthy.status == "NO_RECOVERY_NEEDED"


def test_open_principal_without_acquisition_metadata_is_insufficient_not_an_empty_plan() -> None:
    held, _ = position()
    result = plan_recovery(snapshot(), [reserve()], [held], [], [], [], user_id=USER)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.projected_boundary is None


def test_same_product_identity_cannot_carry_two_incompatible_original_versions() -> None:
    first, candidate = position(10, 10000)
    second, other = position(11, 20000)
    assert other.quote is not None
    other = other.model_copy(
        update={
            "product": other.product.model_copy(
                update={"product_id": candidate.product.product_id}
            ),
            "quote": other.quote.model_copy(update={"product_id": candidate.product.product_id}),
        }
    )
    with pytest.raises(ValueError):
        plan_recovery(
            snapshot(),
            [reserve()],
            [first, second],
            [],
            [candidate, other],
            [authorization()],
            user_id=USER,
        )


def test_evidence_order_is_not_an_economic_change_but_fee_and_expiry_are_hash_bound() -> None:
    held, candidate = position(principal=30000)
    assert candidate.quote is not None
    quote = candidate.quote.model_copy(update={"fee_cents": 100, "net_cents": 29900})
    candidate = candidate.model_copy(
        update={"quote": quote, "evidence_ids": [EVIDENCE, UUID(int=900)]}
    )
    first = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    reordered = candidate.model_copy(
        update={"evidence_ids": list(reversed(candidate.evidence_ids))}
    )
    second = plan_recovery(
        snapshot(), [reserve()], [held], [], [reordered], [authorization()], user_id=USER
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    changed = candidate.model_copy(
        update={
            "quote": quote.model_copy(
                update={
                    "fee_cents": 101,
                    "net_cents": 29899,
                    "expires_at": NOW + timedelta(minutes=2),
                }
            )
        }
    )
    third = plan_recovery(
        snapshot(), [reserve()], [held], [], [changed], [authorization()], user_id=USER
    )
    assert first.plan_hash != third.plan_hash
    assert first.candidates[0].action is not None and third.candidates[0].action is not None
    assert first.candidates[0].action.request_hash != third.candidates[0].action.request_hash


def test_current_authority_expiry_does_not_forbid_a_previously_accepted_t1_arrival() -> None:
    held, candidate = position(principal=30000, delay=1)
    auth = authorization().model_copy(update={"valid_until": NOW + timedelta(minutes=2)})
    result = plan_recovery(snapshot(), [reserve()], [held], [], [candidate], [auth], user_id=USER)
    assert len(result.steps) == 1
    assert result.steps[0].expires_at == NOW + timedelta(minutes=2)
    assert result.steps[0].quote.principal_available_at == NOW + timedelta(days=1)


def test_delaying_an_already_known_today_return_cannot_pass_the_monotonicity_gate() -> None:
    held, candidate = position(principal=30000, delay=1)
    held = held.model_copy(update={"principal_available_at": NOW + timedelta(hours=1)})
    result = plan_recovery(
        snapshot(), [reserve()], [held], [], [candidate], [authorization()], user_id=USER
    )
    assert result.steps == []
    assert result.candidates[0].reasons == ["RECOVERY_WORSENS_A_CHECKPOINT"]
    assert result.projected_boundary == result.actual_boundary


def test_cumulative_safe_plan_stops_before_asking_for_an_unnecessary_costly_redemption() -> None:
    held, candidate = position(10, 50000)
    later_held, later = position(11, 30000)
    assert later.quote is not None
    later = later.model_copy(
        update={"quote": later.quote.model_copy(update={"fee_cents": 100, "net_cents": 29900})}
    )
    result = plan_recovery(
        snapshot(),
        [reserve()],
        [held, later_held],
        [],
        [candidate, later],
        [authorization()],
        user_id=USER,
    )
    assert len(result.steps) == 1
    assert result.candidates[1].decision == "BLOCKED"
    assert result.candidates[1].action is None
    assert result.candidates[1].reasons == ["NO_NEGATIVE_POINT_IMPROVED"]
