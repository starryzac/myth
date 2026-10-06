"""Independent public history probes against real action chains and PostgreSQL."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import app.services.execution as execution
import pytest
from app.api.dependencies import get_demo_user, get_now
from app.db.models import ActionPlan, DecisionConstraint, EvidenceItem, User
from app.domain.policy_configuration import configuration_hash
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF
from app.services.execution_bank import BankOperationResult, process_operation
from app.services.execution_exposure import refresh_execution_exposure
from app.services.policy_lifecycle import revoke_policy
from app.tests.test_execution_api_audit import prepared_transfer
from app.tests.test_goal_api import NOW, all_tables
from app.tests.test_goal_api import goal_client as goal_client
from app.tests.test_recovery_service import recovery_fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def original_run(engine: Engine, action: dict[str, Any]) -> UUID:
    with Session(engine) as session:
        row = session.get(ActionPlan, UUID(str(action["action_id"])))
        assert row is not None and row.user_id == DEMO_USER_ID
        return row.decision_run_id


def test_public_original_decision_and_explanation_are_readonly(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    run_id = original_run(engine, action)
    before = all_tables(engine)
    first = client.get(f"/api/v1/decisions/{run_id}")
    assert first.status_code == 200, first.text
    explanation = client.get(f"/api/v1/decisions/{run_id}/explanation")
    assert explanation.status_code == 200, explanation.text
    assert client.get(f"/api/v1/decisions/{run_id}").json() == first.json()
    assert client.get(f"/api/v1/decisions/{run_id}/explanation").json() == explanation.json()
    assert all_tables(engine) == before


def test_original_source_rehash_cannot_rewrite_recorded_decision(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    run_id = original_run(engine, action)
    assert client.get(f"/api/v1/decisions/{run_id}").status_code == 200
    with Session(engine) as session, session.begin():
        proof = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.user_id == DEMO_USER_ID,
                EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                EvidenceItem.content["account_id"].as_string()
                == action["effect"]["cash_uses"][0]["account_id"],
                EvidenceItem.status == "VALID",
            )
        ).one()
        proof.content = {**proof.content, "balance_cents": proof.content["balance_cents"] + 1}
        proof.content_hash = configuration_hash(proof.content)
    before = all_tables(engine)
    for suffix in ["", "/explanation"]:
        response = client.get(f"/api/v1/decisions/{run_id}{suffix}")
        assert response.status_code == 409, response.text
    assert all_tables(engine) == before


def test_cross_user_history_is_404_and_does_not_touch_any_business_table(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    run_id = original_run(engine, prepared_transfer(client, engine))
    own = client.get(f"/api/v1/decisions/{run_id}")
    assert own.status_code == 200, own.text
    with Session(engine) as session, session.begin():
        foreign = User(
            id=uuid4(),
            external_ref="trace-audit-foreign",
            display_name="其他模拟用户",
            is_simulated=True,
        )
        session.add(foreign)
        session.flush()
        session.expunge(foreign)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_demo_user] = lambda: foreign
    before = all_tables(engine)
    for suffix in ["", "/explanation"]:
        assert client.get(f"/api/v1/decisions/{run_id}{suffix}").status_code == 404
    assert all_tables(engine) == before


def test_legal_source_supersession_keeps_frozen_trace_and_explanation(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    run_id = original_run(engine, action)
    first = client.get(f"/api/v1/decisions/{run_id}")
    assert first.status_code == 200, first.text
    saved = first.json()
    proof_id = UUID(saved["trace"]["sources"][0]["id"])
    with Session(engine) as session, session.begin():
        proof = session.get(EvidenceItem, proof_id)
        assert proof is not None and proof.status == "VALID"
        proof.status = "SUPERSEDED"
    before = all_tables(engine)
    later = client.get(f"/api/v1/decisions/{run_id}")
    assert later.status_code == 200, later.text
    assert later.json()["trace"] == saved["trace"]
    assert later.json()["explanation"] == saved["explanation"]
    assert later.json()["current_references"] != saved["current_references"]
    assert all_tables(engine) == before


@pytest.mark.parametrize("column", ["satisfied", "available_cents"])
def test_constraint_projection_corruption_is_rejected_without_repair(
    goal_client: tuple[TestClient, Engine], column: str
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    run_id = original_run(engine, action)
    first = client.get(f"/api/v1/decisions/{run_id}")
    assert first.status_code == 200, first.text
    assert first.json()["trace"]["constraints"]
    with Session(engine) as session, session.begin():
        row = session.scalars(
            select(DecisionConstraint)
            .where(DecisionConstraint.decision_run_id == run_id)
            .order_by(DecisionConstraint.constraint_key)
            .limit(1)
        ).one()
        if column == "satisfied":
            row.satisfied = not row.satisfied
        else:
            row.available_cents = (row.available_cents or 0) + 1
    before = all_tables(engine)
    response = client.get(f"/api/v1/decisions/{run_id}")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DECISION_TRACE_INTEGRITY_ERROR"
    assert all_tables(engine) == before


def test_receipt_links_all_fresh_phases_and_keeps_original_ask_source(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    run_id = original_run(engine, action)
    confirmation = client.post(
        f"/api/v1/actions/{action['action_id']}/confirm",
        json={"effect_hash": action["effect_hash"], "accepted": True},
    )
    assert confirmation.status_code == 200, confirmation.text
    bank = process_operation
    fresh_id = uuid4()
    original_id: UUID | None = None

    def fresh_balance(
        engine: Engine, user: UUID, action_id: UUID, now: datetime
    ) -> BankOperationResult:
        nonlocal original_id
        # A committed replacement bank observation arrives after reserve phase.
        # It carries identical economics; independent bank ledger money is untouched.
        with Session(engine) as session, session.begin():
            proof = session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == user,
                    EvidenceItem.source_type == "SIMULATED_BANK_BALANCE",
                    EvidenceItem.content["account_id"].as_string()
                    == action["effect"]["cash_uses"][0]["account_id"],
                    EvidenceItem.status == "VALID",
                )
            ).one()
            original_id = proof.id
            proof.status = "SUPERSEDED"
            session.add(
                EvidenceItem(
                    id=fresh_id,
                    user_id=user,
                    created_at=now,
                    evidence_level=proof.evidence_level,
                    source_type=proof.source_type,
                    source_ref="trace-audit-fresh-bank-observation",
                    content={**proof.content},
                    content_hash=proof.content_hash,
                    status="VALID",
                    valid_from=now,
                    observed_at=now,
                    supersedes_id=proof.id,
                )
            )
            session.flush()
            refresh_execution_exposure(session, user, now, fresh_id)
        return bank(engine, user, action_id, now)

    monkeypatch.setattr(execution, "process_operation", fresh_balance)
    executed = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert executed.status_code == 200, executed.text
    receipt_id = executed.json()["receipt"]["receipt_id"]
    before = all_tables(engine)
    response = client.get(f"/api/v1/actions/{action['action_id']}/decision")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == str(run_id) and body["completeness"] == "COMPLETE"
    traces = [body["trace"]]
    for child_id in body["children"]:
        child = client.get(f"/api/v1/decisions/{child_id}")
        assert child.status_code == 200, child.text
        trace = child.json()["trace"]
        assert trace["parent_run_id"] == str(run_id)
        traces.append(trace)
    by_phase = {trace["phase"]: trace for trace in traces}
    assert set(by_phase) == {"PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"}
    assert len(traces) == 4
    for trace in traces:
        assert trace["action_id"] == action["action_id"]
        assert trace["outcome"]["autonomy_level"] == "ASK_ONCE"
        assert trace["inputs"]["effect"]["operation_id"] == action["action_id"]
        assert trace["outcome"]["validation"]["effect_hash"] == action["effect_hash"]
        assert len(trace["outcome"]["validation"]["baseline_boundary"]["calculation_trace"]) == 273
        assert len(trace["outcome"]["validation"]["projected_boundary"]["calculation_trace"]) == 273
    assert by_phase["PREPARE"]["inputs"]["confirmation"] is None
    assert (
        by_phase["RESERVE"]["inputs"]["execution_context"]["snapshot"]["source_digest"]
        != by_phase["BANK_ACCEPT"]["inputs"]["execution_context"]["snapshot"]["source_digest"]
    )
    assert str(original_id) in {s["id"] for s in by_phase["RESERVE"]["sources"]}
    assert str(fresh_id) not in {s["id"] for s in by_phase["RESERVE"]["sources"]}
    assert str(fresh_id) in {s["id"] for s in by_phase["BANK_ACCEPT"]["sources"]}
    for phase in ["CONFIRM", "RESERVE", "BANK_ACCEPT"]:
        trace = by_phase[phase]
        grant = trace["inputs"]["confirmation"]
        assert grant["effect_hash"] == action["effect_hash"]
        proofs = [
            source
            for source in trace["sources"]
            if source["source_type"] == "USER_ACTION_CONFIRMATION"
        ]
        assert len(proofs) == 1
        assert proofs[0]["content"]["accepted"] is True
        assert proofs[0]["content"]["effect_hash"] == action["effect_hash"]
    assert any(link["receipt_id"] == receipt_id for link in body["actions"])
    assert all_tables(engine) == before
    monkeypatch.setattr(execution, "process_operation", bank)
    replay = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert replay.status_code == 200 and replay.json()["receipt"]["receipt_id"] == receipt_id
    assert all_tables(engine) == before


@pytest.mark.parametrize("goal_client", ["legacy-income"], indirect=True)
def test_t1_history_get_preserves_waiting_cash_and_does_not_settle_revoked_action(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    policy_id, position_id, _ = recovery_fixture(engine, delay=1)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_now] = lambda: SEED_AS_OF
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "trace-audit-t1",
            "intent": {"kind": "redeem_asset", "position_id": str(position_id)},
        },
    )
    assert prepared.status_code == 200, prepared.text
    action = prepared.json()
    run_id = original_run(engine, action)
    accepted = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["bank_status"] == "ACCEPTED"
    assert accepted.json()["receipt"] is None
    first = client.get(f"/api/v1/decisions/{run_id}")
    assert first.status_code == 200, first.text
    saved = first.json()
    assert (
        saved["trace"]["outcome"]["validation"]["baseline_boundary"]["status"] == "LIQUIDITY_RISK"
    )
    with Session(engine) as session, session.begin():
        version = session.get(ActionPlan, UUID(action["action_id"]))
        assert version is not None and version.policy_version_id is not None
        revoke_policy(session, DEMO_USER_ID, policy_id, version.policy_version_id, NOW)
    api.dependency_overrides[get_now] = lambda: SEED_AS_OF + timedelta(days=1)
    before = all_tables(engine)
    later = client.get(f"/api/v1/decisions/{run_id}")
    assert later.status_code == 200, later.text
    body = later.json()
    assert body["trace"] == saved["trace"]
    assert body["explanation"] == saved["explanation"]
    assert body["actions"][0]["bank_status"] == "ACCEPTED"
    assert body["actions"][0]["receipt_id"] is None
    assert body["as_of"] == saved["as_of"]
    assert body["read_at"] != saved["read_at"]
    assert all_tables(engine) == before


def test_unknown_lost_response_keeps_original_committed_bank_trace_on_retry(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    run_id = original_run(engine, action)
    confirmed = client.post(
        f"/api/v1/actions/{action['action_id']}/confirm",
        json={"effect_hash": action["effect_hash"], "accepted": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    bank = process_operation

    def lost_response(
        engine: Engine, user: UUID, action_id: UUID, now: datetime
    ) -> BankOperationResult:
        bank(engine, user, action_id, now)
        raise RuntimeError("audit response lost after independent bank commit")

    monkeypatch.setattr(execution, "process_operation", lost_response)
    lost = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert lost.status_code == 500, lost.text
    before = all_tables(engine)
    read = client.get(f"/api/v1/decisions/{run_id}")
    assert read.status_code == 200, read.text
    saved = read.json()
    link = saved["actions"][0]
    assert link["status"] == "UNKNOWN"
    assert link["bank_status"] == "SETTLED"
    assert link["receipt_id"] is None
    children = {}
    for child_id in saved["children"]:
        response = client.get(f"/api/v1/decisions/{child_id}")
        assert response.status_code == 200, response.text
        children[child_id] = response.json()["trace"]
    assert {trace["phase"] for trace in children.values()} == {
        "CONFIRM",
        "RESERVE",
        "BANK_ACCEPT",
    }
    assert all_tables(engine) == before
    monkeypatch.setattr(execution, "process_operation", bank)
    retry = client.post(f"/api/v1/actions/{action['action_id']}/execute", json={})
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "SUCCEEDED"
    after = all_tables(engine)
    recovered = client.get(f"/api/v1/decisions/{run_id}")
    assert recovered.status_code == 200, recovered.text
    body = recovered.json()
    assert body["trace"] == saved["trace"]
    assert body["children"] == saved["children"]
    assert body["actions"][0]["bank_operation_id"] == link["bank_operation_id"]
    assert body["actions"][0]["receipt_id"] == retry.json()["receipt"]["receipt_id"]
    for child_id, trace in children.items():
        response = client.get(f"/api/v1/decisions/{child_id}")
        assert response.status_code == 200 and response.json()["trace"] == trace
    assert all_tables(engine) == after
