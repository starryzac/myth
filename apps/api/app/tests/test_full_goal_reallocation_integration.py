"""One actual generated-PG preview node; no reallocation execution exists here."""

from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, Goal
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.full_goal_reallocation import ReallocationPreviewRequest
from app.domain.policy_configuration import configuration_hash
from app.services.action_contracts import ConfirmActionRequest, GoalIntent, PrepareActionRequest
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution import confirm_action, execute_action, prepare_action
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_policy_lifecycle import canonical_candidate
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/goal-reallocation/preview"


def preview_original(client: TestClient, engine: Engine, request: dict[str, Any]) -> dict[str, Any]:
    before = physical_snapshot(engine)
    response = client.post(URL, json=request)
    assert response.status_code == 200, response.text
    value: dict[str, Any] = response.json()
    assert value["user_id"] == str(DEMO_USER_ID) and value["read_only"] is True
    assert value["bank_authority"] is value["financial_grant_created"] is False
    assert value["original_goal_bridge_cross_enabled"] is False
    assert value["decision"]["candidate_amount_cents"] is None
    assert value["decision"]["cumulative_used_cents"] is None
    assert physical_snapshot(engine) == before
    return value


def test_actual_current_repair_math_preserves_default_lock_policy_limits_and_original_cash(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id_text, model_command = confirmed_existing_goal(client, engine)
    goal_id = UUID(goal_id_text)
    model_command["configuration"]["monthly_contribution"] = {
        "min_cents": 0,
        "target_cents": 20000,
        "max_cents": 20000,
    }
    model_command["configuration"]["minimum_guarantee_cents"] = 0
    model_url = f"/api/v1/goals/{goal_id}/full-model"
    reviewed = client.post(
        model_url + "/preview",
        json={key: model_command[key] for key in ("expected_version_id", "configuration")},
    )
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()
    accepted = client.post(
        model_url + "/confirm",
        json=model_command
        | {
            "accepted": True,
            "reviewed_full_hash": review["full_configuration_hash"],
            "reviewed_base_hash": review["base_configuration_hash"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    with Session(engine) as session:
        cash_id = session.scalar(
            select(Account.id)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        goal = session.get(Goal, goal_id)
        assert cash_id is not None and goal is not None
        goal_version = goal.policy_version_id
    income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="FULL304_actual_preview_payroll",
            idempotency_key="FULL304_actual_preview_payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    action = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="FULL304_actual_goal_cash",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        NOW,
    )
    assert action.effect.amount_cents == 20000
    if action.autonomy_level == "ASK_ONCE":
        confirm_action(
            engine,
            DEMO_USER_ID,
            action.action_id,
            ConfirmActionRequest(effect_hash=action.effect_hash, accepted=True),
            NOW,
        )
    allocated = execute_action(engine, DEMO_USER_ID, action.action_id, NOW)
    assert allocated.status == "SUCCEEDED" and allocated.receipt is not None
    annual = client.get("/api/v1/planning/full-annual")
    assert annual.status_code == 200, annual.text
    original = annual.json()
    points = original["projection"]["original_execution_view"]["calculation_trace"]
    point = next(row for row in points if row["day"] == 0 and row["phase"] == "BEFORE_PAYMENT")
    amounts = point["protected_cents_by_reason"]
    free = point["cash_cents"] - amounts["goal_cash"]
    # An explicit functional risk declaration constructed from the actual original
    # snapshot to leave a known 10000-cent core emergency shortfall. This is not a
    # frozen case, a benchmark result, or an independently measured financial metric.
    emergency_cents = free - amounts["obligations"] - amounts["living"] + 10000
    assert emergency_cents > 0
    epoch_id = model_command["expected_epoch_id"]
    declared = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": {
                "type": "emergency_buffer",
                "name": "明确声明的当前核心保护缺口测试",
                "amount_cents": emergency_cents,
            },
            "expected_epoch_id": epoch_id,
            "idempotency_key": "FULL304_actual_emergency_declaration",
        },
    )
    assert declared.status_code == 200, declared.text
    original_consent = client.post(
        f"/api/v1/policy-proposals/{declared.json()['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": declared.json()["configuration_hash"]},
    )
    assert original_consent.status_code == 200, original_consent.text
    disabled = {"type": "cross_goal_reallocation", "source_goal_ids": [goal_id_text]}
    created = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "CrossGoalReallocationPolicy",
            "configuration": disabled,
            "reviewed_hash": configuration_hash(
                canonical_candidate("CrossGoalReallocationPolicy", disabled)
            ),
            "accepted": True,
            "reason": "默认关闭回拨的实际规划确认",
            "idempotency_key": "FULL304_actual_disabled",
        },
    )
    assert created.status_code == 200, created.text
    policy = created.json()
    request = ReallocationPreviewRequest(
        policy_id=UUID(policy["policy_id"]),
        source_goal_id=goal_id,
        expected_policy_version_id=UUID(policy["version_id"]),
        expected_goal_policy_version_id=goal_version,
        expected_epoch_id=UUID(epoch_id),
    ).model_dump(mode="json")
    default = preview_original(client, engine, request)
    assert default["source_issues"] == []
    assert default["decision"]["math"]["status"] == "COMPUTED"
    assert default["decision"]["math"]["minimum_repair_cents"] == 10000
    assert default["decision"]["state"] == "BLOCKED"
    assert "CROSS_GOAL_REALLOCATION_DEFAULT_DISABLED" in default["decision"]["reasons"]
    enabled = {
        "type": "cross_goal_reallocation",
        "enabled": True,
        "source_goal_ids": [goal_id_text],
        "emergency_conditions": [
            "HARD_OBLIGATION_SHORTFALL",
            "LIVING_RESERVE_SHORTFALL",
            "EMERGENCY_BUFFER_SHORTFALL",
        ],
        "single_action_cap_cents": 50000,
        "total_cap_cents": 100000,
        "valid_from": "2026-10-01",
        "valid_until": "2026-10-31",
    }
    changed = client.post(
        f"/api/v1/full-policies/{policy['policy_id']}/change",
        json={
            "expected_version_id": policy["version_id"],
            "configuration": enabled,
            "reviewed_hash": configuration_hash(
                canonical_candidate("CrossGoalReallocationPolicy", enabled)
            ),
            "accepted": True,
            "reason": "仅规划回拨规则，不授予执行或伪造Goal开关",
            "idempotency_key": "FULL304_actual_planning_enabled",
        },
    )
    assert changed.status_code == 200, changed.text
    previous_request = dict(request)
    request["expected_policy_version_id"] = changed.json()["version_id"]
    pending = preview_original(client, engine, request)
    assert pending["decision"]["state"] == "UNKNOWN"
    assert pending["decision"]["math"]["minimum_repair_cents"] == 10000
    assert pending["decision"]["math"]["source_cash_releasable_above_minimum_cents"] == 20000
    assert pending["goal_ownership"]["cash"]["bank_cents"] == 20000
    assert pending["goal_ownership"]["principal"]["bank_cents"] == 0
    assert "LIFETIME_POLICY_USAGE_ORIGINALS_MISSING" in pending["decision"]["reasons"]
    assert (
        "DEDICATED_ORIGINAL_GOAL_GRANT_AND_EXECUTION_NOT_IMPLEMENTED"
        in pending["decision"]["reasons"]
    )
    before_invalid = physical_snapshot(engine)
    assert client.post(URL, json=previous_request).status_code == 409
    for extra in (
        "amount_cents",
        "now",
        "emergency",
        "bank_authority",
        "grant",
        "bank_fact",
        "income_uses",
    ):
        assert client.post(URL, json=request | {extra: True}).status_code == 422
        assert client.post(URL, json=request, params={extra: "fake"}).status_code == 422
    assert physical_snapshot(engine) == before_invalid
    forbidden_bridge = client.post(
        model_url + "/preview",
        json={
            "expected_version_id": str(goal_version),
            "configuration": model_command["configuration"]
            | {
                "cross_goal_reallocation_allowed": True,
                "cross_goal_reallocation_policy_id": policy["policy_id"],
            },
        },
    )
    assert forbidden_bridge.status_code == 409
    assert forbidden_bridge.json()["error"]["code"] == "CROSS_GOAL_AUTHORITY_NOT_IMPLEMENTED"
    assert physical_snapshot(engine) == before_invalid
    revoked = client.post(
        f"/api/v1/full-policies/{policy['policy_id']}/revoke",
        json={
            "expected_version_id": request["expected_policy_version_id"],
            "reason": "真实撤销当前规划权限",
            "idempotency_key": "FULL304_actual_revoke",
        },
    )
    assert revoked.status_code == 200, revoked.text
    after_revoke = preview_original(client, engine, request)
    assert after_revoke["decision"]["state"] == "BLOCKED"
    assert after_revoke["decision"]["math"]["minimum_repair_cents"] == 10000
    assert "CURRENT_PLANNING_CONFIRMATION_NOT_VALID" in after_revoke["decision"]["reasons"]
    with Session(engine) as session, session.begin():
        goal = session.get(Goal, goal_id)
        assert goal is not None
        goal.allocated_cents += (
            1  # Only this disposable DB; original bank/Evidence/hash never altered.
        )
    unproved = preview_original(client, engine, request)
    assert unproved["decision"]["math"]["status"] == "UNKNOWN"
    assert unproved["decision"]["math"]["minimum_repair_cents"] is None
    assert unproved["source_issues"]
