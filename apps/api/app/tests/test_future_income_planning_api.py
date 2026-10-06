"""Actual FastAPI JSON decoding with synthetic principal/service doubles, no PG."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import future_income_planning as routes
from app.api.v1.local_actor_sessions import get_local_actor_principal
from app.services import future_income_planning as service
from app.tests.test_future_income_planning import ACTOR, EPOCH, NOW, ORIGIN, USER, originals
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    _, row, double = originals()
    candidate = service._candidate(cast(Session, double), row, USER, EPOCH, NOW)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_session] = lambda: double
    app.dependency_overrides[get_local_actor_principal] = lambda: ACTOR
    monkeypatch.setattr(service, "create_future_income_candidate", lambda *_: candidate)
    with TestClient(app) as http:
        yield http, app, candidate


def test_native_json_uuid_strings_reach_original_candidate_route(client: Any) -> None:
    http, _, candidate = client
    response = http.post(
        "/api/v1/planning/future-income/candidates",
        json=candidate.original_request.model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate_id"] == str(candidate.candidate_id)
    assert response.json()["assumption"]["included_in_execution_cents"] == 0


@pytest.mark.parametrize(
    "extra", ["amount_cents", "clock", "role", "result", "receipt", "schedule"]
)
def test_fake_facts_extra_fields_and_query_fail_before_service(client: Any, extra: str) -> None:
    http, _, candidate = client
    body = candidate.original_request.model_dump(mode="json") | {extra: 12345}
    assert http.post("/api/v1/planning/future-income/candidates", json=body).status_code == 422
    assert (
        http.post(
            "/api/v1/planning/future-income/candidates?now=2030-01-01",
            json=candidate.original_request.model_dump(mode="json"),
        ).status_code
        == 422
    )


@pytest.mark.parametrize("role", ["AGENT", "SYSTEM", "REVIEWER"])
def test_non_user_principal_cannot_declare_income_condition(client: Any, role: str) -> None:
    http, app, candidate = client
    app.dependency_overrides[get_local_actor_principal] = lambda: ACTOR.model_copy(
        update={"role": role}
    )
    assert (
        http.post(
            "/api/v1/planning/future-income/candidates",
            json=candidate.original_request.model_dump(mode="json"),
        ).status_code
        == 403
    )


def test_wrong_owner_or_expired_current_principal_is_not_cached(client: Any) -> None:
    http, app, candidate = client
    body = {
        "expected_epoch_id": str(EPOCH),
        "origin_transaction_id": str(ORIGIN),
        "expected_origin_hash": candidate.source.origin_hash,
        "idempotency_key": "other.key",
    }
    app.dependency_overrides[get_local_actor_principal] = lambda: ACTOR.model_copy(
        update={"user_id": EPOCH}
    )
    assert http.post("/api/v1/planning/future-income/candidates", json=body).status_code == 403
    app.dependency_overrides[get_local_actor_principal] = lambda: ACTOR
    app.dependency_overrides[get_now] = lambda: ACTOR.expires_at
    assert (
        http.post(
            "/api/v1/planning/future-income/candidates",
            json=candidate.original_request.model_dump(mode="json"),
        ).status_code
        == 403
    )
