"""One generated-PG actual read-only candidate; root alone runs it after registration."""

from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from app.db.models import Account
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import verify_audit_chain
from app.services.full_policy_lifecycle import canonical_candidate
from app.services.scenario_runner import ScenarioRunner
from app.tests.test_boundary_service import confirmed_policy
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_current_periodic_producer_confirmation_denominator_and_zero_writes(
    annual_client: tuple[TestClient, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine = annual_client
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", "SYNTHETIC_204_PERIODIC_ACTOR")
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "cf" * 32)
    local = NOW.astimezone(ZoneInfo("Asia/Shanghai"))
    common = {
        "payee_id": "synthetic-landlord-001",
        "amount_rule": {"kind": "exact", "amount_cents": 100},
        "due_day": local.day,
        "auto_execute": True,
        "prepare_days_before": 0,
    }
    with Session(engine) as session, session.begin():
        source = session.scalar(
            select(Account)
            .where(Account.user_id == DEMO_USER_ID, Account.account_type == "CASH")
            .order_by(Account.id)
        )
        assert source is not None
        source_id = source.id
        original_id, original_version = confirmed_policy(
            session, common | {"type": "recurring_obligation"}, NOW
        )
    observed = ScenarioRunner(engine, DEMO_USER_ID).observe_recurring_payment(
        original_id, local.strftime("%Y-%m"), NOW
    )
    assert observed["observation"]["paid_cents"] == 0
    assert observed["observation"]["observation_protocol"] == "scenario-empty-bank-payment-v1"
    config = common | {
        "type": "periodic_transfer",
        "name": "204周期实际只读",
        "source_account_id": str(source_id),
        "single_action_cap_cents": 100,
        "valid_until": (local.date() + timedelta(days=60)).isoformat(),
    }
    created = client.post(
        "/api/v1/full-policies/confirm",
        json={
            "template_name": "PeriodicTransferPolicy",
            "configuration": config,
            "reviewed_hash": configuration_hash(
                canonical_candidate("PeriodicTransferPolicy", config)
            ),
            "accepted": True,
            "reason": "隔离模拟USER明确规则",
            "idempotency_key": "204-periodic-full",
        },
    )
    assert created.status_code == 200, created.text
    full = created.json()
    assert full["bank_authority"] is False
    target = "full-periodic:" + full["policy_id"]
    before = physical_snapshot(engine)
    empty = client.get("/api/v1/boundary/periodic-action-producers/current")
    assert empty.status_code == 200, empty.text
    empty_value = empty.json()
    selected = next(row for row in empty_value["results"] if row["candidate_key"] == target)
    assert selected["view"]["state"] == "EXCLUDED" and selected["view"]["reasons"] == [
        "NO_CURRENT_DEDICATED_RELATION"
    ], empty_value
    assert physical_snapshot(engine) == before
    assert (
        client.get(
            "/api/v1/boundary/periodic-action-producers/current?now=2026-10-01&amount_cents=100"
        ).status_code
        == 422
    )
    logged = client.post(
        "/api/v1/local-actor/login",
        json={"username": "bounded-user", "secret": "SYNTHETIC_204_PERIODIC_ACTOR"},
    )
    assert logged.status_code == 200, logged.text
    body = {
        "expected_epoch_id": full["epoch_id"],
        "full_policy_id": full["policy_id"],
        "expected_full_version_id": full["version_id"],
        "original_policy_id": str(original_id),
        "expected_original_version_id": str(original_version),
        "idempotency_key": "204-periodic-start",
    }
    started = client.post("/api/v1/full-payment-relations/start", json=body)
    assert started.status_code == 200, started.text
    command = started.json()["original"]
    confirmed = client.post(
        f"/api/v1/full-payment-relations/starts/{command['command_id']}/confirm",
        json={
            "expected_epoch_id": full["epoch_id"],
            "reviewed_scope_hash": command["scope_hash"],
            "accepted": True,
            "reason": "真实模拟签名USER原完整周期范围",
            "idempotency_key": "204-periodic-confirm",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    before = physical_snapshot(engine)
    current = client.get("/api/v1/boundary/periodic-action-producers/current")
    assert current.status_code == 200, current.text
    value = current.json()
    assert value["periodic_family_complete"], value
    selected = next(row for row in value["results"] if row["candidate_key"] == target)
    assert (
        selected["view"]["state"] == "INCLUDED"
        and selected["view"]["amount_cents"] == 100
        and selected["view"]["autonomy_level"] == "AUTO_EXECUTE"
    ), selected
    assert selected["shadow_original_candidate_key"] == "payment:" + str(original_id)
    assert selected["current_confirmation_evidence_ids"] == [confirmed.json()["evidence_id"]]
    assert (
        len(selected["original_command_ids"]) == 2
        and selected["unresolved_original_action_ids"] == []
    )
    assert (
        value["relation_source_count"] >= 2
        and len(value["relation_source_ids"]) == value["relation_source_count"]
    )
    assert (
        not value["bank_authority"]
        and not value["financial_write"]
        and not value["full_global_adapter_installed"]
    )
    assert physical_snapshot(engine) == before
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        with Session(bind=connection) as session:
            audit = verify_audit_chain(session, DEMO_USER_ID)
            assert audit.status == "VALID"
    # New fixed relation authorizes a future action scope; this test does not prepare or pay.
    assert UUID(full["policy_id"]) and UUID(full["epoch_id"])
