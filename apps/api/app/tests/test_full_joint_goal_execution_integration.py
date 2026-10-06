"""Root-only generated-PG candidate; collection proves no bank behaviour.

Requires installed 0015, original Joint typed guards, and the dedicated current
income-reservation proof bridge. Does not bypass any missing prerequisite.
"""

from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, ActionPlan, BankOperation
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_joint_goal_execution_dispatch import (
    fresh_read,
    require_original_joint_pipeline_hooks,
)
from app.services.full_policy_lifecycle import canonical_candidate
from app.services.income_ledger import read_income_state
from app.services.scenario_runner import _fault
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
URL = "/api/v1/joint-goal-actions"


def _actual_goal(
    client: TestClient, engine: Engine, epoch_id: UUID, number: int, account_id: UUID
) -> str:
    config = {
        "type": "goal_saving",
        "name": f"联合实际目标{number}",
        "target_cents": 300000,
        "deadline": "2027-10-01",
        "monthly_contribution": {"min_cents": 0, "target_cents": 10000, "max_cents": 20000},
    }
    declared = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": config,
            "expected_epoch_id": str(epoch_id),
            "idempotency_key": f"joint-actual-goal-{number}",
        },
    )
    assert declared.status_code == 200, declared.text
    original = declared.json()
    confirmed = client.post(
        f"/api/v1/policy-proposals/{original['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": original["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    version = confirmed.json()
    created = client.post(
        "/api/v1/goals",
        json={
            "policy_id": version["policy_id"],
            "expected_version_id": version["current_version_id"],
            "account_id": str(account_id),
        },
    )
    assert created.status_code == 200, created.text
    goal = created.json()["goal"]
    model = {
        "type": "long_term_goal",
        "name": goal["name"],
        "target_cents": goal["target_cents"],
        "deadline": goal["deadline"],
        "monthly_contribution": {"min_cents": 0, "target_cents": 10000, "max_cents": 20000},
        "importance": 87 - number,
        "minimum_guarantee_cents": 0,
        "allow_partial": True,
        "allow_deferral": True,
        "deferral_cost_cents_per_day": 19,
    }
    path = f"/api/v1/goals/{goal['id']}/full-model"
    review = client.post(
        path + "/preview",
        json={"expected_version_id": goal["policy_version_id"], "configuration": model},
    )
    assert review.status_code == 200, review.text
    value = review.json()
    approved = client.post(
        path + "/confirm",
        json={
            "expected_version_id": goal["policy_version_id"],
            "expected_epoch_id": str(epoch_id),
            "configuration": model,
            "reviewed_full_hash": value["full_configuration_hash"],
            "reviewed_base_hash": value["base_configuration_hash"],
            "accepted": True,
            "reason": "隔离PG明确采纳完整模型，非真人效果研究",
            "idempotency_key": f"joint-actual-full-model-{number}",
        },
    )
    assert approved.status_code == 200, approved.text
    return str(goal["id"])


def test_actual_joint_different_amount_signed_whole_consent_fixed_key_unknown_no_advance(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = goal_client
    require_original_joint_pipeline_hooks()
    with Session(engine) as read:
        epoch = current_audit_epoch(read, DEMO_USER_ID)
        goal_account = read.scalar(select(Account).where(Account.account_type == "GOAL"))
        cash = read.scalar(
            select(Account).where(Account.account_type == "CASH").order_by(Account.id)
        )
        assert epoch is not None and goal_account is not None and cash is not None
        epoch_id, account_id, cash_id = epoch.id, goal_account.id, cash.id
    goals = [_actual_goal(client, engine, epoch_id, number, account_id) for number in (1, 2)]
    income = ingest_external_fact(
        engine,
        DEMO_USER_ID,
        ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=cash_id,
            kind="INCOME",
            amount_cents=600000,
            occurred_at=NOW,
            external_ref="joint-actual-new-income",
            idempotency_key="joint-actual-new-income",
            counterparty_ref="payroll",
        ),
        NOW,
    )
    assert income.bank_status == "SETTLED" and income.projection_status == "PROJECTED"
    config: dict[str, Any] = {
        "type": "goal_allocation",
        "goal_ids": goals,
        "max_single_allocation_cents": 30000,
    }
    scope = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "GoalAllocationPolicy",
            "configuration": config,
            "reviewed_hash": configuration_hash(
                canonical_candidate("GoalAllocationPolicy", config)
            ),
            "accepted": True,
            "reason": "完整两目标联合上限三万元分的明确规划范围",
            "idempotency_key": "joint-actual-scope",
        },
    )
    assert scope.status_code == 200, scope.text
    original = scope.json()
    body = {
        "full_policy_id": original["policy_id"],
        "expected_full_policy_version_id": original["version_id"],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "joint-actual-parent",
    }
    before = physical_snapshot(engine)
    preview = client.post(URL + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    value = preview.json()
    assert value["status"] == "READY_TO_REVIEW", preview.text
    assert value["plan"]["total_allocation_cents"] == 30000
    assert sorted(c["command"]["effect"]["amount_cents"] for c in value["plan"]["children"]) == [
        10000,
        20000,
    ]
    assert len(value["plan"]["allocation_input"]["hard_protection_points"]) == 1098
    assert physical_snapshot(engine) == before
    assert client.post(URL + "/prepare", json=body).status_code == 401
    assert physical_snapshot(engine) == before
    secret = "SYNTHETIC_LOCAL_JOINT_ISOLATED_RISK_ONLY"
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", secret)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "cb" * 32)
    signed = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": secret}
    )
    assert signed.status_code == 200, signed.text
    prepared = client.post(URL + "/prepare", json=body)
    assert prepared.status_code == 200, prepared.text
    current = prepared.json()
    plan = current["original_plan"]
    plan_id = plan["plan_id"]
    assert current["state"] == "PREPARED_UNRESERVED" and len(current["children"]) == 2
    assert all(c["original_action"]["autonomy_level"] == "ASK_ONCE" for c in current["children"])
    prepared_rows = physical_snapshot(engine)
    assert client.get(URL + "/by-key/" + body["idempotency_key"]).json()["original_request"] == body
    assert client.post(URL + "/prepare", json=body).json() == current
    assert physical_snapshot(engine) == prepared_rows
    consent_body = {
        "accepted": True,
        "reviewed_plan_hash": plan["plan_hash"],
        "expected_epoch_id": str(epoch_id),
        "idempotency_key": "joint-actual-whole-consent",
    }
    consent = client.post(f"{URL}/{plan_id}/confirm", json=consent_body)
    assert consent.status_code == 200, consent.text
    original_consent = consent.json()["original_consent"]
    actor = original_consent["original_evidence"]["content"]["actor"]
    assert actor["role"] == "USER" and actor["authentication_source"] == "LOCAL_SIGNED_SESSION"
    assert actor["human_identity_verified"] is False
    execute_bodies = [
        {
            "accepted": True,
            "reviewed_plan_hash": plan["plan_hash"],
            "expected_epoch_id": str(epoch_id),
            "expected_child_number": c["child_number"],
            "expected_action_id": c["action_id"],
        }
        for c in plan["children"]
    ]
    held = physical_snapshot(engine)
    assert client.post(f"{URL}/{plan_id}/execute-child", json=execute_bodies[1]).status_code == 409
    assert physical_snapshot(engine) == held
    with (
        _fault("DROP_BANK_RESPONSE", "EXECUTE_ACTION", engine, DEMO_USER_ID),
        pytest.raises(TimeoutError),
    ):
        client.post(f"{URL}/{plan_id}/execute-child", json=execute_bodies[0])
    unknown = client.get(f"{URL}/{plan_id}")
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["state"] == "UNRESOLVED"
    assert unknown.json()["children"][0]["state"] == "UNKNOWN"
    held = physical_snapshot(engine)
    assert client.post(f"{URL}/{plan_id}/execute-child", json=execute_bodies[1]).status_code == 409
    assert physical_snapshot(engine) == held
    recovered = client.post(f"{URL}/{plan_id}/execute-child", json=execute_bodies[0])
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["children"][0]["state"] == "ORIGINAL_RECEIPT_VERIFIED"
    held = physical_snapshot(engine)
    assert (
        client.post(f"{URL}/{plan_id}/execute-child", json=execute_bodies[0]).json()
        == recovered.json()
    )
    assert physical_snapshot(engine) == held
    # This is the real economic-ledger successor boundary: the second fixed
    # child must use the dedicated proof bridge, never rewrite its old hash.
    completed = client.post(f"{URL}/{plan_id}/execute-child", json=execute_bodies[1])
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "ORIGINAL_SERVICE_RECEIPTS_VERIFIED"
    with fresh_read(engine) as read:
        actions = list(
            read.scalars(
                select(ActionPlan).where(
                    ActionPlan.id.in_([UUID(c["action_id"]) for c in plan["children"]])
                )
            )
        )
        operations = list(
            read.scalars(
                select(BankOperation).where(
                    BankOperation.action_plan_id.in_([a.id for a in actions])
                )
            )
        )
        assert (
            len(actions) == len(operations) == 2
            and len({b.idempotency_key for b in operations}) == 2
        )
        for child in plan["children"]:
            action = next(a for a in actions if str(a.id) == child["action_id"])
            bank = next(b for b in operations if b.action_plan_id == action.id)
            assert action.request["execution"] == child["command"] == bank.request
            assert action.request_hash == configuration_hash(action.request)
            assert (
                bank.status == "SETTLED" and bank.idempotency_key == child["bank_idempotency_key"]
            )
        ledger = read_income_state(read, DEMO_USER_ID, NOW).ledger
        origins = [f for f in ledger.fragments if f.origin_transaction_id == income.transaction_id]
        assert sum(f.assigned_cents for f in origins) == 30000
        assert sum(f.available_cents for f in origins) == 570000
        assert verify_audit_chain(read, DEMO_USER_ID).status == "VALID"
    held = physical_snapshot(engine)
    assert client.get(f"{URL}/{plan_id}").json() == completed.json()
    assert physical_snapshot(engine) == held
