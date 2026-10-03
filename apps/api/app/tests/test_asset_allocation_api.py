"""Read-only single-product previews through real PostgreSQL and the public HTTP contract."""

from typing import Any
from uuid import UUID, uuid4

import pytest
from app.db.models import Account, AssetPosition, EvidenceItem, Goal, Policy, PolicyProposal, User
from app.domain.asset_exposure import EXPOSURE_SOURCE, asset_exposure_snapshot
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.tests.test_goal_api import NOW, all_tables, confirmed_goal_request
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

AUTHORIZATION: dict[str, Any] = {
    "type": "asset_authorization",
    "name": "模拟闲钱配置",
    "scope": "general_idle_funds",
    "allowed_asset_classes": ["CASH", "CASH_MGMT_T0", "CASH_MGMT_T1", "FIXED_DEPOSIT"],
    "max_auto_managed_cents": 2000000,
    "single_action_cap_cents": 1000000,
    "max_redemption_delay_days": 1,
    "max_lock_days": 30,
    "max_principal_risk_level": 0,
    "allow_auto_recovery_without_penalty": True,
    "allow_early_withdrawal_with_penalty": False,
}


def confirm_asset_policy(
    client: TestClient, engine: Engine, configuration: dict[str, Any] | None = None
) -> dict[str, Any]:
    config = validate_configuration(configuration or AUTHORIZATION)
    proposal_id = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            PolicyProposal(
                id=proposal_id,
                user_id=DEMO_USER_ID,
                source_type="USER_DECLARED",
                source_text="Explicit synthetic authorization fixture",
                compiler_version="asset-http-fixture-v1",
                proposed_configuration=config,
                evidence_ids=[],
                idempotency_key=f"asset-proposal:{proposal_id}",
            )
        )
    response = client.post(
        f"/api/v1/policy-proposals/{proposal_id}/confirm",
        json={"accepted": True, "reviewed_hash": configuration_hash(config)},
    )
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    return result


