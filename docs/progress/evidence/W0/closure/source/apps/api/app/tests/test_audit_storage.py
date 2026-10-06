"""Real PostgreSQL audit storage boundaries, using generated databases only."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.db.models import AuditEpoch, AuditEvent, User
from app.domain import audit_chain as domain
from app.domain.audit_chain_types import AuditEpochTransition, AuditIntent, AuditPayload
from app.tests.test_demo_seed import demo_engine as demo_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 4, 9, tzinfo=UTC)


def test_first_epoch_has_real_genesis_and_repeat_is_readonly(demo_engine: Engine) -> None:
    from app.services.audit_chain import ensure_audit_epoch, get_audit_head, verify_audit_chain

    identity = uuid4()
    with Session(demo_engine) as session, session.begin():
        session.add(User(id=identity, external_ref=str(identity), display_name="Synthetic"))
        session.flush()
        first = ensure_audit_epoch(session, identity, NOW)
        head = get_audit_head(session, identity)
        assert head is not None
        assert head.event_count == head.last_sequence == 1
        assert head.last_event_id == head.genesis_event_id
        assert ensure_audit_epoch(session, identity, NOW).id == first.id
    with Session(demo_engine) as session:
        assert verify_audit_chain(session, identity).status == "VALID"


@pytest.mark.parametrize("missing", [True, False])
def test_sql_insert_cannot_omit_hashed_required_tenant_header(
    demo_engine: Engine, missing: bool
) -> None:
    identity, epoch_id = uuid4(), uuid4()
    with Session(demo_engine) as session, session.begin():
        session.add(User(id=identity, external_ref=str(identity), display_name="Synthetic"))
    with Session(demo_engine) as session:
        session.add(AuditEpoch(id=epoch_id, user_id=identity, epoch_number=1, opened_at=NOW))
        session.flush()
        intent = AuditIntent(
            user_id=identity,
            event_type="EPOCH_STARTED",
            aggregate_type="EPOCH",
            aggregate_id=epoch_id,
            correlation_id=epoch_id,
            idempotency_key="malformed-genesis",
            occurred_at=NOW,
            payload=AuditPayload(
                fact_key="malformed-genesis",
                correlation_kind="EPOCH",
                epoch_transition=AuditEpochTransition(kind="INIT"),
            ),
        )
        event = domain.build_event(
            intent,
            event_id=uuid4(),
            epoch_id=epoch_id,
            sequence_number=1,
            previous_hash=None,
            observed_at=NOW,
            appended_at=NOW,
        )
        fields = event.model_dump(
            mode="python", exclude={"id", "epoch_id", "event_hash", "appended_at", "simulation"}
        )
        fields["payload"] = event.payload.model_dump(mode="json")
        body = event.model_dump(mode="json")
        if missing:
            body.pop("user_id")
        else:
            body["user_id"] = None
        body.pop("event_hash")
        body["event_hash"] = hashlib.sha256(
            b"bounded-funds/audit-event-v1\0" + domain.canonical_bytes(body)
        ).hexdigest()
        session.add(
            AuditEvent(
                id=event.id,
                epoch_id=epoch_id,
                event_hash=body["event_hash"],
                created_at=NOW,
                canonical_text=domain.canonical_text(body),
                **fields,
            )
        )
        with pytest.raises(DBAPIError, match="AUDIT_EVENT_INVALID"):
            session.flush()
        session.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET event_hash = event_hash",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events CASCADE",
        "UPDATE audit_epochs SET event_count=event_count",
        "DELETE FROM audit_epochs",
        "TRUNCATE audit_epochs CASCADE",
        "TRUNCATE audit_subject_snapshots",
    ],
)
def test_ordinary_owner_dml_cannot_rewrite_or_remove_history(
    demo_engine: Engine, statement: str
) -> None:
    from app.services.demo_seed import seed_demo

    seed_demo(demo_engine)
    with demo_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="AUDIT_APPEND_ONLY|AUDIT_HEAD_WRITE_FORBIDDEN"):
            connection.execute(text(statement))
        connection.rollback()


def test_reading_absent_chain_is_unaudited_and_has_no_writes(demo_engine: Engine) -> None:
    from app.services.audit_chain import get_audit_head, list_audit_events, verify_audit_chain

    with Session(demo_engine) as session:
        identity = uuid4()
        session.add(User(id=identity, external_ref=str(identity), display_name="uncommitted"))
        assert get_audit_head(session, identity) is None
        assert list_audit_events(session, identity).items == []
        assert verify_audit_chain(session, identity).status == "LEGACY_UNAUDITED"
        assert len(session.new) == 1
        with session.no_autoflush:
            assert session.scalar(text("SELECT count(*) FROM users")) == 0


def test_real_nonowner_role_cannot_bypass_append_only_triggers(demo_engine: Engine) -> None:
    from app.services.demo_seed import seed_demo

    seed_demo(demo_engine)
    role = "bf_audit_" + uuid4().hex
    with demo_engine.begin() as connection:
        connection.exec_driver_sql(
            f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE'
        )
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(
            f'GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON ALL TABLES IN SCHEMA public TO "{role}"'
        )
    try:
        with demo_engine.connect() as connection:
            connection.exec_driver_sql(f'SET ROLE "{role}"')
            assert connection.scalar(text("SELECT current_user")) == role
            connection.commit()
            for statement in [
                "UPDATE audit_events SET payload=payload",
                "DELETE FROM audit_events",
                "TRUNCATE audit_events CASCADE",
                "UPDATE audit_epochs SET last_event_hash=last_event_hash",
                "ALTER TABLE audit_events DISABLE TRIGGER ALL",
            ]:
                with pytest.raises(DBAPIError):
                    connection.execute(text(statement))
                connection.rollback()
            connection.exec_driver_sql("RESET ROLE")
            connection.commit()
    finally:
        with demo_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')


def test_retry_checks_actual_stored_columns_and_never_repairs_them(demo_engine: Engine) -> None:
    from app.services.audit_chain import append_audit_event, ensure_audit_epoch
    from app.services.policy_lifecycle import PolicyLifecycleError

    identity = uuid4()
    with Session(demo_engine) as session, session.begin():
        session.add(User(id=identity, external_ref=str(identity), display_name="Synthetic"))
        session.flush()
        epoch = ensure_audit_epoch(session, identity, NOW)
        row = session.get(AuditEvent, epoch.genesis_event_id)
        assert row is not None and row.canonical_text is not None
        event = domain.parse_event(row.canonical_text)
        original = AuditIntent.model_validate(
            event.model_dump(mode="python", include=set(AuditIntent.model_fields))
        )
        assert append_audit_event(session, original, NOW, NOW).id == event.id
        changed = original.model_copy(update={"idempotency_key": "different-key"})
        with pytest.raises(PolicyLifecycleError, match="同一原始事实"):
            append_audit_event(session, changed, NOW, NOW)
    # Administrators can alter DDL. This test checks detection after that explicit boundary.
    with demo_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE audit_events DISABLE TRIGGER audit_events_immutable"
        )
        connection.execute(
            text("UPDATE audit_events SET event_hash=:digest WHERE id=:id"),
            {"digest": "f" * 64, "id": event.id},
        )
        connection.exec_driver_sql("ALTER TABLE audit_events ENABLE TRIGGER audit_events_immutable")
    with Session(demo_engine) as session:
        with pytest.raises(PolicyLifecycleError, match="核验|一致"):
            append_audit_event(session, original, NOW, NOW)


def test_verification_refuses_budget_before_loading_original_text(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import audit_chain
    from app.services.demo_seed import DEMO_USER_ID, seed_demo

    seed_demo(demo_engine)
    monkeypatch.setattr(audit_chain, "VERIFY_BYTE_LIMIT", 1)

    def forbidden_parse(_: str) -> None:
        raise AssertionError("bounded read must stop before parsing original text")

    monkeypatch.setattr(domain, "parse_event", forbidden_parse)
    with Session(demo_engine) as session:
        result = audit_chain.verify_audit_chain(session, DEMO_USER_ID)
        assert result.status == "INCOMPLETE"
        assert result.errors[0].code == "LIMIT_EXCEEDED"


def test_open_missing_original_fails_but_lifecycle_and_sealed_reset_remain_valid(
    demo_engine: Engine,
) -> None:
    from app.db.models import EvidenceItem
    from app.domain.autonomy import ALGORITHM_VERSION
    from app.domain.decision_trace import build_trace
    from app.domain.policy_configuration import configuration_hash
    from app.services.audit_chain import current_audit_epoch, verify_audit_chain
    from app.services.decision_trace import evidence_copy, record_trace
    from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo

    seed_demo(demo_engine)
    evidence_id = uuid4()
    with Session(demo_engine) as session, session.begin():
        content = {"simulation": True, "declared": "original server fixture"}
        evidence = EvidenceItem(
            id=evidence_id,
            user_id=DEMO_USER_ID,
            evidence_level="USER_DECLARED",
            source_type="TEST_USER_INTENT",
            source_ref="original-audit-proof",
            content=content,
            content_hash=configuration_hash(content),
            observed_at=SEED_AS_OF,
            valid_from=SEED_AS_OF,
        )
        session.add(evidence)
        session.flush()
        trace = build_trace(
            run_id=uuid4(),
            user_id=DEMO_USER_ID,
            phase="EVALUATION",
            as_of=SEED_AS_OF,
            algorithm_versions={"autonomy": ALGORITHM_VERSION},
            inputs={},
            sources=[evidence_copy(evidence)],
            policies=[],
            constraints=[],
            candidates=[],
            outcome={"status": "BLOCKED"},
        )
        record_trace(session, trace)
        epoch = current_audit_epoch(session, DEMO_USER_ID)
        assert epoch is not None
        old_epoch = epoch.id
    with Session(demo_engine) as session:
        current = session.get(EvidenceItem, evidence_id)
        assert current is not None
        session.delete(current)
        session.flush()
        result = verify_audit_chain(session, DEMO_USER_ID)
        assert result.status == "INTEGRITY_ERROR"
        assert any(issue.code == "CURRENT_ORIGINAL_MISSING" for issue in result.errors)
        session.rollback()
    with Session(demo_engine) as session, session.begin():
        restored = session.get(EvidenceItem, evidence_id)
        assert restored is not None
        restored.status = "SUPERSEDED"
    with Session(demo_engine) as session:
        assert verify_audit_chain(session, DEMO_USER_ID).status == "VALID"
    seed_demo(demo_engine)
    with Session(demo_engine) as session:
        assert session.get(EvidenceItem, evidence_id) is None
        assert verify_audit_chain(session, DEMO_USER_ID, old_epoch).status == "VALID"
