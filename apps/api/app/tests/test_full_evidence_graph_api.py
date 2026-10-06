"""The real new HTTP contract with isolated synthetic dependency fixtures, no DB."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from app.api import dependencies
from app.api.v1 import full_evidence_graph as api
from app.db.models import User
from app.domain.full_evidence_graph import FullEvidenceGraph
from app.tests.test_full_evidence_graph import USER, graph, original
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[dependencies.get_session] = lambda: cast(Session, object())
    app.dependency_overrides[dependencies.get_demo_user] = lambda: cast(
        User, SimpleNamespace(id=USER)
    )
    app.dependency_overrides[dependencies.get_now] = lambda: datetime(2026, 10, 6, tzinfo=UTC)

    def view(*args: Any) -> FullEvidenceGraph:
        assert args[1] == USER and args[2] == "ACCOUNT" and args[3] == ROOT.id
        return graph([ROOT], ROOT)

    monkeypatch.setattr(api, "full_evidence_graph", view)
    return TestClient(app)


ROOT = original("ACCOUNT", balance_cents=177013)
PATH = "/api/v1/evidence/full-graph/ACCOUNT/" + str(ROOT.id)


def test_actual_json_response_has_navigation_and_no_authority(client: TestClient) -> None:
    response = client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.json()["protocol"] == "persisted-full-evidence-graph-v2"
    assert not response.json()["financial_success_inferred"]
    assert response.json()["nodes"][0]["original"]["balance_cents"] == 177013


@pytest.mark.parametrize(
    "query",
    [
        "?actor=USER",
        "?success=true",
        "?amount_cents=1",
        "?known_at=2026-10-06T00%3A00%3A00Z&known_at=2026-10-06T00%3A00%3A00Z",
    ],
)
def test_unknown_financial_or_duplicate_query_never_reaches_reader(
    client: TestClient, query: str
) -> None:
    assert client.get(PATH + query).status_code == 422


def test_new_path_validates_registered_kind_and_real_uuid(client: TestClient) -> None:
    assert client.get("/api/v1/evidence/full-graph/UNKNOWN/" + str(uuid4())).status_code == 422
    assert client.get("/api/v1/evidence/full-graph/ACCOUNT/true").status_code == 422
