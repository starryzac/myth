"""Explicit lossless joint-source-sequence DAG; transport integrity never authority.

Runtime draft only. V2/V3 budgets and financial consumers remain untouched. DAG
nodes are closed SourceReference sequences, never arbitrary recursive objects.
"""

import base64
import binascii
import copy
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

from app.domain.full_joint_goal_archive import (
    MAX_DECODED_BYTES as MAX_DECODED_BYTES,
)
from app.domain.full_joint_goal_archive import (
    MAX_DEPTH as MAX_DEPTH,
)
from app.domain.full_joint_goal_archive import (
    MAX_NODES as MAX_NODES,
)
from app.domain.full_joint_goal_archive import (
    JointArchiveError,
    _constant,
    _gzip,
    _inflate,
    _json_budget,
    _pairs,
)
from app.domain.full_joint_goal_archive_protocol import _canonical
from app.domain.policy_configuration import configuration_hash

PROTOCOL = "joint-goal-source-sequence-archive-v4"
DAG_PROTOCOL = "joint-goal-source-sequence-dag-v1"
CODEC = "joint-source-dag-canonical-json-gzip-mtime0-base64-v1"
MARKER = "$joint_source_sequence_v4"
NODE_KIND = "ORDERED_SOURCE_REFERENCE_SEQUENCE"
MAX_SEQUENCES = 64
MAX_SEQUENCE_ITEMS = 1000
MAX_REFERENCE_OCCURRENCES = 4096
MAX_AMPLIFICATION = 64
MAX_WIRE_BYTES = 10 * 1024 * 1024
# One exact SourceReference object has canonical size 185 and four value nodes.
# Logical expanded work is bounded by unchanged bytes + shared structure, rather
# than replacing the old generic expanded-JSON one-million-node decoder cap.
MIN_SOURCE_REFERENCE_BYTES = 185
MAX_LOGICAL_NODES = MAX_NODES + 4 * (MAX_DECODED_BYTES // MIN_SOURCE_REFERENCE_BYTES)
Kind = Literal["INPUT", "PLAN", "TRACE_INPUTS", "TRACE_OUTCOME", "PLAN_FIELDS_SIZE_ONLY"]
KINDS = {"INPUT", "PLAN", "TRACE_INPUTS", "TRACE_OUTCOME", "PLAN_FIELDS_SIZE_ONLY"}
TRACE_PROTOCOL = "joint-goal-source-sequence-trace-v4"
PHASES = {"EVALUATION", "PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"}
FALSE_FLAGS = {
    "bank_authority": False,
    "grants_authority": False,
    "financial_write": False,
    "financial_validation_performed": False,
}
FIELDS = {
    "protocol",
    "codec",
    "record_kind",
    "binding",
    "shared_plaintext_sha256",
    "expanded_plaintext_sha256",
    "shared_bytes",
    "shared_nodes",
    "shared_depth",
    "expanded_bytes",
    "expanded_nodes",
    "expanded_depth",
    "reference_occurrences",
    "expanded_source_rows",
    "distinct_source_sequences",
    "compressed_bytes",
    "structure_hash",
    "payload_base64",
    "record_hash",
    *FALSE_FLAGS,
}
BINDING_KEYS = {"user_id", "epoch_id", "plan_id", "request_hash", "plan_hash", "plan_protocol"}
NODE_FIELDS = {"node_id", "kind", "value", "structure_hash"}
INPUT_PATHS: tuple[tuple[str, ...], ...] = (
    ("joint", "verified_source_refs"),
    ("joint", "original_actual_input", "dynamic_goals", "*", "data", "source_refs"),
    ("joint", "original_joint_input", "hard_protection_points", "*", "source_refs"),
    ("joint", "original_joint_input", "income_lots", "*", "source_refs"),
    ("joint", "actual_planning", "binding", "verified_source_refs"),
    (
        "joint",
        "actual_planning",
        "binding",
        "candidate",
        "hard_protection_points",
        "*",
        "source_refs",
    ),
    ("joint", "actual_planning", "binding", "candidate", "income_lots", "*", "source_refs"),
)
PLAN_PATHS = tuple(("inputs", *path) for path in INPUT_PATHS) + (
    ("allocation_input", "hard_protection_points", "*", "source_refs"),
    ("allocation_input", "income_lots", "*", "source_refs"),
)


def digest(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def canonical_uuid(value: Any) -> bool:
    try:
        return type(value) is str and len(value) == 36 and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def binding(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != BINDING_KEYS:
        raise JointArchiveError("V4 binding shape differs")
    if not all(canonical_uuid(value[key]) for key in ("user_id", "epoch_id", "plan_id")):
        raise JointArchiveError("V4 binding identity must be canonical UUID")
    if (
        not all(digest(value[key]) for key in ("request_hash", "plan_hash"))
        or type(value["plan_protocol"]) is not str
        or value["plan_protocol"]
        not in {
            "registered-joint-goal-execution-v2",
            "registered-joint-goal-execution-archive-v3",
            "registered-joint-goal-execution-source-dag-v4",
        }
    ):
        raise JointArchiveError("V4 original hash or protocol differs")
    return value.copy()


def paths(kind: Kind) -> tuple[tuple[str, ...], ...]:
    if kind == "INPUT":
        return INPUT_PATHS
    if kind in {"PLAN", "PLAN_FIELDS_SIZE_ONLY"}:
        return PLAN_PATHS
    if kind == "TRACE_INPUTS":
        return tuple(("joint_execution_input", *path) for path in INPUT_PATHS) + tuple(
            ("planning", "full_joint_goal_execution", "plan", *path) for path in PLAN_PATHS
        )
    return tuple(("joint_execution_plan", *path) for path in PLAN_PATHS)


def allowed(path: tuple[str, ...], kind: Kind) -> bool:
    return any(
        len(path) == len(pattern)
        and all(a == b or b == "*" and a.isdecimal() for a, b in zip(path, pattern, strict=True))
        for pattern in paths(kind)
    )


def source_sequence(value: Any, owner: str) -> None:
    if type(value) is not list or not 1 <= len(value) <= MAX_SEQUENCE_ITEMS:
        raise JointArchiveError("V4 source sequence exceeds closed item bound")
    seen: set[str] = set()
    for source in value:
        if type(source) is not dict or set(source) != {"user_id", "evidence_id", "content_hash"}:
            raise JointArchiveError(
                "V4 source nodes are leaf-only; cycle/nested/ambiguous refs forbidden"
            )
        if (
            source["user_id"] != owner
            or not canonical_uuid(source["evidence_id"])
            or not digest(source["content_hash"])
        ):
            raise JointArchiveError("V4 source owner, identity or hash differs")
        if source["evidence_id"] in seen:
            raise JointArchiveError("V4 duplicate source identity")
        seen.add(source["evidence_id"])


def pool_sources(value: dict[str, Any], bound: dict[str, Any], kind: Kind) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    references = 0
    root_nodes = 0
    by_sequence: dict[tuple[tuple[str, str, str], ...], str] = {}

    def visit(item: Any, path: tuple[str, ...]) -> Any:
        nonlocal references, root_nodes
        root_nodes += 1
        if root_nodes > MAX_NODES:
            raise JointArchiveError("V4 shared root node budget exceeded before allocation")
        if len(path) > MAX_DEPTH:
            raise JointArchiveError("V4 original depth budget exceeded")
        if type(item) is dict:
            if MARKER in item:
                raise JointArchiveError("V4 original has ambiguous reserved reference marker")
            if any(type(key) is not str for key in item):
                raise JointArchiveError("V4 original object keys must be strings")
            return {key: visit(child, (*path, key)) for key, child in item.items()}
        if type(item) is list:
            if path and path[-1] == "hard_protection_points" and len(item) > 1098:
                raise JointArchiveError("V4 whole original point count exceeds 1098")
            if path and path[-1] == "dynamic_goals" and len(item) > 8:
                raise JointArchiveError("V4 whole original goal count exceeds eight")
            if item and allowed(path, kind):
                source_sequence(item, bound["user_id"])
                references += 1
                if references > MAX_REFERENCE_OCCURRENCES:
                    raise JointArchiveError("V4 reference occurrence budget exceeded")
                core = {"kind": NODE_KIND, "value": item}
                fingerprint = tuple(
                    (row["user_id"], row["evidence_id"], row["content_hash"]) for row in item
                )
                identity = by_sequence.get(fingerprint)
                if identity is None:
                    identity = configuration_hash(core)
                    by_sequence[fingerprint] = identity
                nodes.setdefault(
                    identity, {"node_id": identity, **core, "structure_hash": identity}
                )
                if len(nodes) > MAX_SEQUENCES:
                    raise JointArchiveError("V4 distinct source sequence budget exceeded")
                return {MARKER: identity}
            return [visit(child, (*path, str(index))) for index, child in enumerate(item)]
        return item

    root = visit(value, ())
    return {
        "protocol": DAG_PROTOCOL,
        "root": root,
        "nodes": [nodes[identity] for identity in sorted(nodes)],
    }


@dataclass(frozen=True)
class LogicalMetrics:
    bytes: int
    nodes: int
    depth: int
    sha256: str
    references: int
    source_rows: int


class SharedJointOriginal:
    """Validated immutable-by-interface DAG; isolated reads copy, never return pool aliases."""

    def __init__(self, dag: dict[str, Any], bound: dict[str, Any], kind: Kind) -> None:
        if (
            type(dag) is not dict
            or set(dag) != {"protocol", "root", "nodes"}
            or dag["protocol"] != DAG_PROTOCOL
        ):
            raise JointArchiveError("V4 DAG shape differs")
        if (
            type(dag["root"]) is not dict
            or type(dag["nodes"]) is not list
            or len(dag["nodes"]) > MAX_SEQUENCES
        ):
            raise JointArchiveError("V4 root or node-table shape differs")
        self._root = copy.deepcopy(dag["root"])
        self._nodes: dict[str, list[dict[str, str]]] = {}
        self._cached: dict[str, tuple[bytes, int, int]] = {}
        self._bound = binding(bound)
        self.kind = kind
        order: list[str] = []
        for node in dag["nodes"]:
            if type(node) is not dict or set(node) != NODE_FIELDS or node["kind"] != NODE_KIND:
                raise JointArchiveError("V4 closed source-node shape differs")
            identity = node["node_id"]
            if not digest(identity) or identity in self._nodes:
                raise JointArchiveError("V4 invalid/duplicate source node")
            source_sequence(node["value"], self._bound["user_id"])
            if (
                configuration_hash({"kind": node["kind"], "value": node["value"]}) != identity
                or node["structure_hash"] != identity
            ):
                raise JointArchiveError("V4 source node structural hash differs")
            self._nodes[identity] = copy.deepcopy(node["value"])
            raw = json.dumps(
                node["value"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf8")
            self._cached[identity] = (raw, 1 + 4 * len(node["value"]), 2)
            order.append(identity)
        if order != sorted(order):
            raise JointArchiveError("V4 node table order differs")
        used: set[str] = set()

        def validate(item: Any, path: tuple[str, ...]) -> None:
            if len(path) > MAX_DEPTH:
                raise JointArchiveError("V4 root depth exceeded")
            if type(item) is dict:
                if MARKER in item:
                    if set(item) != {MARKER} or not allowed(path, kind):
                        raise JointArchiveError("V4 ambiguous or unregistered reference path")
                    identity = item[MARKER]
                    if type(identity) is not str or identity not in self._nodes:
                        raise JointArchiveError("V4 dangling source reference")
                    used.add(identity)
                else:
                    for key, child in item.items():
                        validate(child, (*path, key))
            elif type(item) is list:
                if path and path[-1] == "hard_protection_points" and len(item) > 1098:
                    raise JointArchiveError("V4 whole original point count exceeds 1098")
                if path and path[-1] == "dynamic_goals" and len(item) > 8:
                    raise JointArchiveError("V4 whole original goal count exceeds eight")
                if item and allowed(path, kind):
                    raise JointArchiveError("V4 ambiguous inline source sequence")
                for index, child in enumerate(item):
                    validate(child, (*path, str(index)))

        validate(self._root, ())
        if used != set(self._nodes):
            raise JointArchiveError("V4 unreachable source node")

    @property
    def bound(self) -> dict[str, Any]:
        return self._bound.copy()

    def logical_metrics(
        self,
        *,
        omit_root_key: str | None = None,
        root_path: tuple[str | int, ...] = (),
    ) -> LogicalMetrics:
        total = nodes = depth = references = source_rows = 0
        hasher = hashlib.sha256()

        def emit(raw: bytes) -> None:
            nonlocal total
            total += len(raw)
            if total > MAX_DECODED_BYTES:
                raise JointArchiveError("V4 expanded byte budget exceeded")
            hasher.update(raw)

        def visit(item: Any, level: int) -> None:
            nonlocal nodes, depth, references, source_rows
            if type(item) is dict and MARKER in item:
                identity = item[MARKER]
                raw, count, local_depth = self._cached[identity]
                references += 1
                source_rows += len(self._nodes[identity])
                nodes += count
                depth = max(depth, level + local_depth)
                if references > MAX_REFERENCE_OCCURRENCES:
                    raise JointArchiveError("V4 reference occurrence budget exceeded")
                emit(raw)
            else:
                nodes += 1
                depth = max(depth, level)
                if type(item) is dict:
                    emit(b"{")
                    keys = sorted(key for key in item if level != 0 or key != omit_root_key)
                    for index, key in enumerate(keys):
                        if index:
                            emit(b",")
                        emit(json.dumps(key, ensure_ascii=False).encode("utf8") + b":")
                        visit(item[key], level + 1)
                    emit(b"}")
                elif type(item) is list:
                    emit(b"[")
                    for index, child in enumerate(item):
                        if index:
                            emit(b",")
                        visit(child, level + 1)
                    emit(b"]")
                else:
                    emit(
                        json.dumps(
                            item, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                        ).encode("utf8")
                    )
            if nodes > MAX_LOGICAL_NODES or depth > MAX_DEPTH:
                raise JointArchiveError("V4 logical work/depth budget exceeded")

        root: Any = self._root
        for key in root_path:
            root = root[key]
        visit(root, 0)
        return LogicalMetrics(total, nodes, depth, hasher.hexdigest(), references, source_rows)

    def check_trusted_trace_scalars(self) -> None:
        """Keep all original monetary scalar/owner checks; transport is no verdict."""

        def visit(item: Any) -> None:
            if type(item) is dict:
                if "user_id" in item and item["user_id"] != self._bound["user_id"]:
                    raise JointArchiveError("V4 trace nested original owner differs")
                for key, child in item.items():
                    if key == "amount_options_cents" and child is not None:
                        if (
                            type(child) is not list
                            or not 2 <= len(child) <= 8
                            or any(type(x) is not int or not 0 < x <= 2**63 - 1 for x in child)
                        ):
                            raise JointArchiveError("V4 trace amount options require integer cents")
                    elif key.endswith("_cents") and child is not None:
                        if type(child) is not int or not -(2**63) <= child <= 2**63 - 1:
                            raise JointArchiveError(
                                "V4 trace monetary originals require integer cents"
                            )
                    visit(child)
            elif type(item) is list:
                for child in item:
                    visit(child)

        visit(self._root)

    def read(self, path: tuple[str | int, ...] = ()) -> Any:
        """Read all original values, or one bounded point; no financial verdict or alias."""
        item: Any = self._root
        for key in path:
            if type(item) is dict and MARKER in item:
                item = self._nodes[item[MARKER]]
            item = item[key]

        def expand(value: Any) -> Any:
            if type(value) is dict:
                if MARKER in value:
                    return copy.deepcopy(self._nodes[value[MARKER]])
                return {key: expand(child) for key, child in value.items()}
            if type(value) is list:
                return [expand(child) for child in value]
            return value

        return expand(item)

    def iter_points(self, path: tuple[str | int, ...]) -> Iterator[dict[str, Any]]:
        item: Any = self._root
        for key in path:
            item = item[key]
        if type(item) is not list or len(item) > 1098:
            raise JointArchiveError("V4 original point list shape differs")
        for index in range(len(item)):
            yield self.read((*path, index))

    def check_original_binding(self) -> None:
        from app.domain.full_joint_goal_execution import NAMESPACE

        bound = self._bound

        def check_input(prefix: tuple[str | int, ...]) -> None:
            request = self.read((*prefix, "request"))
            if (
                self.read((*prefix, "protocol")) != "joint-goal-execution-input-v2"
                or configuration_hash(request) != bound["request_hash"]
                or request["expected_epoch_id"] != bound["epoch_id"]
                or self.read((*prefix, "joint", "original_actual_input", "base", "user_id"))
                != bound["user_id"]
                or self.read((*prefix, "joint", "original_actual_input", "base", "epoch_id"))
                != bound["epoch_id"]
                or str(
                    uuid5(
                        NAMESPACE,
                        configuration_hash({"user": bound["user_id"], "request": request}),
                    )
                )
                != bound["plan_id"]
            ):
                raise JointArchiveError("V4 original request/owner/epoch/plan identity differs")

        def check_plan(prefix: tuple[str | int, ...]) -> None:
            check_input((*prefix, "inputs"))
            if (
                any(
                    self.read((*prefix, key)) != bound[key]
                    for key in ("user_id", "epoch_id", "plan_id", "plan_hash")
                )
                or self.read((*prefix, "protocol")) != bound["plan_protocol"]
            ):
                raise JointArchiveError("V4 complete plan binding differs")
            if (
                self.logical_metrics(root_path=prefix, omit_root_key="plan_hash").sha256
                != bound["plan_hash"]
            ):
                raise JointArchiveError("V4 original complete plan hash differs")

        try:
            if self.kind == "INPUT":
                check_input(())
            elif self.kind == "PLAN_FIELDS_SIZE_ONLY":
                if set(self._root) != {"inputs", "allocation_input"}:
                    raise JointArchiveError("V4 size-only mandatory-field shape differs")
                check_input(("inputs",))
            elif self.kind == "PLAN":
                check_plan(())
            elif self.kind == "TRACE_INPUTS":
                checked = False
                if "joint_execution_input" in self._root:
                    check_input(("joint_execution_input",))
                    checked = True
                if "planning" in self._root:
                    check_plan(("planning", "full_joint_goal_execution", "plan"))
                    checked = True
                if not checked:
                    raise JointArchiveError("V4 trace inputs lack whole original binding")
                self.check_trusted_trace_scalars()
            else:
                if "joint_execution_plan" in self._root:
                    check_plan(("joint_execution_plan",))
                elif not (
                    {"validation", "autonomy_level", "decision_status"} <= set(self._root)
                    and self._root["autonomy_level"] == "ASK_ONCE"
                    and self._root["decision_status"] == "COMPUTED"
                    and type(self._root["validation"]) is dict
                ):
                    raise JointArchiveError("V4 child outcome requires paired whole-original trace")
                # Child outcome has no substitute plan. The mandatory paired
                # trace reference below binds it to verified complete inputs;
                # native financial/consent validation must still run unchanged.
                self.check_trusted_trace_scalars()
        except (KeyError, TypeError, IndexError, AttributeError) as error:
            raise JointArchiveError("V4 original binding is malformed") from error


def encode_shared_joint(
    value: dict[str, Any], original_binding: dict[str, Any], kind: Kind
) -> dict[str, Any]:
    if kind not in KINDS:
        raise JointArchiveError("V4 record kind unsupported")
    bound = binding(original_binding)
    dag = pool_sources(value, bound, kind)
    raw, shared_nodes, shared_depth = _canonical(dag, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
    view = SharedJointOriginal(dag, bound, kind)
    view.check_original_binding()
    expanded = view.logical_metrics()
    if expanded.bytes > len(raw) * MAX_AMPLIFICATION:
        raise JointArchiveError("V4 expanded/shared byte amplification exceeds budget")
    compressed = _gzip(raw)
    record = {
        "protocol": PROTOCOL,
        "codec": CODEC,
        "record_kind": kind,
        "binding": bound,
        "shared_plaintext_sha256": hashlib.sha256(raw).hexdigest(),
        "expanded_plaintext_sha256": expanded.sha256,
        "shared_bytes": len(raw),
        "shared_nodes": shared_nodes,
        "shared_depth": shared_depth,
        "expanded_bytes": expanded.bytes,
        "expanded_nodes": expanded.nodes,
        "expanded_depth": expanded.depth,
        "reference_occurrences": expanded.references,
        "expanded_source_rows": expanded.source_rows,
        "distinct_source_sequences": len(dag["nodes"]),
        "compressed_bytes": len(compressed),
        "structure_hash": configuration_hash(dag),
        "payload_base64": base64.b64encode(compressed).decode("ascii"),
        **FALSE_FLAGS,
    }
    record["record_hash"] = configuration_hash(record)
    if len(json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf8")) > MAX_WIRE_BYTES:
        raise JointArchiveError("V4 unchanged JSONB wire budget exceeded")
    return record


def reference(record: dict[str, Any]) -> dict[str, Any]:
    """Caller must retain this in a verified original, never derive it from untrusted input."""
    if type(record) is not dict or set(record) != FIELDS:
        raise JointArchiveError("V4 original reference requires closed record shape")
    return {
        "protocol": "joint-goal-source-dag-reference-v4",
        "record_kind": record["record_kind"],
        "binding": copy.deepcopy(record["binding"]),
        "record_hash": record["record_hash"],
        "structure_hash": record["structure_hash"],
        "expanded_plaintext_sha256": record["expanded_plaintext_sha256"],
        **FALSE_FLAGS,
    }


def decode_shared_joint(
    record: Any,
    original_binding: dict[str, Any],
    kind: Kind,
    *,
    expected_reference: dict[str, Any],
) -> SharedJointOriginal:
    if kind not in KINDS:
        raise JointArchiveError("V4 record kind unsupported")
    if type(record) is not dict or set(record) != FIELDS:
        raise JointArchiveError("V4 closed envelope shape differs")
    bound = binding(original_binding)
    if (
        record["protocol"] != PROTOCOL
        or record["codec"] != CODEC
        or record["record_kind"] != kind
        or record["binding"] != bound
    ):
        raise JointArchiveError("V4 envelope protocol/codec/kind/binding differs")
    if expected_reference != reference(record):
        raise JointArchiveError("V4 verified original reference differs")
    if any(record[key] is not False for key in FALSE_FLAGS):
        raise JointArchiveError("V4 source archive cannot grant authority or claim validation")
    for key in (
        "shared_bytes",
        "shared_nodes",
        "shared_depth",
        "expanded_bytes",
        "expanded_nodes",
        "expanded_depth",
        "reference_occurrences",
        "expanded_source_rows",
        "distinct_source_sequences",
        "compressed_bytes",
    ):
        if type(record[key]) is not int or record[key] < 0:
            raise JointArchiveError("V4 budget metadata must be exact nonnegative integers")
    if (
        not 1 <= record["shared_bytes"] <= MAX_DECODED_BYTES
        or not 1 <= record["shared_nodes"] <= MAX_NODES
        or record["shared_depth"] > MAX_DEPTH
    ):
        raise JointArchiveError("V4 shared representation budget exceeded")
    if (
        not 1 <= record["expanded_bytes"] <= MAX_DECODED_BYTES
        or not 1 <= record["expanded_nodes"] <= MAX_LOGICAL_NODES
        or record["expanded_depth"] > MAX_DEPTH
        or record["reference_occurrences"] > MAX_REFERENCE_OCCURRENCES
        or record["distinct_source_sequences"] > MAX_SEQUENCES
    ):
        raise JointArchiveError("V4 expanded/reference/depth work budget exceeded")
    if record["expanded_bytes"] > record["shared_bytes"] * MAX_AMPLIFICATION:
        raise JointArchiveError("V4 expanded/shared byte amplification exceeds budget")
    if any(
        not digest(record[key])
        for key in (
            "record_hash",
            "structure_hash",
            "shared_plaintext_sha256",
            "expanded_plaintext_sha256",
        )
    ):
        raise JointArchiveError("V4 digests must be lowercase SHA256")
    if (
        configuration_hash({key: value for key, value in record.items() if key != "record_hash"})
        != record["record_hash"]
    ):
        raise JointArchiveError("V4 envelope hash differs")
    if len(json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf8")) > MAX_WIRE_BYTES:
        raise JointArchiveError("V4 unchanged JSONB wire budget exceeded")
    text = record["payload_base64"]
    if (
        type(text) is not str
        or not 1 <= record["compressed_bytes"] <= MAX_DECODED_BYTES + 1024 * 1024
        or len(text) > 4 * ((record["compressed_bytes"] + 2) // 3)
    ):
        raise JointArchiveError("V4 compressed/base64 shape or budget differs")
    try:
        compressed = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as error:
        raise JointArchiveError("V4 invalid base64") from error
    if (
        len(compressed) != record["compressed_bytes"]
        or base64.b64encode(compressed).decode("ascii") != text
    ):
        raise JointArchiveError("V4 compressed byte count/canonical encoding differs")
    raw = _inflate(compressed, record["shared_bytes"])
    if (
        len(raw) != record["shared_bytes"]
        or hashlib.sha256(raw).hexdigest() != record["shared_plaintext_sha256"]
    ):
        raise JointArchiveError("V4 shared plaintext length/hash differs")
    _json_budget(raw, MAX_NODES, MAX_DEPTH)
    try:
        dag = json.loads(raw.decode("utf8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise JointArchiveError("V4 shared JSON malformed") from error
    canonical, shared_nodes, shared_depth = _canonical(dag, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)
    if (
        canonical != raw
        or (shared_nodes, shared_depth) != (record["shared_nodes"], record["shared_depth"])
        or configuration_hash(dag) != record["structure_hash"]
    ):
        raise JointArchiveError("V4 shared canonical bytes/nodes/depth/structure hash differs")
    view = SharedJointOriginal(dag, bound, kind)
    view.check_original_binding()
    expanded = view.logical_metrics()
    expected = (
        expanded.bytes,
        expanded.nodes,
        expanded.depth,
        expanded.sha256,
        expanded.references,
        expanded.source_rows,
        len(dag["nodes"]),
    )
    actual = tuple(
        record[key]
        for key in (
            "expanded_bytes",
            "expanded_nodes",
            "expanded_depth",
            "expanded_plaintext_sha256",
            "reference_occurrences",
            "expanded_source_rows",
            "distinct_source_sequences",
        )
    )
    if expected != actual:
        raise JointArchiveError("V4 expanded original budget/hash/reference count differs")
    return view


def trace_reference(inputs: dict[str, Any], outcome: dict[str, Any], phase: str) -> dict[str, Any]:
    """Retain this exact pair in the original trace; do not derive from external input."""
    if inputs.get("binding") != outcome.get("binding"):
        raise JointArchiveError("V4 trace parts cannot bind different whole originals")
    value = {
        "protocol": TRACE_PROTOCOL,
        "phase": phase,
        "inputs": reference(inputs),
        "outcome": reference(outcome),
        **FALSE_FLAGS,
    }
    return {**value, "pair_hash": configuration_hash(value)}


def encode_shared_trace_parts(
    inputs: dict[str, Any],
    outcome: dict[str, Any],
    phase: str,
    original_binding: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if type(phase) is not str or phase not in PHASES:
        raise JointArchiveError("V4 trace phase differs")
    if phase == "EVALUATION":
        if "joint_execution_input" not in inputs or "joint_execution_plan" not in outcome:
            raise JointArchiveError("V4 evaluation trace requires complete whole input and plan")
    elif "planning" not in inputs or "joint_execution_plan" in outcome:
        raise JointArchiveError("V4 child trace requires whole-plan input and native child outcome")
    raw_inputs = encode_shared_joint(inputs, original_binding, "TRACE_INPUTS")
    raw_outcome = encode_shared_joint(outcome, original_binding, "TRACE_OUTCOME")
    expected = trace_reference(raw_inputs, raw_outcome, phase)
    return (
        {
            "protocol": TRACE_PROTOCOL,
            "phase": phase,
            "pair_hash": expected["pair_hash"],
            "record": raw_inputs,
        },
        {
            "protocol": TRACE_PROTOCOL,
            "phase": phase,
            "pair_hash": expected["pair_hash"],
            "record": raw_outcome,
        },
        expected,
    )


def decode_shared_trace_parts(
    inputs: dict[str, Any],
    outcome: dict[str, Any],
    phase: str,
    original_binding: dict[str, Any],
    *,
    expected_trace_reference: dict[str, Any],
) -> tuple[SharedJointOriginal, SharedJointOriginal]:
    if type(phase) is not str or phase not in PHASES:
        raise JointArchiveError("V4 trace phase differs")
    for part in (inputs, outcome):
        if (
            type(part) is not dict
            or set(part) != {"protocol", "phase", "pair_hash", "record"}
            or part["protocol"] != TRACE_PROTOCOL
            or part["phase"] != phase
            or not digest(part["pair_hash"])
        ):
            raise JointArchiveError("V4 trace part closed shape or phase differs")
    expected = trace_reference(inputs["record"], outcome["record"], phase)
    if (
        expected != expected_trace_reference
        or inputs["pair_hash"] != expected["pair_hash"]
        or outcome["pair_hash"] != expected["pair_hash"]
    ):
        raise JointArchiveError("V4 verified whole trace pair differs")
    left = decode_shared_joint(
        inputs["record"],
        original_binding,
        "TRACE_INPUTS",
        expected_reference=expected_trace_reference["inputs"],
    )
    right = decode_shared_joint(
        outcome["record"],
        original_binding,
        "TRACE_OUTCOME",
        expected_reference=expected_trace_reference["outcome"],
    )
    if phase == "EVALUATION":
        if "joint_execution_input" not in left._root or "joint_execution_plan" not in right._root:
            raise JointArchiveError("V4 whole evaluation trace originals missing")
    elif "planning" not in left._root or "joint_execution_plan" in right._root:
        raise JointArchiveError("V4 whole child trace originals differ")
    # Return complete lazy originals. Native mathematical source/consent checks
    # are intentionally neither skipped nor claimed performed by this codec.
    return left, right