def test_http_preview_selects_one_authorized_product_without_writing_or_claiming_execution(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    policy = confirm_asset_policy(client, engine)
    path = f"/api/v1/asset-policies/{policy['policy_id']}/allocation-preview"
    before = all_tables(engine)
    response = client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["simulation"] is True and body["user_id"] == str(DEMO_USER_ID)
    allocation = body["allocation"]
    assert allocation["status"] == "READY"
    assert allocation["financial_only"] is True and allocation["preview_only"] is True
    assert allocation["selected_asset_class"] == "CASH_MGMT_T1"
    assert allocation["suggested_cents"] == 1000000
    assert allocation["net_simulated_yield_cents"] == 4389
    assert allocation["remaining_managed_cents"] == 2000000
    assert allocation["baseline_boundary"]["safe_idle_cents"] == 3157400
    assert allocation["candidate_boundary"]["status"] == "READY"
    assert sum(item["amount_cents"] for item in allocation["source_cash_uses"]) == 1000000
    assert client.get(path).json() == body
    assert all_tables(engine) == before


def test_http_preview_rejects_client_financial_overrides_and_other_user_policy(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    policy = confirm_asset_policy(client, engine)
    path = f"/api/v1/asset-policies/{policy['policy_id']}/allocation-preview"
    other_id, foreign_policy_id = uuid4(), uuid4()
    with Session(engine) as session, session.begin():
        session.add(User(id=other_id, external_ref=f"other-asset:{other_id}", display_name="Other"))
        session.flush()
        session.add(
            Policy(
                id=foreign_policy_id,
                user_id=other_id,
                name="Other asset policy",
                policy_type="asset_authorization",
            )
        )
    before = all_tables(engine)
    for query in (
        "as_of=2027-01-01T00:00:00Z",
        "amount_cents=100000000",
        "managed_principal_cents=0",
        "scope=goal",
        f"goal_id={uuid4()}",
    ):
        assert client.get(f"{path}?{query}").status_code == 422
    assert (
        client.get(f"/api/v1/asset-policies/{foreign_policy_id}/allocation-preview").status_code
        == 404
    )
    assert client.get(f"/api/v1/asset-policies/{uuid4()}/allocation-preview").status_code == 404
    assert all_tables(engine) == before


def test_http_missing_exposure_cannot_be_treated_as_zero_managed_funds(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    policy = confirm_asset_policy(client, engine)
    with Session(engine) as session, session.begin():
        proof = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE")
        )
        assert proof is not None
        session.delete(proof)
    before = all_tables(engine)
    response = client.get(f"/api/v1/asset-policies/{policy['policy_id']}/allocation-preview")
    assert response.status_code == 200
    allocation = response.json()["allocation"]
    assert allocation["status"] == "INSUFFICIENT_EVIDENCE"
    assert allocation["suggested_cents"] is None
    assert allocation["remaining_managed_cents"] is None
    assert response.json()["source_issues"]
    assert all_tables(engine) == before


def test_http_goal_asset_placement_preserves_total_ownership_and_monthly_contribution(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_request = confirmed_goal_request(client, engine)
    created = client.post("/api/v1/goals", json=goal_request)
    assert created.status_code == 200
    goal_id = UUID(created.json()["goal"]["id"])
    asset = confirm_asset_policy(
        client, engine, {**AUTHORIZATION, "scope": "goal", "goal_id": str(goal_id)}
    )
    current = client.get(f"/api/v1/policies/{goal_request['policy_id']}/versions").json()["items"][
        -1
    ]
    goal_config = {**current["configuration"], "asset_policy_id": asset["policy_id"]}
    changed = client.patch(
        f"/api/v1/policies/{goal_request['policy_id']}",
        json={
            "expected_version_id": current["id"],
            "configuration": goal_config,
            "reviewed_hash": configuration_hash(validate_configuration(goal_config)),
            "accepted": True,
            "reason": "Explicitly bind this goal to its own asset authorization",
            "idempotency_key": f"bind-assets:{goal_id}",
        },
    )
    assert changed.status_code == 200
    with Session(engine) as session, session.begin():
        goal = session.get(Goal, goal_id)
        assert goal is not None
        goal.allocated_cents = 150000
        ownership = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_GOAL_OWNERSHIP",
                EvidenceItem.content["goal_id"].as_string() == str(goal_id),
            )
        )
        assert ownership is not None
        ownership.content = {
            **ownership.content,
            "allocated_cents": 150000,
            "cash_owned_cents": 150000,
        }
        ownership.content_hash = configuration_hash(ownership.content)
        session.flush()
        exposure = session.scalar(
            select(EvidenceItem).where(EvidenceItem.source_type == EXPOSURE_SOURCE)
        )
        assert exposure is not None
        exposure.content = asset_exposure_snapshot(
            DEMO_USER_ID,
            NOW,
            accounts=session.scalars(select(Account)),
            positions=session.scalars(select(AssetPosition)),
            actions=[],
            receipts=[],
            evidence=session.scalars(select(EvidenceItem)),
        )
        exposure.content_hash = configuration_hash(exposure.content)
        exposure.observed_at = exposure.valid_from = NOW
    before = all_tables(engine)
    response = client.get(f"/api/v1/asset-policies/{asset['policy_id']}/allocation-preview")
    assert response.status_code == 200
    result = response.json()["allocation"]
    assert result["status"] == "READY"
    assert result["scope"] == "goal" and result["goal_id"] == str(goal_id)
    assert result["suggested_cents"] == 150000
    assert result["selected_asset_class"] == "CASH_MGMT_T1"
    assert result["net_simulated_yield_cents"] == 658
    before_point = result["baseline_boundary"]["calculation_trace"][0]
    after_point = result["candidate_boundary"]["calculation_trace"][0]
    assert after_point["cash_cents"] == before_point["cash_cents"] - 150000
    assert after_point["protected_cents_by_reason"]["goal_cash"] == 10000
    assert after_point["margin_cents"] == before_point["margin_cents"]
    assert all_tables(engine) == before
