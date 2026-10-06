"""Actual-service PG candidates; root schedules these, no formal experiment claims."""

from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.external_bank_facts import ingest_external_fact
from app.services.income_ledger import read_income_state
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def full_dynamic_goal(client: TestClient, engine: Engine) -> tuple[str, str]:
    goal_id, command = confirmed_existing_goal(client, engine)
    config: dict[str, Any] = command["configuration"]
    config["monthly_contribution"] = {"min_cents": 0, "target_cents": 10000, "max_cents": 20000}
    config["minimum_guarantee_cents"] = 0
    url = f"/api/v1/goals/{goal_id}/full-model"
    preview = client.post(
        url + "/preview",
        json={key: command[key] for key in ("expected_version_id", "configuration")},
    )
    assert preview.status_code == 200
    value = preview.json()
    confirmed = client.post(
        url + "/confirm",
        json=command
        | {
            "accepted": True,
            "reviewed_full_hash": value["full_configuration_hash"],
            "reviewed_base_hash": value["base_configuration_hash"],
        },
    )
    assert confirmed.status_code == 200
    return goal_id, confirmed.json()["evidence_id"]


def test_actual_dynamic_read_and_executed_contribution_track_original_ledger_without_repeat(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, model_evidence = full_dynamic_goal(client, engine)
    url = f"/api/v1/goals/{goal_id}/dynamic-reserve"
    with Session(engine) as session:
        cash = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        )
        assert cash is not None
        fact = ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash.id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="full-dynamic-actual-payroll",
            idempotency_key="full-dynamic-actual-payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        )
    bank = ingest_external_fact(engine, DEMO_USER_ID, fact, NOW)
    assert bank.bank_status == "SETTLED" and bank.projection_status == "PROJECTED"
    assert len(bank.economic_posting_ids) == 2 and bank.transaction_id is not None
    before = physical_snapshot(engine)
    first = client.get(url)
    assert first.status_code == 200
    value = first.json()
    assert value["state"] == "COMPUTED" and value["source_issues"] == []
    assert model_evidence in value["source_evidence_ids"]
    reserve = value["reserve"]
    assert reserve["status"] == "READY" and reserve["dynamic_month_total_cents"] == 20000
    assert reserve["current_owned_cents"] == reserve["current_month_contributed_cents"] == 0
    assert reserve["suggested_additional_cents"] == 20000
    assert reserve["eligible_available_income_cents"] == 600000
    assert reserve["future_income_included_cents"] == 0 and value["grants_authority"] is False
    assert client.get(url).json() == value and physical_snapshot(engine) == before

    # The existing execution contract still uses the explicitly confirmed
    # nominal MVP target. The new read-only pace does not inject an amount.
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "full-dynamic-original-nominal-action",
            "intent": {"kind": "allocate_goal", "goal_id": goal_id},
        },
    )
    assert prepared.status_code == 200
    action = prepared.json()
    assert action["effect"]["amount_cents"] == 10000
    if action["autonomy_level"] == "ASK_ONCE":
        review = client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={"effect_hash": action["effect_hash"], "accepted": True},
        )
        assert review.status_code == 200
    else:
        assert action["autonomy_level"] == "AUTO_EXECUTE"
    executed = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert executed.status_code == 200 and executed.json()["status"] == "SUCCEEDED"
    assert executed.json()["receipt"]["executed_cents"] == 10000
    with Session(engine) as session:
        income = read_income_state(session, DEMO_USER_ID, NOW)
        origin = next(
            row for row in income.ledger.origins if row.origin_transaction_id == bank.transaction_id
        )
        fragments = [
            row
            for row in income.ledger.fragments
            if row.origin_transaction_id == origin.origin_transaction_id
        ]
        assert sum(row.assigned_cents for row in fragments) == 10000
        assert sum(row.available_cents for row in fragments) == 590000
    settled = physical_snapshot(engine)
    after = client.get(url)
    assert after.status_code == 200 and after.json()["source_issues"] == []
    remainder = after.json()["reserve"]
    assert remainder["current_owned_cents"] == remainder["current_month_contributed_cents"] == 10000
    assert remainder["dynamic_month_total_cents"] == reserve["dynamic_month_total_cents"]
    assert remainder["suggested_additional_cents"] == 10000
    assert remainder["eligible_available_income_cents"] == 590000
    assert client.get(url).json() == after.json()
    assert physical_snapshot(engine) == settled


def test_dynamic_read_missing_full_model_and_tampered_balance_remain_unknown_readonly(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, _ = confirmed_existing_goal(client, engine)
    url = f"/api/v1/goals/{goal_id}/dynamic-reserve"
    before = physical_snapshot(engine)
    missing = client.get(url)
    assert missing.status_code == 200
    assert missing.json()["state"] == "UNKNOWN" and missing.json()["reserve"] is None
    assert "MISSING_FULL_GOAL_MODEL" in {row["code"] for row in missing.json()["source_issues"]}
    for params in (
        {"as_of": "2099-01-01"},
        {"available_cents": "99999999"},
        {"user_id": str(UUID(int=9))},
    ):
        assert client.get(url, params=params).status_code == 422
    assert physical_snapshot(engine) == before
    with Session(engine) as session, session.begin():
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        cash.balance_cents += 1
    tampered = physical_snapshot(engine)
    response = client.get(url)
    assert response.status_code == 200 and response.json()["reserve"] is None
    assert response.json()["state"] == "UNKNOWN" and response.json()["source_issues"]
    assert physical_snapshot(engine) == tampered
