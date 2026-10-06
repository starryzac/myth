"""One isolated actual PG candidate; root runs it, no FULL-105 acceptance claim."""

from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, User
from app.services.full_policy_change_impact import preview_full_policy_financial_impact
from app.services.full_policy_lifecycle import FullPreviewRequest, canonical_candidate
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_protection_projection_api import confirm
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def preview(
    engine: Engine, policy_id: UUID, version_id: UUID, config: dict[str, Any]
) -> dict[str, Any]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                user = session.scalar(select(User).where(User.is_simulated.is_(True)))
                assert user is not None
                return preview_full_policy_financial_impact(
                    session,
                    user.id,
                    policy_id,
                    FullPreviewRequest(expected_version_id=version_id, configuration=config),
                    NOW,
                ).model_dump(mode="json")


def test_actual_confirmed_full_future_change_preview_retains_all_originals_and_unknown_on_tamper(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    config = {
        "type": "dated_expense",
        "name": "隔离用户实际确认的未来支出",
        "window": {"start": "2026-10-12", "end": "2026-10-15"},
        "amount": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
    }
    registered = confirm(client, "DatedExpensePolicy", config, "impact-real-dated")
    policy_id, version_id = UUID(registered["policy_id"]), UUID(registered["version_id"])
    proposed = {**config, "amount": {"min_cents": 0, "target_cents": 150, "max_cents": 350}}
    before = physical_snapshot(engine)
    original_annual = client.get("/api/v1/planning/full-annual").json()
    value = preview(engine, policy_id, version_id, proposed)
    impact = value["financial_impact"]
    assert impact["status"] == "PROJECTED"
    assert impact["before"] == original_annual["projection"]["full_annual_projection"]
    assert (
        len(impact["before"]["calculation_trace"])
        == len(impact["after"]["calculation_trace"])
        == 1098
    )
    assert impact["delta_minimum_margin_cents"] == -150
    assert value["current_configuration_hash"] == registered["configuration_hash"]
    assert value["configuration_hash"] != value["current_configuration_hash"]
    assert value["after_configuration"] == canonical_candidate("DatedExpensePolicy", proposed)
    assert impact["grants_authority"] is False and impact["future_income_cents"] == 0
    assert len(impact["positions"]) == len(value["original_position_ids"])
    assert preview(engine, policy_id, version_id, proposed) == value
    assert client.get("/api/v1/planning/full-annual").json() == original_annual
    assert physical_snapshot(engine) == before
    with pytest.raises(PolicyLifecycleError, match="版本已变化"):
        preview(engine, policy_id, UUID(int=999999), proposed)
    assert physical_snapshot(engine) == before
    # A real original row tamper must make the entire numerical difference unknown.
    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        account.balance_cents += 1
    tampered = physical_snapshot(engine)
    rejected = preview(engine, policy_id, version_id, proposed)
    assert rejected["financial_impact"]["status"] == "UNKNOWN"
    assert rejected["financial_impact"]["after"] is None
    assert rejected["financial_impact"]["delta_safe_idle_cents"] is None
    assert physical_snapshot(engine) == tampered
