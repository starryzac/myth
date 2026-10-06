"""Synthetic joint whole-consent risks; no bank or PostgreSQL acceptance."""

import copy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.decision_trace import build_trace
from app.domain.decision_trace_types import DecisionTrace, TraceEvidence
from app.domain.full_joint_goal_execution import (
    ALGORITHM,
    MARKER,
    FullJointFrozenPlan,
    FullJointGoalConfirmRequest,
    derive_joint_execution_plan,
    whole_consent_content,
)
from app.domain.full_joint_goal_execution_trace import _whole_consent
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_joint_goal_execution import fixture


@pytest.fixture(scope="module")
def plan() -> FullJointFrozenPlan:
    result = derive_joint_execution_plan(fixture())
    assert result.plan is not None, result.reasons
    return result.plan


def consent_trace(plan: FullJointFrozenPlan, change: str | None = None) -> DecisionTrace:
    actor = LocalActorPrincipal(
        user_id=plan.user_id,
        role="USER",
        session_id=UUID(int=771),
        issued_at=plan.prepared_at - timedelta(minutes=1),
        expires_at=plan.prepared_at + timedelta(minutes=14),
    )
    body = FullJointGoalConfirmRequest(
        accepted=True,
        reviewed_plan_hash=plan.plan_hash,
        expected_epoch_id=plan.epoch_id,
        idempotency_key="synthetic-whole-confirmation",
    )
    value: dict[str, Any] = copy.deepcopy(
        whole_consent_content(plan, body, actor, plan.prepared_at).model_dump(mode="json")
    )
    if change == "role":
        value["actor"]["role"] = "AGENT"
    elif change == "owner":
        value["actor"]["user_id"] = str(UUID(int=999))
    elif change == "session_label":
        value["actor_session_id"] = str(UUID(int=999))
    elif change == "expired_actor":
        value["actor"]["issued_at"] = (plan.prepared_at - timedelta(minutes=15)).isoformat()
        value["actor"]["expires_at"] = plan.prepared_at.isoformat()
    elif change == "different_plan":
        value["plan_hash"] = "1" * 64
    elif change == "false_accept":
        value["accepted"] = False
    elif change == "false_original_accept":
        value["original_request"]["accepted"] = False
        value["request_hash"] = configuration_hash(value["original_request"])
    digest = configuration_hash(value)
    source = TraceEvidence(
        id=uuid5(plan.plan_id, "whole-confirmation:" + plan.plan_hash),
        user_id=plan.user_id,
        evidence_level="USER_CONFIRMED_ACTION",
        source_type="USER_JOINT_GOAL_CONFIRMATION",
        source_ref=str(plan.plan_id),
        content=value,
        content_hash=digest,
        captured_content_hash=digest,
        content_integrity="VERIFIED",
        status_at_decision="VALID",
        observed_at=plan.prepared_at,
        valid_from=plan.prepared_at,
        valid_to=plan.expires_at,
    )
    return build_trace(
        run_id=uuid5(plan.plan_id, "whole-confirmation-proof"),
        user_id=plan.user_id,
        action_id=None,
        parent_run_id=uuid5(plan.plan_id, "planning-proof"),
        phase="EVALUATION",
        as_of=plan.prepared_at,
        algorithm_versions={MARKER: ALGORITHM},
        inputs={
            "joint_execution_input": plan.inputs.model_dump(mode="json"),
            "joint_confirmation_request": body.model_dump(mode="json"),
        },
        sources=[] if change == "missing" else [source],
        policies=[],
        outcome={
            "joint_execution_plan": plan.model_dump(mode="json"),
            "joint_confirmation": value,
            "decision_status": "COMPUTED",
        },
    )


def test_original_whole_user_consent_binds_actual_actor_and_exact_original_plan(
    plan: FullJointFrozenPlan,
) -> None:
    trace = consent_trace(plan)
    before = trace.model_dump_json()
    _whole_consent(trace, plan)
    assert trace.model_dump_json() == before


@pytest.mark.parametrize(
    "change",
    [
        "role",
        "owner",
        "session_label",
        "expired_actor",
        "different_plan",
        "false_accept",
        "false_original_accept",
        "missing",
    ],
)
def test_rehashed_whole_consent_cannot_gain_user_identity_or_replace_plan(
    plan: FullJointFrozenPlan,
    change: str,
) -> None:
    with pytest.raises(ValueError):
        _whole_consent(consent_trace(plan, change), plan)
