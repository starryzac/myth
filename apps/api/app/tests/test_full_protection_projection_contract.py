"""Public read contract and snapshot guards only; service output is a pure fixture."""

from collections.abc import Iterator
from datetime import timedelta
from typing import cast
from unittest.mock import MagicMock

import pytest
from app.api.dependencies import get_demo_user, get_now, get_session
from app.api.v1 import full_protection_projection as api
from app.db.models import User
from app.domain.full_protection_projection import project_full_protection
from app.services.full_projection import FutureIncomeProjection, _checkpoint
from app.services.full_protection_projection import (
    FullAnnualProtectionResponse,
    compute_full_annual_protection,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_projection import NOW, USER, audit
from app.tests.test_full_protection_projection import data
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def response() -> FullAnnualProtectionResponse:
    projection = project_full_protection(data())
    assert projection.full_annual_projection is not None
    points = projection.full_annual_projection.calculation_trace
    first = points[0].date
    return FullAnnualProtectionResponse(
        user_id=USER,
        as_of=NOW,
        projection=projection,
        initial_checkpoint=_checkpoint(0, first, points[:3]),
        daily_checkpoints=[
            _checkpoint(day, first + timedelta(days=day), points[day * 3 : (day + 1) * 3])
            for day in range(1, 366)
        ],
        full_policy_sources=[],
        future_income=FutureIncomeProjection(),
        source_evidence_ids=[],
        source_issues=[],
        input_digest=projection.input_hash,
        audit=audit(),
        limitations=["SYNTHETIC_PURE_CONTRACT_FIXTURE"],
    )


def test_router_forwards_only_actual_server_principal_clock_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(api.router)
    placeholder = cast(Session, object())

    def dependency() -> Iterator[Session]:
        yield placeholder

    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[get_demo_user] = lambda: User(id=USER)
    app.dependency_overrides[get_now] = lambda: NOW
    expected = response()
    calls: list[tuple[Session, object, object]] = []

    def read(session: Session, identity: object, now: object) -> FullAnnualProtectionResponse:
        calls.append((session, identity, now))
        return expected

    monkeypatch.setattr(api, "compute_full_annual_protection", read)
    with TestClient(app) as client:
        value = client.get("/api/v1/planning/full-annual")
        assert value.status_code == 200 and value.json() == expected.model_dump(mode="json")
        for key, injection in (
            ("user_id", str(USER)),
            ("future_income_cents", "999999999"),
            ("paid_cents", "200"),
            ("as_of", "2099-01-01"),
            ("horizon_days", "1"),
            ("authority", "AUTO_EXECUTE"),
        ):
            assert (
                client.get("/api/v1/planning/full-annual", params={key: injection}).status_code
                == 422
            )
    assert calls == [(placeholder, USER, NOW)]


@pytest.mark.parametrize("violation", ["new", "dirty", "deleted", "isolation", "read_only"])
def test_service_requires_clean_repeatable_read_readonly_snapshot(violation: str) -> None:
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
        compute_full_annual_protection(session, USER, NOW)
    assert error.value.code == "INVALID_READ_SNAPSHOT"
