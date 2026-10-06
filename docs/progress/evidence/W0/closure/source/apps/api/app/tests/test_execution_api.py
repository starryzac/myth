"""Action HTTP contract carries intents and exact consent, never client authority."""

from uuid import uuid4

import pytest
from app.tests.test_execution_service import transfer_accounts
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_http_preparation_confirmation_execution_and_receipt_roundtrip(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    source, target = transfer_accounts(engine)
    body = {
        "idempotency_key": "http-transfer",
        "intent": {
            "kind": "transfer_internal",
            "source_account_id": str(source),
            "destination_account_id": str(target),
            "amount_cents": 12345,
        },
    }
    response = client.post("/api/v1/actions/prepare", json=body)
    assert response.status_code == 200, response.text
    action = response.json()
    identifier = action["action_id"]
    assert action["autonomy_level"] == "ASK_ONCE" and action["effect"]["amount_cents"] == 12345
    assert client.get(f"/api/v1/actions/{identifier}/receipt").status_code == 409
    before = all_tables(engine)
    assert client.post(f"/api/v1/actions/{identifier}/execute", json={}).status_code == 409
    assert all_tables(engine) == before
    confirmed = client.post(
        f"/api/v1/actions/{identifier}/confirm",
        json={"effect_hash": action["effect_hash"], "accepted": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    executed = client.post(f"/api/v1/actions/{identifier}/execute", json={})
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "SUCCEEDED"
    done = all_tables(engine)
    receipt = client.get(f"/api/v1/actions/{identifier}/receipt")
    assert receipt.status_code == 200 and receipt.json()["executed_cents"] == 12345
    assert (
        client.post(f"/api/v1/actions/{identifier}/execute", json={}).json()["receipt"]
        == receipt.json()
    )
    assert client.get(f"/api/v1/actions/{identifier}").json()["receipt"] == receipt.json()
    assert all_tables(engine) == done


def test_http_cannot_override_identity_clock_money_or_authority_on_execute(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    before = all_tables(engine)
    identifier = str(uuid4())
    for field, value in (
        ("user_id", str(uuid4())),
        ("as_of", "2030-01-01T00:00:00Z"),
        ("amount_cents", 1),
        ("autonomy_level", "AUTO_EXECUTE"),
    ):
        assert (
            client.post(f"/api/v1/actions/{identifier}/execute", json={field: value}).status_code
            == 422
        )
        assert client.get(f"/api/v1/actions/{identifier}?{field}={value}").status_code == 422
    for accepted in (False, "true", 1):
        assert (
            client.post(
                f"/api/v1/actions/{identifier}/confirm",
                json={"effect_hash": "a" * 64, "accepted": accepted},
            ).status_code
            == 422
        )
    assert client.get(f"/api/v1/actions/{identifier}").status_code == 404
    assert all_tables(engine) == before
