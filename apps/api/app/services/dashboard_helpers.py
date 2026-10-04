"""Read-only audit verification once per aggregate request."""

from uuid import UUID

from app.db.models import AuditEvent
from app.services.audit_chain import current_audit_epoch, verify_audit_chain
from app.services.dashboard_types import DashboardAuditCard
from sqlalchemy import select
from sqlalchemy.orm import Session


def current_epoch_audit(session: Session, user_id: UUID, run_ids: list[UUID]) -> DashboardAuditCard:
    """Verify the live epoch once, and bind each selected run to its own anchor."""
    epoch = current_audit_epoch(session, user_id)
    if epoch is None:
        return DashboardAuditCard(
            epoch_id=None,
            status="LEGACY_UNAUDITED",
            complete=False,
            anchored_run_statuses={run_id: "LEGACY_UNAUDITED" for run_id in run_ids},
        )
    result = verify_audit_chain(session, user_id, epoch.id)
    anchors = (
        set(
            session.scalars(
                select(AuditEvent.decision_run_id).where(
                    AuditEvent.user_id == user_id,
                    AuditEvent.epoch_id == epoch.id,
                    AuditEvent.event_type == "DECISION_RECORDED",
                    AuditEvent.decision_run_id.in_(run_ids),
                )
            )
        )
        if run_ids
        else set()
    )
    statuses: dict[UUID, str] = {
        run_id: result.status if run_id in anchors else "LEGACY_UNAUDITED" for run_id in run_ids
    }
    return DashboardAuditCard(
        epoch_id=epoch.id,
        status=result.status,
        complete=result.status == "VALID" and all(s == "VALID" for s in statuses.values()),
        anchored_run_statuses=statuses,
    )
