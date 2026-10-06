"""Actual routing and strict query contract with doubles, not PostgreSQL proof."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_joint_goal_planning as api
from app.db.models import User
from app.services.full_joint_goal_planning import full_joint_goal_planning
from app.tests.test_full_joint_goal_planning_service import setup
from app.tests.test_full_projection import NOW, USER
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_actual_route_only_uses_fixed_owner_server_clock_and_rejects_all_extra_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _, _, _, _ = setup(monkeypatch)
    expected = full_joint_goal_planning(session, USER, NOW)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    calls: list[tuple[Any, ...]] = []

    def read(*args: Any) -> Any:
        calls.append(args)
        return expected

    monkeypatch.setattr(api, "full_joint_goal_planning", read)
    with TestClient(app) as client:
        url = "/api/v1/planning/full-current-goal-allocation"
        result = client.get(url)
        assert result.status_code == 200 and result.json() == expected.model_dump(mode="json")
        for key in (
            "now",
            "user_id",
            "cash_cents",
            "future_income_cents",
            "source_refs",
            "full_protection",
            "authority",
            "horizon_days",
            "release_goal_minimum",
        ):
            assert client.get(url, params={key: "fake"}).status_code == 422
        assert client.post(url, json={"cash_cents": 999999}).status_code == 405
    assert calls == [(session, USER, NOW)]
