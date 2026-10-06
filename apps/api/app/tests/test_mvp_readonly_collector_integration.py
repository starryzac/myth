"""Actual independent local read-only capture; DEVELOPMENT, never formal corpus proof."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from app.db.models import Account, AuditEvent, AuditSubjectSnapshot
from app.services.action_contracts import PrepareActionRequest, TransferIntent
from app.services.audit_chain import current_audit_epoch
from app.services.demo_seed import (
    DEMO_USER_ID,
    SEED_AS_OF,
    seed_demo,
)
from app.services.execution import prepare_action
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_scenario_service_steps import original_rows
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scripts import mvp_readonly_collector as collector

ROOT = Path(__file__).resolve().parents[4]


def write(path: Path, value: object) -> dict[str, str]:
    raw = collector.canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.mark.integration
def test_actual_full_physical_capture_preserves_stored_text_nulls_and_all24_rows(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine, _experiment_seed_extension="MVP503_SECOND_CASH_INITIAL_FACT_V1")
    clock = SEED_AS_OF + timedelta(seconds=1)
    with Session(demo_engine) as session:
        cash = session.scalar(
            select(Account).where(Account.external_ref == "mvp-301-v6:account:cash")
        )
        second = session.scalar(
            select(Account).where(Account.external_ref == "mvp503-author-v2:account:cash-second")
        )
        assert cash is not None and second is not None
        source_id, target_id = cash.id, second.id
    prepared = prepare_action(
        demo_engine,
        DEMO_USER_ID,
        PrepareActionRequest(
            idempotency_key="DEVELOPMENT_COLLECTOR_ACTUAL_PREPARE",
            intent=TransferIntent(
                kind="transfer_internal",
                source_account_id=source_id,
                destination_account_id=target_id,
                amount_cents=47143,
            ),
        ),
        clock,
    )
    assert prepared.autonomy_level == "ASK_ONCE" and prepared.receipt is None
    with Session(demo_engine) as session:
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None and epoch.status == "OPEN"
        epoch_id = str(epoch.id)
        events = {str(row.id): row.canonical_text for row in session.scalars(select(AuditEvent))}
        subjects = {
            str(row.id): row.canonical_text for row in session.scalars(select(AuditSubjectSnapshot))
        }
    before = original_rows(demo_engine)
    assert len(before) == 24 and events and subjects
    directory = ROOT / ".runtime/W1-readonly-collector-development-pg" / uuid4().hex
    source_names = collector.required_sources(ROOT)
    source_files = []
    for name in sorted(source_names):
        raw = (ROOT / name).read_bytes()
        archived = directory / "source-originals" / name
        archived.parent.mkdir(parents=True, exist_ok=True)
        with archived.open("xb") as stream:
            stream.write(raw)
        source_files.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    source_ref = write(
        directory / "SOURCE.original.json",
        {
            "protocol": "mvp-development-source-inventory-v1",
            "purpose": "DEVELOPMENT",
            "files": source_files,
            "formal_frozen": False,
        },
    )
    refs = {}
    for name in ("input", "oracle", "design", "rule"):
        refs[name] = write(
            directory / (name + ".original.json"),
            {
                "protocol": "mvp-development-collector-integration-input-v1",
                "artifact_role": name,
                "purpose": "DEVELOPMENT",
                "formal_frozen": False,
                "financial_effect_evidence": False,
                "human_research": "NOT_STARTED",
                "test": "ACTUAL_FULL_PHYSICAL_READ_ONLY_CAPTURE_NOT_ORACLE_OR_ARM_VALIDATION",
            },
        )
    bindings = {
        "experiment_run_id": str(uuid4()),
        "case_id": "DEVELOPMENT_UNFROZEN_COLLECTOR_CAPTURE",
        "arm_id": "P",
        "execution_mode": "SERVICE_INTEGRATION",
        **{name + "_sha256": ref["sha256"] for name, ref in refs.items()},
        "source_sha256": source_ref["sha256"],
        "seed_version": "mvp-301-v6",
        "isolated_db_epoch": epoch_id,
        "purpose": "DEVELOPMENT",
        "user_id": str(DEMO_USER_ID),
    }
    registration_ref = write(
        directory / "collector-registration.original.json",
        {
            "protocol": collector.PROTOCOL,
            "bindings": bindings,
            "database_name": demo_engine.url.database,
            "as_of": clock.isoformat(),
            "role": "SNAPSHOT",
            "source_ref": source_ref,
            "root_registration_ref": None,
        },
    )
    manifest = collector.collect_readonly(
        registration_ref,
        directory / "actual-capture",
        workspace=ROOT,
    )
    assert manifest["status"] == "CAPTURED_RAW_NOT_ECONOMICALLY_VERIFIED"
    assert original_rows(demo_engine) == before
    physical = json.loads((directory / "actual-capture/physical-snapshot.json").read_bytes())
    payload = physical["payload"]
    assert physical["bindings"] == bindings
    assert set(payload["tables"]) == set(before)
    assert payload["tables"]["alembic_version"] == before["alembic_version"]
    assert before["alembic_version"] == [{"version_num": "0007_external_bank_facts"}]
    for table in inspect(demo_engine).get_table_names():
        columns = {row["name"] for row in inspect(demo_engine).get_columns(table)}
        for row in payload["tables"][table]:
            assert set(row) == columns
    assert {row["id"]: row["canonical_text"] for row in payload["tables"]["audit_events"]} == events
    assert {
        row["id"]: row["canonical_text"] for row in payload["tables"]["audit_subject_snapshots"]
    } == subjects
    assert any(
        value is None
        for rows in payload["tables"].values()
        for row in rows
        for value in row.values()
    )
    assert len(payload["tables"]["accounts"]) == 6
    assert len(payload["tables"]["simulated_bank_postings"]) == 18
    assert len(payload["tables"]["users"]) == 1
    assert payload["tables"]["users"][0]["id"] == str(DEMO_USER_ID)
    for row in source_files:
        assert (ROOT / row["path"]).read_bytes() == (
            directory / "source-originals" / row["path"]
        ).read_bytes()
    # The physically complete capture still has explicitly missing metric inputs.
    facts = json.loads((directory / "actual-capture/financial-facts.json").read_bytes())["payload"][
        "facts"
    ]
    assert facts["protocol"] == "mvp-financial-facts-v2"
    assert facts["complete"] is True and facts["financial_effect_verified"] is False
    assert facts["execution_authority"] is False and facts["missing_originals"]
    assert set(facts["tables"]) == set(collector.FINANCIAL_TABLES)
    assert facts["policy_state_events"] == payload["tables"]["audit_events"]
    assert facts["policy_subjects"] == payload["tables"]["audit_subject_snapshots"]
    for name, inventory in facts["inventory"].items():
        assert (
            inventory["sha256"]
            == hashlib.sha256(collector.canonical(facts["tables"][name])).hexdigest()
        )
        assert inventory["row_ids"] == [row["id"] for row in facts["tables"][name]]
    # Captured input artifacts have explicit DEVELOPMENT purpose, no fake freeze.
    assert manifest["bindings"]["purpose"] == "DEVELOPMENT"
    write(
        directory / "independent-zero-write-verification.json",
        {
            "status": "ACTUAL_ALL24_DATABASE_ROWS_UNCHANGED",
            "table_count": len(before),
            "stored_event_text_count": len(events),
            "stored_subject_text_count": len(subjects),
            "physical_columns_verified": True,
            "source_archives_verified": True,
            "collector_manifest_sha256": hashlib.sha256(
                (directory / "actual-capture/manifest.json").read_bytes()
            ).hexdigest(),
            "registered_corpus_valid": False,
            "formal_comparison_completed": False,
            "financial_effect_evidence": False,
        },
    )
