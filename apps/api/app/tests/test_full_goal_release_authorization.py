"""Original consent cannot silently change its economics, identity or permissions."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.full_goal_release_authorization import (
    GoalReleaseAuthorization,
    GoalReleaseBinding,
    GoalReleaseScope,
    ReleaseAuthorizationConfirmation,
    release_authorization_identity,
    release_authorization_request_hash,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import ValidationError

NOW = datetime(2026, 10, 6, tzinfo=UTC)


def original() -> GoalReleaseAuthorization:
    user, epoch, policy, version = [uuid4() for _ in range(4)]
    scope = GoalReleaseScope(
        user_id=user,
        epoch_id=epoch,
        policy_id=policy,
        policy_version_id=version,
        policy_configuration_hash="a" * 64,
        source_goals=[
            GoalReleaseBinding(
                goal_id=UUID(int=1),
                original_policy_id=uuid4(),
                original_policy_version_id=uuid4(),
                full_model_evidence_id=uuid4(),
                full_model_evidence_hash="b" * 64,
                full_configuration_hash="c" * 64,
                minimum_guarantee_cents=20000,
            )
        ],
        emergency_conditions=["HARD_OBLIGATION_SHORTFALL", "LIVING_RESERVE_SHORTFALL"],
        single_action_cap_cents=10000,
        total_cap_cents=50000,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=7),
    )
    scope_hash = configuration_hash(scope.model_dump(mode="json"))
    body = ReleaseAuthorizationConfirmation(
        expected_epoch_id=epoch,
        expected_policy_version_id=version,
        reviewed_scope_hash=scope_hash,
        accepted=True,
        idempotency_key="review-emergency-only",
    )
    return GoalReleaseAuthorization(
        authorization_id=release_authorization_identity(user, epoch, body.idempotency_key),
        user_id=user,
        epoch_id=epoch,
        policy_id=policy,
        policy_version_id=version,
        scope=scope,
        scope_hash=scope_hash,
        accepted=True,
        idempotency_key=body.idempotency_key,
        original_request=body,
        request_hash=release_authorization_request_hash(user, policy, body),
        confirmed_at=NOW,
        valid_until=scope.valid_until,
    )


def test_exact_original_consent_survives_json_restart_without_financial_result() -> None:
    consent = original()
    restored = GoalReleaseAuthorization.model_validate_json(consent.model_dump_json())
    assert restored == consent
    assert restored.scope.creates_new_income is False
    assert restored.scope.changes_original_assigned_income is False
    assert restored.scope.principal_release_allowed is False
    assert restored.scope.ordinary_goal_redistribution_allowed is False
    assert restored.scope.cumulative_scope == "POLICY_ID_ALL_VERSIONS"
    assert "amount_cents" not in restored.model_dump()
    assert "bank_receipt" not in restored.model_dump()


@pytest.mark.parametrize(
    "field", ["user_id", "epoch_id", "policy_id", "policy_version_id", "authorization_id"]
)
def test_old_consent_cannot_be_rebound_to_other_owner_epoch_policy_or_command(field: str) -> None:
    data = original().model_dump()
    data[field] = uuid4()
    with pytest.raises(ValidationError):
        GoalReleaseAuthorization.model_validate(data)


@pytest.mark.parametrize("value", [False, 1, 0, "true", None])
def test_explicit_release_confirmation_never_accepts_bool_coercion(value: object) -> None:
    data = original().original_request.model_dump()
    data["accepted"] = value
    with pytest.raises(ValidationError):
        ReleaseAuthorizationConfirmation.model_validate(data)


@pytest.mark.parametrize(
    "field",
    [
        "principal_release_allowed",
        "ordinary_goal_redistribution_allowed",
        "creates_new_income",
        "changes_original_assigned_income",
    ],
)
def test_release_scope_cannot_be_expanded_to_principal_new_income_or_ordinary_goal_moves(
    field: str,
) -> None:
    data = original().scope.model_dump()
    data[field] = True
    with pytest.raises(ValidationError):
        GoalReleaseScope.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("single_action_cap_cents", -1),
        ("single_action_cap_cents", 50001),
        ("total_cap_cents", 0),
        ("total_cap_cents", 2**63),
        ("total_cap_cents", True),
        ("single_action_cap_cents", 1.0),
    ],
)
def test_integer_finite_caps_remain_bounded(field: str, value: object) -> None:
    data = original().scope.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        GoalReleaseScope.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    [
        "scope_cap",
        "source_goal",
        "minimum",
        "scope_hash",
        "request_hash",
        "reviewed_hash",
        "original_key",
        "confirmation_before",
        "confirmation_after",
        "expiry",
    ],
)
def test_original_hashes_bind_every_permission_and_consent_field(mutation: str) -> None:
    consent = original()
    data = deepcopy(consent.model_dump())
    if mutation == "scope_cap":
        data["scope"]["single_action_cap_cents"] += 1
    elif mutation == "source_goal":
        data["scope"]["source_goals"][0]["goal_id"] = uuid4()
    elif mutation == "minimum":
        data["scope"]["source_goals"][0]["minimum_guarantee_cents"] = 0
    elif mutation in {"scope_hash", "request_hash"}:
        data[mutation] = "f" * 64
    elif mutation == "reviewed_hash":
        data["original_request"]["reviewed_scope_hash"] = "f" * 64
    elif mutation == "original_key":
        data["original_request"]["idempotency_key"] = "different"
    elif mutation == "confirmation_before":
        data["confirmed_at"] = consent.scope.valid_from - timedelta(seconds=1)
    elif mutation == "confirmation_after":
        data["confirmed_at"] = consent.scope.valid_until
    else:
        data["valid_until"] += timedelta(days=1)
    with pytest.raises(ValidationError):
        GoalReleaseAuthorization.model_validate(data)


def test_duplicate_source_goal_and_emergency_are_not_separate_permissions() -> None:
    scope = original().scope.model_dump()
    scope["source_goals"].append(deepcopy(scope["source_goals"][0]))
    with pytest.raises(ValidationError):
        GoalReleaseScope.model_validate(scope)
    scope = original().scope.model_dump()
    scope["emergency_conditions"].append(scope["emergency_conditions"][0])
    with pytest.raises(ValidationError):
        GoalReleaseScope.model_validate(scope)


def test_confirm_request_cannot_supply_bank_truth_amount_results_or_dates() -> None:
    for field in (
        "amount_cents",
        "bank_balance_cents",
        "used_total_cents",
        "now",
        "receipt",
        "source_goals",
    ):
        body = original().original_request.model_dump()
        body[field] = 100
        with pytest.raises(ValidationError):
            ReleaseAuthorizationConfirmation.model_validate(body)
