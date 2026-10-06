"""Real router DTO forwarding with service doubles; no database or finance evidence."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1.full_policies import router
from app.db.models import User
from app.services import full_policy_lifecycle as full
from app.tests.test_full_policy_lifecycle import confirm_body
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER = UUID(int=300)
POLICY = UUID(int=301)
VERSION = UUID(int=302)
NOW = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def result() -> full.FullLifecycleResult:
    return full.FullLifecycleResult(
        policy_id=POLICY,
        epoch_id=UUID(int=303),
        version_id=VERSION,
        command_id=UUID(int=304),
        status="ACTIVE",
        configuration_hash="a" * 64,
    )


def test_actual_confirm_router_forwards_server_owner_clock_and_strict_request(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> full.FullLifecycleResult:
        calls.append(args)
        return result()

    monkeypatch.setattr(full, "confirm_full_policy", capture)
    response = client.post("/api/v1/full-policies/confirm", json=confirm_body())
    assert response.status_code == 200
    assert calls[0][1] == USER and calls[0][3] == NOW
    assert isinstance(calls[0][2], full.FullCreateRequest)
    assert response.json()["bank_authority"] is False
    assert response.json()["receipt_is_current_authority"] is False


@pytest.mark.parametrize("field", ["user_id", "now", "authority", "effect", "result"])
def test_unknown_financial_body_never_calls_service(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    def forbidden(*args: Any) -> None:
        pytest.fail("Invalid body must not reach service")

    monkeypatch.setattr(full, "confirm_full_policy", forbidden)
    assert (
        client.post(
            "/api/v1/full-policies/confirm", json={**confirm_body(), field: "injected"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("accepted", [False, 1, "true"])
def test_boolean_confirmation_is_not_coerced_in_actual_router(
    client: TestClient, accepted: Any
) -> None:
    assert (
        client.post(
            "/api/v1/full-policies/confirm", json={**confirm_body(), "accepted": accepted}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("suffix", ["", "/versions", "/commands"])
def test_get_rejects_unknown_query_owner_or_clock(client: TestClient, suffix: str) -> None:
    assert client.get(f"/api/v1/full-policies/{POLICY}{suffix}?owner={USER}").status_code == 422


@pytest.mark.parametrize("suffix", ["change", "resume", "suspend", "revoke", "change-preview"])
def test_mutations_require_current_version_and_reject_grant_fields(
    client: TestClient, suffix: str
) -> None:
    assert client.post(f"/api/v1/full-policies/{POLICY}/{suffix}", json={}).status_code == 422
    assert (
        client.post(
            f"/api/v1/full-policies/{POLICY}/{suffix}",
            json={"expected_version_id": str(VERSION), "grant": True},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(("suffix", "keyword"), [("change", False), ("resume", True)])
def test_change_and_resume_use_explicit_request_and_original_server_clock(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    keyword: bool,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def capture(*args: Any, **kwargs: Any) -> full.FullLifecycleResult:
        calls.append((args, kwargs))
        return result()

    monkeypatch.setattr(full, "change_full_policy", capture)
    body = deepcopy(confirm_body())
    body.pop("template_name")
    body["expected_version_id"] = str(VERSION)
    if suffix == "resume":
        body.pop("configuration")
    response = client.post(f"/api/v1/full-policies/{POLICY}/{suffix}", json=body)
    assert response.status_code == 200
    assert calls[0][0][1:3] == (USER, POLICY)
    assert calls[0][0][4] == NOW
    assert calls[0][1].get("resume", False) is keyword


@pytest.mark.parametrize(("suffix", "keyword"), [("suspend", False), ("revoke", True)])
def test_state_command_forwards_exact_current_version_and_kind(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    keyword: bool,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def capture(*args: Any, **kwargs: Any) -> full.FullLifecycleResult:
        calls.append((args, kwargs))
        return result()

    monkeypatch.setattr(full, "stop_full_policy", capture)
    response = client.post(
        f"/api/v1/full-policies/{POLICY}/{suffix}",
        json={
            "expected_version_id": str(VERSION),
            "reason": "停止声明",
            "idempotency_key": "stop-1",
        },
    )
    assert response.status_code == 200
    assert calls[0][0][1:3] == (USER, POLICY) and calls[0][0][4] == NOW
    assert calls[0][0][3].expected_version_id == VERSION
    assert calls[0][1].get("revoke", False) is keyword


def test_refresh_body_cannot_supply_time_or_owner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> full.FullRefreshResult:
        calls.append(args)
        return full.FullRefreshResult(results=[])

    monkeypatch.setattr(full, "refresh_full_policy_time", capture)
    assert (
        client.post("/api/v1/full-policies/time-refresh", json={"now": NOW.isoformat()}).status_code
        == 422
    )
    assert not calls
    assert client.post("/api/v1/full-policies/time-refresh", json={}).status_code == 200
    assert calls[0][1:] == (USER, NOW)


def test_command_lookup_encoded_key_preserves_exact_key_and_unknown_outcome(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []
    key = "user/create:规则/原键 %?+"

    def capture(*args: Any) -> full.FullCommandLookup:
        calls.append(args)
        return full.FullCommandLookup(
            status="NOT_FOUND",
            idempotency_key=args[2],
            original_request=None,
            request_hash=None,
            command=None,
        )

    monkeypatch.setattr(full, "lookup_full_policy_command", capture)
    response = client.get("/api/v1/full-policies/commands/by-key/" + quote(key, safe=""))
    assert response.status_code == 200
    assert calls[0][1:] == (USER, key, NOW)
    assert response.json()["status"] == "NOT_FOUND"
    assert response.json()["not_found_is_final"] is False
    assert response.json()["bank_authority"] is False
    assert response.json()["receipt_is_current_authority"] is False
    assert response.json()["original_request"] is None and response.json()["command"] is None


@pytest.mark.parametrize("key", [" " * 2, "x" * 161])
def test_command_lookup_rejects_invalid_key_before_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    def forbidden(*args: Any) -> None:
        pytest.fail("Invalid key must not reach service")

    monkeypatch.setattr(full, "lookup_full_policy_command", forbidden)
    assert (
        client.get("/api/v1/full-policies/commands/by-key/" + quote(key, safe="")).status_code
        == 422
    )


def test_command_lookup_forbids_owner_and_clock_query(client: TestClient) -> None:
    assert (
        client.get(f"/api/v1/full-policies/commands/by-key/create?owner={USER}").status_code == 422
    )
