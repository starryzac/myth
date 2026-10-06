"""Strict public API contracts with doubles; no actual financial/PG evidence."""

from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import boundary_difference as api
from app.db.models import User
from app.domain.boundary_difference import BoundaryDifference, unknown_difference
from app.tests.test_boundary import NOW
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER = UUID(int=44)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def body() -> dict[str, Any]:
    return {"before_run_id": str(UUID(int=1)), "after_run_id": str(UUID(int=2))}


def test_only_original_ids_with_server_owner_clock_enter_readonly_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> BoundaryDifference:
        calls.append(args)
        return unknown_difference(UUID(int=1), UUID(int=2), ["ORIGINAL_INPUT_MISSING"])

    monkeypatch.setattr(api, "compare_boundary_runs", capture)
    result = client.post("/api/v1/boundary-differences/compare", json=body())
    assert result.status_code == 200 and result.json()["delta_cents"] is None
    assert result.json()["authority_granted"] is False
    assert calls[0][1] == USER and calls[0][3] == NOW
    assert calls[0][2].before_run_id == UUID(int=1)


@pytest.mark.parametrize(
    "extra",
    ["user_id", "now", "before_snapshot", "facts", "grant", "delta_cents", "cause", "result"],
)
def test_client_cannot_supply_money_snapshot_permission_or_cause(
    client: TestClient, extra: str
) -> None:
    assert (
        client.post(
            "/api/v1/boundary-differences/compare", json={**body(), extra: True}
        ).status_code
        == 422
    )


def test_unknown_selector_invalid_uuid_and_query_rejected(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/boundary-differences/compare", json={**body(), "boundary_field": "made_up"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/boundary-differences/compare", json={**body(), "before_run_id": "bad"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/boundary-differences/compare?grant=true", json=body()).status_code
        == 422
    )
