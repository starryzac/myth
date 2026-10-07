"""Versioned lossless joint originals; hashes prove content, never financial authority."""

import base64
import binascii
import hashlib
import json
import math
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID, uuid5

from app.domain.full_joint_goal_archive import (
    MAX_COMPRESSED_BYTES,
    MAX_DECODED_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    JointArchiveError,
    _constant,
    _gzip,
    _inflate,
    _json_budget,
    _limits,
    _pairs,
)
from app.domain.policy_configuration import configuration_hash

if TYPE_CHECKING:
    from app.domain.decision_trace_types import DecisionTrace

ALGORITHM_V3 = "registered-joint-goal-execution-archive-v3"
RAW_ALGORITHM = "registered-joint-goal-execution-v2"
RECORD_PROTOCOL = "joint-goal-record-archive-v3"
TRACE_PROTOCOL = "joint-goal-trace-archive-v3"
CODEC = "canonical-json-gzip-mtime0-base64-v2"
MAX_WIRE_BYTES = 10 * 1024 * 1024
MARKER = "full_joint_goal_execution"
RecordKind = Literal["INPUT", "PLAN", "TRACE_INPUTS", "TRACE_OUTCOME"]
_KINDS = {"INPUT", "PLAN", "TRACE_INPUTS", "TRACE_OUTCOME"}
_PHASES = {"EVALUATION", "PREPARE", "CONFIRM", "RESERVE", "BANK_ACCEPT"}
_FLAGS = {
    "bank_authority": False,
    "grants_authority": False,
    "financial_write": False,
    "financial_validation_performed": False,
}
_FIELDS = {
    "protocol",
    "codec",
    "record_kind",
    "binding",
    "plaintext_sha256",
    "decoded_bytes",
    "compressed_bytes",
    "decoded_nodes",
    "decoded_depth",
    "payload_base64",
    "record_hash",
    *_FLAGS,
}
_BINDING = {"user_id", "epoch_id", "plan_id", "request_hash", "plan_hash", "plan_protocol"}


