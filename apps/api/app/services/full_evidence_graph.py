"""Actual persisted originals and their typed links, in one read-only snapshot."""

import json
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from app.db import catalog_models, full_models, models
from app.db.base import Base
from app.domain import audit_chain as audit_domain
from app.domain.full_evidence_graph import (
    KIND_TABLES,
    SHARED_KINDS,
    FullEvidenceGraph,
    GraphInventory,
    GraphIssue,
    GraphOriginal,
    GraphProof,
    GraphReference,
    GraphRootKind,
    Kind,
    build_full_evidence_graph,
    node_key,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import SUBJECT_MODELS, audit_read_scope, row_copy
from app.services.decision_trace import get_decision_trace
from app.services.evidence_graph import readonly
from app.services.full_goals import read_full_goal_model
from app.services.full_policy_lifecycle import read_full_policy
from app.services.full_reconciliation import full_reconciliation
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.product_catalog import read_catalog
from sqlalchemy import Table, func, inspect, select
from sqlalchemy.orm import Session

# These imports register the actual mapped tables; no model or schema is synthesized.
REGISTERED_MODELS = (models, full_models, catalog_models)
ROW_LIMIT = 10000
POSTING_LIMIT = 100000
TOTAL_ROW_LIMIT = 120000
SOURCE_BYTE_LIMIT = 64 * 1024 * 1024
VERIFIED_RUN_LIMIT = 64
VERIFIED_FULL_POLICY_LIMIT = 32
KNOWLEDGE_FIELDS = frozenset(
    {
        "created_at",
        "observed_at",
        "confirmed_at",
        "completed_at",
        "updated_at",
        "requested_at",
        "settled_at",
        "projected_at",
        "accepted_at",
        "captured_at",
        "reconciled_at",
        "received_at",
        "acknowledged_at",
        "published_at",
        "started_at",
        "finished_at",
        "opened_at",
        "sealed_at",
        "occurred_at",
        "as_of",
    }
)


def _issue(code: str, reference: str, detail: str) -> GraphIssue:
    return GraphIssue(code=code, reference=reference, detail=detail)


def original_known_at(raw: dict[str, Any]) -> datetime:
    clocks = []
    for field in KNOWLEDGE_FIELDS & raw.keys():
        value = raw[field]
        if value is not None:
            if type(value) is not str:
                raise ValueError("Original knowledge timestamp is not canonical text")
            clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if clock.tzinfo is None or clock.utcoffset() is None:
                raise ValueError("Original knowledge timestamp has no timezone")
            clocks.append(clock)
    if not clocks:
        raise ValueError("Original has no actual knowledge timestamp")
    return max(clocks)


def _model_map() -> dict[str, Any]:
    return {cast(Table, mapper.local_table).name: mapper.class_ for mapper in Base.registry.mappers}


def _original_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, models.SimulatedBankPosting):
        return row_copy(row)
    # Old row_copy deliberately retains the v1 posting hash shape. This new view
    # needs every actual SQL column, including the independent ownership dimension.
    raw = {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}
    return cast(dict[str, Any], json.loads(audit_domain.canonical_text(raw, raw=True)))


