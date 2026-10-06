"""Historical completed receipts must still match independent settled bank evidence."""

from uuid import UUID

import pytest
from app.db.models import ActionReceipt
from app.tests.test_execution_api_audit import prepared_transfer
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("fault", ["executed_amount", "missing_postings", "duplicate_postings"])
def test_completed_receipt_read_and_replay_reject_tampered_bank_evidence(
    goal_client: tuple[TestClient, Engine],
    fault: str,
) -> None:
    client, engine = goal_client
    prepared = prepared_transfer(client, engine)
    action_id = prepared["action_id"]
    confirmation = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        json={"effect_hash": prepared["effect_hash"], "accepted": True},
    )
    assert confirmation.status_code == 200, confirmation.text
    completed = client.post(f"/api/v1/actions/{action_id}/execute", json={})
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "SUCCEEDED"
    receipt = completed.json()["receipt"]
    assert receipt["executed_cents"] == 12345 and receipt["posting_ids"]
    intact = all_tables(engine)
    assert client.get(f"/api/v1/actions/{action_id}/receipt").json() == receipt
    assert client.post(f"/api/v1/actions/{action_id}/execute", json={}).json()["receipt"] == receipt
    assert all_tables(engine) == intact

    with Session(engine) as session, session.begin():
        row = session.scalars(
            select(ActionReceipt).where(ActionReceipt.action_plan_id == UUID(action_id))
        ).one()
        if fault == "executed_amount":
            row.executed_cents += 1
        else:
            postings = list(row.response["posting_ids"])
            changed = [] if fault == "missing_postings" else [*postings, postings[0]]
            row.response = {**row.response, "posting_ids": changed}

    corrupted = all_tables(engine)
    reads = {
        "action": client.get(f"/api/v1/actions/{action_id}"),
        "receipt": client.get(f"/api/v1/actions/{action_id}/receipt"),
        "replay": client.post(f"/api/v1/actions/{action_id}/execute", json={}),
    }
    assert all_tables(engine) == corrupted
    statuses = {endpoint: response.status_code for endpoint, response in reads.items()}
    assert statuses == {"action": 409, "receipt": 409, "replay": 409}, statuses
    assert all(
        response.json()["error"]["code"] == "BANK_RECONCILIATION_REQUIRED"
        for response in reads.values()
    )
