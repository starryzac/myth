"""A simulation reset seals history instead of erasing its original events."""

import pytest
from app.db.models import AuditEpoch, AuditEvent
from app.services.audit_chain import ensure_audit_epoch, verify_audit_chain
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_demo_seed import database_snapshot
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_seed_summary_excludes_growing_audit_history(demo_engine: Engine) -> None:
    summary = seed_demo(demo_engine)
    assert len(summary.counts) == 19
    assert "audit_events" not in summary.counts
    assert summary.seed_version == "mvp-301-v6"


def test_reset_seals_original_events_and_preserves_their_bytes(demo_engine: Engine) -> None:
    first = seed_demo(demo_engine)
    with Session(demo_engine) as session, session.begin():
        old = ensure_audit_epoch(session, DEMO_USER_ID, SEED_AS_OF)
        epoch_id = old.id
        originals = {row.id: row.canonical_text for row in session.scalars(select(AuditEvent))}
    assert seed_demo(demo_engine) == first
    with Session(demo_engine) as session:
        epochs = list(session.scalars(select(AuditEpoch).order_by(AuditEpoch.epoch_number)))
        assert len(epochs) == 2
        assert epochs[0].status == "SEALED" and epochs[1].status == "OPEN"
        assert epochs[1].previous_seal_hash == epochs[0].seal_hash
        for identity, original in originals.items():
            row = session.get(AuditEvent, identity)
            assert row is not None and row.canonical_text == original
        assert verify_audit_chain(session, DEMO_USER_ID, epoch_id).status == "VALID"


def test_reset_retry_preserves_original_epoch_and_cannot_change_reason(demo_engine: Engine) -> None:
    from app.services.demo_seed import SeedConflictError

    first = seed_demo(demo_engine, reset_key="stable-reset")
    before = database_snapshot(demo_engine)
    assert seed_demo(demo_engine, reset_key="stable-reset") == first
    assert database_snapshot(demo_engine) == before
    with pytest.raises(SeedConflictError, match="reset_key"):
        seed_demo(demo_engine, reset_key="stable-reset", reason="DIFFERENT_REASON")
    assert database_snapshot(demo_engine) == before


def test_failure_after_old_seal_rolls_back_entire_graph_and_history(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import demo_seed

    seed_demo(demo_engine)
    before = database_snapshot(demo_engine)

    def fail_open(_: Session) -> None:
        raise RuntimeError("late bank opening failure")

    monkeypatch.setattr(demo_seed, "_open_seed_bank", fail_open)
    with pytest.raises(RuntimeError, match="late bank"):
        seed_demo(demo_engine)
    assert database_snapshot(demo_engine) == before


def test_current_epoch_checks_real_previous_seal_not_only_its_local_claim(
    demo_engine: Engine,
) -> None:
    from sqlalchemy import text

    seed_demo(demo_engine)
    seed_demo(demo_engine)
    with demo_engine.begin() as connection:
        # Owner DDL is explicitly outside prevention; a subsequent read must detect damage.
        connection.exec_driver_sql("ALTER TABLE audit_epochs DISABLE TRIGGER audit_epoch_guard")
        connection.execute(
            text("UPDATE audit_epochs SET seal_hash=:hash WHERE status='SEALED'"),
            {"hash": "f" * 64},
        )
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        connection.exec_driver_sql("ALTER TABLE audit_epochs ENABLE TRIGGER audit_epoch_guard")
    with Session(demo_engine) as session:
        assert verify_audit_chain(session, DEMO_USER_ID).status == "INTEGRITY_ERROR"
