"""Canonical simulated audit facts; pure functions with no database or clock reads."""

import hashlib
import json
import math
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Literal, cast, overload
from uuid import UUID, uuid5

if TYPE_CHECKING:
    from app.domain.audit_chain_types import (
        AuditCheckpoint,
        AuditEnvelope,
        AuditEnvelopeV2,
        AuditEpochSeal,
        AuditEvent,
        AuditHead,
        AuditIntent,
        AuditIntentAny,
        AuditIntentV2,
        AuditSubject,
        AuditVerification,
        ReferenceBundle,
    )

MAX_EVENT_BYTES = 1024 * 1024
MAX_SUBJECT_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MANIFEST_BYTES = 16 * 1024 * 1024
EXTERNAL_EVENT_TYPES = frozenset({"EXTERNAL_BANK_FACT_SETTLED", "EXTERNAL_BANK_FACT_PROJECTED"})
EXTERNAL_ANCHOR_ALGORITHM = "bank-external-canonical-sha256-v1"
EXTERNAL_ANCHOR_KINDS = frozenset(
    {
        "EXTERNAL_BANK_REQUEST",
        "EXTERNAL_BANK_RESULT",
        "EXTERNAL_BANK_POSTING_SET",
        "EXTERNAL_BANK_PROJECTION",
    }
)


class AuditContractError(ValueError):
    """Invalid frozen audit content, independent of any application transaction."""


def canonical_bytes(value: dict[str, Any], *, raw: bool = False) -> bytes:
    """Versioned UTF-8 bytes; raw originals preserve finite JSON numeric types."""
    return _canonical_bytes(
        value, raw=raw, byte_limit=MAX_SUBJECT_BYTES if raw else MAX_EVENT_BYTES
    )


def _canonical_bytes(value: dict[str, Any], *, raw: bool, byte_limit: int) -> bytes:
    if type(value) is not dict:
        raise AuditContractError("Audit canonical value must be an object")
    nodes = 0

    def convert(child: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if depth > (64 if raw else 32) or nodes > (1000000 if raw else 250000):
            raise AuditContractError("Audit JSON exceeds depth or node limit")
        if child is None or type(child) in (str, bool):
            return child
        if type(child) is int:
            if not raw and not -(2**63) <= child <= 2**63 - 1:
                raise AuditContractError("Audit integer is outside signed64")
            return child
        if type(child) is float:
            if not raw or not math.isfinite(child):
                raise AuditContractError("Audit numbers must be allowed finite JSON numbers")
            return child
        if isinstance(child, UUID):
            return str(child)
        if isinstance(child, datetime):
            if child.tzinfo is None or child.utcoffset() is None:
                raise AuditContractError("Audit datetime must be timezone-aware")
            return child.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if isinstance(child, date):
            return child.isoformat()
        if type(child) is dict:
            result = {}
            for key, item in child.items():
                if type(key) is not str:
                    raise AuditContractError("Audit JSON keys must be strings")
                if not raw and key.endswith("_cents") and item is not None:
                    if type(item) is not int or not -(2**63) <= item <= 2**63 - 1:
                        raise AuditContractError("Audit money must be signed integer cents")
                result[key] = convert(item, depth + 1)
            return result
        if type(child) is list:
            return [convert(item, depth + 1) for item in child]
        raise AuditContractError("Audit value is not a permitted JSON scalar or container")

    try:
        encoded = json.dumps(
            convert(value, 0),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AuditContractError("Audit value cannot be canonically encoded") from error
    if len(encoded) > byte_limit:
        raise AuditContractError("Audit JSON exceeds byte limit")
    return encoded


def archive_manifest_bytes(value: dict[str, Any]) -> bytes:
    """Complete SQL archive index, with its own bounded size and identical v1 bytes.

    This index spans many original subjects; it is not an event or a subject.
    All event bounds and the existing canonical namespace/algorithm are retained.
    """
    from app.domain.audit_chain_types import SUBJECT_KINDS

    if type(value) is not dict or set(value) != {"entries", "counts"}:
        raise AuditContractError("Audit archive manifest has an unsupported shape")
    entries, counts = value["entries"], value["counts"]
    if type(entries) is not list or type(counts) is not dict:
        raise AuditContractError("Audit archive manifest needs complete entries and counts")
    if len(entries) > 50000:
        raise AuditContractError("Audit archive manifest exceeds entry limit")
    actual: dict[str, int] = {}
    previous: tuple[str, str, str] | None = None
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"kind", "id", "snapshot_hash"}:
            raise AuditContractError("Audit archive entry has an unsupported shape")
        kind, identity, digest = entry["kind"], entry["id"], entry["snapshot_hash"]
        if type(kind) is not str or kind not in SUBJECT_KINDS:
            raise AuditContractError("Audit archive subject kind is unsupported")
        if type(identity) is not str:
            raise AuditContractError("Audit archive identity must be canonical UUID text")
        try:
            if str(UUID(identity)) != identity:
                raise ValueError("Noncanonical identity")
        except (ValueError, AttributeError) as error:
            raise AuditContractError(
                "Audit archive identity must be canonical UUID text"
            ) from error
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise AuditContractError("Audit archive snapshot hash must be original SHA256 text")
        key = (kind, identity, digest)
        if previous is not None and key <= previous:
            raise AuditContractError("Audit archive entries must be uniquely ordered")
        previous = key
        actual[kind] = actual.get(kind, 0) + 1
    if (
        any(
            type(kind) is not str
            or kind not in SUBJECT_KINDS
            or type(count) is not int
            or not 1 <= count <= 50000
            for kind, count in counts.items()
        )
        or counts != actual
    ):
        raise AuditContractError("Audit archive counts differ from complete original entries")
    return _canonical_bytes(value, raw=False, byte_limit=MAX_ARCHIVE_MANIFEST_BYTES)


def canonical_text(value: dict[str, Any], *, raw: bool = False) -> str:
    return canonical_bytes(value, raw=raw).decode("utf-8")


def _digest(namespace: str, value: dict[str, Any], *, raw: bool = False) -> str:
    return hashlib.sha256(
        ("bounded-funds/" + namespace + "\0").encode() + canonical_bytes(value, raw=raw)
    ).hexdigest()


def _read(text: str) -> dict[str, Any]:
    if type(text) is not str or len(text.encode("utf-8")) > MAX_SUBJECT_BYTES:
        raise AuditContractError("Original audit text exceeds its byte limit")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise AuditContractError("Audit JSON contains a duplicate key")
            result[key] = value
        return result

    try:

        def nonfinite(value: str) -> Any:
            raise AuditContractError("Audit original JSON contains a nonfinite number")

        value = json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, TypeError, RecursionError) as error:
        raise AuditContractError("Audit original text is not strict JSON") from error
    if type(value) is not dict:
        raise AuditContractError("Audit original must be an object")
    return value


def _protocol(value: dict[str, Any], schema: str) -> None:
    if (
        value.get("schema_version") != schema
        or value.get("canonical_version") != "audit-canonical-json-v1"
    ):
        raise AuditUnsupportedVersion("Unsupported original audit schema or canonical version")


def _external_fact_original(data: dict[str, Any]) -> None:
    from app.domain.external_bank_fact import validate_external_fact_original

    validate_external_fact_original(data)


def _external_fact_immutable(
    original: "AuditSubject", current: dict[tuple[str, UUID], "AuditSubject"]
) -> None:
    from app.domain.external_bank_fact import verify_external_fact_binding

    latest = current.get((original.kind, original.id))
    if latest is not None:
        verify_external_fact_binding(original.data, latest.data)


def _validated_subject_bytes(**fields: Any) -> tuple["AuditSubject", bytes]:
    from app.domain.audit_chain_types import SUBJECT_KINDS, AuditSubject

    subject = AuditSubject.model_validate(fields)
    if subject.data.get("id") != str(subject.id):
        raise AuditContractError("Audit subject identity differs from its original")
    owner = subject.data.get("user_id")
    if subject.scope == "GLOBAL_CATALOG":
        if subject.kind != "ASSET_PRODUCT" or owner is not None:
            raise AuditContractError("Only original global products may have global scope")
    elif subject.kind == "USER":
        if subject.id != subject.user_id:
            raise AuditContractError("Audit user subject belongs to another tenant")
    elif owner != str(subject.user_id):
        raise AuditContractError("Audit subject row belongs to another tenant")
    for key, value in subject.data.items():
        if (
            key.endswith("_cents")
            and value is not None
            and (type(value) is not int or not -(2**63) <= value <= 2**63 - 1)
        ):
            raise AuditContractError("Original row money must be strict signed integer cents")
    if subject.kind not in SUBJECT_KINDS or subject.snapshot_version not in (
        (1, 2) if subject.kind == "BANK_POSTING" else (1,)
    ):
        raise AuditUnsupportedVersion("Unsupported audit subject kind or version")
    if subject.kind == "BANK_POSTING":
        from app.domain.bank_posting_codec import validate_posting_original

        validate_posting_original(subject.data, subject.snapshot_version)
    elif subject.kind == "BANK_EXTERNAL_FACT":
        _external_fact_original(subject.data)
    encoded = canonical_bytes(subject.model_dump(mode="python"), raw=True)
    return subject, encoded


def build_subject(**fields: Any) -> "AuditSubject":
    subject, _ = _validated_subject_bytes(**fields)
    return subject


def _subject_hash_bytes(encoded: bytes) -> str:
    """Only bytes produced by the immediately preceding full subject validation."""
    return hashlib.sha256(b"bounded-funds/audit-subject-v1\0" + encoded).hexdigest()


def encode_subject_original(**fields: Any) -> tuple["AuditSubject", str, str]:
    """Validate original fields once and return subject, exact text, and digest."""
    subject, encoded = _validated_subject_bytes(**fields)
    return subject, encoded.decode("utf-8"), _subject_hash_bytes(encoded)


def subject_canonical_text(subject: "AuditSubject") -> str:
    _, encoded = _validated_subject_bytes(**subject.model_dump(mode="python"))
    return encoded.decode("utf-8")


def subject_hash(subject: "AuditSubject") -> str:
    _declared(subject)
    _, encoded = _validated_subject_bytes(**subject.model_dump(mode="python"))
    return _subject_hash_bytes(encoded)


def parse_subject(text: str) -> "AuditSubject":
    from app.domain.audit_chain_types import AuditSubject

    raw = _read(text)
    _protocol(raw, "audit-subject-v1")
    subject = AuditSubject.model_validate_json(json.dumps(raw, allow_nan=False))
    subject, encoded = _validated_subject_bytes(**subject.model_dump(mode="python"))
    if encoded.decode("utf-8") != text:
        raise AuditContractError("Audit subject text is not its original canonical encoding")
    return subject


class AuditUnsupportedVersion(AuditContractError):
    """An original protocol is unknown; it must never be silently treated as valid."""


class AuditBudgetExceeded(AuditContractError):
    """A bounded original set was not fully checked, so no PASS may be inferred."""


def _intent(intent: "AuditIntentAny") -> "AuditIntentAny":
    from app.domain.audit_chain_types import AuditIntent

    _declared(intent)
    fields = intent.model_dump(mode="python", include=set(AuditIntent.model_fields))
    restored = _event_model(fields, envelope=False).model_validate(fields)
    refs = sorted(
        restored.payload.references, key=lambda r: (r.kind, str(r.id), r.role, r.snapshot_hash)
    )
    anchors = sorted(
        restored.payload.anchors,
        key=lambda a: (a.kind, str(a.reference_id), a.snapshot_hash, a.hash_algorithm),
    )
    updates: dict[str, Any] = {"references": refs, "anchors": anchors}
    if restored.payload_version == 1:
        updates["missing_evidence_ids"] = sorted(
            cast("AuditIntent", restored).payload.missing_evidence_ids
        )
    payload = restored.payload.model_copy(update=updates)
    return restored.model_copy(update={"payload": payload})


def intent_digest(intent: "AuditIntentAny") -> str:
    return _digest("audit-intent-v1", _intent(intent).model_dump(mode="python"))


def _declared(value: Any) -> None:
    from pydantic import BaseModel

    if isinstance(value, BaseModel):
        if set(value.__dict__) - set(type(value).model_fields):
            raise AuditContractError("Audit model has undeclared copied fields")
        for child in value.__dict__.values():
            _declared(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, BaseModel):
                _declared(child)