def _digest(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _canonical(
    value: dict[str, Any],
    byte_limit: int,
    node_limit: int,
    depth_limit: int,
) -> tuple[bytes, int, int]:
    """Bound allocation first; raw rejected claims retain their exact JSON scalar types."""
    if type(value) is not dict:
        raise JointArchiveError("Joint original must be a JSON object")
    nodes, deepest, byte_count = 0, 0, 0

    def add(size: int) -> None:
        nonlocal byte_count
        byte_count += size
        if byte_count > byte_limit:
            raise JointArchiveError("Joint original exceeds decoded byte budget")

    def string(value: str) -> None:
        add(2)
        for start in range(0, len(value), 4096):
            try:
                add(
                    len(json.dumps(value[start : start + 4096], ensure_ascii=False).encode("utf8"))
                    - 2
                )
            except UnicodeError as error:
                raise JointArchiveError("Joint original contains incomplete UTF-8") from error

    def visit(child: Any, depth: int) -> None:
        nonlocal nodes, deepest
        nodes, deepest = nodes + 1, max(deepest, depth)
        if nodes > node_limit or depth > depth_limit:
            raise JointArchiveError("Joint original exceeds node/depth budget")
        if child is None:
            add(4)
        elif type(child) is bool:
            add(4 if child else 5)
        elif type(child) is str:
            string(child)
        elif type(child) is int:
            if not -(2**63) <= child <= 2**63 - 1:
                raise JointArchiveError("Joint original integer is outside signed64")
            add(len(str(child)))
        elif type(child) is float:
            if not math.isfinite(child):
                raise JointArchiveError("Joint original float must be finite")
            add(len(json.dumps(child, allow_nan=False)))
        elif type(child) is list:
            add(2 + max(0, len(child) - 1))
            for item in child:
                visit(item, depth + 1)
        elif type(child) is dict:
            add(2 + max(0, len(child) - 1))
            for key, item in child.items():
                if type(key) is not str:
                    raise JointArchiveError("Joint original keys must be strings")
                string(key)
                add(1)
                visit(item, depth + 1)
        else:
            raise JointArchiveError("Joint originals accept JSON values only")

    visit(value, 0)
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf8")
    if len(raw) != byte_count:
        raise JointArchiveError("Joint original canonical byte count differs")
    return raw, nodes, deepest


def _binding(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _BINDING:
        raise JointArchiveError("Joint archive binding shape differs")
    for key in ("user_id", "epoch_id", "plan_id"):
        try:
            if (
                type(value[key]) is not str
                or len(value[key]) != 36
                or str(UUID(value[key])) != value[key]
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise JointArchiveError("Joint archive identity must be canonical UUID") from error
    if (
        not _digest(value["request_hash"])
        or not _digest(value["plan_hash"])
        or type(value["plan_protocol"]) is not str
        or value["plan_protocol"] not in {RAW_ALGORITHM, ALGORITHM_V3}
    ):
        raise JointArchiveError("Joint archive original hash or protocol differs")
    return value.copy()


def validate_joint_original_capacity(value: dict[str, Any]) -> None:
    """Decoded transport bound only; no source, solver, scope or consent verdict."""
    _canonical(value, MAX_DECODED_BYTES, MAX_NODES, MAX_DEPTH)


def binding_for_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """No derivation/permission claim: require exact original plan identity and hashes."""
    validate_joint_original_capacity(plan)
    try:
        result = _binding(
            {
                "user_id": plan["user_id"],
                "epoch_id": plan["epoch_id"],
                "plan_id": plan["plan_id"],
                "request_hash": configuration_hash(plan["inputs"]["request"]),
                "plan_hash": plan["plan_hash"],
                "plan_protocol": plan["protocol"],
            }
        )
        if (
            configuration_hash({k: v for k, v in plan.items() if k != "plan_hash"})
            != result["plan_hash"]
        ):
            raise JointArchiveError("Joint plan original hash differs")
        _check_input(plan["inputs"], result)
        return result
    except (KeyError, TypeError, ValueError) as error:
        raise JointArchiveError("Joint plan original binding cannot be verified") from error


def _check_input(value: Any, binding: dict[str, Any]) -> None:
    try:
        request = value["request"]
        base = value["joint"]["original_actual_input"]["base"]
        # Use the original plan_identity formula, never a new archive identity.
        from app.domain.full_joint_goal_execution import NAMESPACE

        identity = uuid5(
            NAMESPACE, configuration_hash({"user": binding["user_id"], "request": request})
        )
        if (
            value["protocol"] != "joint-goal-execution-input-v2"
            or request["expected_epoch_id"] != binding["epoch_id"]
            or base["user_id"] != binding["user_id"]
            or base["epoch_id"] != binding["epoch_id"]
            or str(identity) != binding["plan_id"]
            or configuration_hash(request) != binding["request_hash"]
        ):
            raise JointArchiveError("Joint input owner, epoch or original request differs")
    except (KeyError, TypeError, ValueError) as error:
        raise JointArchiveError("Joint input original identity cannot be verified") from error


def _check_original(value: dict[str, Any], binding: dict[str, Any], kind: RecordKind) -> None:
    if kind == "INPUT":
        _check_input(value, binding)
    elif kind == "PLAN":
        if binding_for_plan(value) != binding:
            raise JointArchiveError("Joint plan archive original binding differs")
    elif kind == "TRACE_INPUTS":
        try:
            if "joint_execution_input" in value:
                _check_input(value["joint_execution_input"], binding)
            elif (raw := value.get("planning", {}).get(MARKER)) is not None:
                if binding_for_plan(raw["plan"]) != binding:
                    raise JointArchiveError("Joint child trace plan binding differs")
            else:
                raise JointArchiveError("Joint trace inputs lack their complete original binding")
        except (KeyError, TypeError, AttributeError) as error:
            raise JointArchiveError("Joint trace complete original binding is malformed") from error
    elif "joint_execution_plan" in value:
        if binding_for_plan(value["joint_execution_plan"]) != binding:
            raise JointArchiveError("Joint trace outcome plan binding differs")
    # Child outcomes contain full revalidation rather than an independently substituted plan.


def _wire(record: dict[str, Any]) -> None:
    # This exact restricted outer shape contains ASCII/integers only, so JSONB
    # separators are conservatively included instead of testing compact bytes alone.
    if len(json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf8")) > MAX_WIRE_BYTES:
        raise JointArchiveError("Joint archive exceeds unchanged JSONB wire budget")


def encode_joint_record(
    value: dict[str, Any],
    binding: dict[str, Any],
    kind: RecordKind,
    *,
    max_decoded_bytes: int = MAX_DECODED_BYTES,
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    _limits(max_decoded_bytes, max_nodes, max_depth)
    bound = _binding(binding)
    if type(kind) is not str or kind not in _KINDS:
        raise JointArchiveError("Joint archive record kind is unsupported")
    raw, nodes, depth = _canonical(value, max_decoded_bytes, max_nodes, max_depth)
    _check_original(value, bound, kind)
    compressed = _gzip(raw)
    record = {
        "protocol": RECORD_PROTOCOL,
        "codec": CODEC,
        "record_kind": kind,
        "binding": bound,
        "plaintext_sha256": hashlib.sha256(raw).hexdigest(),
        "decoded_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "decoded_nodes": nodes,
        "decoded_depth": depth,
        "payload_base64": base64.b64encode(compressed).decode("ascii"),
        **_FLAGS,
    }
    record["record_hash"] = configuration_hash(record)
    _wire(record)
    return record


def record_reference(record: dict[str, Any]) -> dict[str, Any]:
    _check_record(record)
    return {
        "protocol": "joint-goal-original-reference-v3",
        "record_kind": record["record_kind"],
        "binding": record["binding"].copy(),
        "plaintext_sha256": record["plaintext_sha256"],
        "record_hash": record["record_hash"],
    }


def _check_record(record: dict[str, Any]) -> dict[str, Any]:
    if (
        type(record) is not dict
        or set(record) != _FIELDS
        or record["protocol"] != RECORD_PROTOCOL
        or record["codec"] != CODEC
        or type(record["record_kind"]) is not str
        or record["record_kind"] not in _KINDS
        or any(record[k] is not False for k in _FLAGS)
    ):
        raise JointArchiveError("Joint archive closed protocol or authority flags differ")
    text = record["payload_base64"]
    if type(text) is not str or len(text) > MAX_WIRE_BYTES or not text.isascii():
        raise JointArchiveError("Joint archive encoded payload is invalid")
    binding = _binding(record["binding"])
    for key, maximum, minimum in (
        ("decoded_bytes", MAX_DECODED_BYTES, 1),
        ("compressed_bytes", MAX_COMPRESSED_BYTES, 1),
        ("decoded_nodes", MAX_NODES, 1),
        ("decoded_depth", MAX_DEPTH, 0),
    ):
        if type(record[key]) is not int or not minimum <= record[key] <= maximum:
            raise JointArchiveError("Joint archive declared capacity is invalid")
    if len(text) != 4 * ((record["compressed_bytes"] + 2) // 3):
        raise JointArchiveError("Joint archive declared base64 length differs")
    if (
        not _digest(record["record_hash"])
        or not _digest(record["plaintext_sha256"])
        or configuration_hash({k: v for k, v in record.items() if k != "record_hash"})
        != record["record_hash"]
    ):
        raise JointArchiveError("Joint archive record or plaintext hash differs")
    _wire(record)
    return binding


def decode_joint_record(
    record: dict[str, Any],
    binding: dict[str, Any],
    kind: RecordKind,
    *,
    expected_reference: dict[str, Any] | None = None,
    max_decoded_bytes: int = MAX_DECODED_BYTES,
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    from app.domain.immutable_joint_archive_scope import decode_complete_original

    record_binding = record.get("binding")
    protocol = record_binding.get("plan_protocol") if type(record_binding) is dict else None
    return decode_complete_original(
        str(protocol),
        {
            "record": record,
            "binding": binding,
            "kind": kind,
            "expected_reference": expected_reference,
            "max_decoded_bytes": max_decoded_bytes,
            "max_nodes": max_nodes,
            "max_depth": max_depth,
        },
        record.get("decoded_bytes", 0),
        lambda: _decode_joint_record_uncached(
            record,
            binding,
            kind,
            expected_reference=expected_reference,
            max_decoded_bytes=max_decoded_bytes,
            max_nodes=max_nodes,
            max_depth=max_depth,
        ),
    )


def _decode_joint_record_uncached(
    record: dict[str, Any],
    binding: dict[str, Any],
    kind: RecordKind,
    *,
    expected_reference: dict[str, Any] | None = None,
    max_decoded_bytes: int = MAX_DECODED_BYTES,
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    _limits(max_decoded_bytes, max_nodes, max_depth)
    bound = _check_record(record)
    if bound != _binding(binding) or record["record_kind"] != kind:
        raise JointArchiveError("Joint archive cannot substitute another original binding")
    if expected_reference is not None and record_reference(record) != expected_reference:
        raise JointArchiveError("Joint archive reference differs from supplied original")
    if (
        record["decoded_bytes"] > max_decoded_bytes
        or record["decoded_nodes"] > max_nodes
        or record["decoded_depth"] > max_depth
    ):
        raise JointArchiveError("Joint archive exceeds caller decoded capacity")
    try:
        compressed = base64.b64decode(record["payload_base64"], validate=True)
    except (ValueError, binascii.Error) as error:
        raise JointArchiveError("Joint archive base64 is invalid") from error
    if (
        len(compressed) != record["compressed_bytes"]
        or base64.b64encode(compressed).decode("ascii") != record["payload_base64"]
    ):
        raise JointArchiveError("Joint archive encoded length or canonical base64 differs")
    raw = _inflate(compressed, record["decoded_bytes"])
    if (
        len(raw) != record["decoded_bytes"]
        or hashlib.sha256(raw).hexdigest() != record["plaintext_sha256"]
    ):
        raise JointArchiveError("Joint archive plaintext length or hash differs")
    _json_budget(raw, max_nodes, max_depth)
    try:
        value = json.loads(raw.decode("utf8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise JointArchiveError("Joint archive plaintext is not strict JSON") from error
    canonical, nodes, depth = _canonical(value, max_decoded_bytes, max_nodes, max_depth)
    if canonical != raw or nodes != record["decoded_nodes"] or depth != record["decoded_depth"]:
        raise JointArchiveError("Joint archive original normalization or counts differ")
    _check_original(value, bound, kind)
    return cast(dict[str, Any], value)


def _decode_trusted_trace_record(
    record: dict[str, Any],
    binding: dict[str, Any],
    kind: Literal["TRACE_INPUTS", "TRACE_OUTCOME"],
) -> dict[str, Any]:
    """Cache only the complete native trace scalar/owner proof, not raw decoding.

    Generic records deliberately retain untrusted declarations. A distinct
    complete key prevents that raw decoder's successful proof from upgrading
    a trace. First decode preserves every codec and trusted-original check.
    Current facts, mathematical replay, audit and permission remain outside.
    """
    from app.domain.immutable_joint_archive_scope import decode_complete_original

    def complete() -> dict[str, Any]:
        value = _decode_joint_record_uncached(record, binding, kind)
        _trusted_trace_original(value, binding["user_id"])
        return value

    return decode_complete_original(
        str(binding.get("plan_protocol")),
        {
            "record": record,
            "binding": binding,
            "kind": kind,
            "expected_reference": None,
            "max_decoded_bytes": MAX_DECODED_BYTES,
            "max_nodes": MAX_NODES,
            "max_depth": MAX_DEPTH,
            "validation": "native-trusted-trace-original-v1",
            "trusted_trace_user_id": binding.get("user_id"),
        },
        record.get("decoded_bytes", 0),
        complete,
    )


def encode_joint_trace_parts(
    inputs: dict[str, Any],
    outcome: dict[str, Any],
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(phase) is not str or phase not in _PHASES:
        raise JointArchiveError("Joint archive trace phase is unsupported")
    try:
        plan = (
            outcome["joint_execution_plan"]
            if phase == "EVALUATION"
            else inputs["planning"][MARKER]["plan"]
        )
        binding = binding_for_plan(plan)
        if binding["plan_protocol"] != ALGORITHM_V3:
            raise JointArchiveError("Archive trace requires the explicit v3 plan protocol")
        _trusted_trace_original(inputs, binding["user_id"])
        _trusted_trace_original(outcome, binding["user_id"])
        return (
            {
                "protocol": TRACE_PROTOCOL,
                "phase": phase,
                "record": encode_joint_record(inputs, binding, "TRACE_INPUTS"),
            },
            {
                "protocol": TRACE_PROTOCOL,
                "phase": phase,
                "record": encode_joint_record(outcome, binding, "TRACE_OUTCOME"),
            },
        )
    except (KeyError, TypeError, ValueError) as error:
        raise JointArchiveError("Joint trace complete original binding is missing") from error


def decode_joint_trace_parts(
    inputs: dict[str, Any],
    outcome: dict[str, Any],
    phase: str,
    user_id: UUID,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(phase) is not str or phase not in _PHASES:
        raise JointArchiveError("Joint archive trace phase is unsupported")
    for part in (inputs, outcome):
        if (
            type(part) is not dict
            or set(part) != {"protocol", "phase", "record"}
            or part["protocol"] != TRACE_PROTOCOL
            or part["phase"] != phase
        ):
            raise JointArchiveError("Joint trace archived phase or closed shape differs")
    bound = _check_record(inputs["record"])
    if bound["user_id"] != str(user_id) or bound["plan_protocol"] != ALGORITHM_V3:
        raise JointArchiveError("Joint trace archived owner or algorithm differs")
    decoded_inputs = _decode_trusted_trace_record(inputs["record"], bound, "TRACE_INPUTS")
    decoded_outcome = _decode_trusted_trace_record(outcome["record"], bound, "TRACE_OUTCOME")
    return decoded_inputs, decoded_outcome


def _trusted_trace_original(value: dict[str, Any], user_id: str) -> None:
    """Retain existing trusted trace scalar/owner rules after transport decoding."""
    validate_joint_original_capacity(value)

    def visit(child: Any) -> None:
        if type(child) is dict:
            if "user_id" in child and child["user_id"] != user_id:
                raise JointArchiveError("Joint trace nested original belongs to another user")
            for key, item in child.items():
                if key == "amount_options_cents" and item is not None:
                    if (
                        type(item) is not list
                        or not 2 <= len(item) <= 8
                        or any(
                            type(amount) is not int or not 0 < amount <= 2**63 - 1
                            for amount in item
                        )
                    ):
                        raise JointArchiveError(
                            "Joint trace amount options must be 2-8 integer cents"
                        )
                elif key.endswith("_cents") and item is not None:
                    if type(item) is not int or not -(2**63) <= item <= 2**63 - 1:
                        raise JointArchiveError(
                            "Joint trace monetary originals must be integer cents"
                        )
                visit(item)
        elif type(child) is list:
            for item in child:
                visit(item)

    visit(value)


def expanded_joint_trace(trace: "DecisionTrace") -> "DecisionTrace":
    """Ephemeral full view after verify_trace(wire); never persist/re-hash this view."""
    from app.domain.full_joint_goal_source_adapter import ALGORITHM_V4, expanded_source_joint_trace

    if trace.algorithm_versions.get(MARKER) == ALGORITHM_V4:
        return expanded_source_joint_trace(trace)
    if trace.algorithm_versions.get(MARKER) != ALGORITHM_V3:
        return trace
    inputs, outcome = decode_joint_trace_parts(
        trace.inputs, trace.outcome, trace.phase, trace.user_id
    )
    return trace.model_copy(update={"inputs": inputs, "outcome": outcome})
