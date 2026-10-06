"""Independent MVP protection math from bound originals; never evaluate P code.

This stdlib-only calculator has no execution authority. The outer experiment
collector must establish frozen run/source/epoch provenance. Unsupported or
incomplete inputs remain MISSING, including every unobserved checkpoint.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta, timezone
from fractions import Fraction
from typing import Any, cast
from uuid import UUID, uuid5

PROTOCOL = "mvp-independent-financial-timeline-v1"
FACT_PROTOCOL = "mvp-financial-facts-v1"
TABLES = (
    "accounts",
    "evidence_items",
    "policies",
    "policy_versions",
    "goals",
    "transactions",
    "credit_card_bills",
    "asset_positions",
    "asset_products",
    "bank_operations",
    "action_plans",
    "action_receipts",
    "simulated_bank_postings",
    "action_resource_reservations",
)
MAX_INT = 2**63 - 1
MAX_ROWS = 100_000
SUPPORTED_TIMEZONES = {"UTC": UTC, "Asia/Shanghai": timezone(timedelta(hours=8))}
CONFIG_FIELDS = {
    "emergency_buffer": {"amount_cents"},
    "recurring_obligation": {
        "payee_id",
        "amount_rule",
        "due_day",
        "prepare_days_before",
        "auto_execute",
        "priority",
    },
    "living_reserve": {
        "horizon_days",
        "method",
        "extra_buffer_cents",
        "reconfirm_on_boundary_crossing",
    },
    "goal_saving": {
        "target_cents",
        "deadline",
        "monthly_contribution",
        "priority",
        "cross_goal_reallocation_allowed",
        "asset_policy_id",
    },
    "asset_authorization": {
        "scope",
        "goal_id",
        "allowed_asset_classes",
        "max_auto_managed_cents",
        "single_action_cap_cents",
        "max_redemption_delay_days",
        "max_lock_days",
        "max_principal_risk_level",
        "allow_auto_recovery_without_penalty",
        "allow_early_withdrawal_with_penalty",
    },
}


class Missing(ValueError):
    """No precise supported measurement can be established from these originals."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise Missing(reason)


def _obj(value: Any, name: str) -> dict[str, Any]:
    _need(type(value) is dict, f"Missing or non-object {name}")
    return cast(dict[str, Any], value)


def _list(value: Any, name: str) -> list[Any]:
    _need(type(value) is list and len(value) <= MAX_ROWS, f"Missing or over-capacity {name}")
    return cast(list[Any], value)


def _text(value: Any, name: str) -> str:
    _need(type(value) is str and 0 < len(value) <= 512, f"Missing or invalid {name}")
    return cast(str, value)


def _uuid(value: Any) -> str:
    original = _text(value, "canonical UUID")
    _need(str(UUID(original)) == original, "UUID is not its canonical original string")
    return original


def _lookup(index: dict[str, dict[str, Any]], identity: Any, name: str) -> dict[str, Any]:
    row = index.get(_text(identity, name))
    _need(row is not None, f"Missing original {name}")
    return cast(dict[str, Any], row)


def _money(value: Any, name: str, *, signed: bool = False) -> int:
    _need(
        type(value) is int and (-MAX_INT - 1 if signed else 0) <= value <= MAX_INT,
        f"{name} must be a strict signed64 integer in its permitted range",
    )
    return cast(int, value)


def _sum(values: Any) -> int:
    return _money(sum(values), "integer sum", signed=True)


def _at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_text(value, "aware original timestamp"))
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None, "Timestamp lacks timezone")
    return parsed.astimezone(UTC)


def _day(value: Any) -> date:
    parsed = date.fromisoformat(_text(value, "calendar date"))
    _need(parsed.isoformat() == value, "Calendar date is not canonical")
    return parsed


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def value_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(value: Any) -> str:
    _need(
        type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None,
        "Invalid original SHA256",
    )
    return cast(str, value)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        _need(key not in result, "Duplicate key in original artifact JSON")
        result[key] = value
    return result


def _nonfinite(value: str) -> Any:
    raise Missing(f"Non-finite original JSON number {value}")


def _original_scalars(value: Any, key: str = "", depth: int = 0) -> None:
    _need(depth <= 32, "Original JSON nesting exceeds supported depth")
    if type(value) is dict:
        for name, item in value.items():
            _original_scalars(item, name, depth + 1)
    elif type(value) is list:
        for item in value:
            _original_scalars(item, "", depth + 1)
    elif value is not None:
        if key.endswith("_cents"):
            _money(value, f"original {key}", signed=True)
        elif key.endswith("_at") or key == "as_of":
            _at(value)
        elif key in {"valid_from", "valid_until", "valid_to", "effective_from", "effective_until"}:
            if type(value) is str and len(value) == 10:
                _day(value)
            else:
                _at(value)
        elif type(value) is float:
            _need(math.isfinite(value), "Non-finite original scalar")


