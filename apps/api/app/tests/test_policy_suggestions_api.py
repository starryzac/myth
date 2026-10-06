"""Strict public query/owner/server-clock checks with doubles, not actual PG evidence."""

from typing import Any

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import policy_suggestions as api
from app.domain.pattern_suggestions import PeriodicParameters, SeasonalParameters
from app.services.policy_suggestions import PeriodicSuggestions, SeasonalSuggestions
from app.tests.test_policy_suggestions_service import NOW, USER, MemorySession
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_demo_user] = lambda: MemorySession().user
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


def test_gets_use_fixed_actual_user_and_clock_and_only_tighten_parameters(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def periodic(*args: Any) -> PeriodicSuggestions:
        calls.append(args)
        return api_service_periodic(args[3])

    def seasonal(*args: Any) -> SeasonalSuggestions:
        calls.append(args)
        from app.services.policy_suggestions import seasonal_suggestions

        return seasonal_suggestions(MemorySession().as_session(), USER, NOW, args[3])

    monkeypatch.setattr(api, "periodic_suggestions", periodic)
    monkeypatch.setattr(api, "seasonal_suggestions", seasonal)
    response = client.get("/api/v1/policy-suggestions/periodic?maximum_cv_bps=0&minimum_cycles=3")
    assert response.status_code == 200
    assert calls[-1][1:3] == (USER, NOW)
    assert calls[-1][3].maximum_cv_bps == 0 and calls[-1][3].minimum_cycles == 3
    response = client.get(
        "/api/v1/policy-suggestions/seasonal",
        params={
            "window_id": "CN-2026-NATIONAL_DAY",
            "quantile_bps": "10000",
            "essential_categories": "food",
            "adjustment_cap_cents": "1000",
        },
    )
    assert response.status_code == 200 and calls[-1][1:3] == (USER, NOW)
    assert calls[-1][3].quantile_bps == 10000
    assert response.json()["suggestion"]["required_adjustment_cents"] is None
    assert response.json()["hard_protection_changed"] is False


def api_service_periodic(parameters: PeriodicParameters) -> PeriodicSuggestions:
    from app.services.policy_suggestions import periodic_suggestions

    return periodic_suggestions(MemorySession().as_session(), USER, NOW, parameters)


@pytest.mark.parametrize("path", ["periodic", "seasonal"])
@pytest.mark.parametrize(
    "key", ["now", "user_id", "facts", "coverage", "bank_balance", "amount_cents", "auto_confirm"]
)
def test_public_cannot_supply_financial_originals_permission_or_clock(
    client: TestClient,
    path: str,
    key: str,
) -> None:
    params = {key: "true"}
    if path == "seasonal":
        params["window_id"] = "CN-2026-SPRING_FESTIVAL"
    assert client.get(f"/api/v1/policy-suggestions/{path}", params=params).status_code == 422


@pytest.mark.parametrize(
    "key,value",
    [
        ("minimum_cycles", "1"),
        ("maximum_cv_bps", "1001"),
        ("maximum_day_spread", "3"),
        ("lookback_days", "57"),
    ],
)
def test_periodic_query_cannot_loosen_finite_rule(client: TestClient, key: str, value: str) -> None:
    assert client.get("/api/v1/policy-suggestions/periodic", params={key: value}).status_code == 422


@pytest.mark.parametrize(
    "key,value",
    [
        ("minimum_historical_windows", "1"),
        ("quantile_bps", "7999"),
        ("essential_categories", "rent"),
        ("adjustment_cap_cents", "500001"),
        ("lookback_days", "1097"),
    ],
)
def test_seasonal_query_cannot_loosen_window_source_rule(
    client: TestClient,
    key: str,
    value: str,
) -> None:
    assert (
        client.get(
            "/api/v1/policy-suggestions/seasonal",
            params={
                "window_id": "CN-2026-SPRING_FESTIVAL",
                key: value,
            },
        ).status_code
        == 422
    )


def test_duplicate_categories_and_missing_window_are_422(client: TestClient) -> None:
    assert client.get("/api/v1/policy-suggestions/seasonal").status_code == 422
    assert (
        client.get(
            "/api/v1/policy-suggestions/seasonal",
            params=[
                ("window_id", "CN-2026-SPRING_FESTIVAL"),
                ("essential_categories", "food"),
                ("essential_categories", "food"),
            ],
        ).status_code
        == 422
    )


def test_unregistered_public_year_is_explicit_unknown_not_invented_calendar(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def capture(*args: Any) -> SeasonalSuggestions:
        from app.services.policy_suggestions import seasonal_suggestions

        return seasonal_suggestions(MemorySession().as_session(), USER, NOW, args[3])

    monkeypatch.setattr(api, "seasonal_suggestions", capture)
    response = client.get("/api/v1/policy-suggestions/seasonal?window_id=CN-2027-SPRING_FESTIVAL")
    assert response.status_code == 200
    assert response.json()["suggestion"]["status"] == "UNKNOWN"
    assert response.json()["suggestion"]["target"] is None
    assert response.json()["suggestion"]["candidate_configuration"] is None
    assert SeasonalParameters(window_id="CN-2027-SPRING_FESTIVAL").window_id
