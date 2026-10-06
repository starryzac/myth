"""Real FastAPI/DTO plumbing with synthetic User dependency; no DB or financial proof."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_now
from app.api.v1.full_policy_compilation import router
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_policy_compiler import NOW, USER, user
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Any:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_demo_user] = user
    app.dependency_overrides[get_now] = lambda: NOW

    @app.exception_handler(PolicyLifecycleError)
    async def failure(_request: Request, error: PolicyLifecycleError) -> JSONResponse:
        return JSONResponse({"code": error.code}, status_code=error.status_code)

    with TestClient(app) as test:
        yield test


def test_public_natural_json_uses_server_user_local_date_and_never_persists_confirmation(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/full-policy-compilations/preview", json={"text": "保留3000元应急金"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["user_id"] == str(USER) and result["reference_date"] == "2026-10-06"
    assert result["compilation"]["configuration"]["amount_cents"] == 300000
    assert not result["confirmation_record_created"] and not result["bank_authority"]
    assert client.get("/api/v1/full-policy-compilations/grammar").status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        "role",
        "actor",
        "bank_facts",
        "time",
        "amount_cents",
        "accepted",
        "provider",
        "network_url",
        "llm_enabled",
    ],
)
def test_public_body_rejects_authority_fact_clock_network_and_provider_overrides(
    client: TestClient, extra: str
) -> None:
    response = client.post(
        "/api/v1/full-policy-compilations/preview", json={"text": "保留3000元应急金", extra: True}
    )
    assert response.status_code == 422


def test_default_llm_is_disabled_even_when_client_requests_the_engine(client: TestClient) -> None:
    response = client.post(
        "/api/v1/full-policy-compilations/preview",
        json={"text": "保留3000元应急金", "engine": "llm"},
    )
    assert response.status_code == 422 and response.json()["code"] == "LLM_DISABLED"


def test_legal_uuid_json_reference_is_independently_validated_without_coercing_money_bool(
    client: TestClient,
) -> None:
    body: dict[str, Any] = {
        "text": "保留3000元应急金",
        "comparison_candidate": {
            "template_name": "PeriodicTransferPolicy",
            "configuration": {
                "type": "periodic_transfer",
                "source_account_id": str(USER),
                "payee_id": "demo-landlord",
                "amount_rule": {"kind": "exact", "amount_cents": 100},
                "due_day": 5,
                "single_action_cap_cents": 100,
            },
        },
    }
    assert client.post("/api/v1/full-policy-compilations/preview", json=body).status_code == 200
    body["comparison_candidate"]["configuration"]["single_action_cap_cents"] = True
    response = client.post("/api/v1/full-policy-compilations/preview", json=body)
    assert response.status_code == 422 and response.json()["code"] == "INVALID_COMPARISON"


def test_unregistered_query_and_empty_text_are_rejected(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/full-policy-compilations/preview?role=USER", json={"text": "保留3000元应急金"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/full-policy-compilations/preview", json={"text": ""}).status_code
        == 422
    )