class Facts:
    """Original byte/index checks, independent of policy protection calculations."""

    def __init__(self, value: dict[str, Any]):
        self.value = _obj(value, "financial facts")
        _need(
            value.get("protocol") in {FACT_PROTOCOL, "mvp-financial-facts-v2"}
            and value.get("complete") is True,
            "Incomplete facts protocol",
        )
        self.original_byte_budget = (
            (512 if value["protocol"] == "mvp-financial-facts-v2" else 64) * 1024 * 1024
        )
        self.owner = _uuid(value.get("user_id"))
        self.as_of = _at(value.get("as_of"))
        zone = _text(value.get("timezone"), "timezone")
        _need(zone in SUPPORTED_TIMEZONES, "Unsupported local calendar timezone")
        self.zone = SUPPORTED_TIMEZONES[zone]
        self.tables = _obj(value.get("tables"), "complete original tables")
        inventory = _obj(value.get("inventory"), "complete original inventory")
        _need(
            set(self.tables) == set(TABLES) == set(inventory),
            "Exact fourteen original tables required",
        )
        self.originals = _obj(value.get("artifact_originals"), "original artifact bytes")
        self.parsed: dict[str, Any] = {}
        self.refs: list[dict[str, Any]] = []
        self.index: dict[str, dict[str, dict[str, Any]]] = {}
        for table in TABLES:
            rows = [_obj(row, table) for row in _list(self.tables[table], table)]
            indexed = {_uuid(row.get("id")): row for row in rows}
            _need(len(indexed) == len(rows), f"Duplicate original identity in {table}")
            for row in rows:
                _original_scalars(row)
                if table != "asset_products":
                    _need(_uuid(row.get("user_id")) == self.owner, f"Foreign owner in {table}")
                for key, field in row.items():
                    if key.endswith("_cents") and field is not None:
                        _money(field, f"{table}.{key}", signed=key == "delta_cents")
            ordered = sorted(rows, key=lambda row: row["id"])
            declaration = _obj(inventory[table], f"{table} original inventory")
            _need(
                set(declaration) == {"row_ids", "sha256", "source_ref"}, "Inventory shape differs"
            )
            _need(
                declaration["row_ids"] == list(sorted(indexed)),
                f"Incomplete inventory IDs for {table}",
            )
            _need(
                _sha(declaration["sha256"]) == value_sha256(ordered),
                f"Inventory hash differs for {table}",
            )
            original = self.source(declaration["source_ref"])
            original_rows = [_obj(row, table) for row in _list(original, "source table original")]
            _need(
                _canonical(sorted(original_rows, key=lambda row: row["id"])) == _canonical(ordered),
                f"{table} rows differ from the actual original artifact",
            )
            self.index[table] = indexed
        _need(len(self.index["accounts"]) <= 100, "Account capacity exceeded")
        self.events = [
            _obj(row, "policy state event")
            for row in _list(value.get("policy_state_events"), "state events")
        ]
        original_events = [
            {key: field for key, field in event.items() if key != "source_ref"}
            for event in self.events
        ]
        if value.get("policy_state_events_protocol") == "TYPED_AUDIT_POLICY_EVENTS_V1":
            _need(
                value["protocol"] == "mvp-financial-facts-v2",
                "Typed state originals need explicit facts V2",
            )
            self.events = self._typed_policy_events()
        else:
            self._minimal_policy_events(original_events)
        self.evidence = self.index["evidence_items"]
        for row in self.evidence.values():
            _need(
                _sha(row.get("content_hash")) == value_sha256(row.get("content")),
                "Evidence original hash differs",
            )
            _need(
                _at(row.get("observed_at")) <= self.as_of, "Evidence not observed at snapshot time"
            )
            valid_from = _at(row.get("valid_from"))
            if row.get("valid_to") is not None:
                _need(_at(row["valid_to"]) > valid_from, "Invalid evidence validity interval")
        for row in self.index["policy_versions"].values():
            _need(
                _sha(row.get("content_hash")) == value_sha256(row.get("configuration")),
                "Original policy configuration hash differs",
            )

    def _minimal_policy_events(self, original_events: list[dict[str, Any]]) -> None:
        _need(
            _canonical(self.source(self.value.get("policy_state_events_source_ref")))
            == _canonical(original_events),
            "Complete state-event array differs from actual original",
        )
        for event in self.events:
            _need(
                set(event)
                == {
                    "policy_id",
                    "version_id",
                    "occurred_at",
                    "from_status",
                    "to_status",
                    "source_ref",
                },
                "Unsupported state event representation; typed AuditEvent mapping unavailable",
            )
            _need(_uuid(event["policy_id"]) in self.index["policies"], "Unknown state-event policy")
            _need(
                _uuid(event["version_id"]) in self.index["policy_versions"],
                "Unknown state-event version",
            )
            _need(
                self.index["policy_versions"][event["version_id"]].get("policy_id")
                == event["policy_id"],
                "State event version belongs to another policy",
            )
            _need(_at(event["occurred_at"]) <= self.as_of, "Future state-event original")
            original = {key: field for key, field in event.items() if key != "source_ref"}
            _need(
                _canonical(self.source(event["source_ref"])) == _canonical(original),
                "State event source differs",
            )

    def _typed_policy_events(self) -> list[dict[str, Any]]:
        _need(
            _canonical(self.source(self.value.get("policy_state_events_source_ref")))
            == _canonical(self.events),
            "Complete typed audit-event array differs from original",
        )
        subjects = _list(self.value.get("policy_subjects"), "complete policy audit subjects")
        _need(
            _canonical(self.source(self.value.get("policy_subjects_source_ref")))
            == _canonical(subjects),
            "Complete typed subject array differs from original",
        )
        subject_index: dict[str, dict[str, Any]] = {}
        for item in subjects:
            row = _obj(item, "typed original subject row")
            _need(_uuid(row.get("user_id")) == self.owner, "Foreign typed subject owner")
            text = row.get("canonical_text")
            _need(
                type(text) is str and 0 < len(text.encode()) <= 16 * 1024 * 1024,
                "Original subject text exceeds its explicit budget",
            )
            raw = cast(str, text).encode()
            subject = _obj(
                json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite),
                "typed subject",
            )
            _need(_canonical(subject) == raw, "Subject text is not original canonical JSON")
            _need(
                hashlib.sha256(b"bounded-funds/audit-subject-v1\0" + raw).hexdigest()
                == _sha(row.get("snapshot_hash"))
                and subject.get("schema_version") == "audit-subject-v1"
                and subject.get("canonical_version") == "audit-canonical-json-v1"
                and subject.get("simulation") is True
                and subject.get("user_id") == self.owner
                and subject.get("epoch_id") == row.get("epoch_id")
                and subject.get("kind") == row.get("kind")
                and subject.get("id") == row.get("entity_id"),
                "Typed subject header/hash differs",
            )
            key = _text(row["snapshot_hash"], "original subject hash")
            _need(key not in subject_index, "Typed subject original hash is ambiguous")
            subject_index[key] = subject
        parsed = []
        ids: set[str] = set()
        for row in self.events:
            identity = _uuid(row.get("id"))
            _need(
                identity not in ids and _uuid(row.get("user_id")) == self.owner,
                "Typed event identity/owner differs",
            )
            ids.add(identity)
            if row.get("event_type") not in {"POLICY_STATE_CHANGED", "POLICY_VERSION_CONFIRMED"}:
                continue
            text = row.get("canonical_text")
            _need(
                type(text) is str and 0 < len(text.encode()) <= 1024 * 1024,
                "Original event text exceeds its explicit budget",
            )
            raw = cast(str, text).encode()
            event = _obj(
                json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite), "typed event"
            )
            _need(_canonical(event) == raw, "Event text is not original canonical JSON")
            content = {key: field for key, field in event.items() if key != "event_hash"}
            _need(
                hashlib.sha256(b"bounded-funds/audit-event-v1\0" + _canonical(content)).hexdigest()
                == event.get("event_hash")
                == row.get("event_hash")
                and event.get("schema_version") == "audit-event-v1"
                and event.get("canonical_version") == "audit-canonical-json-v1"
                and event.get("simulation") is True,
                "Typed event hash/protocol differs",
            )
            for key in (
                "id",
                "user_id",
                "epoch_id",
                "event_type",
                "aggregate_type",
                "aggregate_id",
                "correlation_id",
                "payload",
                "sequence_number",
            ):
                _need(
                    _canonical(event.get(key)) == _canonical(row.get(key)),
                    "Typed event row/canonical fields differ",
                )
            _need(
                _at(event.get("occurred_at")) == _at(row.get("occurred_at")) <= self.as_of,
                "Typed event original clock differs",
            )
            payload = _obj(event.get("payload"), "original typed payload")
            _need(payload.get("correlation_kind") == "POLICY", "Typed policy correlation differs")
            refs = [
                _obj(ref, "original typed reference")
                for ref in _list(payload.get("references"), "typed references")
            ]

            def resolve_subject(
                kind: str,
                role: str,
                refs: list[dict[str, Any]] = refs,
                event: dict[str, Any] = event,
            ) -> dict[str, Any]:
                matching = [
                    ref for ref in refs if ref.get("kind") == kind and ref.get("role") == role
                ]
                _need(len(matching) == 1, "Unique original typed policy reference missing")
                ref = matching[0]
                original = _lookup(
                    subject_index, ref.get("snapshot_hash"), "typed original reference"
                )
                _need(
                    original.get("kind") == kind
                    and original.get("id") == ref.get("id")
                    and original.get("user_id") == ref.get("user_id") == self.owner
                    and original.get("epoch_id") == event.get("epoch_id"),
                    "Typed policy original reference is rebound",
                )
                return _obj(original.get("data"), "typed policy original data")

            after = resolve_subject("POLICY", "AFTER")
            policy_id = _uuid(after.get("id"))
            _need(
                policy_id == event.get("correlation_id") and policy_id in self.index["policies"],
                "Typed policy identity differs",
            )
            role = "BASIS" if event["event_type"] == "POLICY_STATE_CHANGED" else "AFTER"
            version = resolve_subject("POLICY_VERSION", role)
            version_id = _uuid(version.get("id"))
            _need(
                version_id in self.index["policy_versions"]
                and version.get("policy_id")
                == policy_id
                == self.index["policy_versions"][version_id].get("policy_id"),
                "Typed policy version is rebound",
            )
            result = {
                "policy_id": policy_id,
                "version_id": version_id,
                "occurred_at": row["occurred_at"],
                "to_status": after.get("status"),
                "audit_event_id": identity,
                "sequence_number": event["sequence_number"],
                "event_type": event["event_type"],
            }
            if event["event_type"] == "POLICY_STATE_CHANGED":
                before = resolve_subject("POLICY", "BEFORE")
                changes = [
                    _obj(change, "typed state change")
                    for change in _list(payload.get("changes"), "original state changes")
                ]
                _need(
                    len(changes) == 1
                    and changes[0].get("kind") == "POLICY"
                    and changes[0].get("id") == policy_id
                    and changes[0].get("field") == "status"
                    and changes[0].get("before") == before.get("status")
                    and changes[0].get("after") == after.get("status"),
                    "Typed status change does not bind original before/after",
                )
                _need(before.get("id") == policy_id, "Original before policy identity differs")
                for label, role in (("before", "BEFORE"), ("after", "AFTER")):
                    matching = [
                        ref
                        for ref in refs
                        if ref.get("kind") == "POLICY" and ref.get("role") == role
                    ]
                    _need(
                        changes[0].get(label + "_snapshot_hash")
                        == matching[0].get("snapshot_hash"),
                        "Typed status change snapshot hash differs",
                    )
                result["from_status"] = before.get("status")
            else:
                _need(
                    event.get("aggregate_type") == "POLICY_VERSION"
                    and event.get("aggregate_id") == version_id
                    and _at(version.get("confirmed_at")) == _at(row["occurred_at"]),
                    "Typed confirmation does not bind exact version clock",
                )
            parsed.append(result)
        explicit = {
            (row["version_id"], _at(row["occurred_at"]))
            for row in parsed
            if row["event_type"] == "POLICY_STATE_CHANGED"
        }
        result_events = []
        statuses: dict[str, Any] = {}
        for row in sorted(
            parsed,
            key=lambda row: (
                _at(row["occurred_at"]),
                _money(row["sequence_number"], "audit sequence"),
            ),
        ):
            if row["event_type"] == "POLICY_VERSION_CONFIRMED":
                if (row["version_id"], _at(row["occurred_at"])) in explicit:
                    continue
                # First capture has no asserted prior status. Later unchanged
                # status confirmations bind the exact new version activation.
                row["from_status"] = statuses.get(row["policy_id"])
            statuses[row["policy_id"]] = row["to_status"]
            result_events.append(row)
        return result_events

    def source(self, ref: Any) -> Any:
        descriptor = _obj(ref, "original source reference")
        _need(
            set(descriptor) == {"artifact_sha256", "json_pointer", "value_sha256"},
            "Source reference shape differs",
        )
        digest = _sha(descriptor["artifact_sha256"])
        if digest not in self.parsed:
            artifact = _obj(self.originals.get(digest), "original artifact descriptor")
            _need(
                set(artifact) == {"utf8"} and type(artifact["utf8"]) is str,
                "Original UTF8 text missing",
            )
            raw = artifact["utf8"].encode("utf-8")
            _need(
                len(raw) <= self.original_byte_budget,
                "Original artifact exceeds explicit protocol capacity",
            )
            _need(hashlib.sha256(raw).hexdigest() == digest, "Original artifact byte SHA differs")
            self.parsed[digest] = json.loads(
                raw, object_pairs_hook=_pairs, parse_constant=_nonfinite
            )
        pointer = descriptor["json_pointer"]
        _need(
            type(pointer) is str and (pointer == "" or pointer.startswith("/")),
            "Invalid source JSON pointer",
        )
        _need(re.search(r"~(?![01])", pointer) is None, "Invalid pointer escape")
        current = self.parsed[digest]
        if pointer:
            for token in pointer[1:].split("/"):
                key = token.replace("~1", "/").replace("~0", "~")
                if type(current) is list:
                    _need(
                        re.fullmatch("0|[1-9][0-9]*", key) is not None, "Noncanonical array pointer"
                    )
                    current = current[int(key)]
                else:
                    current = _obj(current, "pointer parent")[key]
        _need(
            _sha(descriptor["value_sha256"]) == value_sha256(current),
            "Source pointer value hash differs",
        )
        if descriptor not in self.refs:
            self.refs.append(descriptor)
        return current

    def valid(self, evidence: dict[str, Any], at: datetime, level: str) -> bool:
        return (
            evidence.get("status") == "VALID"
            and evidence.get("evidence_level") == level
            and _at(evidence["observed_at"]) <= at
            and _at(evidence["valid_from"]) <= at
            and (evidence.get("valid_to") is None or at < _at(evidence["valid_to"]))
        )

    def proof_record(
        self, source: str, at: datetime, selector: dict[str, Any], level: str = "BANK_CONFIRMED"
    ) -> dict[str, Any]:
        rows = [
            evidence
            for evidence in self.evidence.values()
            if evidence.get("source_type") == source
            and self.valid(evidence, at, level)
            and all(
                _canonical(_obj(evidence["content"], "evidence content").get(key))
                == _canonical(field)
                for key, field in selector.items()
            )
        ]
        _need(len(rows) == 1, f"Missing or conflicting {source} original")
        return rows[0]

    def proof(
        self, source: str, at: datetime, selector: dict[str, Any], level: str = "BANK_CONFIRMED"
    ) -> dict[str, Any]:
        return _obj(self.proof_record(source, at, selector, level)["content"], "evidence content")


