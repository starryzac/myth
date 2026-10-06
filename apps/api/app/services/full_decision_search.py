"""Bounded exact search in one clean RR/read-only invocation, with actual denominators."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from app.db.full_models import FullPolicyVersion
from app.db.models import ActionPlan, AuditEpoch, AuditEvent, DecisionRun, PolicyVersion, User
from app.domain.full_decision_search import (
    MAX_SCAN_BYTES,
    MAX_SCAN_ROWS,
    DecisionSearchInventory,
    DecisionSearchItem,
    DecisionSearchQuery,
    DecisionSearchResponse,
    SearchReference,
    typed_policy_references,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.decision_trace import _stored_trace
from app.services.evidence_graph import readonly
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

UNSUPPORTED = [
    "FULL_POLICY_VERSION_SPECIALIZED_PROOF_FILTER",
    "SEALED_ARCHIVE_DECISION_SEARCH",
    "FREE_TEXT_OR_INFERRED_UUID_REFERENCES",
]


def _error(code: str, detail: str, status: int = 409) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, detail, status)


def _visible_run(now: datetime) -> Any:
    # Business decision clocks differ from the audit epoch's wall-clock append time.
    return and_(
        DecisionRun.created_at <= now,
        DecisionRun.as_of <= now,
        or_(DecisionRun.completed_at.is_(None), DecisionRun.completed_at <= now),
    )


def search_decisions(
    session: Session, user_id: UUID, query: DecisionSearchQuery, now: datetime
) -> DecisionSearchResponse:
    query = DecisionSearchQuery.model_validate(query.model_dump(mode="python"))
    if now.tzinfo is None or now.utcoffset() is None:
        raise _error("INVALID_SEARCH_CLOCK", "搜索时钟必须包含时区", 422)
    now = now.astimezone(UTC)
    readonly(session)
    with session.no_autoflush:
        if session.scalar(select(User.id).where(User.id == user_id)) is None:
            raise _error("NOT_FOUND", "搜索拥有者不存在", 404)
        action = None
        if query.action_id is not None or query.action_key is not None:
            action = session.scalar(
                select(ActionPlan).where(
                    ActionPlan.user_id == user_id,
                    ActionPlan.id == query.action_id
                    if query.action_id is not None
                    else ActionPlan.idempotency_key == query.action_key,
                    ActionPlan.created_at <= now,
                )
            )
            if action is None:
                raise _error("NOT_FOUND", "原动作不存在、属于他人或尚未知悉", 404)
            if configuration_hash(action.request) != action.request_hash:
                raise _error("ACTION_SEARCH_INTEGRITY_ERROR", "原动作请求摘要不一致")
        family: Literal["NONE", "MVP", "FULL_UNSUPPORTED"] = "NONE"
        if query.policy_version_id is not None:
            version = session.scalar(
                select(PolicyVersion).where(
                    PolicyVersion.user_id == user_id,
                    PolicyVersion.id == query.policy_version_id,
                    PolicyVersion.created_at <= now,
                )
            )
            full_version = session.scalar(
                select(FullPolicyVersion).where(
                    FullPolicyVersion.user_id == user_id,
                    FullPolicyVersion.id == query.policy_version_id,
                    FullPolicyVersion.created_at <= now,
                )
            )
            if version is None and full_version is None:
                raise _error("NOT_FOUND", "原策略版本不存在、属于他人或尚未知悉", 404)
            if version is not None and full_version is not None:
                raise _error("AMBIGUOUS_VERSION_IDENTITY", "版本在两种协议中存在冲突身份")
            if (
                version is not None
                and configuration_hash(version.configuration) != version.content_hash
            ):
                raise _error("VERSION_SEARCH_INTEGRITY_ERROR", "原策略版本配置摘要不一致")
            family = "MVP" if version is not None else "FULL_UNSUPPORTED"
        if (
            query.epoch_id is not None
            and session.scalar(
                select(AuditEpoch.id).where(
                    AuditEpoch.user_id == user_id, AuditEpoch.id == query.epoch_id
                )
            )
            is None
        ):
            raise _error("NOT_FOUND", "原审计轮次不存在或属于他人", 404)

        owned = DecisionRun.user_id == user_id
        visible = and_(owned, _visible_run(now))
        selected = visible
        if action is not None:
            selected = and_(
                selected,
                or_(
                    DecisionRun.subject_action_plan_id == action.id,
                    DecisionRun.id == action.decision_run_id,
                ),
            )
        if query.epoch_id is not None:
            selected = and_(
                selected,
                select(AuditEvent.id)
                .where(
                    AuditEvent.user_id == user_id,
                    AuditEvent.epoch_id == query.epoch_id,
                    AuditEvent.decision_run_id == DecisionRun.id,
                    AuditEvent.event_type == "DECISION_RECORDED",
                    AuditEvent.occurred_at <= now,
                )
                .exists(),
            )
        actual_count = int(
            session.scalar(select(func.count()).select_from(DecisionRun).where(owned)) or 0
        )
        known_count = int(
            session.scalar(select(func.count()).select_from(DecisionRun).where(visible)) or 0
        )
        scope_count = int(
            session.scalar(select(func.count()).select_from(DecisionRun).where(selected)) or 0
        )
        run_ids = select(DecisionRun.id).where(selected)
        action_scope = and_(
            ActionPlan.user_id == user_id,
            ActionPlan.decision_run_id.in_(run_ids),
            ActionPlan.created_at <= now,
        )
        event_scope = and_(
            AuditEvent.user_id == user_id,
            AuditEvent.decision_run_id.in_(run_ids),
            AuditEvent.event_type == "DECISION_RECORDED",
            AuditEvent.occurred_at <= now,
        )
        action_count = int(
            session.scalar(select(func.count()).select_from(ActionPlan).where(action_scope)) or 0
        )
        event_count = int(
            session.scalar(select(func.count()).select_from(AuditEvent).where(event_scope)) or 0
        )
        bytes_count = int(
            session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            func.octet_length(cast(DecisionRun.input_snapshot, String))
                            + func.octet_length(cast(DecisionRun.result, String))
                        ),
                        0,
                    )
                ).where(selected)
            )
            or 0
        )
        bytes_count += int(
            session.scalar(
                select(
                    func.coalesce(func.sum(func.octet_length(cast(ActionPlan.request, String))), 0)
                ).where(action_scope)
            )
            or 0
        )
        bytes_count += int(
            session.scalar(
                select(
                    func.coalesce(func.sum(func.octet_length(AuditEvent.canonical_text)), 0)
                ).where(event_scope)
            )
            or 0
        )
        issues: list[str] = []
        if (
            max(scope_count, action_count, event_count) > MAX_SCAN_ROWS
            or bytes_count > MAX_SCAN_BYTES
        ):
            issues.append("SOURCE_CAPACITY_EXCEEDED")
            rows: list[DecisionRun] = []
            all_actions: list[ActionPlan] = []
            all_events: list[AuditEvent] = []
        else:
            rows = list(
                session.scalars(
                    select(DecisionRun)
                    .where(selected)
                    .order_by(DecisionRun.as_of.desc(), DecisionRun.id.desc())
                )
            )
            if len(rows) != scope_count:
                issues.append("INCOMPLETE_DECISION_DENOMINATOR")
            all_actions = list(
                session.scalars(select(ActionPlan).where(action_scope).order_by(ActionPlan.id))
            )
            all_events = list(
                session.scalars(select(AuditEvent).where(event_scope).order_by(AuditEvent.id))
            )
            if len(all_actions) != action_count or len(all_events) != event_count:
                issues.append("INCOMPLETE_ORIGINAL_LINK_DENOMINATOR")
        actions_by_run: dict[UUID, list[ActionPlan]] = {}
        for linked in all_actions:
            if linked.user_id != user_id or linked.created_at > now:
                raise _error("SEARCH_OWNER_OR_CLOCK_MISMATCH", "原动作关联拥有者或知悉时间不一致")
            if configuration_hash(linked.request) != linked.request_hash:
                issues.append("LINKED_ACTION_REQUEST_HASH_INVALID")
            actions_by_run.setdefault(linked.decision_run_id, []).append(linked)
        events_by_run: dict[UUID, list[AuditEvent]] = {}
        for event in all_events:
            if event.user_id != user_id or event.occurred_at > now or event.decision_run_id is None:
                raise _error("SEARCH_OWNER_OR_CLOCK_MISMATCH", "原审计关联拥有者或业务时间不一致")
            events_by_run.setdefault(event.decision_run_id, []).append(event)
        matched: list[DecisionSearchItem] = []
        verified_count = 0
        unknown_count = 0
        originals = []
        if family == "FULL_UNSUPPORTED":
            issues.append("FULL_VERSION_TYPED_FAMILIES_NOT_ADAPTED")
        for row in rows:
            if (
                row.user_id != user_id
                or row.as_of > now
                or row.created_at > now
                or (row.completed_at is not None and row.completed_at > now)
            ):
                raise _error("SEARCH_OWNER_OR_CLOCK_MISMATCH", "原决策身份或知悉时间不一致")
            originals.append(row_copy(row))
            refs: list[SearchReference] = []
            action_ids = set()
            if row.subject_action_plan_id is not None:
                action_ids.add(row.subject_action_plan_id)
                refs.append(
                    SearchReference(
                        kind="ACTION",
                        identity=row.subject_action_plan_id,
                        pointer="/subject_action_plan_id",
                        relation="PERSISTED_FOREIGN_KEY",
                    )
                )
            linked_actions = actions_by_run.get(row.id, [])
            for linked in linked_actions:
                action_ids.add(linked.id)
                refs.append(
                    SearchReference(
                        kind="ACTION",
                        identity=linked.id,
                        pointer=f"action_plans/{linked.id}/decision_run_id",
                        relation="PERSISTED_FOREIGN_KEY",
                    )
                )
            epochs = events_by_run.get(row.id, [])
            epoch_ids = sorted({event.epoch_id for event in epochs if event.epoch_id is not None})
            for event in epochs:
                if event.epoch_id is not None:
                    refs.append(
                        SearchReference(
                            kind="AUDIT_EPOCH",
                            identity=event.epoch_id,
                            pointer=f"audit_events/{event.id}/epoch_id",
                            relation="PERSISTED_AUDIT_LINK",
                        )
                    )
            row_issues: list[str] = []
            if len(epoch_ids) > 1:
                row_issues.append("AMBIGUOUS_RUN_EPOCH_BINDING")
                issues.append("AMBIGUOUS_RUN_EPOCH_BINDING")
            completeness: Literal["COMPLETE", "LEGACY_PARTIAL", "UNSUPPORTED_VERSION", "INVALID"]
            trace = None
            try:
                completeness, trace = _stored_trace(session, row)
                if trace is not None:
                    refs.extend(typed_policy_references(trace))
                if completeness != "COMPLETE":
                    row_issues.append(completeness)
            except (PolicyLifecycleError, ValueError, TypeError, KeyError):
                completeness = "INVALID"
                row_issues.append("ORIGINAL_TRACE_NOT_VERIFIABLE")
            typed_complete = completeness == "COMPLETE" and trace is not None
            if typed_complete:
                verified_count += 1
            else:
                unknown_count += 1
            policy_match = query.policy_version_id is None or any(
                ref.kind == "MVP_POLICY_VERSION" and ref.identity == query.policy_version_id
                for ref in refs
            )
            if family == "FULL_UNSUPPORTED":
                row_issues.append("FULL_VERSION_TYPED_FAMILIES_NOT_ADAPTED")
                policy_match = False
            if not policy_match and typed_complete and family != "FULL_UNSUPPORTED":
                continue
            match_state: Literal["MATCHED", "UNVERIFIABLE"] = (
                "MATCHED"
                if policy_match and (query.policy_version_id is None or typed_complete)
                else "UNVERIFIABLE"
            )
            matched.append(
                DecisionSearchItem(
                    run_id=row.id,
                    as_of=row.as_of,
                    trigger_type=row.trigger_type,
                    record_status=row.status,
                    action_ids=sorted(action_ids),
                    epoch_ids=epoch_ids,
                    phase=trace.phase if trace else None,
                    snapshot_hash=row.snapshot_hash,
                    trace_hash=trace.trace_hash if trace else None,
                    completeness=completeness,
                    match_state=match_state,
                    references=refs,
                    issues=row_issues,
                )
            )
        if unknown_count:
            issues.append("UNVERIFIABLE_ORIGINALS_INCLUDED_IN_DENOMINATOR")
        count_complete = not issues
        source_hash = (
            configuration_hash(
                {
                    "query": query.model_dump(mode="json", exclude={"limit", "offset"}),
                    "user_id": str(user_id),
                    "decisions": originals,
                    "items": [item.model_dump(mode="json") for item in matched],
                    "action_links": [row_copy(linked) for linked in all_actions],
                    "audit_links": [row_copy(event) for event in all_events],
                    "action_request_hash": action.request_hash if action else None,
                }
            )
            if len(rows) == scope_count
            else None
        )
        page = matched[query.offset : query.offset + query.limit]
        return DecisionSearchResponse(
            user_id=user_id,
            read_at=now,
            business_known_at=now,
            query=query,
            resolved_action_id=action.id if action else None,
            version_family=family,
            state="SEARCHED" if count_complete else "UNKNOWN",
            inventory=DecisionSearchInventory(
                actual_owned_decision_count=actual_count,
                known_decision_count=known_count,
                selected_scope_count=scope_count,
                captured_scope_count=len(rows),
                source_bytes=bytes_count,
                action_link_count=action_count,
                captured_action_link_count=len(all_actions),
                audit_link_count=event_count,
                captured_audit_link_count=len(all_events),
                verified_typed_count=verified_count,
                unverifiable_count=unknown_count,
                returned_count=len(page),
            ),
            source_hash=source_hash,
            verified_match_count=sum(
                item.match_state == "MATCHED" and item.completeness == "COMPLETE"
                for item in matched
            ),
            total_match_count=len(matched) if count_complete else None,
            items=page,
            next_offset=query.offset + query.limit
            if query.offset + query.limit < len(matched)
            else None,
            issues=issues,
            unsupported_families=UNSUPPORTED,
        )