@overload
def _event_model(
    value: dict[str, Any], *, envelope: Literal[False]
) -> "type[AuditIntent] | type[AuditIntentV2]": ...


@overload
def _event_model(
    value: dict[str, Any], *, envelope: Literal[True]
) -> "type[AuditEnvelope] | type[AuditEnvelopeV2]": ...


def _event_model(
    value: dict[str, Any], *, envelope: bool
) -> "type[AuditIntent] | type[AuditIntentV2] | type[AuditEnvelope] | type[AuditEnvelopeV2]":
    from app.domain.audit_chain_types import (
        EVENT_TYPES,
        AuditEnvelope,
        AuditEnvelopeV2,
        AuditIntent,
        AuditIntentV2,
    )

    version, kind = value.get("payload_version"), value.get("event_type")
    if type(version) is not int or kind not in EVENT_TYPES:
        raise AuditUnsupportedVersion("Unsupported audit event type or payload version")
    expected = 2 if kind in EXTERNAL_EVENT_TYPES else 1
    if version != expected:
        raise AuditUnsupportedVersion("Unsupported registered audit event payload version")
    if envelope:
        return AuditEnvelopeV2 if version == 2 else AuditEnvelope
    return AuditIntentV2 if version == 2 else AuditIntent


def _known_event_integrity(value: dict[str, Any]) -> None:
    """Check known outer content before rejecting an unknown inner payload version."""
    if value.get("simulation") is not True:
        raise AuditContractError("Audit simulation must be the boolean true")
    sequence, digest = value.get("sequence_number"), value.get("event_hash")
    if (
        type(sequence) is not int
        or not 1 <= sequence <= 2**31 - 1
        or type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or (sequence == 1) != (value.get("previous_hash") is None)
    ):
        raise AuditContractError("Audit original row header is invalid")
    for field in ("id", "user_id", "epoch_id"):
        UUID(str(value[field]))
    if _digest("audit-event-v1", {k: v for k, v in value.items() if k != "event_hash"}) != digest:
        raise AuditContractError("Audit event hash differs from its frozen content")
    payload = value.get("payload")
    if type(payload) is dict and type(payload.get("references")) is list:
        for ref in payload["references"]:
            if (
                type(ref) is dict
                and ref.get("scope") == "TENANT"
                and (str(ref.get("user_id")) != str(value["user_id"]))
            ):
                raise AuditContractError("Audit reference belongs to another tenant")


def _external_event_content(event: "AuditEvent") -> None:
    payload = event.payload
    if (
        payload.correlation_kind != "EXTERNAL_BANK_FACT"
        or event.aggregate_type != "BANK_EXTERNAL_FACT"
        or event.aggregate_id != event.correlation_id
        or any((event.decision_run_id, event.action_plan_id, event.action_receipt_id))
    ):
        raise AuditContractError("External audit fact must bind its real independent bank identity")
    if (event.event_type == "EXTERNAL_BANK_FACT_SETTLED") != (event.causation_id is None):
        raise AuditContractError("External projection requires its earlier settled cause")
    if any(
        ref.kind in {"DECISION_RUN", "ACTION_PLAN", "ACTION_RECEIPT", "BANK_OPERATION"}
        for ref in payload.references
    ):
        raise AuditContractError("External bank fact cannot borrow an Agent command relationship")
    required = {"EXTERNAL_BANK_RESULT", "EXTERNAL_BANK_POSTING_SET"}
    required.add(
        "EXTERNAL_BANK_REQUEST"
        if event.event_type == "EXTERNAL_BANK_FACT_SETTLED"
        else "EXTERNAL_BANK_PROJECTION"
    )
    kinds = {anchor.kind for anchor in payload.anchors}
    identities = [(anchor.kind, anchor.reference_id) for anchor in payload.anchors]
    if not required.issubset(kinds) or len(identities) != len(set(identities)):
        raise AuditContractError("External bank fact lacks its unique mandatory original anchors")
    for anchor in payload.anchors:
        if (
            anchor.kind not in EXTERNAL_ANCHOR_KINDS
            or anchor.hash_algorithm != EXTERNAL_ANCHOR_ALGORITHM
        ):
            raise AuditUnsupportedVersion("Unsupported external bank fact anchor protocol")
        if anchor.reference_id != event.correlation_id:
            raise AuditContractError("External bank anchor differs from its actual fact identity")
    for ref in payload.references:
        if ref.kind == "BANK_EXTERNAL_FACT" and ref.id != event.correlation_id:
            raise AuditContractError("External audit event references a different bank fact")
    canonical_bytes(event.model_dump(mode="python"))


def _event_content(event: "AuditEvent") -> None:
    from app.domain.audit_chain_types import PUBLIC_SUBJECT_KINDS

    _event_model(event.model_dump(mode="python"), envelope=True)
    if event.occurred_at > event.observed_at:
        raise AuditContractError("Audit fact has not occurred at its observation clock")
    if (event.sequence_number == 1) != (event.previous_hash is None):
        raise AuditContractError("Audit genesis/previous hash is inconsistent")
    payload = event.payload
    keys = [(r.kind, r.id, r.role) for r in payload.references]
    if len(keys) != len(set(keys)):
        raise AuditContractError("Audit original references contain duplicate identities")
    for ref in payload.references:
        if ref.kind not in PUBLIC_SUBJECT_KINDS:
            raise AuditUnsupportedVersion("Unsupported ordinary audit reference kind")
        if ref.scope == "TENANT" and ref.user_id != event.user_id:
            raise AuditContractError("Audit reference belongs to another tenant")
        if ref.scope == "GLOBAL_CATALOG" and (
            ref.kind != "ASSET_PRODUCT" or ref.user_id is not None
        ):
            raise AuditContractError("Only original catalog products may have global scope")
    if event.payload_version == 2:
        _external_event_content(event)
        return
    event = cast("AuditEnvelope", event)
    payload = event.payload
    if len(payload.missing_evidence_ids) != len(set(payload.missing_evidence_ids)):
        raise AuditContractError("Audit original references contain duplicate identities")
    meta = event.event_type in {"EPOCH_STARTED", "EPOCH_SEALED"}
    if meta:
        transition = payload.epoch_transition
        if (
            transition is None
            or payload.correlation_kind != "EPOCH"
            or event.aggregate_type != "EPOCH"
            or event.aggregate_id != event.epoch_id
            or event.correlation_id != event.epoch_id
        ):
            raise AuditContractError("Epoch metadata must bind its actual epoch")
        if any(
            (
                event.causation_id,
                event.decision_run_id,
                event.action_plan_id,
                event.action_receipt_id,
            )
        ):
            raise AuditContractError("Epoch metadata cannot invent business references")
        if (
            payload.references
            or payload.anchors
            or payload.changes
            or payload.missing_evidence_ids
            or payload.legacy_origin is not None
        ):
            raise AuditContractError("Epoch metadata cannot carry invented ordinary business facts")
        if event.event_type == "EPOCH_STARTED":
            if event.sequence_number != 1 or transition.kind not in {"INIT", "RESET"}:
                raise AuditContractError("Epoch genesis must be the first actual event")
            if transition.kind == "INIT" and any(
                (
                    transition.previous_epoch_id,
                    transition.previous_seal_hash,
                    transition.pre_seal_head,
                )
            ):
                raise AuditContractError("Initial epoch cannot invent a previous seal")
            if transition.kind == "RESET" and not all(
                (
                    transition.previous_epoch_id,
                    transition.previous_seal_hash,
                    transition.reset_key,
                    transition.reason,
                    transition.principal,
                    transition.dataset_hash,
                )
            ):
                raise AuditContractError(
                    "Reset genesis must bind the actual prior seal and new dataset"
                )
        elif (
            transition.kind != "SEAL"
            or transition.pre_seal_head is None
            or not all(
                (
                    transition.reset_key,
                    transition.reason,
                    transition.principal,
                    transition.archive_manifest_hash,
                )
            )
        ):
            raise AuditContractError("Epoch sealing must bind the actual archive and old head")
        if event.event_type == "EPOCH_SEALED":
            old = transition.pre_seal_head
            assert old is not None
            _head(old)
            if (
                old.status != "OPEN"
                or old.user_id != event.user_id
                or old.epoch_id != event.epoch_id
                or old.last_sequence + 1 != event.sequence_number
                or old.last_event_hash != event.previous_hash
                or transition.previous_epoch_id != old.previous_epoch_id
                or transition.previous_seal_hash != old.previous_seal_hash
            ):
                raise AuditContractError(
                    "Epoch sealing differs from the actual immediate OPEN predecessor head"
                )
    elif payload.epoch_transition is not None or payload.correlation_kind == "EPOCH":
        raise AuditContractError("Ordinary business events cannot impersonate epoch transitions")
    if not meta:
        shapes = {
            "DECISION_RECORDED": ("DECISION_RUN", "DECISION_RUN"),
            "ACTION_CREATED": ("ACTION_PLAN", "DECISION_RUN"),
            "ACTION_STATE_CHANGED": ("ACTION_PLAN", "DECISION_RUN"),
            "BANK_ACCEPTED": ("BANK_OPERATION", "DECISION_RUN"),
            "BANK_SETTLED": ("BANK_OPERATION", "DECISION_RUN"),
            "ACTION_PROJECTED": ("ACTION_RECEIPT", "DECISION_RUN"),
            "RECOVERY_OBSERVED": ("DECISION_RUN", "DECISION_RUN"),
            "POLICY_VERSION_CONFIRMED": ("POLICY_VERSION", "POLICY"),
            "POLICY_STATE_CHANGED": ("POLICY", "POLICY"),
            "GOAL_INITIALIZED": ("GOAL", "GOAL"),
            "TRANSACTION_CATEGORY_CONFIRMED": ("TRANSACTION", "TRANSACTION"),
        }
        if (event.aggregate_type, payload.correlation_kind) != shapes[event.event_type]:
            raise AuditContractError(
                "Audit event does not have its registered aggregate and correlation"
            )
        if payload.correlation_kind == "DECISION_RUN" and event.decision_run_id is None:
            raise AuditContractError("Business audit event lacks its actual decision run identity")
        if (
            event.event_type
            in {
                "ACTION_CREATED",
                "ACTION_STATE_CHANGED",
                "BANK_ACCEPTED",
                "BANK_SETTLED",
                "ACTION_PROJECTED",
            }
            and event.action_plan_id is None
        ):
            raise AuditContractError("Action audit event lacks its actual action identity")
        if (
            event.aggregate_type == "DECISION_RUN"
            and event.aggregate_id != event.decision_run_id
            or event.aggregate_type == "ACTION_PLAN"
            and event.aggregate_id != event.action_plan_id
            or event.aggregate_type == "ACTION_RECEIPT"
            and event.aggregate_id != event.action_receipt_id
        ):
            raise AuditContractError("Audit aggregate differs from its typed business identity")
        if event.event_type != "ACTION_PROJECTED" and event.action_receipt_id is not None:
            raise AuditContractError("Only actual projection may declare its application receipt")
        if payload.correlation_kind in {"POLICY", "GOAL", "TRANSACTION"} and any(
            (event.decision_run_id, event.action_plan_id, event.action_receipt_id)
        ):
            raise AuditContractError("Policy/goal metadata cannot invent action relationships")
        if (
            event.event_type
            in {"POLICY_STATE_CHANGED", "GOAL_INITIALIZED", "TRANSACTION_CATEGORY_CONFIRMED"}
            and event.aggregate_id != event.correlation_id
        ):
            raise AuditContractError(
                "Policy/goal fact correlation differs from its original aggregate"
            )
        required = {
            "DECISION_RECORDED": {"DECISION_TRACE"},
            "ACTION_CREATED": {"EXECUTION_REQUEST"},
            "BANK_ACCEPTED": {"EXECUTION_REQUEST"},
            "BANK_SETTLED": {"EXECUTION_REQUEST", "BANK_POSTING_SET"},
            "ACTION_PROJECTED": {"ACTION_RECEIPT", "BANK_POSTING_SET"},
            "POLICY_VERSION_CONFIRMED": {"POLICY_CONFIGURATION"},
            "TRANSACTION_CATEGORY_CONFIRMED": {"EVIDENCE_CONTENT"},
        }
        if not required.get(event.event_type, set()).issubset({a.kind for a in payload.anchors}):
            raise AuditContractError(
                "Registered audit event lacks its mandatory original content anchors"
            )
        anchor_keys = [(a.kind, a.reference_id) for a in payload.anchors]
        if len(anchor_keys) != len(set(anchor_keys)):
            raise AuditContractError("Audit event duplicates an original content anchor")
        state_event = event.event_type in {"ACTION_STATE_CHANGED", "POLICY_STATE_CHANGED"}
        if payload.changes and not state_event:
            raise AuditContractError(
                "Only registered state-change events may declare changed fields"
            )
        if state_event:
            if payload.context.reason_code is None:
                raise AuditContractError("Actual state changes need their original stable reason")
            if payload.changes:
                if len(payload.changes) != 1:
                    raise AuditContractError(
                        "State-change event needs its single actual aggregate transition"
                    )
                change = payload.changes[0]
                if (
                    change.kind != event.aggregate_type
                    or change.id != event.aggregate_id
                    or change.field != "status"
                    or type(change.before) is not str
                    or type(change.after) is not str
                    or change.before == change.after
                ):
                    raise AuditContractError(
                        "State-change event must bind a real original status transition"
                    )
            elif event.event_type != "ACTION_STATE_CHANGED" or not payload.context.details.get(
                "released_claim_ids"
            ):
                raise AuditContractError(
                    "A same-state audit fact needs actual resolved resource claims"
                )
    for change in payload.changes:
        if change.field.endswith("_cents") and any(
            v is not None and type(v) is not int for v in (change.before, change.after)
        ):
            raise AuditContractError("Changed audit money must be strict signed integer cents")

    def trusted_owners(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"user_id", "owner_user_id"} and child != str(event.user_id):
                    raise AuditContractError("Trusted audit context belongs to another tenant")
                trusted_owners(child)
        elif isinstance(value, list):
            for child in value:
                trusted_owners(child)

    trusted_owners(payload.context.details)
    if (event.event_type == "RECOVERY_OBSERVED") != (payload.observation is not None):
        raise AuditContractError("Recovery observation belongs only to its proper event")
    if payload.observation is not None:
        observation = payload.observation
        if (
            observation.run_id != event.decision_run_id
            or observation.action_id != event.action_plan_id
        ):
            raise AuditContractError("Recovery observation differs from its actual run/action")
        if (
            observation.kind in {"PROJECTION_FAILED", "BANK_ERROR"}
            and observation.error_code is None
        ):
            raise AuditContractError(
                "Failed recovery observation needs its stable original error code"
            )
        if observation.kind == "RUN_COMPLETED" and any(
            (
                observation.action_id,
                observation.request_id,
                observation.error_code,
                observation.bank_status,
            )
        ):
            raise AuditContractError("Completed run cannot invent a per-action bank error")
    canonical_bytes(event.model_dump(mode="python"))