def validate_facts(original_facts: dict[str, Any]) -> dict[str, Any]:
    """Validate originals only; VERIFIED does not mean a safe or authorized action."""
    try:
        facts = Facts(original_facts)
        return {"status": "VERIFIED", "missing_reason": None, "raw_refs": facts.refs}
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError) as error:
        return {"status": "MISSING", "missing_reason": str(error), "raw_refs": []}


def _cash(
    facts: Facts, at: datetime
) -> tuple[int, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    changes: list[dict[str, Any]] = []
    for row in facts.index["simulated_bank_postings"].values():
        _need(_at(row.get("occurred_at")) <= at, "Future posting cannot supply cash")
        grouped[_text(row.get("ledger_key"), "ledger identity")].append(row)
    heads: dict[str, dict[str, Any]] = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda row: _money(row.get("sequence_number"), "posting sequence"))
        prior: dict[str, Any] | None = None
        for sequence, row in enumerate(rows, 1):
            before = _money(row.get("balance_before_cents"), "posting before")
            delta = _money(row.get("delta_cents"), "posting delta", signed=True)
            after = _money(row.get("balance_after_cents"), "posting after")
            _need(
                after == before + delta and row["sequence_number"] == sequence,
                "Bank ledger conservation/sequence differs",
            )
            dimension = row.get("ledger_dimension", "ECONOMIC")
            _need(
                dimension in {"ECONOMIC", "GOAL_OWNERSHIP", "INCOME_LOCATION", "LIABILITY"},
                "Unknown ledger dimension",
            )
            if prior is None:
                _need(
                    row.get("entry_kind") == "OPENING"
                    and before == 0
                    and row.get("previous_posting_id") is None,
                    "Ledger opening missing",
                )
            else:
                _need(
                    before == prior["balance_after_cents"]
                    and row.get("previous_posting_id") == prior["id"],
                    "Bank predecessor differs",
                )
                _need(
                    _at(row["occurred_at"]) >= _at(prior["occurred_at"]), "Bank chronology differs"
                )
                _need(
                    dimension == prior.get("ledger_dimension", "ECONOMIC")
                    and row.get("ledger_metadata", {}) == prior.get("ledger_metadata", {}),
                    "Ledger semantics changed",
                )
                _need(
                    row.get("account_id") == prior.get("account_id")
                    and row.get("position_id") == prior.get("position_id"),
                    "Ledger resource identity changed",
                )
            if key.startswith("CASH:"):
                _need(
                    dimension == "ECONOMIC"
                    and row.get("account_id") == key[5:]
                    and row.get("position_id") is None,
                    "Virtual/resource leg cannot represent actual cash",
                )
            if dimension == "ECONOMIC" and row.get("account_id") is not None:
                account_id = _uuid(row["account_id"])
                _need(
                    key == f"CASH:{account_id}" and row.get("position_id") is None,
                    "Cash ledger identity differs",
                )
                _need(account_id in facts.index["accounts"], "Bank cash account original missing")
                if prior is not None:
                    channels = [
                        name
                        for name in ("operation_id", "external_fact_id")
                        if row.get(name) is not None
                    ]
                    _need(
                        len(channels) == 1,
                        "Nonopening cash leg needs one actual operation identity",
                    )
                    _uuid(row[channels[0]])
                    if row.get("redemption_id") is not None:
                        _uuid(row["redemption_id"])
                        _need(
                            channels == ["operation_id"],
                            "External cash leg cannot also represent redemption",
                        )
                    if channels[0] == "operation_id":
                        operation = facts.index["bank_operations"].get(row["operation_id"])
                        _need(
                            operation is not None and operation.get("status") == "SETTLED",
                            "Actual cash operation is not settled in original bank record",
                        )
                    changes.append(
                        {
                            "posting_id": row["id"],
                            "delta_cents": delta,
                            "occurred_at": row["occurred_at"],
                            "channel": "EXOGENOUS_EXTERNAL_FACT"
                            if row.get("external_fact_id") is not None
                            else "SYSTEM_BANK_OPERATION",
                            "source_id": row[channels[0]],
                        }
                    )
            prior = row
        heads[key] = rows[-1]
    total = 0
    for account in facts.index["accounts"].values():
        _need(
            account.get("account_type")
            in {"CASH", "GOAL", "ASSET", "CREDIT_CARD", "CASH_MANAGEMENT", "FIXED_DEPOSIT"},
            "Unknown account kind",
        )
        if account["account_type"] not in {"CASH", "GOAL"}:
            continue
        _need(account.get("currency") == "CNY", "Unsupported cash currency")
        head = heads.get(f"CASH:{account['id']}")
        _need(
            head is not None and head.get("ledger_dimension", "ECONOMIC") == "ECONOMIC",
            "Complete actual cash ledger missing",
        )
        total = _sum((total, cast(dict[str, Any], head)["balance_after_cents"]))
    _need(bool(facts.index["accounts"]), "No actual accounts")
    return total, heads, changes


def _configuration(row: dict[str, Any]) -> dict[str, Any]:
    configuration = _obj(row.get("configuration"), "policy configuration")
    kind = _text(configuration.get("type"), "configuration kind")
    _need(kind in CONFIG_FIELDS, f"Unsupported policy configuration {kind}")
    allowed = CONFIG_FIELDS[kind] | {"type", "name", "valid_from", "valid_until"}
    _need(set(configuration) <= allowed, "Unsupported policy configuration fields")
    _need(
        _sha(row.get("content_hash")) == value_sha256(configuration), "Configuration hash differs"
    )
    for key, value in configuration.items():
        if key.endswith("_cents"):
            _money(value, key)
    return configuration


def _versions(facts: Facts, at: datetime) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    by_policy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in facts.index["policy_versions"].values():
        policy_id = _uuid(row.get("policy_id"))
        _need(policy_id in facts.index["policies"], "Version policy original missing")
        if row.get("confirmed_at") is None:
            continue
        confirmed = _at(row["confirmed_at"])
        _need(confirmed <= at, "Future confirmation cannot supply protection or authority")
        config = _configuration(row)
        confirmation = _obj(row.get("confirmation"), "exact original confirmation")
        _need(
            confirmation.get("accepted") is True
            and confirmation.get("reviewed_hash") == row["content_hash"],
            "Original reviewed confirmation differs",
        )
        _need(
            confirmation.get("user_id") == facts.owner
            and confirmation.get("policy_id") == policy_id
            and confirmation.get("version_id") == row["id"],
            "Confirmation identity differs",
        )
        _need(_at(confirmation.get("confirmed_at")) == confirmed, "Confirmation timestamp differs")
        _need(
            _canonical(
                facts.proof(
                    "POLICY_CONFIRMATION", at, {"version_id": row["id"]}, "USER_CONFIRMED_POLICY"
                )
            )
            == _canonical(confirmation),
            "Original confirmation evidence differs",
        )
        start = _at(row.get("valid_from"))
        end = _at(row["valid_until"]) if row.get("valid_until") is not None else None
        _need(start == _at(confirmation.get("effective_from")), "Effective start differs")
        _need(
            end
            == (
                _at(confirmation["effective_until"])
                if confirmation.get("effective_until") is not None
                else None
            ),
            "Effective end differs",
        )
        _need(end is None or end > start, "Empty effective interval")
        if config.get("valid_from") is not None:
            _need(
                start
                == datetime.combine(_day(config["valid_from"]), time.min, facts.zone).astimezone(
                    UTC
                ),
                "Declared local start differs",
            )
        else:
            _need(start == confirmed, "Default effective start must be original confirmation")
        if config.get("valid_until") is not None:
            expected = datetime.combine(
                _day(config["valid_until"]) + timedelta(days=1), time.min, facts.zone
            ).astimezone(UTC)
            _need(end == expected, "Declared local exclusive end differs")
        item: dict[str, Any] = {
            "row": row,
            "config": config,
            "start": max(start, confirmed),
            "end": end,
            "confirmed": confirmed,
        }
        by_policy[policy_id].append(item)
    for policy_id, items in by_policy.items():
        items.sort(key=lambda item: _money(item["row"].get("version_number"), "version number"))
        numbers = [item["row"]["version_number"] for item in items]
        _need(len(set(numbers)) == len(numbers), "Duplicate policy version number")
        events = sorted(
            (event for event in facts.events if event["policy_id"] == policy_id),
            key=lambda event: _at(event["occurred_at"]),
        )
        _need(bool(events), "Complete original policy state history missing")
        prior_status: str | None = None
        for event in events:
            _need(
                prior_status is None or event["from_status"] == prior_status,
                "State event history is discontinuous",
            )
            prior_status = _text(event["to_status"], "state status")
            _need(
                prior_status
                in {
                    "PROPOSED",
                    "CONFIRMED",
                    "ACTIVE",
                    "MODIFIED",
                    "SUSPENDED",
                    "REVOKED",
                    "EXPIRED",
                },
                "Unsupported policy state",
            )
        policy = facts.index["policies"][policy_id]
        _need(
            policy.get("status") == prior_status,
            "Persistent policy state differs from original history",
        )
        for index, item in enumerate(items):
            _need(
                any(
                    event["version_id"] == item["row"]["id"]
                    and _at(event["occurred_at"]) == item["confirmed"]
                    and event["to_status"] in {"CONFIRMED", "ACTIVE", "MODIFIED"}
                    for event in events
                ),
                "Original confirmation state transition missing",
            )
            if index + 1 < len(items):
                stop = items[index + 1]["confirmed"]
                item["end"] = min(item["end"], stop) if item["end"] is not None else stop
            changes = [
                event
                for event in events
                if event["version_id"] == item["row"]["id"]
                and event["to_status"] in {"SUSPENDED", "REVOKED", "EXPIRED"}
            ]
            if changes:
                stop = _at(changes[0]["occurred_at"])
                if item["config"]["type"] == "recurring_obligation":
                    _need(
                        all(event["to_status"] == "EXPIRED" for event in changes)
                        and item["end"] is not None
                        and stop >= item["end"],
                        "Historical interrupted obligation needs an independent debt-event mapping",
                    )
                item["end"] = min(item["end"], stop) if item["end"] is not None else stop
            item["latest"] = index == len(items) - 1
            versions.append(item)
    return versions


