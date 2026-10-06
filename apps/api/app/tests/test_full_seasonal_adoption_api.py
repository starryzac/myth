"""Real signed local-session dependency and strict JSON; service doubles only."""

from collections.abc import Iterator
from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_seasonal_adoption as routes
from app.api.v1 import local_actor_sessions as actors
from app.db.models import User
from app.services.full_seasonal_adoption import SeasonalAdoptionLookup
from app.tests.test_full_seasonal_adoption import EPOCH, POLICY, USER, original_fixture
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, list[tuple[Any, ...]]]]:
    original = original_fixture()
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", "SYNTHETIC_ROLE_ONLY_NO_ACTUAL_HUMAN")
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "ef" * 32)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(actors.router)
    app.dependency_overrides[get_demo_user] = lambda: User(
        id=USER, is_simulated=True, timezone="Asia/Shanghai"
    )
    app.dependency_overrides[get_now] = lambda: original.recorded_at
    app.dependency_overrides[get_engine] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    calls: list[tuple[Any, ...]] = []

    def stop(*args: Any) -> Any:
        calls.append(args)
        raise HTTPException(503, "SERVICE_DOUBLE_NOT_FINANCIAL_PROOF")

    def lookup(_session: Any, user: Any, epoch: Any, key: Any, _now: Any) -> SeasonalAdoptionLookup:
        return SeasonalAdoptionLookup(
            user_id=user,
            epoch_id=epoch,
            idempotency_key=key,
            status="NOT_FOUND_NOT_FINAL",
            original_receipt=None,
        )

    monkeypatch.setattr(routes, "preview_seasonal_adoption", stop)
    monkeypatch.setattr(routes, "confirm_seasonal_adoption", stop)
    monkeypatch.setattr(routes, "read_seasonal_adoption_command", lookup)
    with TestClient(app) as test:
        yield test, calls


def test_signed_user_is_required_and_legal_uuid_json_reaches_only_server_principal(
    client: tuple[TestClient, list[tuple[Any, ...]]],
) -> None:
    test, calls = client
    body = original_fixture().original_request.model_dump(mode="json")
    path = f"/api/v1/seasonal-reserve-adoptions/{POLICY}/confirm"
    assert test.post(path, json=body).status_code == 401 and not calls
    assert (
        test.post(
            "/api/v1/local-actor/login",
            json={"username": "bounded-user", "secret": "SYNTHETIC_ROLE_ONLY_NO_ACTUAL_HUMAN"},
        ).status_code
        == 200
    )
    assert test.post(path, json=body).status_code == 503
    assert calls[0][1] == USER and calls[0][4].role == "USER"
    assert calls[0][5] == original_fixture().recorded_at
    bad_bodies: tuple[dict[str, Any], ...] = (
        {"accepted": 1},
        {"accepted": False},
        {"actor": "USER"},
        {"amount_cents": 1},
        {"now": "2026-02-14T00:00:00Z"},
    )
    for changed in bad_bodies:
        assert test.post(path, json=body | changed).status_code == 422
    assert len(calls) == 1


def test_original_get_is_available_without_session_and_absence_is_not_final(
    client: tuple[TestClient, list[tuple[Any, ...]]],
) -> None:
    test, calls = client
    path = f"/api/v1/seasonal-reserve-adoptions/commands/{EPOCH}/by-key/original"
    result = test.get(path)
    assert result.status_code == 200 and result.json()["simulation"] is True
    assert result.json()["status"] == "NOT_FOUND_NOT_FINAL"
    assert result.json()["original_receipt"] is None and not result.json()["bank_authority"]
    assert test.get(path + "?replace=true").status_code == 422
    assert not calls


def test_preview_is_readonly_and_accepts_no_client_amount_or_source(
    client: tuple[TestClient, list[tuple[Any, ...]]],
) -> None:
    test, calls = client
    original = original_fixture()
    body = {
        "expected_version_id": str(original.scope.version_id),
        "window_id": original.scope.window_id,
    }
    path = f"/api/v1/seasonal-reserve-adoptions/{POLICY}/preview"
    assert test.post(path, json=body).status_code == 503 and len(calls) == 1
    overrides: tuple[dict[str, Any], ...] = (
        {"bank_facts": []},
        {"period_start": "2026-01-01"},
        {"result": "READY"},
        {"amount_cents": 1800},
    )
    for changed in overrides:
        assert test.post(path, json=body | changed).status_code == 422
    assert len(calls) == 1
