"""Owned real-PG risk candidates; root executes them, never formal acceptance."""

from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, AssetProduct
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.policy_configuration import configuration_hash
from app.services import full_policy_lifecycle as full
from app.services.demo_seed import DEMO_USER_ID
from app.services.external_bank_facts import ingest_external_fact
from app.tests.test_full_dynamic_goal_reserve_api import full_dynamic_goal
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW as GOAL_NOW
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def actual_asset_declaration(
    client: TestClient, engine: Engine, *, goal_id: str | None = None, now: datetime = NOW
) -> str:
    config: dict[str, Any] = {
        "type": "asset_authorization",
        "scope": "goal" if goal_id else "general_idle_funds",
        "allowed_asset_classes": [
            "CASH",
            "CASH_MGMT_T0",
            "CASH_MGMT_T1",
            "FIXED_DEPOSIT_7D",
            "FIXED_DEPOSIT_30D",
            "FIXED_DEPOSIT_90D",
            "LOW_RISK_TERM",
        ],
        "max_auto_managed_cents": 2000000,
        "single_action_cap_cents": 300000,
        "max_redemption_delay_days": 1,
        "max_lock_days": 90,
        "allow_auto_recovery_without_penalty": True,
    }
    if goal_id:
        config["goal_id"] = goal_id
    canonical = full.canonical_candidate("AssetAuthorizationPolicy", config)
    request = full.FullCreateRequest(
        template_name="AssetAuthorizationPolicy",
        configuration=config,
        reviewed_hash=configuration_hash(canonical),
        accepted=True,
        reason="明确复核的实际隔离资产规划确认，不授予银行执行权限",
        idempotency_key="full-asset-risk",
    )
    registered = client.post("/api/v1/catalog/products/register-current", json={})
    assert registered.status_code == 200 and registered.json()["registered_ids"]
    with Session(engine) as session, session.begin():
        outcome = full.confirm_full_policy(session, DEMO_USER_ID, request, now)
        assert not outcome.bank_authority
        return str(outcome.policy_id)


def actual_income(engine: Engine, now: datetime = NOW) -> None:
    with Session(engine) as session:
        account = session.scalar(
            select(Account).where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
        )
        assert account is not None
        request = ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=account.id,
            kind="INCOME",
            amount_cents=5000000,
            external_ref="full-assets-actual-payroll",
            idempotency_key="full-assets-actual-payroll",
            counterparty_ref="payroll",
            occurred_at=now,
        )
    fact = ingest_external_fact(engine, DEMO_USER_ID, request, now)
    assert fact.projection_status == "PROJECTED" and fact.bank_status == "SETTLED"
    assert len(fact.economic_posting_ids) == 2


def test_actual_registered_portfolio_is_readonly_and_drift_never_returns_money(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    actual_income(engine)
    policy_id = actual_asset_declaration(client, engine)
    url = f"/api/v1/full-policies/{policy_id}/asset-allocation"
    before = physical_snapshot(engine)
    response = client.get(url)
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "COMPUTED" and payload["catalogue"]["status"] == "VERIFIED"
    plan = payload["allocation"]
    assert plan["status"] == "OPTIMAL" and plan["purchase_count"] >= 2
    assert plan["planning_only"] and not payload["bank_authority"]
    assert payload["execution_support"] == "NOT_IMPLEMENTED"
    assert "LOW_RISK_TERM" in payload["unavailable_asset_classes"]
    assert all(not candidate["bank_auto_eligible"] for candidate in plan["candidates"])
    bindings = {row["product_id"]: row for row in payload["catalogue"]["bindings"]}
    assert all(
        batch["terms_digest"] == bindings[batch["product_id"]]["terms_digest"]
        and batch["amount_cents"] <= 300000
        for batch in plan["batches"]
    )
    tightened = client.get(
        url, params={"planning_max_components": "1", "planning_max_turnover_cents": "20000"}
    )
    assert tightened.status_code == 200
    narrowed = tightened.json()["allocation"]
    assert narrowed["purchase_count"] <= 1 and narrowed["total_purchase_cents"] <= 20000
    assert client.get(url).json()["input_hash"] == payload["input_hash"]
    assert physical_snapshot(engine) == before
    for field in ("user_id", "balance_cents", "now", "bank_authority", "terms_digest"):
        assert client.get(url, params={field: "1"}).status_code == 422
    with Session(engine) as session, session.begin():
        product = session.scalar(
            select(AssetProduct).where(AssetProduct.id == UUID(plan["batches"][0]["product_id"]))
        )
        assert product is not None
        product.annual_yield_bps += 1
    tampered = physical_snapshot(engine)
    rejected = client.get(url)
    assert rejected.status_code == 200
    value = rejected.json()
    assert value["state"] == "UNKNOWN" and value["allocation"] is None
    assert value["catalogue"]["status"] == "UNKNOWN"
    assert not value["catalogue"]["products"] and not value["catalogue"]["bindings"]
    assert value["source_issues"] and physical_snapshot(engine) == tampered


def test_actual_goal_owned_cash_and_deadline_match_single_available_catalogue_maturity(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, _ = full_dynamic_goal(client, engine)
    actual_income(engine, GOAL_NOW)
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "intent": {"kind": "allocate_goal", "goal_id": goal_id},
            "idempotency_key": "full-assets-actual-goal-owned",
        },
    )
    assert prepared.status_code == 200
    action = prepared.json()
    if action["autonomy_level"] == "ASK_ONCE":
        confirmed = client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={
                "effect_hash": action["effect_hash"],
                "accepted": True,
            },
        )
        assert confirmed.status_code == 200
    else:
        assert action["autonomy_level"] == "AUTO_EXECUTE"
    receipt = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert receipt.status_code == 200 and receipt.json()["status"] == "SUCCEEDED"
    policy_id = actual_asset_declaration(client, engine, goal_id=goal_id, now=GOAL_NOW)
    url = f"/api/v1/full-policies/{policy_id}/asset-allocation"
    before = physical_snapshot(engine)
    response = client.get(
        url, params={"planning_mode": "FIXED_LADDER", "planning_comparison_days": "365"}
    )
    assert response.status_code == 200
    plan = response.json()["allocation"]
    assert plan["status"] == "OPTIMAL" and plan["ladder_status"] == "SINGLE_MATURITY_AVAILABLE"
    assert plan["purchase_count"] == 1
    assert plan["batches"][0]["amount_cents"] <= action["effect"]["amount_cents"]
    assert not plan["bank_authority"] and plan["future_income_included_cents"] == 0
    short = client.get(
        url, params={"planning_mode": "FIXED_LADDER", "planning_funds_use_date": "2026-10-06"}
    )
    assert short.status_code == 200 and not short.json()["allocation"]["batches"]
    assert physical_snapshot(engine) == before
