"""Synthetic actual FastAPI JSON/delegation risks; no SQL/bank execution proof."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now, get_session
from app.api.v1 import full_recovery_execution as api
from app.api.v1.local_actor_sessions import get_local_actor_principal
from app.db.models import User
from app.domain.full_recovery_execution import derive_full_recovery_execution_proof
from app.domain.local_actor_session_types import LocalActorPrincipal
from app.services.full_recovery_execution import (
    FullRecoveryExecutionLookup,
    FullRecoveryExecutionPreview,
)
from app.tests.test_full_recovery_execution import ACTION, fixture
from app.tests.test_recovery import NOW, USER
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def setup() -> tuple[FastAPI, object, object, LocalActorPrincipal]:
    session, engine = object(), object()
    principal = LocalActorPrincipal(
        user_id=USER,
        role="USER",
        session_id=UUID(int=923),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_local_actor_principal] = lambda: principal
    return app, session, engine, principal


def test_actual_json_uuid_preview_and_extra_financial_inputs_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session, _, _ = setup()
    data = fixture()
    expected = FullRecoveryExecutionPreview(
        user_id=USER,
        original_request=data.request,
        proof=derive_full_recovery_execution_proof(data),
        limitations=["SYNTHETIC_SCOPE_ONLY_NOT_FINANCIAL_RUNTIME"],
    )
    calls: list[tuple[Any, ...]] = []

    def preview(*args: Any) -> FullRecoveryExecutionPreview:
        calls.append(args)
        return expected

    monkeypatch.setattr(api, "preview_full_recovery_execution", preview)
    body = data.request.model_dump(mode="json")
    with TestClient(app) as client:
        result = client.post("/api/v1/full-recovery-actions/preview", json=body)
        assert result.status_code == 200
        assert result.json() == expected.model_dump(mode="json")
        for field in ("amount_cents", "now", "role", "receipt", "authority", "user_id"):
            assert (
                client.post(
                    "/api/v1/full-recovery-actions/preview", json={**body, field: 1}
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/v1/full-recovery-actions/preview", json=body, params={field: "fake"}
                ).status_code
                == 422
            )
        for field in ("policy_id", "expected_version_id", "expected_epoch_id", "position_id"):
            assert (
                client.post(
                    "/api/v1/full-recovery-actions/preview", json={**body, field: "bad"}
                ).status_code
                == 422
            )
    assert calls == [(session, USER, data.request, NOW)]


@pytest.mark.parametrize("kind", ["prepare", "confirm", "execute"])
def test_write_json_routes_delegate_exact_original_to_signed_user_service(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    app, _, engine, principal = setup()
    data = fixture()
    if kind == "prepare":
        path = "/api/v1/full-recovery-actions/prepare"
        body = data.request.model_dump(mode="json")
        name = "prepare_full_recovery_execution"
        prefix: tuple[object, ...] = (engine, USER)
    else:
        path = f"/api/v1/full-recovery-actions/actions/{ACTION}/{kind}"
        body = {"expected_epoch_id": str(data.epoch_id), "reviewed_effect_hash": "a" * 64}
        if kind == "confirm":
            body["accepted"] = True
        name = f"{kind}_full_recovery_action"
        prefix = (engine, USER, ACTION)
    calls: list[tuple[Any, ...]] = []

    def original_pipeline(*args: Any) -> None:
        calls.append(args)
        # Actual route/DTO succeeds, but do not manufacture a successful bank response.
        raise HTTPException(409, "SYNTHETIC_NATIVE_PIPELINE_SENTINEL")

    monkeypatch.setattr(api, name, original_pipeline)
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 409
        assert client.post(path, json={**body, "amount_cents": 1}).status_code == 422
        assert client.post(path, json=body, params={"now": "fake"}).status_code == 422
        if kind == "confirm":
            for accepted in (False, 1, "true"):
                assert client.post(path, json={**body, "accepted": accepted}).status_code == 422
    assert len(calls) == 1 and calls[0][:-3] == prefix
    assert calls[0][-3].model_dump(mode="json") == body
    assert calls[0][-2:] == (principal, NOW)


def test_original_key_lookup_keeps_not_found_nonfinal_and_encoded_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session, _, _ = setup()
    calls: list[tuple[Any, ...]] = []

    def lookup(*args: Any) -> FullRecoveryExecutionLookup:
        calls.append(args)
        return FullRecoveryExecutionLookup(
            user_id=USER, idempotency_key=args[2], status="NOT_FOUND_NOT_FINAL"
        )

    monkeypatch.setattr(api, "lookup_full_recovery_execution", lookup)
    with TestClient(app) as client:
        result = client.get("/api/v1/full-recovery-actions/by-key/original%2Fexact")
        assert result.status_code == 200
        assert result.json()["not_found_is_final"] is False
        assert result.json()["original_consent"] is None
        assert result.json()["current_authority"] is False
        for key in ("%20", "x" * 121):
            assert client.get(f"/api/v1/full-recovery-actions/by-key/{key}").status_code == 422
        assert (
            client.get(
                "/api/v1/full-recovery-actions/by-key/original", params={"epoch": "fake"}
            ).status_code
            == 422
        )
    assert calls == [(session, USER, "original/exact", NOW)]


def test_missing_current_local_session_cannot_call_even_readonly_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _, _ = setup()
    del app.dependency_overrides[get_local_actor_principal]

    def forbidden(*args: Any) -> None:
        raise AssertionError("A missing session must not call the service")

    monkeypatch.setattr(api, "preview_full_recovery_execution", forbidden)
    monkeypatch.setattr(api, "lookup_full_recovery_execution", forbidden)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/full-recovery-actions/preview",
                json=fixture().request.model_dump(mode="json"),
            ).status_code
            == 401
        )
        assert client.get("/api/v1/full-recovery-actions/by-key/original").status_code == 401


@pytest.mark.parametrize("change", ["role", "owner", "expired"])
def test_authenticated_non_user_or_expired_identity_cannot_preview(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    app, _, _, principal = setup()
    updates: dict[str, Any] = (
        {"role": "AGENT"}
        if change == "role"
        else (
            {"user_id": UUID(int=999)}
            if change == "owner"
            else {"issued_at": NOW - timedelta(minutes=12), "expires_at": NOW}
        )
    )
    app.dependency_overrides[get_local_actor_principal] = lambda: principal.model_copy(
        update=updates
    )

    def forbidden(*args: Any) -> None:
        raise AssertionError("The current USER role/owner/time must be checked")

    monkeypatch.setattr(api, "preview_full_recovery_execution", forbidden)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/full-recovery-actions/preview",
                json=fixture().request.model_dump(mode="json"),
            ).status_code
            == 403
        )
