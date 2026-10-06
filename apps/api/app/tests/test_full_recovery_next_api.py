"""Actual JSON/identity routing with synthetic sentinels; no database/bank proof."""

from typing import Any

import pytest
from app.api.v1 import full_recovery_next as api
from app.api.v1.local_actor_sessions import get_local_actor_principal
from app.tests.test_full_recovery_execution_api import setup
from app.tests.test_full_recovery_next import request
from app.tests.test_recovery import NOW, USER
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.mark.parametrize("kind", ["preview", "prepare"])
def test_actual_valid_json_delegates_strict_closed_request_and_financial_extras_fail(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    app, session, engine, principal = setup()
    app.include_router(api.router)
    calls: list[tuple[Any, ...]] = []

    def actual(*args: Any) -> None:
        calls.append(args)
        raise HTTPException(409, "SYNTHETIC_DELEGATION_ONLY_NO_BANK_RESULT")

    monkeypatch.setattr(api, f"{kind}_next_whole_recovery", actual)
    body = request().model_dump(mode="json")
    path = f"/api/v1/full-recovery-next-actions/{kind}"
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 409
        for field in (
            "position_id",
            "amount_cents",
            "quote",
            "now",
            "deadline_at",
            "role",
            "receipt",
        ):
            assert client.post(path, json={**body, field: 1}).status_code == 422
            assert client.post(path, json=body, params={field: "fake"}).status_code == 422
        for field in ("policy_id", "expected_epoch_id", "expected_version_id"):
            assert client.post(path, json={**body, field: "bad"}).status_code == 422
    assert calls == (
        [(session, USER, request(), NOW)]
        if kind == "preview"
        else [(engine, USER, request(), principal, NOW)]
    )


def test_missing_or_non_user_session_cannot_call_reader_or_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _, _, principal = setup()
    app.include_router(api.router)

    def never(*args: Any) -> None:
        pytest.fail("identity gate was bypassed")

    monkeypatch.setattr(api, "preview_next_whole_recovery", never)
    monkeypatch.setattr(api, "prepare_next_whole_recovery", never)
    monkeypatch.setattr(api, "lookup_next_whole_recovery", never)
    with TestClient(app) as client:
        del app.dependency_overrides[get_local_actor_principal]
        assert (
            client.post(
                "/api/v1/full-recovery-next-actions/preview", json=request().model_dump(mode="json")
            ).status_code
            == 401
        )
        assert client.get("/api/v1/full-recovery-next-actions/by-key/root").status_code == 401
        app.dependency_overrides[get_local_actor_principal] = lambda: principal.model_copy(
            update={"role": "AGENT"}
        )
        assert (
            client.post(
                "/api/v1/full-recovery-next-actions/prepare", json=request().model_dump(mode="json")
            ).status_code
            == 403
        )
