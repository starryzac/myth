"""One isolated actual PG command: grant consent is metadata, not a financial effect."""

import json
from typing import Any
from uuid import UUID

import pytest
from app.db.models import EvidenceItem, Goal
from app.domain.full_goal_release_authorization import SOURCE
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_lifecycle import canonical_candidate
from app.tests.test_full_goals_api import confirmed_existing_goal, money_originals
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/goal-release-authorizations"


def test_actual_dedicated_permission_binds_scope_replays_after_revocation_and_changes_no_money(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, command = confirmed_existing_goal(client, engine)
    model_url = f"/api/v1/goals/{goal_id}/full-model"
    preview = client.post(
        model_url + "/preview",
        json={key: command[key] for key in ("expected_version_id", "configuration")},
    )
    assert preview.status_code == 200, preview.text
    hashes = preview.json()
    accepted = client.post(
        model_url + "/confirm",
        json=command
        | {
            "accepted": True,
            "reviewed_full_hash": hashes["full_configuration_hash"],
            "reviewed_base_hash": hashes["base_configuration_hash"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    config = {
        "type": "cross_goal_reallocation",
        "enabled": True,
        "source_goal_ids": [goal_id],
        "emergency_conditions": ["HARD_OBLIGATION_SHORTFALL", "LIVING_RESERVE_SHORTFALL"],
        "single_action_cap_cents": 10000,
        "total_cap_cents": 50000,
        "valid_from": "2026-10-01",
        "valid_until": "2026-10-31",
    }
    policy_response = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "CrossGoalReallocationPolicy",
            "configuration": config,
            "reviewed_hash": configuration_hash(
                canonical_candidate("CrossGoalReallocationPolicy", config)
            ),
            "accepted": True,
            "reason": "仅有限回拨规划规则，另需专用范围确认",
            "idempotency_key": "actual-release-planning-only",
        },
    )
    assert policy_response.status_code == 200, policy_response.text
    policy = policy_response.json()
    assert policy["bank_authority"] is False
    policy_id = policy["policy_id"]
    base: dict[str, Any] = {
        "expected_epoch_id": command["expected_epoch_id"],
        "expected_policy_version_id": policy["version_id"],
    }
    before = physical_snapshot(engine)
    reviewed = client.post(f"{URL}/policies/{policy_id}/preview", json=base)
    assert reviewed.status_code == 200, reviewed.text
    assert physical_snapshot(engine) == before
    scope = reviewed.json()
    assert scope["financial_permission_recorded"] is False
    assert scope["scope"]["source_goals"][0]["goal_id"] == goal_id
    assert scope["scope"]["single_action_cap_cents"] == 10000
    assert scope["scope"]["total_cap_cents"] == 50000
    assert scope["scope"]["source_goals"][0]["minimum_guarantee_cents"] == 15000
    body = base | {
        "accepted": True,
        "reviewed_scope_hash": scope["scope_hash"],
        "idempotency_key": "actual-explicit-emergency-release",
    }
    confirmed = client.post(f"{URL}/policies/{policy_id}/confirm", json=body)
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert receipt["current_scope_status"] == "CURRENT"
    assert receipt["original_authorization"]["scope_hash"] == scope["scope_hash"]
    assert receipt["submits_bank_operation"] is False
    assert receipt["execution_support"] == "NOT_IMPLEMENTED"
    assert money_originals(physical_snapshot(engine)) == money_originals(before)
    with Session(engine) as session:
        goal = session.get(Goal, UUID(goal_id))
        assert goal is not None and goal.cross_goal_reallocation_allowed is False
        proofs = list(
            session.scalars(select(EvidenceItem).where(EvidenceItem.source_type == SOURCE))
        )
        assert len(proofs) == 1 and proofs[0].content_hash == receipt["evidence_hash"]
        assert proofs[0].content == receipt["original_authorization"]
    recorded = physical_snapshot(engine)
    retry = client.post(f"{URL}/policies/{policy_id}/confirm", json=body)
    assert retry.status_code == 200 and retry.json()["idempotent_replay"] is True
    assert retry.json()["original_authorization"] == receipt["original_authorization"]
    assert physical_snapshot(engine) == recorded
    changed = client.post(
        f"{URL}/policies/{policy_id}/confirm", json=body | {"reviewed_scope_hash": "f" * 64}
    )
    assert changed.status_code == 409
    assert physical_snapshot(engine) == recorded
    for value in (1, "true", False):
        assert (
            client.post(
                f"{URL}/policies/{policy_id}/confirm", json=body | {"accepted": value}
            ).status_code
            == 422
        )
    assert physical_snapshot(engine) == recorded
    revoked = client.post(
        f"/api/v1/full-policies/{policy_id}/revoke",
        json={
            "expected_version_id": policy["version_id"],
            "reason": "撤销回拨条件",
            "idempotency_key": "actual-release-revoke",
        },
    )
    assert revoked.status_code == 200, revoked.text
    after_revoke = physical_snapshot(engine)
    lookup = client.get(
        f"{URL}/commands/{base['expected_epoch_id']}/by-key/{body['idempotency_key']}"
    )
    assert lookup.status_code == 200, lookup.text
    stale = lookup.json()["original"]
    assert stale["current_scope_status"] == "STALE"
    assert stale["original_authorization"] == receipt["original_authorization"]
    assert stale["evidence_hash"] == receipt["evidence_hash"]
    assert physical_snapshot(engine) == after_revoke
    assert money_originals(after_revoke) == money_originals(before)
    assert json.loads(after_revoke)["action_receipts"] == json.loads(before)["action_receipts"]
