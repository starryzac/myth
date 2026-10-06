"""Current RRRO retained-adoption reader; no new suggestion, adoption or financial write."""

from datetime import datetime
from uuid import UUID

from app.db.models import EvidenceItem, User
from app.domain.full_seasonal_adoption import MAX_ADOPTIONS, SOURCE
from app.domain.full_seasonal_ended_adoption import (
    EndedSeasonalAdoptionInput,
    EndedSeasonalAdoptionProof,
    EndedSeasonalAdoptionRecord,
    derive_ended_seasonal_adoption,
    unknown_ended_seasonal_adoption,
)
from app.services.audit_chain import (
    audit_read_scope,
    current_audit_epoch,
    row_copy,
    verify_audit_chain,
)
from app.services.decision_trace import get_decision_trace
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.full_seasonal_adoption import _original
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import select
from sqlalchemy.orm import Session


def read_current_ended_seasonal_adoption(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> EndedSeasonalAdoptionProof:
    _read_snapshot(session)
    now = _now(now)
    owner = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if owner is None or not owner.is_simulated or owner.timezone != "Asia/Shanghai":
        raise PolicyLifecycleError("NOT_FOUND", "当前模拟用户或中国日历来源不存在", 404)
    if epoch is None or epoch.status != "OPEN" or epoch.user_id != user_id:
        raise PolicyLifecycleError("ENDED_ADOPTION_OPEN_EPOCH_REQUIRED", "需要唯一当前原周期", 409)
    try:
        with audit_read_scope(session):
            audit = verify_audit_chain(session, user_id, epoch.id, mode="EXACT")
            if audit.status != "VALID":
                raise ValueError("ENDED_ADOPTION_CURRENT_AUDIT_NOT_VALID")
            policy = read_full_policy(session, user_id, policy_id, now)
            if policy.epoch_id != epoch.id or policy.template_name != "SeasonalReservePolicy":
                raise ValueError("ENDED_ADOPTION_CURRENT_POLICY_EPOCH_OR_TYPE_DIFFERS")
            # Every owner row is captured, including other epochs. Never filter history
            # and then falsely call a missing adoption an empty complete denominator.
            rows = list(
                session.scalars(
                    select(EvidenceItem)
                    .where(EvidenceItem.user_id == user_id, EvidenceItem.source_type == SOURCE)
                    .order_by(EvidenceItem.id)
                    .limit(MAX_ADOPTIONS + 1)
                )
            )
            if len(rows) > MAX_ADOPTIONS:
                raise ValueError("ENDED_ADOPTION_REGISTERED_ORIGINAL_CAPACITY_EXCEEDED")
            records: list[EndedSeasonalAdoptionRecord] = []
            for row in rows:
                if row.source_ref is None:
                    raise ValueError("ENDED_ADOPTION_ORIGINAL_COMMAND_ID_MISSING")
                command_id = UUID(row.source_ref)
                receipt = _original(session, user_id, command_id, now, replay=True)
                saved = get_decision_trace(session, user_id, command_id, now)
                if (
                    receipt is None
                    or receipt.evidence_id != row.id
                    or saved.completeness != "COMPLETE"
                    or saved.audit_chain_status != "VALID"
                    or saved.trace is None
                ):
                    raise ValueError("ENDED_ADOPTION_ORIGINAL_TRACE_OR_ROW_MISSING")
                ids = {UUID(raw["id"]) for raw in receipt.original.scope.source_evidence_originals}
                ids.add(row.id)
                evidence = [
                    session.get(EvidenceItem, identity) for identity in sorted(ids, key=str)
                ]
                if any(raw is None for raw in evidence):
                    raise ValueError("ENDED_ADOPTION_CURRENT_ORIGINAL_EVIDENCE_MISSING")
                records.append(
                    EndedSeasonalAdoptionRecord(
                        adoption_evidence_original=row_copy(row),
                        trace=saved.trace,
                        current_evidence_originals=[
                            row_copy(raw) for raw in evidence if raw is not None
                        ],
                    )
                )
            value = EndedSeasonalAdoptionInput(
                user_id=user_id,
                epoch_id=epoch.id,
                policy_id=policy_id,
                as_of=now,
                epoch_original=row_copy(epoch),
                audit=audit,
                current_policy_original=policy.model_dump(mode="json"),
                registered_adoption_evidence_count=len(rows),
                registered_adoption_evidence_ids=[row.id for row in rows],
                records=records,
            )
            return derive_ended_seasonal_adoption(value)
    except (PolicyLifecycleError, ValueError, TypeError, KeyError, OverflowError) as error:
        return unknown_ended_seasonal_adoption(user_id, epoch.id, policy_id, now, [str(error)])
