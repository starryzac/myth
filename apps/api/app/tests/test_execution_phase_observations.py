"""Real isolated PostgreSQL phase risks, never formal 24-case evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from app.db.base import Base
from app.db.models import ActionPlan, ActionReceipt, ActionResourceReservation, BankOperation
from app.db.settings import REPOSITORY_ROOT
from app.services import execution
from app.services.demo_seed import DEMO_USER_ID
from app.services.execution_bank import process_operation
from app.services.execution_projection import project_execution
from app.tests.test_boundary_service import boundary_engine, snapshot
from app.tests.test_experiment_arms import Registration
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scripts.mvp_arm_executor import OriginalReader

__all__ = ["boundary_engine"]
pytestmark = pytest.mark.integration


def phases(reg: Registration, capture: dict[str, Any]) -> list[dict[str, Any]]:
    context = reg.context_at(1)
    reader = OriginalReader(context, capture["original_paths"])
    envelope = reader.resolve(capture["artifact_ref"])
    assert envelope["service_call_id"] == envelope["payload"]["service_call_id"]
    refs = envelope["payload"]["live_phase_observation_refs"]
    result: list[dict[str, Any]] = [reader.resolve(ref) for ref in refs]
    for row in result:
        assert row["bindings"] == context.bindings
        assert row["user_id"] == str(DEMO_USER_ID)
        assert row["protocol"] == "execution-live-phase-observation-v2"
        assert row["service_call_id"] == envelope["payload"]["service_call_id"]
        assert row["financial_effect_verified"] is False
    return result


def rechecks(reg: Registration, capture: dict[str, Any]) -> list[dict[str, Any]]:
    context = reg.context_at(1)
    reader = OriginalReader(context, capture["original_paths"])
    payload = reader.resolve(capture["artifact_ref"])["payload"]
    terminals = {}
    for ref in payload["live_phase_observation_refs"]:
        row = reader.resolve(ref)
        if row["state"] in {"COMMITTED", "COMMITTED_THEN_ERROR", "ROLLED_BACK"}:
            terminals[row["phase_id"]] = (row, ref)
    result: list[dict[str, Any]] = []
    for ref in payload["independent_phase_recheck_refs"]:
        row = reader.resolve(ref)
        original, original_ref = terminals[row["phase_id"]]
        assert row["protocol"] == "execution-independent-phase-recheck-v2"
        assert row["bindings"] == context.bindings
        assert row["service_call_id"] == payload["service_call_id"]
        assert row["user_id"] == str(DEMO_USER_ID)
        assert row["action_id"] == payload["action_id"]
        assert row["epoch_id"] == context.bindings["isolated_db_epoch"]
        assert row["phase"] == original["phase"]
        assert row["terminal_state"] == original["state"]
        assert row["terminal_value_sha256"] == original_ref["value_sha256"]
        assert row["target_sql_identity"] == original["sql_identity"]
        assert row["probe_sql_identity"]["database_name"] == context.database_name
        assert row["probe_sql_identity"]["isolation"] == "repeatable read"
        assert row["probe_sql_identity"]["read_only"] == "on"
        assert (
            row["probe_sql_identity"]["transaction_id"]
            != row["target_sql_identity"]["transaction_id"]
        )
        assert row["probe_sql_identity"]["backend_pid"] != row["target_sql_identity"]["backend_pid"]
        assert row["capability"]["procedure"] == "pg_xact_status(xid8)"
        assert row["capability"]["may_execute"] is True
        assert row["capture_state"] == "CAPTURED_VALIDATOR_PENDING"
        assert row["financial_effect_verified"] is False
        assert row["independent_phase_proof_verified"] is False
        assert row["readonly_outer_context_exited"] == "NORMAL"
        assert datetime.fromisoformat(row["business_clock"]) == datetime.fromisoformat(context.now)
        assert datetime.fromisoformat(row["started_at"]) >= datetime.fromisoformat(
            original["observed_at"]
        )
        assert datetime.fromisoformat(row["finished_at"]) >= datetime.fromisoformat(
            row["started_at"]
        )
        assert set(row["tables"]) == {
            "action_plans",
            "action_resource_reservations",
            "bank_operations",
            "action_receipts",
            "simulated_bank_postings",
            "accounts",
            "evidence_items",
            "users",
        }
        assert len(row["tables"]["users"]) == 1
        assert row["tables"]["users"][0]["id"] == str(DEMO_USER_ID)
        assert row["tables"]["users"][0]["is_simulated"] is True
        for name, rows in row["tables"].items():
            assert row["table_inventory"][name]["row_ids"] == [item["id"] for item in rows]
            assert (
                row["table_inventory"][name]["sha256"]
                == hashlib.sha256(
                    json.dumps(
                        rows,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
            )
        result.append(row)
    return result


def run(reg: Registration) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = reg.prepare()
    assert prepared["outcome"] == "PREPARED"
    response = prepared["response"]
    capture = reg.provider.execute_arm_action(reg.context_at(1), response["action_id"])
    return response, capture


def test_three_actual_outer_commits_have_distinct_sql_transaction_ids(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine, "P")
    response, capture = run(reg)
    rows = phases(reg, capture)
    reader = OriginalReader(reg.context_at(1), capture["original_paths"])
    payload = reader.resolve(capture["artifact_ref"])["payload"]
    for name in ("before_snapshot_ref", "after_snapshot_ref"):
        tables = reader.resolve(payload[name])
        assert set(tables) == {table.name for table in Base.metadata.sorted_tables}
        assert len(tables) == 23
        assert len(tables["users"]) == 1 and tables["users"][0]["id"] == str(DEMO_USER_ID)
        assert tables["users"][0]["is_simulated"] is True
    committed = [row for row in rows if row["state"] == "COMMITTED"]
    assert {row["phase"] for row in committed} == {
        "APPLICATION_RESERVATION",
        "INDEPENDENT_BANK",
        "APPLICATION_PROJECTION",
    }
    assert len(committed) == 3
    assert len({row["sql_identity"]["transaction_id"] for row in committed}) == 3
    assert all(row["outer_after_commit_observed"] is True for row in committed)
    assert all(row["outer_after_rollback_observed"] is False for row in committed)
    assert all(row["live"]["nested_savepoint_active"] is False for row in committed)
    projected = next(row for row in committed if row["phase"] == "APPLICATION_PROJECTION")
    assert len(projected["live"]["tables"]["action_receipts"]) == 1
    assert (
        projected["live"]["tables"]["action_plans"][0]["request"]["execution"]["effect_hash"]
        == response["effect_hash"]
    )
    nested = next(row for row in rows if row["state"] == "APPLICATION_SAVEPOINT_APPLIED")
    assert nested["live"]["nested_savepoint_active"] is True
    assert nested["outer_after_commit_observed"] is False
    independent = rechecks(reg, capture)
    assert len(independent) == 3
    assert {row["phase"] for row in independent} == {row["phase"] for row in committed}
    assert len({row["service_call_id"] for row in [*rows, *independent]}) == 1
    assert len({row["target_sql_identity"]["transaction_id"] for row in independent}) == 3
    assert len({row["probe_sql_identity"]["transaction_id"] for row in independent}) == 3
    assert all(
        row["target_status"] == row["expected_target_status"] == "committed" for row in independent
    )


def test_actual_bank_commit_survives_original_lost_response(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registration(boundary_engine, "P")

    def loss(engine: Engine, owner: UUID, action: UUID, now: datetime) -> Any:
        result = process_operation(engine, owner, action, now)
        raise ConnectionError("PHASE_RISK_ONLY_AFTER_ACTUAL_BANK_COMMIT:" + result.status)

    monkeypatch.setattr(execution, "process_operation", loss)
    response, capture = run(reg)
    rows = phases(reg, capture)
    assert {row["phase"] for row in rows if row["state"] == "COMMITTED"} == {
        "APPLICATION_RESERVATION",
        "INDEPENDENT_BANK",
    }
    assert not any(row["phase"] == "APPLICATION_PROJECTION" for row in rows)
    with Session(boundary_engine) as session:
        action = session.get(ActionPlan, UUID(response["action_id"]))
        bank = session.get(BankOperation, UUID(response["action_id"]))
        assert action is not None and bank is not None
        assert action.status == "UNKNOWN" and bank.status == "SETTLED"
        assert action.request["execution"]["effect_hash"] == response["effect_hash"]
        assert list(session.scalars(select(ActionReceipt))) == []
        assert list(session.scalars(select(ActionResourceReservation)))


def test_projection_savepoint_success_then_outer_failure_has_no_commit_proof(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registration(boundary_engine, "P")
    original = project_execution

    def after_savepoint(session: Session, operation: BankOperation, now: datetime) -> Any:
        original(session, operation, now)  # Actual financial projection, real nested savepoint.
        raise RuntimeError("PHASE_RISK_ONLY_AFTER_ORIGINAL_PROJECTION_SAVEPOINT")

    monkeypatch.setattr(execution, "project_execution", after_savepoint)
    response, capture = run(reg)
    projected = [row for row in phases(reg, capture) if row["phase"] == "APPLICATION_PROJECTION"]
    assert any(row["state"] == "APPLICATION_SAVEPOINT_APPLIED" for row in projected)
    assert not any(row["state"] == "COMMITTED" for row in projected)
    rollback = next(row for row in projected if row["state"] == "ROLLED_BACK")
    assert rollback["outer_after_rollback_observed"] is True
    assert rollback["outer_after_commit_observed"] is False
    independent = rechecks(reg, capture)
    assert len(independent) == 3
    assert len({row["service_call_id"] for row in independent}) == 1
    projected_recheck = next(row for row in independent if row["phase"] == "APPLICATION_PROJECTION")
    assert (
        projected_recheck["target_status"]
        == projected_recheck["expected_target_status"]
        == "aborted"
    )
    assert projected_recheck["tables"]["action_receipts"] == []
    bank_recheck = next(row for row in independent if row["phase"] == "INDEPENDENT_BANK")
    assert bank_recheck["target_status"] == bank_recheck["expected_target_status"] == "committed"
    assert len(bank_recheck["tables"]["bank_operations"]) == 1
    assert bank_recheck["tables"]["bank_operations"][0]["id"] == response["action_id"]
    assert bank_recheck["tables"]["bank_operations"][0]["status"] == "SETTLED"
    with Session(boundary_engine) as session:
        action = session.get(ActionPlan, UUID(response["action_id"]))
        bank = session.get(BankOperation, UUID(response["action_id"]))
        assert action is not None and action.status == "UNKNOWN"
        assert bank is not None and bank.status == "SETTLED"
        assert list(session.scalars(select(ActionReceipt))) == []


def test_after_bank_commit_capture_failure_preserves_original_economics(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registration(boundary_engine, "P")
    original = reg.provider.CaptureWriter.write

    def write(self: Any, kind: str, payload: dict[str, Any], pointer: str = "") -> Any:
        ref = original(self, kind, payload, pointer)
        if (
            kind == "ACTUAL_LIVE_PHASE"
            and payload["phase"]["phase"] == "INDEPENDENT_BANK"
            and payload["phase"]["state"] == "COMMITTED"
        ):
            raise OSError("PHASE_RISK_ONLY_CAPTURE_FAILED_AFTER_REAL_BANK_COMMIT")
        return ref

    monkeypatch.setattr(reg.provider.CaptureWriter, "write", write)
    response, capture = run(reg)
    reader = OriginalReader(reg.context_at(1), capture["original_paths"])
    payload = reader.resolve(capture["artifact_ref"])["payload"]
    error = reader.resolve(payload["error_ref"])
    assert any("OBSERVATION_FAILED_AFTER_COMMIT" in note for note in error["notes"])
    with Session(boundary_engine) as session:
        action = session.get(ActionPlan, UUID(response["action_id"]))
        bank = session.get(BankOperation, UUID(response["action_id"]))
        assert action is not None and action.status == "UNKNOWN"
        assert bank is not None and bank.status == "SETTLED"
        assert bank.request["effect_hash"] == response["effect_hash"]


def test_before_reservation_commit_capture_failure_actually_rolls_back(
    boundary_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = Registration(boundary_engine, "P")
    prepared = reg.prepare()
    identity = UUID(prepared["response"]["action_id"])
    original = reg.provider.CaptureWriter.write

    def write(self: Any, kind: str, payload: dict[str, Any], pointer: str = "") -> Any:
        ref = original(self, kind, payload, pointer)
        if (
            kind == "ACTUAL_LIVE_PHASE"
            and payload["phase"]["phase"] == "APPLICATION_RESERVATION"
            and payload["phase"]["state"] == "BEFORE_OUTER_COMMIT"
        ):
            raise OSError("PHASE_RISK_ONLY_CAPTURE_FAILED_BEFORE_COMMIT")
        return ref

    monkeypatch.setattr(reg.provider.CaptureWriter, "write", write)
    with pytest.raises(reg.provider.ProviderNotImplemented, match="no-bank"):
        reg.provider.execute_arm_action(reg.context_at(1), str(identity))
    with Session(boundary_engine) as session:
        action = session.get(ActionPlan, identity)
        assert action is not None and action.status == prepared["response"]["status"]
        assert list(session.scalars(select(ActionResourceReservation))) == []
        assert list(session.scalars(select(BankOperation))) == []
    # The current provider's explicit no-bank refusal cannot return a full capture;
    # preserve its actual newly written raw artifacts and verify their original bindings.
    directory = (
        REPOSITORY_ROOT / ".runtime/W1-arm-provider-originals" / reg.bindings["experiment_run_id"]
    )
    raw = [json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*/*.json")]
    records = [row["payload"]["phase"] for row in raw if row["kind"] == "ACTUAL_LIVE_PHASE"]
    assert all(row["bindings"] == reg.bindings for row in records)
    assert any(
        row["state"] == "ROLLED_BACK" and row["outer_after_rollback_observed"] is True
        for row in records
    )
    assert not any(row["state"] == "COMMITTED" for row in records)


def test_idempotent_success_does_not_invent_unreached_bank_or_projection_phases(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine, "P")
    response, _first = run(reg)
    capture = reg.provider.execute_arm_action(reg.context_at(1), response["action_id"])
    rows = phases(reg, capture)
    assert {row["phase"] for row in rows} == {"APPLICATION_RESERVATION"}
    assert len([row for row in rows if row["state"] == "COMMITTED"]) == 1


def test_default_without_observer_retains_original_pipeline_and_no_scope_cache(
    boundary_engine: Engine,
) -> None:
    reg = Registration(boundary_engine, "P")
    prepared = reg.prepare()
    before = snapshot(boundary_engine)
    import app.services.execution_observations as observations

    assert observations._BINDING.get() is None and observations._PHASE.get() is None
    result = execution.execute_action(
        boundary_engine,
        DEMO_USER_ID,
        UUID(prepared["response"]["action_id"]),
        datetime.fromisoformat(reg.context_at(1).now),
    )
    assert (
        result.status == "SUCCEEDED" and result.effect_hash == prepared["response"]["effect_hash"]
    )
    assert observations._BINDING.get() is None and observations._PHASE.get() is None
    assert snapshot(boundary_engine) != before
