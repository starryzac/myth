"""Strict public DTO/router checks with doubles; not a database acceptance."""

from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import autonomy_envelope as api
from app.db.models import User
from app.domain.autonomy_envelope import evaluate_envelope
from app.services.autonomy_envelope import EnvelopeResponse
from app.services.dashboard_types import DashboardAuditCard
from app.tests.test_autonomy_envelope import proven_payment
from app.tests.test_execution_domain import NOW, USER
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def response() -> EnvelopeResponse:
    facts = proven_payment()
    return EnvelopeResponse(
        user_id=USER,
        as_of=NOW,
        action_id=None,
        action_type=facts.action_type,
        effect=facts.effect,
        amount_cents=facts.effect.amount_cents if facts.effect else None,
        assessment=evaluate_envelope(facts, audit_verified=True),
        sources=[],
        audit=DashboardAuditCard(
            epoch_id=UUID(int=900), status="VALID", complete=True, anchored_run_statuses={}
        ),
    )


def body() -> dict[str, Any]:
    return {"intent": {"kind": "pay_recurring", "policy_id": str(UUID(int=100))}}


def test_router_forwards_only_small_intent_actual_owner_and_server_clock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> EnvelopeResponse:
        calls.append(args)
        return response()

    monkeypatch.setattr(api, "assess_envelope_intent", capture)
    result = client.post("/api/v1/autonomy-envelope/assess", json=body())
    assert result.status_code == 200
    assert calls[0][1] == USER and calls[0][3] == NOW
    assert calls[0][2].kind == "pay_recurring"
    assert result.json()["authority_granted"] is False


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "now",
        "authority",
        "validation",
        "effect",
        "facts",
        "confirmation",
        "balance_cents",
    ],
)
def test_client_cannot_submit_financial_proof_or_permission(client: TestClient, field: str) -> None:
    assert (
        client.post("/api/v1/autonomy-envelope/assess", json={**body(), field: True}).status_code
        == 422
    )


@pytest.mark.parametrize("field", ["amount_cents", "source_context_hash", "risk", "grant"])
def test_client_cannot_expand_fixed_policy_intent(client: TestClient, field: str) -> None:
    assert (
        client.post(
            "/api/v1/autonomy-envelope/assess", json={"intent": {**body()["intent"], field: True}}
        ).status_code
        == 422
    )


def test_route_rejects_query_and_preserves_server_bound_action_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    action = UUID(int=321)
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> EnvelopeResponse:
        calls.append(args)
        return response()

    monkeypatch.setattr(api, "assess_envelope_action", capture)
    assert (
        client.get(f"/api/v1/autonomy-envelope/actions/{action}?authority=true").status_code == 422
    )
    assert client.get(f"/api/v1/autonomy-envelope/actions/{action}").status_code == 200
    assert calls[0][1:] == (USER, action, NOW)


def test_full_selector_is_only_finite_original_record_identity(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/autonomy-envelope/assess",
            json={
                "intent": {
                    "kind": "full_template",
                    "template_name": "InventedBankAuthority",
                    "policy_id": str(UUID(int=1)),
                }
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/autonomy-envelope/assess",
            json={
                "intent": {
                    "kind": "full_template",
                    "template_name": "RecoveryPolicy",
                    "policy_id": str(UUID(int=1)),
                    "amount_cents": 500,
                }
            },
        ).status_code
        == 422
    )
