"""One actual native FULL metadata/read candidate; root runs it serially on generated PG."""

import json
from uuid import UUID

import pytest
from app.domain.demo_identity import DEMO_USER_ID
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.full_native_steps import FullNativeSteps
from app.tests.test_full_projection import NOW
from app.tests.test_full_projection_api import annual_client as annual_client
from app.tests.test_full_projection_api import physical_snapshot
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_native_login_full_validate_and_metadata_confirm_use_original_routes(
    annual_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = annual_client
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", "SYNTHETIC_NATIVE_FULL_USER_804")
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "ca" * 32)
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None and epoch.status == "OPEN"
        epoch_id = epoch.id
    before = physical_snapshot(engine)
    with FullNativeSteps(engine, DEMO_USER_ID, epoch_id, NOW) as native:
        login = native.dispatch("LOCAL_USER_LOGIN", {}, NOW)
        assert login["status_code"] == 200
        assert login["result"]["principal"]["role"] == "USER"
        assert login["result"]["bank_authority"] is False
        assert native.dispatch("LOCAL_USER_READ", {}, NOW)["status_code"] == 200
        config = {
            "type": "dated_expense",
            "name": "原FULL路由真实隔离执行",
            "window": {"start": "2026-10-12", "end": "2026-10-15"},
            "amount": {"min_cents": 0, "target_cents": 100, "max_cents": 200},
        }
        validated = native.dispatch(
            "FULL_POLICY_VALIDATE",
            {
                "body": {
                    "template_name": "DatedExpensePolicy",
                    "configuration": config,
                }
            },
            NOW,
        )
        assert validated["status_code"] == 200
        assert physical_snapshot(engine) == before
        original = validated["result"]
        confirmed = native.dispatch(
            "FULL_POLICY_CONFIRM",
            {
                "body": {
                    "template_name": "DatedExpensePolicy",
                    "configuration": original["normalized_configuration"],
                    "reviewed_hash": original["configuration_hash"],
                    "accepted": True,
                    "reason": "实际原JSON复核",
                    "idempotency_key": "native-full-policy",
                }
            },
            NOW,
        )
        assert confirmed["status_code"] == 200, confirmed
        identity = confirmed["result"]["policy_id"]
        after = physical_snapshot(engine)
        read = native.dispatch("FULL_POLICY_READ", {"policy_id": identity}, NOW)
        assert read["status_code"] == 200
        assert read["result"]["current_version"]["content_hash"] == original["configuration_hash"]
        assert physical_snapshot(engine) == after
        injected = native.dispatch(
            "FULL_POLICY_CONFIRM",
            {
                "body": {
                    "principal": {"role": "USER"},
                    "configuration": config,
                }
            },
            NOW,
        )
        assert injected["status_code"] == 422
        assert physical_snapshot(engine) == after
    assert UUID(identity)
    metadata = {
        "full_policies",
        "full_policy_versions",
        "full_policy_commands",
        "evidence_items",
        "decision_runs",
        "audit_events",
        "audit_subject_snapshots",
        "audit_heads",
        "audit_epochs",
        "intervention_outbox",
        "intervention_inbox",
    }
    assert {name: rows for name, rows in json.loads(before).items() if name not in metadata} == {
        name: rows for name, rows in json.loads(after).items() if name not in metadata
    }
    with Session(engine) as session:
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
