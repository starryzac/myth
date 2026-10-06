"""Public identity contract with service doubles only; no database or financial result."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_goal_release_execution as routes
from app.db.models import User
from app.services.full_goal_release_execution import GoalReleaseLookup
from app.tests.test_full_goal_release_execution_service import body, consent
from app.tests.test_goal_release_provenance import NOW, USER
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_demo_user] = lambda: User(
        id=USER, is_simulated=True, timezone="UTC"
    )
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_engine] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    calls = []

    def prepare(*args: Any) -> Any:
        calls.append(args)
        raise HTTPException(503, "SYNTHETIC_SERVICE_STOP_NO_FINANCIAL_RESULT")

    def lookup(*args: Any) -> GoalReleaseLookup:
        calls.append(args)
        return GoalReleaseLookup(
            user_id=USER,
            epoch_id=args[2],
            idempotency_key=args[3],
            status="NOT_FOUND_NOT_FINAL",
            original=None,
        )

    monkeypatch.setattr(routes, "prepare_goal_release_execution", prepare)
    monkeypatch.setattr(routes, "execute_goal_release_execution", prepare)
    monkeypatch.setattr(routes, "read_goal_release_by_key", lookup)
    with TestClient(app) as test:
        yield test, calls


def test_public_prepare_uses_actual_dependency_owner_and_clock_not_body(client: Any) -> None:
    test, calls = client
    response = test.post("/api/v1/goal-cash-releases/prepare", json=body())
    assert response.status_code == 503
    assert calls[0][1] == USER and calls[0][3] == NOW
    for field in ("amount_cents", "user_id", "now", "authority", "result"):
        assert (
            test.post("/api/v1/goal-cash-releases/prepare", json=body() | {field: True}).status_code
            == 422
        )
    assert len(calls) == 1


def test_execute_carries_explicit_effect_consent_and_rejects_missing_or_coerced_acceptance(
    client: Any,
) -> None:
    test, calls = client
    path = f"/api/v1/goal-cash-releases/actions/{USER}/execute"
    for payload in (
        {},
        consent() | {"accepted": False},
        consent() | {"accepted": 1},
        consent() | {"amount_cents": 1},
    ):
        assert test.post(path, json=payload).status_code == 422
    assert calls == []
    assert test.post(path, json=consent()).status_code == 503
    assert len(calls) == 1 and calls[0][3].accepted is True and calls[0][4] == NOW


def test_lookup_absence_is_unknown_and_query_cannot_attach_financial_facts(client: Any) -> None:
    test, calls = client
    path = f"/api/v1/goal-cash-releases/commands/{body()['expected_epoch_id']}/by-key/original"
    result = test.get(path)
    assert result.status_code == 200 and result.json()["status"] == "NOT_FOUND_NOT_FINAL"
    assert result.json()["not_found_is_final"] is result.json()["replacement_allowed"] is False
    assert test.get(path + "?bank_status=SETTLED").status_code == 422
    assert test.get(path.replace("original", "k" * 151)).status_code == 422
    assert len(calls) == 1
