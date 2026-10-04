"""Independent real-PG audit probes through existing workflow commands."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from time import monotonic, sleep
from typing import Any
from uuid import UUID

import pytest
from app.api.dependencies import get_now
from app.db.audit_guard import gate_key
from app.db.models import ActionPlan, ActionReceipt, AuditEpoch, AuditEvent, BankOperation
from app.services import execution
from app.services.action_contracts import GoalIntent, PrepareActionRequest
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.services.execution import execute_action, prepare_action
from app.services.execution_bank import BankOperationResult, process_operation
from app.services.policy_lifecycle import revoke_policy
from app.services.recovery import get_recovery_run, run_recovery
from app.tests.test_execution_api_audit import prepared_transfer
from app.tests.test_execution_goal_creation import public_zero_goal
from app.tests.test_goal_api import NOW, all_tables, confirmed_goal_request
from app.tests.test_goal_api import goal_client as goal_client
from app.tests.test_recovery_service import recovery_fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def events(engine: Engine) -> list[dict[str, Any]]:
    with Session(engine) as session:
        return [
            {
                "id": row.id,
                "type": row.event_type,
                "run_id": row.decision_run_id,
                "action_id": row.action_plan_id,
                "receipt_id": row.action_receipt_id,
                "payload": row.payload,
            }
            for row in session.scalars(
                select(AuditEvent).order_by(AuditEvent.created_at, AuditEvent.id)
            )
        ]


def test_prepared_action_has_one_original_decision_and_complete_request_anchor(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    recorded = events(engine)
    assert len([item for item in recorded if item["type"] == "DECISION_RECORDED"]) == 1
    assert len([item for item in recorded if item["type"] == "ACTION_CREATED"]) == 1
    unchanged = all_tables(engine)
    history = client.get(f"/api/v1/actions/{action['action_id']}/decision")
    assert history.status_code == 200, history.text
    assert history.json()["audit_chain_status"] == "VALID", history.text
    assert history.json()["explanation"]["audit_chain"] == "NOT_IMPLEMENTED"
    assert client.get(f"/api/v1/actions/{action['action_id']}").status_code == 200
    repeated = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "independent-transfer",
            "intent": {
                "kind": "transfer_internal",
                "source_account_id": action["effect"]["cash_uses"][0]["account_id"],
                "destination_account_id": action["effect"]["destination_account_id"],
                "amount_cents": 12345,
            },
        },
    )
    assert repeated.status_code == 200 and repeated.json()["action_id"] == action["action_id"]
    assert all_tables(engine) == unchanged


def test_legacy_t1_has_one_unified_acceptance_and_settlement_and_readonly_wait(
    goal_client: tuple[TestClient, Engine],
) -> None:
    _, engine = goal_client
    recovery_fixture(engine, delay=1)
    pending = run_recovery(engine, DEMO_USER_ID, "audit-legacy-t1", SEED_AS_OF)
    assert pending.status == "PENDING_SETTLEMENT" and len(pending.actions) == 1
    identity = pending.actions[0].action_id
    recorded = [item for item in events(engine) if item["action_id"] == identity]
    assert [item["type"] for item in recorded].count("BANK_ACCEPTED") == 1
    assert not any(item["type"] == "BANK_SETTLED" for item in recorded)
    assert not any(item["type"] == "ACTION_PROJECTED" for item in recorded)
    before = all_tables(engine)
    later = SEED_AS_OF + timedelta(days=1)
    with Session(engine) as session:
        observed = get_recovery_run(session, DEMO_USER_ID, pending.run_id, later)
    assert observed.status == "PENDING_SETTLEMENT" and observed.actions[0].receipt_id is None
    assert all_tables(engine) == before
    settled = run_recovery(engine, DEMO_USER_ID, "audit-legacy-t1", later)
    assert settled.status == "RECOVERED"
    recorded = [item for item in events(engine) if item["action_id"] == identity]
    for kind in ["ACTION_CREATED", "BANK_ACCEPTED", "BANK_SETTLED", "ACTION_PROJECTED"]:
        assert [item["type"] for item in recorded].count(kind) == 1
    unchanged = all_tables(engine)
    assert run_recovery(engine, DEMO_USER_ID, "audit-legacy-t1", later).status == "RECOVERED"
    assert all_tables(engine) == unchanged


def test_one_real_transfer_records_four_decisions_and_one_bank_and_receipt_effect(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    identity = UUID(action["action_id"])
    confirmed = client.post(
        f"/api/v1/actions/{identity}/confirm",
        json={"effect_hash": action["effect_hash"], "accepted": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    executed = client.post(f"/api/v1/actions/{identity}/execute", json={})
    assert executed.status_code == 200, executed.text
    receipt_id = UUID(executed.json()["receipt"]["receipt_id"])
    recorded = [item for item in events(engine) if item["action_id"] == identity]
    assert [item["type"] for item in recorded].count("DECISION_RECORDED") == 4
    for kind in ["ACTION_CREATED", "BANK_ACCEPTED", "BANK_SETTLED", "ACTION_PROJECTED"]:
        assert [item["type"] for item in recorded].count(kind) == 1
    projected = next(item for item in recorded if item["type"] == "ACTION_PROJECTED")
    assert projected["receipt_id"] == receipt_id
    settlement = next(item for item in recorded if item["type"] == "BANK_SETTLED")
    original_refs = settlement["payload"]["references"]
    assert ("ACTION_PLAN", str(identity)) in {(ref["kind"], ref["id"]) for ref in original_refs}
    bank_ref = next(
        ref
        for ref in original_refs
        if ref["kind"] == "BANK_OPERATION" and ref["id"] == str(identity)
    )
    posting_anchor = next(
        anchor
        for anchor in settlement["payload"]["anchors"]
        if anchor["kind"] == "BANK_POSTING_SET"
    )
    assert posting_anchor["reference_id"] == str(identity)
    assert posting_anchor["snapshot_hash"] == bank_ref["snapshot_hash"]
    unchanged = all_tables(engine)
    assert client.post(f"/api/v1/actions/{identity}/execute", json={}).status_code == 200
    verification = client.post("/api/v1/audit/verify", json={})
    assert verification.status_code == 200, verification.text
    assert verification.json()["status"] == "VALID", verification.text
    assert all_tables(engine) == unchanged


def test_real_audit_insert_failure_rolls_back_projection_but_preserves_bank_and_unknown(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    identity = UUID(action["action_id"])
    assert (
        client.post(
            f"/api/v1/actions/{identity}/confirm",
            json={"effect_hash": action["effect_hash"], "accepted": True},
        ).status_code
        == 200
    )
    with engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION fail_test_audit_projection() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.event_type = 'ACTION_PROJECTED' THEN
                    RAISE EXCEPTION 'injected database failure before audit projection commit';
                END IF;
                RETURN NEW;
            END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER test_audit_projection_failure BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION fail_test_audit_projection()
        """)
        )
    failed = client.post(f"/api/v1/actions/{identity}/execute", json={})
    assert failed.status_code == 500, failed.text
    original = client.get(f"/api/v1/actions/{identity}").json()
    assert original["status"] == "UNKNOWN" and original["bank_status"] == "SETTLED"
    assert original["receipt"] is None
    recorded = [item for item in events(engine) if item["action_id"] == identity]
    assert [item["type"] for item in recorded].count("BANK_SETTLED") == 1
    assert not any(item["type"] == "ACTION_PROJECTED" for item in recorded)
    bank_event_ids = {item["id"] for item in recorded if item["type"].startswith("BANK_")}
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER test_audit_projection_failure ON audit_events"))
        connection.execute(text("DROP FUNCTION fail_test_audit_projection()"))
    retry = client.post(f"/api/v1/actions/{identity}/execute", json={})
    assert retry.status_code == 200 and retry.json()["status"] == "SUCCEEDED", retry.text
    recovered = [item for item in events(engine) if item["action_id"] == identity]
    assert {item["id"] for item in recovered if item["type"].startswith("BANK_")} == bank_event_ids
    assert [item["type"] for item in recovered].count("ACTION_PROJECTED") == 1


