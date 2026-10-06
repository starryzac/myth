"""Public JSON identity contract with doubles, no database or money execution."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_action_set_boundary as api
from app.db.models import User
from app.domain.full_action_set_boundary import (
    ActionSetSnapshot,
    GlobalBoundaryObservation,
    derive_action_set,
)
from app.services.full_action_set_boundary import verify_frozen_action_set_trace
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_full_action_set_boundary import EPOCH, fixture, trace_for
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_actual_json_routes_use_fixed_owner_clock_and_reject_fake_financial_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(api.router)
    session, engine = object(), object()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    snapshot, recorded = (
        derive_action_set(fixture()),
        verify_frozen_action_set_trace(trace_for(fixture())),
    )
    calls: list[tuple[Any, ...]] = []

    def current(*args: Any) -> ActionSetSnapshot:
        calls.append(("current", *args))
        return snapshot

    def observe(*args: Any) -> GlobalBoundaryObservation:
        calls.append(("observe", *args))
        return recorded

    def read(*args: Any) -> GlobalBoundaryObservation:
        calls.append(("read", *args))
        return recorded

    monkeypatch.setattr(api, "read_current_action_set", current)
    monkeypatch.setattr(api, "observe_global_boundary", observe)
    monkeypatch.setattr(api, "read_global_boundary_observation", read)
    with TestClient(app) as client:
        result = client.get("/api/v1/boundary/action-set/current")
        assert result.status_code == 200 and result.json() == snapshot.model_dump(mode="json")
        body = recorded.original_request.model_dump(mode="json")
        result = client.post("/api/v1/boundary/action-set/observe", json=body)
        assert result.status_code == 200 and result.json() == recorded.model_dump(mode="json")
        result = client.get(
            f"/api/v1/boundary/action-set/observations/{recorded.observation_run_id}"
        )
        assert result.status_code == 200 and result.json() == recorded.model_dump(mode="json")
        for field in (
            "amount_cents",
            "user_id",
            "now",
            "authority",
            "facts",
            "original_inventory",
            "action_set_signature",
        ):
            assert (
                client.post(
                    "/api/v1/boundary/action-set/observe", json={**body, field: 1}
                ).status_code
                == 422
            )
            for path in (
                "/api/v1/boundary/action-set/current",
                f"/api/v1/boundary/action-set/observations/{recorded.observation_run_id}",
            ):
                assert client.get(path, params={field: "fake"}).status_code == 422
            assert (
                client.post(
                    "/api/v1/boundary/action-set/observe", json=body, params={field: "fake"}
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/api/v1/boundary/action-set/observe", json={**body, "expected_epoch_id": "bad"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/boundary/action-set/observe", json={**body, "idempotency_key": " "}
            ).status_code
            == 422
        )
    assert calls == [
        ("current", session, USER, NOW),
        ("observe", engine, USER, recorded.original_request, NOW),
        ("read", session, USER, recorded.observation_run_id, NOW),
    ]
    assert recorded.epoch_id == EPOCH
