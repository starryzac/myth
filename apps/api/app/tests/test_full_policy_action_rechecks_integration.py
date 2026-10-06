"""Actual FULL revoke before new bank acceptance; original cash/income rows remain exact."""

import json

import pytest
from app.tests.test_full_goal_release_execution_integration import URL, arrange_release
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_actual_full_scope_revoke_invalidates_only_unsubmitted_cash_release(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = arrange_release(client, engine)
    prepared = client.post(URL + "/prepare", json=body)
    assert prepared.status_code == 200, prepared.text
    original = prepared.json()
    assert original["original_action_status"] == "PLANNED"
    action_id = original["action_id"]
    before = json.loads(physical_snapshot(engine))
    command_body = {
        "expected_version_id": body["expected_policy_version_id"],
        "reason": "在首次银行受理前明确撤销原完整回拨范围",
        "idempotency_key": "actual-605-revoke-before-bank",
    }
    revoked = client.post(f"/api/v1/full-policies/{body['policy_id']}/revoke", json=command_body)
    assert revoked.status_code == 200, revoked.text
    result = revoked.json()
    assert result["status"] == "REVOKED"
    assert result["invalidated_action_ids"] == [action_id]
    assert result["inflight_action_ids"] == []
    assert result["bank_authority"] is result["action_dependencies_supported"] is False
    after = json.loads(physical_snapshot(engine))
    permitted_metadata = {
        "action_plans",
        "audit_events",
        "audit_epochs",
        "evidence_items",
        "full_policies",
        "full_policy_commands",
        "execution_exposures",
    }
    assert set(after) == set(before)
    for name in before.keys() - permitted_metadata:
        assert after[name] == before[name], name
    retained = physical_snapshot(engine)
    replayed = client.post(f"/api/v1/full-policies/{body['policy_id']}/revoke", json=command_body)
    assert replayed.status_code == 200 and replayed.json() == result
    assert physical_snapshot(engine) == retained
    old = client.get(f"{URL}/actions/{action_id}")
    assert old.status_code == 200 and old.json()["original_action_status"] == "INVALIDATED"
    blocked = client.post(
        f"{URL}/actions/{action_id}/execute",
        json={
            "expected_epoch_id": body["expected_epoch_id"],
            "accepted": True,
            "reviewed_effect_hash": original["original_command"]["effect_hash"],
        },
    )
    assert blocked.status_code == 409, blocked.text
    after_block = json.loads(physical_snapshot(engine))
    for name in before.keys() - permitted_metadata:
        assert after_block[name] == before[name], name
