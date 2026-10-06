"""One isolated real-PG candidate; collection only until root runs the financial chain."""

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from app.db.models import Account, User
from app.services.full_policy_change_history import preview_full_policy_history_financial_impact
from app.services.full_policy_lifecycle import FullPreviewRequest, canonical_candidate
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_full_protection_projection_api import confirm
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def actual_preview(
    engine: Engine, policy: str, version: str, configuration: dict[str, Any]
) -> dict[str, Any]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                user = session.scalar(select(User).where(User.is_simulated.is_(True)))
                assert user is not None
                return preview_full_policy_history_financial_impact(
                    session,
                    user.id,
                    UUID(policy),
                    FullPreviewRequest(
                        expected_version_id=UUID(version), configuration=configuration
                    ),
                    NOW,
                ).model_dump(mode="json")


def test_actual_second_change_preview_rebuilds_originals_and_never_writes_financial_facts(
    annual_client: tuple[TestClient, Engine],
) -> None:
    client, engine = annual_client
    configuration = {
        "type": "dated_expense",
        "name": "隔离重复未来支出修改",
        "window": {"start": "2026-10-12", "end": "2026-10-15"},
        "amount": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
    }
    first = confirm(client, "DatedExpensePolicy", configuration, "history-preview-create")
    policy = first["policy_id"]
    second_configuration = {
        **configuration,
        "amount": {"min_cents": 0, "target_cents": 150, "max_cents": 350},
    }
    changed = client.post(
        f"/api/v1/full-policies/{policy}/change",
        json={
            "expected_version_id": first["version_id"],
            "configuration": second_configuration,
            "reviewed_hash": configuration_hash_for(second_configuration),
            "accepted": True,
            "reason": "真实隔离用户第一次修改",
            "idempotency_key": "history-preview-first-change",
        },
    )
    assert changed.status_code == 200, changed.text
    second = changed.json()
    candidate = {**configuration, "amount": {"min_cents": 0, "target_cents": 200, "max_cents": 475}}
    before = physical_snapshot(engine)
    original = client.get("/api/v1/planning/full-annual").json()
    value = actual_preview(engine, policy, second["version_id"], candidate)
    impact = value["financial_impact"]
    assert impact["protocol"] == "full-policy-financial-impact-history-v2"
    assert impact["status"] == "PROJECTED" and impact["delta_minimum_margin_cents"] == -125
    assert impact["history_proof"]["actual_version_count"] == 2
    assert impact["history_proof"]["captured_version_count"] == 2
    assert impact["before"] == original["projection"]["full_annual_projection"]
    assert len(impact["after"]["calculation_trace"]) == 1098
    assert actual_preview(engine, policy, second["version_id"], candidate) == value
    assert physical_snapshot(engine) == before
    third = client.post(
        f"/api/v1/full-policies/{policy}/change",
        json={
            "expected_version_id": second["version_id"],
            "configuration": candidate,
            "reviewed_hash": value["configuration_hash"],
            "accepted": True,
            "reason": "真实隔离用户明确再次修改",
            "idempotency_key": "history-preview-second-change",
        },
    )
    assert third.status_code == 200, third.text
    actual = client.get("/api/v1/planning/full-annual").json()
    curve = actual["projection"]["full_annual_projection"]
    assert (
        curve is not None
        and actual["projection"]["algorithm_version"]
        == "registered-full-protection-future-dated-history-v2"
    )
    expected = deepcopy(impact["after"]["calculation_trace"])
    old_identity = f"CANDIDATE:{policy}:{value['configuration_hash']}:2026-10-12"
    new_identity = f"FULL:{policy}:{third.json()['version_id']}:DATED:2026-10-12:2026-10-15"
    assert any(old_identity in point["obligation_occurrence_ids"] for point in expected)
    for point in expected:
        point["obligation_occurrence_ids"] = sorted(
            new_identity if item == old_identity else item
            for item in point["obligation_occurrence_ids"]
        )
    assert curve["calculation_trace"] == expected
    for key in ("minimum_margin_cents", "safe_idle_cents", "max_allocatable_by_product"):
        assert curve[key] == impact["after"][key]
    with Session(engine) as session, session.begin():
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        account.balance_cents += 1
    tampered = physical_snapshot(engine)
    rejected = actual_preview(engine, policy, third.json()["version_id"], candidate)
    assert rejected["financial_impact"]["status"] == "UNKNOWN"
    assert (
        rejected["financial_impact"]["after"] is None
        and rejected["financial_impact"]["delta_safe_idle_cents"] is None
    )
    assert physical_snapshot(engine) == tampered


def configuration_hash_for(configuration: dict[str, Any]) -> str:
    from app.domain.policy_configuration import configuration_hash

    return configuration_hash(canonical_candidate("DatedExpensePolicy", configuration))
