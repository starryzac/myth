"""Real FastAPI signed-session dependency, service doubles; no financial or human proof."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_payment_permissions as routes
from app.api.v1 import local_actor_sessions as actors
from app.db.models import User
from app.services.full_payment_permissions import PaymentCommandLookup
from app.tests.test_full_payment_permissions import ACTION, EPOCH, NOW, USER, start_body
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

SECRET = "SYNTHETIC_ROLE_TEST_SECRET_ONLY_NOT_PRODUCTION"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", SECRET)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "ab" * 32)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(actors.router)
    app.dependency_overrides[get_demo_user] = lambda: User(
        id=USER, timezone="UTC", is_simulated=True
    )
    app.dependency_overrides[get_engine] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[get_now] = lambda: NOW
    calls: list[tuple[Any, ...]] = []

    def stop(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise HTTPException(503, "SERVICE_DOUBLE_NO_FINANCIAL_RESULT")

    def lookup(_s: Any, user: Any, epoch: Any, key: Any, _n: Any) -> PaymentCommandLookup:
        return PaymentCommandLookup(
            user_id=user,
            epoch_id=epoch,
            idempotency_key=key,
            status="NOT_FOUND_NOT_FINAL",
            original=None,
        )

    for name in (
        "start_payment_relation",
        "confirm_payment_relation",
        "prepare_full_payment",
        "confirm_full_payment_action",
        "execute_full_payment",
    ):
        monkeypatch.setattr(routes, name, stop)
    monkeypatch.setattr(routes, "read_payment_command", lookup)
    with TestClient(app) as test:
        yield test, calls


def login(test: TestClient) -> None:
    result = test.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": SECRET}
    )
    assert result.status_code == 200
    assert result.json()["principal"]["human_identity_verified"] is False
    assert result.json()["bank_authority"] is False


def test_new_payee_requires_actual_signed_user_dependency_and_uses_server_identity_clock(
    client: Any,
) -> None:
    test, calls = client
    assert test.post("/api/v1/full-payment-relations/start", json=start_body()).status_code == 401
    assert calls == []
    login(test)
    result = test.post("/api/v1/full-payment-relations/start", json=start_body())
    assert result.status_code == 503
    assert len(calls) == 1
    assert calls[0][1] == USER and calls[0][3].role == "USER" and calls[0][4] == NOW
    for field in ("actor", "initiator", "amount_cents", "paid_cents", "result", "now", "user_id"):
        assert (
            test.post(
                "/api/v1/full-payment-relations/start", json=start_body() | {field: True}
            ).status_code
            == 422
        )
    assert len(calls) == 1


def test_exact_relation_and_action_confirmation_reject_coercion_and_financial_override(
    client: Any,
) -> None:
    test, calls = client
    login(test)
    base = {
        "expected_epoch_id": str(EPOCH),
        "reviewed_scope_hash": "a" * 64,
        "accepted": True,
        "reason": "explicit",
        "idempotency_key": "original-confirm",
    }
    path = f"/api/v1/full-payment-relations/starts/{ACTION}/confirm"
    for changed in (
        {"accepted": 1},
        {"accepted": False},
        {"reason": " "},
        {"bank_status": "SETTLED"},
    ):
        assert test.post(path, json=base | changed).status_code == 422
    assert calls == []
    assert test.post(path, json=base).status_code == 503
    consent = {"expected_epoch_id": str(EPOCH), "reviewed_effect_hash": "b" * 64, "accepted": True}
    path = f"/api/v1/full-payment-relations/actions/{ACTION}/confirm"
    for changed in ({"accepted": 1}, {"accepted": False}, {"amount_cents": 1}):
        assert test.post(path, json=consent | changed).status_code == 422
    assert test.post(path, json=consent).status_code == 503
    assert len(calls) == 2


def test_readonly_original_absence_is_nonfinal_and_query_cannot_create_authority(
    client: Any,
) -> None:
    test, calls = client
    path = f"/api/v1/full-payment-relations/commands/{EPOCH}/by-key/original"
    result = test.get(path)
    assert result.status_code == 200
    assert result.json()["status"] == "NOT_FOUND_NOT_FINAL"
    assert result.json()["replacement_allowed"] is False
    assert test.get(path + "?authority=true").status_code == 422
    assert calls == []
