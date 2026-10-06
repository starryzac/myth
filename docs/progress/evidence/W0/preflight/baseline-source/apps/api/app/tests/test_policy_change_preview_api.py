"""Real snapshot-only policy assumptions, never implicit confirmation or fund writes."""

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.dependencies import get_engine, get_now
from app.db.models import EvidenceItem
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.main import create_app
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.services.goals import create_goal_projection
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_demo_seed import database_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


@pytest.fixture
def preview_fixture() -> Iterator[tuple[TestClient, Engine, UUID, UUID]]:
    with temporary_database() as url:
        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_database_engine(url)
        try:
            seed_demo(engine)
            with Session(engine) as session, session.begin():
                policy, version = confirmed_policy(
                    session, {"type": "emergency_buffer", "amount_cents": 100_000}, NOW
                )
            app = create_app()
            app.dependency_overrides[get_engine] = lambda: engine
            app.dependency_overrides[get_now] = lambda: NOW
            with TestClient(app) as client:
                yield client, engine, policy, version
        finally:
            engine.dispose()


def test_change_preview_shares_real_homepage_basis_and_is_readonly_before_first_user(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    client, engine, policy, version = preview_fixture
    dashboard = client.get("/api/v1/dashboard").json()
    before = database_snapshot(engine)
    transactions: list[tuple[str, str]] = []

    def observe_first_user(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.lstrip().lower().startswith("select users."):
            transactions.append(
                (
                    connection.exec_driver_sql("SHOW transaction_isolation").scalar_one(),
                    connection.exec_driver_sql("SHOW transaction_read_only").scalar_one(),
                )
            )

    event.listen(engine, "before_cursor_execute", observe_first_user)
    try:
        response = client.post(
            f"/api/v1/policies/{policy}/change-preview",
            json={
                "expected_version_id": str(version),
                "configuration": {
                    "type": "emergency_buffer",
                    "amount_cents": 200_000,
                },
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", observe_first_user)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert transactions and all(item == ("repeatable read", "on") for item in transactions)
    assert doc["schema_version"] == "policy-change-preview-v1"
    assert doc["simulation"] is doc["preview_only"] is doc["financial_only"] is True
    assert doc["as_of"] == "2026-10-04T01:00:00Z"
    assert doc["before"] == dashboard["boundary"]
    assert doc["before"]["safe_idle_cents"] == 3_057_400
    assert doc["after"]["safe_idle_cents"] == 2_957_400
    assert doc["delta_safe_idle_cents"] == -100_000
    assert doc["delta_minimum_margin_cents"] == -100_000
    assert doc["after"]["input_digest"] != doc["before"]["input_digest"]
    assert doc["current_fact_input_digest"] == doc["before"]["input_digest"]
    assert doc["hypothetical_input_digest"] == doc["after"]["input_digest"]
    assert database_snapshot(engine) == before
    assert (
        client.post(
            f"/api/v1/policies/{policy}/change-preview",
            json={
                "expected_version_id": str(version),
                "configuration": deepcopy(doc["configuration"]),
            },
        ).json()
        == doc
    )
    assert database_snapshot(engine) == before


def test_preview_rejects_stale_foreign_and_injected_authority_without_writes(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    client, engine, policy, version = preview_fixture
    before = database_snapshot(engine)
    valid: dict[str, Any] = {
        "expected_version_id": str(version),
        "configuration": {"type": "emergency_buffer", "amount_cents": 200_000},
    }
    for body, status in [
        ({**valid, "accepted": True}, 422),
        ({**valid, "as_of": "2099-01-01T00:00:00Z"}, 422),
        ({**valid, "user_id": str(uuid4())}, 422),
        ({**valid, "safe_idle_cents": 100000000}, 422),
        ({**valid, "expected_version_id": str(uuid4())}, 409),
        ({**valid, "configuration": {"type": "emergency_buffer", "amount_cents": True}}, 422),
        ({**valid, "configuration": {"type": "emergency_buffer", "amount_cents": -1}}, 422),
        ({**valid, "configuration": {"type": "goal_saving"}}, 422),
    ]:
        response = client.post(f"/api/v1/policies/{policy}/change-preview", json=body)
        assert response.status_code == status, response.text
    assert (
        client.post(
            f"/api/v1/policies/{uuid4()}/change-preview",
            json=valid,
        ).status_code
        == 404
    )
    assert database_snapshot(engine) == before


def test_suspended_preview_never_resumes_and_revoked_policy_is_rejected(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    client, engine, policy, version = preview_fixture
    assert (
        client.post(
            f"/api/v1/policies/{policy}/suspend",
            json={"expected_version_id": str(version)},
        ).status_code
        == 200
    )
    before = database_snapshot(engine)
    result = client.post(
        f"/api/v1/policies/{policy}/change-preview",
        json={
            "expected_version_id": str(version),
            "configuration": {"type": "emergency_buffer", "amount_cents": 2_000_000},
        },
    )
    assert result.status_code == 200, result.text
    doc = result.json()
    assert doc["assumed_status"] == "SUSPENDED"
    assert doc["before"]["safe_idle_cents"] == doc["after"]["safe_idle_cents"] == 3_157_400
    assert doc["delta_safe_idle_cents"] == 0
    assert database_snapshot(engine) == before
    assert (
        client.post(
            f"/api/v1/policies/{policy}/revoke",
            json={"expected_version_id": str(version)},
        ).status_code
        == 200
    )
    stopped = database_snapshot(engine)
    assert (
        client.post(
            f"/api/v1/policies/{policy}/change-preview",
            json={
                "expected_version_id": str(version),
                "configuration": doc["configuration"],
            },
        ).status_code
        == 409
    )
    assert database_snapshot(engine) == stopped


def test_hypothetical_financial_values_match_later_explicit_change_on_fixed_real_facts(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    client, engine, policy, version = preview_fixture
    result = client.post(
        f"/api/v1/policies/{policy}/change-preview",
        json={
            "expected_version_id": str(version),
            "configuration": {"type": "emergency_buffer", "amount_cents": 234_567},
        },
    )
    assert result.status_code == 200, result.text
    doc = result.json()
    payload = {
        "expected_version_id": str(version),
        "configuration": doc["configuration"],
        "reviewed_hash": doc["configuration_hash"],
        "accepted": True,
        "reason": "Explicitly reviewed real preview",
        "idempotency_key": "402-change-one",
    }
    updated = client.patch(f"/api/v1/policies/{policy}", json=payload)
    assert updated.status_code == 200, updated.text
    actual = client.get("/api/v1/dashboard").json()["boundary"]
    for key in (
        "state",
        "status",
        "safe_idle_cents",
        "minimum_margin_cents",
        "deficit_cents",
        "protected_cents_by_reason",
        "current_protected_cents",
        "current_protected_cents_by_reason",
        "current_margin_cents",
        "constraining_date",
    ):
        assert actual[key] == doc["after"][key], key
    final = database_snapshot(engine)
    assert client.patch(f"/api/v1/policies/{policy}", json=payload).json() == updated.json()
    assert database_snapshot(engine) == final


def test_configuration_change_cannot_hide_missing_complete_financial_source(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    client, engine, policy, version = preview_fixture
    with Session(engine) as session, session.begin():
        proof = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_ASSET_EXPOSURE",
                EvidenceItem.status == "VALID",
            )
        )
        assert proof is not None
        proof.status = "SUPERSEDED"
    before = database_snapshot(engine)
    result = client.post(
        f"/api/v1/policies/{policy}/change-preview",
        json={
            "expected_version_id": str(version),
            "configuration": {"type": "emergency_buffer", "amount_cents": 0},
        },
    )
    assert result.status_code == 200, result.text
    doc = result.json()
    assert doc["before"]["safe_idle_cents"] is doc["after"]["safe_idle_cents"] is None
    assert doc["delta_safe_idle_cents"] is doc["delta_minimum_margin_cents"] is None
    assert doc["before"]["issues"] and doc["after"]["issues"]
    assert database_snapshot(engine) == before


def test_future_goal_change_reads_real_existing_month_without_zero_assumption(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    from app.db.models import Account
    from app.domain.policy_configuration import configuration_hash, validate_configuration
    from app.services.policy_lifecycle import change_policy

    client, engine, _, _ = preview_fixture
    goal_configuration: dict[str, Any] = {
        "type": "goal_saving",
        "name": "真实月累计来源",
        "target_cents": 100_000,
        "deadline": "2026-12-31",
        "monthly_contribution": {
            "min_cents": 10_000,
            "target_cents": 10_000,
            "max_cents": 20_000,
        },
    }
    with Session(engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, goal_configuration, NOW)
        account = session.scalar(select(Account).where(Account.account_type == "CASH"))
        assert account is not None
        goal = create_goal_projection(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            account.id,
            NOW,
        ).goal
        goal_id = goal.id
        future_configuration = {**goal_configuration, "valid_from": "2026-11-01"}
        changed = change_policy(
            session,
            DEMO_USER_ID,
            policy_id,
            version_id,
            future_configuration,
            configuration_hash(validate_configuration(future_configuration)),
            True,
            "Future policy preserves actual month proof",
            "402-future-goal",
            NOW,
        )
    before = database_snapshot(engine)
    result = client.post(
        f"/api/v1/policies/{policy_id}/change-preview",
        json={
            "expected_version_id": str(changed.current_version_id),
            "configuration": goal_configuration,
        },
    )
    assert result.status_code == 200, result.text
    doc = result.json()
    assert doc["before"]["state"] == doc["after"]["state"] == "PROVEN"
    # Today's future policy is inactive; the worst later day includes the paid bill.
    assert doc["before"]["current_protected_cents_by_reason"]["goal_minimum"] == 0
    assert doc["after"]["current_protected_cents_by_reason"]["goal_minimum"] == 30_000
    assert doc["before"]["safe_idle_cents"] == 3_037_400
    assert doc["after"]["safe_idle_cents"] == 3_027_400
    assert doc["delta_safe_idle_cents"] == -10_000
    assert database_snapshot(engine) == before
    with Session(engine) as session, session.begin():
        month = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_GOAL_MONTH_CONTRIBUTION",
                EvidenceItem.content["goal_id"].astext == str(goal_id),
                EvidenceItem.status == "VALID",
            )
        )
        assert month is not None
        month.status = "SUPERSEDED"
    missing_before = database_snapshot(engine)
    missing = client.post(
        f"/api/v1/policies/{policy_id}/change-preview",
        json={
            "expected_version_id": str(changed.current_version_id),
            "configuration": goal_configuration,
        },
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["after"]["safe_idle_cents"] is None
    assert "INVALID_GOAL_MONTH_SOURCE" in {
        issue["code"] for issue in missing.json()["after"]["issues"]
    }
    assert database_snapshot(engine) == missing_before


def test_living_change_reestimates_real_history_and_only_replaces_parameter_shortfall(
    preview_fixture: tuple[TestClient, Engine, UUID, UUID],
) -> None:
    client, engine, _, _ = preview_fixture
    configuration: dict[str, Any] = {
        "type": "living_reserve",
        "horizon_days": 14,
        "extra_buffer_cents": 50_000,
        "method": {
            "name": "rolling_window_quantile",
            "lookback_days": 90,
            "quantile": 0.8,
            "essential_categories": ["food", "transport", "daily_necessities"],
            "exclude_one_off": True,
        },
    }
    with Session(engine) as session, session.begin():
        policy_id, version_id = confirmed_policy(session, configuration, NOW)
    candidate = {**configuration, "method": {**configuration["method"], "lookback_days": 56}}
    before = database_snapshot(engine)
    response = client.post(
        f"/api/v1/policies/{policy_id}/change-preview",
        json={
            "expected_version_id": str(version_id),
            "configuration": candidate,
        },
    )
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["before"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert doc["before"]["safe_idle_cents"] is None
    assert doc["after"]["state"] == "PROVEN"
    assert doc["after"]["safe_idle_cents"] == 2_929_500
    assert doc["after"]["protected_cents_by_reason"]["living"] == 127_900
    assert doc["delta_safe_idle_cents"] is None
    assert database_snapshot(engine) == before
    with Session(engine) as session, session.begin():
        history = session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.source_type == "SIMULATED_TRANSACTION_HISTORY_COVERAGE",
            )
        )
        assert history is not None
        history.status = "UNKNOWN"
    bad_before = database_snapshot(engine)
    bad = client.post(
        f"/api/v1/policies/{policy_id}/change-preview",
        json={
            "expected_version_id": str(version_id),
            "configuration": candidate,
        },
    )
    assert bad.status_code == 200, bad.text
    assert bad.json()["after"]["safe_idle_cents"] is None
    assert bad.json()["after"]["issues"]
    assert database_snapshot(engine) == bad_before