def _capture(
    session: Session,
    user: UUID,
    known_at: datetime,
) -> tuple[list[GraphInventory], list[GraphOriginal], list[GraphIssue]]:
    mapped = _model_map()
    issues = []
    if set(mapped) != set(KIND_TABLES.values()):
        issues.append(
            _issue(
                "UNREGISTERED_PERSISTED_TABLE",
                "Base.metadata",
                "实际业务表与显式图分母不同: "
                + str(sorted(set(mapped) ^ set(KIND_TABLES.values()))),
            )
        )
    inventory: list[GraphInventory] = []
    originals: list[GraphOriginal] = []
    retained_bytes = 0
    for kind, table_name in KIND_TABLES.items():
        model = mapped.get(table_name)
        if model is None:
            issues.append(_issue("REGISTERED_TABLE_ABSENT", table_name, "实际映射表不存在"))
            continue
        table = model.__table__
        query = select(model)
        if kind not in SHARED_KINDS:
            owner = model.id == user if kind == "USER" else model.user_id == user
            query = query.where(owner)
        clocks = [column for column in table.columns if column.name in KNOWLEDGE_FIELDS]
        known = clocks[0] if len(clocks) == 1 else func.greatest(*clocks)
        actual_count = session.scalar(select(func.count()).select_from(query.subquery()))
        eligible = query.where(known <= known_at)
        known_count = session.scalar(select(func.count()).select_from(eligible.subquery()))
        if type(actual_count) is not int or type(known_count) is not int:
            raise PolicyLifecycleError("GRAPH_DENOMINATOR_MISSING", "真实原行分母不可用", 409)
        limit = min(
            POSTING_LIMIT if kind == "POSTING" else ROW_LIMIT,
            max(0, TOTAL_ROW_LIMIT - len(originals)),
        )
        captured = []
        fetched: list[Any] = list(session.scalars(eligible.order_by(model.id).limit(limit)))
        for row in fetched:
            try:
                raw = _original_row(row)
                size = len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                if retained_bytes + size > SOURCE_BYTE_LIMIT:
                    issues.append(
                        _issue(
                            "GRAPH_SOURCE_BYTE_CAPACITY",
                            table_name,
                            "原行64MiB预算不足，分母仍保留",
                        )
                    )
                    break
                original = GraphOriginal(
                    kind=cast(Kind, kind),
                    id=row.id,
                    known_at=original_known_at(raw),
                    original=raw,
                    row_hash=configuration_hash(raw),
                )
            except (ValueError, TypeError) as error:
                issues.append(_issue("GRAPH_ORIGINAL_NOT_READABLE", f"{kind}:{row.id}", str(error)))
                continue
            captured.append(raw)
            originals.append(original)
            retained_bytes += size
        inventory.append(
            GraphInventory(
                kind=cast(Kind, kind),
                table=table_name,
                owner_scope="SHARED_PRODUCT_CATALOGUE" if kind in SHARED_KINDS else "CURRENT_USER",
                actual_owned_count=actual_count,
                known_count=known_count,
                captured_count=len(captured),
                complete=len(captured) == known_count,
                captured_rows_hash=configuration_hash({"rows": captured}),
            )
        )
    return inventory, originals, issues


