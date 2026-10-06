"""Registered Main identity routes with a real SQL owner; all physical rows stay exact."""

from typing import Any

import pytest
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.main import create_app
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.local_actor_sessions import COOKIE_NAME
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_migrations import migrated_database as migrated_database
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_actual_local_signed_cookie_reads_real_owner_without_database_writes(
    migrated_database: tuple[Engine, Config], monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = migrated_database
    seed_demo(engine)
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", "SYNTHETIC_REAL_PG_USER_606_CREDENTIAL")
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "62" * 32)
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_now] = lambda: SEED_AS_OF
    before = physical_snapshot(engine)
    with TestClient(app) as client:
        assert client.get("/api/v1/local-actor/session").status_code == 401
        rejected = client.post(
            "/api/v1/local-actor/login",
            json={
                "username": "bounded-user",
                "secret": "SYNTHETIC_REAL_PG_USER_606_CREDENTIAL",
                "role": "SYSTEM",
            },
        )
        assert rejected.status_code == 422
        response = client.post(
            "/api/v1/local-actor/login",
            json={"username": "bounded-user", "secret": "SYNTHETIC_REAL_PG_USER_606_CREDENTIAL"},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        assert body["principal"]["user_id"] == str(DEMO_USER_ID)
        assert body["principal"]["role"] == "USER"
        assert body["principal"]["human_identity_verified"] is False
        assert body["bank_authority"] is body["confirms_financial_action"] is False
        assert "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/v1/local-actor/session").json() == body
        token = client.cookies.get(COOKIE_NAME)
        assert token is not None
        actual_cookie_domain = next(
            cookie.domain for cookie in client.cookies.jar if cookie.name == COOKIE_NAME
        )
        client.cookies.clear()
        client.cookies.set(
            COOKIE_NAME,
            "forged." + token.split(".")[-1],
            path="/api/v1",
            domain=actual_cookie_domain,
        )
        assert client.get("/api/v1/local-actor/session").status_code == 401
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, token, path="/api/v1", domain=actual_cookie_domain)
        assert client.get("/api/v1/local-actor/session").status_code == 200
        assert client.post("/api/v1/local-actor/logout", json={}).status_code == 200
        assert client.get("/api/v1/local-actor/session").status_code == 401
    assert physical_snapshot(engine) == before