def build_event(
    intent: "AuditIntentAny",
    *,
    event_id: UUID,
    epoch_id: UUID,
    sequence_number: int,
    previous_hash: str | None,
    observed_at: datetime,
    appended_at: datetime,
) -> "AuditEvent":
    normalized = _intent(intent)
    fields = normalized.model_dump(mode="python")
    fields.update(
        id=event_id,
        epoch_id=epoch_id,
        sequence_number=sequence_number,
        previous_hash=previous_hash,
        observed_at=observed_at,
        appended_at=appended_at,
        event_hash="0" * 64,
    )
    event = _event_model(fields, envelope=True).model_validate(fields)
    _event_content(event)
    digest = _digest("audit-event-v1", event.model_dump(mode="python", exclude={"event_hash"}))
    return event.model_copy(update={"event_hash": digest})


def verify_event(event: "AuditEvent") -> None:
    from app.domain.audit_chain_types import AuditEvent

    if not isinstance(event, AuditEvent):
        raise AuditContractError("Expected a frozen AuditEnvelope")
    _declared(event)
    fields = event.model_dump(mode="python")
    _protocol(fields, "audit-event-v1")
    _known_event_integrity(fields)
    event = _event_model(fields, envelope=True).model_validate(fields)
    _event_content(event)


def event_canonical_text(event: "AuditEvent") -> str:
    verify_event(event)
    return canonical_text(event.model_dump(mode="python"))


def parse_event(text: str) -> "AuditEvent":
    raw = _read(text)
    _protocol(raw, "audit-event-v1")
    _known_event_integrity(raw)
    event = _event_model(raw, envelope=True).model_validate_json(json.dumps(raw, allow_nan=False))
    verify_event(event)
    if event_canonical_text(event) != text:
        raise AuditContractError("Audit event text is not its original canonical encoding")
    return event


def same_intent(event: "AuditEvent", intent: "AuditIntentAny") -> bool:
    verify_event(event)
    return intent_digest(event) == intent_digest(intent)


def _head(head: "AuditHead") -> None:
    from app.domain.audit_chain_types import AuditHead

    _declared(head)
    restored = AuditHead.model_validate(head.model_dump(mode="python"))
    if restored.event_count != restored.last_sequence or not all(
        (
            restored.event_count,
            restored.last_event_id,
            restored.last_event_hash,
            restored.genesis_event_id,
            restored.genesis_event_hash,
        )
    ):
        raise AuditContractError(
            "Published audit head must have its complete actual genesis and tail"
        )
    if (restored.epoch_number == 1) != (
        restored.previous_epoch_id is None and restored.previous_seal_hash is None
    ):
        raise AuditContractError("Audit head epoch predecessor is inconsistent")
    if (restored.previous_epoch_id is None) != (restored.previous_seal_hash is None):
        raise AuditContractError("Audit head predecessor needs both identity and seal")
    if restored.previous_epoch_id == restored.epoch_id:
        raise AuditContractError("Audit epoch cannot precede itself")
    if len(canonical_bytes(restored.model_dump(mode="python"))) > 65536:
        raise AuditContractError("Audit head exceeds its byte limit")


def build_checkpoint(head: "AuditHead", *, captured_at: datetime) -> "AuditCheckpoint":
    from app.domain.audit_chain_types import AuditCheckpoint

    _head(head)
    assert head.genesis_event_id is not None and head.genesis_event_hash is not None
    assert head.last_event_id is not None and head.last_event_hash is not None
    checkpoint = AuditCheckpoint(
        user_id=head.user_id,
        epoch_id=head.epoch_id,
        epoch_number=head.epoch_number,
        genesis_event_id=head.genesis_event_id,
        genesis_event_hash=head.genesis_event_hash,
        expected_count=head.event_count,
        last_sequence=head.last_sequence,
        tail_id=head.last_event_id,
        tail_hash=head.last_event_hash,
        previous_epoch_id=head.previous_epoch_id,
        previous_seal_hash=head.previous_seal_hash,
        captured_at=captured_at,
        checkpoint_hash="0" * 64,
    )
    return checkpoint.model_copy(
        update={
            "checkpoint_hash": _digest(
                "audit-checkpoint-v1",
                checkpoint.model_dump(mode="python", exclude={"checkpoint_hash"}),
            )
        }
    )


def verify_checkpoint(checkpoint: "AuditCheckpoint") -> None:
    from app.domain.audit_chain_types import AuditCheckpoint

    _declared(checkpoint)
    restored = AuditCheckpoint.model_validate(checkpoint.model_dump(mode="python"))
    if (
        restored.expected_count != restored.last_sequence
        or len(canonical_bytes(restored.model_dump(mode="python"))) > 65536
        or _digest(
            "audit-checkpoint-v1", restored.model_dump(mode="python", exclude={"checkpoint_hash"})
        )
        != restored.checkpoint_hash
    ):
        raise AuditContractError("Audit checkpoint is inconsistent with its original content")


def checkpoint_canonical_text(checkpoint: "AuditCheckpoint") -> str:
    verify_checkpoint(checkpoint)
    return canonical_text(checkpoint.model_dump(mode="python"))


def parse_checkpoint(text: str) -> "AuditCheckpoint":
    from app.domain.audit_chain_types import AuditCheckpoint

    raw = _read(text)
    _protocol(raw, "audit-checkpoint-v1")
    checkpoint = AuditCheckpoint.model_validate_json(json.dumps(raw, allow_nan=False))
    verify_checkpoint(checkpoint)
    if checkpoint_canonical_text(checkpoint) != text:
        raise AuditContractError("Audit checkpoint text is not canonical")
    return checkpoint


def _seal_content(seal: "AuditEpochSeal") -> None:
    _head(seal.head)
    if seal.head.status != "SEALED" or (
        seal.user_id,
        seal.epoch_id,
        seal.epoch_number,
        seal.previous_epoch_id,
        seal.previous_seal_hash,
    ) != (
        seal.head.user_id,
        seal.head.epoch_id,
        seal.head.epoch_number,
        seal.head.previous_epoch_id,
        seal.head.previous_seal_hash,
    ):
        raise AuditContractError("Audit seal must bind the final original sealed head")
    if len(canonical_bytes(seal.model_dump(mode="python"))) > 65536:
        raise AuditContractError("Audit seal exceeds its byte limit")


def build_seal(**fields: Any) -> "AuditEpochSeal":
    from app.domain.audit_chain_types import AuditEpochSeal

    if "seal_hash" in fields:
        raise AuditContractError("Caller cannot supply an audit seal hash")
    seal = AuditEpochSeal.model_validate({**fields, "seal_hash": "0" * 64})
    _seal_content(seal)
    return seal.model_copy(
        update={
            "seal_hash": _digest(
                "audit-epoch-seal-v1", seal.model_dump(mode="python", exclude={"seal_hash"})
            )
        }
    )


def verify_seal(seal: "AuditEpochSeal", previous: "AuditEpochSeal | None" = None) -> None:
    from app.domain.audit_chain_types import AuditEpochSeal

    _declared(seal)
    restored = AuditEpochSeal.model_validate(seal.model_dump(mode="python"))
    _seal_content(restored)
    if restored.seal_hash != _digest(
        "audit-epoch-seal-v1", restored.model_dump(mode="python", exclude={"seal_hash"})
    ):
        raise AuditContractError("Audit epoch seal differs from its original hash")
    if previous is not None:
        _declared(previous)
        if (
            previous.seal_hash
            != _digest(
                "audit-epoch-seal-v1", previous.model_dump(mode="python", exclude={"seal_hash"})
            )
            or previous.user_id != seal.user_id
            or previous.epoch_number + 1 != seal.epoch_number
            or previous.epoch_id != seal.previous_epoch_id
            or previous.seal_hash != seal.previous_seal_hash
        ):
            raise AuditContractError("Audit previous seal belongs to a different original epoch")


def seal_canonical_text(seal: "AuditEpochSeal") -> str:
    verify_seal(seal)
    return canonical_text(seal.model_dump(mode="python"))


def parse_seal(text: str) -> "AuditEpochSeal":
    from app.domain.audit_chain_types import AuditEpochSeal

    raw = _read(text)
    _protocol(raw, "audit-epoch-seal-v1")
    seal = AuditEpochSeal.model_validate_json(json.dumps(raw, allow_nan=False))
    verify_seal(seal)
    if seal_canonical_text(seal) != text:
        raise AuditContractError("Audit seal text is not canonical")
    return seal


def archive_manifest(subjects: Iterable["AuditSubject"]) -> dict[str, Any]:
    entries = []
    counts: dict[str, int] = {}
    seen = set()
    identity = None
    for subject in subjects:
        original = build_subject(**subject.model_dump(mode="python"))
        owner = (original.user_id, original.epoch_id)
        if identity is not None and identity != owner:
            raise AuditContractError("Audit archive manifest mixes tenants or epochs")
        identity = owner
        digest = subject_hash(original)
        key = (original.kind, str(original.id), digest)
        if key in seen:
            raise AuditContractError("Audit archive manifest repeats an original snapshot version")
        seen.add(key)
        entries.append({"kind": original.kind, "id": str(original.id), "snapshot_hash": digest})
        counts[original.kind] = counts.get(original.kind, 0) + 1
    entries.sort(key=lambda item: (item["kind"], item["id"], item["snapshot_hash"]))
    return {"entries": entries, "counts": counts}


def archive_manifest_digest(subjects: Iterable["AuditSubject"]) -> str:
    return hashlib.sha256(
        b"bounded-funds/audit-archive-v1\0" + archive_manifest_bytes(archive_manifest(subjects))
    ).hexdigest()


