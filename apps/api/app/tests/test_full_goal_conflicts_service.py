"""Read-only production composition risks using doubles, not financial runtime proof."""

from typing import Any, cast
from uuid import UUID

import pytest
from app.db.models import AuditEpoch
from app.domain.full_goal_conflicts import GoalRepairPreviewRequest, MonthlyMaxPlanningAdjustment
from app.domain.multi_goal_allocation import MultiGoalAllocationInput, solve_multi_goal_allocation
from app.domain.policy_configuration import configuration_hash
from app.services import full_goal_conflicts as service
from app.services.dashboard_types import FinancialBoundaryCard
from app.services.full_goals import (
    FullGoalModelResponse,
    FullGoalPreviewResponse,
    canonical_goal_bridge,
)
from app.services.full_joint_goal_planning import (
    FullJointPlanningResponse,
    full_joint_goal_planning,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.policy_preview_types import PolicyChangePreviewResponse
from app.tests.test_full_joint_goal_planning_service import setup
from app.tests.test_full_projection import NOW, USER
from sqlalchemy.orm import Session

EPOCH = UUID(int=700)


def current(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Session, FullJointPlanningResponse, FullGoalModelResponse, list[tuple[Any, ...]]]:
    session, _, _, _, _, _ = setup(monkeypatch)
    planning = full_joint_goal_planning(session, USER, NOW)
    assert planning.binding is not None
    bound = planning.binding
    original = bound.candidate
    target = original.goals[0].model_copy(
        update={
            "monthly_min_cents": 0,
            "monthly_target_cents": 5,
            "monthly_max_cents": 5,
            "minimum_guarantee_cents": 8,
        }
    )
    candidate = MultiGoalAllocationInput.model_validate(
        original.model_copy(update={"goals": [target]}).model_dump()
    )
    planning = planning.model_copy(
        update={
            "binding": bound.model_copy(update={"candidate": candidate}),
            "allocation": solve_multi_goal_allocation(candidate),
        }
    )
    full, base = canonical_goal_bridge(
        {
            "type": "long_term_goal",
            "name": "合成只读组合测试",
            "target_cents": target.target_cents,
            "deadline": target.deadline.isoformat(),
            "monthly_contribution": {"min_cents": 0, "target_cents": 5, "max_cents": 5},
            "minimum_guarantee_cents": 8,
            "allow_partial": True,
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
    monkeypatch.setattr(service, "full_joint_goal_planning", lambda *args: planning)
    monkeypatch.setattr(
        service,
        "current_audit_epoch",
        lambda *args: AuditEpoch(id=EPOCH, user_id=USER, status="OPEN"),
    )
    monkeypatch.setattr(service, "read_full_goal_model", lambda *args: model)
    calls: list[tuple[Any, ...]] = []

    def preview(*args: Any) -> FullGoalPreviewResponse:
        calls.append(args)
        current_session, user, goal_id, version, configuration, now = args
        assert current_session is session and user == USER and now == NOW
        new_full, new_base = canonical_goal_bridge(configuration)
        card = FinancialBoundaryCard(
            state="PROVEN",
            status="READY",
            safe_idle_cents=11,
            minimum_margin_cents=11,
            deficit_cents=0,
            protected_cents_by_reason={},
            current_protected_cents=9,
            current_protected_cents_by_reason={},
            current_margin_cents=11,
            constraining_date=NOW.date(),
            window_start=NOW.date(),
            window_end=NOW.date(),
            input_digest="a" * 64,
            boundary_hash="b" * 64,
            blocking_constraints=[],
            calculation_notes=[],
        )
        impact = PolicyChangePreviewResponse(
            user_id=USER,
            as_of=NOW,
            timezone="Asia/Shanghai",
            policy_id=target.policy_id,
            expected_version_id=version,
            configuration=new_base,
            configuration_hash=configuration_hash(new_base),
            assumed_status="ACTIVE",
            assumed_valid_from=target.valid_from,
            assumed_valid_until=None,
            assumption_digest="a" * 64,
            current_fact_input_digest="b" * 64,
            hypothetical_input_digest="c" * 64,
            before=card,
            after=card,
            delta_safe_idle_cents=0,
            delta_minimum_margin_cents=0,
            notes=["TOOL_ONLY"],
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
            notes=["TOOL_ONLY"],
        )

    monkeypatch.setattr(service, "preview_full_goal_model", preview)
    return session, planning, model, calls


def command(
    response: service.FullGoalConflictResponse, planning: FullJointPlanningResponse
) -> GoalRepairPreviewRequest:
    assert (
        response.epoch_id is not None
        and response.review_state_hash is not None
        and planning.binding is not None
    )
    target = planning.binding.candidate.goals[0]
    return GoalRepairPreviewRequest(
        expected_epoch_id=response.epoch_id,
        reviewed_state_hash=response.review_state_hash,
        adjustments=[
            MonthlyMaxPlanningAdjustment(
                goal_id=target.goal_id,
                expected_version_id=target.effective_policy_version_id,
                minimum_new_monthly_max_cents=6,
                maximum_new_monthly_max_cents=20,
            )
        ],
    )


def test_current_conflict_and_selected_preview_use_original_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, planning, model, calls = current(monkeypatch)
    original = planning.model_dump(mode="json")
    read = service.read_full_goal_conflicts(session, USER, NOW)
    assert read.state == "COMPUTED" and read.explanation is not None
    assert read.explanation.conflict.status == "MINIMAL_CONFLICT"
    assert read.current_permission_repair is not None
    assert read.current_permission_repair.status == "NO_PERMITTED_REPAIR"
    proposed = service.preview_full_goal_repairs(session, USER, command(read, planning), NOW)
    assert proposed.state == "PROPOSAL" and len(calls) == 1 and proposed.proposal is not None
    version = proposed.version_previews[0]
    assert version.proposed_monthly_max_cents == 8 and version.original_monthly_max_cents == 5
    assert version.original_full_configuration == model.full_configuration
    assert (
        version.actual_existing_preview.full_configuration["monthly_contribution"]["max_cents"] == 8
    )
    assert (
        version.confirmation_bindings["reviewed_full_hash"]
        == version.actual_existing_preview.full_configuration_hash
    )
    assert (
        version.confirmation_bindings["reviewed_base_hash"]
        == version.actual_existing_preview.base_configuration_hash
    )
    assert not {"accepted", "reason", "idempotency_key"} & version.confirmation_bindings.keys()
    assert version.missing_explicit_user_fields == ["accepted", "reason", "idempotency_key"]
    assert proposed.grants_authority is False and proposed.writes_performed is False
    assert planning.model_dump(mode="json") == original
    cast(Any, session).add.assert_not_called()
    cast(Any, session).commit.assert_not_called()


@pytest.mark.parametrize("violation", ["bank", "audit", "goals", "source", "hash"])
def test_missing_current_sources_keep_denominator_without_fake_explanation_or_repair(
    violation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, planning, _, calls = current(monkeypatch)
    assert planning.binding is not None and planning.allocation is not None
    good = service.read_full_goal_conflicts(session, USER, NOW)
    changes: dict[str, Any] = {}
    if violation == "bank":
        changes["original_joint"] = planning.original_joint.model_copy(
            update={"independent_bank_projection_matched": False}
        )
    elif violation == "audit":
        changes["full_protection"] = planning.full_protection.model_copy(
            update={
                "audit": planning.full_protection.audit.model_copy(update={"status": "INVALID"})
            }
        )
    elif violation == "goals":
        changes["original_joint"] = planning.original_joint.model_copy(
            update={"registered_goal_count": 2, "uncovered_goal_ids": [UUID(int=999)]}
        )
    elif violation == "hash":
        changes["allocation"] = planning.allocation.model_copy(update={"input_hash": "0" * 64})
    else:
        changes["binding"] = planning.binding.model_copy(
            update={
                "candidate": planning.binding.candidate.model_copy(
                    update={"source_issues": ["MISSING_ORIGINAL"]}
                )
            }
        )
    monkeypatch.setattr(
        service, "full_joint_goal_planning", lambda *args: planning.model_copy(update=changes)
    )
    actual = service.read_full_goal_conflicts(session, USER, NOW)
    assert (
        actual.state == "UNKNOWN"
        and actual.explanation is None
        and actual.current_permission_repair is None
    )
    assert actual.review_state_hash is None and actual.included_goal_ids == good.included_goal_ids
    result = service.preview_full_goal_repairs(session, USER, command(good, planning), NOW)
    assert result.state == "UNKNOWN" and result.proposal is None and result.version_previews == []
    assert calls == []


@pytest.mark.parametrize("violation", ["epoch", "review", "version", "model_hash"])
def test_stale_original_and_current_model_block_new_preview(
    violation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, planning, model, calls = current(monkeypatch)
    body = command(service.read_full_goal_conflicts(session, USER, NOW), planning)
    if violation == "epoch":
        body = body.model_copy(update={"expected_epoch_id": UUID(int=999)})
    elif violation == "review":
        body = body.model_copy(update={"reviewed_state_hash": "0" * 64})
    elif violation == "version":
        body = body.model_copy(
            update={
                "adjustments": [
                    body.adjustments[0].model_copy(update={"expected_version_id": UUID(int=999)})
                ]
            }
        )
    else:
        monkeypatch.setattr(
            service,
            "read_full_goal_model",
            lambda *args: model.model_copy(update={"full_configuration_hash": "0" * 64}),
        )
    with pytest.raises(PolicyLifecycleError) as error:
        service.preview_full_goal_repairs(session, USER, body, NOW)
    assert error.value.status_code == 409 and calls == []
