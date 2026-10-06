"""Actual first-genesis initial facts, never substituted from an after-action state."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from app.db.models import AuditEpoch
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_mvp_readonly_collector_integration import write
from app.tests.test_scenario_service_steps import original_rows
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scripts import mvp_readonly_collector as collector

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.integration
def test_initial_facts_are_first_genesis_before_any_action_and_capture_writes_nothing(
    demo_engine: Engine,
) -> None:
    seed_demo(demo_engine, _experiment_seed_extension="MVP503_SECOND_CASH_INITIAL_FACT_V1")
    before = original_rows(demo_engine)
    # SQLAlchemy identifiers can be quoted_name subclasses. Keep all original
    # values; only the identifiers become ordinary JSON strings for transport.
    transport_rows = {
        str(name): [{str(key): value for key, value in row.items()} for row in rows]
        for name, rows in before.items()
    }
    assert len(before) == 24
    for table in (
        "action_plans",
        "action_receipts",
        "bank_operations",
        "action_resource_reservations",
    ):
        assert before[table] == []
    with Session(demo_engine) as session:
        epochs = list(session.scalars(select(AuditEpoch)))
        assert len(epochs) == 1 and epochs[0].status == "OPEN"
        assert epochs[0].genesis_event_hash is not None
        assert len(epochs[0].genesis_event_hash) == 64
        epoch_id = str(epochs[0].id)
    assert len(before["audit_events"]) == 1
    assert before["audit_events"][0]["event_type"] == "EPOCH_STARTED"
    directory = ROOT / ".runtime/W1-first-genesis-initial-facts-pg" / uuid4().hex
    source_files = []
    for name in sorted(collector.required_sources(ROOT)):
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
    refs = {
        name: write(
            directory / (name + ".original.json"),
            {
                "protocol": "mvp-development-first-genesis-capture-registration-v1",
                "artifact_role": name,
                "purpose": "DEVELOPMENT",
                "status": "FIRST_GENESIS_BEFORE_ANY_ACTION_NOT_A_FORMAL_CASE",
                "financial_effect_evidence": False,
                "human_research": "NOT_STARTED",
            },
        )
        for name in ("input", "oracle", "design", "rule")
    }
    bindings = {
        "experiment_run_id": str(uuid4()),
        "case_id": "DEVELOPMENT_FIRST_GENESIS_INITIAL_FACTS",
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
            "as_of": SEED_AS_OF.isoformat(),
            "role": "BEFORE",
            "source_ref": source_ref,
            "root_registration_ref": None,
        },
    )
    manifest = collector.collect_readonly(
        registration_ref, directory / "actual-capture", workspace=ROOT
    )
    assert manifest["status"] == "CAPTURED_RAW_NOT_ECONOMICALLY_VERIFIED"
    assert original_rows(demo_engine) == before
    physical = json.loads((directory / "actual-capture/physical-snapshot.json").read_bytes())
    assert physical["payload"]["tables"] == json.loads(collector.canonical(transport_rows))
    facts = json.loads((directory / "actual-capture/financial-facts.json").read_bytes())["payload"][
        "facts"
    ]
    assert facts["complete"] is True and facts["financial_effect_verified"] is False
    write(
        directory / "initial-facts-original-verification.json",
        {
            "status": "ACTUAL_FIRST_GENESIS_ALL24_INITIAL_FACTS_ZERO_CAPTURE_WRITES",
            "database_name": demo_engine.url.database,
            "bindings": bindings,
            "as_of": SEED_AS_OF.isoformat(),
            "initial_physical_sha256": hashlib.sha256(
                collector.canonical(transport_rows)
            ).hexdigest(),
            "audit_epoch_count": len(epochs),
            "genesis_event_count": len(before["audit_events"]),
            "table_count": len(before),
            "application_action_count": len(before["action_plans"]),
            "all24_rows_unchanged": True,
            "is_after_action_projection": False,
            "registered_corpus_valid": False,
            "formal_comparison_completed": False,
            "financial_effect_evidence": False,
        },
    )
