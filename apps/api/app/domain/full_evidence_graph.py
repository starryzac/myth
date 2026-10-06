"""Finite persisted-reference navigation. A resolved link is never financial authority."""

from collections import deque
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.policy_configuration import configuration_hash
from pydantic import AwareDatetime, Field, StrictBool, StrictInt

Kind = Literal[
    "ACCOUNT",
    "TRANSACTION",
    "BILL",
    "GOAL",
    "POSITION",
    "BANK_OPERATION",
    "BANK_REDEMPTION",
    "POSTING",
    "EXTERNAL_FACT",
    "AUDIT_EPOCH",
    "AUDIT_EVENT",
    "AUDIT_SNAPSHOT",
    "FULL_POLICY",
    "FULL_POLICY_VERSION",
    "FULL_POLICY_COMMAND",
    "EVIDENCE",
    "PROPOSAL",
    "POLICY",
    "POLICY_VERSION",
    "DECISION",
    "ACTION",
    "RECEIPT",
    "PRODUCT",
    "PRODUCT_CATALOGUE",
    "USER",
    "CONSTRAINT",
    "RESOURCE_CLAIM",
    "COMMAND_OUTBOX",
    "COMMAND_INBOX",
    "COMMAND_ATTEMPT",
    "INTERVENTION_OUTBOX",
    "INTERVENTION_INBOX",
    "ASSET_PORTFOLIO",
    "ASSET_BATCH",
    "ASSET_CONSENT",
]
GraphRootKind = Kind | Literal["FULL_GOAL_MODEL"]
KIND_TABLES: dict[str, str] = {
    "ACCOUNT": "accounts",
    "TRANSACTION": "transactions",
    "BILL": "credit_card_bills",
    "GOAL": "goals",
    "POSITION": "asset_positions",
    "BANK_OPERATION": "bank_operations",
    "BANK_REDEMPTION": "simulated_bank_redemptions",
    "POSTING": "simulated_bank_postings",
    "EXTERNAL_FACT": "external_bank_facts",
    "AUDIT_EPOCH": "audit_epochs",
    "AUDIT_EVENT": "audit_events",
    "AUDIT_SNAPSHOT": "audit_subject_snapshots",
    "FULL_POLICY": "full_policies",
    "FULL_POLICY_VERSION": "full_policy_versions",
    "FULL_POLICY_COMMAND": "full_policy_commands",
    "EVIDENCE": "evidence_items",
    "PROPOSAL": "policy_proposals",
    "POLICY": "policies",
    "POLICY_VERSION": "policy_versions",
    "DECISION": "decision_runs",
    "ACTION": "action_plans",
    "RECEIPT": "action_receipts",
    "PRODUCT": "asset_products",
    "PRODUCT_CATALOGUE": "product_catalog_versions",
    "USER": "users",
    "CONSTRAINT": "decision_constraints",
    "RESOURCE_CLAIM": "action_resource_reservations",
    "COMMAND_OUTBOX": "command_outbox",
    "COMMAND_INBOX": "command_inbox",
    "COMMAND_ATTEMPT": "command_delivery_attempts",
    "INTERVENTION_OUTBOX": "intervention_outbox",
    "INTERVENTION_INBOX": "intervention_inbox",
    "ASSET_PORTFOLIO": "full_asset_execution_portfolios",
    "ASSET_BATCH": "full_asset_execution_batches",
    "ASSET_CONSENT": "full_asset_execution_consents",
}
SHARED_KINDS = frozenset({"PRODUCT", "PRODUCT_CATALOGUE"})
IMMUTABLE_HISTORICAL_KINDS = frozenset(
    {
        "POLICY_VERSION",
        "FULL_POLICY_VERSION",
        "FULL_POLICY_COMMAND",
        "RECEIPT",
        "POSTING",
        "AUDIT_EVENT",
        "AUDIT_SNAPSHOT",
        "PRODUCT_CATALOGUE",
        "ASSET_PORTFOLIO",
        "ASSET_BATCH",
        "ASSET_CONSENT",
    }
)
MAX_NODES = 2048
MAX_EDGES = 20000
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[StrictInt, Field(ge=0)]