def _active(version: dict[str, Any], at: datetime) -> bool:
    return version["start"] <= at and (version["end"] is None or at < version["end"])


def _months(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        _need(len(months) <= 120, "Historical monthly capacity exceeded")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _known_content(
    facts: Facts, source: str, at: datetime, selector: dict[str, Any]
) -> dict[str, Any]:
    record = facts.proof_record(source, at, selector)
    proof = _obj(record["content"], "cumulative original content")
    _need(
        proof.get("simulation") is True and proof.get("user_id") == facts.owner,
        "Bank proof simulation/owner differs",
    )
    _need(
        _at(proof.get("as_of")) <= _at(record["observed_at"]) <= at,
        "Cumulative proof observation chronology differs",
    )
    return proof


def _obligations(
    facts: Facts, versions: list[dict[str, Any]], at: datetime, end: datetime
) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    today, last = at.astimezone(facts.zone).date(), end.astimezone(facts.zone).date()
    for bill in facts.index["credit_card_bills"].values():
        proof = _lookup(facts.evidence, bill.get("evidence_id"), "bill bank evidence")
        _need(
            proof is not None
            and facts.valid(proof, at, "BANK_CONFIRMED")
            and proof.get("source_type") == "SIMULATED_CREDIT_CARD_BILL",
            "Original bill proof missing",
        )
        payload = _obj(proof["content"], "bill content")
        _need(
            payload.get("simulation") is True and payload.get("user_id") == facts.owner,
            "Bill source owner/simulation differs",
        )
        for key in (
            "account_id",
            "due_date",
            "statement_date",
            "total_cents",
            "paid_cents",
            "status",
        ):
            _need(
                _canonical(payload.get(key)) == _canonical(bill.get(key)), "Bill original differs"
            )
        _need(payload.get("bill_id") == bill["id"], "Bill proof identity differs")
        _need(
            bill["account_id"] in facts.index["accounts"]
            and facts.index["accounts"][bill["account_id"]].get("account_type") == "CREDIT_CARD",
            "Bill credit account missing",
        )
        total, paid = (
            _money(bill.get("total_cents"), "bill total"),
            _money(bill.get("paid_cents"), "bill paid"),
        )
        _need(
            paid <= total and _day(bill["statement_date"]) <= min(today, _day(bill["due_date"])),
            "Bill totals/calendar differ",
        )
        _need(
            bill["status"] in {"UNPAID", "PARTIALLY_PAID", "OVERDUE", "PAID"},
            "Unknown bill payment status",
        )
        _need((bill["status"] == "PAID") == (paid == total), "Bill status disagrees with payment")
        if _day(bill["due_date"]) <= last and total > paid:
            obligations.append(
                {
                    "kind": "BILL",
                    "id": bill["id"],
                    "due_on": max(today, _day(bill["due_date"])).isoformat(),
                    "required_cents": total - paid,
                }
            )
    generated: set[tuple[str, str]] = set()
    for version in versions:
        config, row = version["config"], version["row"]
        if config["type"] != "recurring_obligation":
            continue
        rule = _obj(config.get("amount_rule"), "obligation amount rule")
        if rule.get("kind") == "bill_balance":
            _need(
                set(rule) == {"kind", "account_id"}
                and rule["account_id"] in facts.index["accounts"],
                "Bill rule reference missing",
            )
            continue  # Real bills are already counted, never duplicate the payment policy.
        if rule.get("kind") == "exact":
            _need(set(rule) == {"kind", "amount_cents"}, "Unsupported exact amount rule")
            amount = _money(rule["amount_cents"], "exact obligation")
        elif rule.get("kind") == "range":
            _need(set(rule) == {"kind", "min_cents", "max_cents"}, "Unsupported range rule")
            amount = _money(rule["max_cents"], "range upper obligation")
            _need(_money(rule["min_cents"], "range lower") <= amount, "Reversed amount range")
        else:
            raise Missing("Unsupported obligation amount rule")
        due_day = _money(config.get("due_day"), "due day")
        _need(1 <= due_day <= 31, "Invalid due day")
        for year, month in _months(version["start"].astimezone(facts.zone).date(), last):
            due = date(year, month, min(due_day, calendar.monthrange(year, month)[1]))
            if due > last:
                continue
            day_start = datetime.combine(due, time.min, facts.zone).astimezone(UTC)
            day_end = day_start + timedelta(days=1)
            if version["start"] >= day_end or (
                version["end"] is not None and version["end"] <= day_start
            ):
                continue
            period = f"{year:04}-{month:02}"
            period_key = row["policy_id"], period
            _need(
                period_key not in generated,
                "One obligation period overlaps policy versions; explicit debt mapping required",
            )
            generated.add(period_key)
            evidence = [
                e
                for e in facts.evidence.values()
                if e.get("source_type") == "SIMULATED_RECURRING_SETTLEMENT"
                and facts.valid(e, at, "BANK_CONFIRMED")
                and e["content"].get("policy_id") == row["policy_id"]
                and e["content"].get("period") == period
            ]
            if due <= today or evidence:
                proof = _known_content(
                    facts,
                    "SIMULATED_RECURRING_SETTLEMENT",
                    at,
                    {"policy_id": row["policy_id"], "period": period},
                )
                _need(
                    proof.get("protocol") == "recurring-settlement-v1"
                    and proof.get("complete") is True
                    and proof.get("payee_id") == config.get("payee_id"),
                    "Cumulative payment proof differs",
                )
                _need(
                    _at(proof["as_of"]) == at,
                    "Cumulative payment needs same-time original completeness",
                )
                paid = _money(proof.get("paid_cents"), "observed paid")
                _need(paid <= amount, "Observed paid exceeds confirmed obligation")
            else:
                paid = 0  # Future known obligations, not a claim about historical unpaid facts.
            if amount > paid:
                obligations.append(
                    {
                        "kind": "RECURRING",
                        "id": f"{row['policy_id']}:{period}",
                        "due_on": max(today, due).isoformat(),
                        "required_cents": amount - paid,
                    }
                )
    return obligations


def _reserve(facts: Facts, configuration: dict[str, Any], at: datetime) -> dict[str, Any]:
    method = _obj(configuration.get("method"), "living reserve method")
    _need(
        set(method)
        == {"name", "lookback_days", "quantile", "essential_categories", "exclude_one_off"},
        "Unsupported reserve method fields",
    )
    _need(method["name"] == "rolling_window_quantile", "Unsupported reserve estimator")
    lookback = _money(method["lookback_days"], "reserve lookback")
    horizon = _money(configuration.get("horizon_days"), "reserve horizon")
    _need(1 <= horizon <= lookback <= 366, "Reserve window unsupported")
    quantile = method["quantile"]
    _need(
        type(quantile) is float and math.isfinite(quantile) and 0 < quantile <= 1,
        "Quantile must be original finite probability",
    )
    categories = _list(method["essential_categories"], "confirmed category scope")
    _need(
        bool(categories)
        and len(categories) == len(set(categories))
        and all(type(category) is str for category in categories),
        "Invalid category scope",
    )
    _need(type(method["exclude_one_off"]) is bool, "One-off flag must remain boolean")
    coverage = _obj(facts.value.get("expense_history"), "expense history coverage original")
    _need(
        coverage.get("protocol") == "mvp-expense-coverage-v1" and coverage.get("complete") is True,
        "Expense coverage missing",
    )
    scope = [
        _uuid(identity) for identity in _list(coverage.get("account_ids"), "coverage account scope")
    ]
    _need(
        set(scope) == set(facts.index["accounts"]) and len(scope) == len(set(scope)),
        "Complete original expense account scope differs",
    )
    beginning, ending = _at(coverage.get("start_at")), _at(coverage.get("end_at"))
    today = at.astimezone(facts.zone).date()
    first = today - timedelta(days=lookback)
    _need(
        beginning <= datetime.combine(first, time.min, facts.zone).astimezone(UTC)
        and datetime.combine(today, time.min, facts.zone).astimezone(UTC) <= ending <= at,
        "Full closed history days missing",
    )
    refs = _list(coverage.get("source_refs"), "coverage original sources")
    _need(bool(refs), "Coverage needs its original source")
    body = {key: value for key, value in coverage.items() if key != "source_refs"}
    _need(
        any(_canonical(facts.source(ref)) == _canonical(body) for ref in refs),
        "Coverage fields differ from original",
    )
    selected = [
        row
        for row in facts.index["transactions"].values()
        if beginning <= _at(row["occurred_at"]) < ending
    ]
    _need(
        sorted(row["id"] for row in selected)
        == sorted(_list(coverage.get("transaction_ids"), "complete transaction IDs")),
        "History transaction inventory differs",
    )
    evidence_ids = sorted(_uuid(row.get("evidence_id")) for row in selected)
    _need(
        evidence_ids == sorted(_list(coverage.get("evidence_ids"), "complete bank evidence IDs")),
        "History evidence inventory differs",
    )
    daily = {first + timedelta(days=offset): 0 for offset in range(lookback)}
    included = []
    for row in selected:
        evidence = _lookup(facts.evidence, row.get("evidence_id"), "expense bank evidence")
        _need(
            evidence is not None
            and facts.valid(evidence, at, "BANK_CONFIRMED")
            and evidence.get("source_type") == "SIMULATED_BANK_TRANSACTION",
            "History bank evidence missing",
        )
        payload = _obj(evidence["content"], "bank transaction payload")
        _need(
            payload.get("transaction_id") == row["id"] and payload.get("simulation") is True,
            "History transaction identity differs",
        )
        for key in (
            "account_id",
            "direction",
            "amount_cents",
            "balance_after_cents",
            "counterparty_ref",
        ):
            _need(
                payload.get(key) == row.get(key) and type(payload.get(key)) is type(row.get(key)),
                "History bank field differs",
            )
        _need(
            _at(payload.get("occurred_at")) == _at(row["occurred_at"]) <= at,
            "History bank time differs",
        )
        role = payload.get("economic_role")
        _need(
            role
            in {
                "CONSUMPTION",
                "INCOME",
                "OPENING",
                "INTERNAL_TRANSFER",
                "ASSET_PURCHASE",
                "CREDIT_CARD_PAYMENT",
                "PRINCIPAL_RETURN",
            },
            "History economic role unknown",
        )
        occurred_on = _at(row["occurred_at"]).astimezone(facts.zone).date()
        if occurred_on not in daily or row.get("direction") != "DEBIT" or role != "CONSUMPTION":
            continue
        _need(
            type(row.get("category_confirmed")) is bool and type(row.get("is_one_off")) is bool,
            "History classification types differ",
        )
        if row["category_confirmed"]:
            confirmation = facts.proof(
                "SIMULATED_USER_CATEGORY_CONFIRMATION",
                at,
                {"transaction_id": row["id"]},
                "USER_DECLARED",
            )
            _need(
                confirmation.get("confirmed") is True
                and confirmation.get("category") == row.get("category"),
                "Actual user category confirmation differs",
            )
        if (
            not row["category_confirmed"]
            or row.get("category") not in categories
            or (method["exclude_one_off"] and row["is_one_off"])
        ):
            continue
        daily[occurred_on] = _sum(
            (daily[occurred_on], _money(row["amount_cents"], "history amount"))
        )
        included.append(row["id"])
    amounts = [daily[day] for day in sorted(daily)]
    windows = [_sum(amounts[start : start + horizon]) for start in range(lookback - horizon + 1)]
    probability = Fraction(str(quantile))
    rank = (
        len(windows) * probability.numerator + probability.denominator - 1
    ) // probability.denominator
    base = sorted(windows)[rank - 1]
    amount = _sum(
        (base, _money(configuration.get("extra_buffer_cents", 0), "extra reserve buffer"))
    )
    return {
        "kind": "LIVING_RESERVE",
        "required_cents": amount,
        "base_cents": base,
        "rank": rank,
        "windows_cents": windows,
        "daily_cents": amounts,
        "included_transaction_ids": sorted(included),
        "history_start": first.isoformat(),
        "history_end": (today - timedelta(days=1)).isoformat(),
    }


def _goals(
    facts: Facts,
    heads: dict[str, dict[str, Any]],
    versions: list[dict[str, Any]],
    at: datetime,
    end: datetime,
) -> tuple[int, int, list[dict[str, Any]]]:
    owned_total, minimum_total = 0, 0
    components = []
    by_account: dict[str, int] = defaultdict(int)
    for goal in facts.index["goals"].values():
        proof = _known_content(facts, "SIMULATED_GOAL_OWNERSHIP", at, {"goal_id": goal["id"]})
        _need(_at(proof["as_of"]) <= at, "Future goal ownership original")
        if facts.value["protocol"] != "mvp-financial-facts-v2":
            _need(_at(proof["as_of"]) == at, "Goal ownership needs same-time original completeness")
        else:
            _need(
                all(
                    _at(head["occurred_at"]) <= _at(proof["as_of"])
                    for key, head in heads.items()
                    if key in {"GOAL_CASH:" + goal["id"], "GOAL_PRINCIPAL:" + goal["id"]}
                ),
                "Goal ownership original predates an unprojected bank ownership leg",
            )
        _need(
            proof.get("protocol") == "goal-ownership-v1"
            and proof.get("policy_id") == goal.get("policy_id")
            and proof.get("account_id") == goal.get("account_id"),
            "Goal ownership identity differs",
        )
        cash = _money(proof.get("cash_owned_cents"), "owned goal cash")
        principal = _money(proof.get("principal_owned_cents"), "owned goal principal")
        allocated = _money(proof.get("allocated_cents"), "goal allocation")
        _need(
            allocated == cash + principal == goal.get("allocated_cents"),
            "Goal ownership decomposition differs",
        )
        positions = [
            p
            for p in facts.index["asset_positions"].values()
            if p.get("goal_id") == goal["id"] and p.get("status") != "REDEEMED"
        ]
        _need(
            sorted(p["id"] for p in positions)
            == sorted(_list(proof.get("position_ids"), "owned position IDs")),
            "Goal principal identity inventory differs",
        )
        _need(
            _sum(p["principal_cents"] for p in positions) == principal,
            "Goal principal original amount differs",
        )
        for position in positions:
            _need(
                position.get("status") in {"HELD", "REDEEMING", "MATURED"},
                "Unknown owned principal state",
            )
            original_position = _known_content(
                facts, "SIMULATED_BANK_POSITION", at, {"position_id": position["id"]}
            )
            _need(
                all(
                    _canonical(original_position.get(key)) == _canonical(position.get(key))
                    for key in ("account_id", "goal_id", "product_id", "principal_cents", "status")
                ),
                "Goal principal original differs",
            )
        if cash:
            account_id = _uuid(goal.get("account_id"))
            _need(account_id in facts.index["accounts"], "Goal cash account missing")
            by_account[account_id] += cash
        head = heads.get(f"GOAL_CASH:{goal['id']}")
        _need(cash == 0 or head is not None, "Nonzero goal ownership memo original missing")
        if head is not None:
            _need(
                head.get("ledger_dimension") == "GOAL_OWNERSHIP"
                and head.get("ledger_metadata")
                == {"goal_id": goal["id"], "account_id": goal.get("account_id")}
                and head["balance_after_cents"] == cash,
                "Goal ownership memo differs from current proof",
            )
        owned_total = _sum((owned_total, cash))
        relevant = [
            version for version in versions if version["row"]["id"] == goal.get("policy_version_id")
        ]
        _need(len(relevant) == 1, "Goal exact policy version missing")
        version = relevant[0]
        config = version["config"]
        _need(
            config["type"] == "goal_saving"
            and config.get("cross_goal_reallocation_allowed") is False,
            "Cross-goal redistribution unsupported",
        )
        for key in ("target_cents", "deadline"):
            _need(config.get(key) == goal.get(key), "Goal configuration original differs")
        monthly = _obj(config.get("monthly_contribution"), "monthly goal commitment")
        _need(set(monthly) == {"min_cents", "target_cents", "max_cents"}, "Unsupported goal range")
        low, target, high = [
            _money(monthly[name], name) for name in ("min_cents", "target_cents", "max_cents")
        ]
        _need(
            low <= target <= high
            and [
                goal.get(key)
                for key in ("monthly_min_cents", "monthly_target_cents", "monthly_max_cents")
            ]
            == [low, target, high],
            "Goal monthly commitment differs",
        )
        minimum = 0
        _need(
            _day(config["deadline"]) >= at.astimezone(facts.zone).date()
            or allocated >= goal["target_cents"],
            "Past-deadline unfinished goal needs explicit continuing commitment mapping",
        )
        _need(
            version["start"] <= at or allocated >= goal["target_cents"],
            "Future goal activation timeline is unsupported",
        )
        _need(
            version["end"] is None
            or version["end"] <= at
            or version["end"] > end
            or allocated >= goal["target_cents"],
            "Goal commitment expiry inside horizon needs a per-instant mapping",
        )
        if _active(version, at) or version["start"] > at:
            first = max(at, version["start"]).astimezone(facts.zone).date()
            last = min(end.astimezone(facts.zone).date(), _day(config["deadline"]))
            if version["end"] is not None:
                last = min(
                    last, (version["end"] - timedelta(microseconds=1)).astimezone(facts.zone).date()
                )
            if first <= last:
                months = _months(first, last)
                contributed = 0
                current_period = at.astimezone(facts.zone).strftime("%Y-%m")
                if months[0] == (at.astimezone(facts.zone).year, at.astimezone(facts.zone).month):
                    contribution = _known_content(
                        facts,
                        "SIMULATED_GOAL_MONTH_CONTRIBUTION",
                        at,
                        {"goal_id": goal["id"], "period": current_period},
                    )
                    _need(
                        contribution.get("protocol") == "goal-month-contribution-v1"
                        and contribution.get("complete") is True
                        and _at(contribution["as_of"]) <= _at(proof["as_of"])
                        and (
                            facts.value["protocol"] == "mvp-financial-facts-v2"
                            or _at(contribution["as_of"]) == _at(proof["as_of"])
                        ),
                        "Goal month/ownership time differs",
                    )
                    contributed = _money(
                        contribution.get("contributed_cents"), "monthly contribution"
                    )
                    if facts.value["protocol"] == "mvp-financial-facts-v2":
                        for operation in facts.index["bank_operations"].values():
                            if operation.get("operation_type") != "ALLOCATE_GOAL":
                                continue
                            original_effect = _obj(
                                _obj(operation.get("request"), "bank goal request").get("effect"),
                                "bank goal effect",
                            )
                            if original_effect.get("goal_id") != goal["id"]:
                                continue
                            _need(
                                value_sha256(original_effect)
                                == operation["request"].get("effect_hash"),
                                "Bank goal contribution effect hash differs",
                            )
                            if operation.get("status") == "SETTLED":
                                settled = _at(operation.get("settled_at"))
                                if (
                                    settled.astimezone(facts.zone).strftime("%Y-%m")
                                    == current_period
                                ):
                                    _need(
                                        settled <= _at(contribution["as_of"]),
                                        "Monthly contribution predates actual bank allocation",
                                    )
                monthly_remaining = max(0, low - contributed) + low * (len(months) - 1)
                priority = _obj(config.get("priority"), "goal priority")
                cumulative = _money(priority.get("minimum_cents"), "minimum goal ownership")
                _need(
                    cumulative == goal.get("minimum_protection_cents"),
                    "Cumulative goal commitment differs",
                )
                minimum = min(
                    max(0, goal["target_cents"] - allocated),
                    max(monthly_remaining, max(0, cumulative - allocated)),
                )
        minimum_total = _sum((minimum_total, minimum))
        components.append(
            {
                "kind": "GOAL",
                "id": goal["id"],
                "cash_owned_cents": cash,
                "principal_owned_cents": principal,
                "minimum_remaining_cents": minimum,
                "version_id": version["row"]["id"],
            }
        )
    for account in facts.index["accounts"].values():
        if account["account_type"] not in {"CASH", "GOAL"}:
            continue
        balance = heads[f"CASH:{account['id']}"]["balance_after_cents"]
        _need(by_account[account["id"]] <= balance, "Goal cash exceeds settled account cash")
        if account["account_type"] == "GOAL":
            unassigned = balance - by_account[account["id"]]
            owned_total = _sum((owned_total, unassigned))
            components.append(
                {
                    "kind": "UNASSIGNED_GOAL_CASH",
                    "account_id": account["id"],
                    "required_cents": unassigned,
                }
            )
    return owned_total, minimum_total, components


def _income(
    facts: Facts, heads: dict[str, dict[str, Any]], at: datetime, authorization: dict[str, Any]
) -> int:
    payload = _obj(facts.value.get("income_payload"), "original income-location ledger")
    _need(
        payload.get("protocol") == "new-funds-ledger-v2"
        and payload.get("complete") is True
        and payload.get("simulation") is True
        and payload.get("user_id") == facts.owner,
        "Income-location ledger original missing",
    )
    proof = _known_content(
        facts, "SIMULATED_NEW_FUNDS_LEDGER", at, {"protocol": "new-funds-ledger-v2"}
    )
    _need(_canonical(payload) == _canonical(proof), "Income payload differs from original evidence")
    _need(_at(payload["as_of"]) <= at, "Future income-location proof")
    scope = [_uuid(account) for account in _list(payload.get("scope_account_ids"), "income scope")]
    _need(
        len(set(scope)) == len(scope)
        and all(account in facts.index["accounts"] for account in scope),
        "Income scope differs",
    )
    origins = {
        _uuid(row.get("origin_transaction_id")): _obj(row, "income origin")
        for row in _list(payload.get("origins"), "income origins")
    }
    _need(len(origins) == len(payload["origins"]), "Duplicate income origin")
    totals: dict[str, int] = defaultdict(int)
    available_by_account: dict[str, int] = defaultdict(int)
    eligible = 0
    fragments: dict[str, dict[str, Any]] = {}
    for fragment in _list(payload.get("fragments"), "income fragments"):
        row = _obj(fragment, "income fragment")
        origin_id, account_id = (
            _uuid(row.get("origin_transaction_id")),
            _uuid(row.get("account_id")),
        )
        _need(
            origin_id in origins and account_id in scope, "Income fragment origin/account missing"
        )
        identity = _uuid(row.get("fragment_id"))
        _need(
            identity == str(uuid5(UUID(origin_id), "income-location:" + account_id))
            and identity not in fragments,
            "Income fragment identity differs",
        )
        fragments[identity] = row
        portions = [
            _money(row.get(key), key)
            for key in ("spent_cents", "assigned_cents", "reserved_cents", "available_cents")
        ]
        _need(
            _money(row.get("legacy_reserved_cents"), "legacy reservation") <= portions[2],
            "Legacy reservation exceeds reserved source",
        )
        totals[origin_id] = _sum((totals[origin_id], *portions))
        available_by_account[account_id] += portions[3]
        origin = origins[origin_id]
        for portion, amount in zip(
            ("SPENT", "ASSIGNED", "RESERVED", "AVAILABLE"), portions, strict=True
        ):
            original_head = _lookup(heads, f"LOT_{portion}:{identity}", "income ownership head")
            if facts.value["protocol"] == "mvp-financial-facts-v2":
                # App reservations retain bank AVAILABLE until bank commit;
                # only legacy reservations occupy the bank RESERVED bucket.
                if portion == "AVAILABLE":
                    amount = _sum((amount, portions[2], -row["legacy_reserved_cents"]))
                elif portion == "RESERVED":
                    amount = row["legacy_reserved_cents"]
            _need(
                original_head is not None
                and original_head.get("ledger_dimension") == "INCOME_LOCATION"
                and original_head["balance_after_cents"] == amount,
                "Income portion differs from actual virtual ownership ledger",
            )
            _need(
                original_head.get("ledger_metadata")
                == {"account_id": account_id, "origin": origin},
                "Income ownership ledger source metadata differs",
            )
        transaction = _lookup(facts.index["transactions"], origin_id, "income transaction")
        evidence = _lookup(facts.evidence, origin.get("bank_evidence_id"), "income bank evidence")
        _need(
            transaction is not None
            and evidence is not None
            and facts.valid(evidence, at, "BANK_CONFIRMED"),
            "Income origin bank originals missing",
        )
        origin_time, observed = _at(origin.get("occurred_at")), _at(origin.get("observed_at"))
        _need(
            origin_time <= observed <= at and origin.get("origin_account_id") in scope,
            "Future or unscoped income origin",
        )
        _need(
            facts.index["accounts"][origin["origin_account_id"]].get("account_type") == "CASH",
            "Income origin is not a cash receipt",
        )
        _need(
            evidence.get("source_type") == "SIMULATED_BANK_TRANSACTION"
            and observed == _at(evidence["observed_at"]) == _at(transaction.get("observed_at")),
            "Income original observation differs",
        )
        _need(
            origin.get("bank_evidence_hash") == evidence.get("content_hash")
            and transaction.get("evidence_id") == evidence.get("id"),
            "Income origin proof identity differs",
        )
        bank = _obj(evidence["content"], "income bank original")
        _need(
            bank.get("economic_role") == "INCOME"
            and bank.get("direction") == "CREDIT"
            and bank.get("transaction_id") == origin_id,
            "Transfer/principal is not new income",
        )
        _need(
            _money(bank.get("amount_cents"), "bank income amount")
            == _money(origin.get("amount_cents"), "income origin amount")
            == transaction.get("amount_cents")
            and bank.get("account_id")
            == origin.get("origin_account_id")
            == transaction.get("account_id"),
            "Income origin amount/account differs",
        )
        _need(
            bank.get("simulation") is True and transaction.get("direction") == "CREDIT",
            "Income source simulation/direction differs",
        )
        _need(
            _at(bank.get("occurred_at")) == origin_time == _at(transaction.get("occurred_at")),
            "Income origin occurrence changed",
        )
        if (
            origin_time >= authorization["start"]
            and facts.index["accounts"][account_id]["account_type"] == "CASH"
        ):
            eligible = _sum((eligible, portions[3]))
    _need(
        dict(totals)
        == {
            identity: _money(row.get("amount_cents"), "original income amount")
            for identity, row in origins.items()
        },
        "Income origin conservation differs",
    )
    for account_id, amount in available_by_account.items():
        goal_owned = _sum(
            _money(
                _known_content(facts, "SIMULATED_GOAL_OWNERSHIP", at, {"goal_id": goal["id"]})[
                    "cash_owned_cents"
                ],
                "goal cash ownership",
            )
            for goal in facts.index["goals"].values()
            if goal.get("account_id") == account_id
        )
        _need(
            amount <= heads[f"CASH:{account_id}"]["balance_after_cents"] - goal_owned,
            "Unspent income exceeds unassigned actual cash",
        )
    claimed: dict[str, int] = defaultdict(int)
    seen_actions: set[str] = set()
    for reservation in _list(payload.get("reservations"), "income reservations"):
        row = _obj(reservation, "income reservation")
        action_id = _uuid(row.get("action_id"))
        _need(
            action_id not in seen_actions and action_id in facts.index["action_plans"],
            "Reservation original action missing or repeated",
        )
        seen_actions.add(action_id)
        _need(
            row.get("state") in {"RESERVED", "COMMITTED", "RELEASED"}, "Unknown reservation state"
        )
        uses = _list(row.get("uses"), "reservation uses")
        _need(bool(uses), "Empty reservation original uses")
        seen_fragments: set[str] = set()
        for use in uses:
            item = _obj(use, "income use")
            fragment_id = _uuid(item.get("fragment_id"))
            _need(fragment_id not in seen_fragments, "Repeated source in one income reservation")
            seen_fragments.add(fragment_id)
            _need(
                fragment_id in fragments
                and item.get("origin_transaction_id")
                == fragments[fragment_id]["origin_transaction_id"]
                and item.get("account_id") == fragments[fragment_id]["account_id"],
                "Reservation source identity differs",
            )
            if row["state"] == "RESERVED":
                claimed[fragment_id] += _money(item.get("amount_cents"), "reserved income use")
    for identity, row in fragments.items():
        _need(
            row["reserved_cents"] == row["legacy_reserved_cents"] + claimed[identity],
            "Reserved income source reconciliation differs",
        )
    return eligible


def _deployment(
    facts: Facts,
    heads: dict[str, dict[str, Any]],
    versions: list[dict[str, Any]],
    at: datetime,
    checkpoint: dict[str, Any],
    margins: list[tuple[date, int]],
) -> tuple[int, list[dict[str, Any]]]:
    _need(
        checkpoint.get("requested_action_type") == "PURCHASE_ASSET",
        "Deployment action selector missing or unsupported",
    )
    _need(checkpoint.get("scope") == "GENERAL", "Goal-scope deployment is not implemented")
    policy_id, product_id = _uuid(checkpoint.get("policy_id")), _uuid(checkpoint.get("product_id"))
    candidates = [
        version
        for version in versions
        if version["row"]["policy_id"] == policy_id and version["latest"] and _active(version, at)
    ]
    _need(len(candidates) == 1, "Exact currently effective deployment authorization missing")
    authorization = candidates[0]
    config = authorization["config"]
    _need(
        config["type"] == "asset_authorization"
        and config.get("scope") == "general_idle_funds"
        and config.get("goal_id") is None,
        "General asset authorization differs",
    )
    product = facts.index["asset_products"].get(product_id)
    _need(product is not None, "Original selected product missing")
    product = cast(dict[str, Any], product)
    _need(
        product.get("principal_fluctuation") is False
        and type(product.get("auto_purchase_allowed")) is bool,
        "Product risk/automatic flags unknown",
    )
    _need(
        _at(product.get("effective_from")) <= at
        and (product.get("effective_until") is None or at < _at(product["effective_until"])),
        "Selected product terms not currently effective",
    )
    permitted = (
        product.get("asset_class")
        in _list(config.get("allowed_asset_classes"), "authorized asset classes")
        and product["auto_purchase_allowed"] is True
        and _money(product.get("risk_level"), "principal risk")
        <= _money(config.get("max_principal_risk_level"), "authorized risk")
        and _money(product.get("lock_days"), "product lock")
        <= _money(config.get("max_lock_days"), "authorized lock")
        and _money(product.get("redemption_delay_days"), "product delay")
        <= _money(config.get("max_redemption_delay_days"), "authorized delay")
    )
    _need(
        config.get("max_principal_risk_level") == 0,
        "Nonzero principal-risk authorization unsupported",
    )
    return_day: date | None = None
    rule = _obj(product.get("maturity_rule"), "original product maturity rule")
    if set(rule) == {"kind", "auto_rollover"}:
        _need(
            rule["kind"] == "RETURN_TO_CASH" and rule["auto_rollover"] is False,
            "Unsupported legacy product terms",
        )
        # Original v1 directory has no dated guarantee: use full-horizon
        # principal occupancy, without upgrading a product label to liquidity.
        rule = {}
    if rule:
        _need(
            rule.get("protocol") != "planned-principal-return-v1",
            "On-request product needs an independently bound candidate exit-time selector",
        )
        _need(
            set(rule)
            <= {
                "protocol",
                "day_basis",
                "guaranteed",
                "rollover",
                "term_days",
                "settlement_delay_days",
                "principal_return_bps",
                "kind",
                "auto_rollover",
                "yield_rule",
            },
            "Unsupported fixed-return term fields",
        )
        _need(
            rule.get("protocol") == "fixed-principal-return-v1"
            and rule.get("day_basis") == "CALENDAR"
            and rule.get("guaranteed") is True
            and rule.get("rollover") is False
            and ("auto_rollover" not in rule or rule["auto_rollover"] is False)
            and ("kind" not in rule or rule["kind"] == "RETURN_TO_CASH")
            and _money(product.get("risk_level"), "guaranteed return product risk") == 0
            and _money(rule.get("principal_return_bps"), "principal return") == 10000,
            "Principal return is not guaranteed full nonrollover",
        )
        term = _money(rule.get("term_days"), "fixed term")
        delay = _money(rule.get("settlement_delay_days"), "settlement delay")
        if "yield_rule" in rule:
            yield_rule = _obj(rule["yield_rule"], "original yield terms")
            _need(
                set(yield_rule)
                <= {
                    "protocol",
                    "simulation",
                    "annual_yield_bps",
                    "basis",
                    "accrual",
                    "fee_cents",
                    "purchase_fee_bps",
                    "redemption_fee_bps",
                },
                "Unsupported yield term fields",
            )
            _need(
                yield_rule.get("protocol") == "simple-annual-yield-v1"
                and yield_rule.get("simulation") is True
                and yield_rule.get("basis") == "ACT_365"
                and yield_rule.get("accrual") == "UNTIL_MATURITY",
                "Unsupported principal-return yield contract",
            )
            _money(yield_rule.get("annual_yield_bps"), "original yield rate")
            _need(
                all(
                    _money(yield_rule.get(key), key) == 0
                    for key in ("fee_cents", "purchase_fee_bps", "redemption_fee_bps")
                ),
                "Nonzero candidate fees need independent cash-flow mapping",
            )
        _need(
            term >= product["lock_days"] and delay >= product["redemption_delay_days"],
            "Product maturity terms conflict",
        )
        return_day = at.astimezone(facts.zone).date() + timedelta(days=term + delay)
    managed, reserved = 0, 0
    for operation in facts.index["bank_operations"].values():
        _need(
            operation.get("status") in {"ACCEPTED", "SETTLED", "UNKNOWN", "REJECTED"},
            "Unknown original bank operation state",
        )
        _need(
            operation["status"] not in {"ACCEPTED", "UNKNOWN"},
            "Unsettled principal/cash operation needs a scheduled-availability mapping",
        )
    for position in facts.index["asset_positions"].values():
        if position.get("status") == "REDEEMED":
            continue
        _need(
            position.get("status") in {"HELD", "REDEEMING", "MATURED"},
            "Unknown position reconciliation state",
        )
        proof = _known_content(
            facts, "SIMULATED_BANK_POSITION", at, {"position_id": position["id"]}
        )
        for key in (
            "product_id",
            "account_id",
            "goal_id",
            "principal_cents",
            "status",
            "policy_version_id",
        ):
            _need(
                _canonical(proof.get(key)) == _canonical(position.get(key)),
                "Position original differs",
            )
        if proof.get("acquisition") == "synthetic_user_manual_purchase":
            continue
        _need(
            proof.get("acquisition") == "synthetic_auto_purchase",
            "Unknown manual/automatic principal acquisition",
        )
        action = _lookup(
            facts.index["action_plans"],
            proof.get("purchase_action_id"),
            "principal purchase action",
        )
        receipt = _lookup(
            facts.index["action_receipts"],
            proof.get("purchase_receipt_id"),
            "principal purchase receipt",
        )
        transaction = _lookup(
            facts.index["transactions"],
            proof.get("purchase_transaction_id"),
            "principal purchase transaction",
        )
        _need(
            action is not None and receipt is not None and transaction is not None,
            "Automatic principal lacks original action/receipt/transaction identities",
        )
        _need(
            action.get("action_type") == "PURCHASE_ASSET"
            and action.get("status") in {"SUCCEEDED", "RECONCILED"}
            and receipt.get("status") == "SUCCEEDED"
            and receipt.get("action_plan_id") == action["id"]
            and transaction.get("action_plan_id") == action["id"],
            "Automatic principal purchase originals do not bind one executed action",
        )
        version = _lookup(
            facts.index["policy_versions"],
            position.get("policy_version_id"),
            "automatic principal policy version",
        )
        _need(version is not None, "Original automatic position policy missing")
        original_config = _configuration(version)
        _need(
            action.get("product_id") == position.get("product_id")
            and action.get("policy_version_id") == version["id"]
            and _money(action.get("amount_cents"), "original purchase amount")
            >= position["principal_cents"],
            "Automatic principal original purchase context differs",
        )
        if original_config.get("scope") == "general_idle_funds" and position.get("goal_id") is None:
            managed = _sum((managed, position["principal_cents"]))
    for action in facts.index["action_plans"].values():
        if action.get("action_type") == "PURCHASE_ASSET" and action.get("status") in {
            "UNKNOWN",
            "SUBMITTED",
        }:
            raise Missing("Unreconciled purchase exposure prevents precise deployment")
        if action.get("action_type") == "PURCHASE_ASSET" and action.get("status") in {
            "PLANNED",
            "AUTHORIZED",
        }:
            claims = [
                claim
                for claim in facts.index["action_resource_reservations"].values()
                if claim.get("action_plan_id") == action["id"]
                and claim.get("resource_kind") == "CASH"
                and claim.get("status") == "RESERVED"
            ]
            _need(
                bool(claims)
                and _sum(
                    _money(claim.get("amount_cents"), "pending purchase cash") for claim in claims
                )
                == _money(action.get("amount_cents"), "pending purchase amount"),
                "Pending purchase lacks exact unsubmitted cash reservation originals",
            )
    for claim in facts.index["action_resource_reservations"].values():
        _need(
            claim.get("status") in {"RESERVED", "CONSUMED", "RELEASED"},
            "Unknown resource reservation state",
        )
        _need(
            claim.get("resource_kind")
            in {"CASH", "INCOME", "GOAL_CASH", "MANAGED", "POSITION", "OBLIGATION", "BUSINESS"},
            "Unsupported resource reservation kind",
        )
        if claim["status"] != "RESERVED" or claim.get("resource_kind") not in {"CASH", "GOAL_CASH"}:
            continue
        _need(
            claim.get("resource_kind") == "CASH",
            "Goal-cash pending exposure needs a separate ownership mapping",
        )
        action = _lookup(
            facts.index["action_plans"], claim.get("action_plan_id"), "cash reservation action"
        )
        _need(
            action is not None and action.get("status") in {"PLANNED", "AUTHORIZED"},
            "Reserved cash lacks an unsubmitted original action",
        )
        amount = _money(claim.get("amount_cents"), "reserved cash")
        reserved = _sum((reserved, amount))
        if action.get("action_type") == "PURCHASE_ASSET":
            managed = _sum((managed, amount))
    eligible = _income(facts, heads, at, authorization)
    usable = [margin for day, margin in margins if return_day is None or day <= return_day]
    financial = max(0, min(usable))
    # Any baseline shortfall disallows a new purchase even if it repays before that shortfall.
    if min(margin for _, margin in margins) < 0:
        financial = 0
    remaining = max(
        0, _money(config.get("max_auto_managed_cents"), "managed authorization") - managed
    )
    single = _money(config.get("single_action_cap_cents"), "single authorization")
    _need(single <= config["max_auto_managed_cents"], "Single authorization exceeds total")
    result = min(max(0, financial - reserved), eligible, remaining, single) if permitted else 0
    if result < _money(product.get("minimum_purchase_cents"), "minimum purchase"):
        result = 0
    return result, [
        {
            "kind": "DEPLOYMENT_LIMITS",
            "version_id": authorization["row"]["id"],
            "product_id": product_id,
            "financial_limit_cents": financial,
            "reserved_cash_cents": reserved,
            "eligible_new_income_cents": eligible,
            "managed_principal_cents": managed,
            "managed_remaining_cents": remaining,
            "single_cap_cents": single,
            "terms_permitted": permitted,
            "candidate_return_on": return_day.isoformat() if return_day else None,
        }
    ]


def _checkpoint(facts: Facts, checkpoint: dict[str, Any]) -> dict[str, Any]:
    _need(
        set(checkpoint)
        <= {
            "checkpoint_id",
            "kind",
            "at",
            "scope",
            "goal_id",
            "horizon_end_at",
            "available_mode",
            "requested_action_type",
            "product_id",
            "policy_id",
        },
        "Unsupported checkpoint fields or precomputed oracle amounts",
    )
    at, end = _at(checkpoint.get("at")), _at(checkpoint.get("horizon_end_at"))
    _need(
        at == facts.as_of,
        "Checkpoint needs its same-time original snapshot; past/future state is not reconstructed",
    )
    _need(
        at <= end
        and (end.astimezone(facts.zone).date() - at.astimezone(facts.zone).date()).days <= 90,
        "MVP horizon is at most ninety local days",
    )
    _need(
        checkpoint.get("available_mode") == "ACTUAL_SETTLED",
        "Only actual settled cash is supported",
    )
    _need(
        checkpoint.get("kind") in {"PROTECTION", "DUE", "DEPLOYMENT"}, "Unsupported checkpoint kind"
    )
    _need(
        checkpoint.get("scope") == "GENERAL" and checkpoint.get("goal_id") is None,
        "Goal-scope checkpoint is not implemented",
    )
    cash, heads, cash_changes = _cash(facts, at)
    versions = _versions(facts, at)
    obligations = _obligations(facts, versions, at, end)
    obligation_total = _sum(row["required_cents"] for row in obligations)
    goal_cash, goal_minimum, goal_components = _goals(facts, heads, versions, at, end)
    components: list[dict[str, Any]] = [
        *obligations,
        *goal_components,
        {"kind": "ACTUAL_CASH", "amount_cents": cash, "cash_changes": cash_changes},
    ]
    floor_versions = []
    for version in versions:
        if version["end"] is not None and version["end"] <= at:
            continue
        if version["start"] > end:
            continue
        kind = version["config"]["type"]
        if kind == "living_reserve":
            result = _reserve(facts, version["config"], at)
            result["version_id"] = version["row"]["id"]
            components.append(result)
            floor_versions.append((version, result["required_cents"]))
        elif kind == "emergency_buffer":
            amount = _money(version["config"].get("amount_cents"), "emergency buffer")
            components.append(
                {"kind": "EMERGENCY", "version_id": version["row"]["id"], "required_cents": amount}
            )
            floor_versions.append((version, amount))
    today = at.astimezone(facts.zone).date()
    last = end.astimezone(facts.zone).date()
    margins = []
    for offset in range((last - today).days + 1):
        day = today + timedelta(days=offset)
        beginning = max(at, datetime.combine(day, time.min, facts.zone).astimezone(UTC))
        ending = beginning + timedelta(days=1)
        if day != today:
            ending = datetime.combine(day + timedelta(days=1), time.min, facts.zone).astimezone(UTC)
        else:
            ending = datetime.combine(today + timedelta(days=1), time.min, facts.zone).astimezone(
                UTC
            )
        ending = min(ending, end + timedelta(microseconds=1))
        active_policies = [
            version["row"]["policy_id"]
            for version, _ in floor_versions
            if version["start"] < ending and (version["end"] is None or beginning < version["end"])
        ]
        _need(
            len(active_policies) == len(set(active_policies)),
            "Multiple floor versions in one day need an exact intra-day commitment mapping",
        )
        rolling = _sum(
            amount
            for version, amount in floor_versions
            if version["start"] < ending and (version["end"] is None or beginning < version["end"])
        )
        required = _sum((obligation_total, goal_cash, goal_minimum, rolling))
        # Known current unpaid obligations hold cash; paying them later reduces
        # cash and liability equally. No forecast income or unstaged principal
        # is added. This algebra does not create hypothetical bank transactions.
        margins.append((day, _sum((cash, -required))))
    # Financial floors conservatively cover the remaining first local day;
    # authority separately uses the exact confirmation/effective instant.
    protected = _sum((cash, -margins[0][1]))
    components.append(
        {
            "kind": "PROTECTION_TIMELINE",
            "margins": [{"on": day.isoformat(), "margin_cents": margin} for day, margin in margins],
            "assumed_future_income_cents": 0,
            "added_unsettled_principal_cents": 0,
            "current_margin_cents": cash - protected,
        }
    )
    deployable: int | None = None
    if checkpoint["kind"] == "DEPLOYMENT":
        deployable, deployment_components = _deployment(
            facts, heads, versions, at, checkpoint, margins
        )
        components.extend(deployment_components)
    return {
        "status": "MEASURED",
        "available_cash_cents": cash,
        "protected_required_cents": protected,
        "safe_authorized_deployable_cents": deployable,
        "components": components,
        "raw_refs": facts.refs,
        "missing_reason": None,
    }


def compute_timeline(
    original_facts: dict[str, Any], checkpoints: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep every declared checkpoint, including missing and unsupported ones."""
    errors = (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError)
    try:
        facts: Facts | None = Facts(original_facts)
        reason = None
    except errors as error:
        facts, reason = None, str(error)
    results = []
    declared = _list(checkpoints, "frozen checkpoints")
    counts = Counter(
        record.get("checkpoint_id")
        for record in declared
        if type(record) is dict and type(record.get("checkpoint_id")) is str
    )
    for checkpoint in declared:
        identity = {
            key: checkpoint.get(key) if type(checkpoint) is dict else None
            for key in ("checkpoint_id", "kind", "at", "scope", "goal_id")
        }
        try:
            record = _obj(checkpoint, "checkpoint")
            _text(record.get("checkpoint_id"), "frozen checkpoint identity")
            _need(counts[record["checkpoint_id"]] == 1, "Duplicate frozen checkpoint identity")
            _need(facts is not None, reason or "Original facts missing")
            result = _checkpoint(cast(Facts, facts), record)
        except errors as error:
            result = {
                "status": "MISSING",
                "available_cash_cents": None,
                "protected_required_cents": None,
                "safe_authorized_deployable_cents": None,
                "components": [],
                "raw_refs": facts.refs if facts else [],
                "missing_reason": str(error),
            }
        results.append({**identity, **result})
    return {
        "protocol": PROTOCOL,
        "facts_status": "VERIFIED" if facts else "MISSING",
        "missing_reason": reason,
        "checkpoints": results,
        "execution_authority": False,
        "financial_effect_evidence": False,
    }
