"""Two real-PG risk candidates for root; no formal acceptance or result fabrication."""

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.models import Account, AssetPosition
from app.domain.external_bank_fact_types import ExternalFactRequest
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import DEMO_USER_ID
from app.services.external_bank_facts import ingest_external_fact
from app.services.full_policy_lifecycle import canonical_candidate
from app.services.simulated_redemption_quote import issue_fixed_early_quote
from app.tests.test_full_asset_allocation_api import actual_income
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def actual_purchase(
    client: TestClient,
    engine: Engine,
    kind: Literal["LIQUID_ASSET", "FIXED_ASSET"],
) -> tuple[str, UUID, dict[str, Any]]:
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = str(epoch.id)
    template = client.post(
        f"/api/v1/demo/templates/{kind}/prepare",
        json={"expected_epoch_id": epoch_id},
    )
    assert template.status_code == 200, template.text
    declaration = template.json()
    confirmed = client.post(
        f"/api/v1/policy-proposals/{declaration['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": declaration["configuration_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    policy_id = confirmed.json()["policy_id"]
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "full-recovery-actual-purchase",
            "intent": {"kind": "purchase_asset", "policy_id": policy_id},
        },
    )
    assert prepared.status_code == 200, prepared.text
    action = prepared.json()
    if action["autonomy_level"] == "ASK_ONCE":
        accepted = client.post(
            f"/api/v1/actions/{action['action_id']}/confirm",
            json={
                "effect_hash": action["effect_hash"],
                "accepted": True,
            },
        )
        assert accepted.status_code == 200, accepted.text
    executed = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert executed.status_code == 200, executed.text
    done = executed.json()
    assert done["status"] == "SUCCEEDED" and done["receipt"]["executed_cents"] > 0
    assert done["receipt"]["fee_cents"] == done["receipt"]["loss_cents"] == 0
    return policy_id, UUID(action["effect"]["position_id"]), done["receipt"]


def actual_recovery_confirmation(client: TestClient, asset_policy_id: str) -> str:
    config: dict[str, Any] = {
        "type": "recovery",
        "scope": "general_idle_funds",
        "asset_policy_id": asset_policy_id,
        "triggers": ["LIQUIDITY_SHORTFALL"],
        "single_action_cap_cents": 1000000,
        "max_redemption_delay_days": 1,
        "allow_auto_recovery_without_penalty": True,
    }
    registered = client.post("/api/v1/catalog/products/register-current", json={})
    assert registered.status_code == 200 and registered.json()["registered_ids"]
    canonical = canonical_candidate("RecoveryPolicy", config)
    confirmed = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "RecoveryPolicy",
            "configuration": config,
            "accepted": True,
            "reviewed_hash": configuration_hash(canonical),
            "reason": "明确模拟恢复规划范围和零费损上限，不授予银行执行权限",
            "idempotency_key": "full-recovery-current-declaration",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert not confirmed.json()["bank_authority"]
    return str(confirmed.json()["policy_id"])


def actual_shrink_to_deficit(client: TestClient, engine: Engine) -> None:
    # The amount comes from the actual verified original curve, not an expected outcome label.
    response = client.get("/api/v1/planning/annual")
    assert response.status_code == 200 and not response.json()["source_issues"]
    actual = response.json()["annual_projection"]
    assert actual["status"] == "READY" and actual["minimum_margin_cents"] >= 0
    amount = actual["minimum_margin_cents"] + 30000
    with Session(engine) as session:
        account = session.scalar(
            select(Account).where(
                Account.user_id == DEMO_USER_ID,
                Account.account_type == "CASH",
            )
        )
        assert account is not None and account.balance_cents >= amount
        request = ExternalFactRequest(
            user_id=DEMO_USER_ID,
            account_id=account.id,
            kind="CONSUMPTION",
            amount_cents=amount,
            external_ref="full-recovery-original-boundary-consumption",
            idempotency_key="full-recovery-original-boundary-consumption",
            counterparty_ref="merchant",
            occurred_at=NOW,
        )
    outcome = ingest_external_fact(engine, DEMO_USER_ID, request, NOW)
    assert outcome.bank_status == "SETTLED" and outcome.projection_status == "PROJECTED"
    assert len(outcome.economic_posting_ids) == 2 and outcome.transaction_id is not None


