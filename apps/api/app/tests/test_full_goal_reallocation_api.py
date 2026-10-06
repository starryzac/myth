"""Actual FastAPI body/query contract with explicit synthetic service doubles."""

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1.full_goal_reallocation import router
from app.db.models import User
from app.tests.test_full_goal_reallocation import NOW, USER
from app.tests.test_full_goal_reallocation_service import request, setup
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_preview_api_accepts_original_identity_json_and_rejects_client_financial_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = setup(monkeypatch)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    body = request().model_dump(mode="json")
    with TestClient(app) as client:
        response = client.post("/api/v1/goal-reallocation/preview", json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["decision"]["math"]["minimum_repair_cents"] == 25000
        assert result["decision"]["candidate_amount_cents"] is None
        assert result["bank_authority"] is result["financial_grant_created"] is False
        for extra in (
            "amount_cents",
            "now",
            "user_id",
            "bank_fact",
            "bank_authority",
            "grant",
            "accepted",
            "emergency",
        ):
            assert (
                client.post(
                    "/api/v1/goal-reallocation/preview", json=body | {extra: True}
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/v1/goal-reallocation/preview", json=body, params={extra: "fake"}
                ).status_code
                == 422
            )
        assert client.post("/api/v1/goal-reallocation/execute", json=body).status_code == 404
