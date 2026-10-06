"""One actual owned PG candidate; collection/static only until Root schedules it.

The original 47-minute UNKNOWN/replay node is retained independently. This case
stops before bank submission and measures exact monetary/ledger originals.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import ActionPlan, AuditEpoch, BankOperation, PolicyVersion
from app.domain.income_ledger import LEDGER_SOURCE
from app.services.audit_chain import verify_audit_chain
from app.services.boundary import CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE
from app.services.demo_seed import DEMO_USER_ID
from app.tests.test_asset_allocation_service import authorization
from app.tests.test_full_asset_allocation_api import actual_asset_declaration, actual_income
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def original_effects(engine: Engine) -> dict[str, Any]:
    """Whole actual money tables and exact income/goal evidence, not zero flags."""
    original = json.loads(physical_snapshot(engine))
    names = (
        "accounts",
        "transactions",
        "credit_card_bills",
        "asset_positions",
        "goals",
        "bank_operations",
        "action_receipts",
        "simulated_bank_postings",
        "simulated_bank_redemptions",
        "external_bank_facts",
        "action_resource_reservations",
    )
    assert all(name in original for name in names)
    effects = {name: original[name] for name in names}
    effects["income_and_goal_originals"] = [
        row
        for row in original["evidence_items"]
        if row["source_type"] in {LEDGER_SOURCE, OWNERSHIP_SOURCE, CONTRIBUTION_SOURCE}
    ]
    return effects


def test_actual_full_asset_revoke_invalidates_unsubmitted_originals_without_money_effects(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    assert isinstance(client.app, FastAPI)
    now = datetime.now(UTC)
    with Session(engine) as session:
        epochs = list(
            session.scalars(
                select(AuditEpoch).where(
                    AuditEpoch.user_id == DEMO_USER_ID, AuditEpoch.status == "OPEN"
                )
            )
        )
        assert len(epochs) == 1 and now >= epochs[0].opened_at
        epoch_id = str(epochs[0].id)
    client.app.dependency_overrides[get_now] = lambda: now
    actual_income(engine)
    full_id = actual_asset_declaration(client, engine)
    declared = client.post(
        "/api/v1/policy-declarations",
        json={
            "configuration": authorization(max_auto_managed_cents=2000000),
            "idempotency_key": "asset-revoke-original-MVP-permission",
            "expected_epoch_id": epoch_id,
            "source_proposal_id": None,
        },
    )
    assert declared.status_code == 200, declared.text
    candidate = declared.json()
    confirmed = client.post(
        f"/api/v1/policy-proposals/{candidate['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": candidate["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    mvp_id = confirmed.json()["policy_id"]
    with Session(engine) as session:
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == UUID(mvp_id))
            .order_by(PolicyVersion.version_number.desc())
        )
        assert version is not None
        mvp_version_id = str(version.id)
    full_response = client.get(f"/api/v1/full-policies/{full_id}")
    assert full_response.status_code == 200, full_response.text
    full_version_id = full_response.json()["current_version"]["version_id"]
    prepare = {
        "full_policy_id": full_id,
        "expected_full_policy_version_id": full_version_id,
        "mvp_asset_policy_id": mvp_id,
        "expected_mvp_policy_version_id": mvp_version_id,
        "goal_id": None,
        "expected_goal_policy_version_id": None,
        "expected_epoch_id": epoch_id,
        "idempotency_key": "actual-full-asset-revoke-original:1",
        "planning_mode": "PORTFOLIO",
    }
    before_prepare = original_effects(engine)
    assert before_prepare["bank_operations"] == []
    assert before_prepare["action_receipts"] == []
    prepared = client.post("/api/v1/full-asset-executions/prepare", json=prepare)
    assert prepared.status_code == 200, prepared.text
    original = prepared.json()["original_portfolio"]
    assert len(original["batches"]) >= 2
    assert original_effects(engine) == before_prepare
    assert prepared.json()["state"] == "PREPARED_UNRESERVED"
    assert prepared.json()["original_consent"] is None
    ids = {UUID(batch["action_id"]) for batch in original["batches"]}
    with Session(engine) as session:
        actions = list(session.scalars(select(ActionPlan).where(ActionPlan.id.in_(ids))))
        assert len(actions) == len(ids)
        assert all(action.status == "PLANNED" for action in actions)
        assert list(session.scalars(select(BankOperation))) == []
    revoke_body = {
        "expected_version_id": full_version_id,
        "reason": "实际用户撤销尚未提交银行的原整组资产权限",
        "idempotency_key": "actual-full-asset-revoke-command:1",
    }
    effects_before_revoke = original_effects(engine)
    revoked = client.post(f"/api/v1/full-policies/{full_id}/revoke", json=revoke_body)
    assert revoked.status_code == 200, revoked.text
    result = revoked.json()
    assert result["status"] == "REVOKED" and not result["bank_authority"]
    assert {UUID(value) for value in result["invalidated_action_ids"]} == ids
    assert result["inflight_action_ids"] == []
    assert original_effects(engine) == effects_before_revoke
    with Session(engine) as session:
        actions = list(session.scalars(select(ActionPlan).where(ActionPlan.id.in_(ids))))
        assert len(actions) == len(ids)
        assert all(action.status == "INVALIDATED" for action in actions)
        assert {action.idempotency_key for action in actions} == {
            batch["bank_idempotency_key"] for batch in original["batches"]
        }
        assert verify_audit_chain(session, DEMO_USER_ID, mode="EXACT").status == "VALID"
    after_revoke = physical_snapshot(engine)
    replay = client.post(f"/api/v1/full-policies/{full_id}/revoke", json=revoke_body)
    assert replay.status_code == 200 and replay.json() == result
    assert physical_snapshot(engine) == after_revoke
    base = f"/api/v1/full-asset-executions/portfolios/{original['portfolio_id']}"
    refused_confirm = client.post(
        base + "/confirm",
        json={
            "accepted": True,
            "reviewed_portfolio_hash": original["portfolio_hash"],
            "expected_epoch_id": epoch_id,
            "idempotency_key": "actual-full-asset-revoked-confirm:1",
        },
    )
    assert refused_confirm.status_code == 409, refused_confirm.text
    refused_execute = client.post(
        base + "/execute-next",
        json={
            "accepted": True,
            "reviewed_portfolio_hash": original["portfolio_hash"],
            "expected_epoch_id": epoch_id,
            "expected_batch_number": 1,
            "expected_action_id": original["batches"][0]["action_id"],
        },
    )
    assert refused_execute.status_code == 409, refused_execute.text
    assert physical_snapshot(engine) == after_revoke
    assert original_effects(engine) == effects_before_revoke
