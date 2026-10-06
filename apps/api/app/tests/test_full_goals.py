"""FULL model bridge risks with literal pure fixtures, never bank observations."""

from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from app.api.v1.full_goals import FullGoalConfirmationRequest, FullGoalPreviewRequest
from app.db.models import AuditEpoch, EvidenceItem, Goal, Policy, PolicyVersion, User
from app.domain.policy_configuration import configuration_hash
from app.services.full_goals import (
    MODEL_SOURCE,
    FullGoalModelContent,
    FullGoalModelResponse,
    ReviewedGoalHashes,
    _confirmation_window,
    _model_lineage,
    _request_hash,
    canonical_goal_bridge,
    verify_full_goal_model_original,
)
from app.services.policy_lifecycle import LifecycleResult, PolicyLifecycleError
from pydantic import ValidationError

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
USER, EPOCH, GOAL, POLICY, VERSION, BEFORE = (UUID(int=i) for i in range(1, 7))


def configuration() -> dict[str, Any]:
    return {
        "type": "long_term_goal",
        "name": "严格确认的完整目标",
        "target_cents": 1000000,
        "deadline": "2027-01-01",
        "monthly_contribution": {"min_cents": 10000, "target_cents": 20000, "max_cents": 30000},
        "importance": 80,
        "minimum_guarantee_cents": 5000,
        "allow_partial": True,
        "allow_deferral": True,
        "deferral_cost_cents_per_day": 71,
    }


def originals() -> tuple[EvidenceItem, Goal, Policy, PolicyVersion, AuditEpoch]:
    full, base = canonical_goal_bridge(configuration())
    full_hash, base_hash = configuration_hash(full), configuration_hash(base)
    lifecycle = LifecycleResult(
        policy_id=POLICY,
        current_version_id=VERSION,
        previous_version_id=BEFORE,
        status="ACTIVE",
        effective_status="ACTIVE",
        invalidated_action_ids=[],
        inflight_action_ids=[],
    )
    content = FullGoalModelContent(
        user_id=USER,
        epoch_id=EPOCH,
        goal_id=GOAL,
        policy_id=POLICY,
        base_policy_version_id=VERSION,
        expected_version_id=BEFORE,
        full_configuration=full,
        full_hash=full_hash,
        base_hash=base_hash,
        reviewed=ReviewedGoalHashes(full_hash=full_hash, base_hash=base_hash),
        accepted=True,
        confirmed_at=NOW,
        idempotency_key="pure-original",
        reason="明确复核",
        request_hash=_request_hash(USER, EPOCH, GOAL, BEFORE, full, "明确复核", "pure-original"),
    ).model_dump(mode="json")
    row = EvidenceItem(
        id=UUID(int=8),
        user_id=USER,
        created_at=NOW,
        observed_at=NOW,
        valid_from=NOW,
        valid_to=None,
        evidence_level="USER_CONFIRMED_POLICY",
        source_type=MODEL_SOURCE,
        source_ref=str(GOAL),
        content=content,
        content_hash=configuration_hash(content),
        status="VALID",
    )
    goal = Goal(
        id=GOAL,
        user_id=USER,
        policy_id=POLICY,
        policy_version_id=VERSION,
        account_id=UUID(int=9),
        name=full["name"],
        target_cents=full["target_cents"],
        allocated_cents=777,
        deadline=date(2027, 1, 1),
        monthly_min_cents=10000,
        monthly_target_cents=20000,
        monthly_max_cents=30000,
        importance=80,
        minimum_protection_cents=5000,
        reducible=True,
        deferrable=True,
        cross_goal_reallocation_allowed=False,
        asset_policy_id=None,
    )
    policy = Policy(
        id=POLICY, user_id=USER, name=full["name"], policy_type="goal_saving", status="ACTIVE"
    )
    version = PolicyVersion(
        id=VERSION,
        user_id=USER,
        policy_id=POLICY,
        version_number=2,
        configuration=base,
        content_hash=base_hash,
        confirmed_at=NOW,
        valid_from=NOW,
        valid_until=None,
        confirmation={
            "accepted": True,
            "user_id": str(USER),
            "policy_id": str(POLICY),
            "version_id": str(VERSION),
            "reviewed_hash": base_hash,
            "confirmed_at": NOW.isoformat(),
            "request_key": "change:full-goal:pure-original",
            "request_hash": configuration_hash(
                {
                    "user_id": str(USER),
                    "policy_id": str(POLICY),
                    "expected_version_id": str(BEFORE),
                    "configuration": base,
                    "reason": "明确复核",
                    "accepted": True,
                }
            ),
        },
        impact_analysis={"lifecycle_result": lifecycle.model_dump(mode="json")},
    )
    return row, goal, policy, version, AuditEpoch(id=EPOCH, user_id=USER, status="OPEN")


def test_full_model_maps_original_fields_and_retains_extra_planning_contract() -> None:
    source = configuration()
    before = deepcopy(source)
    full, base = canonical_goal_bridge(source)
    assert source == before
    assert base["type"] == "goal_saving" and full["type"] == "long_term_goal"
    assert base["priority"] == {
        "importance": 80,
        "minimum_cents": 5000,
        "reducible": True,
        "deferrable": True,
    }
    assert base["monthly_contribution"] == full["monthly_contribution"]
    assert "deferral_cost_cents_per_day" not in base and full["deferral_cost_cents_per_day"] == 71
    assert base["cross_goal_reallocation_allowed"] is False
    assert configuration_hash(full) != configuration_hash(base)
    original = originals()
    value = verify_full_goal_model_original(*original, NOW)
    assert value.full_configuration == full
    assert original[1].allocated_cents == 777


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_owned_cents", 99),
        ("effective_policy_version_id", str(VERSION)),
        ("user_id", str(USER)),
        ("result", "SUCCESS"),
        ("importance", True),
    ],
)
def test_full_goal_financial_overrides_and_coerced_integer_reject(field: str, value: Any) -> None:
    with pytest.raises(PolicyLifecycleError) as failure:
        canonical_goal_bridge(configuration() | {field: value})
    assert failure.value.code == "INVALID_FULL_GOAL_CONFIGURATION"


