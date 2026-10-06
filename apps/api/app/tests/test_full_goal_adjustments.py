"""Synthetic finite-domain/composition risks; never financial execution evidence."""

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_goal_adjustments as api
from app.domain.full_goal_adjustments import (
    DeadlineRange,
    GoalAdjustmentRequest,
    MonthlyMinimumRange,
    preview_goal_adjustments,
)
from app.domain.full_goal_conflicts import explain_goal_conflict, goal_repair_review_state_hash
from app.domain.multi_goal_allocation import (
    MultiGoalAllocationInput,
    propose_minimal_goal_repairs,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services import full_goal_adjustments as service
from app.services.dashboard_types import FinancialBoundaryCard
from app.services.full_goal_conflicts import FullGoalConflictResponse
from app.services.full_goals import (
    FullGoalModelResponse,
    FullGoalPreviewResponse,
    canonical_goal_bridge,
)
from app.services.full_joint_goal_planning import FullJointPlanningResponse
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.policy_preview_types import PolicyChangePreviewResponse
from app.tests.test_multi_goal_allocation import NOW, USER, goal, request
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

EPOCH = UUID(int=700)


def selected(
    original: MultiGoalAllocationInput, *, field: str = "deadline", **changes: Any
) -> GoalAdjustmentRequest:
    target = original.goals[0]
    option: dict[str, Any] = {
        "field": field,
        "goal_id": target.goal_id,
        "expected_version_id": target.effective_policy_version_id,
    }
    option.update(
        {"lower_date": NOW.date(), "upper_date": NOW.date() + timedelta(days=3)}
        if field == "deadline"
        else {"lower_cents": 0, "upper_cents": 0}
    )
    option.update(changes)
    return GoalAdjustmentRequest(
        expected_epoch_id=EPOCH,
        reviewed_state_hash=goal_repair_review_state_hash(original, EPOCH),
        adjustments=[cast(Any, option)],
    )


def calculate(original: MultiGoalAllocationInput, body: GoalAdjustmentRequest | None = None) -> Any:
    return preview_goal_adjustments(
        original,
        body or selected(original),
        {row.goal_id: row.source_refs[0] for row in original.goals},
    )


def due(**changes: Any) -> MultiGoalAllocationInput:
    return request(goals=[goal(deadline=NOW.date(), **changes)])


def test_explicit_deadline_repairs_nondeferrable_bound_without_old_default_changes() -> None:
    original = due()
    before = original.model_dump(mode="json")
    result = calculate(original)
    assert result.state == "PROPOSAL"
    chosen = result.hard_repair.candidates[0]
    assert chosen.deadline == NOW.date() + timedelta(days=1)
    assert result.hard_repair.changed_policy_count == 1
    assert result.hard_repair.parameter_deviation_numerator == 1
    assert result.hard_repair.hypothetical_allocation.status == "OPTIMAL"
    assert original.model_dump(mode="json") == before and original.goals[0].allow_deferral is False
    old = original.model_copy(
        update={"goals": [original.goals[0].model_copy(update={"negotiable_fields": ["deadline"]})]}
    )
    with pytest.raises(ValueError, match="explicit deferral"):
        propose_minimal_goal_repairs(old, [chosen])
    assert result.original_allow_deferral_unchanged and not result.grants_authority


def test_monthly_min_is_soft_even_when_a_due_hard_constraint_still_fails() -> None:
    original = due(monthly_min_cents=10)
    result = calculate(original, selected(original, field="monthly_min_cents", upper_cents=3))
    assert result.state == "NO_PERMITTED_REPAIR"
    assert result.hard_repair.candidates == []
    assert result.soft_preference_candidates[0].monthly_min_cents == 3
    assert result.outcomes[0].hypothetical_allocation.status == "INFEASIBLE"
    assert result.outcomes[0].monthly_min_is_soft


def test_soft_preference_preview_uses_nearest_selected_min_and_keeps_guarantee_and_ownership() -> (
    None
):
    original = request(
        goals=[goal(monthly_min_cents=10, current_owned_cents=4, minimum_guarantee_cents=8)]
    )
    before = deepcopy(original.model_dump(mode="json"))
    result = calculate(
        original, selected(original, field="monthly_min_cents", lower_cents=2, upper_cents=7)
    )
    assert result.state == "SOFT_PREFERENCE_PREVIEW" and result.hard_repair.status == "NOT_NEEDED"
    assert result.soft_preference_candidates[0].monthly_min_cents == 7
    assert result.hard_repair.changed_policy_count == 0
    assert result.outcomes[0].hypothetical_allocation.status == "OPTIMAL"
    assert result.minimum_guarantees_unchanged and result.original_ownership_and_income_unchanged
    assert original.model_dump(mode="json") == before


def test_two_goal_exact_subset_ranking_and_unchanged_third_goal() -> None:
    original = request(
        goals=[
            goal(
                target_cents=10, monthly_target_cents=10, monthly_max_cents=10, deadline=NOW.date()
            ),
            goal(
                11,
                target_cents=10,
                monthly_target_cents=10,
                monthly_max_cents=10,
                deadline=NOW.date() - timedelta(days=10),
            ),
            goal(12),
        ],
        cash=10,
    )
    options = [
        DeadlineRange(
            field="deadline",
            goal_id=row.goal_id,
            expected_version_id=row.effective_policy_version_id,
            lower_date=NOW.date(),
            upper_date=NOW.date() + timedelta(days=5),
        )
        for row in original.goals[:2]
    ]
    body = selected(original).model_copy(update={"adjustments": options})
    result = calculate(original, body)
    assert result.hard_repair.status == "PROPOSAL" and result.hard_repair.changed_policy_count == 1
    assert result.hard_repair.candidates[0].goal_id == original.goals[0].goal_id
    assert result.hard_repair.parameter_deviation_numerator == 1
    assert result.hard_repair.parameter_deviation_denominator == 1
    assert result.evaluated_subset_count == 2
    assert result.hard_repair.unaffected_goal_ids == [
        original.goals[1].goal_id,
        original.goals[2].goal_id,
    ]


def test_two_versions_required_and_hard_minimum_guarantee_cannot_be_relaxed() -> None:
    original = request(goals=[goal(deadline=NOW.date()), goal(11, deadline=NOW.date())], cash=0)
    body = selected(original).model_copy(
        update={
            "adjustments": [
                DeadlineRange(
                    field="deadline",
                    goal_id=row.goal_id,
                    expected_version_id=row.effective_policy_version_id,
                    lower_date=NOW.date(),
                    upper_date=NOW.date() + timedelta(days=3),
                )
                for row in original.goals
            ]
        }
    )
    result = calculate(original, body)
    assert result.state == "PROPOSAL" and result.hard_repair.changed_policy_count == 2
    assert result.evaluated_subset_count == 3
    hard = due(minimum_guarantee_cents=30)
    blocked = calculate(hard)
    assert (
        blocked.state == "NO_PERMITTED_REPAIR"
        and blocked.hard_repair.hypothetical_allocation is None
    )


def test_shanghai_full_deadline_day_must_fit_original_exclusive_timestamp() -> None:
    end = datetime(2026, 10, 6, 16, tzinfo=UTC)
    original = due(valid_until=end).model_copy(update={"timezone": "Asia/Shanghai"})
    assert calculate(original).state == "PROPOSAL"
    before_end = original.model_copy(
        update={
            "goals": [
                original.goals[0].model_copy(
                    update={"valid_until": end - timedelta(microseconds=1)}
                )
            ]
        }
    )
    rejected = calculate(before_end)
    assert rejected.state == "NO_PERMITTED_REPAIR"
    assert rejected.outcomes[0].reason == "ORIGINAL_EXCLUSIVE_VALIDITY_DOES_NOT_COVER_DEADLINE_DAY"


@pytest.mark.parametrize(
    "changes",
    [
        {"allow_partial": True},
        {"allow_deferral": True},
        {"deadline": date(2027, 1, 1)},
        {"policy_status": "REVOKED"},
    ],
)
def test_nonhard_or_not_authorized_does_not_invent_a_deadline_repair(
    changes: dict[str, Any],
) -> None:
    original = request(goals=[goal(**changes)])
    result = calculate(original)
    assert result.hard_repair.status == "NOT_NEEDED"
    assert all(row.candidate is None for row in result.outcomes)


@pytest.mark.parametrize("field", ["epoch", "state", "version", "source"])
def test_original_binding_cannot_be_replaced(field: str) -> None:
    original = due()
    body = selected(original)
    refs = {original.goals[0].goal_id: original.goals[0].source_refs[0]}
    if field == "epoch":
        body = body.model_copy(update={"expected_epoch_id": UUID(int=999)})
    elif field == "state":
        body = body.model_copy(update={"reviewed_state_hash": "0" * 64})
    elif field == "version":
        body = body.model_copy(
            update={
                "adjustments": [
                    body.adjustments[0].model_copy(update={"expected_version_id": UUID(int=999)})
                ]
            }
        )
    else:
        refs = {}
    with pytest.raises(ValueError):
        preview_goal_adjustments(original, body, refs)


def test_unproved_subset_cannot_claim_minimality(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import full_goal_adjustments as domain

    original = due()
    real = solve_multi_goal_allocation

    def solve(value: MultiGoalAllocationInput) -> Any:
        result = real(value)
        return (
            result.model_copy(update={"status": "UNKNOWN", "reasons": ["CAPACITY"]})
            if value.goals[0].deadline > NOW.date()
            else result
        )

    monkeypatch.setattr(domain, "solve_multi_goal_allocation", solve)
    result = calculate(original)
    assert result.state == "UNKNOWN" and result.hard_repair.changed_policy_count is None
    assert not result.hard_repair.candidates


def test_one_proved_candidate_does_not_hide_an_unknown_peer_at_the_same_policy_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain import full_goal_adjustments as domain

    original = request(
        goals=[
            goal(
                target_cents=10, monthly_target_cents=10, monthly_max_cents=10, deadline=NOW.date()
            ),
            goal(
                11,
                target_cents=10,
                monthly_target_cents=10,
                monthly_max_cents=10,
                deadline=NOW.date(),
            ),
        ],
        cash=10,
    )
    options = [
        DeadlineRange(
            field="deadline",
            goal_id=row.goal_id,
            expected_version_id=row.effective_policy_version_id,
            lower_date=NOW.date(),
            upper_date=NOW.date() + timedelta(days=3),
        )
        for row in original.goals
    ]
    real = solve_multi_goal_allocation

    def solve(value: MultiGoalAllocationInput) -> Any:
        actual = real(value)
        if value.goals[1].deadline > NOW.date():
            return actual.model_copy(update={"status": "UNKNOWN", "reasons": ["CAPACITY"]})
        return actual

    monkeypatch.setattr(domain, "solve_multi_goal_allocation", solve)
    result = calculate(original, selected(original).model_copy(update={"adjustments": options}))
    assert result.state == "UNKNOWN" and result.evaluated_subset_count <= 2
    assert result.hard_repair.candidates == []
    assert result.hard_repair.parameter_deviation_numerator is None


@pytest.mark.parametrize(
    "changes",
    [
        {"source_issues": ["BANK_SOURCE_MISSING"]},
        {"hard_protection_points": [request(cash=-1).hard_protection_points[0]]},
    ],
)
def test_actual_unknown_and_hard_base_are_not_filled_with_zero(changes: dict[str, Any]) -> None:
    original = due().model_copy(update=changes)
    result = calculate(original)
    assert result.state in {"UNKNOWN", "BASE_INFEASIBLE"} and result.outcomes == []
    assert result.hard_repair.changed_policy_count is None


def fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Session, MultiGoalAllocationInput, list[dict[str, Any]]]:
    original = due()
    target = original.goals[0]
    full, base = canonical_goal_bridge(
        {
            "type": "long_term_goal",
            "name": "SYNTHETIC_DIRECT_ONLY",
            "target_cents": target.target_cents,
            "deadline": target.deadline.isoformat(),
            "monthly_contribution": {
                "min_cents": target.monthly_min_cents,
                "target_cents": target.monthly_target_cents,
                "max_cents": target.monthly_max_cents,
            },
            "minimum_guarantee_cents": target.minimum_guarantee_cents,
            "allow_partial": target.allow_partial,
            "allow_deferral": target.allow_deferral,
        }
    )
    model = FullGoalModelResponse(
        goal_id=target.goal_id,
        policy_id=target.policy_id,
        base_policy_version_id=target.effective_policy_version_id,
        epoch_id=EPOCH,
        status="VERIFIED",
        policy_effective_status="ACTIVE",
        evidence_id=target.source_refs[0].evidence_id,
        evidence_hash=target.source_refs[0].content_hash,
        full_configuration=full,
        full_configuration_hash=configuration_hash(full),
        base_configuration_hash=configuration_hash(base),
        confirmed_at=target.confirmed_at,
    )
    conflicts = FullGoalConflictResponse(
        user_id=USER,
        epoch_id=EPOCH,
        as_of=NOW,
        state="COMPUTED",
        registered_goal_count=1,
        included_goal_ids=[target.goal_id],
        uncovered_goal_ids=[],
        current_input_hash=configuration_hash(original.model_dump(mode="json")),
        review_state_hash=goal_repair_review_state_hash(original, EPOCH),
        planning_source_digest="a" * 64,
        full_binding_hash="b" * 64,
        explanation=explain_goal_conflict(original),
        current_permission_repair=propose_minimal_goal_repairs(original, []),
        source_evidence_ids=[target.source_refs[0].evidence_id],
        reasons=[],
        limits=["SYNTHETIC_DIRECT_ONLY"],
    )
    planning = cast(
        FullJointPlanningResponse, SimpleNamespace(binding=SimpleNamespace(candidate=original))
    )
    monkeypatch.setattr(service, "_current", lambda *args: (planning, conflicts))
    monkeypatch.setattr(service, "read_full_goal_model", lambda *args: model)
    calls: list[dict[str, Any]] = []
    card = FinancialBoundaryCard(
        state="PROVEN",
        status="READY",
        safe_idle_cents=20,
        minimum_margin_cents=20,
        deficit_cents=0,
        protected_cents_by_reason={},
        current_protected_cents=0,
        current_protected_cents_by_reason={},
        current_margin_cents=20,
        constraining_date=NOW.date(),
        window_start=NOW.date(),
        window_end=NOW.date(),
        input_digest="c" * 64,
        boundary_hash="d" * 64,
        blocking_constraints=[],
        calculation_notes=[],
    )

    def preview(
        session: Session,
        user: UUID,
        goal_id: UUID,
        version: UUID,
        config: dict[str, Any],
        now: datetime,
    ) -> FullGoalPreviewResponse:
        assert user == USER and now == NOW
        new_full, new_base = canonical_goal_bridge(config)
        calls.append(deepcopy(config))
        impact = PolicyChangePreviewResponse(
            user_id=USER,
            as_of=NOW,
            timezone="UTC",
            policy_id=target.policy_id,
            expected_version_id=version,
            configuration=new_base,
            configuration_hash=configuration_hash(new_base),
            assumed_status="ACTIVE",
            assumed_valid_from=target.valid_from,
            assumed_valid_until=None,
            assumption_digest="e" * 64,
            current_fact_input_digest="f" * 64,
            hypothetical_input_digest="a" * 64,
            before=card,
            after=card,
            delta_safe_idle_cents=0,
            delta_minimum_margin_cents=0,
            notes=["SYNTHETIC_DIRECT_ONLY"],
        )
        return FullGoalPreviewResponse(
            goal_id=goal_id,
            epoch_id=EPOCH,
            expected_version_id=version,
            full_configuration=new_full,
            full_configuration_hash=configuration_hash(new_full),
            base_configuration=new_base,
            base_configuration_hash=configuration_hash(new_base),
            base_policy_impact=impact,
            notes=["SYNTHETIC_DIRECT_ONLY"],
        )

    monkeypatch.setattr(service, "preview_full_goal_model", preview)
    return cast(Session, object()), original, calls


def test_actual_service_seam_preserves_exact_original_model_and_produces_two_review_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, original, calls = fixture(monkeypatch)
    result = service.preview_current_goal_adjustments(session, USER, selected(original), NOW)
    assert result.state == "PROPOSAL" and len(calls) == 1
    preview = result.version_previews[0]
    assert preview.scope == "CURRENT_PERIOD_HARD_DEADLINE_REPAIR"
    assert (
        preview.confirmation_bindings["reviewed_full_hash"]
        == preview.actual_existing_preview.full_configuration_hash
    )
    assert (
        preview.confirmation_bindings["reviewed_base_hash"]
        == preview.actual_existing_preview.base_configuration_hash
    )
    assert preview.ready_to_submit_confirmation is False and result.writes_performed is False
    assert calls[0]["allow_deferral"] is False
    assert set(preview.confirmation_bindings).isdisjoint({"accepted", "reason", "idempotency_key"})


def test_model_identity_parameters_must_match_original_joint_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _ = fixture(monkeypatch)
    model = service.read_goal_adjustments(session, USER, NOW).goals[0].original_model
    wrong = deepcopy(model.full_configuration)
    assert wrong is not None
    wrong["minimum_guarantee_cents"] = 1
    dirty = model.model_copy(
        update={"full_configuration": wrong, "full_configuration_hash": configuration_hash(wrong)}
    )
    monkeypatch.setattr(service, "read_full_goal_model", lambda *args: dirty)
    with pytest.raises(PolicyLifecycleError, match="原联合输入"):
        service.read_goal_adjustments(session, USER, NOW)


def test_original_preview_cannot_substitute_other_owner_or_business_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, original, _ = fixture(monkeypatch)
    real = cast(Callable[..., FullGoalPreviewResponse], service.__dict__["preview_full_goal_model"])

    def dirty(*args: Any) -> FullGoalPreviewResponse:
        actual = real(*args)
        impact = actual.base_policy_impact.model_copy(update={"user_id": UUID(int=999)})
        return actual.model_copy(update={"base_policy_impact": impact})

    monkeypatch.setattr(service, "preview_full_goal_model", dirty)
    with pytest.raises(PolicyLifecycleError, match="双hash原预览"):
        service.preview_current_goal_adjustments(session, USER, selected(original), NOW)


def test_real_json_route_parses_exact_uuid_dates_and_rejects_fake_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, original, _ = fixture(monkeypatch)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW

    @app.exception_handler(PolicyLifecycleError)
    def failure(request: Request, error: PolicyLifecycleError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    with TestClient(app) as client:
        body = selected(original).model_dump(mode="json")
        assert client.get("/api/v1/planning/full-goal-adjustments").status_code == 200
        result = client.post("/api/v1/planning/full-goal-adjustments/preview", json=body)
        assert result.status_code == 200 and result.json()["state"] == "PROPOSAL"
        for field in ("amount_cents", "now", "accepted", "bank_facts", "authority"):
            assert (
                client.post(
                    "/api/v1/planning/full-goal-adjustments/preview", json=body | {field: 1}
                ).status_code
                == 422
            )
        assert (
            client.get("/api/v1/planning/full-goal-adjustments?now=2026-01-01").status_code == 422
        )
        assert (
            client.post(
                "/api/v1/planning/full-goal-adjustments/preview?amount=10", json=body
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/planning/full-goal-adjustments/preview",
                json=body | {"reviewed_state_hash": "0" * 64},
            ).status_code
            == 409
        )


@pytest.mark.parametrize(
    "change",
    [
        {"lower_cents": True},
        {"upper_cents": "0"},
        {"lower_cents": -1},
        {"lower_cents": 4, "upper_cents": 3},
        {"bank_authority": True},
    ],
)
def test_monthly_range_strict_numbers_and_fields(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MonthlyMinimumRange.model_validate(
            {
                "field": "monthly_min_cents",
                "goal_id": UUID(int=10),
                "expected_version_id": UUID(int=310),
                "lower_cents": 0,
                "upper_cents": 0,
            }
            | change
        )


def test_duplicate_goals_and_month_min_increase_are_not_silent_new_permissions() -> None:
    original = due()
    body = selected(original, field="monthly_min_cents", upper_cents=2)
    with pytest.raises(ValueError, match="reduction"):
        calculate(original, body)
    with pytest.raises(ValidationError):
        GoalAdjustmentRequest.model_validate(
            {**body.model_dump(), "adjustments": body.adjustments * 2}
        )
