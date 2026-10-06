"""Real FastAPI JSON boundary with service doubles, not PostgreSQL evidence."""

from copy import deepcopy
from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_goal_conflicts as api
from app.db.models import User
from app.services.full_goal_conflicts import preview_full_goal_repairs, read_full_goal_conflicts
from app.tests.test_full_goal_conflicts_service import command, current
from app.tests.test_full_projection import NOW, USER
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_actual_json_uuids_strict_ranges_and_extra_fields_reject_before_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, planning, _, _ = current(monkeypatch)
    expected = read_full_goal_conflicts(session, USER, NOW)
    body = command(expected, planning)
    preview = preview_full_goal_repairs(session, USER, body, NOW)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    reads: list[tuple[Any, ...]] = []
    previews: list[tuple[Any, ...]] = []

    def read(*args: Any) -> Any:
        reads.append(args)
        return expected

    def proposed(*args: Any) -> Any:
        previews.append(args)
        return preview

    monkeypatch.setattr(api, "read_full_goal_conflicts", read)
    monkeypatch.setattr(api, "preview_full_goal_repairs", proposed)
    raw = body.model_dump(mode="json")
    with TestClient(app) as client:
        result = client.get("/api/v1/planning/full-goal-conflicts")
        assert result.status_code == 200 and result.json() == expected.model_dump(mode="json")
        result = client.post("/api/v1/planning/full-goal-repairs/preview", json=raw)
        assert result.status_code == 200 and result.json() == preview.model_dump(mode="json")
        bad = []
        for value in (True, 6.0, "6", -1, 2**63):
            changed = deepcopy(raw)
            changed["adjustments"][0]["minimum_new_monthly_max_cents"] = value
            bad.append(changed)
        for field in ("cash_cents", "source_refs", "accepted", "permission", "result", "now"):
            bad.append(raw | {field: "fake"})
            changed = deepcopy(raw)
            changed["adjustments"][0][field] = "fake"
            bad.append(changed)
            assert (
                client.get(
                    "/api/v1/planning/full-goal-conflicts", params={field: "fake"}
                ).status_code
                == 422
            )
        bad.extend(
            [
                raw | {"adjustments": []},
                raw | {"adjustments": raw["adjustments"] * 2},
                raw | {"expected_epoch_id": True},
                raw | {"reviewed_state_hash": "fake"},
            ]
        )
        for changed in bad:
            assert (
                client.post("/api/v1/planning/full-goal-repairs/preview", json=changed).status_code
                == 422
            )
        assert (
            client.post("/api/v1/planning/full-goal-repairs/preview?now=fake", json=raw).status_code
            == 422
        )
    assert reads == [(session, USER, NOW)]
    assert previews == [(session, USER, body, NOW)]
