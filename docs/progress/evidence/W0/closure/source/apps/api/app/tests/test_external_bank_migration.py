"""Upgrade authentic committed 304 histories, using disposable PostgreSQL databases only."""

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db.base import Base
from app.db.models import AuditSubjectSnapshot
from app.db.testing import require_test_database
from app.domain.audit_chain import parse_checkpoint
from app.services.audit_chain import capture_audit_subject, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, seed_demo
from app.tests.test_migrations import migrated_database as migrated_database
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[4]
LEGACY_COMMIT = "c1a1e5ce9b4cde7f92d5cc4ef2dac05534a2d5fd"
RESET_KEY = "401-authentic-304-migration"

LEGACY_RUNNER = r"""
import json
import os
import sys
from pathlib import Path

from app.db.base import Base
from app.db.models import AuditEpoch, SimulatedBankPosting
from app.db.session import create_database_engine
from app.db.testing import require_test_database
from app.domain.audit_chain import checkpoint_canonical_text, build_checkpoint
from app.services.audit_chain import get_audit_head, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_decision_trace_reset import completed_transfer
from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

url = os.environ["MVP401_LEGACY_TEST_URL"]
require_test_database(make_url(url).database)
assert "external_fact_id" not in SimulatedBankPosting.__table__.columns
assert Path(sys.modules["app.db.models"].__file__).resolve().is_relative_to(Path.cwd().resolve())
engine = create_database_engine(url)
try:
    first = seed_demo(engine)
    assert first.summary_version == "seed-summary-v2"
    completed_transfer(engine)
    with Session(engine) as session:
        head = get_audit_head(session, DEMO_USER_ID)
        assert head is not None
        checkpoint = build_checkpoint(head, captured_at=SEED_AS_OF)
    seed_demo(engine, reset_key="401-authentic-304-migration")
    completed_transfer(engine)
    states = {}
    with Session(engine) as session:
        for epoch in session.scalars(select(AuditEpoch).order_by(AuditEpoch.epoch_number)):
            result = verify_audit_chain(session, DEMO_USER_ID, epoch.id)
            assert result.status == "VALID", result.errors
            states[str(epoch.id)] = {"status": epoch.status, "verification": result.status}
    assert {entry["status"] for entry in states.values()} == {"OPEN", "SEALED"}
    tables = inspect(engine).get_table_names()
    snapshot = {}
    with engine.connect() as connection:
        for name in sorted(tables):
            if name == "alembic_version":
                continue
            table = Base.metadata.tables[name]
            snapshot[name] = {
                "columns": list(table.c.keys()),
                "rows": [dict(row) for row in connection.execute(
                    select(table).order_by(table.c.id)).mappings()],
            }
    output = {"states": states, "checkpoint": checkpoint_canonical_text(checkpoint),
              "snapshot": snapshot}
    Path(sys.argv[1]).write_text(json.dumps(output, ensure_ascii=False, sort_keys=True,
                                          default=str), encoding="utf-8")
finally:
    engine.dispose()
"""


def _actual_old_history(engine: Engine, destination: Path) -> dict[str, Any]:
    """Execute committed production services; never synthesize their final financial rows."""
    require_test_database(engine.url.database)
    source = destination / "source"
    source.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", LEGACY_COMMIT, "apps/api/app"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        for member in contents.getmembers():
            target = (source / member.name).resolve()
            if not target.is_relative_to(source.resolve()) or not (
                member.isfile() or member.isdir()
            ):
                raise ValueError("Historical source archive escaped its reserved test directory")
        contents.extractall(source, filter="data")
    api = source / "apps/api"
    runner = api / "capture_authentic_304.py"
    runner.write_text(LEGACY_RUNNER, encoding="utf-8")
    snapshot = destination / "authentic-304.json"
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["UV_CACHE_DIR"] = str(ROOT / ".uv-cache")
    environment["MVP401_LEGACY_TEST_URL"] = engine.url.render_as_string(hide_password=False)
    completed = subprocess.run(
        [sys.executable, "-B", str(runner), str(snapshot)],
        cwd=api,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    (destination / "legacy-runtime.txt").write_text(
        completed.stdout + completed.stderr, encoding="utf-8"
    )
    assert completed.returncode == 0, completed.stderr
    return cast(dict[str, Any], json.loads(snapshot.read_text(encoding="utf-8")))


def test_external_schema_empty_roundtrip_matches_models(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(config, "0006_audit_chain")
    assert "external_bank_facts" not in inspect(engine).get_table_names()
    assert "external_fact_id" not in {
        column["name"] for column in inspect(engine).get_columns("simulated_bank_postings")
    }
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_upgrade_preserves_authentic_open_sealed_v1_and_readonly_old_reset_replay(
    migrated_database: tuple[Engine, Config],
) -> None:
    engine, config = migrated_database
    command.downgrade(config, "0006_audit_chain")
    destination = ROOT / ".runtime" / ("MVP-401-authentic-upgrade-" + uuid4().hex)
    old = _actual_old_history(engine, destination)
    assert len(old["snapshot"]["simulated_bank_postings"]["columns"]) == 18
    command.upgrade(config, "head")
    with engine.connect() as connection:
        for name, original in old["snapshot"].items():
            table = Base.metadata.tables[name]
            current = [
                dict(row)
                for row in connection.execute(
                    select(*(table.c[column] for column in original["columns"])).order_by(
                        table.c.id
                    )
                ).mappings()
            ]
            assert json.loads(json.dumps(current, default=str)) == original["rows"], name
        assert connection.execute(text("SELECT count(*) FROM external_bank_facts")).scalar() == 0
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM simulated_bank_postings "
                    "WHERE external_fact_id IS NOT NULL"
                )
            ).scalar()
            == 0
        )
    checkpoint = parse_checkpoint(old["checkpoint"])
    with Session(engine) as session:
        for identity in old["states"]:
            result = verify_audit_chain(session, DEMO_USER_ID, UUID(identity))
            assert result.status == old["states"][identity]["verification"], result.errors
        assert (
            verify_audit_chain(
                session, DEMO_USER_ID, checkpoint.epoch_id, checkpoint=checkpoint, mode="PREFIX"
            ).status
            == "VALID"
        )
        assert (
            verify_audit_chain(
                session, DEMO_USER_ID, checkpoint.epoch_id, checkpoint=checkpoint, mode="EXACT"
            ).status
            == "INTEGRITY_ERROR"
        )
        live_opening: UUID = session.execute(
            text(
                "SELECT id FROM simulated_bank_postings "
                "WHERE entry_kind='OPENING' ORDER BY id LIMIT 1"
            )
        ).scalar_one()
        original = session.scalars(
            select(AuditSubjectSnapshot).where(
                AuditSubjectSnapshot.kind == "BANK_POSTING",
                AuditSubjectSnapshot.entity_id == live_opening,
            )
        ).first()
        assert original is not None
        original_hash = original.snapshot_hash
        original_text = original.canonical_text
        count = session.scalar(select(func.count()).select_from(AuditSubjectSnapshot))
        captured = capture_audit_subject(
            session, DEMO_USER_ID, original.epoch_id, "BANK_POSTING", original.entity_id
        )
        assert captured.snapshot_hash == original_hash and captured.canonical_text == original_text
        assert session.scalar(select(func.count()).select_from(AuditSubjectSnapshot)) == count
    # The original v2 financial summary is returned without invoking fresh v3 bootstrap.
    from app.tests.test_demo_seed import database_snapshot

    before = database_snapshot(engine)
    replay = seed_demo(engine, reset_key=RESET_KEY)
    assert replay.summary_version == "seed-summary-v2"
    assert database_snapshot(engine) == before
