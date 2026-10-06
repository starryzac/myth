"""Owned PostgreSQL proofs for actual current-period joint planning, not final acceptance."""

from typing import Any

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
URL = "/api/v1/planning/current-goal-allocation"


def test_joint_planning_retains_missing_model_and_bank_tamper_without_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, _ = confirmed_existing_goal(client, engine)
    before = physical_snapshot(engine)
    response = client.get(URL)
    assert response.status_code == 200
    value = response.json()
    assert value["state"] == "UNKNOWN"
    assert value["registered_goal_count"] == 1
    assert value["included_goal_ids"] == []
    assert value["uncovered_goal_ids"] == [goal_id]
    assert "MISSING_FULL_GOAL_MODEL" in {issue["code"] for issue in value["source_issues"]}
    assert value["allocation"] is None or value["allocation"]["status"] == "UNKNOWN"
    assert value["grants_authority"] is False
    for query in (
        {"user_id": str(DEMO_USER_ID)},
        {"as_of": "2099-01-01"},
        {"income_cents": "1000000000000"},
        {"horizon_days": "1"},
    ):
        assert client.get(URL, params=query).status_code == 422
    assert physical_snapshot(engine) == before
    with Session(engine) as session, session.begin():
        cash = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert cash is not None
        cash.balance_cents += 1
    tampered = physical_snapshot(engine)
    failure = client.get(URL)
    assert failure.status_code == 200
    unknown = failure.json()
    assert unknown["state"] == "UNKNOWN" and unknown["source_issues"]
    assert unknown["independent_bank_projection_matched"] is False
    assert unknown["uncovered_goal_ids"] == [goal_id]
    assert unknown["allocation"] is None or unknown["allocation"]["status"] == "UNKNOWN"
    assert physical_snapshot(engine) == tampered


def test_joint_planning_uses_actual_full_confirmation_and_unassigned_income_readonly(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, command = confirmed_existing_goal(client, engine)
    configuration: dict[str, Any] = command["configuration"]
    configuration["monthly_contribution"] = {
        "min_cents": 0,
        "target_cents": 10000,
        "max_cents": 20000,
    }
    configuration["minimum_guarantee_cents"] = 0
    model_url = f"/api/v1/goals/{goal_id}/full-model"
    preview = client.post(
        model_url + "/preview",
        json={key: command[key] for key in ("expected_version_id", "configuration")},
    )
    assert preview.status_code == 200
    review = preview.json()
    confirmation = client.post(
        model_url + "/confirm",
        json=command
        | {
            "accepted": True,
            "reviewed_full_hash": review["full_configuration_hash"],
            "reviewed_base_hash": review["base_configuration_hash"],
        },
    )
    assert confirmation.status_code == 200
    receipt = confirmation.json()
    # Historic income precedes the newly confirmed policy window. Do not backdate
    # that authority or treat old cash as newly eligible income.
    historic = client.get(URL)
    assert historic.status_code == 200
    assert historic.json()["allocation"]["goals"][0]["amount_cents"] == 0
    with Session(engine) as session:
        cash = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        )
        assert cash is not None
        income_request = ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash.id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="full-joint-actual-payroll",
            idempotency_key="full-joint-actual-payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        )
    settled = ingest_external_fact(engine, DEMO_USER_ID, income_request, NOW)
    assert settled.bank_status == "SETTLED" and settled.projection_status == "PROJECTED"
    assert settled.transaction_id is not None and len(settled.economic_posting_ids) == 2
    with Session(engine) as session:
        income = read_income_state(session, DEMO_USER_ID, NOW)
        fragments = {str(row.fragment_id): row for row in income.ledger.fragments}
        available = sum(row.available_cents for row in fragments.values())
    before = physical_snapshot(engine)
    result = client.get(URL)
    assert result.status_code == 200
    value = result.json()
    assert value["state"] == "COMPUTED"
    assert value["source_issues"] == []
    assert value["independent_bank_projection_matched"] is True
    assert value["registered_goal_count"] == 1
    assert value["included_goal_ids"] == [goal_id] and value["uncovered_goal_ids"] == []
    assert receipt["evidence_id"] in value["source_evidence_ids"]
    assert value["funds_scope"] == "ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY"
    assert value["protection_scope"] == "ALL_ORIGINAL_365_DAY_RESERVES_RETAINED"
    assert value["grants_authority"] is False
    allocation = value["allocation"]
    assert allocation["status"] == "OPTIMAL"
    assert allocation["purpose"] == "CURRENT_PERIOD_PLANNING_ONLY"
    assert allocation["grants_authority"] is False
    assert len(allocation["objective_vector"]) == 8
    goal = allocation["goals"][0]
    assert goal["goal_id"] == goal_id
    assert goal["effective_policy_version_id"] == receipt["lifecycle"]["current_version_id"]
    assert 0 < goal["amount_cents"] <= 20000
    assert goal["completion_date"] is None and goal["delay_censored"] is True
    uses = allocation["income_uses"]
    assert uses and all(use["origin_transaction_id"] == str(settled.transaction_id) for use in uses)
    assert sum(use["amount_cents"] for use in uses) == goal["amount_cents"]
    assert goal["amount_cents"] <= allocation["budget_cents"] <= available
    for use in uses:
        fragment = fragments[use["fragment_id"]]
        assert use["origin_transaction_id"] == str(fragment.origin_transaction_id)
        assert fragment.account_id == income_request.account_id
        assert 0 < use["amount_cents"] <= fragment.available_cents
    assert client.get(URL).json() == value
    assert physical_snapshot(engine) == before
