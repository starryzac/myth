"""Strict actual JSON routing with doubles; no SQL or financial runtime claim."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_dynamic_goal_execution as api
from app.db.models import User
from app.domain.full_dynamic_goal_execution import derive_full_dynamic_goal_proof
from app.services.full_dynamic_goal_execution import FullDynamicGoalLookup, FullDynamicGoalPreview
from app.tests.test_full_dynamic_goal_execution import (
    NOW,
    USER,
    fixture,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_public_json_ids_roundtrip_and_money_clock_authority_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    expected = FullDynamicGoalPreview(
        user_id=USER,
        request=data.request,
        proof=derive_full_dynamic_goal_proof(data),
        limitations=[],
    )
    session, engine = object(), object()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    calls: list[tuple[Any, ...]] = []

    def preview(*args: Any) -> FullDynamicGoalPreview:
        calls.append(args)
        return expected

    monkeypatch.setattr(api, "preview_full_dynamic_goal_execution", preview)
    with TestClient(app) as client:
        body = data.request.model_dump(mode="json")
        response = client.post("/api/v1/dynamic-goal-actions/preview", json=body)
        assert response.status_code == 200
        assert response.json() == expected.model_dump(mode="json")
        assert "inputs" not in response.json()["proof"]
        for field in ("amount_cents", "now", "role", "user_id", "income_uses", "authority"):
            assert (
                client.post(
                    "/api/v1/dynamic-goal-actions/preview", json={**body, field: 1}
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/v1/dynamic-goal-actions/preview", json=body, params={field: "fake"}
                ).status_code
                == 422
            )
        for field in (
            "goal_id",
            "expected_epoch_id",
            "expected_policy_version_id",
            "expected_model_evidence_id",
        ):
            assert (
                client.post(
                    "/api/v1/dynamic-goal-actions/preview", json={**body, field: "bad"}
                ).status_code
                == 422
            )
    assert calls == [(session, USER, data.request, NOW)]


def test_lookup_is_strict_owner_clock_read_and_not_found_is_not_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(api.router)
    session = object()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    calls: list[tuple[Any, ...]] = []

    def lookup(*args: Any) -> FullDynamicGoalLookup:
        calls.append(args)
        return FullDynamicGoalLookup(user_id=USER, idempotency_key=args[2], status="NOT_FOUND")

    monkeypatch.setattr(api, "lookup_full_dynamic_goal_execution", lookup)
    with TestClient(app) as client:
        result = client.get("/api/v1/dynamic-goal-actions/by-key/actual-original-key")
        assert result.status_code == 200
        assert result.json()["not_found_is_final"] is False
        assert result.json()["current_authority"] is False
        assert result.json()["action"] is None and result.json()["confirmation"] is None
        assert client.get("/api/v1/dynamic-goal-actions/by-key/%20").status_code == 422
        assert client.get("/api/v1/dynamic-goal-actions/by-key/" + "x" * 121).status_code == 422
        for field in ("user_id", "epoch_id", "now", "authority"):
            assert (
                client.get(
                    "/api/v1/dynamic-goal-actions/by-key/actual-original-key",
                    params={field: "fake"},
                ).status_code
                == 422
            )
    assert calls == [(session, USER, "actual-original-key", NOW)]