class GraphIssue(BoundaryModel):
    code: str
    reference: str
    detail: str


class GraphInventory(BoundaryModel):
    kind: Kind
    table: str
    owner_scope: Literal["CURRENT_USER", "SHARED_PRODUCT_CATALOGUE"]
    actual_owned_count: Count
    known_count: Count
    captured_count: Count
    complete: StrictBool
    captured_rows_hash: Hash


class GraphProof(BoundaryModel):
    state: Literal["VERIFIED", "UNKNOWN", "NOT_APPLICABLE", "NOT_CHECKED"]
    check: str
    original_refs: list[str] = Field(default_factory=list)
    detail: str
    grants_authority: Literal[False] = False


class GraphOriginal(BoundaryModel):
    kind: Kind
    id: UUID
    known_at: AwareDatetime
    original: dict[str, Any]
    row_hash: Hash


class GraphReference(BoundaryModel):
    from_key: str
    to_key: str
    relation: str
    pointer: str


class GraphNode(BoundaryModel):
    key: str
    kind: Kind
    id: UUID
    owner_scope: Literal["CURRENT_USER", "SHARED_PRODUCT_CATALOGUE"]
    known_at: AwareDatetime
    original: dict[str, Any] | None
    row_hash: Hash | None
    source_classification: str | None
    proof: GraphProof
    historical_mutable_state_reconstructed: Literal[False] = False
    execution_authority: Literal[False] = False


class FullEvidenceGraph(BoundaryModel):
    protocol: Literal["persisted-full-evidence-graph-v2"] = "persisted-full-evidence-graph-v2"
    simulation: Literal[True] = True
    read_only: Literal[True] = True
    user_id: UUID
    as_of: AwareDatetime
    known_at: AwareDatetime
    root: str
    requested_root_kind: GraphRootKind
    inventory: list[GraphInventory]
    complete_registered_inventory: StrictBool
    expected_node_count: Count
    displayed_node_count: Count
    expected_edge_count: Count
    nodes: list[GraphNode]
    edges: list[GraphReference]
    issues: list[GraphIssue]
    state: Literal["REFERENCES_RESOLVED", "UNKNOWN"]
    audit_proof: GraphProof
    bank_proof: GraphProof
    financial_success_inferred: Literal[False] = False
    grants_authority: Literal[False] = False
    performs_repair: Literal[False] = False
    input_hash: Hash
    limitations: list[str]


def node_key(kind: str, identity: UUID | str) -> str:
    return kind + ":" + str(identity)


