"""One actual generated-bf_test search node; Root alone runs PostgreSQL."""

from uuid import UUID, uuid4

import pytest
from app.domain.full_decision_search import DecisionSearchResponse
from app.tests.test_autonomy_api import transfer_intent
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_actual_action_key_and_captured_version_search_is_zero_write(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    key = "full-706-exact-action-key + / : %"
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": key,
            "intent": transfer_intent(engine),
        },
    )
    assert prepared.status_code == 200, prepared.text
    action_id = UUID(prepared.json()["action_id"])
    before = physical_snapshot(engine)
    original = client.get(f"/api/v1/actions/{action_id}/decision")
    assert original.status_code == 200, original.text
    typed = original.json()["trace"]
    assert typed is not None and typed["policies"], original.text
    version_id = UUID(typed["policies"][0]["id"])
    first = client.get("/api/v1/decision-search", params={"action_id": str(action_id)})
    assert first.status_code == 200, first.text
    response = DecisionSearchResponse.model_validate_json(first.text)
    assert response.resolved_action_id == action_id and response.items
    assert response.inventory.selected_scope_count == response.inventory.captured_scope_count
    assert any(item.run_id == UUID(original.json()["run_id"]) for item in response.items)
    assert all(action_id in item.action_ids for item in response.items)
    by_key = client.get("/api/v1/decision-search", params={"action_key": key})
    assert by_key.status_code == 200, by_key.text
    key_response = DecisionSearchResponse.model_validate_json(by_key.text)
    assert key_response.resolved_action_id == action_id
    assert {item.run_id for item in key_response.items} == {item.run_id for item in response.items}
    narrowed = client.get(
        "/api/v1/decision-search",
        params={
            "action_id": str(action_id),
            "policy_version_id": str(version_id),
        },
    )
    assert narrowed.status_code == 200, narrowed.text
    version_response = DecisionSearchResponse.model_validate_json(narrowed.text)
    assert version_response.version_family == "MVP"
    assert any(
        item.run_id == UUID(original.json()["run_id"]) and item.match_state == "MATCHED"
        for item in version_response.items
    )
    assert all(
        not item.grants_authority and not item.financial_success_inferred
        for item in version_response.items
    )
    assert not version_response.absence_is_final and not version_response.audit_chain_verified
    assert not version_response.archived_records_searched
    for query in (
        {"action_id": str(uuid4())},
        {"policy_version_id": str(uuid4())},
        {"action_id": str(action_id), "epoch_id": str(uuid4())},
    ):
        assert client.get("/api/v1/decision-search", params=query).status_code == 404
    for query in (
        {"action_id": str(action_id), "amount_cents": "1"},
        {"action_id": str(action_id), "now": "2030-01-01"},
        {"action_id": str(action_id), "bank_confirmed": "true"},
    ):
        assert client.get("/api/v1/decision-search", params=query).status_code == 422
    assert physical_snapshot(engine) == before
