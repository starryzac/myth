"""Two owned PostgreSQL risk nodes; runtime proof is obtained by the root coordinator."""

import json
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from app.db.models import EvidenceItem, Goal, PolicyVersion
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID
from app.services.full_goals import MODEL_SOURCE, confirm_full_goal_model
from app.tests.test_full_projection_api import physical_snapshot
from app.tests.test_goal_api import NOW, confirmed_goal_request
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def money_originals(snapshot: str) -> dict[str, Any]:
    tables: dict[str, Any] = json.loads(snapshot)
    names = {
        "accounts",
        "transactions",
        "credit_card_bills",
        "asset_positions",
        "bank_operations",
        "simulated_bank_redemptions",
        "simulated_bank_postings",
        "external_bank_facts",
        "asset_products",
    }
    assert names <= tables.keys()
    return {name: tables[name] for name in sorted(names)}


def confirmed_existing_goal(client: TestClient, engine: Engine) -> tuple[str, dict[str, Any]]:
    body = confirmed_goal_request(client, engine)
    created = client.post("/api/v1/goals", json=body)
    assert created.status_code == 200
    goal = created.json()["goal"]
    with Session(engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        epoch_id = str(epoch.id)
    return goal["id"], {
        "expected_version_id": goal["policy_version_id"],
        "expected_epoch_id": epoch_id,
        "configuration": {
            "type": "long_term_goal",
            "name": goal["name"],
            "target_cents": goal["target_cents"],
            "deadline": goal["deadline"],
            "monthly_contribution": {
                "min_cents": goal["monthly_min_cents"],
                "target_cents": goal["monthly_target_cents"],
                "max_cents": goal["monthly_max_cents"],
            },
            "importance": 87,
            "minimum_guarantee_cents": 15000,
            "allow_partial": True,
            "allow_deferral": True,
            "deferral_cost_cents_per_day": 19,
        },
        "reason": "真实已有目标的完整模型确认",
        "idempotency_key": "full-goal-risk",
    }


def test_actual_goal_full_model_preview_confirm_replay_and_tamper_are_bound_without_money(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, command = confirmed_existing_goal(client, engine)
    url = f"/api/v1/goals/{goal_id}/full-model"
    before = physical_snapshot(engine)
    empty = client.get(url)
    assert empty.status_code == 200 and empty.json()["status"] == "MODEL_MISSING"
    preview = client.post(
        url + "/preview", json={k: command[k] for k in ("expected_version_id", "configuration")}
    )
    assert preview.status_code == 200
    value = preview.json()
    assert value["preview_only"] is True and value["grants_authority"] is False
    assert value["base_policy_impact"]["configuration_hash"] == value["base_configuration_hash"]
    assert value["extra_fields_in_base_impact"] is False
    assert physical_snapshot(engine) == before
    payload = command | {
        "reviewed_full_hash": value["full_configuration_hash"],
        "reviewed_base_hash": value["base_configuration_hash"],
        "accepted": True,
    }
    for change in (
        {"reviewed_full_hash": "0" * 64},
        {"reviewed_base_hash": "0" * 64},
        {"accepted": False},
        {"expected_epoch_id": str(UUID(int=999))},
        {"allocated_cents": 1},
    ):
        rejected = client.post(url + "/confirm", json=payload | change)
        assert rejected.status_code in {409, 422}
        assert physical_snapshot(engine) == before
    result = client.post(url + "/confirm", json=payload)
    assert result.status_code == 200
    receipt = result.json()
    assert receipt["bank_authority"] is False and receipt["dedicated_audit_event"] is False
    assert receipt["lifecycle"]["previous_version_id"] == command["expected_version_id"]
    after = physical_snapshot(engine)
    assert money_originals(after) == money_originals(before)
    current = client.get(url)
    assert current.status_code == 200 and current.json()["status"] == "VERIFIED"
    assert current.json()["policy_effective_status"] == "ACTIVE"
    assert current.json()["evidence_id"] == receipt["evidence_id"]
    assert current.json()["full_configuration"] == value["full_configuration"]
    repeated = client.post(url + "/confirm", json=payload)
    assert repeated.status_code == 200 and repeated.json()["idempotent_replay"] is True
    assert repeated.json()["evidence_id"] == receipt["evidence_id"]
    assert physical_snapshot(engine) == after
    changed = deepcopy(payload)
    changed["configuration"]["deferral_cost_cents_per_day"] += 1
    changed_preview = client.post(
        url + "/preview",
        json={
            "expected_version_id": receipt["lifecycle"]["current_version_id"],
            "configuration": changed["configuration"],
        },
    )
    assert changed_preview.status_code == 200
    changed["reviewed_full_hash"] = changed_preview.json()["full_configuration_hash"]
    assert client.post(url + "/confirm", json=changed).status_code == 409
    assert physical_snapshot(engine) == after
    with Session(engine) as session:
        goal = session.get(Goal, UUID(goal_id))
        assert goal is not None and goal.allocated_cents == 0
        old = session.get(PolicyVersion, UUID(command["expected_version_id"]))
        assert old is not None and old.configuration["type"] == "goal_saving"
        assert verify_audit_chain(session, DEMO_USER_ID).errors == []
    with Session(engine) as session, session.begin():
        proof = session.get(EvidenceItem, UUID(receipt["evidence_id"]))
        assert proof is not None
        proof.content_hash = "0" * 64
    tampered = physical_snapshot(engine)
    assert client.get(url).status_code == 409
    assert physical_snapshot(engine) == tampered


def test_full_evidence_failure_rolls_back_actual_base_version_and_audit(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = goal_client
    goal_id, request = confirmed_existing_goal(client, engine)
    preview = client.post(
        f"/api/v1/goals/{goal_id}/full-model/preview",
        json={k: request[k] for k in ("configuration", "expected_version_id")},
    )
    assert preview.status_code == 200
    value = preview.json()
    before = physical_snapshot(engine)
    with Session(engine) as session, session.begin():
        original_flush = session.flush

        def reject_full_evidence(*args: Any, **kwargs: Any) -> None:
            if any(
                isinstance(row, EvidenceItem) and row.source_type == MODEL_SOURCE
                for row in session.new
            ):
                raise RuntimeError("EXPLICIT_FULL_MODEL_STORAGE_FAILURE")
            original_flush(*args, **kwargs)

        monkeypatch.setattr(session, "flush", reject_full_evidence)
        with pytest.raises(RuntimeError, match="EXPLICIT_FULL_MODEL_STORAGE_FAILURE"):
            confirm_full_goal_model(
                session,
                DEMO_USER_ID,
                UUID(goal_id),
                UUID(request["expected_version_id"]),
                UUID(request["expected_epoch_id"]),
                request["configuration"],
                value["full_configuration_hash"],
                value["base_configuration_hash"],
                True,
                request["reason"],
                request["idempotency_key"],
                NOW,
            )
        assert (
            session.scalar(select(EvidenceItem).where(EvidenceItem.source_type == MODEL_SOURCE))
            is None
        )
    assert physical_snapshot(engine) == before
