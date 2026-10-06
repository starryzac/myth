"""Read-only bitemporal evidence and persisted provenance references.

These views grant no execution authority. They do not change old evidence,
canonical audit text or historical hashes.
"""

from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from app.db.models import (
    ActionPlan,
    ActionReceipt,
    DecisionRun,
    EvidenceItem,
    Policy,
    PolicyProposal,
    PolicyVersion,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import row_copy
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

Kind = Literal["EVIDENCE", "PROPOSAL", "POLICY", "POLICY_VERSION", "DECISION", "ACTION", "RECEIPT"]
GraphModel = (
    type[EvidenceItem]
    | type[PolicyProposal]
    | type[Policy]
    | type[PolicyVersion]
    | type[DecisionRun]
    | type[ActionPlan]
    | type[ActionReceipt]
)
GraphRecord = (
    EvidenceItem
    | PolicyProposal
    | Policy
    | PolicyVersion
    | DecisionRun
    | ActionPlan
    | ActionReceipt
)
MODELS: dict[str, GraphModel] = {
    "EVIDENCE": EvidenceItem,
    "PROPOSAL": PolicyProposal,
    "POLICY": Policy,
    "POLICY_VERSION": PolicyVersion,
    "DECISION": DecisionRun,
    "ACTION": ActionPlan,
    "RECEIPT": ActionReceipt,
}
FACT_LIMIT = 10000
NODE_LIMIT = 512


class FactView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: UUID
    user_id: UUID
    evidence_level: str
    source_type: str
    source_ref: str
    content: dict[str, Any]
    content_hash: str
    valid_from: datetime
    valid_to: datetime | None
    observed_at: datetime
    supersedes_id: UUID | None
    status: str


def clocks(valid_at: datetime, known_at: datetime, now: datetime) -> None:
    if any(
        value.tzinfo is None or value.utcoffset() is None for value in (valid_at, known_at, now)
    ):
        raise PolicyLifecycleError("INVALID_READ_TIME", "查询时点必须包含时区", 422)
    if known_at > now:
        raise PolicyLifecycleError("FUTURE_KNOWLEDGE", "不能查询尚未观察到的系统知识", 422)


def readonly(session: Session) -> None:
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("DIRTY_READ_SESSION", "事实读取不能携带未提交写入", 409)
    if (
        session.scalar(text("SHOW transaction_isolation")) != "repeatable read"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError("READ_ONLY_SNAPSHOT_REQUIRED", "需要独立一致的只读快照", 409)


def select_facts(
    rows: Sequence[FactView], valid_at: datetime, known_at: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve only corrections known then and valid in the requested interval."""
    eligible = {
        row.id: row
        for row in rows
        if row.observed_at <= known_at
        and row.valid_from <= valid_at
        and (row.valid_to is None or valid_at < row.valid_to)
    }
    issues: list[dict[str, Any]] = []
    suppressed: set[UUID] = set()
    for row in eligible.values():
        if row.supersedes_id is None:
            continue
        parent = eligible.get(row.supersedes_id)
        if parent is None:
            # A predecessor outside this valid interval is legitimate. A missing
            # predecessor in the retained known inventory is not proved away.
            if row.supersedes_id not in {value.id for value in rows}:
                issues.append({"code": "MISSING_SUPERSEDED_ORIGINAL", "id": str(row.id)})
            continue
        if (
            parent.user_id != row.user_id
            or (parent.source_type, parent.source_ref) != (row.source_type, row.source_ref)
            or parent.observed_at > row.observed_at
        ):
            issues.append({"code": "INVALID_SUPERSESSION", "id": str(row.id)})
            continue
        chain: set[UUID] = {row.id}
        cursor: FactView | None = parent
        while cursor is not None:
            if cursor.id in chain:
                issues.append({"code": "SUPERSESSION_CYCLE", "id": str(row.id)})
                break
            chain.add(cursor.id)
            cursor = eligible.get(cursor.supersedes_id) if cursor.supersedes_id else None
        else:
            suppressed.add(parent.id)
    groups: dict[tuple[str, str], list[FactView]] = defaultdict(list)
    for row in eligible.values():
        if row.id not in suppressed:
            groups[(row.source_type, row.source_ref)].append(row)
    result: list[dict[str, Any]] = []
    for identity, values in sorted(groups.items()):
        hashes = {row.content_hash for row in values}
        unknown = False
        for row in values:
            if configuration_hash(row.content) != row.content_hash:
                unknown = True
                issues.append({"code": "CONTENT_HASH_MISMATCH", "id": str(row.id)})
            if row.status in {"UNKNOWN", "SUPERSEDED"}:
                unknown = True
                issues.append({"code": "STATUS_HISTORY_NOT_PROVEN", "id": str(row.id)})
        state = (
            "CONFLICTED"
            if len(hashes) > 1 or any(row.status == "CONFLICTED" for row in values)
            else "UNKNOWN"
            if unknown
            else "VALID"
        )
        result.append(
            {
                "source_type": identity[0],
                "source_ref": identity[1],
                "state": state,
                "evidence_ids": sorted(str(row.id) for row in values),
                "originals": [
                    row.model_dump(mode="json")
                    for row in sorted(values, key=lambda row: (row.observed_at, str(row.id)))
                ],
                "execution_authority": False,
            }
        )
    return result, issues


def facts_at(
    session: Session,
    user_id: UUID,
    valid_at: datetime,
    known_at: datetime,
    now: datetime,
    source_type: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    clocks(valid_at, known_at, now)
    readonly(session)
    query = select(EvidenceItem).where(
        EvidenceItem.user_id == user_id, EvidenceItem.observed_at <= known_at
    )
    if source_type is not None:
        query = query.where(EvidenceItem.source_type == source_type)
    if source_ref is not None:
        query = query.where(EvidenceItem.source_ref == source_ref)
    rows = list(
        session.scalars(
            query.order_by(EvidenceItem.observed_at, EvidenceItem.id).limit(FACT_LIMIT + 1)
        )
    )
    over_capacity = len(rows) > FACT_LIMIT
    groups, issues = select_facts(
        [FactView.model_validate(row) for row in rows[:FACT_LIMIT]], valid_at, known_at
    )
    if over_capacity:
        issues.append({"code": "FACT_CAPACITY_EXCEEDED", "limit": FACT_LIMIT})
    if not groups:
        issues.append({"code": "NO_COVERED_FACTS"})
    return {
        "schema_version": "bitemporal-fact-view-v1",
        "simulation": True,
        "user_id": str(user_id),
        "valid_at": valid_at.isoformat(),
        "known_at": known_at.isoformat(),
        "interval_semantics": "VALID_FROM_INCLUSIVE_VALID_TO_EXCLUSIVE",
        "state": "CONFLICTED"
        if any(group["state"] == "CONFLICTED" for group in groups)
        else "UNKNOWN"
        if issues or any(group["state"] == "UNKNOWN" for group in groups)
        else "VALID",
        "groups": groups,
        "issues": issues,
        "complete_within_registered_capacity": not over_capacity,
        "historical_status_reconstructed": False,
        "execution_authority": False,
    }


def node_references(kind: str, row: Any) -> list[tuple[str, UUID, str]]:
    refs: list[tuple[str, UUID, str]] = []
    lists = {
        "evidence_ids": ("EVIDENCE", "SUPPORTED_BY"),
        "policy_version_ids": ("POLICY_VERSION", "USES_VERSION"),
    }
    for field, (target, relation) in lists.items():
        for value in getattr(row, field, []):
            refs.append((target, UUID(value), relation))
    singular = {
        "EVIDENCE": [("supersedes_id", "EVIDENCE", "SUPERSEDES")],
        "PROPOSAL": [("confirmed_policy_id", "POLICY", "CONFIRMED_AS")],
        "POLICY_VERSION": [],
        "DECISION": [("parent_run_id", "DECISION", "DERIVED_FROM")],
        "ACTION": [
            ("decision_run_id", "DECISION", "GENERATED_BY"),
            ("policy_version_id", "POLICY_VERSION", "USES_VERSION"),
        ],
        "RECEIPT": [("action_plan_id", "ACTION", "EXECUTION_OF")],
    }
    for field, target, relation in singular.get(kind, []):
        value = getattr(row, field)
        if value is not None:
            refs.append((target, value, relation))
    return sorted(set(refs), key=lambda ref: (ref[0], str(ref[1]), ref[2]))


def evidence_graph(
    session: Session,
    user_id: UUID,
    kind: Kind,
    identity: UUID,
    known_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    clocks(now, known_at, now)
    readonly(session)
    queue: deque[tuple[str, UUID]] = deque([(kind, identity)])
    visited: set[tuple[str, UUID]] = set()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    while queue:
        current_kind, current_id = queue.popleft()
        if (current_kind, current_id) in visited:
            continue
        if len(visited) >= NODE_LIMIT:
            issues.append({"code": "GRAPH_CAPACITY_EXCEEDED", "limit": NODE_LIMIT})
            break
        visited.add((current_kind, current_id))
        model = MODELS[current_kind]
        row = cast(
            GraphRecord | None,
            session.scalar(select(model).where(model.id == current_id, model.user_id == user_id)),
        )
        if row is None:
            if not nodes:
                raise PolicyLifecycleError("NOT_FOUND", "当前用户的证据图根不存在", 404)
            issues.append({"code": "BROKEN_REFERENCE", "kind": current_kind, "id": str(current_id)})
            continue
        observed = (
            getattr(row, "observed_at", None)
            or getattr(row, "confirmed_at", None)
            or getattr(row, "as_of", None)
            or getattr(row, "occurred_at", None)
            or row.created_at
        )
        if observed > known_at:
            if not nodes:
                raise PolicyLifecycleError(
                    "NOT_KNOWN_AT_QUERY_TIME", "该原件在查询知识时点尚未知", 404
                )
            issues.append(
                {"code": "REFERENCE_NOT_KNOWN", "kind": current_kind, "id": str(current_id)}
            )
            continue
        key = current_kind + ":" + str(current_id)
        data = row_copy(row)
        nodes.append({"key": key, "kind": current_kind, "id": str(current_id), "original": data})
        if isinstance(row, EvidenceItem) and configuration_hash(row.content) != row.content_hash:
            issues.append({"code": "CONTENT_HASH_MISMATCH", "id": str(row.id)})
        try:
            references = node_references(current_kind, row)
        except (TypeError, ValueError):
            issues.append(
                {"code": "INVALID_PERSISTED_REFERENCE", "kind": current_kind, "id": str(current_id)}
            )
            continue
        if current_kind == "POLICY":
            references += [
                ("POLICY_VERSION", value.id, "HAS_VERSION")
                for value in session.scalars(
                    select(PolicyVersion)
                    .where(PolicyVersion.user_id == user_id, PolicyVersion.policy_id == current_id)
                    .order_by(PolicyVersion.version_number)
                    .limit(NODE_LIMIT + 1)
                )
            ]
        for target_kind, target_id, relation in references:
            edges.append(
                {"from": key, "to": target_kind + ":" + str(target_id), "relation": relation}
            )
            queue.append((target_kind, target_id))
    return {
        "schema_version": "persisted-evidence-graph-v1",
        "simulation": True,
        "user_id": str(user_id),
        "known_at": known_at.isoformat(),
        "root": kind + ":" + str(identity),
        "nodes": nodes,
        "edges": edges,
        "issues": issues,
        "state": "UNKNOWN" if issues else "REFERENCES_RESOLVED",
        "execution_authority": False,
        "historical_status_reconstructed": False,
        "uncovered": [
            "Full bank-source revision ingestion and validation in this view",
            "External bank anchor verification in this view",
            "Exact historical mutable action/policy status",
        ],
    }
