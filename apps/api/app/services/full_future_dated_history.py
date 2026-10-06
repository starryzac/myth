"""Capture complete retained dated versions in the caller's actual clean RRRO request."""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.full_models import FullPolicy, FullPolicyCommand, FullPolicyVersion
from app.db.models import AuditEpoch, EvidenceItem, User
from app.domain.full_future_dated_history import (
    MAX_COMMANDS,
    MAX_VERSIONS,
    FutureDatedHistoryProof,
    history_digest,
    validate_future_dated_history,
)
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.full_policy_lifecycle import (
    FullPolicyView,
    _read_snapshot,
    list_full_commands,
    list_full_versions,
    read_full_policy,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def prove_current_future_dated_history(
    session: Session, user_id: UUID, full_policy_view: FullPolicyView, now: datetime
) -> FutureDatedHistoryProof:
    """No writes/clock override/cache. UNKNOWN never supplies a numeric unpaid amount."""
    _read_snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    if user is None or not user.is_simulated or user.timezone not in {"UTC", "Asia/Shanghai"}:
        raise PolicyLifecycleError("INVALID_FUTURE_DATED_USER", "当前模拟用户/日界未证明", 409)
    values: dict[str, Any] = {
        "status": "UNKNOWN",
        "user_id": user_id,
        "epoch_id": full_policy_view.epoch_id,
        "policy_id": full_policy_view.policy_id,
        "current_version_id": full_policy_view.current_version.version_id,
        "current_content_hash": full_policy_view.current_version.content_hash,
        "as_of": now,
        "timezone": user.timezone,
        "today": now.astimezone(ZoneInfo(user.timezone)).date(),
        "actual_version_count": None,
        "captured_version_count": 0,
        "actual_command_count": None,
        "captured_command_count": 0,
        "expected_evidence_count": None,
        "captured_evidence_count": 0,
        "policy_original": None,
        "user_original": row_copy(user),
        "epoch_original": None,
        "versions": [],
        "commands": [],
        "evidence_originals": [],
        "source_digest": "0" * 64,
        "reasons": [],
    }
    with session.no_autoflush:
        try:
            # Actual all-owner counts retain rows that a user-filtered list would omit.
            version_count = session.scalar(
                select(func.count())
                .select_from(FullPolicyVersion)
                .where(FullPolicyVersion.policy_id == full_policy_view.policy_id)
            )
            command_count = session.scalar(
                select(func.count())
                .select_from(FullPolicyCommand)
                .where(FullPolicyCommand.policy_id == full_policy_view.policy_id)
            )
            if type(version_count) is not int or type(command_count) is not int:
                raise ValueError("Original history counts unavailable")
            values["actual_version_count"], values["actual_command_count"] = (
                version_count,
                command_count,
            )
            if not (1 < version_count <= MAX_VERSIONS and 0 < command_count <= MAX_COMMANDS):
                raise ValueError("Future dated history capacity or multi-version scope unavailable")
            fresh = read_full_policy(session, user_id, full_policy_view.policy_id, now)
            if (
                fresh.model_dump(mode="json") != full_policy_view.model_dump(mode="json")
                or fresh.template_name != "DatedExpensePolicy"
                or not fresh.planning_confirmation_valid
                or fresh.reference_validation != "CURRENT"
                or fresh.effective_status not in {"ACTIVE", "CONFIRMED"}
            ):
                raise ValueError("Current policy/confirmation/reference binding changed")
            listed_versions = list_full_versions(session, user_id, fresh.policy_id, now).items
            listed_commands = list_full_commands(session, user_id, fresh.policy_id, now).items
            if len(listed_versions) != version_count or len(listed_commands) != command_count:
                raise ValueError("Actual complete denominator differs from verified owner lists")
            version_rows = list(
                session.scalars(
                    select(FullPolicyVersion)
                    .where(FullPolicyVersion.policy_id == fresh.policy_id)
                    .order_by(FullPolicyVersion.version_number)
                )
            )
            command_rows = list(
                session.scalars(
                    select(FullPolicyCommand)
                    .where(FullPolicyCommand.policy_id == fresh.policy_id)
                    .order_by(FullPolicyCommand.command_number)
                )
            )
            if [row.id for row in version_rows] != [row.version_id for row in listed_versions] or (
                [row.id for row in command_rows] != [row.command_id for row in listed_commands]
            ):
                raise ValueError("Captured originals differ from verified complete lists")
            values["versions"] = [row_copy(row) for row in version_rows]
            values["commands"] = [row_copy(row) for row in command_rows]
            values["captured_version_count"] = len(version_rows)
            values["captured_command_count"] = len(command_rows)
            evidence_ids: set[UUID] = set()
            for version in version_rows:
                if type(version.evidence_ids) is not list:
                    raise ValueError("Original evidence denominator unavailable")
                for text_id in version.evidence_ids:
                    if type(text_id) is not str or str(UUID(text_id)) != text_id:
                        raise ValueError("Original evidence identity not canonical")
                    evidence_ids.add(UUID(text_id))
            values["expected_evidence_count"] = len(evidence_ids)
            evidence_rows = list(
                session.scalars(
                    select(EvidenceItem)
                    .where(EvidenceItem.id.in_(evidence_ids))
                    .order_by(EvidenceItem.id)
                )
            )
            values["evidence_originals"] = [row_copy(row) for row in evidence_rows]
            values["captured_evidence_count"] = len(evidence_rows)
            policy = session.get(FullPolicy, fresh.policy_id)
            epoch = session.get(AuditEpoch, fresh.epoch_id)
            current_epoch = current_audit_epoch(session, user_id)
            if (
                policy is None
                or epoch is None
                or current_epoch is None
                or (
                    current_epoch.id != epoch.id
                    or current_epoch.user_id != user_id
                    or epoch.status != "OPEN"
                )
            ):
                raise ValueError("Current original OPEN epoch not proven")
            values["policy_original"], values["epoch_original"] = row_copy(policy), row_copy(epoch)
            candidate = FutureDatedHistoryProof.model_validate(values)
            candidate = candidate.model_copy(update={"source_digest": history_digest(candidate)})
            if not validate_future_dated_history(candidate):
                raise ValueError(
                    "Retained chain/evidence or strictly future old windows not proven"
                )
            return candidate.model_copy(update={"status": "VERIFIED_FUTURE_DATED_HISTORY"})
        except (PolicyLifecycleError, ValueError, TypeError, KeyError, OverflowError) as error:
            values["reasons"] = [str(error)]
            result = FutureDatedHistoryProof.model_validate(values)
            return result.model_copy(update={"source_digest": history_digest(result)})