def test_actual_t0_original_sources_and_365_readonly_cash_plan(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    actual_income(engine)
    linked_id, position_id, original_receipt = actual_purchase(client, engine, "LIQUID_ASSET")
    full_id = actual_recovery_confirmation(client, linked_id)
    actual_shrink_to_deficit(client, engine)
    before = physical_snapshot(engine)
    path = f"/api/v1/full-policies/{full_id}/recovery-planning"
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "COMPUTED" and body["source_issues"] == []
    result = body["plan"]
    assert result["status"] == "CONDITIONAL_RECOVERY_PLAN"
    assert result["actual_boundary"]["deficit_cents"] == 30000
    assert len(result["actual_boundary"]["calculation_trace"]) == 1098
    assert result["lossless_conditional_boundary"]["status"] == "READY"
    step = next(row for row in result["lossless_steps"] if row["position_id"] == str(position_id))
    assert step["original_quote"]["net_cents"] == original_receipt["executed_cents"]
    assert step["quote_source"] == "DERIVED_ORIGINAL_TERMS" and step["decision"] == "ASK_ONCE"
    assert step["original_policy_version_id"] and step["catalogue_version_id"]
    assert not body["bank_authority"] and body["execution_support"] == "NOT_IMPLEMENTED"
    assert client.get(path).json() == body
    for key in ("amount_cents", "user_id", "accepted", "goal_id", "now", "max_loss_cents"):
        assert client.get(path, params={key: "1"}).status_code == 422
    assert (
        client.get(
            path, params={"planning_deadline_at": (NOW + timedelta(days=1)).isoformat()}
        ).status_code
        == 422
    )
    assert physical_snapshot(engine) == before
    with Session(engine) as session, session.begin():
        account = session.scalar(
            select(Account).where(
                Account.user_id == DEMO_USER_ID,
                Account.account_type == "CASH",
            )
        )
        assert account is not None
        account.balance_cents += 1  # Deliberate isolated bank/application mismatch negative.
    tampered = physical_snapshot(engine)
    rejected = client.get(path).json()
    assert rejected["state"] == "UNKNOWN" and rejected["plan"] is None
    assert rejected["source_issues"] and physical_snapshot(engine) == tampered


def test_actual_fixed_original_issued_price_is_ask_only_and_expiry_is_not_repriced(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    actual_income(engine)
    linked_id, position_id, _ = actual_purchase(client, engine, "FIXED_ASSET")
    full_id = actual_recovery_confirmation(client, linked_id)
    actual_shrink_to_deficit(client, engine)
    quote = issue_fixed_early_quote(engine, DEMO_USER_ID, position_id, NOW)
    before = physical_snapshot(engine)
    path = f"/api/v1/full-policies/{full_id}/recovery-planning"
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "COMPUTED" and body["source_issues"] == []
    result = body["plan"]
    candidate = next(row for row in result["candidates"] if row["position_id"] == str(position_id))
    assert candidate["original_quote"] == quote.model_dump(mode="json")
    assert candidate["quote_source"] == "BANK_CONFIRMED" and candidate["decision"] == "ASK_ONCE"
    assert candidate["independent_loss_cents"] == 100 and candidate["net_cents"] == 49900
    assert "FULL_LOSS_CAP_EXCEEDED" in candidate["reasons"]
    assert not candidate["lossless_eligible"] and result["lossless_steps"] == []
    assert result["status"] == "LIQUIDITY_RISK"
    assert result["conditional_on_time_recovery_cents"] == 0
    assert candidate["conditional_impact_boundary"]["status"] == "READY"
    assert physical_snapshot(engine) == before
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=16)
    expired = client.get(path)
    assert expired.status_code == 200
    assert expired.json()["state"] == "UNKNOWN" and expired.json()["plan"] is None
    assert expired.json()["source_issues"] and physical_snapshot(engine) == before
    with Session(engine) as session:
        row = session.get(AssetPosition, position_id)
        assert row is not None and row.status == "HELD" and row.principal_cents == 50000
