"""Public annual read contract rejects client financial and principal overrides without a DB."""

from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import planning
from app.db.models import User
from app.services.full_projection import compute_annual_projection, project_verified_context
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_projection import NOW, USER, audit, context
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_annual_http_contract_accepts_no_client_money_identity_clock_or_horizon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FastAPI()
    api.include_router(planning.router)
    placeholder = cast(Session, object())

    def session_dependency() -> Iterator[Session]:
        yield placeholder

    api.dependency_overrides[get_session] = session_dependency
    api.dependency_overrides[get_demo_user] = lambda: User(id=USER)
    api.dependency_overrides[get_now] = lambda: NOW
    expected = project_verified_context(context(), audit())
    calls: list[tuple[Session, object, object]] = []

    def read(session: Session, user_id: object, now: object) -> object:
        calls.append((session, user_id, now))
        return expected

    monkeypatch.setattr(planning, "compute_annual_projection", read)
    with TestClient(api) as client:
        response = client.get("/api/v1/planning/annual")
        assert response.status_code == 200
        assert response.json() == expected.model_dump(mode="json")
        for key, value in (
            ("future_income_cents", "9999999"),
            ("expected_salary_cents", "9999999"),
            ("cash_cents", "9999999"),
            ("authority", "AUTO_EXECUTE"),
            ("user_id", str(USER)),
            ("as_of", "2099-01-01"),
            ("horizon_days", "90"),
            ("principal_available_at", "2026-10-05"),
        ):
            assert client.get("/api/v1/planning/annual", params={key: value}).status_code == 422
    assert calls == [(placeholder, USER, NOW)]


@pytest.mark.parametrize("violation", ["new", "dirty", "deleted", "isolation", "read_only"])
def test_service_refuses_any_non_readonly_or_mutating_transaction(violation: str) -> None:
    session = MagicMock(spec=Session)
    session.new = set()
    session.dirty = set()
    session.deleted = set()
    session.connection.return_value.get_isolation_level.return_value = "REPEATABLE READ"
    session.scalar.return_value = "on"
    if violation in {"new", "dirty", "deleted"}:
        setattr(session, violation, {object()})
    elif violation == "isolation":
        session.connection.return_value.get_isolation_level.return_value = "READ COMMITTED"
    else:
        session.scalar.return_value = "off"
    with pytest.raises(PolicyLifecycleError) as error:
        compute_annual_projection(session, USER, NOW)
    assert error.value.code == "INVALID_READ_SNAPSHOT"
