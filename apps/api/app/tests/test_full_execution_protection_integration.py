"""Real isolated FULL veto at application and independent bank commit boundaries."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import app.services.execution as execution
import pytest
from app.db.models import Account, ActionPlan, BankOperation
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.execution_bank import BankOperationResult, process_operation
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


def test_actual_full_floor_rejects_original_prepare_and_changes_before_bank_acceptance(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
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
    preview = client.post(
        model + "/preview",
        json={key: command[key] for key in ("expected_version_id", "configuration")},
    )
    assert preview.status_code == 200, preview.text
    review = preview.json()
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
            select(Account.id)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        assert cash is not None
    deposited = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash,
            kind="INCOME",
            amount_cents=600000,
            external_ref="full-guard-real-payroll",
            idempotency_key="full-guard-real-payroll",
            counterparty_ref="payroll",
            occurred_at=NOW,
        ),
        NOW,
    )
    assert deposited.bank_status == "SETTLED" and len(deposited.economic_posting_ids) == 2
    first = (NOW + timedelta(days=10)).date().isoformat()
    last = (NOW + timedelta(days=12)).date().isoformat()
    small = {
        "type": "dated_expense",
        "window": {"start": first, "end": last},
        "amount": {"min_cents": 1, "target_cents": 1, "max_cents": 1},
    }
    confirm(client, "DatedExpensePolicy", small, "full-guard-small-original")
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "full-guard-before-change",
            "intent": {"kind": "allocate_goal", "goal_id": goal_id},
        },
    )
    assert prepared.status_code == 200, prepared.text
    original = prepared.json()
    assert original["effect"]["amount_cents"] == 20000
    action_id = UUID(original["action_id"])
    if original["autonomy_level"] == "ASK_ONCE":
        accepted_action = client.post(
            f"/api/v1/actions/{action_id}/confirm",
            json={"effect_hash": original["effect_hash"], "accepted": True},
        )
        assert accepted_action.status_code == 200, accepted_action.text
    baseline = client.get("/api/v1/planning/full-annual")
    assert baseline.status_code == 200, baseline.text
    margin = baseline.json()["projection"]["full_annual_projection"]["minimum_margin_cents"]
    assert isinstance(margin, int) and margin > 10000
    amount = margin - 10000
    real_bank = process_operation
    declared: list[dict[str, Any]] = []

    def changed_before_independent_bank(
        bank_engine: Engine, user: UUID, action: UUID, now: datetime
    ) -> BankOperationResult:
        # Original application reservations already committed. The real policy
        # route acquires the original user lock before independent bank acceptance.
        declared.append(
            confirm(
                client,
                "DatedExpensePolicy",
                {
                    **small,
                    "amount": {"min_cents": amount, "target_cents": amount, "max_cents": amount},
                },
                "full-guard-new-at-bank-boundary",
            )
        )
        return real_bank(bank_engine, user, action, now)

    before_bank_rows = None
    with Session(engine) as session:
        before_bank_rows = list(session.scalars(select(BankOperation.id)))
    monkeypatch.setattr(execution, "process_operation", changed_before_independent_bank)
    denied = client.post(f"/api/v1/actions/{action_id}/execute", json={})
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "FULL_EXECUTION_PROTECTION_BLOCKED"
    assert len(declared) == 1 and declared[0]["bank_authority"] is False
    monkeypatch.setattr(execution, "process_operation", real_bank)
    with Session(engine) as session:
        assert list(session.scalars(select(BankOperation.id))) == before_bank_rows
        action = session.get(ActionPlan, action_id)
        assert (
            action is not None
            and action.request["execution"]["effect_hash"] == original["effect_hash"]
        )
    before = physical_snapshot(engine)
    fresh = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "full-guard-new-refused",
            "intent": {"kind": "allocate_goal", "goal_id": goal_id},
        },
    )
    assert fresh.status_code == 409, fresh.text
    assert fresh.json()["error"]["code"] == "FULL_EXECUTION_PROTECTION_BLOCKED"
    assert physical_snapshot(engine) == before