def build_full_evidence_graph(
    *,
    user: UUID,
    root_kind: GraphRootKind,
    root_id: UUID,
    now: datetime,
    known_at: datetime,
    inventory: list[GraphInventory],
    originals: list[GraphOriginal],
    references: list[GraphReference],
    proofs: dict[str, GraphProof],
    audit: GraphProof,
    bank: GraphProof,
    source_issues: list[GraphIssue] | None = None,
) -> FullEvidenceGraph:
    if (
        any(value.tzinfo is None or value.utcoffset() is None for value in (now, known_at))
        or known_at > now
    ):
        raise ValueError("Knowledge clock must be aware and cannot be in the future")
    issues = list(source_issues or [])
    rows: dict[str, GraphOriginal] = {}
    for item in originals:
        key = node_key(item.kind, item.id)
        if key in rows:
            issues.append(
                GraphIssue(
                    code="DUPLICATE_ORIGINAL_ID",
                    reference=key,
                    detail="原行重复，不能合并为完整来源",
                )
            )
            continue
        if (
            item.original.get("id") != str(item.id)
            or item.kind == "USER"
            and item.id != user
            or item.kind not in SHARED_KINDS | {"USER"}
            and item.original.get("user_id") != str(user)
            or item.kind in SHARED_KINDS
            and "user_id" in item.original
            or configuration_hash(item.original) != item.row_hash
            or item.known_at > known_at
        ):
            issues.append(
                GraphIssue(
                    code="ORIGINAL_OWNER_HASH_OR_KNOWLEDGE_DIFFERS",
                    reference=key,
                    detail="原行owner、hash或知悉时间不一致",
                )
            )
            continue
        rows[key] = item
    if {item.kind for item in inventory} != set(KIND_TABLES) or len(inventory) != len(KIND_TABLES):
        issues.append(
            GraphIssue(
                code="REGISTERED_TABLE_DENOMINATOR_MISSING",
                reference="inventory",
                detail="注册表分母缺失或重复",
            )
        )
    complete = len(inventory) == len(KIND_TABLES)
    for table_inventory in inventory:
        captured = sorted(
            (row.original for row in rows.values() if row.kind == table_inventory.kind),
            key=lambda row: row["id"],
        )
        expected_scope = (
            "SHARED_PRODUCT_CATALOGUE" if table_inventory.kind in SHARED_KINDS else "CURRENT_USER"
        )
        valid = (
            table_inventory.table == KIND_TABLES[table_inventory.kind]
            and table_inventory.owner_scope == expected_scope
            and table_inventory.known_count <= table_inventory.actual_owned_count
            and table_inventory.captured_count == len(captured)
            and table_inventory.captured_count <= table_inventory.known_count
            and table_inventory.complete
            == (table_inventory.captured_count == table_inventory.known_count)
            and table_inventory.captured_rows_hash == configuration_hash({"rows": captured})
        )
        if not valid or not table_inventory.complete:
            issues.append(
                GraphIssue(
                    code="TABLE_INVENTORY_NOT_COMPLETE",
                    reference=table_inventory.table,
                    detail=(
                        f"actual={table_inventory.actual_owned_count}, "
                        f"known={table_inventory.known_count}, "
                        f"captured={table_inventory.captured_count}"
                    ),
                )
            )
            complete = False
    kind = "EVIDENCE" if root_kind == "FULL_GOAL_MODEL" else root_kind
    root = node_key(kind, root_id)
    if (
        root not in rows
        or root_kind == "FULL_GOAL_MODEL"
        and rows[root].original.get("source_type") != "FULL_GOAL_MODEL_V1"
    ):
        raise LookupError("当前用户在该知识时点的图根原件不存在或不符合来源类型")
    links: dict[str, list[GraphReference]] = {}
    unique = sorted({(ref.from_key, ref.to_key, ref.relation, ref.pointer) for ref in references})
    for first, last, relation, pointer in unique:
        ref = GraphReference(from_key=first, to_key=last, relation=relation, pointer=pointer)
        if first not in rows:
            issues.append(
                GraphIssue(
                    code="REFERENCE_SOURCE_MISSING",
                    reference=first,
                    detail="引用来源不在实际原分母内",
                )
            )
            continue
        links.setdefault(first, []).append(ref)
        links.setdefault(last, []).append(ref)
    queue = deque([root])
    visited: set[str] = set()
    chosen: dict[tuple[str, str, str, str], GraphReference] = {}
    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        if key not in rows:
            issues.append(
                GraphIssue(
                    code="REFERENCE_UNAVAILABLE",
                    reference=key,
                    detail="同owner/知识时点内缺原引用，不能借他人或未来原件",
                )
            )
            continue
        for ref in links.get(key, []):
            chosen[(ref.from_key, ref.to_key, ref.relation, ref.pointer)] = ref
            queue.append(ref.to_key if ref.from_key == key else ref.from_key)
    available = sorted(key for key in visited if key in rows)
    if len(available) > MAX_NODES or len(chosen) > MAX_EDGES:
        issues.append(
            GraphIssue(
                code="GRAPH_PRESENTATION_CAPACITY",
                reference=root,
                detail=f"真实连通nodes={len(available)}, edges={len(chosen)}；不缩小分母冒完成",
            )
        )
    nodes: list[GraphNode] = []
    for key in available[:MAX_NODES]:
        item = rows[key]
        historical_missing = known_at < now and item.kind not in IMMUTABLE_HISTORICAL_KINDS
        proof = proofs.get(
            key,
            GraphProof(
                state="NOT_CHECKED",
                check="NAVIGATION_ONLY",
                detail="找到实际原引用，不等于金融验真",
            ),
        )
        if historical_missing:
            issues.append(
                GraphIssue(
                    code="MUTABLE_HISTORICAL_VALUE_NOT_RECONSTRUCTED",
                    reference=key,
                    detail="当前可变行不能当过去值，原内容不返回",
                )
            )
        if proof.state == "UNKNOWN":
            issues.append(
                GraphIssue(code="SOURCE_PROOF_UNKNOWN", reference=key, detail=proof.detail)
            )
        nodes.append(
            GraphNode(
                key=key,
                kind=item.kind,
                id=item.id,
                owner_scope="SHARED_PRODUCT_CATALOGUE"
                if item.kind in SHARED_KINDS
                else "CURRENT_USER",
                known_at=item.known_at,
                original=None if historical_missing else item.original,
                row_hash=None if historical_missing else item.row_hash,
                source_classification=item.original.get("source_type")
                if item.kind == "EVIDENCE"
                else None,
                proof=proof
                if not historical_missing
                else GraphProof(
                    state="UNKNOWN",
                    check="HISTORICAL_MUTABLE_VALUE",
                    detail="没有该过去时点的不可变原快照",
                ),
            )
        )
    if audit.state != "VERIFIED" or bank.state != "VERIFIED":
        issues.append(
            GraphIssue(
                code="INDEPENDENT_INTEGRITY_NOT_FULLY_VERIFIED",
                reference=root,
                detail="原审计/银行账本未完整验真；不推定成功",
            )
        )
    selected_keys = {row.key for row in nodes}
    edges = [
        ref
        for ref in chosen.values()
        if ref.from_key in selected_keys and ref.to_key in selected_keys
    ][:MAX_EDGES]
    basis = {
        "user_id": str(user),
        "known_at": known_at.isoformat(),
        "as_of": now.isoformat(),
        "root": root,
        "inventory": [row.model_dump(mode="json") for row in inventory],
        "row_hashes": sorted([key, row.row_hash] for key, row in rows.items()),
        "references": [list(value) for value in unique],
        "proofs": {key: value.model_dump(mode="json") for key, value in sorted(proofs.items())},
        "audit": audit.model_dump(mode="json"),
        "bank": bank.model_dump(mode="json"),
    }
    return FullEvidenceGraph(
        user_id=user,
        as_of=now,
        known_at=known_at,
        root=root,
        requested_root_kind=root_kind,
        inventory=inventory,
        complete_registered_inventory=complete
        and not any(
            item.code
            in {
                "REGISTERED_TABLE_DENOMINATOR_MISSING",
                "ORIGINAL_OWNER_HASH_OR_KNOWLEDGE_DIFFERS",
                "DUPLICATE_ORIGINAL_ID",
                "UNREGISTERED_PERSISTED_TABLE",
                "REGISTERED_TABLE_ABSENT",
            }
            for item in issues
        ),
        expected_node_count=len(available),
        displayed_node_count=len(nodes),
        expected_edge_count=len(chosen),
        nodes=nodes,
        edges=edges,
        issues=issues,
        state="UNKNOWN" if issues else "REFERENCES_RESOLVED",
        audit_proof=audit,
        bank_proof=bank,
        input_hash=configuration_hash(basis),
        limitations=[
            "Resolved IDs do not prove financial success, authorization, or causation",
            "Only registered persisted tables and exact typed reference fields are covered",
            "Historical mutable values are omitted rather than inferred",
            "Read-only view; no version, financial fact, grant, or historical hash is changed",
        ],
    )