def test_policy_revocation_records_only_true_invalidation_and_original_before_status(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    policy_id, position_id, _ = recovery_fixture(engine)
    api = client.app
    assert isinstance(api, FastAPI)
    api.dependency_overrides[get_now] = lambda: SEED_AS_OF
    prepared = client.post(
        "/api/v1/actions/prepare",
        json={
            "idempotency_key": "audit-revocation",
            "intent": {"kind": "redeem_asset", "position_id": str(position_id)},
        },
    )
    assert prepared.status_code == 200, prepared.text
    identity = UUID(prepared.json()["action_id"])
    with Session(engine) as session, session.begin():
        action = session.get(ActionPlan, identity)
        assert action is not None and action.policy_version_id is not None
        version_id = action.policy_version_id
        revoke_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
    changes = [
        change
        for item in events(engine)
        if item["type"] == "ACTION_STATE_CHANGED" and item["action_id"] == identity
        for change in item["payload"]["changes"]
        if change["field"] == "status"
    ]
    assert len(changes) == 1
    assert changes[0]["before"] == "PLANNED" and changes[0]["after"] == "INVALIDATED"
    unchanged = all_tables(engine)
    with Session(engine) as session, session.begin():
        repeated = revoke_policy(session, DEMO_USER_ID, policy_id, version_id, SEED_AS_OF)
    assert identity in repeated.invalidated_action_ids
    assert all_tables(engine) == unchanged


def test_explicit_policy_confirmation_and_zero_goal_are_once_and_do_not_move_money(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    body = confirmed_goal_request(client, engine)
    before_money = client.get("/api/v1/accounts/summary").json()
    created = client.post("/api/v1/goals", json=body)
    assert created.status_code == 200, created.text
    kinds = [item["type"] for item in events(engine)]
    assert kinds.count("POLICY_VERSION_CONFIRMED") == 1
    assert kinds.count("GOAL_INITIALIZED") == 1
    assert client.get("/api/v1/accounts/summary").json() == before_money
    unchanged = all_tables(engine)
    assert client.post("/api/v1/goals", json=body).json() == created.json()
    assert all_tables(engine) == unchanged


def test_income_augmented_action_anchors_the_actual_final_request_and_executes(
    goal_client: tuple[TestClient, Engine],
) -> None:
    client, engine = goal_client
    goal_id, _, _, _ = public_zero_goal(engine, import_income=False)
    action = prepare_action(
        engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="audit-income-goal",
            intent=GoalIntent(kind="allocate_goal", goal_id=goal_id),
        ),
        SEED_AS_OF,
    )
    assert action.effect.income_uses
    with Session(engine) as session:
        original = session.get(ActionPlan, action.action_id)
        assert original is not None and original.request["income_evidence"]["id"]
        digest = original.request_hash
    created = next(
        item
        for item in events(engine)
        if item["type"] == "ACTION_CREATED" and item["action_id"] == action.action_id
    )
    request_anchor = next(
        anchor for anchor in created["payload"]["anchors"] if anchor["kind"] == "EXECUTION_REQUEST"
    )
    assert request_anchor["digest"] == digest
    completed = execute_action(engine, DEMO_USER_ID, action.action_id, SEED_AS_OF)
    assert completed.status == "SUCCEEDED" and completed.effect.amount_cents == 10000
    unchanged = all_tables(engine)
    verification = client.post("/api/v1/audit/verify", json={})
    assert verification.status_code == 200, verification.text
    assert verification.json()["status"] == "VALID", verification.text
    assert all_tables(engine) == unchanged


def test_real_reset_waits_across_bank_commit_and_original_application_projection(
    goal_client: tuple[TestClient, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = goal_client
    action = prepared_transfer(client, engine)
    identity = UUID(action["action_id"])
    confirmation = client.post(
        f"/api/v1/actions/{identity}/confirm",
        json={"effect_hash": action["effect_hash"], "accepted": True},
    )
    assert confirmation.status_code == 200, confirmation.text
    bank_committed, resume_projection = Event(), Event()
    phase_marks: dict[str, float] = {}
    futures: dict[str, Any] = {}

    def run_actual_action() -> Any:
        phase_marks["action_started"] = monotonic()
        try:
            return execute_action(engine, DEMO_USER_ID, identity, NOW)
        finally:
            phase_marks["action_finished"] = monotonic()

    def run_actual_reset() -> Any:
        phase_marks["reset_started"] = monotonic()
        try:
            return seed_demo(engine, reset_key="real-command-gate")
        finally:
            phase_marks["reset_finished"] = monotonic()

    def pause_after_real_bank_commit(
        original_engine: Engine, user_id: UUID, action_id: UUID, now: datetime
    ) -> BankOperationResult:
        phase_marks["bank_started"] = monotonic()
        operation = process_operation(original_engine, user_id, action_id, now)
        phase_marks["bank_finished"] = monotonic()
        bank_committed.set()
        assert resume_projection.wait(240), "The test must release the real application phase"
        return operation

    monkeypatch.setattr(execution, "process_operation", pause_after_real_bank_commit)
    with Session(engine) as session:
        epoch_id = session.scalar(select(AuditEpoch.id).where(AuditEpoch.status == "OPEN"))
    assert epoch_id is not None
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            action_future = workers.submit(run_actual_action)
            futures["action"] = action_future
            try:
                ready_deadline = monotonic() + 240
                while not bank_committed.wait(0.1):
                    if action_future.done():
                        terminal = action_future.result()
                        pytest.fail(
                            f"Action finished before the real bank phase: {terminal.status}"
                        )
                    assert monotonic() < ready_deadline, "Real bank phase did not commit"
                with Session(engine) as session:
                    operation = session.get(BankOperation, identity)
                    assert operation is not None and operation.status == "SETTLED"
                    assert session.scalar(select(ActionReceipt.id)) is None
                reset_future = workers.submit(run_actual_reset)
                futures["reset"] = reset_future
                deadline = monotonic() + 10
                key = gate_key(DEMO_USER_ID)
                reset_waiting = False
                while monotonic() < deadline:
                    with engine.connect() as connection:
                        reset_waiting = bool(
                            connection.scalar(
                                text("""
                                SELECT EXISTS (
                                    SELECT 1 FROM pg_locks
                                    WHERE locktype = 'advisory' AND mode = 'ExclusiveLock'
                                        AND NOT granted AND classid = :high AND objid = :low
                                        AND database = (
                                            SELECT oid FROM pg_database
                                            WHERE datname = current_database()
                                        )
                                )
                                """),
                                {"high": key >> 32, "low": key & 0xFFFFFFFF},
                            )
                        )
                    if reset_waiting:
                        break
                    sleep(0.02)
                assert reset_waiting and not reset_future.done()
            finally:
                phase_marks["projection_released"] = monotonic()
                resume_projection.set()
            completed = action_future.result(timeout=240)
            assert completed.status == "SUCCEEDED" and completed.receipt is not None
            # Full frozen trace archives contain the original 273-point projections.
            # The test's lock proof above is independent of archive serialization time.
            assert reset_future.result(timeout=600).user_id == DEMO_USER_ID
    finally:
        phase_pairs = {
            "reserve_and_command_guard": ("action_started", "bank_started"),
            "bank_transaction": ("bank_started", "bank_finished"),
            "application_projection": ("projection_released", "action_finished"),
            "reset_gate_archive_seed": ("reset_started", "reset_finished"),
        }
        print(
            "Real reset synchronization diagnostics (test budgets, not a service SLA):",
            {
                "budgets_seconds": {"ready": 240, "resume": 240, "projection": 240, "archive": 600},
                "phase_seconds": {
                    phase: round(phase_marks[end] - phase_marks[start], 3)
                    if start in phase_marks and end in phase_marks
                    else None
                    for phase, (start, end) in phase_pairs.items()
                },
                "future_states": {
                    name: {
                        "done": future.done(),
                        "running": future.running(),
                        "cancelled": future.cancelled(),
                    }
                    for name, future in futures.items()
                },
            },
        )
    with Session(engine) as session:
        assert session.get(BankOperation, identity) is None
        old_epoch = session.get(AuditEpoch, epoch_id)
        assert old_epoch is not None and old_epoch.status == "SEALED"
        original_kinds = list(
            session.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.epoch_id == epoch_id, AuditEvent.action_plan_id == identity
                )
            )
        )
    for kind in ["BANK_ACCEPTED", "BANK_SETTLED", "ACTION_PROJECTED"]:
        assert original_kinds.count(kind) == 1
