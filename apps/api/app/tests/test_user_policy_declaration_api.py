"""Actual server candidates and separate original confirmation; root schedules real PG."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from app.db.models import EvidenceItem
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_policy_schema import physical_originals
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_declaration_is_proposed_only_replay_and_lookup_bind_original_confirmation(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    epoch_id = client.get("/api/v1/demo/state").json()["epoch_id"]
    body = {
        "configuration": {"type": "emergency_buffer", "amount_cents": 123007},
        "idempotency_key": "k" * 160,
        "expected_epoch_id": epoch_id,
        "source_proposal_id": None,
    }
    base = "/api/v1/policy-declarations"
    lookup = f"{base}/{epoch_id}/by-key/{body['idempotency_key']}"
    before = physical_originals(engine)
    not_found = client.get(lookup)
    assert not_found.status_code == 200
    assert not_found.json()["status"] == "NOT_FOUND" and not not_found.json()["not_found_is_final"]
    response = client.post(base, json=body)
    assert response.status_code == 200, response.text
    declared = response.json()
    assert declared["status"] == declared["current_proposal_status"] == "PROPOSED"
    assert declared["original_request"] == body
    assert declared["request_hash"] == configuration_hash(body)
    assert not declared["grants_authority"] and not declared["receipt_is_current_authority"]
    after = physical_originals(engine)
    assert len(after) == 30
    changed_tables = {name for name in after if before[name] != after[name]}
    assert changed_tables == {"evidence_items", "policy_proposals"}
    assert len(after["evidence_items"]) == len(before["evidence_items"]) + 1
    assert len(after["policy_proposals"]) == len(before["policy_proposals"]) + 1
    assert client.post(base, json=body).json() == declared
    found = client.get(lookup).json()
    assert found["status"] == "RECORDED" and found["record"] == declared
    assert physical_originals(engine) == after
    for invalid in (
        body | {"expected_epoch_id": str(uuid4())},
        body | {"configuration": {"type": "emergency_buffer", "amount_cents": 123008}},
        body | {"source_proposal_id": str(uuid4())},
    ):
        assert client.post(base, json=invalid).status_code == 409
    assert physical_originals(engine) == after

    url = f"/api/v1/policy-proposals/{declared['proposal_id']}/confirm"
    confirmed = client.post(
        url, json={"accepted": True, "reviewed_hash": declared["configuration_hash"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_rows = physical_originals(engine)
    original = client.get(lookup)
    assert original.status_code == 200, original.text
    assert original.json()["record"]["status"] == "PROPOSED"
    assert original.json()["record"]["current_proposal_status"] == "CONFIRMED"
    assert original.json()["record"]["original_request"] == body
    assert client.post(base, json=body).json() == original.json()["record"]
    assert physical_originals(engine) == confirmed_rows


def test_new_user_candidate_preserves_source_proposal_and_rejects_current_tampered_evidence(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    epoch_id = client.get("/api/v1/demo/state").json()["epoch_id"]
    source = client.post("/api/v1/policies/compile", json={"text": "保留2000元应急金"}).json()
    source_id = source["proposal_id"]
    before = physical_originals(engine)
    body = {
        "configuration": {"type": "emergency_buffer", "amount_cents": 234007},
        "idempotency_key": "original-source-proposal-edit",
        "expected_epoch_id": epoch_id,
        "source_proposal_id": source_id,
    }
    base = "/api/v1/policy-declarations"
    response = client.post(base, json=body)
    assert response.status_code == 200, response.text
    record = response.json()
    after = physical_originals(engine)
    assert all(row in after["policy_proposals"] for row in before["policy_proposals"])
    assert all(row in after["evidence_items"] for row in before["evidence_items"])
    declaration_evidence = next(
        row for row in after["evidence_items"] if str(row["id"]) == record["evidence_id"]
    )
    assert declaration_evidence["evidence_level"] == "USER_DECLARED"
    assert declaration_evidence["content"]["source_proposal_snapshot"]["id"] == source_id
    with Session(engine) as session, session.begin():
        evidence = session.get(EvidenceItem, UUID(record["evidence_id"]))
        assert evidence is not None
        content = deepcopy(evidence.content)
        content["configuration"]["amount_cents"] += 1
        evidence.content = content
    tampered = physical_originals(engine)
    lookup = f"{base}/{epoch_id}/by-key/{body['idempotency_key']}"
    assert client.get(lookup).status_code == 409
    assert client.post(base, json=body).status_code == 409
    confirmation = client.post(
        f"/api/v1/policy-proposals/{record['proposal_id']}/confirm",
        json={"accepted": True, "reviewed_hash": record["configuration_hash"]},
    )
    assert confirmation.status_code == 422
    assert physical_originals(engine) == tampered
