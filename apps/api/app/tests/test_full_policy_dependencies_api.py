"""Strict actual HTTP route contracts with source doubles, not PG results."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_policy_dependencies as api
from app.db.models import User
from app.domain.full_policy_dependencies import review_dependencies
from app.tests.test_full_policy_dependencies import FIRST, NOW, USER, fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_read_forwards_actual_owner_and_clock_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def read(*args: Any) -> Any:
        calls.append(args)
        return review_dependencies(fixture(cycle=True))

    monkeypatch.setattr(api, "read_full_policy_dependencies", read)
    response = client.get(f"/api/v1/full-policy-dependencies/{FIRST}")
    assert response.status_code == 200, response.text
    assert calls[0][1:] == (USER, FIRST, NOW) and len(calls) == 1
    value = response.json()
    assert value["review_required"] and not value["bank_authority"]
    assert not value["cyclic_components"][0]["financial_infeasibility_proven"]


@pytest.mark.parametrize("query", ["actor=USER", "amount=100", "now=2027-01-01", "result=SUCCESS"])
def test_query_overrides_never_reach_reader(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    monkeypatch.setattr(
        api, "read_full_policy_dependencies", lambda *_: pytest.fail("override reached source")
    )
    assert client.get(f"/api/v1/full-policy-dependencies/{FIRST}?{query}").status_code == 422


def test_path_uuid_and_mutations_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/full-policy-dependencies/not-an-id").status_code == 422
    assert (
        client.post(
            f"/api/v1/full-policy-dependencies/{FIRST}", json={"accepted": True}
        ).status_code
        == 405
    )
