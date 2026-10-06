"""Root executes this single disposable PG risk; no formal financial claim."""

from datetime import timedelta
from typing import Any

import pytest
from app.db.models import Account
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.external_bank_facts import ingest_external_fact
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_protection_projection_api import confirm
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/planning/full-current-goal-allocation"
OLD = "/api/v1/planning/current-goal-allocation"


def test_actual_full_protection_is_consumed_by_original_goals_and_unknown_sources_stay_readonly(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, command = confirmed_existing_goal(client, engine)
    configuration: dict[str, Any] = command["configuration"]
    configuration["monthly_contribution"] = {
        "min_cents": 0,
        "target_cents": 20000,
        "max_cents": 20000,
    }
    configuration["minimum_guarantee_cents"] = 0
    model = f"/api/v1/goals/{goal_id}/full-model"
    review_response = client.post(
        model + "/preview",
        json={key: command[key] for key in ("expected_version_id", "configuration")},
    )
    assert review_response.status_code == 200, review_response.text
    review = review_response.json()
    accepted = client.post(
        model + "/confirm",
        json=command
        | {
            "accepted": True,
            "reviewed_full_hash": review["full_configuration_hash"],
            "reviewed_base_hash": review["base_configuration_hash"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    with Session(engine) as session:
        cash = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        assert cash is not None
        cash_id = cash.id
    deposited = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            external_ref="full-protection-joint-actual-payroll",
            idempotency_key="full-protection-joint-actual-payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert deposited.bank_status == "SETTLED" and deposited.projection_status == "PROJECTED"
    assert deposited.transaction_id is not None and len(deposited.economic_posting_ids) == 2
    initial_rows = physical_snapshot(engine)
    original_response = client.get(OLD)
    initial_response = client.get(URL)
    assert original_response.status_code == initial_response.status_code == 200
    original, initial = original_response.json(), initial_response.json()
    assert initial["state"] == "COMPUTED" and initial["binding"]["status"] == "VERIFIED"
    assert initial["binding"]["bound_point_count"] == 1098
    assert initial["original_joint"] == original
    assert initial["allocation"]["budget_cents"] == original["allocation"]["budget_cents"]
    assert initial["allocation"]["goals"][0]["amount_cents"] > 10000
    assert client.get(OLD).json() == original
    assert physical_snapshot(engine) == initial_rows
    # Register an explicit planning-only obligation large enough to constrain the
    # existing goal fixture. This is a functional input, not a product metric or
    # a retroactively selected experimental case/oracle.
    margin = initial["full_protection"]["projection"]["original_annual_projection"][
        "minimum_margin_cents"
    ]
    assert isinstance(margin, int) and margin > 10000
    amount = margin - 10000
    first = (NOW + timedelta(days=10)).date().isoformat()
    last = (NOW + timedelta(days=12)).date().isoformat()
    declared = confirm(
        client,
        "DatedExpensePolicy",
        {
            "type": "dated_expense",
            "name": "明确确认的联合规划保守支出",
            "window": {"start": first, "end": last},
            "amount": {"min_cents": amount, "target_cents": amount, "max_cents": amount},
        },
        "full-joint-dated-binding",
    )
    assert declared["bank_authority"] is False
    before = physical_snapshot(engine)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["state"] == "COMPUTED" and value["binding"]["status"] == "VERIFIED"
    assert value["original_joint"] == original and client.get(OLD).json() == original
    assert value["binding"]["original_input_hash"] == original["allocation"]["input_hash"]
    assert (
        value["binding"]["bound_point_count"]
        == value["binding"]["original_point_count"]
        == value["binding"]["full_point_count"]
        == 1098
    )
    assert value["allocation"]["status"] == "OPTIMAL"
    assert value["allocation"]["budget_cents"] == 10000
    assert value["allocation"]["goals"][0]["amount_cents"] == 10000
    assert value["allocation"]["goals"][0]["goal_id"] == goal_id
    assert len(value["allocation"]["objective_vector"]) == 8
    assert value["grants_authority"] is False and value["execution_support"] == "NOT_IMPLEMENTED"
    uses = value["allocation"]["income_uses"]
    assert uses and all(
        row["origin_transaction_id"] == str(deposited.transaction_id) for row in uses
    )
    assert sum(row["amount_cents"] for row in uses) == 10000
    original_points = initial["binding"]["candidate"]["hard_protection_points"]
    full_points = value["binding"]["candidate"]["hard_protection_points"]
    for old, new in zip(original_points, full_points, strict=True):
        assert new["date"] == old["date"] and new["cash_cents"] <= old["cash_cents"]
        for key in (
            "obligation_floor_cents",
            "living_floor_cents",
            "emergency_floor_cents",
            "owned_goal_cash_cents",
        ):
            assert new[key] == old[key]
        assert new["other_protection_floor_cents"] >= old["other_protection_floor_cents"]
    assert client.get(URL).json() == value
    for key in (
        "user_id",
        "cash_cents",
        "future_income_cents",
        "source_refs",
        "as_of",
        "authority",
        "release_goal_minimum",
    ):
        assert client.get(URL, params={key: "fake"}).status_code == 422
    assert physical_snapshot(engine) == before
    confirm(
        client,
        "PeriodicTransferPolicy",
        {
            "type": "periodic_transfer",
            "source_account_id": str(cash_id),
            "payee_id": "synthetic-landlord-001",
            "amount_rule": {"kind": "exact", "amount_cents": 1},
            "due_day": 15,
            "single_action_cap_cents": 1,
        },
        "full-joint-unproven-future-source-account",
    )
    periodic_rows = physical_snapshot(engine)
    periodic = client.get(URL)
    assert periodic.status_code == 200, periodic.text
    unknown = periodic.json()
    assert (
        unknown["state"] == "UNKNOWN"
        and "FULL_FUTURE_ACCOUNT_DEBITS_NOT_PROVEN" in unknown["reasons"]
    )
    assert unknown["original_joint"]["registered_goal_count"] == 1
    assert unknown["allocation"]["status"] == "UNKNOWN"
    assert [row["goal_id"] for row in unknown["allocation"]["goals"]] == [goal_id]
    assert all(row["amount_cents"] is None for row in unknown["allocation"]["goals"])
    assert client.get(OLD).json() == original
    assert physical_snapshot(engine) == periodic_rows
    with Session(engine) as session, session.begin():
        row = session.get(Account, cash_id)
        assert row is not None
        row.balance_cents += 1
    tampered = physical_snapshot(engine)
    failure = client.get(URL)
    assert failure.status_code == 200, failure.text
    rejected = failure.json()
    assert rejected["state"] == "UNKNOWN" and rejected["reasons"]
    assert rejected["original_joint"]["registered_goal_count"] == 1
    assert rejected["allocation"] is None or rejected["allocation"]["status"] == "UNKNOWN"
    assert physical_snapshot(engine) == tampered
