"""Owned actual PG/HTTP candidate; collection does not prove runtime acceptance."""

from uuid import UUID

import pytest
from app.domain.demo_identity import DEMO_USER_ID
from app.services.audit_chain import verify_audit_chain
from app.tests.test_full_action_set_boundary_integration import financial_originals
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_recovery_composed_original_observation_replay_keeps_financial_rows(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    path = "/api/v1/boundary/recovery-composed-action-set"
    before = physical_snapshot(engine)
    current = client.get(path + "/current")
    assert current.status_code == 200, current.text
    snapshot = current.json()
    assert snapshot["bank_authority"] is False and snapshot["financial_write"] is False
    assert physical_snapshot(engine) == before
    payload = {
        "expected_epoch_id": snapshot["epoch_id"],
        "idempotency_key": "actual-recovery-composed-original",
        "previous_observation_run_id": None,
    }
    observed = client.post(path + "/observe", json=payload)
    assert observed.status_code == 200, observed.text
    value = observed.json()
    assert value["user_id"] == str(DEMO_USER_ID) and value["original_request"] == payload
    assert value["snapshot"] == snapshot
    assert value["notification_support"] == "NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"
    assert value["financial_write"] is False and value["grants_authority"] is False
    assert financial_originals(physical_snapshot(engine)) == financial_originals(before)
    original = client.get(path + "/observations/" + value["observation_run_id"])
    assert original.status_code == 200 and original.json() == value, original.text
    after = physical_snapshot(engine)
    replay = client.post(path + "/observe", json=payload)
    assert replay.status_code == 200 and replay.json() == {**value, "idempotent_replay": True}
    assert physical_snapshot(engine) == after
    payload["previous_observation_run_id"] = value["observation_run_id"]
    conflict = client.post(path + "/observe", json=payload)
    assert conflict.status_code == 409, conflict.text
    assert physical_snapshot(engine) == after
    payload["idempotency_key"] = "actual-recovery-composed-original-child"
    child = client.post(path + "/observe", json=payload)
    assert child.status_code == 200, child.text
    child_value = child.json()
    assert child_value["previous_snapshot_hash"] == value["snapshot"]["snapshot_hash"]
    assert child_value["previous_observation_run_id"] == value["observation_run_id"]
    if not value["global_action_set_complete"]:
        assert child_value["kind"] is None and child_value["global_action_set_complete"] is False
    original_child = client.get(path + "/observations/" + child_value["observation_run_id"])
    assert original_child.status_code == 200 and original_child.json() == child_value
    assert financial_originals(physical_snapshot(engine)) == financial_originals(before)
    with Session(engine) as session:
        audit = verify_audit_chain(session, DEMO_USER_ID, NOW)
        assert audit.status == "VALID"
    assert UUID(child_value["observation_run_id"]) != UUID(value["observation_run_id"])
