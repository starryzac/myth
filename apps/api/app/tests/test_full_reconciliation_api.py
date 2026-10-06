"""Strict actual route contract with synthetic doubles, no financial runtime proof."""

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_reconciliation as api
from app.db.models import User
from app.services.full_reconciliation import full_reconciliation
from app.tests.test_full_reconciliation import NOW, USER
from app.tests.test_full_reconciliation_service import setup
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_actual_api_is_readonly_fixed_owner_and_rejects_fake_facts_authority_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = setup(monkeypatch)
    expected = full_reconciliation(session, USER, NOW)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    with TestClient(app) as client:
        result = client.get("/api/v1/reconciliation/current")
        assert result.status_code == 200 and result.json() == expected.model_dump(mode="json")
        for key in (
            "user_id",
            "now",
            "bank_status",
            "bank_balance_cents",
            "receipt",
            "economic_verified",
            "repair",
            "permission",
            "action_id",
        ):
            assert (
                client.get("/api/v1/reconciliation/current", params={key: "fake"}).status_code
                == 422
            )
        assert (
            client.post("/api/v1/reconciliation/current", json={"repair": True}).status_code == 405
        )
