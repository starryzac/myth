"""Identity-only JSON routes with explicit doubles; no database result claimed."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_action_set_boundary_full as api
from app.db.models import User
from app.domain.full_action_set_boundary_full import (
    FullActionSetSnapshot,
    FullGlobalBoundaryObservation,
    derive_full_action_set,
)
from app.services.full_action_set_boundary_full import verify_frozen_full_action_set_trace
from app.tests.test_full_action_set_boundary_full import fixture, trace_for
from app.tests.test_full_dynamic_goal_execution import NOW, USER
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_actual_json_routes_fixed_owner_clock_identity_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(api.router)
    session, engine = object(), object()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    value = fixture()
    snapshot = derive_full_action_set(value)
    original = verify_frozen_full_action_set_trace(trace_for(value))
    calls: list[tuple[Any, ...]] = []

    def current(*args: Any) -> FullActionSetSnapshot:
        calls.append(("current", *args))
        return snapshot

    def observe(*args: Any) -> FullGlobalBoundaryObservation:
        calls.append(("observe", *args))
        return original

    def read(*args: Any) -> FullGlobalBoundaryObservation:
        calls.append(("read", *args))
        return original

    monkeypatch.setattr(api, "read_current_full_action_set", current)
    monkeypatch.setattr(api, "observe_full_global_boundary", observe)
    monkeypatch.setattr(api, "read_full_global_boundary_observation", read)
    prefix = "/api/v1/boundary/full-action-set"
    body = original.original_request.model_dump(mode="json")
    with TestClient(app) as client:
        assert client.get(prefix + "/current").json() == snapshot.model_dump(mode="json")
        response = client.post(prefix + "/observe", json=body)
        assert response.status_code == 200 and response.json() == original.model_dump(mode="json")
        assert (
            client.get(prefix + f"/observations/{original.observation_run_id}").json()
            == response.json()
        )
        for field in (
            "amount_cents",
            "user_id",
            "now",
            "authority",
            "facts",
            "dynamic_goals",
            "original_inventory",
        ):
            assert client.post(prefix + "/observe", json={**body, field: 1}).status_code == 422
            for path in ("/current", "/observe", f"/observations/{original.observation_run_id}"):
                response = client.request(
                    "POST" if path == "/observe" else "GET",
                    prefix + path,
                    json=body if path == "/observe" else None,
                    params={field: "fake"},
                )
                assert response.status_code == 422
    assert calls == [
        ("current", session, USER, NOW),
        ("observe", engine, USER, original.original_request, NOW),
        ("read", session, USER, original.observation_run_id, NOW),
    ]
