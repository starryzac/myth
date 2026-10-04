"""Only the server can assess financial facts; advisory HTTP endpoints never write money."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.api.dependencies import get_demo_user, get_now
from app.db.models import User
from app.tests.test_execution_api_audit import prepared_transfer
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_goal_api import NOW, all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def transfer_intent(engine: Engine) -> dict[str, Any]:
    source, destination = transfer_accounts(engine)
    return {
        "kind": "transfer_internal",
        "source_account_id": str(source),
        "destination_account_id": str(destination),
        "amount_cents": 10000,
    }


def test_assess_intent_is_readonly_repeatable_and_never_grants_confirmation(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    intent = transfer_intent(engine)
    before = all_tables(engine)
    result = client.post("/api/v1/actions/assess", json={"intent": intent})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["simulation"] is True
    assert body["decision"]["level"] == "ASK_ONCE"
    assert body["decision"]["execution_eligible"] is False
    assert body["decision"]["financial_evaluation"] == "VERIFIED"
    assert body["effect"]["amount_cents"] == 10000
    assert client.post("/api/v1/actions/assess", json={"intent": intent}).json() == body
    assert all_tables(engine) == before


def test_autonomy_assessment_hides_other_users_actions_and_accounts(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    prepared = prepared_transfer(client, engine)
    effect = prepared["effect"]
    with Session(engine) as session, session.begin():
        foreign = User(
            id=uuid4(),
            external_ref="autonomy-foreign",
            display_name="另一模拟用户",
            is_simulated=True,
        )
        session.add(foreign)
        session.flush()
        session.expunge(foreign)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_demo_user] = lambda: foreign
    before = all_tables(engine)
    for action_id in [prepared["action_id"], str(uuid4())]:
        response = client.get(f"/api/v1/actions/{action_id}/autonomy")
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "NOT_FOUND"
    response = client.post(
        "/api/v1/actions/assess",
        json={
            "intent": {
                "kind": "transfer_internal",
                "source_account_id": effect["cash_uses"][0]["account_id"],
                "destination_account_id": effect["destination_account_id"],
                "amount_cents": effect["amount_cents"],
            }
        },
    )
    assert response.status_code == 404, response.text
    assert all_tables(engine) == before


def test_server_clock_blocks_expired_effect_and_query_cannot_override_it(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    prepared = prepared_transfer(client, engine)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_now] = lambda: NOW + timedelta(minutes=15)
    path = f"/api/v1/actions/{prepared['action_id']}/autonomy"
    before = all_tables(engine)
    expired = client.get(path)
    assert expired.status_code == 200, expired.text
    decision = expired.json()["decision"]
    assert decision["level"] == "BLOCKED"
    assert decision["execution_eligible"] is False
    assert "EFFECT_OUTSIDE_VALIDITY" in decision["reasons"]
    for query in ["as_of=2026-10-04T01:00:00Z", "authorized=true", "level=AUTO_EXECUTE"]:
        assert client.get(f"{path}?{query}").status_code == 422
    assert all_tables(engine) == before


def test_assessment_rejects_client_authority_worlds_identity_and_clock(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    intent = transfer_intent(engine)
    before = all_tables(engine)
    for key, value in {
        "authorized": True,
        "level": "AUTO_EXECUTE",
        "worlds": [],
        "as_of": "2030-01-01T00:00:00Z",
        "user_id": "other",
        "confirmation": {"accepted": True},
        "source_context_hash": "f" * 64,
    }.items():
        response = client.post("/api/v1/actions/assess", json={"intent": intent, key: value})
        assert response.status_code == 422, (key, response.text)
    assert (
        client.post("/api/v1/actions/assess?authorized=true", json={"intent": intent}).status_code
        == 422
    )
    assert all_tables(engine) == before


def test_existing_ask_keeps_human_provenance_then_refuses_to_reclassify_settled_money(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    prepared = prepared_transfer(client, engine)
    path = f"/api/v1/actions/{prepared['action_id']}/autonomy"
    before = all_tables(engine)
    initial = client.get(path)
    assert initial.status_code == 200, initial.text
    assert initial.json()["decision"]["level"] == "ASK_ONCE"
    assert initial.json()["decision"]["execution_eligible"] is False
    assert all_tables(engine) == before
    confirmed = client.post(
        f"/api/v1/actions/{prepared['action_id']}/confirm",
        json={
            "effect_hash": prepared["effect_hash"],
            "accepted": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    before = all_tables(engine)
    assessed = client.get(path)
    assert assessed.status_code == 200, assessed.text
    decision = assessed.json()["decision"]
    assert decision["level"] == "ASK_ONCE" and decision["execution_eligible"] is True
    assert decision["confirmation_satisfied"] is True
    assert all_tables(engine) == before
    done = client.post(f"/api/v1/actions/{prepared['action_id']}/execute", json={})
    assert done.status_code == 200 and done.json()["status"] == "SUCCEEDED"
    before = all_tables(engine)
    historical = client.get(path)
    assert historical.status_code == 409, historical.text
    assert historical.json()["error"]["code"] == "NO_RECLASSIFICATION_AFTER_ACCEPTANCE"
    assert all_tables(engine) == before
