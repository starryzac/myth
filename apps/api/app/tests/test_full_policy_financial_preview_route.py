"""Production financial-preview routing contracts with doubles, not bank evidence."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_policies
from app.db.models import User
from app.domain.full_policy_change_impact import FullPolicyFinancialImpact
from app.services.full_policy_change_impact import FullPolicyChangeFinancialPreview
from app.services.full_policy_lifecycle import FullPreviewRequest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER = UUID(int=780)
POLICY = UUID(int=781)
VERSION = UUID(int=782)
NOW = datetime(2026, 10, 6, tzinfo=UTC)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(full_policies.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_financial_preview_forwards_original_owner_clock_and_candidate_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> FullPolicyChangeFinancialPreview:
        calls.append(args)
        return FullPolicyChangeFinancialPreview(
            policy_id=POLICY,
            epoch_id=UUID(int=783),
            expected_version_id=VERSION,
            as_of=NOW,
            configuration_hash="a" * 64,
            current_configuration_hash="b" * 64,
            before_configuration={},
            after_configuration={"name": "candidate"},
            changed_fields=["name"],
            current_fact_digest="c" * 64,
            reference_snapshots=[],
            financial_impact=FullPolicyFinancialImpact(
                status="UNKNOWN", before=None, after=None, input_hash="d" * 64
            ),
        )

    monkeypatch.setattr(full_policies, "preview_full_policy_financial_impact", capture)
    result = client.post(
        f"/api/v1/full-policies/{POLICY}/financial-change-preview",
        json={"expected_version_id": str(VERSION), "configuration": {"name": "candidate"}},
    )
    assert result.status_code == 200
    assert len(calls) == 1
    assert calls[0][1:3] == (USER, POLICY) and calls[0][4] == NOW
    assert isinstance(calls[0][3], FullPreviewRequest)
    value = result.json()
    assert value["hypothetical"] is True and value["grants_authority"] is False
    assert value["financial_impact"]["status"] == "UNKNOWN"
    assert value["financial_impact"]["delta_safe_idle_cents"] is None


@pytest.mark.parametrize("field", ["amount_cents", "user_id", "now", "result", "authority"])
def test_financial_preview_rejects_financial_and_authority_overrides_before_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    def forbidden(*args: Any) -> None:
        pytest.fail("An override must not reach the financial preview")

    monkeypatch.setattr(full_policies, "preview_full_policy_financial_impact", forbidden)
    assert (
        client.post(
            f"/api/v1/full-policies/{POLICY}/financial-change-preview",
            json={"expected_version_id": str(VERSION), "configuration": {}, field: 1},
        ).status_code
        == 422
    )


def test_financial_preview_rejects_query_owner_and_missing_current_version(
    client: TestClient,
) -> None:
    path = f"/api/v1/full-policies/{POLICY}/financial-change-preview"
    assert client.post(path, json={"configuration": {}}).status_code == 422
    assert (
        client.post(
            path + "?owner=client",
            json={"expected_version_id": str(VERSION), "configuration": {}},
        ).status_code
        == 422
    )