def test_full_cross_goal_model_cannot_bootstrap_bank_permission() -> None:
    with pytest.raises(PolicyLifecycleError) as failure:
        canonical_goal_bridge(
            configuration()
            | {
                "cross_goal_reallocation_allowed": True,
                "cross_goal_reallocation_policy_id": str(POLICY),
            }
        )
    assert failure.value.code == "CROSS_GOAL_AUTHORITY_NOT_IMPLEMENTED"


@pytest.mark.parametrize(
    "mutate",
    [
        "hash",
        "review",
        "owner",
        "epoch",
        "status",
        "source",
        "base",
        "goal",
        "confirmation",
        "time",
        "receipt",
        "lifecycle",
        "extra",
        "accepted",
    ],
)
def test_model_original_tampering_never_promotes_to_verified(mutate: str) -> None:
    row, goal, policy, version, epoch = originals()
    if mutate == "hash":
        row.content_hash = "f" * 64
    elif mutate == "review":
        row.content = deepcopy(row.content)
        row.content["reviewed"]["full_hash"] = "f" * 64
        row.content_hash = configuration_hash(row.content)
    elif mutate == "owner":
        row.user_id = UUID(int=999)
    elif mutate == "epoch":
        epoch.id = UUID(int=999)
    elif mutate == "status":
        row.status = "UNKNOWN"
    elif mutate == "source":
        row.source_type = "POLICY_CONFIRMATION"
    elif mutate == "base":
        version.configuration = {**version.configuration, "name": "伪改"}
    elif mutate == "goal":
        goal.monthly_max_cents += 1
    elif mutate == "confirmation":
        version.confirmation = {**version.confirmation, "accepted": False}
    elif mutate == "time":
        row.observed_at = datetime(2026, 10, 6, tzinfo=UTC)
    elif mutate == "receipt":
        version.confirmation = {**version.confirmation, "request_hash": "f" * 64}
    elif mutate == "lifecycle":
        version.impact_analysis = deepcopy(version.impact_analysis)
        version.impact_analysis["lifecycle_result"]["previous_version_id"] = str(UUID(int=999))
    else:
        row.content = {
            **row.content,
            ("forged" if mutate == "extra" else "accepted"): (True if mutate == "extra" else 1),
        }
        row.content_hash = configuration_hash(row.content)
    with pytest.raises(PolicyLifecycleError) as failure:
        verify_full_goal_model_original(row, goal, policy, version, epoch, NOW)
    assert failure.value.code in {"INVALID_FULL_GOAL_MODEL", "INVALID_GOAL_SOURCE"}


def test_missing_full_model_response_does_not_infer_extra_fields_or_authority() -> None:
    missing = FullGoalModelResponse(
        goal_id=GOAL,
        policy_id=POLICY,
        base_policy_version_id=VERSION,
        epoch_id=EPOCH,
        status="MODEL_MISSING",
    )
    assert missing.full_configuration is None and missing.confirmed_at is None
    assert missing.policy_effective_status is None
    assert missing.bank_authority is False and missing.dedicated_audit_event is False


@pytest.mark.parametrize("predecessor", [UUID(int=999), UUID(int=8)])
def test_supersedes_cannot_point_to_missing_or_same_original(predecessor: UUID) -> None:
    row, *_ = originals()
    row.supersedes_id = predecessor
    with pytest.raises(PolicyLifecycleError) as failure:
        _model_lineage([row])
    assert failure.value.code == "INVALID_FULL_GOAL_MODEL"


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_both_reviewed_hashes_require_actual_explicit_true(accepted: Any) -> None:
    full, base = canonical_goal_bridge(configuration())
    with pytest.raises(ValidationError):
        FullGoalConfirmationRequest.model_validate(
            {
                "expected_version_id": str(VERSION),
                "expected_epoch_id": str(EPOCH),
                "configuration": full,
                "reviewed_full_hash": configuration_hash(full),
                "reviewed_base_hash": configuration_hash(base),
                "accepted": accepted,
                "reason": "确认",
                "idempotency_key": "pure-confirm",
            }
        )


def test_request_cannot_replace_owner_or_original_financial_state() -> None:
    for field in ("user_id", "allocated_cents", "confirmed_at", "bank_authority"):
        with pytest.raises(ValidationError):
            FullGoalPreviewRequest.model_validate(
                {
                    "expected_version_id": str(VERSION),
                    "configuration": configuration(),
                    field: "forged",
                }
            )


def test_expired_real_timezone_window_rejects_before_write_but_future_start_is_not_authority() -> (
    None
):
    user = User(id=USER, timezone="Asia/Shanghai", is_simulated=True)
    _, base = canonical_goal_bridge(
        configuration()
        | {"deadline": "2026-10-04", "valid_from": "2026-10-01", "valid_until": "2026-10-04"}
    )
    with pytest.raises(PolicyLifecycleError) as failure:
        _confirmation_window(user, base, NOW)
    assert failure.value.code == "EXPIRED_FULL_GOAL_WINDOW" and failure.value.status_code == 422
    _, future = canonical_goal_bridge(
        configuration() | {"valid_from": "2026-10-06", "valid_until": "2027-01-02"}
    )
    _confirmation_window(user, future, NOW)