def persisted_references(
    originals: list[GraphOriginal],
) -> tuple[list[GraphReference], list[GraphIssue]]:
    """Exact FK columns and explicitly typed JSON fields; no arbitrary UUID search."""
    references: list[GraphReference] = []
    issues: list[GraphIssue] = []
    mapped = _model_map()
    tables = {table: kind for kind, table in KIND_TABLES.items()}
    subjects = {
        kind: tables[model.__tablename__]
        for kind, model in SUBJECT_MODELS.items()
        if model.__tablename__ in tables
    }
    subject_copies: dict[tuple[str, str, str, str], list[str]] = {}
    original_identities = {(row.kind, row.id) for row in originals}
    for row in originals:
        if row.kind == "AUDIT_SNAPSHOT":
            raw = row.original
            subject_copies.setdefault(
                (raw["epoch_id"], raw["kind"], raw["entity_id"], raw["snapshot_hash"]), []
            ).append(str(row.id))

    def add(row: GraphOriginal, target: str, value: Any, pointer: str, relation: str) -> None:
        if value is None:
            return
        try:
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("引用不是原canonical UUID")
        except (ValueError, AttributeError) as error:
            issues.append(
                _issue("INVALID_TYPED_REFERENCE", node_key(row.kind, row.id) + pointer, str(error))
            )
            return
        references.append(
            GraphReference(
                from_key=node_key(row.kind, row.id),
                to_key=node_key(target, value),
                relation=relation,
                pointer=pointer,
            )
        )

    for row in originals:
        raw = row.original
        table = mapped[KIND_TABLES[row.kind]].__table__
        for fk in table.foreign_keys:
            if fk.column.name == "id" and fk.parent.name != "user_id":
                target = tables.get(fk.column.table.name)
                if target is not None:
                    add(
                        row,
                        target,
                        raw.get(fk.parent.name),
                        "/" + fk.parent.name,
                        "PERSISTED_FOREIGN_KEY",
                    )
        for field, target in (
            ("evidence_ids", "EVIDENCE"),
            ("policy_version_ids", "POLICY_VERSION"),
        ):
            values = raw.get(field, [])
            if type(values) is not list:
                issues.append(
                    _issue(
                        "INVALID_REFERENCE_ARRAY",
                        node_key(row.kind, row.id) + "/" + field,
                        "原数组不可用",
                    )
                )
                continue
            for index, value in enumerate(values):
                add(row, target, value, f"/{field}/{index}", "PERSISTED_TYPED_ARRAY")
        if row.kind == "AUDIT_EVENT":
            try:
                event = audit_domain.parse_event(raw["canonical_text"])
                if (
                    event.id != row.id
                    or str(event.user_id) != raw["user_id"]
                    or event.event_hash != raw["event_hash"]
                    or event.payload.model_dump(mode="json") != raw["payload"]
                ):
                    raise ValueError("审计原列与canonical原文不同")
                for field, target in (
                    ("decision_run_id", "DECISION"),
                    ("action_plan_id", "ACTION"),
                    ("action_receipt_id", "RECEIPT"),
                ):
                    add(row, target, raw.get(field), "/" + field, "AUDIT_ORIGINAL_LINK")
                for index, ref in enumerate(event.payload.references):
                    key = (str(event.epoch_id), ref.kind, str(ref.id), ref.snapshot_hash)
                    copies = subject_copies.get(key, [])
                    if len(copies) != 1:
                        issues.append(
                            _issue(
                                "AUDIT_REFERENCE_COPY_NOT_UNIQUE",
                                node_key(row.kind, row.id),
                                "完整原副本不存在或不唯一: " + str(key),
                            )
                        )
                    else:
                        add(
                            row,
                            "AUDIT_SNAPSHOT",
                            copies[0],
                            f"/payload/references/{index}",
                            "EXACT_AUDIT_SUBJECT_SNAPSHOT",
                        )
            except (ValueError, TypeError, KeyError) as error:
                issues.append(
                    _issue("INVALID_AUDIT_EVENT_ORIGINAL", node_key(row.kind, row.id), str(error))
                )
        if row.kind == "AUDIT_SNAPSHOT":
            try:
                subject = audit_domain.parse_subject(raw["canonical_text"])
                if (
                    str(subject.id) != raw["entity_id"]
                    or subject.kind != raw["kind"]
                    or str(subject.user_id) != raw["user_id"]
                    or str(subject.epoch_id) != raw["epoch_id"]
                    or audit_domain.subject_hash(subject) != raw["snapshot_hash"]
                ):
                    raise ValueError("审计副本身份/hash与原列不同")
                # A deleted current row is not a missing retained archive copy.
                target = subjects.get(subject.kind)
                if target and (target, subject.id) in original_identities:
                    add(
                        row,
                        target,
                        str(subject.id),
                        "/entity_id",
                        "CURRENT_IDENTITY_OF_RETAINED_SNAPSHOT",
                    )
            except (ValueError, TypeError, KeyError) as error:
                issues.append(
                    _issue(
                        "INVALID_AUDIT_SNAPSHOT_ORIGINAL", node_key(row.kind, row.id), str(error)
                    )
                )
        if row.kind == "FULL_POLICY_VERSION":
            impact = raw.get("impact_analysis")
            refs = impact.get("reference_snapshots") if type(impact) is dict else None
            if type(refs) is not list:
                issues.append(
                    _issue(
                        "INVALID_FULL_REFERENCE_ARRAY",
                        node_key(row.kind, row.id),
                        "原完整引用分母不是数组",
                    )
                )
                continue
            for index, ref in enumerate(refs):
                if type(ref) is not dict or type(ref.get("kind")) is not str:
                    issues.append(
                        _issue("UNKNOWN_FULL_REFERENCE_KIND", node_key(row.kind, row.id), str(ref))
                    )
                    continue
                target = {
                    "ACCOUNT": "ACCOUNT",
                    "EVIDENCE": "EVIDENCE",
                    "GOAL": "GOAL",
                    "MVP_POLICY": "POLICY",
                    "FULL_POLICY": "FULL_POLICY",
                }.get(ref["kind"])
                if target is None:
                    issues.append(
                        _issue("UNKNOWN_FULL_REFERENCE_KIND", node_key(row.kind, row.id), str(ref))
                    )
                    continue
                add(
                    row,
                    target,
                    ref.get("id"),
                    f"/impact_analysis/reference_snapshots/{index}/id",
                    "FULL_CONFIRMED_ORIGINAL_REFERENCE",
                )
                versions = ref.get("version_ids", [])
                if type(versions) is not list:
                    issues.append(
                        _issue(
                            "INVALID_FULL_REFERENCE_ARRAY",
                            node_key(row.kind, row.id),
                            "原版本引用不是数组",
                        )
                    )
                    continue
                for version in versions:
                    if target in {"POLICY", "FULL_POLICY"}:
                        add(
                            row,
                            "POLICY_VERSION" if target == "POLICY" else "FULL_POLICY_VERSION",
                            version,
                            f"/impact_analysis/reference_snapshots/{index}/version_ids",
                            "FULL_REFERENCE_VERSION",
                        )
        if row.kind in {"POLICY_VERSION", "FULL_POLICY_VERSION"}:
            confirmation = raw.get("confirmation", {})
            if type(confirmation) is dict:
                add(
                    row,
                    "EVIDENCE",
                    confirmation.get("confirmation_evidence_id"),
                    "/confirmation/confirmation_evidence_id",
                    "ORIGINAL_CONFIRMATION_EVIDENCE",
                )
        if row.kind == "EVIDENCE" and raw.get("source_type") == "FULL_GOAL_MODEL_V1":
            content = raw.get("content", {})
            if type(content) is not dict:
                issues.append(
                    _issue(
                        "INVALID_FULL_GOAL_MODEL_CONTENT",
                        node_key(row.kind, row.id),
                        "原内容不是object",
                    )
                )
                continue
            for field, target in (
                ("goal_id", "GOAL"),
                ("policy_id", "POLICY"),
                ("base_policy_version_id", "POLICY_VERSION"),
                ("epoch_id", "AUDIT_EPOCH"),
            ):
                add(
                    row,
                    target,
                    content.get(field),
                    "/content/" + field,
                    "FULL_GOAL_MODEL_ORIGINAL_BINDING",
                )
        if row.kind == "EVIDENCE" and raw.get("source_type") == "FULL_POLICY_CONFIRMATION":
            content = raw.get("content")
            if type(content) is not dict:
                issues.append(
                    _issue(
                        "INVALID_FULL_CONFIRMATION_CONTENT",
                        node_key(row.kind, row.id),
                        "原确认内容不是object",
                    )
                )
                continue
            for field, target in (
                ("policy_id", "FULL_POLICY"),
                ("version_id", "FULL_POLICY_VERSION"),
                ("epoch_id", "AUDIT_EPOCH"),
            ):
                add(
                    row,
                    target,
                    content.get(field),
                    "/content/" + field,
                    "ORIGINAL_FULL_PLANNING_CONFIRMATION",
                )
        if row.kind == "BANK_OPERATION":
            add(
                row,
                "POSITION",
                raw.get("closing_position_id"),
                "/closing_position_id",
                "BANK_CLOSING_POSITION",
            )
        if row.kind in {"COMMAND_OUTBOX", "ASSET_BATCH"}:
            add(
                row,
                "ACTION",
                raw.get("action_plan_id"),
                "/action_plan_id",
                "ORIGINAL_EXECUTION_BINDING",
            )
        if row.kind == "ASSET_CONSENT":
            add(row, "EVIDENCE", raw.get("evidence_id"), "/evidence_id", "ORIGINAL_USER_CONSENT")
        if row.kind == "INTERVENTION_OUTBOX":
            add(
                row,
                "DECISION",
                raw.get("source_run_id"),
                "/source_run_id",
                "ORIGINAL_INTERVENTION_OBSERVATION",
            )
    return references, issues


