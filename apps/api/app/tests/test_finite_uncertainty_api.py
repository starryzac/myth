"""Public API source/field boundaries with doubles; not real financial evidence."""

from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import finite_uncertainty as api
from app.db.models import User
from app.domain.finite_uncertainty import unknown_planning
from app.services.dashboard_types import DashboardAuditCard
from app.services.finite_uncertainty import FinitePlanningResponse
from app.tests.test_execution_domain import NOW, USER
from app.tests.test_finite_uncertainty import amount_variable, intent, original_engine
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


def body() -> dict[str, Any]:
    return {
        "base_action_id": str(UUID(int=100)),
        "variables": [amount_variable().model_dump(mode="json")],
    }


def response() -> FinitePlanningResponse:
    outcome = original_engine(intent())
    assert outcome.decision is not None
    return FinitePlanningResponse(
        user_id=USER,
        as_of=NOW,
        base_action_id=UUID(int=100),
        original_run_id=UUID(int=101),
        original_trace_hash="a" * 64,
        request_hash="b" * 64,
        current_source_context_hash="c" * 64,
        base_decision=outcome.decision,
        audit=DashboardAuditCard(
            epoch_id=UUID(int=999), status="VALID", complete=True, anchored_run_statuses={}
        ),
        sources=[],
        declarations=[],
        result=unknown_planning([amount_variable()], ["MISSING_ORIGINAL_IN_TEST_DOUBLE"]),
    )


def test_router_preserves_actual_owner_clock_and_only_declared_planning_variables(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def capture(*args: Any) -> FinitePlanningResponse:
        calls.append(args)
        return response()

    monkeypatch.setattr(api, "analyze_finite_planning", capture)
    result = client.post("/api/v1/finite-planning/analyze", json=body())
    assert result.status_code == 200
    assert calls[0][1] == USER and calls[0][3] == NOW
    assert calls[0][2].variables[0].source == "USER_REQUEST"
    assert result.json()["result"]["execution_eligible"] is False
    assert result.json()["result"]["should_ask"] is None


@pytest.mark.parametrize(
    "extra",
    [
        "user_id",
        "now",
        "facts",
        "worlds",
        "balance_cents",
        "authority",
        "confirmation",
        "result",
        "probability",
    ],
)
def test_no_public_bank_context_authority_confirmation_clock_or_world_result(
    client: TestClient, extra: str
) -> None:
    assert (
        client.post("/api/v1/finite-planning/analyze", json={**body(), extra: True}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "field",
    ["BANK_BALANCE", "FUTURE_INCOME", "POLICY_AUTHORITY", "EVIDENCE_LEVEL", "CONFIRMED_LOSS"],
)
def test_non_user_owned_financial_fact_fields_refused(client: TestClient, field: str) -> None:
    raw = body()
    raw["variables"][0]["field"] = field
    assert client.post("/api/v1/finite-planning/analyze", json=raw).status_code == 422


def test_fake_bank_grade_unknown_choice_fields_and_query_refused(client: TestClient) -> None:
    raw = body()
    raw["variables"][0]["source"] = "BANK_CONFIRMED"
    assert client.post("/api/v1/finite-planning/analyze", json=raw).status_code == 422
    raw = body()
    raw["variables"][0]["choices"][0]["value"]["grant"] = True
    assert client.post("/api/v1/finite-planning/analyze", json=raw).status_code == 422
    assert (
        client.post("/api/v1/finite-planning/analyze?execute=true", json=body()).status_code == 422
    )


def test_public_json_account_choices_and_registered_evidence_remain_exact_typed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = body()
    raw["variables"].append(
        {
            "variable_id": "destination",
            "field": "TRANSFER_DESTINATION",
            "choices": [
                {"key": "a", "value": {"kind": "account", "account_id": str(UUID(int=401))}},
                {"key": "b", "value": {"kind": "account", "account_id": str(UUID(int=402))}},
            ],
            "source": "REGISTERED_EVIDENCE",
            "evidence_id": str(UUID(int=403)),
        }
    )
    calls: list[Any] = []

    def capture(*args: Any) -> FinitePlanningResponse:
        calls.append(args)
        return response()

    monkeypatch.setattr(api, "analyze_finite_planning", capture)
    result = client.post("/api/v1/finite-planning/analyze", json=raw)
    assert result.status_code == 200
    variable = calls[0][2].variables[1]
    assert variable.evidence_id == UUID(int=403)
    assert variable.choices[0].value.account_id == UUID(int=401)


@pytest.mark.parametrize("amount", ["100", 100.5, True, None])
def test_public_json_normalization_does_not_coerce_money_or_enable_extra_fields(
    client: TestClient, amount: Any
) -> None:
    raw = body()
    raw["variables"][0]["choices"][0]["value"]["amount_cents"] = amount
    assert client.post("/api/v1/finite-planning/analyze", json=raw).status_code == 422
