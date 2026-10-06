"""Actual routing and strict DTO rejection only; no financial evaluator or DB is mocked safe."""

from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_engine, get_session
from app.api.v1 import full_asset_execution as routes
from app.db.models import User
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    api = FastAPI()
    api.include_router(routes.router)
    api.dependency_overrides[get_demo_user] = lambda: User(id=UUID(int=1), is_simulated=True)
    api.dependency_overrides[get_engine] = lambda: Mock(spec=Engine)
    api.dependency_overrides[get_session] = lambda: Session()
    for function in (
        "preview_full_asset_execution",
        "prepare_full_asset_execution",
        "confirm_full_asset_execution",
        "execute_full_asset_execution",
        "read_full_asset_execution",
    ):
        monkeypatch.setattr(
            routes,
            function,
            lambda *args, **kwargs: pytest.fail("invalid request reached a financial service"),
        )
    return TestClient(api)


def body() -> dict[str, str]:
    return {
        "full_policy_id": str(UUID(int=1)),
        "expected_full_policy_version_id": str(UUID(int=2)),
        "mvp_asset_policy_id": str(UUID(int=3)),
        "expected_mvp_policy_version_id": str(UUID(int=4)),
        "expected_epoch_id": str(UUID(int=5)),
        "idempotency_key": "strict-public-original",
    }


def test_actual_router_rejects_client_money_and_query_facts_before_financial_calls(
    client: TestClient,
) -> None:
    for path in ("preview", "prepare"):
        assert (
            client.post(
                f"/api/v1/full-asset-executions/{path}", json=body() | {"amount_cents": "1"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/v1/full-asset-executions/{path}?now=2099-01-01", json=body()
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/api/v1/full-asset-executions/prepare",
            json=body() | {"idempotency_key": "not/readable"},
        ).status_code
        == 422
    )


def test_whole_confirm_and_execute_reject_truthy_values_extra_grants_and_missing_original_identity(
    client: TestClient,
) -> None:
    prefix = f"/api/v1/full-asset-executions/portfolios/{UUID(int=6)}"
    for path in ("confirm", "execute-next"):
        candidate = {
            "accepted": 1,
            "reviewed_portfolio_hash": "a" * 64,
            "expected_epoch_id": str(UUID(int=5)),
        }
        if path == "confirm":
            candidate["idempotency_key"] = "strict-original"
        else:
            candidate["expected_batch_number"] = 1
            candidate["expected_action_id"] = str(UUID(int=7))
        assert client.post(f"{prefix}/{path}", json=candidate).status_code == 422
        candidate["accepted"] = True
        assert (
            client.post(f"{prefix}/{path}", json=candidate | {"bank_authority": True}).status_code
            == 422
        )
        assert client.post(f"{prefix}/{path}", json={"accepted": True}).status_code == 422
    assert client.get(prefix + "?amount_cents=1").status_code == 422


@pytest.mark.parametrize("operation", ["preview", "prepare", "confirm", "execute-next"])
def test_real_json_uuid_strings_reach_the_exact_typed_service_without_financial_mock_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    calls: list[tuple[Any, ...]] = []

    def reached(*args: Any) -> Any:
        calls.append(args)
        raise LookupError("JSON_REQUEST_REACHED_SERVICE_NO_FINANCIAL_RUN")

    names = {
        "preview": "preview_full_asset_execution",
        "prepare": "prepare_full_asset_execution",
        "confirm": "confirm_full_asset_execution",
        "execute-next": "execute_full_asset_execution",
    }
    monkeypatch.setattr(routes, names[operation], reached)
    candidate: dict[str, Any] = body()
    path = f"/api/v1/full-asset-executions/{operation}"
    if operation in {"confirm", "execute-next"}:
        path = f"/api/v1/full-asset-executions/portfolios/{UUID(int=6)}/{operation}"
        candidate = {
            "accepted": True,
            "reviewed_portfolio_hash": "a" * 64,
            "expected_epoch_id": str(UUID(int=5)),
        }
        if operation == "confirm":
            candidate["idempotency_key"] = "strict-confirm-original"
        else:
            candidate["expected_batch_number"] = 1
            candidate["expected_action_id"] = str(UUID(int=7))
    with pytest.raises(LookupError, match="NO_FINANCIAL_RUN"):
        client.post(path, json=candidate)
    assert len(calls) == 1
    parsed = calls[0][-2]
    assert parsed.expected_epoch_id == UUID(int=5)
    if operation == "execute-next":
        assert parsed.expected_action_id == UUID(int=7) and parsed.expected_batch_number == 1
    assert (
        client.post(path, json=candidate | {"expected_epoch_id": "not-a-uuid"}).status_code == 422
    )


@pytest.mark.parametrize("number", [0, 5, True, "1"])
def test_execute_fixed_batch_number_is_strict(client: TestClient, number: object) -> None:
    assert (
        client.post(
            f"/api/v1/full-asset-executions/portfolios/{UUID(int=6)}/execute-next",
            json={
                "accepted": True,
                "reviewed_portfolio_hash": "a" * 64,
                "expected_epoch_id": str(UUID(int=5)),
                "expected_batch_number": number,
                "expected_action_id": str(UUID(int=7)),
            },
        ).status_code
        == 422
    )
