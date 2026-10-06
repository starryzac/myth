"""Root-only disposable PG candidate; collection never proves financial behaviour."""

from uuid import UUID

import pytest
from app.db.models import Account
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.services.audit_chain import verify_audit_chain
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_action_set_boundary_actual import capture_actual_action_set
from app.services.full_action_set_joint_producers import capture_current_joint_producers
from app.services.full_goal_release_authorization import _reader
from app.tests.test_full_goals_api import confirmed_existing_goal
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_complete_joint_original_goal_income_1098_and_exact_effect_zero_writes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, command = confirmed_existing_goal(client, engine)
    # Register this current functional fixture through the actual double-hash
    # consent service. The equal target/max makes the two original planners'
    # economic output identical; no old case, permission or seed hash is edited.
    config = command["configuration"]
    config["monthly_contribution"] = {
        "min_cents": 0,
        "target_cents": 20000,
        "max_cents": 20000,
    }
    config["minimum_guarantee_cents"] = 0
    url = f"/api/v1/goals/{goal_id}/full-model"
    preview = client.post(
        url + "/preview",
        json={key: command[key] for key in ("expected_version_id", "configuration")},
    )
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()
    accepted = client.post(
        url + "/confirm",
        json=command
        | {
            "accepted": True,
            "reviewed_full_hash": reviewed["full_configuration_hash"],
            "reviewed_base_hash": reviewed["base_configuration_hash"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    with Session(engine) as session:
        cash = session.scalar(
            select(Account)
            .where(
                Account.user_id == DEMO_USER_ID,
                Account.account_type == "CASH",
            )
            .order_by(Account.id)
        )
        assert cash is not None
        cash_id = cash.id
    bank = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            occurred_at=NOW,
            external_ref="joint-producer-current-payroll",
            idempotency_key="joint-producer-current-payroll",
            counterparty_ref="payroll",
        ),
        NOW,
    )
    assert bank.bank_status == "SETTLED" and bank.projection_status == "PROJECTED"
    assert bank.transaction_id is not None
    before = physical_snapshot(engine)
    with _reader(engine) as session:
        actual = capture_actual_action_set(session, DEMO_USER_ID, NOW)
        original_bytes = actual.inputs.model_dump_json()
        captured = capture_current_joint_producers(
            session,
            DEMO_USER_ID,
            NOW,
            original_actual_capture=actual,
        )
        result = captured.result
        assert result.joint_family_complete, result.model_dump(mode="json")
        assert result.expected_goal_ids == [UUID(goal_id)]
        assert result.original_action_ids == result.unresolved_original_action_ids == []
        assert len(result.results) == 1
        view = result.results[0]
        assert view.view.state == "INCLUDED" and view.view.amount_cents == 20000
        assert view.shadow_original_candidate_key == "goal:" + goal_id
        assert view.view.signature is not None
        assert result.bank_authority is result.financial_write is False
        assert actual.inputs.model_dump_json() == original_bytes
        assert captured.inputs.actual_planning is not None
        assert captured.inputs.actual_planning.binding is not None
        assert captured.inputs.actual_planning.binding.bound_point_count == 1098
        assert captured.inputs.actual_planning.allocation is not None
        assert (
            sum(row.amount_cents for row in captured.inputs.actual_planning.allocation.income_uses)
            == 20000
        )
        assert all(
            row.origin_transaction_id == bank.transaction_id
            for row in captured.inputs.actual_planning.allocation.income_uses
        )
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    assert physical_snapshot(engine) == before
