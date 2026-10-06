"""Real HMAC and FastAPI cookies against a synthetic user dependency; not financial PG."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now
from app.api.v1 import local_actor_sessions as routes
from app.services.local_actor_sessions import COOKIE_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 10, 6, tzinfo=UTC)
USER = UUID(int=606)
SECRET = "SYNTHETIC_USER_CREDENTIAL_API_ONLY_606"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", SECRET)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "61" * 32)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_demo_user] = lambda: SimpleNamespace(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    with TestClient(app) as http:
        yield http


def test_exact_cookie_session_is_authenticated_without_money_or_human_claim(client: Any) -> None:
    response = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": SECRET}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulation"] is True and body["bank_authority"] is False
    assert body["confirms_financial_action"] is False
    assert body["principal"]["role"] == "USER" and body["principal"]["user_id"] == str(USER)
    assert body["principal"]["human_identity_verified"] is False
    assert "secret" not in response.text and "signing_key" not in response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/v1" in cookie
    assert response.headers["cache-control"] == "no-store"
    read = client.get("/api/v1/local-actor/session")
    assert read.status_code == 200 and read.json()["principal"] == body["principal"]
    assert read.headers["cache-control"] == "no-store"
    logout = client.post("/api/v1/local-actor/logout", json={})
    assert logout.status_code == 200 and logout.json()["logged_out"] is True
    assert client.get("/api/v1/local-actor/session").status_code == 401


@pytest.mark.parametrize(
    "extra", ["role", "actor", "initiator", "user_id", "amount_cents", "now", "accepted"]
)
def test_client_fields_cannot_select_an_identity_or_bank_authority(client: Any, extra: str) -> None:
    response = client.post(
        "/api/v1/local-actor/login",
        json={"username": "bounded-user", "secret": SECRET, extra: "USER"},
    )
    assert response.status_code == 422
    assert COOKIE_NAME not in client.cookies
    assert client.get("/api/v1/local-actor/session").status_code == 401


@pytest.mark.parametrize("username,secret", [("agent", SECRET), ("bounded-user", "incorrect")])
def test_invalid_credentials_do_not_issue_a_session(
    client: Any, username: str, secret: str
) -> None:
    assert (
        client.post(
            "/api/v1/local-actor/login", json={"username": username, "secret": secret}
        ).status_code
        == 401
    )
    assert COOKIE_NAME not in client.cookies


def test_conflicting_cookie_and_client_bearer_is_not_a_role_override(client: Any) -> None:
    response = client.post(
        "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": SECRET}
    )
    assert response.status_code == 200
    assert (
        client.get(
            "/api/v1/local-actor/session", headers={"Authorization": "Bearer forged"}
        ).status_code
        == 401
    )
    token = client.cookies.get(COOKIE_NAME)
    assert token is not None
    assert (
        client.get(
            "/api/v1/local-actor/session", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 200
    )


def test_missing_credentials_bad_logout_and_query_do_not_widen_permissions(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "")
    assert (
        client.post(
            "/api/v1/local-actor/login", json={"username": "bounded-user", "secret": SECRET}
        ).status_code
        == 401
    )
    assert client.post("/api/v1/local-actor/logout", json={"role": "USER"}).status_code == 422
    assert (
        client.post(
            "/api/v1/local-actor/login?role=USER",
            json={"username": "bounded-user", "secret": SECRET},
        ).status_code
        == 422
    )
