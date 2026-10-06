"""Actual audit command exit codes and read-only PostgreSQL behavior."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.db.models import Account, User
from app.db.testing import require_test_database
from app.services.audit_chain import ensure_audit_epoch
from app.services.demo_seed import seed_demo
from app.tests.test_goal_api import all_tables
from app.tests.test_goal_api import goal_client as goal_client
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "verify_audit_chain.py"
SEED_SCRIPT = SCRIPT.with_name("seed_demo.py")


def run_cli(
    engine: Engine, *arguments: str, script: Path = SCRIPT
) -> tuple[int, dict[str, object]]:
    environment = {**os.environ, "DATABASE_URL": engine.url.render_as_string(False)}
    result = subprocess.run(
        [sys.executable, str(script), *arguments],
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=45,
        check=False,
    )
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_actual_cli_valid_export_checkpoint_and_exact_verification_are_readonly(
    goal_client: tuple[TestClient, Engine], tmp_path: Path
) -> None:
    _, engine = goal_client
    before = all_tables(engine)
    checkpoint = tmp_path / "original-checkpoint.json"
    code, report = run_cli(engine, "--checkpoint-output", str(checkpoint))
    assert code == 0, report
    assert report["status"] == "VALID"
    assert report["verification_scope"] == "ALL_RETAINED_EPOCHS"
    assert report["simulation"] is True
    assert report["isolation"] == "repeatable read"
    assert report["read_only"] is True
    assert report["selected_epoch_count"] == report["registered_epoch_count"] == 1
    original = checkpoint.read_bytes()
    code, report = run_cli(engine, "--checkpoint", str(checkpoint), "--mode", "EXACT")
    assert code == 0, report
    assert report["checkpoint_origin"] == "CALLER_SUPPLIED"
    assert all_tables(engine) == before
    code, report = run_cli(engine, "--checkpoint-output", str(checkpoint))
    assert code == 2 and report["status"] == "INCOMPLETE"
    assert checkpoint.read_bytes() == original
    assert all_tables(engine) == before


def test_actual_cli_missing_epoch_and_unknown_user_never_claim_valid(
    goal_client: tuple[TestClient, Engine],
) -> None:
    _, engine = goal_client
    before = all_tables(engine)
    code, report = run_cli(engine, "--epoch-id", str(uuid4()))
    assert code == 1 and report["status"] == "NOT_VERIFIED"
    code, report = run_cli(engine, "--user-id", str(uuid4()))
    assert code == 2 and report["error"] == "USER_NOT_FOUND"
    assert all_tables(engine) == before


def test_actual_cli_rejects_admin_tampered_historical_row(
    goal_client: tuple[TestClient, Engine],
) -> None:
    _, engine = goal_client
    # Only this generated test database permits deliberate owner DDL tampering.
    require_test_database(engine.url.database)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable"))
        connection.execute(text("UPDATE audit_events SET event_hash=:hash"), {"hash": "0" * 64})
        connection.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_immutable"))
    before = all_tables(engine)
    code, report = run_cli(engine)
    assert code == 1, report
    assert report["status"] == "NOT_VERIFIED"
    assert report["read_only"] is True
    assert all_tables(engine) == before


def test_actual_cli_verifies_retained_epochs_and_trusted_checkpoint_after_reset(
    goal_client: tuple[TestClient, Engine], tmp_path: Path
) -> None:
    _, engine = goal_client
    checkpoint = tmp_path / "before-reset.json"
    code, report = run_cli(engine, "--checkpoint-output", str(checkpoint))
    assert code == 0, report
    seed_demo(engine)
    seed_demo(engine)
    before = all_tables(engine)
    code, report = run_cli(engine, "--checkpoint", str(checkpoint), "--mode", "PREFIX")
    assert code == 0, report
    assert report["registered_epoch_count"] == report["selected_epoch_count"] == 3
    code, report = run_cli(engine, "--checkpoint", str(checkpoint), "--mode", "EXACT")
    assert code == 1 and report["status"] == "NOT_VERIFIED"
    assert all_tables(engine) == before


def test_actual_cli_refuses_oversized_checkpoint_before_using_or_changing_database(
    goal_client: tuple[TestClient, Engine], tmp_path: Path
) -> None:
    _, engine = goal_client
    checkpoint = tmp_path / "oversized.json"
    checkpoint.write_bytes(b" " * 65537)
    before = all_tables(engine)
    code, report = run_cli(engine, "--checkpoint", str(checkpoint))
    assert code == 2 and report["status"] == "INCOMPLETE"
    assert report["error"] == "Checkpoint exceeds the byte limit"
    assert all_tables(engine) == before


def test_actual_cli_does_not_hide_pre_activation_business_history(
    goal_client: tuple[TestClient, Engine],
) -> None:
    _, engine = goal_client
    identity = uuid4()
    with Session(engine) as session, session.begin():
        session.add(
            User(id=identity, external_ref=str(identity), display_name="Synthetic old import")
        )
        session.flush()
        session.add(
            Account(user_id=identity, external_ref="before-audit", name="Old synthetic import")
        )
    with Session(engine) as session, session.begin():
        ensure_audit_epoch(session, identity, datetime.now(UTC))
    before = all_tables(engine)
    code, report = run_cli(engine, "--user-id", str(identity))
    assert code == 1 and report["status"] == "NOT_VERIFIED", report
    epochs = report["epochs"]
    assert isinstance(epochs, list) and len(epochs) == 1
    assert epochs[0]["status"] == "LEGACY_UNAUDITED"
    assert epochs[0]["chain_status"] == "VALID"
    assert all_tables(engine) == before


def test_actual_seed_command_separates_stable_business_summary_and_growing_audit(
    goal_client: tuple[TestClient, Engine],
) -> None:
    _, engine = goal_client
    code, first = run_cli(engine, script=SEED_SCRIPT)
    assert code == 0, first
    code, second = run_cli(engine, script=SEED_SCRIPT)
    assert code == 0, second
    first_audit = first.pop("audit_metadata")
    second_audit = second.pop("audit_metadata")
    assert first == second
    assert first["summary_version"] == "seed-summary-v2"
    assert first["seed_version"] == "mvp-301-v6"
    assert isinstance(first_audit, dict) and isinstance(second_audit, dict)
    assert first_audit["retained_epoch_count"] == 2
    assert second_audit["retained_epoch_count"] == 3
    assert second_audit["retained_event_count"] > first_audit["retained_event_count"]
    assert second_audit["retained_snapshot_count"] > first_audit["retained_snapshot_count"]
    assert second_audit["isolation"] == "repeatable read"
    assert second_audit["read_only"] is True