def _proof(state: str, check: str, refs: list[str], detail: str) -> GraphProof:
    return GraphProof(state=cast(Any, state), check=check, original_refs=refs, detail=detail)


def full_evidence_graph(
    session: Session,
    user: UUID,
    kind: GraphRootKind,
    identity: UUID,
    known_at: datetime,
    now: datetime,
) -> FullEvidenceGraph:
    readonly(session)
    if (
        any(clock.tzinfo is None or clock.utcoffset() is None for clock in (known_at, now))
        or known_at > now
    ):
        raise PolicyLifecycleError(
            "INVALID_GRAPH_KNOWLEDGE_TIME", "知识时点必须有时区且不晚于服务器", 422
        )
    with session.no_autoflush, audit_read_scope(session):
        inventory, originals, issues = _capture(session, user, known_at)
        references, reference_issues = persisted_references(originals)
        issues.extend(reference_issues)
        root_kind = "EVIDENCE" if kind == "FULL_GOAL_MODEL" else kind
        root = node_key(root_kind, identity)
        by_key = {node_key(row.kind, row.id): row for row in originals}
        if root not in by_key:
            raise PolicyLifecycleError(
                "GRAPH_ROOT_NOT_FOUND", "当前owner/知识时点不存在原图根", 404
            )
        report = full_reconciliation(session, user, now)
        audit = _proof(
            "VERIFIED" if report.audit.status == "VALID" else "UNKNOWN",
            "ORIGINAL_COMPLETE_AUDIT_CHAIN",
            [],
            report.audit.status,
        )
        bank = _proof(
            "VERIFIED" if report.bank_ledger_verified else "UNKNOWN",
            "INDEPENDENT_BANK_LEDGER_CHAIN",
            ["RECONCILIATION:" + report.input_hash],
            "原独立银行账本完整核验；不表示应用投影或权限已满足",
        )
        proofs: dict[str, GraphProof] = {}
        for value in [*report.account_cash, *report.position_principals]:
            target = "ACCOUNT" if value.kind == "ACCOUNT_CASH" else "POSITION"
            refs = [node_key("POSTING", value.bank_head.posting_id)] if value.bank_head else []
            proofs[node_key(target, value.entity_id)] = _proof(
                "VERIFIED" if value.state == "MATCHED" else "UNKNOWN",
                "ACTUAL_BANK_APPLICATION_AMOUNT",
                refs,
                value.state,
            )
        for ownership in report.goal_ownership:
            verified = (
                ownership.current_ownership_proof_verified
                and ownership.cash.state == ownership.principal.state == "MATCHED"
            )
            refs = (
                [node_key("EVIDENCE", ownership.ownership_evidence_id)]
                if ownership.ownership_evidence_id
                else []
            )
            proofs[node_key("GOAL", ownership.goal_id)] = _proof(
                "VERIFIED" if verified else "UNKNOWN",
                "ACTUAL_GOAL_OWNERSHIP_DIMENSIONS",
                refs,
                "原银行产权维度与当前完整归属证明",
            )
        for action_result in report.actions:
            for operation in action_result.bank_operation_ids:
                proofs[node_key("BANK_OPERATION", operation)] = _proof(
                    "VERIFIED" if action_result.service_receipt_verified else "NOT_CHECKED",
                    "ORIGINAL_OPERATION_RECEIPT_LINK",
                    [node_key("ACTION", action_result.action_id)],
                    action_result.state + "; no new authority is inferred",
                )
            for receipt in action_result.receipt_ids:
                proofs[node_key("RECEIPT", receipt)] = _proof(
                    "VERIFIED" if action_result.service_receipt_verified else "UNKNOWN",
                    "ORIGINAL_SERVICE_RECEIPT_AND_BANK_LEGS",
                    [
                        node_key("ACTION", action_result.action_id),
                        *[
                            node_key("BANK_OPERATION", row)
                            for row in action_result.bank_operation_ids
                        ],
                    ],
                    action_result.state,
                )
        for posting in report.bank_postings:
            proofs[node_key("POSTING", posting.posting_id)] = _proof(
                "VERIFIED" if report.bank_ledger_verified else "UNKNOWN",
                "ORIGINAL_INDEPENDENT_POSTING_CHAIN",
                [node_key("POSTING", posting.posting_id)],
                "独立账本逐腿/序号/余额连续性；不将虚拟产权腿当现金",
            )
        for row in originals:
            key = node_key(row.kind, row.id)
            if row.kind == "EVIDENCE":
                content = row.original.get("content")
                valid = type(content) is dict and configuration_hash(content) == row.original.get(
                    "content_hash"
                )
                proofs[key] = _proof(
                    "VERIFIED" if valid else "UNKNOWN",
                    "ORIGINAL_EVIDENCE_CONTENT_HASH_ONLY",
                    [key],
                    "仅核原内容hash，声明等级/当前授权另有原服务门",
                )
            elif row.kind in {"AUDIT_EVENT", "AUDIT_SNAPSHOT", "AUDIT_EPOCH"}:
                proofs[key] = _proof(
                    audit.state,
                    "ORIGINAL_COMPLETE_AUDIT_REPORT",
                    [key],
                    "完整原链报告及逐行typed副本检查；历史receipt不授新权限",
                )
        # Determine reachable originals without an additional database read or verification pass.
        adjacent: dict[str, set[str]] = {}
        for ref in references:
            adjacent.setdefault(ref.from_key, set()).add(ref.to_key)
            adjacent.setdefault(ref.to_key, set()).add(ref.from_key)
        pending, connected = [root], set()
        while pending:
            key = pending.pop()
            if key in connected:
                continue
            connected.add(key)
            pending.extend(adjacent.get(key, set()) - connected)
        decisions = sorted(
            key for key in connected if key.startswith("DECISION:") and key in by_key
        )
        if len(decisions) > VERIFIED_RUN_LIMIT:
            issues.append(
                _issue(
                    "TYPED_TRACE_VERIFICATION_CAPACITY",
                    root,
                    f"真实关联decisions={len(decisions)}，验真预算={VERIFIED_RUN_LIMIT}",
                )
            )
        for key in decisions[:VERIFIED_RUN_LIMIT]:
            try:
                view = get_decision_trace(session, user, by_key[key].id, now)
                complete = (
                    view.completeness == "COMPLETE"
                    and view.audit_chain_status == "VALID"
                    and view.trace is not None
                )
                proofs[key] = _proof(
                    "VERIFIED" if complete else "UNKNOWN",
                    "ORIGINAL_TYPED_DECISION_TRACE",
                    [key],
                    view.completeness + "/" + view.audit_chain_status,
                )
                if view.trace:
                    for source in view.trace.sources:
                        references.append(
                            GraphReference(
                                from_key=key,
                                to_key=node_key("EVIDENCE", source.id),
                                relation="VERIFIED_FROZEN_TRACE_SOURCE",
                                pointer="/input_snapshot/decision_trace/sources",
                            )
                        )
                    for policy in view.trace.policies:
                        references.append(
                            GraphReference(
                                from_key=key,
                                to_key=node_key("POLICY_VERSION", policy.id),
                                relation="VERIFIED_FROZEN_TRACE_POLICY",
                                pointer="/input_snapshot/decision_trace/policies",
                            )
                        )
            except (PolicyLifecycleError, ValueError) as error:
                proofs[key] = _proof("UNKNOWN", "ORIGINAL_TYPED_DECISION_TRACE", [key], str(error))
        policies = sorted(
            key for key in connected if key.startswith("FULL_POLICY:") and key in by_key
        )
        if len(policies) > VERIFIED_FULL_POLICY_LIMIT:
            issues.append(_issue("FULL_POLICY_VERIFICATION_CAPACITY", root, str(len(policies))))
        for key in policies[:VERIFIED_FULL_POLICY_LIMIT]:
            try:
                view_full = read_full_policy(session, user, by_key[key].id, now)
                proofs[key] = _proof(
                    "VERIFIED",
                    "ORIGINAL_FULL_POLICY_VERSION_COMMAND_CHAIN",
                    [key],
                    "原版本/命令/确认hash完整核验；reference_validation="
                    + view_full.reference_validation,
                )
            except (PolicyLifecycleError, ValueError) as error:
                proofs[key] = _proof(
                    "UNKNOWN", "ORIGINAL_FULL_POLICY_VERSION_COMMAND_CHAIN", [key], str(error)
                )
        for key in sorted(connected & by_key.keys()):
            row = by_key[key]
            if row.kind == "EVIDENCE" and row.original.get("source_type") == "FULL_GOAL_MODEL_V1":
                try:
                    full_goal = read_full_goal_model(
                        session, user, UUID(row.original["content"]["goal_id"]), now
                    )
                    verified = full_goal.status == "VERIFIED" and full_goal.evidence_id == row.id
                    proofs[key] = _proof(
                        "VERIFIED" if verified else "UNKNOWN",
                        "ORIGINAL_CURRENT_FULL_GOAL_MODEL",
                        [key],
                        full_goal.status,
                    )
                except (PolicyLifecycleError, ValueError, KeyError) as error:
                    proofs[key] = _proof(
                        "UNKNOWN", "ORIGINAL_CURRENT_FULL_GOAL_MODEL", [key], str(error)
                    )
        if any(key.startswith(("PRODUCT:", "PRODUCT_CATALOGUE:")) for key in connected):
            catalogue = read_catalog(session, now)
            for key in sorted(connected & by_key.keys()):
                if by_key[key].kind in {"PRODUCT", "PRODUCT_CATALOGUE"}:
                    proofs[key] = _proof(
                        "UNKNOWN",
                        "IMMUTABLE_PRODUCT_ORIGINAL_BINDING",
                        [key],
                        ";".join(catalogue.issues) or "原登记快照没有匹配该产品",
                    )
            for version in catalogue.versions:
                proofs[node_key("PRODUCT", version.product_id)] = _proof(
                    "VERIFIED" if version.current_source_matched else "UNKNOWN",
                    "CURRENT_PRODUCT_MATCHES_IMMUTABLE_ORIGINAL",
                    [node_key("PRODUCT_CATALOGUE", version.id)],
                    "当前真实产品与原不可变登记逐列hash比较；不授银行权限",
                )
                proofs[node_key("PRODUCT_CATALOGUE", version.id)] = _proof(
                    "VERIFIED" if version.immutable_original_verified else "UNKNOWN",
                    "ORIGINAL_IMMUTABLE_CATALOGUE",
                    [node_key("PRODUCT", version.product_id)],
                    "旧版本原快照验真，current_source_matched="
                    + str(version.current_source_matched),
                )
        try:
            return build_full_evidence_graph(
                user=user,
                root_kind=kind,
                root_id=identity,
                now=now,
                known_at=known_at,
                inventory=inventory,
                originals=originals,
                references=references,
                proofs=proofs,
                audit=audit,
                bank=bank,
                source_issues=issues,
            )
        except LookupError as error:
            raise PolicyLifecycleError("GRAPH_ROOT_NOT_FOUND", str(error), 404) from error
