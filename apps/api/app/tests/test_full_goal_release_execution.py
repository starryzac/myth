"""Synthetic contract risks only; no database, grants or financial outcome evidence."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.full_goal_release_authorization import (
    GoalReleaseAuthorization,
    GoalReleaseBinding,
    GoalReleaseScope,
    ReleaseAuthorizationConfirmation,
    release_authorization_identity,
    release_authorization_request_hash,
)
from app.domain.full_goal_release_execution import (
    GoalReleaseBankCommand,
    GoalReleaseEffect,
    GoalReleasePostingOriginal,
    GoalReleaseSettlementOriginal,
    GoalReleaseUse,
    compute_release_policy_usage,
    goal_release_effect_hash,
    goal_release_leg_specs,
    replay_goal_release_residuals,
    validate_goal_release_settlement,
    validate_release_authorization_binding,
)
from app.domain.goal_release_provenance import GoalCashSourceProof, prove_goal_cash_sources
from app.domain.policy_configuration import configuration_hash
from app.tests.test_goal_release_provenance import NOW, fixture
from pydantic import ValidationError


def basis() -> GoalCashSourceProof:
    return prove_goal_cash_sources(fixture())


def command(
    amount: int = 100, *, operation: int = 9200, version: int = 9201
) -> GoalReleaseBankCommand:
    source = basis()
    part = source.original_allocation_slices[0]
    effect = GoalReleaseEffect(
        operation_id=UUID(int=operation),
        user_id=source.user_id,
        epoch_id=source.epoch_id,
        business_key=f"synthetic-release:{operation}",
        bank_idempotency_key=f"release:{operation}",
        policy_id=UUID(int=9210),
        policy_version_id=UUID(int=version),
        policy_configuration_hash="a" * 64,
        authorization_id=release_authorization_identity(
            source.user_id, source.epoch_id, "synthetic"
        ),
        authorization_evidence_id=UUID(int=9212),
        authorization_evidence_hash="b" * 64,
        authorization_scope_hash="c" * 64,
        authorization_request_hash="d" * 64,
        source_goal_id=source.goal_id,
        original_goal_policy_id=UUID(int=9213),
        original_goal_policy_version_id=source.goal_policy_version_id,
        full_model_evidence_id=UUID(int=9214),
        full_model_evidence_hash="e" * 64,
        full_configuration_hash="f" * 64,
        minimum_guarantee_cents=0,
        source_account_id=source.goal_account_id,
        destination_account_id=UUID(int=9215),
        amount_cents=amount,
        emergency_conditions=["HARD_OBLIGATION_SHORTFALL"],
        release_uses=[
            GoalReleaseUse(
                allocation_action_id=part.allocation_action_id,
                original_policy_version_id=part.original_policy_version_id,
                fragment_id=part.fragment_id,
                origin_transaction_id=part.origin_transaction_id,
                income_location_account_id=part.income_location_account_id,
                allocation_effect_hash=part.allocation_effect_hash,
                allocation_bank_request_hash=part.allocation_bank_request_hash,
                allocation_action_request_hash=part.allocation_action_request_hash,
                amount_cents=amount,
            )
        ],
        source_provenance_hash=source.source_binding_hash,
        financial_input_hash="8" * 64,
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    return GoalReleaseBankCommand(effect=effect, effect_hash=goal_release_effect_hash(effect))


def settlement(
    amount: int = 100,
    *,
    operation: int = 9200,
    version: int = 9201,
    status: str = "SETTLED",
) -> GoalReleaseSettlementOriginal:
    original = command(amount, operation=operation, version=version)
    postings = []
    if status == "SETTLED":
        for index, spec in enumerate(goal_release_leg_specs(original)):
            before = 300 if spec.delta_cents < 0 else 0
            postings.append(
                GoalReleasePostingOriginal(
                    posting_id=uuid5(original.effect.operation_id, "posting:" + spec.leg_ref),
                    user_id=original.effect.user_id,
                    operation_id=original.effect.operation_id,
                    ledger_key=spec.ledger_key,
                    ledger_dimension=spec.ledger_dimension,
                    ledger_metadata=spec.required_metadata,
                    leg_ref=spec.leg_ref,
                    account_id=spec.account_id,
                    previous_posting_id=UUID(int=9300 + index),
                    sequence_number=2,
                    entry_kind="SETTLEMENT",
                    balance_before_cents=before,
                    delta_cents=spec.delta_cents,
                    balance_after_cents=before + spec.delta_cents,
                    occurred_at=NOW,
                    original_row_hash="9" * 64,
                )
            )
    return GoalReleaseSettlementOriginal.model_validate(
        {
            "command": original,
            "bank_operation_id": original.effect.operation_id,
            "action_plan_id": original.effect.operation_id,
            "user_id": original.effect.user_id,
            "request_hash": configuration_hash(original.model_dump(mode="json")),
            "business_key": original.effect.business_key,
            "idempotency_key": original.effect.bank_idempotency_key,
            "status": status,
            "requested_at": NOW,
            "available_at": NOW,
            "settled_at": NOW if status == "SETTLED" else None,
            "postings": postings,
            "original_bank_row_hash": "a" * 64,
        }
    )


def usage(rows: list[GoalReleaseSettlementOriginal], **overrides: Any) -> Any:
    args: dict[str, Any] = dict(
        actual_release_operation_count=len(rows),
        whole_bank_source_verified=True,
        current_source_binding_hash="a" * 64,
        now=NOW,
    )
    args.update(overrides)
    return compute_release_policy_usage(basis().user_id, UUID(int=9210), rows, **args)


def residual(rows: list[GoalReleaseSettlementOriginal], **overrides: Any) -> Any:
    original = basis()
    settled = sum(row.command.effect.amount_cents for row in rows if row.status == "SETTLED")
    args: dict[str, Any] = dict(
        actual_release_operation_count=len(rows),
        whole_bank_source_verified=True,
        current_goal_cash_cents=300 - settled,
        current_goal_principal_cents=0,
        current_assigned_by_fragment={
            row.fragment_id: row.original_assigned_cents for row in original.sources
        },
        current_source_binding_hash="a" * 64,
        now=NOW,
    )
    args.update(overrides)
    return replay_goal_release_residuals(original, rows, **args)


def test_new_hash_and_exact_three_legs_never_change_original_assigned_or_old_effect() -> None:
    frozen = fixture().model_dump_json()
    original = settlement()
    validate_goal_release_settlement(original, NOW)
    assert [row.delta_cents for row in original.postings] == [-100, 100, -100]
    assert (
        sum(row.delta_cents for row in original.postings if row.ledger_dimension == "ECONOMIC") == 0
    )
    result = residual([original])
    assert result.state == "VERIFIED" and result.exactly_attributed_goal_cash_cents == 200
    assert result.remaining[0].cash_remaining_cents == 200
    assert (
        result.original_assigned_unchanged
        and not result.funds_released
        and not result.bank_authority
    )
    assert fixture().model_dump_json() == frozen
    with pytest.raises(ValidationError):
        GoalReleaseBankCommand.model_validate_json(fixture().originals[0].command.model_dump_json())  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "field", ["effect_hash", "amount_cents", "destination_account_id", "fragment_id"]
)
def test_original_effect_hash_or_fragment_cannot_be_rebound(field: str) -> None:
    data = command().model_dump()
    if field == "effect_hash":
        data[field] = "0" * 64
    elif field == "fragment_id":
        data["effect"]["release_uses"][0][field] = UUID(int=1)
    else:
        data["effect"][field] = 101 if field == "amount_cents" else UUID(int=1)
    with pytest.raises(ValidationError):
        GoalReleaseBankCommand.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_cents", True),
        ("amount_cents", 0),
        ("amount_cents", 2**63),
        ("fee_cents", 1),
        ("fee_cents", False),
        ("available_income_increase_cents", 1),
        ("assigned_income_decrease_cents", 1),
        ("principal_change_cents", 1),
        ("other_goal_change_cents", 1),
        ("valid_from", NOW.replace(tzinfo=None)),
        ("expires_at", NOW),
    ],
)
def test_finite_economics_reject_unsafe_or_coerced_fields(field: str, value: object) -> None:
    data = command().effect.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        GoalReleaseEffect.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "owner",
        "account",
        "delta",
        "key",
        "posting_id",
        "balance",
        "time",
        "bank_key",
    ],
)
def test_settlement_requires_original_bank_identity_and_every_actual_leg(change: str) -> None:
    data = settlement().model_dump()
    if change == "missing":
        data["postings"].pop()
    elif change == "extra":
        data["postings"].append(data["postings"][0])
    elif change == "bank_key":
        data["idempotency_key"] = "replacement"
    else:
        mapping = {
            "owner": ("user_id", UUID(int=1)),
            "account": ("account_id", UUID(int=1)),
            "delta": ("delta_cents", -99),
            "key": ("ledger_key", "LOT_AVAILABLE:fake"),
            "posting_id": ("posting_id", UUID(int=1)),
            "balance": ("balance_after_cents", 201),
            "time": ("occurred_at", NOW + timedelta(seconds=1)),
        }
        name, value = mapping[change]
        data["postings"][0][name] = value
    with pytest.raises(ValueError):
        validate_goal_release_settlement(GoalReleaseSettlementOriginal.model_validate(data), NOW)


def test_cross_version_cap_includes_settled_and_accepted_but_never_calls_accepted_paid() -> None:
    result = usage(
        [settlement(100), settlement(50, operation=9202, version=9203, status="ACCEPTED")]
    )
    assert result.state == "VERIFIED"
    assert (result.settled_cents, result.accepted_reserved_cents, result.cap_occupied_cents) == (
        100,
        50,
        150,
    )
    assert result.accepted_operation_ids == [UUID(int=9202)]
    assert not result.service_receipt_or_economic_success_claimed and not result.bank_authority


@pytest.mark.parametrize(
    "override",
    [
        {"actual_release_operation_count": None},
        {"actual_release_operation_count": True},
        {"actual_release_operation_count": 1},
        {"whole_bank_source_verified": False},
        {"current_source_binding_hash": "unknown"},
    ],
)
def test_zero_usage_requires_real_complete_inventory(override: dict[str, Any]) -> None:
    result = usage([], **override)
    assert result.state == "UNKNOWN" and result.cap_occupied_cents is None


def test_actual_complete_zero_is_supported_without_fake_success() -> None:
    result = usage([])
    assert result.state == "VERIFIED" and result.cap_occupied_cents == 0
    assert result.actual_operation_count == 0 and result.captured_operation_count == 0
    assert residual([]).state == "VERIFIED"


def test_unknown_key_and_duplicate_inventory_never_restore_capacity() -> None:
    assert usage([settlement(status="UNKNOWN")]).cap_occupied_cents is None
    original = settlement()
    assert usage([original, original]).state == "UNKNOWN"
    assert residual([original, original]).state == "UNKNOWN"


def test_rejected_no_effect_consumes_zero_but_unexplained_legs_are_unknown() -> None:
    original = settlement(status="REJECTED")
    assert usage([original]).cap_occupied_cents == 0
    assert residual([original]).state == "VERIFIED"
    broken = original.model_copy(update={"postings": settlement().postings})
    assert usage([broken]).state == residual([broken]).state == "UNKNOWN"


def test_pending_source_goal_and_modified_assigned_or_principal_never_become_available() -> None:
    assert residual([settlement(status="ACCEPTED")]).state == "UNKNOWN"
    assert residual([], current_assigned_by_fragment={}).state == "UNKNOWN"
    assert residual([], current_goal_principal_cents=1).state == "UNKNOWN"
    assert residual([], current_goal_cash_cents=299).state == "UNKNOWN"
    assert (
        residual(
            [settlement(200), settlement(200, operation=9202)], current_goal_cash_cents=0
        ).state
        == "UNKNOWN"
    )


def test_exact_authorization_binds_scope_but_does_not_claim_current_sql_permission() -> None:
    effect = command().effect
    scope = GoalReleaseScope(
        user_id=effect.user_id,
        epoch_id=effect.epoch_id,
        policy_id=effect.policy_id,
        policy_version_id=effect.policy_version_id,
        policy_configuration_hash=effect.policy_configuration_hash,
        source_goals=[
            GoalReleaseBinding(
                goal_id=effect.source_goal_id,
                original_policy_id=effect.original_goal_policy_id,
                original_policy_version_id=effect.original_goal_policy_version_id,
                full_model_evidence_id=effect.full_model_evidence_id,
                full_model_evidence_hash=effect.full_model_evidence_hash,
                full_configuration_hash=effect.full_configuration_hash,
                minimum_guarantee_cents=effect.minimum_guarantee_cents,
            )
        ],
        emergency_conditions=effect.emergency_conditions,
        single_action_cap_cents=100,
        total_cap_cents=300,
        valid_from=NOW,
        valid_until=NOW + timedelta(days=1),
    )
    digest = configuration_hash(scope.model_dump(mode="json"))
    body = ReleaseAuthorizationConfirmation(
        expected_epoch_id=effect.epoch_id,
        expected_policy_version_id=effect.policy_version_id,
        reviewed_scope_hash=digest,
        accepted=True,
        idempotency_key="synthetic",
    )
    auth = GoalReleaseAuthorization(
        authorization_id=effect.authorization_id,
        user_id=effect.user_id,
        epoch_id=effect.epoch_id,
        policy_id=effect.policy_id,
        policy_version_id=effect.policy_version_id,
        scope=scope,
        scope_hash=digest,
        accepted=True,
        idempotency_key=body.idempotency_key,
        original_request=body,
        request_hash=release_authorization_request_hash(effect.user_id, effect.policy_id, body),
        confirmed_at=NOW,
        valid_until=scope.valid_until,
    )
    actual = effect.model_copy(
        update={"authorization_scope_hash": digest, "authorization_request_hash": auth.request_hash}
    )
    original = GoalReleaseBankCommand(effect=actual, effect_hash=goal_release_effect_hash(actual))
    validate_release_authorization_binding(original, auth, NOW)
    with pytest.raises(ValueError):
        validate_release_authorization_binding(original, auth, NOW + timedelta(days=2))
    with pytest.raises(ValueError):
        validate_release_authorization_binding(original, auth, NOW.replace(tzinfo=None))
