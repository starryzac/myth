"""Owned isolated PG candidate only; Root schedules this read-only risk node."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.api.dependencies import get_now
from app.db.models import Account, AuditEpoch
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_bill_hypothesis_replays_preserves_all_physical_originals_and_unknown_source(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    with Session(engine) as session:
        epoch = session.scalar(select(AuditEpoch).where(AuditEpoch.status == "OPEN"))
        assert epoch is not None
        now = max(datetime.now(UTC), epoch.opened_at + timedelta(microseconds=1))
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: now
    # Actual Main and original RR/READ ONLY dependency must register the new route.
    # No override fabricates a bank source or a computed result.
    base = "/api/v1/scenario-risk-review"
    before = physical_snapshot(engine)
    read = client.get(base + "/context")
    assert read.status_code == 200, read.text
    context = read.json()
    assert context["bills"] and context["baseline_full"] is not None
    assert context["inventory_counts"]["bills"] == len(context["bills"])
    bill = next(row for row in context["bills"] if row["evidence_ids"])
    body = {
        "expected_epoch_id": context["epoch_id"],
        "expected_source_hash": context["source_hash"],
        "expected_engine_hash": context["engine_hash"],
        "hypothesis": {
            "kind": "BILL",
            "bill_id": bill["bill_id"],
            "remaining_due_cents": bill["total_cents"] - bill["paid_cents"] + 1,
            "due_offset_days": 0,
        },
    }
    first = client.post(base + "/compare", json=body)
    second = client.post(base + "/compare", json=body)
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    result = first.json()
    assert result["status"] == "PROJECTED"
    assert result["delta_safe_idle_cents"] <= 0
    assert result["original_selected"] == bill
    assert result["hypothetical_selected"]["original_paid_cents_retained"] == bill["paid_cents"]
    assert len(result["hypothetical_full"]["calculation_trace"]) == 1098
    assert not result["writes_facts"] and not result["changes_bank_originals"]
    assert not result["grants_authority"] and not result["executes_funds"]
    assert physical_snapshot(engine) == before
    assert (
        client.post(base + "/compare", json={**body, "expected_source_hash": "f" * 64}).status_code
        == 409
    )
    assert (
        client.post(
            base + "/compare",
            json={**body, "hypothesis": {**body["hypothesis"], "bill_id": str(uuid4())}},
        ).status_code
        == 409
    )
    for extra in ("user_id", "clock", "bank_receipt", "safe_idle_cents"):
        assert client.post(base + "/compare", json={**body, extra: "untrusted"}).status_code == 422
    assert physical_snapshot(engine) == before
    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        account.balance_cents += 1
    # Keep the original negative mutation in this disposable owned DB. No restore.
    broken = physical_snapshot(engine)
    unknown = client.get(base + "/context")
    assert unknown.status_code == 200, unknown.text
    value = unknown.json()
    assert value["baseline_full"] is None and value["source_issues"]
    changed_body = {**body, "expected_source_hash": value["source_hash"]}
    response = client.post(base + "/compare", json=changed_body)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "UNKNOWN"
    assert report["hypothetical_full"] is None and report["delta_safe_idle_cents"] is None
    assert physical_snapshot(engine) == broken
