"""Real FastAPI JSON codec with explicit synthetic source doubles; no financial proof."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_maturity_execution as api
from app.api.v1.local_actor_sessions import get_local_actor_principal
from app.db.models import User
from app.domain.full_maturity_execution import derive_maturity_proof
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.services.full_maturity_execution import FullMaturityLookup, FullMaturityPreview
from app.tests.test_full_maturity_execution import fixture
from app.tests.test_recovery import NOW, USER
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def setup() -> tuple[FastAPI, object, object]:
    app = FastAPI()
    app.include_router(api.router)
    session, engine = object(), object()
    principal = LocalActorPrincipal(
        user_id=USER,
        role="USER",
        session_id=UUID(int=941),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_local_actor_principal] = lambda: principal
    return app, session, engine


def test_http_json_uuid_body_delegates_exactly_without_client_money_or_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session, _ = setup()
    data = fixture()
    expected = FullMaturityPreview(
        user_id=USER,
        original_request=data.request,
        proof=derive_maturity_proof(data),
        limitations=["SYNTHETIC_SOURCE_DOUBLE_ONLY"],
    )
    calls: list[tuple[Any, ...]] = []

    def preview(*args: Any) -> FullMaturityPreview:
        calls.append(args)
        return expected

    monkeypatch.setattr(api, "preview_maturity_execution", preview)
    body = data.request.model_dump(mode="json")
    with TestClient(app) as client:
        response = client.post("/api/v1/full-maturity-actions/preview", json=body)
        assert response.status_code == 200 and response.json() == expected.model_dump(mode="json")
        assert calls == [(session, USER, data.request, NOW)]
        for name in (
            "amount_cents",
            "principal_cents",
            "now",
            "receipt",
            "role",
            "user_id",
            "bank_request",
        ):
            assert (
                client.post(
                    "/api/v1/full-maturity-actions/preview", json={**body, name: 1}
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/api/v1/full-maturity-actions/preview?amount_cents=1", json=body
            ).status_code
            == 422
        )
        assert len(calls) == 1


def test_lookup_not_found_is_nonfinal_original_key_and_no_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session, _ = setup()
    calls: list[tuple[Any, ...]] = []

    def lookup(*args: Any) -> FullMaturityLookup:
        calls.append(args)
        return FullMaturityLookup(
            user_id=USER, idempotency_key=args[2], status="NOT_FOUND_NOT_FINAL"
        )

    monkeypatch.setattr(api, "lookup_maturity_execution", lookup)
    with TestClient(app) as client:
        response = client.get("/api/v1/full-maturity-actions/by-key/whole:original")
        assert response.status_code == 200 and response.json()["not_found_is_final"] is False
        assert calls == [(session, USER, "whole:original", NOW)]
        assert (
            client.get("/api/v1/full-maturity-actions/by-key/whole:original?fresh=true").status_code
            == 422
        )
        assert len(calls) == 1


@pytest.mark.parametrize("role", ["AGENT", "REVIEWER", "SYSTEM", "DEMO_ADMIN"])
def test_signed_nonuser_roles_cannot_create_maturity_commands(role: str) -> None:
    app, _, _ = setup()
    principal = LocalActorPrincipal.model_validate(
        {
            "user_id": USER,
            "role": role,
            "session_id": UUID(int=1),
            "issued_at": NOW,
            "expires_at": NOW + timedelta(minutes=5),
        }
    )
    app.dependency_overrides[get_local_actor_principal] = lambda: principal
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/full-maturity-actions/prepare",
                json=fixture().request.model_dump(mode="json"),
            ).status_code
            == 403
        )


def test_missing_authentication_is_not_a_default_user() -> None:
    app, _, _ = setup()

    def missing() -> None:
        raise HTTPException(401, "NOT_CONFIGURED_OR_INVALID")

    app.dependency_overrides[get_local_actor_principal] = missing
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/full-maturity-actions/prepare",
                json=fixture().request.model_dump(mode="json"),
            ).status_code
            == 401
        )
