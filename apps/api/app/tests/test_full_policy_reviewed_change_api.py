"""Actual FastAPI JSON/auth/delegation with synthetic services; no SQL or bank run."""

from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_now
from app.api.v1 import full_policy_reviewed_change as api
from app.api.v1.local_actor_sessions import get_local_actor_principal
from app.db.models import User
from app.domain.full_policy_reviewed_change import ReviewedChangeRecord
from app.services.full_policy_reviewed_change import ReviewCommandLookup
from app.tests.test_full_policy_reviewed_change import confirm_body
from app.tests.test_full_policy_reviewed_change import original as original
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def app(original: ReviewedChangeRecord) -> FastAPI:
    value = FastAPI()
    value.include_router(api.router)
    value.dependency_overrides[get_engine] = lambda: object()
    value.dependency_overrides[get_demo_user] = lambda: User(id=original.user_id, is_simulated=True)
    value.dependency_overrides[get_now] = lambda: original.captured_at
    value.dependency_overrides[get_local_actor_principal] = lambda: original.actor
    return value


@pytest.mark.parametrize("phase", ["review", "confirm"])
def test_actual_json_closed_uuid_request_and_signed_user_original_body_forwarding(
    app: FastAPI, original: ReviewedChangeRecord, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> None:
        calls.append(args)
        raise HTTPException(409, "SYNTHETIC_SERVICE_CALL_ONLY")

    monkeypatch.setattr(
        api, "register_review" if phase == "review" else "confirm_reviewed_change", capture
    )
    request = original.request if phase == "review" else confirm_body(original)
    raw = request.model_dump(mode="json")
    path = f"/api/v1/reviewed-policy-changes/MVP_POLICY/{original.policy_id}/{phase}"
    with TestClient(app) as client:
        assert client.post(path, json=raw).status_code == 409
        for changed in (
            {"expected_epoch_id": "bad"},
            {"expected_version_id": 1},
            {"user_id": str(UUID(int=999))},
            {"amount_cents": 1},
            {"now": "2030-01-01"},
            {"authority": True},
            {"result": "SUCCESS"},
            {"actor": "USER"},
        ):
            assert client.post(path, json={**raw, **changed}).status_code == 422
        assert client.post(path, json=raw, params={"balance": "9999"}).status_code == 422
        if phase == "confirm":
            for accepted in (False, 1, "true"):
                assert client.post(path, json={**raw, "accepted": accepted}).status_code == 422
    assert len(calls) == 1
    assert calls[0][1:4] == (original.user_id, "MVP_POLICY", original.policy_id)
    assert calls[0][4].model_dump(mode="json") == raw
    assert calls[0][5:] == (original.captured_at, original.actor)


@pytest.mark.parametrize("phase", ["review", "confirm"])
@pytest.mark.parametrize("condition", ["no_cookie", "agent", "expired", "other_owner"])
def test_current_signed_user_required_before_any_metadata_or_configuration_commit(
    app: FastAPI,
    original: ReviewedChangeRecord,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    condition: str,
) -> None:
    if condition == "no_cookie":
        del app.dependency_overrides[get_local_actor_principal]
    else:
        principal = original.actor.model_copy(
            update={"role": "AGENT"}
            if condition == "agent"
            else {"expires_at": original.captured_at}
            if condition == "expired"
            else {"user_id": UUID(int=999)}
        )
        app.dependency_overrides[get_local_actor_principal] = lambda: principal

    def forbidden(*args: Any) -> None:
        pytest.fail("Identity rejection must precede the real write service")

    monkeypatch.setattr(
        api, "register_review" if phase == "review" else "confirm_reviewed_change", forbidden
    )
    raw = (original.request if phase == "review" else confirm_body(original)).model_dump(
        mode="json"
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/reviewed-policy-changes/MVP_POLICY/{original.policy_id}/{phase}", json=raw
        )
        assert response.status_code == (401 if condition == "no_cookie" else 403)


def test_readonly_original_key_not_found_remains_nonfinal_no_signed_write_assumption(
    app: FastAPI,
    original: ReviewedChangeRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def lookup(*args: Any) -> ReviewCommandLookup:
        calls.append(args)
        return ReviewCommandLookup(
            status="NOT_FOUND_NOT_FINAL", user_id=original.user_id, idempotency_key=args[2]
        )

    monkeypatch.setattr(api, "lookup_reviewed_change", lookup)
    del app.dependency_overrides[get_local_actor_principal]
    with TestClient(app) as client:
        value = client.get("/api/v1/reviewed-policy-changes/commands/by-key/commit-1")
        assert value.status_code == 200
        assert (
            value.json()["not_found_is_final"] is False
            and value.json()["current_authority"] is False
        )
        assert value.json()["response"] is None
        for suffix in ("%20", "x" * 161):
            assert (
                client.get("/api/v1/reviewed-policy-changes/commands/by-key/" + suffix).status_code
                == 422
            )
        assert (
            client.get(
                "/api/v1/reviewed-policy-changes/commands/by-key/commit-1?user_id=fake"
            ).status_code
            == 422
        )
    assert len(calls) == 1 and calls[0][1:] == (original.user_id, "commit-1", original.captured_at)


def test_review_get_preserves_original_record_and_no_permission_promotion(
    app: FastAPI,
    original: ReviewedChangeRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def read(*args: Any) -> ReviewedChangeRecord:
        calls.append(args)
        return original

    monkeypatch.setattr(api, "read_review", read)
    with TestClient(app) as client:
        result = client.get(f"/api/v1/reviewed-policy-changes/reviews/{original.review_id}")
        assert result.status_code == 200 and result.json() == original.model_dump(mode="json")
        assert result.json()["bank_authority"] is False
        assert (
            client.get(
                f"/api/v1/reviewed-policy-changes/reviews/{original.review_id}?now=fake"
            ).status_code
            == 422
        )
    assert calls[0][1:] == (original.user_id, original.review_id, original.captured_at)
