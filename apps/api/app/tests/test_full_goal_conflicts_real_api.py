"""One actual PostgreSQL candidate; root alone runs this isolated financial node."""

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, PolicyVersion
from app.domain.external_bank_fact_types import ExternalFactRequest
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
CONFLICTS = "/api/v1/planning/full-goal-conflicts"
REPAIRS = "/api/v1/planning/full-goal-repairs/preview"


def test_actual_conflict_readonly_repair_new_version_and_source_tamper(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, original = confirmed_existing_goal(client, engine)
    config: dict[str, Any] = original["configuration"]
    config["monthly_contribution"] = {"min_cents": 0, "target_cents": 1000, "max_cents": 1000}
    config["minimum_guarantee_cents"] = 2000
    url = f"/api/v1/goals/{goal_id}/full-model"
    preview = client.post(
        url + "/preview",
        json={key: original[key] for key in ("expected_version_id", "configuration")},
    )
    assert preview.status_code == 200
    confirmation = client.post(
        url + "/confirm",
        json=original
        | {
            "accepted": True,
            "reviewed_full_hash": preview.json()["full_configuration_hash"],
            "reviewed_base_hash": preview.json()["base_configuration_hash"],
        },
    )
    assert confirmation.status_code == 200
    receipt = confirmation.json()
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
            external_ref="full-repair-actual-payroll",
            idempotency_key="full-repair-actual-payroll",
        )
    settled = ingest_external_fact(engine, DEMO_USER_ID, income, NOW)
    assert settled.bank_status == "SETTLED" and settled.projection_status == "PROJECTED"
    assert len(settled.economic_posting_ids) == 2 and settled.transaction_id is not None
    before = physical_snapshot(engine)
    read = client.get(CONFLICTS)
    assert read.status_code == 200
    actual = read.json()
    assert actual["state"] == "COMPUTED" and actual["registered_goal_count"] == 1
    assert actual["explanation"]["conflict"]["status"] == "MINIMAL_CONFLICT"
    assert len(actual["explanation"]["conflict"]["constraint_ids"]) == 2
    assert actual["current_permission_repair"]["status"] == "NO_PERMITTED_REPAIR"
    body = {
        "expected_epoch_id": actual["epoch_id"],
        "reviewed_state_hash": actual["review_state_hash"],
        "adjustments": [
            {
                "goal_id": goal_id,
                "expected_version_id": receipt["lifecycle"]["current_version_id"],
                "minimum_new_monthly_max_cents": 1001,
                "maximum_new_monthly_max_cents": 3000,
            }
        ],
    }
    for change in (
        {"reviewed_state_hash": "0" * 64},
        {"expected_epoch_id": str(UUID(int=999))},
        {"accepted": True},
    ):
        assert client.post(REPAIRS, json=body | change).status_code in {409, 422}
        assert physical_snapshot(engine) == before
    repaired = client.post(REPAIRS, json=body)
    assert repaired.status_code == 200
    result = repaired.json()
    assert result["state"] == "PROPOSAL" and result["writes_performed"] is False
    assert result["proposal"]["original_execution_caps_unchanged"] is True
    assert len(result["version_previews"]) == 1
    new = result["version_previews"][0]
    assert new["original_monthly_max_cents"] == 1000 and new["proposed_monthly_max_cents"] == 2000
    assert new["missing_explicit_user_fields"] == ["accepted", "reason", "idempotency_key"]
    assert physical_snapshot(engine) == before
    committed = client.post(
        new["confirmation_endpoint"],
        json=new["confirmation_bindings"]
        | {
            "accepted": True,
            "reason": "用户主动复核本次最小候选",
            "idempotency_key": "full-repair-new-confirmed-version",
        },
    )
    assert committed.status_code == 200
    after = physical_snapshot(engine)
    assert money_originals(after) == money_originals(before)
    assert (
        committed.json()["lifecycle"]["previous_version_id"]
        == receipt["lifecycle"]["current_version_id"]
    )
    current = client.get(CONFLICTS)
    assert (
        current.status_code == 200
        and current.json()["explanation"]["conflict"]["status"] == "NO_CONFLICT"
    )
    assert client.post(REPAIRS, json=body).status_code == 409
    assert physical_snapshot(engine) == after
    with Session(engine) as session:
        old = session.get(PolicyVersion, UUID(receipt["lifecycle"]["current_version_id"]))
        assert old is not None and old.configuration["monthly_contribution"]["max_cents"] == 1000
    with Session(engine) as session, session.begin():
        bank_cash = session.get(Account, income.account_id)
        assert bank_cash is not None
        bank_cash.balance_cents += 1
    tampered = physical_snapshot(engine)
    unknown = client.get(CONFLICTS)
    assert unknown.status_code == 200 and unknown.json()["state"] == "UNKNOWN"
    assert (
        unknown.json()["explanation"] is None
        and unknown.json()["current_permission_repair"] is None
    )
    changed = deepcopy(body)
    changed["reviewed_state_hash"] = current.json()["review_state_hash"]
    changed["adjustments"][0]["expected_version_id"] = committed.json()["lifecycle"][
        "current_version_id"
    ]
    no_fake_repair = client.post(REPAIRS, json=changed)
    assert no_fake_repair.status_code == 200 and no_fake_repair.json()["state"] == "UNKNOWN"
    assert (
        no_fake_repair.json()["proposal"] is None
        and no_fake_repair.json()["version_previews"] == []
    )
    assert physical_snapshot(engine) == tampered
