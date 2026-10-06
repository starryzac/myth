"""Real request-local audit reuse still rejects changed originals and scope boundaries."""

from typing import Literal
from uuid import UUID, uuid4

import pytest
from app.db.models import AuditEvent, EvidenceItem
from app.domain import audit_chain as domain
from app.domain.audit_chain_types import AuditCheckpoint, AuditDiagnostic, AuditVerification
from app.services import audit_chain as audit
from app.services.demo_seed import DEMO_USER_ID, SEED_AS_OF, seed_demo
from app.tests.test_demo_seed import demo_engine as demo_engine
from app.tests.test_full_projection_api import physical_snapshot
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_actual_audit_scope_reuses_only_identical_clean_original_snapshot(
    demo_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_demo(demo_engine)
    before = physical_snapshot(demo_engine)
    original = audit._verify_audit_chain
    calls: list[tuple[UUID, UUID | None, str]] = []

    def counted(
        session: Session,
        user: UUID,
        epoch_id: UUID | None = None,
        checkpoint: AuditCheckpoint | None = None,
        mode: Literal["PREFIX", "EXACT"] = "PREFIX",
    ) -> AuditVerification:
        calls.append((user, epoch_id, mode))
        return original(session, user, epoch_id, checkpoint, mode)

    monkeypatch.setattr(audit, "_verify_audit_chain", counted)
    with Session(demo_engine) as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        evidence = session.scalar(
            select(EvidenceItem)
            .where(EvidenceItem.user_id == DEMO_USER_ID)
            .order_by(EvidenceItem.id)
        )
        head = audit.get_audit_head(session, DEMO_USER_ID)
        assert evidence is not None and head is not None
        with audit.audit_read_scope(session):
            first = audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id)
            assert first.status == "VALID" and len(calls) == 1
            first.errors.append(AuditDiagnostic(code="UNIT_MUTATED_RETURN", message="test only"))
            again = audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id)
            assert again.status == "VALID" and again.errors == [] and len(calls) == 1
            with Session(demo_engine) as other:
                other.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                other.execute(text("SET TRANSACTION READ ONLY"))
                for _ in range(2):
                    assert (
                        audit.verify_audit_chain(other, DEMO_USER_ID, head.epoch_id).status
                        == "VALID"
                    )
            assert len(calls) == 3
            assert audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
            assert len(calls) == 4
            with session.begin_nested():
                assert (
                    audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
                )
            assert len(calls) == 5
            for _ in range(2):
                assert (
                    audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
                )
            assert len(calls) == 6
            checkpoint = domain.build_checkpoint(head, captured_at=SEED_AS_OF)
            exact = audit.verify_audit_chain(
                session, DEMO_USER_ID, head.epoch_id, checkpoint, "EXACT"
            )
            assert exact.status == "VALID" and len(calls) == 7
            assert (
                audit.verify_audit_chain(
                    session, DEMO_USER_ID, head.epoch_id, checkpoint, "EXACT"
                ).status
                == "VALID"
            )
            assert len(calls) == 7
            changed = checkpoint.model_copy(update={"checkpoint_hash": "f" * 64})
            assert (
                audit.verify_audit_chain(
                    session, DEMO_USER_ID, head.epoch_id, changed, "EXACT"
                ).status
                != "VALID"
            )
            assert len(calls) == 8
            assert audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
            assert len(calls) == 9
            # The genesis has no reference to this evidence: changing it must
            # invalidate reuse, while the full audit's original scope stays exact.
            evidence.content["request_local_test_mutation"] = True
            assert not session.dirty
            assert audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
            assert len(calls) == 10
            # Actual hashed event JSON is covered by the original verifier.
            event = session.get(AuditEvent, head.last_event_id)
            assert event is not None and event.user_id == DEMO_USER_ID
            event.payload["request_local_event_mutation"] = True
            assert not session.dirty
            assert audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status != "VALID"
            assert len(calls) == 11
            session.rollback()
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            assert audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
            assert len(calls) == 12
        # A later invocation must perform the original verifier again.
        assert audit.verify_audit_chain(session, DEMO_USER_ID, head.epoch_id).status == "VALID"
        assert len(calls) == 13
        foreign = uuid4()
        with audit.audit_read_scope(session):
            assert audit.verify_audit_chain(session, foreign).status == "LEGACY_UNAUDITED"
            assert audit.verify_audit_chain(session, foreign).status == "LEGACY_UNAUDITED"
        assert len(calls) == 15
    assert physical_snapshot(demo_engine) == before
