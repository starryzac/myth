"""HTTP input and server identity binding only; stub responses are not financial evidence."""

from typing import Any, cast
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import delivery as api_module
from app.db.models import User
from app.services.command_delivery import DeliveryList, DeliveryView
from app.tests.test_command_delivery import EPOCH
from app.tests.test_execution_domain import NOW, OP, USER
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

OUTBOX = UUID(int=1200)


def view() -> DeliveryView:
    return DeliveryView(
        outbox_id=OUTBOX,
        root_id=OP,
        action_id=OP,
        epoch_id=EPOCH,
        payload_hash="a" * 64,
        outbox_state="PENDING",
        inbox_state=None,
        source_action_status="PLANNED",
        bank_status=None,
        current_action_available=True,
        blocking_reason=None,
        service_receipt_verified=False,
        attempts=[],
        last_error=None,
    )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[tuple[Any, ...]]]:
    api = FastAPI()
    api.include_router(api_module.router)
    engine, session = cast(Engine, object()), cast(Session, object())
    user = User(id=USER, external_ref="pure-http-test", is_simulated=True)
    api.dependency_overrides[get_engine] = lambda: engine
    api.dependency_overrides[get_session] = lambda: session
    api.dependency_overrides[get_demo_user] = lambda: user
    api.dependency_overrides[get_now] = lambda: NOW
    calls: list[tuple[Any, ...]] = []

    def stub_delivery(*args: Any, **kwargs: Any) -> DeliveryView:
        calls.append((*args, kwargs))
        return view()

    def stub_list(*args: Any, **kwargs: Any) -> DeliveryList:
        calls.append((*args, kwargs))
        return DeliveryList(items=[view()])

    for name in ("get_delivery", "enqueue_action", "deliver_command"):
        monkeypatch.setattr(api_module, name, stub_delivery)
    monkeypatch.setattr(api_module, "list_deliveries", stub_list)
    return TestClient(api), calls


@pytest.mark.parametrize("operation", ["deliver", "enqueue"])
def test_command_routes_forward_only_original_ids_and_server_principal_clock(
    harness: tuple[TestClient, list[tuple[Any, ...]]], operation: str
) -> None:
    client, calls = harness
    path = (
        f"/api/v1/delivery/{OUTBOX}/deliver"
        if operation == "deliver"
        else f"/api/v1/delivery/actions/{OP}/enqueue"
    )
    response = client.post(path, json={})
    assert response.status_code == 200
    assert response.json()["economic_verified"] is False
    assert len(calls) == 1
    assert calls[0][1] == USER and calls[0][3] == NOW
    assert calls[0][2] == (OUTBOX if operation == "deliver" else OP)
    assert calls[0][-1] == {}


@pytest.mark.parametrize(
    "key",
    [
        "user_id",
        "now",
        "amount_cents",
        "payload",
        "effect_hash",
        "accepted",
        "grant",
        "consumer_ref",
    ],
)
@pytest.mark.parametrize("operation", ["deliver", "enqueue"])
def test_empty_command_body_rejects_financial_or_authority_injection_before_service(
    harness: tuple[TestClient, list[tuple[Any, ...]]], key: str, operation: str
) -> None:
    client, calls = harness
    path = (
        f"/api/v1/delivery/{OUTBOX}/deliver"
        if operation == "deliver"
        else f"/api/v1/delivery/actions/{OP}/enqueue"
    )
    assert client.post(path, json={key: True}).status_code == 422
    assert calls == []


def test_read_query_limit_accepts_http_integer_and_rejects_authority_query_fields(
    harness: tuple[TestClient, list[tuple[Any, ...]]],
) -> None:
    client, calls = harness
    assert client.get("/api/v1/delivery", params={"limit": 1}).status_code == 200
    assert calls[-1][-1] == {"limit": 1}
    calls.clear()
    for parameters in ({"limit": 101}, {"limit": 0}, {"user_id": str(USER)}, {"grant": "true"}):
        assert client.get("/api/v1/delivery", params=parameters).status_code == 422
    assert calls == []
    assert (
        client.get(f"/api/v1/delivery/{OUTBOX}", params={"now": NOW.isoformat()}).status_code == 422
    )
    assert calls == []
