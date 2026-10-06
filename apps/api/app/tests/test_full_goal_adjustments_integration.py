"""Root-only isolated PG candidate. No run has been claimed by this module."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, PolicyVersion
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.audit_chain import verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.external_bank_facts import ingest_external_fact
from app.tests.test_full_goals_api import confirmed_existing_goal, money_originals
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/planning/full-goal-adjustments"


def financial_originals(snapshot: str) -> dict[str, Any]:
    rows: dict[str, Any] = json.loads(snapshot)
    return {
        **money_originals(snapshot),
        "action_resource_reservations": rows["action_resource_reservations"],
        "original_bank_and_income_evidence": [
            row
            for row in rows["evidence_items"]
            if row["evidence_level"] in {"BANK_CONFIRMED", "BANK_OBSERVED"}
            or row["source_type"] == "SIMULATED_NEW_FUNDS_LEDGER"
        ],
        "goal_ownership": [
            {key: row[key] for key in ("id", "user_id", "account_id", "allocated_cents")}
            for row in rows["goals"]
        ],
    }


def test_actual_selected_deadline_and_soft_min_preview_new_version_preserves_money(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, body = confirmed_existing_goal(client, engine)
    config: dict[str, Any] = body["configuration"]
    config.update(
        {
            "deadline": NOW.date().isoformat(),
            "monthly_contribution": {"min_cents": 200, "target_cents": 500, "max_cents": 1000},
            "minimum_guarantee_cents": 0,
            "allow_partial": False,
            "allow_deferral": False,
            "deferral_cost_cents_per_day": 0,
        }
    )
    model_url = f"/api/v1/goals/{goal_id}/full-model"
    initial = client.post(
        model_url + "/preview",
        json={key: body[key] for key in ("expected_version_id", "configuration")},
    )
    assert initial.status_code == 200, initial.text
    confirmed = client.post(
        model_url + "/confirm",
        json=body
        | {
            "accepted": True,
            "reviewed_full_hash": initial.json()["full_configuration_hash"],
            "reviewed_base_hash": initial.json()["base_configuration_hash"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    version = confirmed.json()["lifecycle"]["current_version_id"]
    with Session(engine) as session:
        account = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        )
        assert account is not None
        income = ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=account.id,
            kind="INCOME",
            amount_cents=600000,
            occurred_at=NOW,
            counterparty_ref="payroll",
            external_ref="full-306-adjustment-payroll",
            idempotency_key="full-306-adjustment-payroll",
        )
    settled = ingest_external_fact(engine, DEMO_USER_ID, income, NOW)
    assert settled.bank_status == "SETTLED" and settled.projection_status == "PROJECTED"
    before = physical_snapshot(engine)
    read = client.get(URL)
    assert read.status_code == 200, read.text
    current = read.json()
    assert current["state"] == "COMPUTED", current
    request = {
        "expected_epoch_id": current["original_conflicts"]["epoch_id"],
        "reviewed_state_hash": current["original_conflicts"]["review_state_hash"],
        "adjustments": [
            {
                "field": "deadline",
                "goal_id": goal_id,
                "expected_version_id": version,
                "lower_date": NOW.date().isoformat(),
                "upper_date": (NOW.date() + timedelta(days=3)).isoformat(),
            }
        ],
    }
    extras: list[dict[str, Any]] = [
        {"bank_facts": {}},
        {"now": NOW.isoformat()},
        {"accepted": True},
        {"reviewed_state_hash": "0" * 64},
    ]
    for extra in extras:
        rejected = client.post(URL + "/preview", json=request | extra)
        assert rejected.status_code in {409, 422}
        assert physical_snapshot(engine) == before
    response = client.post(URL + "/preview", json=request)
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["state"] == "PROPOSAL", proposal
    assert proposal["proposal"]["hard_repair"]["changed_policy_count"] == 1
    assert proposal["proposal"]["original_allow_deferral_unchanged"] is True
    preview = proposal["version_previews"][0]
    assert preview["proposed_value"] == (NOW.date() + timedelta(days=1)).isoformat()
    assert preview["actual_existing_preview"]["full_configuration"]["allow_deferral"] is False
    assert preview["ready_to_submit_confirmation"] is False
    assert physical_snapshot(engine) == before
    adopted = client.post(
        preview["confirmation_endpoint"],
        json=preview["confirmation_bindings"]
        | {
            "accepted": True,
            "reason": "隔离模拟用户明确选择新期限并复核双hash",
            "idempotency_key": "full-306-exact-deadline-new-version",
        },
    )
    assert adopted.status_code == 200, adopted.text
    after = physical_snapshot(engine)
    assert financial_originals(after) == financial_originals(before)
    assert adopted.json()["lifecycle"]["previous_version_id"] == version
    assert client.post(URL + "/preview", json=request).status_code == 409
    reread = client.get(URL).json()
    assert reread["state"] == "COMPUTED"
    soft_body = {
        "expected_epoch_id": reread["original_conflicts"]["epoch_id"],
        "reviewed_state_hash": reread["original_conflicts"]["review_state_hash"],
        "adjustments": [
            {
                "field": "monthly_min_cents",
                "goal_id": goal_id,
                "expected_version_id": adopted.json()["lifecycle"]["current_version_id"],
                "lower_cents": 0,
                "upper_cents": 100,
            }
        ],
    }
    soft = client.post(URL + "/preview", json=soft_body)
    assert soft.status_code == 200 and soft.json()["state"] == "SOFT_PREFERENCE_PREVIEW", soft.text
    assert soft.json()["proposal"]["hard_repair"]["changed_policy_count"] == 0
    assert soft.json()["version_previews"][0]["scope"] == "SOFT_PREFERENCE_ONLY"
    assert physical_snapshot(engine) == after
    with Session(engine) as session:
        old = session.get(PolicyVersion, UUID(version))
        assert old is not None and old.configuration["deadline"] == NOW.date().isoformat()
        audit = verify_audit_chain(session, DEMO_USER_ID)
        assert audit.status == "VALID" and not audit.errors
