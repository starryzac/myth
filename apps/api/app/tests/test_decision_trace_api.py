"""Historical decision reads never authorize or settle simulated funds."""

from typing import Any

import pytest
from app.tests.test_autonomy_api import transfer_intent
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def prepared(client: TestClient, engine: Engine) -> dict[str, Any]:
    response = client.post(
        "/api/v1/actions/prepare",
        json={"idempotency_key": "trace-api-prepare", "intent": transfer_intent(engine)},
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_action_trace_and_explanation_keep_original_input_and_readonly(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared(client, engine)
    before = all_tables(engine)
    response = client.get(f"/api/v1/actions/{action['action_id']}/decision")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completeness"] == "COMPLETE"
    assert body["trace"]["phase"] == "PREPARE"
    assert body["trace"]["inputs"]["effect"] == action["effect"]
    assert body["trace"]["outcome"]["validation"] == action["prepared_validation"]
    assert body["trace"]["sources"]
    assert len(body["trace"]["constraints"]) >= 273
    path = f"/api/v1/decisions/{body['run_id']}"
    assert client.get(path).json() == body
    explanation = client.get(path + "/explanation")
    assert explanation.status_code == 200, explanation.text
    assert explanation.json() == body["explanation"]
    assert explanation.json()["audit_chain"] == "NOT_IMPLEMENTED"
    assert client.get("/api/v1/decisions?limit=1").json()["items"]
    assert all_tables(engine) == before


def test_decision_query_and_body_reject_client_clocks_and_authority(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared(client, engine)
    path = f"/api/v1/actions/{action['action_id']}/decision"
    before = all_tables(engine)
    for query in ["as_of=2030-01-01", "authorized=true", "user_id=other"]:
        assert client.get(path + "?" + query).status_code == 422
    for query in ["limit=0", "limit=101", "cursor=invalid", "authorized=true"]:
        assert client.get("/api/v1/decisions?" + query).status_code == 422
    assert all_tables(engine) == before


def test_explicit_assessment_records_decision_without_action_or_permission(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    intent = transfer_intent(engine)
    body = {"idempotency_key": "trace-evaluation", "intent": intent}
    first = client.post("/api/v1/decisions/assess", json=body)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["completeness"] == "COMPLETE"
    assert saved["trace"]["phase"] == "EVALUATION"
    assert saved["trace"]["outcome"]["decision"]["level"] == "ASK_ONCE"
    assert saved["trace"]["outcome"]["decision"]["execution_eligible"] is False
    assert saved["actions"] == []
    before = all_tables(engine)
    assert client.post("/api/v1/decisions/assess", json=body).json() == saved
    assert all_tables(engine) == before
    assert client.post("/api/v1/actions/assess", json={"intent": intent}).status_code == 200
    assert all_tables(engine) == before
    assert (
        client.post("/api/v1/decisions/assess", json={**body, "authorized": True}).status_code
        == 422
    )
