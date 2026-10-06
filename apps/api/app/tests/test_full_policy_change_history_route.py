"""Actual JSON/route parsing with explicit service doubles, not database proof."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_policy_change_history as route
from app.db.models import User
from app.domain.full_policy_change_history import FullPolicyHistoryFinancialImpact
from app.services.full_policy_change_history import FullPolicyHistoryChangeFinancialPreview
from app.services.full_policy_lifecycle import FullPreviewRequest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER, POLICY, VERSION, EPOCH = (UUID(int=value) for value in range(9900, 9904))
NOW = datetime(2026, 10, 6, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    api = FastAPI()
    api.include_router(route.router)
    api.dependency_overrides[get_session] = lambda: object()
    api.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    api.dependency_overrides[get_now] = lambda: NOW
    return TestClient(api)


def test_history_route_forwards_real_server_dependencies_and_preserves_unknown(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> FullPolicyHistoryChangeFinancialPreview:
        calls.append(args)
        return FullPolicyHistoryChangeFinancialPreview(
            user_id=USER,
            policy_id=POLICY,
            epoch_id=EPOCH,
            expected_version_id=VERSION,
            as_of=NOW,
            configuration_hash="a" * 64,
            current_configuration_hash="b" * 64,
            before_configuration={},
            after_configuration={"name": "candidate"},
            changed_fields=["name"],
            current_fact_digest="c" * 64,
            reference_snapshots=[],
            financial_impact=FullPolicyHistoryFinancialImpact(
                user_id=USER,
                epoch_id=EPOCH,
                history_proof=None,
                status="UNKNOWN",
                before=None,
                after=None,
                input_hash="d" * 64,
                reasons=["COMPLETE_CURRENT_FUTURE_DATED_HISTORY_NOT_VERIFIED"],
            ),
        )

    monkeypatch.setattr(route, "preview_full_policy_history_financial_impact", capture)
    response = client.post(
        f"/api/v1/full-policies/{POLICY}/financial-change-preview-history",
        json={
            "expected_version_id": str(VERSION),
            "configuration": {"name": "candidate"},
        },
    )
    assert response.status_code == 200 and len(calls) == 1
    assert calls[0][1:3] == (USER, POLICY) and calls[0][4] == NOW
    assert isinstance(calls[0][3], FullPreviewRequest)
    assert calls[0][3].expected_version_id == VERSION
    body = response.json()
    assert body["protocol"] == "full-policy-change-history-preview-v2"
    assert body["financial_impact"]["protocol"] == "full-policy-financial-impact-history-v2"
    assert body["financial_impact"]["status"] == "UNKNOWN"
    assert body["financial_impact"]["delta_safe_idle_cents"] is None
    assert body["grants_authority"] is False


@pytest.mark.parametrize(
    "extra",
    ["amount_cents", "user_id", "epoch_id", "clock", "history_proof", "result", "authority"],
)
def test_client_cannot_supply_identity_clock_proof_or_financial_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
) -> None:
    def forbidden(*args: Any) -> None:
        pytest.fail("Client overrides must be refused before service invocation")

    monkeypatch.setattr(route, "preview_full_policy_history_financial_impact", forbidden)
    response = client.post(
        f"/api/v1/full-policies/{POLICY}/financial-change-preview-history",
        json={
            "expected_version_id": str(VERSION),
            "configuration": {},
            extra: 1,
        },
    )
    assert response.status_code == 422


def test_extra_query_and_missing_expected_version_are_rejected(client: TestClient) -> None:
    path = f"/api/v1/full-policies/{POLICY}/financial-change-preview-history"
    assert client.post(path, json={"configuration": {}}).status_code == 422
    assert (
        client.post(
            path + "?owner=client",
            json={
                "expected_version_id": str(VERSION),
                "configuration": {},
            },
        ).status_code
        == 422
    )