def posting_set_digest(postings: Iterable[dict[str, Any]]) -> str:
    """Original full non-opening legs, with a deterministic explicit set order."""
    from app.domain.bank_posting_codec import validate_posting_original
    from app.domain.policy_configuration import configuration_hash

    originals = []
    for row in postings:
        validate_posting_original(row, 1)
        if row.get("entry_kind") != "OPENING":
            originals.append(json.loads(canonical_bytes(row)))
    if not originals or len(originals) > 10000:
        raise AuditContractError(
            "Audit settlement needs its bounded actual non-opening posting set"
        )
    if len({row["id"] for row in originals}) != len(originals):
        raise AuditContractError("Audit settlement posting identities are duplicated")
    originals.sort(key=lambda row: (row["ledger_key"], row["sequence_number"], row["id"]))
    return configuration_hash({"postings": originals})


def receipt_digest(data: dict[str, Any]) -> str:
    from app.domain.policy_configuration import configuration_hash

    original = json.loads(canonical_bytes(data))
    for field in ("executed_cents", "fee_cents", "loss_cents"):
        if type(original.get(field)) is not int or original[field] < 0:
            raise AuditContractError("Audit receipt amounts must be nonnegative integer cents")
    return configuration_hash(original)


def _clock(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuditContractError("Frozen financial time must be aware")
    return value.astimezone(UTC)


def _financial_identity(
    operation: dict[str, Any],
    action: dict[str, Any],
    observed_at: datetime,
    redemption: dict[str, Any] | None,
) -> tuple[Any, dict[str, tuple[str, str, int]]]:
    from app.domain.execution import ACTION_PLAN_TYPES
    from app.domain.execution_types import BankCommand
    from app.domain.income_ledger import location_id
    from app.domain.policy_configuration import configuration_hash

    canonical_bytes(operation, raw=True)
    canonical_bytes(action, raw=True)
    if type(action.get("amount_cents")) is not int or action["amount_cents"] <= 0:
        raise AuditContractError("Frozen action amount must be positive strict integer cents")
    identity = UUID(operation["id"])
    user = UUID(operation["user_id"])
    if (
        operation["action_plan_id"] != action["id"]
        or action["user_id"] != str(user)
        or operation["idempotency_key"] != action["idempotency_key"]
        or configuration_hash(action["request"]) != action["request_hash"]
        or configuration_hash(operation["request"]) != operation["request_hash"]
    ):
        raise AuditContractError("Frozen bank and action request identities disagree")
    requested, available = _clock(operation["requested_at"]), _clock(operation["available_at"])
    if requested > observed_at or available < requested:
        raise AuditContractError("Frozen bank acceptance has an impossible fact clock")
    specs: dict[str, tuple[str, str, int]] = {}
    if operation["operation_type"] == "LEGACY_REDEMPTION":
        command = operation["request"]
        keys = {
            "user_id",
            "position_id",
            "position_account_id",
            "product_id",
            "goal_id",
            "original_policy_version_id",
            "destination_account_id",
            "principal_cents",
            "requested_at",
            "available_at",
            "expires_at",
            "kind",
        }
        if (
            type(command) is not dict
            or set(command) != keys
            or command["kind"] not in {"REDEEM", "MATURE"}
        ):
            raise AuditContractError("Frozen legacy request shape is unsupported")
        canonical_bytes(command)
        principal = command["principal_cents"]
        if (
            type(principal) is not int
            or principal <= 0
            or command["user_id"] != str(user)
            or identity != uuid5(UUID(action["id"]), "simulated-bank-redemption")
            or operation.get("legacy_redemption_id") != str(identity)
            or operation.get("closing_position_id") != command["position_id"]
            or operation["business_key"] != "close:" + command["position_id"]
            or action["request"].get("bank_request") != command
            or action["action_type"]
            != ("ASSET_MATURITY" if command["kind"] == "MATURE" else "ASSET_REDEEM")
        ):
            raise AuditContractError("Frozen legacy command does not bind its original action")
        pairs = {
            "position_id": "position_id",
            "source_account_id": "position_account_id",
            "product_id": "product_id",
            "goal_id": "goal_id",
            "amount_cents": "principal_cents",
            "expires_at": "expires_at",
        }
        for field, command_field in pairs.items():
            actual, expected = action.get(field), command[command_field]
            if (
                (_clock(actual) != _clock(expected))
                if field == "expires_at"
                else (actual != expected)
            ):
                raise AuditContractError("Frozen legacy action field differs: " + field)
        destination = command["destination_account_id"]
        if (
            action.get("destination_account_id")
            != (destination if destination != action["source_account_id"] else None)
            or requested != _clock(command["requested_at"])
            or available != _clock(command["available_at"])
            or not requested <= _clock(operation["created_at"]) < _clock(command["expires_at"])
        ):
            raise AuditContractError("Frozen legacy request clocks or destination differ")
        if redemption is not None:
            for field in (
                "id",
                "user_id",
                "action_plan_id",
                "request",
                "request_hash",
                "idempotency_key",
                "requested_at",
                "available_at",
                "settled_at",
                "status",
                "created_at",
            ):
                if canonical_bytes({"v": redemption.get(field)}, raw=True) != canonical_bytes(
                    {"v": operation.get(field)}, raw=True
                ):
                    raise AuditContractError(
                        "Frozen unified bank row differs from the original redemption: " + field
                    )
        specs = {
            "CASH_CREDIT": ("CASH:" + destination, "ECONOMIC", principal),
            "PRINCIPAL_DEBIT": ("POSITION:" + command["position_id"], "ECONOMIC", -principal),
        }
        return command, specs
    if operation["operation_type"] == "RELEASE_GOAL":
        from app.domain.full_goal_release_audit import frozen_goal_release_identity

        try:
            return frozen_goal_release_identity(operation, action, observed_at)
        except (KeyError, TypeError, ValueError) as error:
            raise AuditContractError("Frozen dedicated release identity differs") from error
    command_model = BankCommand.model_validate_json(
        json.dumps(operation["request"], allow_nan=False)
    )
    effect = command_model.effect
    if (
        "expires_at" in action
        and _clock(action["expires_at"]) != effect.expires_at
        or not effect.valid_from <= requested < effect.expires_at
    ):
        raise AuditContractError("Frozen request did not occur inside its original effect validity")
    source = str(effect.cash_uses[0].account_id if effect.cash_uses else effect.position_account_id)
    if (
        action["request"].get("execution") != operation["request"]
        or effect.operation_id != identity
        or operation["id"] != action["id"]
        or effect.user_id != user
        or operation["operation_type"] != effect.action_type
        or operation["business_key"] != effect.business_key
        or action["action_type"] != ACTION_PLAN_TYPES[effect.action_type]
        or action["amount_cents"] != effect.amount_cents
        or action["source_account_id"] != source
    ):
        raise AuditContractError(
            "Frozen execution command differs from its complete original action"
        )
    for field in ("goal_id", "product_id", "policy_version_id"):
        value = getattr(effect, field)
        if action.get(field) != (str(value) if value is not None else None):
            raise AuditContractError("Frozen execution action identity differs: " + field)
    position = str(effect.position_id) if effect.position_id else None
    if (
        (action.get("position_id") not in {None, position})
        if effect.action_type == "PURCHASE_ASSET"
        else (action.get("position_id") != position)
    ):
        raise AuditContractError("Frozen execution action position differs")
    destination = str(effect.destination_account_id) if effect.destination_account_id else None
    if action.get("destination_account_id") != (destination if destination != source else None):
        raise AuditContractError("Frozen execution destination differs")
    cash = {str(use.account_id): -use.amount_cents for use in effect.cash_uses}
    if destination is not None:
        credit = effect.net_cents if effect.action_type == "REDEEM_ASSET" else effect.amount_cents
        assert credit is not None
        cash[destination] = cash.get(destination, 0) + credit
    specs.update(
        {"cash:" + key: ("CASH:" + key, "ECONOMIC", value) for key, value in cash.items() if value}
    )
    if effect.action_type in {"PURCHASE_ASSET", "REDEEM_ASSET"}:
        specs["position"] = (
            "POSITION:" + str(effect.position_id),
            "ECONOMIC",
            effect.amount_cents if effect.action_type == "PURCHASE_ASSET" else -effect.amount_cents,
        )
    if effect.action_type == "PAY_RECURRING":
        specs["payee"] = (
            "PAYEE:" + str(uuid5(user, str(effect.payee_id))),
            "ECONOMIC",
            effect.amount_cents,
        )
        liability = str(uuid5(user, "liability:" + effect.business_key))
        specs["liability_due"] = ("LIABILITY_DUE:" + liability, "LIABILITY", -effect.amount_cents)
        specs["liability_paid"] = ("LIABILITY_PAID:" + liability, "LIABILITY", effect.amount_cents)
    for label, value in (("FEE", effect.fee_cents), ("LOSS", effect.loss_cents)):
        if value:
            specs[label.lower()] = (label + ":" + str(user), "ECONOMIC", value)
    if effect.goal_id is not None:
        cash_value = (
            effect.amount_cents
            if effect.action_type == "ALLOCATE_GOAL"
            else -effect.amount_cents
            if effect.action_type == "PURCHASE_ASSET"
            else effect.net_cents
        )
        principal_value = (
            effect.amount_cents
            if effect.action_type == "PURCHASE_ASSET"
            else -effect.amount_cents
            if effect.action_type == "REDEEM_ASSET"
            else 0
        )
        for label, value in (
            ("GOAL_CASH", cash_value),
            ("GOAL_PRINCIPAL", principal_value),
            ("GOAL_LOSS", effect.fee_cents + effect.loss_cents),
        ):
            if value:
                specs[label.lower()] = (label + ":" + str(effect.goal_id), "GOAL_OWNERSHIP", value)
    for use in effect.income_uses:
        specs["income_out:" + str(use.fragment_id)] = (
            "LOT_AVAILABLE:" + str(use.fragment_id),
            "INCOME_LOCATION",
            -use.amount_cents,
        )
        target = (
            location_id(use.origin_transaction_id, effect.destination_account_id)
            if effect.action_type == "TRANSFER_INTERNAL"
            and effect.destination_account_id is not None
            else use.fragment_id
        )
        bucket = (
            "AVAILABLE"
            if effect.action_type == "TRANSFER_INTERNAL"
            else "ASSIGNED"
            if effect.action_type == "ALLOCATE_GOAL"
            else "SPENT"
        )
        specs["income_in:" + str(use.fragment_id)] = (
            "LOT_" + bucket + ":" + str(target),
            "INCOME_LOCATION",
            use.amount_cents,
        )
    return effect, specs


def verify_frozen_settlement(
    operation: dict[str, Any],
    action: dict[str, Any],
    postings: Iterable[dict[str, Any]],
    *,
    observed_at: datetime,
    redemption: dict[str, Any] | None = None,
) -> None:
    """Verify original complete settlement legs; never inspect current ledger balances."""
    observed_at = _clock(observed_at)
    command, specs = _financial_identity(operation, action, observed_at, redemption)
    rows = list(postings)
    if not rows or len(rows) > 10000 or operation["status"] != "SETTLED":
        raise AuditContractError("Frozen settlement must have its actual complete posting set")
    settled = _clock(operation["settled_at"])
    if not _clock(operation["available_at"]) <= settled <= observed_at or (
        type(command) is dict and settled != _clock(operation["available_at"])
    ):
        raise AuditContractError("Frozen settlement has an impossible fact clock")
    actual = {}
    for row in rows:
        canonical_bytes(row)
        leg = row["leg_ref"]
        if (
            leg in actual
            or row["operation_id"] != operation["id"]
            or row["user_id"] != operation["user_id"]
            or row["entry_kind"] == "OPENING"
            or _clock(row["occurred_at"]) != settled
            or not settled <= _clock(row["created_at"]) <= observed_at
            or row["balance_after_cents"] != row["balance_before_cents"] + row["delta_cents"]
        ):
            raise AuditContractError(
                "Frozen posting identity, clock or signed balance is inconsistent"
            )
        identity = uuid5(UUID(operation["id"]), leg if type(command) is dict else "posting:" + leg)
        if row["id"] != str(identity):
            raise AuditContractError("Frozen posting does not bind its original bank operation")
        actual[leg] = (row["ledger_key"], row["ledger_dimension"], row["delta_cents"])
        if operation["operation_type"] == "RELEASE_GOAL":
            from app.domain.full_goal_release_audit import verify_frozen_goal_release_posting

            try:
                verify_frozen_goal_release_posting(command, row)
            except (KeyError, TypeError, ValueError) as error:
                raise AuditContractError(
                    "Frozen dedicated release leg changed its origin"
                ) from error
        if type(command) is dict and (
            row.get("redemption_id") != operation["id"]
            or row["entry_kind"] != leg
            or (leg == "PRINCIPAL_DEBIT" and row["balance_after_cents"] != 0)
        ):
            raise AuditContractError("Frozen legacy posting does not bind its original redemption")
        if row["ledger_key"].startswith("CASH:") and (
            row.get("account_id") != row["ledger_key"][5:] or row.get("position_id") is not None
        ):
            raise AuditContractError("Frozen cash posting has a different actual account")
        if row["ledger_key"].startswith("POSITION:") and (
            row.get("position_id") != row["ledger_key"][9:] or row.get("account_id") is not None
        ):
            raise AuditContractError("Frozen principal posting has a different actual position")
        if (
            row["ledger_key"].startswith(("CASH:", "POSITION:"))
            and min(row["balance_before_cents"], row["balance_after_cents"]) < 0
        ):
            raise AuditContractError(
                "Frozen cash or principal posting has an impossible negative balance"
            )
    if (
        actual != specs
        or sum(row["delta_cents"] for row in rows if row["ledger_dimension"] == "ECONOMIC") != 0
    ):
        raise AuditContractError(
            "Frozen settlement differs from the exact economic and ownership effect"
        )


def verify_frozen_projection(
    operation: dict[str, Any],
    action: dict[str, Any],
    receipt: dict[str, Any],
    postings: Iterable[dict[str, Any]],
    *,
    observed_at: datetime,
    redemption: dict[str, Any] | None = None,
    subjects: Iterable["AuditSubject"] = (),
) -> None:
    """Verify the original receipt and optional explicitly declared projection originals."""
    rows = list(postings)
    verify_frozen_settlement(
        operation, action, rows, observed_at=observed_at, redemption=redemption
    )
    command, _ = _financial_identity(operation, action, _clock(observed_at), redemption)
    receipt_digest(receipt)
    legacy = type(command) is dict
    amount = command["principal_cents"] if legacy else command.amount_cents
    fee, loss = (0, 0) if legacy else (command.fee_cents, command.loss_cents)
    response = receipt["response"]
    posting_ids = response.get("posting_ids")
    bank_field = "bank_request_id" if legacy else "bank_operation_id"
    if (
        action["status"] not in {"SUCCEEDED", "RECONCILED"}
        or receipt["id"] != str(uuid5(UUID(operation["id"]), "receipt"))
        or receipt["user_id"] != operation["user_id"]
        or receipt["action_plan_id"] != action["id"]
        or receipt["status"] != "SUCCEEDED"
        or type(receipt["attempt_number"]) is not int
        or receipt["attempt_number"] != 1
        or receipt["receipt_ref"] != ("bank:" if legacy else "bank-operation:") + operation["id"]
        or (receipt["executed_cents"], receipt["fee_cents"], receipt["loss_cents"])
        != (amount, fee, loss)
        or response.get(bank_field) != operation["id"]
        or type(posting_ids) is not list
        or len(posting_ids) != len(rows)
        or set(posting_ids) != {r["id"] for r in rows}
    ):
        raise AuditContractError("Frozen receipt cannot be rebound to a different original effect")
    settled, reconciled = _clock(receipt["occurred_at"]), _clock(receipt["reconciled_at"])
    if (
        settled != _clock(operation["settled_at"])
        or not settled <= reconciled <= _clock(observed_at)
        or _clock(receipt["created_at"]) != reconciled
        or any(_clock(row["created_at"]) > reconciled for row in rows)
    ):
        raise AuditContractError("Frozen receipt observation and settlement clocks disagree")
    originals = {(s.kind, str(s.id)): s.data for s in subjects}
    if operation["operation_type"] == "RELEASE_GOAL":
        from app.domain.full_goal_release_execution import GoalReleaseEffect
        from app.domain.full_goal_release_projection_audit import (
            verify_frozen_goal_release_projection,
        )

        if not isinstance(command, GoalReleaseEffect):
            raise AuditContractError("Frozen release projection needs its exact new effect")
        try:
            verify_frozen_goal_release_projection(command, operation, receipt, rows, originals)
        except (ValueError, KeyError, TypeError) as error:
            raise AuditContractError("Frozen release internal transfer originals differ") from error
    transaction_ids = list(response.get("transaction_ids", []))
    if response.get("transaction_id") is not None:
        transaction_ids.append(response["transaction_id"])
    if legacy and (
        set(response) != {"bank_request_id", "posting_ids", "transaction_id"}
        or response.get("transaction_id") != str(uuid5(UUID(operation["id"]), "transaction"))
    ):
        raise AuditContractError("Frozen legacy receipt lacks its exact original transaction")
    if len(transaction_ids) != len(set(transaction_ids)):
        raise AuditContractError("Frozen receipt repeats transaction identities")
    for identity in transaction_ids:
        transaction = originals.get(("TRANSACTION", identity))
        if transaction is None or transaction.get("user_id") != operation["user_id"]:
            raise AuditContractError("Frozen receipt transaction original is missing")
        canonical_bytes(transaction)
        cash = [
            r
            for r in rows
            if r["ledger_key"].startswith("CASH:")
            and r.get("account_id") == transaction.get("account_id")
            and abs(r["delta_cents"]) == transaction.get("amount_cents")
        ]
        if (
            len(cash) != 1
            or transaction.get("direction") != ("CREDIT" if cash[0]["delta_cents"] > 0 else "DEBIT")
            or transaction.get("balance_after_cents") != cash[0]["balance_after_cents"]
            or _clock(transaction["occurred_at"]) != settled
        ):
            raise AuditContractError("Frozen transaction differs from its actual original cash leg")
        proof_id = transaction.get("evidence_id")
        if type(proof_id) is not str:
            raise AuditContractError("Frozen transaction proof identity is invalid")
        evidence = originals.get(("EVIDENCE", proof_id))
        if evidence is None:
            raise AuditContractError("Frozen transaction proof original is missing")
        from app.domain.policy_configuration import configuration_hash

        content = evidence.get("content")
        if (
            type(content) is not dict
            or evidence.get("source_type") != "SIMULATED_BANK_TRANSACTION"
            or evidence.get("evidence_level") != "BANK_CONFIRMED"
            or configuration_hash(content) != evidence.get("content_hash")
            or any(
                content.get(field) != transaction.get(field)
                for field in (
                    "user_id",
                    "account_id",
                    "direction",
                    "amount_cents",
                    "balance_after_cents",
                )
            )
            or content.get("transaction_id") != identity
            or content.get("simulation") is not True
            or _clock(content.get("occurred_at")) != settled
        ):
            raise AuditContractError(
                "Frozen transaction proof differs from its actual original transaction"
            )
        if legacy:
            expected_ref = "bank-redemption:" + operation["id"] + ":principal"
            expected_content = {
                "simulation": True,
                "user_id": operation["user_id"],
                "transaction_id": identity,
                "account_id": transaction["account_id"],
                "direction": "CREDIT",
                "amount_cents": amount,
                "balance_after_cents": cash[0]["balance_after_cents"],
                "occurred_at": settled.isoformat(),
                "counterparty_ref": "position:" + command["position_id"],
                "economic_role": "PRINCIPAL_RETURN",
                "bank_request_id": operation["id"],
                "bank_posting_id": cash[0]["id"],
            }
            if (
                proof_id != str(uuid5(UUID(operation["id"]), "transaction-evidence"))
                or transaction.get("source_ref") != expected_ref
                or transaction.get("counterparty_ref") != expected_content["counterparty_ref"]
                or evidence.get("source_ref") != expected_ref
                or content != expected_content
                or _clock(evidence["valid_from"]) != settled
                or any(
                    _clock(row[field]) != reconciled
                    for row, field in (
                        (transaction, "created_at"),
                        (transaction, "observed_at"),
                        (evidence, "created_at"),
                        (evidence, "observed_at"),
                    )
                )
            ):
                raise AuditContractError(
                    "Frozen principal return proof cannot be relabeled as income or rebound"
                )


def verify_frozen_ledgers(postings: Iterable[dict[str, Any]]) -> None:
    """Check the complete archived economic chains from their retained actual openings."""
    from app.domain.bank_posting_codec import (
        bank_posting_snapshot_version,
        validate_posting_original,
    )

    rows = list(postings)
    if len(rows) > 100000:
        raise AuditBudgetExceeded("Frozen ledger count budget exceeded")
    heads: dict[str, dict[str, Any]] = {}
    identities: set[str] = set()
    owner: str | None = None
    try:
        rows.sort(key=lambda row: (row["ledger_key"], row["sequence_number"]))
        for row in rows:
            canonical_bytes(row, raw=True)
            validate_posting_original(row, bank_posting_snapshot_version(row))
            if owner is not None and row["user_id"] != owner:
                raise AuditContractError("Frozen economic ledgers mix tenants")
            owner = row["user_id"]
            UUID(owner)
            for field in (
                "balance_before_cents",
                "delta_cents",
                "balance_after_cents",
                "sequence_number",
            ):
                if type(row[field]) is not int or not -(2**63) <= row[field] <= 2**63 - 1:
                    raise AuditContractError("Frozen ledger values must be strict signed integers")
            if (
                row["id"] in identities
                or row["balance_after_cents"] != row["balance_before_cents"] + row["delta_cents"]
            ):
                raise AuditContractError(
                    "Frozen ledger posting identity or conservation is inconsistent"
                )
            identities.add(row["id"])
            previous = heads.get(row["ledger_key"])
            if previous is None:
                if (
                    row["sequence_number"] != 1
                    or row["entry_kind"] != "OPENING"
                    or row.get("previous_posting_id") is not None
                    or row["balance_before_cents"] != 0
                ):
                    raise AuditContractError("Frozen economic ledger lacks its actual opening")
            elif (
                row.get("previous_posting_id") != previous["id"]
                or row["sequence_number"] != previous["sequence_number"] + 1
                or row["balance_before_cents"] != previous["balance_after_cents"]
                or _clock(row["occurred_at"]) < _clock(previous["occurred_at"])
            ):
                raise AuditContractError(
                    "Frozen economic ledger predecessor or original balance differs"
                )
            heads[row["ledger_key"]] = row
    except (KeyError, TypeError) as error:
        raise AuditContractError("Malformed frozen economic ledger original") from error


def _subject_index(
    bundle: "ReferenceBundle", user_id: UUID, epoch_id: UUID
) -> dict[tuple[str, UUID, str], "AuditSubject"]:
    result = {}
    for subject in bundle.subjects:
        original = build_subject(**subject.model_dump(mode="python"))
        if original.user_id != user_id or original.epoch_id != epoch_id:
            raise AuditContractError("Original audit subject belongs to another tenant or epoch")
        key = (original.kind, original.id, subject_hash(original))
        if key in result:
            raise AuditContractError("Original subject bundle has duplicate versions")
        result[key] = original
    return result


def _current_index(
    bundle: "ReferenceBundle", user_id: UUID, epoch_id: UUID
) -> dict[tuple[str, UUID], "AuditSubject"]:
    result: dict[tuple[str, UUID], AuditSubject] = {}
    for subject in bundle.current_subjects:
        original = build_subject(**subject.model_dump(mode="python"))
        if original.user_id != user_id or original.epoch_id != epoch_id:
            raise AuditContractError("Current audit subject belongs to another tenant or epoch")
        key = original.kind, original.id
        if key in result and subject_hash(result[key]) != subject_hash(original):
            raise AuditContractError("Current subject bundle has conflicting identities")
        result[key] = original
    return result


def _immutable(
    original: "AuditSubject", current: dict[tuple[str, UUID], "AuditSubject"], fields: Iterable[str]
) -> None:
    latest = current.get((original.kind, original.id))
    if latest is not None:
        for field in fields:
            if canonical_bytes({"value": latest.data.get(field)}, raw=True) != canonical_bytes(
                {"value": original.data.get(field)}, raw=True
            ):
                raise AuditContractError(
                    "Current immutable "
                    + original.kind
                    + "."
                    + field
                    + " differs from its original anchor"
                )


def _missing_references(value: Any) -> set[UUID]:
    missing: set[UUID] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "missing_evidence_references":
                if type(child) is not list:
                    raise AuditContractError("Original missing evidence claims must be a UUID list")
                missing.update(UUID(identity) for identity in child)
            else:
                missing.update(_missing_references(child))
    elif isinstance(value, list):
        for child in value:
            missing.update(_missing_references(child))
    return missing


def _trace_algorithms_supported(versions: dict[str, str]) -> bool:
    from app.domain.asset_allocation import ALGORITHM_VERSION as asset
    from app.domain.autonomy import ALGORITHM_VERSION as autonomy
    from app.domain.boundary import ALGORITHM_VERSION as boundary
    from app.domain.execution import ALGORITHM_VERSION as execution
    from app.domain.goal_allocation import ALGORITHM_VERSION as goal
    from app.domain.recovery import ALGORITHM_VERSION as recovery

    supported = {
        asset,
        autonomy,
        boundary,
        execution,
        goal,
        recovery,
        "decision-trace-v1",
        "execution-bank-v1",
        "legacy-redemption-v1",
        "boundary-action-observation-v1",
        "full-one-question-v1",
        "full-one-question-close-v1",
        "full-intervention-v1",
        "full-finite-planning-minimax-v1",
        "full-goal-release-authorization-v1",
        "full-goal-emergency-release-v1",
        "full-payment-relation-v1",
        "full-dynamic-goal-execution-v1",
        "full-policy-action-set-boundary-v1",
        "full-policy-action-set-boundary-full-v1",
        "full-policy-action-set-boundary-actual-v2",
        "full-policy-action-set-boundary-recovery-composed-v4",
        "full-seasonal-adoption-v1",
        "full-recovery-execution-v1",
        "full-maturity-user-execution-v1",
        "full-experiment-asset-execution-v1",
        "registered-joint-goal-execution-v2",
    }
    return all(value in supported for value in versions.values())


def _external_references(
    event: "AuditEvent", index: dict[tuple[str, UUID, str], "AuditSubject"]
) -> None:
    from app.domain.external_bank_fact import (
        verify_external_projection,
        verify_external_settlement,
    )

    originals = [index[(ref.kind, ref.id, ref.snapshot_hash)] for ref in event.payload.references]
    facts = [
        original
        for original in originals
        if original.kind == "BANK_EXTERNAL_FACT" and original.id == event.correlation_id
    ]
    if len(facts) != 1:
        raise AuditContractError("External event requires its unique actual stage fact original")
    fact = facts[0]
    if not any(
        ref.kind == fact.kind
        and ref.id == fact.id
        and ref.snapshot_hash == subject_hash(fact)
        and ref.role == "AFTER"
        for ref in event.payload.references
    ):
        raise AuditContractError("External event fact original lacks its registered stage role")
    rows = [
        original.data
        for original in originals
        if original.kind == "BANK_POSTING" and original.data.get("external_fact_id") == str(fact.id)
    ]
    if fact.data.get("bank_status") != "SETTLED":
        raise AuditContractError("External bank event lacks an actual settled bank fact")
    result = verify_external_settlement(fact.data, rows)
    expected = {
        "EXTERNAL_BANK_REQUEST": fact.data["request_hash"],
        "EXTERNAL_BANK_RESULT": fact.data["bank_result_hash"],
        "EXTERNAL_BANK_POSTING_SET": result.posting_digest,
    }
    clock_field = "settled_at"
    if event.event_type == "EXTERNAL_BANK_FACT_PROJECTED":
        verify_external_projection(fact.data, rows, subjects=originals)
        expected["EXTERNAL_BANK_PROJECTION"] = fact.data["projection_result_hash"]
        clock_field = "projected_at"
    if event.occurred_at != _clock(fact.data[clock_field]) or event.observed_at < _clock(
        fact.data["observed_at"]
    ):
        raise AuditContractError("External audit fact clock differs from its actual stage original")
    for anchor in event.payload.anchors:
        typed = ("BANK_EXTERNAL_FACT", anchor.reference_id, anchor.snapshot_hash)
        if index.get(typed) is not fact or anchor.snapshot_hash != subject_hash(fact):
            raise AuditContractError("External anchor lacks its exact typed stage fact original")
        if anchor.kind not in expected or anchor.digest != expected[anchor.kind]:
            raise AuditContractError("External anchor differs from its complete original content")


def _references(
    event: "AuditEvent",
    index: dict[tuple[str, UUID, str], "AuditSubject"],
    current: dict[tuple[str, UUID], "AuditSubject"],
) -> None:
    from app.domain.decision_trace import verify_trace
    from app.domain.decision_trace_types import DecisionTrace
    from app.domain.policy_configuration import configuration_hash

    refs = event.payload.references
    available = {(r.kind, r.id) for r in refs}
    if (
        event.aggregate_type != "EPOCH"
        and (event.aggregate_type, event.aggregate_id) not in available
    ):
        raise AuditContractError("Audit aggregate lacks its actual typed original reference")
    if (
        event.payload.correlation_kind != "EPOCH"
        and (
            "BANK_EXTERNAL_FACT"
            if event.payload.correlation_kind == "EXTERNAL_BANK_FACT"
            else event.payload.correlation_kind,
            event.correlation_id,
        )
        not in available
    ):
        raise AuditContractError("Audit correlation lacks its actual typed original reference")
    for kind, identity in (
        ("DECISION_RUN", event.decision_run_id),
        ("ACTION_PLAN", event.action_plan_id),
        ("ACTION_RECEIPT", event.action_receipt_id),
    ):
        if identity is not None and (kind, identity) not in available:
            raise AuditContractError("Audit typed relationship lacks its original reference")
    referenced = {}
    for ref in refs:
        original = index.get((ref.kind, ref.id, ref.snapshot_hash))
        if original is None:
            raise AuditContractError("Audit original reference is missing or its digest differs")
        if original.scope != ref.scope or original.snapshot_version != ref.snapshot_version:
            raise AuditContractError("Audit original scope or version differs from its reference")
        referenced[(ref.id, ref.snapshot_hash)] = original
        if original.kind == "EVIDENCE":
            _immutable(
                original,
                current,
                (
                    "content",
                    "content_hash",
                    "evidence_level",
                    "source_type",
                    "source_ref",
                    "observed_at",
                    "valid_from",
                    "valid_to",
                    "supersedes_id",
                ),
            )
        elif original.kind == "POLICY_VERSION":
            _immutable(original, current, original.data)
        elif original.kind == "BANK_POSTING":
            from app.domain.bank_posting_codec import validate_posting_original

            validate_posting_original(original.data, original.snapshot_version)
            latest = current.get((original.kind, original.id))
            if latest is not None:
                validate_posting_original(latest.data, latest.snapshot_version)
                if latest.snapshot_version != original.snapshot_version:
                    raise AuditContractError("Current posting acquired a different original origin")
            _immutable(original, current, original.data)
        elif original.kind == "BANK_EXTERNAL_FACT":
            _external_fact_immutable(original, current)
        elif original.kind == "ACTION_RECEIPT":
            _immutable(original, current, original.data)
        elif original.kind == "BANK_OPERATION":
            _immutable(
                original,
                current,
                (
                    "action_plan_id",
                    "operation_type",
                    "business_key",
                    "request",
                    "request_hash",
                    "idempotency_key",
                    "requested_at",
                    "available_at",
                    "legacy_redemption_id",
                    "closing_position_id",
                    "created_at",
                ),
            )
            if original.data.get("status") == "SETTLED":
                _immutable(original, current, ("status", "settled_at"))
    if event.payload_version == 2:
        _external_references(event, index)
        return
    event = cast("AuditEnvelope", event)
    if event.event_type == "TRANSACTION_CATEGORY_CONFIRMED":
        from app.domain.transaction_category import verify_category_transition

        def category_original(kind: str, identity: UUID, role: str) -> dict[str, Any]:
            matching = [r for r in refs if r.kind == kind and r.id == identity and r.role == role]
            if len(matching) != 1:
                raise AuditContractError("Category event lacks unique original transition sources")
            original = index.get((kind, identity, matching[0].snapshot_hash))
            if original is None:
                raise AuditContractError("Category event lacks its frozen original")
            return original.data

        try:
            category_before = category_original("TRANSACTION", event.aggregate_id, "BEFORE")
            category_after = category_original("TRANSACTION", event.aggregate_id, "AFTER")
            declaration_id = UUID(event.payload.context.details["category_evidence_id"])
            bank_id = UUID(category_before["evidence_id"])
            declaration = category_original("EVIDENCE", declaration_id, "AFTER")
            bank = category_original("EVIDENCE", bank_id, "BASIS")
            if (
                len(refs) != 4
                or event.payload.context.reason_code != "USER_CLASSIFICATION_CONFIRMED"
                or event.payload.context.cause_ref != str(declaration_id)
                or event.payload.context.details.get("command_hash")
                != declaration["content"].get("command_hash")
            ):
                raise ValueError("Category event differs from original explicit command")
            verify_category_transition(
                user_id=event.user_id,
                epoch_id=event.epoch_id,
                transaction_id=event.aggregate_id,
                before=category_before,
                after=category_after,
                bank=bank,
                declaration=declaration,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuditContractError(
                "Category confirmation originals do not prove the registered transition"
            ) from exc
    for change in event.payload.changes:
        before = (
            index.get((change.kind, change.id, change.before_snapshot_hash))
            if change.before_snapshot_hash
            else None
        )
        after = index.get((change.kind, change.id, change.after_snapshot_hash))
        if (
            after is None
            or not any(
                r.kind == change.kind
                and r.id == change.id
                and r.role == "AFTER"
                and r.snapshot_hash == change.after_snapshot_hash
                for r in refs
            )
            or change.field not in after.data
            or canonical_bytes({"v": after.data[change.field]}, raw=True)
            != canonical_bytes({"v": change.after}, raw=True)
        ):
            raise AuditContractError("Changed audit value differs from its actual AFTER original")
        if (
            before is None
            or not any(
                r.kind == change.kind
                and r.id == change.id
                and r.role == "BEFORE"
                and r.snapshot_hash == change.before_snapshot_hash
                for r in refs
            )
            or change.field not in before.data
            or canonical_bytes({"v": before.data[change.field]}, raw=True)
            != canonical_bytes({"v": change.before}, raw=True)
        ):
            raise AuditContractError("Changed audit value differs from its actual BEFORE original")
    details = event.payload.context.details
    for field, kind in (
        ("confirmation_evidence_id", "EVIDENCE"),
        ("reservation_run_id", "DECISION_RUN"),
    ):
        if details.get(field) is not None:
            identity = UUID(details[field])
            if (kind, identity) not in available:
                raise AuditContractError("Audit fact context lacks its actual original " + field)
            originals = [s for s in referenced.values() if s.kind == kind and s.id == identity]
            if (
                kind == "EVIDENCE"
                and details.get("effect_hash") is not None
                and not any(
                    s.data.get("content", {}).get("effect_hash") == details["effect_hash"]
                    and s.data.get("content", {}).get("action_id") == str(event.action_plan_id)
                    for s in originals
                )
            ):
                raise AuditContractError(
                    "Confirmation context differs from the actual original effect proof"
                )
    for field, status in (("resource_claim_ids", "RESERVED"), ("released_claim_ids", "RELEASED")):
        identities = details.get(field, [])
        if type(identities) is not list or len(identities) != len(set(identities)):
            raise AuditContractError("Audit resource context needs unique actual claim IDs")
        for identity in identities:
            claims = [
                s
                for s in referenced.values()
                if s.kind == "RESOURCE_CLAIM"
                and str(s.id) == identity
                and s.data.get("action_plan_id") == str(event.action_plan_id)
                and s.data.get("status") == status
            ]
            if len(claims) != 1:
                raise AuditContractError(
                    "Audit resource context differs from the actual original resolved claim"
                )
        if (
            field == "released_claim_ids"
            and identities
            and details.get("no_effect_status") not in {"ABSENT", "REJECTED"}
        ):
            raise AuditContractError("Released claims need the original confirmed no-effect reason")
    if event.payload.observation is not None and event.payload.observation.request_id is not None:
        observation = event.payload.observation
        requests = [
            s
            for s in referenced.values()
            if s.kind == "BANK_REDEMPTION" and s.id == observation.request_id
        ]
        if (
            len(requests) != 1
            or requests[0].data.get("action_plan_id") != str(observation.action_id)
            or requests[0].data.get("status") != observation.bank_status
        ):
            raise AuditContractError(
                "Recovery observation differs from the original actual bank request"
            )
    for anchor in event.payload.anchors:
        original = referenced.get((anchor.reference_id, anchor.snapshot_hash))
        if original is None:
            raise AuditContractError("Audit anchor has no matching exact original reference")
        if anchor.kind == "DECISION_TRACE" and anchor.hash_algorithm == "decision-trace-v1":
            if original.kind != "DECISION_RUN":
                raise AuditContractError("Trace anchor must refer to an original decision run")
            snapshot = original.data.get("input_snapshot", {})
            raw_trace = snapshot.get("decision_trace")
            if type(raw_trace) is not dict:
                raise AuditContractError("Recorded decision lacks its original trace envelope")
            if raw_trace.get("schema_version") != "decision-trace-v1":
                raise AuditUnsupportedVersion("Original decision trace schema is unsupported")
            trace = DecisionTrace.model_validate_json(json.dumps(raw_trace, allow_nan=False))
            verify_trace(trace)
            if "full_joint_goal_execution" in trace.algorithm_versions:
                from app.domain.full_joint_goal_execution_trace import (
                    verify_frozen_full_joint_goal_trace,
                )

                verify_frozen_full_joint_goal_trace(trace)
            if "full_experiment_asset_execution" in trace.algorithm_versions:
                from app.domain.full_experiment_asset_execution_trace import (
                    verify_frozen_full_experiment_asset_trace,
                )

                verify_frozen_full_experiment_asset_trace(trace)
            if (
                trace.algorithm_versions.get("full_maturity_execution")
                == "full-maturity-user-execution-v1"
            ):
                from app.domain.full_maturity_execution import verify_frozen_maturity_trace

                verify_frozen_maturity_trace(trace)
            if "seasonal_adoption" in trace.algorithm_versions:
                from app.domain.full_seasonal_adoption import verify_frozen_seasonal_adoption_trace

                verify_frozen_seasonal_adoption_trace(trace)
            if "full_recovery_execution" in trace.algorithm_versions:
                from app.domain.full_recovery_execution_trace import (
                    verify_frozen_full_recovery_trace,
                )

                verify_frozen_full_recovery_trace(trace)
            if (
                trace.algorithm_versions.get("global_action_set")
                == "full-policy-action-set-boundary-v1"
            ):
                from app.services.full_action_set_boundary import verify_frozen_action_set_trace

                verify_frozen_action_set_trace(trace)
            elif (
                trace.algorithm_versions.get("global_action_set")
                == "full-policy-action-set-boundary-full-v1"
            ):
                from app.services.full_action_set_boundary_full import (
                    verify_frozen_full_action_set_trace,
                )

                verify_frozen_full_action_set_trace(trace)
            elif (
                trace.algorithm_versions.get("global_action_set")
                == "full-policy-action-set-boundary-actual-v2"
            ):
                from app.services.full_action_set_boundary_actual import (
                    verify_frozen_actual_action_set_trace,
                )

                verify_frozen_actual_action_set_trace(trace)
            elif (
                trace.algorithm_versions.get("global_action_set")
                == "full-policy-action-set-boundary-recovery-composed-v4"
            ):
                from app.services.full_action_set_recovery_observations import (
                    verify_frozen_recovery_composed_observation,
                )

                verify_frozen_recovery_composed_observation(trace)
            if (
                trace.run_id != original.id
                or trace.user_id != event.user_id
                or trace.trace_hash != anchor.digest
                or configuration_hash(snapshot) != original.data.get("snapshot_hash")
            ):
                raise AuditContractError(
                    "Audit decision anchor differs from the original frozen trace"
                )
            if (
                trace.as_of != _clock(original.data["as_of"])
                or trace.parent_run_id
                != (
                    UUID(original.data["parent_run_id"])
                    if original.data.get("parent_run_id")
                    else None
                )
                or trace.action_id
                != (
                    UUID(original.data["subject_action_plan_id"])
                    if original.data.get("subject_action_plan_id")
                    else None
                )
            ):
                raise AuditContractError(
                    "Audit trace identity differs from the original decision row"
                )
            if event.event_type == "DECISION_RECORDED" and (
                trace.run_id != event.decision_run_id
                or trace.action_id != event.action_plan_id
                or trace.as_of != event.occurred_at
            ):
                raise AuditContractError(
                    "Recorded decision fact differs from its original trace identity/clock"
                )
            missing = _missing_references(trace.inputs)
            if missing != set(event.payload.missing_evidence_ids):
                raise AuditContractError(
                    "Missing source claims differ from the original frozen decision"
                )
            _immutable(
                original,
                current,
                (
                    "input_snapshot",
                    "snapshot_hash",
                    "as_of",
                    "parent_run_id",
                    "subject_action_plan_id",
                    "trigger_type",
                    "algorithm_version",
                    "policy_version_ids",
                    "evidence_ids",
                ),
            )
            if not _trace_algorithms_supported(trace.algorithm_versions):
                raise AuditUnsupportedVersion("Original decision trace algorithm is unsupported")
        elif (
            anchor.kind == "EVIDENCE_CONTENT" and anchor.hash_algorithm == "configuration-sha256-v1"
        ):
            if original.kind != "EVIDENCE" or anchor.digest != original.data.get("content_hash"):
                raise AuditContractError(
                    "Audit evidence anchor differs from its original declared hash"
                )
            # Invalid original declarations are legal frozen BLOCKED provenance.
            # The subject digest binds the actual raw copy; this is not financial validity.
            _immutable(
                original,
                current,
                (
                    "content",
                    "content_hash",
                    "evidence_level",
                    "source_type",
                    "source_ref",
                    "observed_at",
                    "valid_from",
                    "valid_to",
                    "supersedes_id",
                ),
            )
        elif (
            anchor.kind in {"POLICY_CONFIGURATION", "EXECUTION_REQUEST"}
            and anchor.hash_algorithm == "configuration-sha256-v1"
        ):
            kind, field, digest_field = (
                ("POLICY_VERSION", "configuration", "content_hash")
                if anchor.kind == "POLICY_CONFIGURATION"
                else ("ACTION_PLAN", "request", "request_hash")
            )
            content = original.data.get(field)
            if (
                original.kind != kind
                or type(content) is not dict
                or configuration_hash(content) != anchor.digest
                or original.data.get(digest_field) != anchor.digest
            ):
                raise AuditContractError(
                    "Audit configuration anchor differs from its full original content"
                )
            _immutable(original, current, (field, digest_field))
            if original.kind == "ACTION_PLAN":
                _immutable(
                    original,
                    current,
                    (
                        "decision_run_id",
                        "action_type",
                        "amount_cents",
                        "source_account_id",
                        "destination_account_id",
                        "goal_id",
                        "product_id",
                        "policy_version_id",
                        "idempotency_key",
                        "expires_at",
                        "created_at",
                    ),
                )
        elif anchor.kind == "ACTION_RECEIPT" and anchor.hash_algorithm == "configuration-sha256-v1":
            if original.kind != "ACTION_RECEIPT" or receipt_digest(original.data) != anchor.digest:
                raise AuditContractError("Audit receipt anchor differs from its original full row")
        elif (
            anchor.kind == "BANK_POSTING_SET" and anchor.hash_algorithm == "configuration-sha256-v1"
        ):
            if original.kind != "BANK_OPERATION":
                raise AuditContractError(
                    "Audit posting set anchor must bind its actual bank operation"
                )
            rows = [
                s.data
                for s in referenced.values()
                if s.kind == "BANK_POSTING"
                and s.data.get("operation_id") == str(original.id)
                and s.data.get("entry_kind") != "OPENING"
            ]
            if posting_set_digest(rows) != anchor.digest:
                raise AuditContractError(
                    "Audit posting set anchor differs from the complete frozen set"
                )
            actions = [
                s
                for s in referenced.values()
                if s.kind == "ACTION_PLAN"
                and str(s.id) == original.data.get("action_plan_id")
                and any(
                    r.id == s.id and r.snapshot_hash == subject_hash(s) and r.role != "BEFORE"
                    for r in refs
                )
            ]
            if len(actions) != 1:
                raise AuditContractError("Audit settlement lacks its unique actual original action")
            redemptions = [
                s.data
                for s in referenced.values()
                if s.kind == "BANK_REDEMPTION" and s.id == original.id
            ]
            verify_frozen_settlement(
                original.data,
                actions[0].data,
                rows,
                observed_at=event.observed_at,
                redemption=redemptions[0] if redemptions else None,
            )
        else:
            raise AuditUnsupportedVersion(
                "Unsupported audit original anchor kind or hash algorithm"
            )
    if event.event_type == "DECISION_RECORDED" and not any(
        a.kind == "DECISION_TRACE" and a.reference_id == event.decision_run_id
        for a in event.payload.anchors
    ):
        raise AuditContractError("Recorded decision needs its exact original trace anchor")
    if event.event_type == "ACTION_PROJECTED":
        receipts = [
            s
            for s in referenced.values()
            if s.kind == "ACTION_RECEIPT" and s.id == event.action_receipt_id
        ]
        actions = [
            s
            for s in referenced.values()
            if s.kind == "ACTION_PLAN"
            and s.id == event.action_plan_id
            and any(
                r.id == s.id and r.snapshot_hash == subject_hash(s) and r.role != "BEFORE"
                for r in refs
            )
        ]
        banks = [
            s
            for s in referenced.values()
            if s.kind == "BANK_OPERATION"
            and s.data.get("action_plan_id") == str(event.action_plan_id)
        ]
        if len(receipts) != 1 or len(actions) != 1 or len(banks) != 1:
            raise AuditContractError(
                "Audit projection lacks its unique original receipt/action/bank relation"
            )
        postings = [
            s.data
            for s in referenced.values()
            if s.kind == "BANK_POSTING" and s.data.get("operation_id") == str(banks[0].id)
        ]
        redemptions = [
            s.data
            for s in referenced.values()
            if s.kind == "BANK_REDEMPTION" and s.id == banks[0].id
        ]
        verify_frozen_projection(
            banks[0].data,
            actions[0].data,
            receipts[0].data,
            postings,
            observed_at=event.observed_at,
            redemption=redemptions[0] if redemptions else None,
            subjects=referenced.values(),
        )


def verify_epoch(
    events: Iterable[Any],
    *,
    head: "AuditHead | None",
    expected_user_id: UUID,
    references: "ReferenceBundle",
    checkpoint: "AuditCheckpoint | None" = None,
    checkpoint_mode: Literal["PREFIX", "EXACT"] = "PREFIX",
    event_limit: int = 100000,
    byte_limit: int = 512 * 1024 * 1024,
) -> "AuditVerification":
    """Check a complete stable epoch against an independent expected head, without writes."""
    from app.domain.audit_chain_types import (
        AuditDiagnostic,
        AuditEvent,
        AuditVerification,
        Status,
    )

    if (
        checkpoint_mode not in {"PREFIX", "EXACT"}
        or type(event_limit) is not int
        or not 1 <= event_limit <= 100000
        or type(byte_limit) is not int
        or not 1 <= byte_limit <= 512 * 1024 * 1024
    ):
        raise AuditContractError("Audit verification options are outside bounded contract")
    errors = []
    chain_status: Status = "VALID"
    reference_status: Status = "VALID"
    checkpoint_status: Literal["VERIFIED", "NOT_REQUESTED", "MISMATCH", "UNAVAILABLE"] = (
        "NOT_REQUESTED" if checkpoint is None else "VERIFIED"
    )
    actual_count = 0
    tail: AuditEvent | None = None
    first: AuditEvent | None = None
    original_first: tuple[UUID, str, int] | None = None
    original_tail: tuple[UUID, str, int] | None = None
    previous = None
    seen: dict[UUID, AuditEvent] = {}
    unsupported_causes: set[UUID] = set()
    seen_keys: set[str] = set()
    seen_facts: set[tuple[str, str]] = set()
    verified_through = 0
    total_bytes = 0
    incomplete = False
    priorities = {
        "VALID": 0,
        "LEGACY_UNAUDITED": 1,
        "INCOMPLETE": 2,
        "UNSUPPORTED_VERSION": 3,
        "INTEGRITY_ERROR": 4,
    }

    def error(
        code: str,
        message: str,
        event: AuditEvent | None = None,
        *,
        status: Status = "INTEGRITY_ERROR",
        reference: bool = False,
    ) -> None:
        nonlocal chain_status, reference_status
        if reference:
            if priorities[status] > priorities[reference_status]:
                reference_status = status
        elif priorities[status] > priorities[chain_status]:
            chain_status = status
        errors.append(
            AuditDiagnostic(
                code=code,
                message=message[:1000],
                sequence_number=event.sequence_number if event else None,
                event_id=event.id if event else None,
            )
        )

    if head is None:
        return AuditVerification(
            user_id=expected_user_id,
            epoch_id=None,
            status="LEGACY_UNAUDITED",
            chain_status="LEGACY_UNAUDITED",
            reference_status="LEGACY_UNAUDITED",
            checkpoint_status="UNAVAILABLE" if checkpoint else "NOT_REQUESTED",
            actual_count=0,
            expected_count=0,
            actual_tail_id=None,
            actual_tail_hash=None,
            expected_tail_id=None,
            expected_tail_hash=None,
            verified_through_sequence=0,
            errors=[
                AuditDiagnostic(
                    code="HEAD_MISSING", message="No registered original audit epoch exists"
                )
            ],
        )
    try:
        _head(head)
        if head.user_id != expected_user_id:
            raise AuditContractError("Audit expected head belongs to another tenant")
        if len(references.subjects) > 100000 or len(references.current_subjects) > 100000:
            raise AuditBudgetExceeded("Audit subject count budget exceeded")
        for original in references.subjects + references.current_subjects:
            total_bytes += len(canonical_bytes(original.model_dump(mode="python"), raw=True))
            if total_bytes > byte_limit:
                raise AuditBudgetExceeded("Audit original subject byte budget exceeded")
        index = _subject_index(references, expected_user_id, head.epoch_id)
        current = _current_index(references, expected_user_id, head.epoch_id)
    except AuditBudgetExceeded as failure:
        error("LIMIT_EXCEEDED", str(failure), status="INCOMPLETE", reference=True)
        incomplete = True
        index, current = {}, {}
    except AuditUnsupportedVersion as failure:
        error("UNSUPPORTED_VERSION", str(failure), status="UNSUPPORTED_VERSION", reference=True)
        index, current = {}, {}
    except (ValueError, TypeError) as failure:
        error("HEAD_OR_REFERENCE_INVALID", str(failure))
        index = {}
        current = {}
    for issue in references.original_errors:
        errors.append(issue)
        reference_status = "INTEGRITY_ERROR"
    if checkpoint is not None:
        try:
            verify_checkpoint(checkpoint)
            if checkpoint.user_id != expected_user_id:
                raise AuditContractError("Audit checkpoint belongs to another tenant")
            if checkpoint.epoch_id != head.epoch_id:
                error(
                    "CHECKPOINT_EPOCH_UNAVAILABLE",
                    "Original checkpoint epoch was not supplied",
                    status="INCOMPLETE",
                )
                checkpoint_status = "UNAVAILABLE"
            elif (
                checkpoint.genesis_event_id != head.genesis_event_id
                or checkpoint.genesis_event_hash != head.genesis_event_hash
                or checkpoint.epoch_number != head.epoch_number
                or checkpoint.previous_seal_hash != head.previous_seal_hash
            ):
                raise AuditContractError("Audit checkpoint genesis or predecessor differs")
        except (ValueError, TypeError) as failure:
            error("CHECKPOINT_MISMATCH", str(failure))
            checkpoint_status = "MISMATCH"
    for raw in () if incomplete else events:
        if actual_count >= event_limit:
            incomplete = True
            error("LIMIT_EXCEEDED", "Audit event budget exceeded", status="INCOMPLETE")
            break
        actual_count += 1
        event = None
        known_header = False
        try:
            value = (
                raw.model_dump(mode="python")
                if isinstance(raw, AuditEvent)
                else _read(raw)
                if isinstance(raw, str)
                else raw
            )
            if not isinstance(value, dict):
                raise AuditContractError("Audit original event is not an object")
            total_bytes += (
                len(raw.encode("utf-8"))
                if isinstance(raw, str)
                else len(canonical_bytes(value, raw=True))
            )
            if total_bytes > byte_limit:
                incomplete = True
                error("LIMIT_EXCEEDED", "Audit byte budget exceeded", status="INCOMPLETE")
                break
            sequence = value.get("sequence_number")
            digest = value.get("event_hash")
            if (
                type(sequence) is not int
                or not 1 <= sequence <= 2**31 - 1
                or type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise AuditContractError("Audit original row header is invalid")
            original_tail = (UUID(str(value["id"])), digest, sequence)
            if original_first is None:
                original_first = original_tail
            if (
                UUID(str(value["user_id"])) != expected_user_id
                or UUID(str(value["epoch_id"])) != head.epoch_id
            ):
                raise AuditContractError(
                    "Audit original row header belongs to another tenant or epoch"
                )
            if (
                value.get("schema_version") != "audit-event-v1"
                or value.get("canonical_version") != "audit-canonical-json-v1"
            ):
                raise AuditUnsupportedVersion("Audit original protocol is unsupported")
            _known_event_integrity(value)
            if sequence != actual_count or value.get("previous_hash") != previous:
                raise AuditContractError("Audit sequence or preceding content hash differs")
            known_header = True
            event = (
                raw
                if isinstance(raw, AuditEvent)
                else _event_model(value, envelope=True).model_validate_json(
                    json.dumps(value, allow_nan=False)
                )
            )
            tail = event
            if first is None:
                first = event
            verify_event(event)
            if isinstance(raw, str) and event_canonical_text(event) != raw:
                raise AuditContractError("Audit original event text is not canonical")
            if event.user_id != expected_user_id or event.epoch_id != head.epoch_id:
                raise AuditContractError("Audit event belongs to another tenant or epoch")
            if event.sequence_number != actual_count or event.previous_hash != previous:
                raise AuditContractError("Audit sequence or preceding content hash differs")
            if actual_count == 1 and event.event_type != "EPOCH_STARTED":
                raise AuditContractError("Audit epoch has no actual first genesis event")
            if actual_count == 1:
                transition = cast("AuditEnvelope", event).payload.epoch_transition
                assert transition is not None
                if (
                    (transition.kind == "INIT") != (head.epoch_number == 1)
                    or transition.previous_epoch_id != head.previous_epoch_id
                    or transition.previous_seal_hash != head.previous_seal_hash
                ):
                    raise AuditContractError(
                        "Actual epoch genesis differs from its expected predecessor seal"
                    )
                if transition.legacy_history and reference_status == "VALID":
                    reference_status = "LEGACY_UNAUDITED"
            if event.event_type == "EPOCH_SEALED":
                transition = cast("AuditEnvelope", event).payload.epoch_transition
                assert transition is not None and transition.pre_seal_head is not None
                if (
                    head.status != "SEALED"
                    or actual_count != head.event_count
                    or transition.pre_seal_head.genesis_event_id != head.genesis_event_id
                    or transition.pre_seal_head.genesis_event_hash != head.genesis_event_hash
                    or transition.pre_seal_head.epoch_number != head.epoch_number
                ):
                    raise AuditContractError(
                        "Actual terminal epoch seal differs from its final SEALED head"
                    )
                manifest = archive_manifest(references.subjects)
                if (
                    archive_manifest_digest(references.subjects) != transition.archive_manifest_hash
                    or manifest["counts"] != transition.archive_record_counts
                ):
                    raise AuditContractError(
                        "Actual archived originals differ from the terminal epoch manifest"
                    )
                verify_frozen_ledgers(s.data for s in index.values() if s.kind == "BANK_POSTING")
            if (
                event.id in seen
                or event.idempotency_key in seen_keys
                or (event.event_type, event.payload.fact_key) in seen_facts
            ):
                raise AuditContractError("Audit event identity, key or fact is duplicated")
            if event.causation_id is not None:
                cause = seen.get(event.causation_id)
                if cause is None and event.causation_id in unsupported_causes:
                    raise AuditUnsupportedVersion(
                        "Audit cause uses an unsupported original payload"
                    )
                if (
                    cause is None
                    or cause.correlation_id != event.correlation_id
                    or cause.payload.correlation_kind != event.payload.correlation_kind
                    or (
                        event.action_plan_id is not None
                        and cause.action_plan_id is not None
                        and event.action_plan_id != cause.action_plan_id
                    )
                ):
                    raise AuditContractError(
                        "Audit cause is not an earlier matching original business event"
                    )
                if event.event_type == "EXTERNAL_BANK_FACT_PROJECTED" and (
                    cause.event_type != "EXTERNAL_BANK_FACT_SETTLED"
                    or cause.epoch_id != event.epoch_id
                    or cause.aggregate_id != event.aggregate_id
                ):
                    raise AuditContractError("External projection cause is not its settled fact")
            try:
                _references(event, index, current)
            except AuditUnsupportedVersion as failure:
                error(
                    "UNSUPPORTED_VERSION",
                    str(failure),
                    event,
                    status="UNSUPPORTED_VERSION",
                    reference=True,
                )
            except (ValueError, TypeError, KeyError) as failure:
                error("REFERENCE_INVALID", str(failure), event, reference=True)
            if (
                event.payload_version == 1
                and cast("AuditEnvelope", event).payload.legacy_origin is not None
            ):
                reference_status = (
                    "LEGACY_UNAUDITED" if reference_status == "VALID" else reference_status
                )
            if (
                checkpoint is not None
                and checkpoint_status == "VERIFIED"
                and event.sequence_number == checkpoint.last_sequence
            ):
                if event.id != checkpoint.tail_id or event.event_hash != checkpoint.tail_hash:
                    checkpoint_status = "MISMATCH"
                    raise AuditContractError("Audit checkpoint original prefix differs")
            verified_through = actual_count if not errors else verified_through
        except AuditUnsupportedVersion as failure:
            error("UNSUPPORTED_VERSION", str(failure), event, status="UNSUPPORTED_VERSION")
            if known_header:
                unsupported_causes.add(UUID(str(value["id"])))
        except (ValueError, TypeError, KeyError) as failure:
            error("EVENT_OR_REFERENCE_INVALID", str(failure), event)
        if event is not None:
            seen[event.id] = event
            seen_keys.add(event.idempotency_key)
            seen_facts.add((event.event_type, event.payload.fact_key))
            previous = event.event_hash
        elif known_header:
            previous = digest
    if not incomplete and (
        actual_count != head.event_count
        or original_tail != (head.last_event_id, head.last_event_hash, head.last_sequence)
        or original_first is None
        or original_first[:2] != (head.genesis_event_id, head.genesis_event_hash)
    ):
        error(
            "HEAD_MISMATCH",
            "Actual complete count/genesis/tail differs from independent expected head",
        )
    if (
        not incomplete
        and head.status == "SEALED"
        and (tail is None or tail.event_type != "EPOCH_SEALED")
    ):
        error("SEAL_MISSING", "A sealed epoch lacks its actual terminal sealing event")
    if checkpoint is not None and checkpoint_status == "VERIFIED":
        if incomplete and (
            checkpoint_mode == "EXACT" or checkpoint.last_sequence > verified_through
        ):
            checkpoint_status = "UNAVAILABLE"
            error(
                "CHECKPOINT_SCOPE_INCOMPLETE",
                "The bounded original history did not include the checkpoint scope",
                status="INCOMPLETE",
            )
        elif checkpoint.last_sequence > actual_count or (
            checkpoint_mode == "EXACT"
            and (
                checkpoint.expected_count != head.event_count
                or checkpoint.tail_id != head.last_event_id
                or checkpoint.tail_hash != head.last_event_hash
            )
        ):
            error("CHECKPOINT_MISMATCH", "Actual history differs from requested checkpoint scope")
            checkpoint_status = "MISMATCH"
    status = max((chain_status, reference_status), key=priorities.__getitem__)
    errors.sort(key=lambda item: (item.sequence_number or 0, item.code, item.reference or ""))
    return AuditVerification(
        user_id=expected_user_id,
        epoch_id=head.epoch_id,
        status=status,
        chain_status=chain_status,
        reference_status=reference_status,
        checkpoint_status=checkpoint_status,
        actual_count=actual_count,
        expected_count=head.event_count,
        actual_tail_id=original_tail[0] if original_tail else None,
        actual_tail_hash=original_tail[1] if original_tail else None,
        expected_tail_id=head.last_event_id,
        expected_tail_hash=head.last_event_hash,
        verified_through_sequence=verified_through,
        errors=errors[:100],
        errors_truncated=len(errors) > 100,
    )
