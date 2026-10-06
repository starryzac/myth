"""Actual FastAPI JSON parsing/forwarding with explicit synthetic service doubles."""

from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_policy_change_multi as routes
from app.db.models import User
from app.domain.full_policy_change_multi import MultiTemplateFinancialImpact
from app.services.full_policy_change_multi import (
    MultiTemplatePreviewRequest,
    MultiTemplatePreviewResponse,
)
from app.tests.boundary_display_cases import NOW
from fastapi import FastAPI
from fastapi.testclient import TestClient

USER, EPOCH, POLICY, VERSION = (UUID(int=n) for n in (90, 91, 92, 93))


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER, is_simulated=True)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def body() -> dict[str, Any]:
    return {
        "expected_version_id": str(VERSION),
        "expected_epoch_id": str(EPOCH),
        "configuration": {"type": "emergency_buffer", "amount_cents": 100},
    }


def test_actual_json_uuid_body_reaches_server_owner_clock_and_original_request(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def capture(*args: Any) -> MultiTemplatePreviewResponse:
        calls.append(args)
        return MultiTemplatePreviewResponse(
            user_id=USER,
            epoch_id=EPOCH,
            source_kind="MVP_POLICY",
            policy_id=POLICY,
            expected_version_id=VERSION,
            template_name="EmergencyBufferPolicy",
            as_of=NOW,
            current_configuration_hash="a" * 64,
            candidate_configuration_hash="b" * 64,
            before_configuration={},
            after_configuration=body()["configuration"],
            changed_fields=["amount_cents"],
            current_fact_digest="c" * 64,
            source_counts={},
            source_evidence_ids=[],
            source_originals={"purpose": "TOOL_ONLY_SYNTHETIC"},
            financial_impact=MultiTemplateFinancialImpact(
                status="UNKNOWN",
                scope="UNSUPPORTED",
                template_name="EmergencyBufferPolicy",
                before=None,
                after=None,
                input_hash="d" * 64,
            ),
            original_action_ids=[],
            original_position_ids=[],
        )

    monkeypatch.setattr(routes, "preview_multi_template_financial_change", capture)
    response = client.post(f"/api/v1/policy-financial-previews/MVP_POLICY/{POLICY}", json=body())
    assert response.status_code == 200 and len(calls) == 1
    args = calls[0]
    assert args[1:4] == (USER, "MVP_POLICY", POLICY) and args[5] == NOW
    assert isinstance(args[4], MultiTemplatePreviewRequest)
    assert args[4].expected_version_id == VERSION and args[4].expected_epoch_id == EPOCH
    assert args[4].configuration == body()["configuration"]
    assert response.json()["source_originals"]["purpose"] == "TOOL_ONLY_SYNTHETIC"
    assert response.json()["financial_impact"]["status"] == "UNKNOWN"
    assert response.json()["grants_authority"] is response.json()["writes_policy_or_bank"] is False


@pytest.mark.parametrize(
    "field", ["user_id", "now", "amount_cents", "result", "receipt", "authority", "grant"]
)
def test_unknown_root_fields_cannot_reach_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    def forbidden(*args: Any) -> None:
        pytest.fail("Rejected request must not reach the preview")

    monkeypatch.setattr(routes, "preview_multi_template_financial_change", forbidden)
    assert (
        client.post(
            f"/api/v1/policy-financial-previews/MVP_POLICY/{POLICY}", json={**body(), field: True}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "path,saved",
    [
        (f"MVP_POLICY/{POLICY}?owner=x", body()),
        (f"BANK_AUTHORITY/{POLICY}", body()),
        (f"MVP_POLICY/{POLICY}", {**body(), "expected_epoch_id": True}),
        (f"MVP_POLICY/{POLICY}", {**body(), "expected_version_id": "bad"}),
    ],
)
def test_query_source_and_identity_boundaries(
    client: TestClient, path: str, saved: dict[str, Any]
) -> None:
    assert client.post("/api/v1/policy-financial-previews/" + path, json=saved).status_code == 422
