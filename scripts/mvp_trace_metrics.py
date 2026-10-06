"""Read original, bound A1--A4 evidence. Never evaluate or execute financial policy.

The only application verifier is the typed audit-integrity contract. Frozen
opportunities, required evidence and causal maps come from the independent
registration, never the P decision's claimed success/source/autonomy list.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any
from uuid import UUID, uuid5

from scripts.mvp_observations import (
    RAW_PROTOCOL,
    Bundle,
    ObservationError,
    _digest,
    _identity,
    _integer,
    _list,
    _object,
    _text,
    _time,
    _unique_ids,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "mvp-trace-metrics-v1"
CONTRACT = "mvp-trace-registration-v1"
NAMES = {
    "A1": "动作可追溯率",
    "A2": "决策所需证据完整率",
    "A3": "审计链验证成功率",
    "A4": "失败原因是否可定位",
}
TABLE_KINDS = {
    "users": "USER",
    "action_plans": "ACTION_PLAN",
    "decision_runs": "DECISION_RUN",
    "policy_versions": "POLICY_VERSION",
    "evidence_items": "EVIDENCE",
    "bank_operations": "BANK_OPERATION",
    "action_receipts": "ACTION_RECEIPT",
    "simulated_bank_postings": "BANK_POSTING",
    "simulated_bank_redemptions": "BANK_REDEMPTION",
    "accounts": "ACCOUNT",
    "policies": "POLICY",
    "goals": "GOAL",
    "asset_positions": "ASSET_POSITION",
    "asset_products": "ASSET_PRODUCT",
    "external_bank_facts": "BANK_EXTERNAL_FACT",
    "action_resource_reservations": "RESOURCE_CLAIM",
    "transactions": "TRANSACTION",
    "credit_card_bills": "CREDIT_CARD_BILL",
    "policy_proposals": "POLICY_PROPOSAL",
    "decision_constraints": "DECISION_CONSTRAINT",
    "audit_events": "LEGACY_AUDIT_EVENT",
}
INVENTORY_TABLES = (
    "action_plans",
    "bank_operations",
    "action_receipts",
    "simulated_bank_redemptions",
)
ACTION_TYPES = {
    "TRANSFER_INTERNAL": "TRANSFER_INTERNAL",
    "PAY_RECURRING": "PAY_RECURRING",
    "ALLOCATE_GOAL": "ALLOCATE_GOAL",
    "PURCHASE_ASSET": "ASSET_PURCHASE",
    "REDEEM_ASSET": "ASSET_REDEEM",
}
EFFECT_FIELDS = set(
    (
        "simulation operation_id user_id business_key action_type amount_cents "
        "cash_uses income_uses "
        "destination_account_id goal_id policy_id policy_version_id policy_version_ids liability "
        "payee_id payee_evidence_id product_id product_version_number terms_digest position_id "
        "position_account_id return_account_id purchase_exit original_policy_version_id fee_cents "
        "loss_cents net_cents quote_id settlement_delay_days latest_arrival_at "
        "valid_from expires_at"
    ).split()
)


class Missing(ObservationError):
    """A required original or independently registered calculator input is absent."""


def digest_value(value: Any) -> str:
    """Original configuration JSON encoding; retain JSON numeric types and strings."""
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _need(value: dict[str, Any], name: str) -> Any:
    if name not in value or value[name] is None:
        raise Missing(f"Required original field is missing: {name}")
    return value[name]


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ObservationError(reason)


def _owner(row: dict[str, Any], owner: str) -> None:
    _check(
        _identity(_need(row, "user_id"), "original owner") == owner,
        "Original belongs to another owner",
    )


def _result(unit_id: str, operation: Any, refs: list[Any] | None = None) -> dict[str, Any]:
    try:
        detail = operation()
        return {
            "unit_id": unit_id,
            "status": "VERIFIED",
            "verified": True,
            "reason": None,
            **detail,
        }
    except Missing as error:
        return {
            "unit_id": unit_id,
            "status": "MISSING",
            "verified": None,
            "reason": str(error),
            "raw_refs": refs or [],
        }
    except (ObservationError, ValueError, TypeError, KeyError, IndexError) as error:
        return {
            "unit_id": unit_id,
            "status": "FAILED",
            "verified": False,
            "reason": str(error),
            "raw_refs": refs or [],
        }


def _rate(
    metric: str,
    units: list[dict[str, Any]],
    *,
    missing: str | None = None,
    run_status: str | None = None,
) -> dict[str, Any]:
    numerator = sum(unit["verified"] is True for unit in units)
    denominator = len(units)
    reason = missing or next(
        (unit["reason"] for unit in units if unit["status"] == "MISSING"), None
    )
    status = (
        "NOT_RUN"
        if run_status in {"NOT_RUN", "NOT_IMPLEMENTED"}
        else "MISSING"
        if reason
        else "MEASURED"
        if denominator
        else "NOT_APPLICABLE"
    )
    return {
        "name": NAMES[metric],
        "status": status,
        "value": numerator / denominator if status == "MEASURED" else None,
        "numerator": numerator if status != "NOT_RUN" else None,
        "denominator": denominator,
        "applicable_units": [u["unit_id"] for u in units],
        "missing_reason": reason if status != "NOT_RUN" else "Run was not attempted",
        "raw_refs": [ref for unit in units for ref in unit.get("raw_refs", [])],
        "failures": [unit for unit in units if unit["verified"] is not True],
        "units": units,
    }


class TraceBundle(Bundle):
    """Retain raw-file absence with registered denominators; never fabricate originals."""

    def __init__(self, manifest_path: Path):
        self.missing_originals: list[dict[str, Any]] = []
        self.current_source_hashes: dict[Path, str] = {}
        super().__init__(manifest_path)
        self.contract = _object(
            self.registrations["oracle"].get("trace_metrics"), "independent A1--A4 registration"
        )
        _check(self.contract.get("protocol") == CONTRACT, "Unknown trace registration")
        self.begin = _time(_need(self.contract, "run_begin_at"), "run begin")
        self.end = _time(_need(self.contract, "run_end_at"), "run end")
        _check(self.begin <= self.end, "Registered run window is reversed")
        for name in (
            "action_requirements",
            "decision_opportunities",
            "audit_checkpoints",
            "failure_opportunities",
        ):
            rows = [_object(row, name) for row in _list(_need(self.contract, name), name)]
            key = "checkpoint_id" if name == "audit_checkpoints" else "opportunity_id"
            ids = [_text(_need(row, key), key) for row in rows]
            _check(len(ids) == len(set(ids)), f"Duplicate frozen {name} identity")

    def _load_ref(self, ref: dict[str, Any], *, parse: bool = True) -> Any:
        try:
            return super()._load_ref(ref, parse=parse)
        except FileNotFoundError:
            if parse and ref in self.manifest.get("raw_refs", []):
                self.missing_originals.append(ref)
                return {
                    "protocol": RAW_PROTOCOL,
                    "kind": ref.get("kind"),
                    "bindings": self.bindings,
                    "payload": {"original_missing": True},
                }
            raise

    def present(self, kind: str) -> list[dict[str, Any]]:
        return [
            raw for raw in self.kinds(kind) if raw["payload"].get("original_missing") is not True
        ]

    def rows(self, kind: str, collection: str, key: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for raw in self.present(kind):
            for index, value in enumerate(_list(_need(raw["payload"], collection), collection)):
                row = dict(_object(value, collection))
                identity = _text(_need(row, key), key)
                _check(identity not in result, f"Duplicate actual {collection} identity")
                row["_original_ref"] = {
                    "artifact_sha256": raw["ref"]["sha256"],
                    "json_pointer": f"/payload/{collection}/{index}",
                    "value_sha256": digest_value(value),
                }
                result[identity] = row
        return result

    def resolve(self, value: Any, *, kind: str | None = None) -> Any:
        ref = _object(value, "exact original reference")
        _check(
            set(ref) == {"artifact_sha256", "json_pointer", "value_sha256"},
            "Original references require exact artifact/pointer/value digests",
        )
        matches = (
            [
                raw
                for raw in self.present(kind)
                if raw["ref"]["sha256"] == _digest(ref["artifact_sha256"], "artifact digest")
            ]
            if kind
            else [
                raw
                for raw in self.raw
                if raw["ref"]["sha256"] == _digest(ref["artifact_sha256"], "artifact digest")
                and raw["payload"].get("original_missing") is not True
            ]
        )
        if not matches:
            raise Missing("Referenced original artifact is unavailable")
        _check(len(matches) == 1, "Original artifact digest is ambiguous")
        pointer = _text(ref["json_pointer"], "original pointer")
        _check(
            pointer.startswith("/payload/") and re.search(r"~(?![01])", pointer) is None,
            "Original pointer must identify raw payload content",
        )
        current: Any = matches[0]["original"]
        try:
            for escaped in pointer.split("/")[1:]:
                key = escaped.replace("~1", "/").replace("~0", "~")
                if isinstance(current, list):
                    _check(
                        re.fullmatch(r"0|[1-9][0-9]*", key) is not None,
                        "Array pointer must be an exact nonnegative index",
                    )
                    current = current[int(key)]
                else:
                    current = _object(current, "pointer parent")[key]
        except (KeyError, IndexError) as error:
            raise Missing("Original pointer has no captured value") from error
        _check(
            digest_value(current) == _digest(ref["value_sha256"], "value digest"),
            "Original value digest differs",
        )
        return current

    def row(
        self, ref: Any, table: str, identity: str | None = None, *, scope: str = "TENANT"
    ) -> dict[str, Any]:
        descriptor = _object(ref, "row reference")
        _check(
            re.fullmatch(
                r"/payload/tables/" + re.escape(table) + r"/(0|[1-9][0-9]*)",
                _text(descriptor.get("json_pointer"), "row pointer"),
            )
            is not None,
            "Row reference does not point to the original business table",
        )
        row = _object(self.resolve(ref, kind="TRACE_SNAPSHOT"), "original business row")
        if scope == "GLOBAL_CATALOG":
            _check(
                table == "asset_products" and "user_id" not in row,
                "Only actual catalog product originals may use global scope",
            )
        elif table == "users":
            _check(row.get("id") == self.bindings["user_id"], "Original user is another owner")
        else:
            _check(scope == "TENANT", "Unsupported original ownership scope")
            _owner(row, self.bindings["user_id"])
        row_id = _identity(_need(row, "id"), "row identity")
        if identity is not None:
            _check(row_id == identity, "Original row identity is rebound")
        return row

    def source_guard(self) -> None:
        expected_paths = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "apps/api/app/domain").glob("*.py")
        } | {
            "scripts/mvp_trace_metrics.py",
            "scripts/mvp_observations.py",
            "pyproject.toml",
            "uv.lock",
        }
        refs = (
            self.frozen_view.source_originals(expected_paths)
            if self.v2
            else [
                _object(ref, "audit verifier source")
                for ref in _list(
                    _need(self.contract, "audit_verifier_sources"), "audit verifier sources"
                )
            ]
        )
        paths = [_text(_need(ref, "original_path"), "current source path") for ref in refs]
        _check(
            len(paths) == len(set(paths)) and set(paths) == expected_paths,
            "Audit verifier source inventory is incomplete or has drifted",
        )
        archived = self.registrations["source"]["files"]
        for ref in refs:
            if self.v2:
                self.frozen_view.read(Path(ref["path"]), ref["sha256"])
            else:
                _check(
                    {"path": ref["path"], "sha256": ref["sha256"]} in archived,
                    "Verifier source is not bound to the original source archive",
                )
                self._load_ref({"path": ref["path"], "sha256": ref["sha256"]}, parse=False)
            path = (ROOT / ref["original_path"]).resolve()
            _check(path.is_relative_to(ROOT), "Verifier source path escapes repository")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            _check(digest == ref["sha256"], "Current audit verifier source differs from run")
            self.current_source_hashes[path] = digest
            # A reused Python process may already have imported an older module even
            # when disk bytes now match the new archive. Compare original function
            # code, without executing or evaluating any financial source function.
            module_name = (
                ref["original_path"].removeprefix("apps/api/").removesuffix(".py").replace("/", ".")
            )
            loaded = sys.modules.get(module_name)
            if loaded is not None and ref["original_path"].startswith("apps/api/app/domain/"):
                compiled = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
                original_codes = {
                    code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)
                }
                for original_code in original_codes.values():
                    function = vars(loaded).get(original_code.co_name)
                    if isinstance(function, FunctionType):
                        _check(
                            function.__module__ == module_name
                            and function.__code__ == original_code,
                            "Loaded verifier/helper function differs from archived source",
                        )

    def unchanged(self) -> None:
        super().unchanged()
        for path, digest in self.current_source_hashes.items():
            _check(
                hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                "Current audit verifier source changed during this invocation",
            )


def _evidence(
    bundle: TraceBundle, ref: Any, expected: dict[str, Any], at: datetime
) -> dict[str, Any]:
    row = bundle.row(
        ref, "evidence_items", _identity(_need(expected, "evidence_id"), "required evidence id")
    )
    content = _object(_need(row, "content"), "original evidence content")
    digest = _digest(_need(row, "content_hash"), "content hash")
    _check(
        digest_value(content)
        == digest
        == _digest(_need(expected, "content_hash"), "expected content hash"),
        "Required evidence content/hash differs from independently frozen original",
    )
    for field in ("source_type", "source_ref", "evidence_level"):
        _check(
            _text(_need(row, field), field) == _text(_need(expected, field), field),
            "Required evidence source/level is rebound",
        )
    observed = _time(_need(row, "observed_at"), "evidence observed_at")
    valid_from = _time(_need(row, "valid_from"), "evidence valid_from")
    _check(
        observed <= at and valid_from <= at and row.get("status") == "VALID",
        "Required evidence is not timely VALID original evidence",
    )
    _check("valid_to" in row, "Required evidence validity end is not captured")
    if row["valid_to"] is not None:
        _check(at < _time(row["valid_to"], "evidence valid_to"), "Required evidence expired")
    for pointer, value in _object(
        _need(expected, "subject_fields"), "evidence subject map"
    ).items():
        _check(
            pointer in content and digest_value(content[pointer]) == digest_value(value),
            "Required evidence subject is rebound",
        )
    return row


def _decision(bundle: TraceBundle, ref: Any) -> dict[str, Any]:
    row = bundle.row(ref, "decision_runs")
    snapshot = _object(_need(row, "input_snapshot"), "original decision snapshot")
    _check(
        digest_value(snapshot) == _digest(_need(row, "snapshot_hash"), "snapshot hash"),
        "Original decision snapshot hash differs",
    )
    at = _time(_need(row, "as_of"), "decision as_of")
    _check(bundle.begin <= at <= bundle.end, "Decision is outside this registered run")
    trace = _object(_need(snapshot, "decision_trace"), "original typed decision trace")
    _check(
        trace.get("schema_version") == "decision-trace-v1"
        and trace.get("simulation") is True
        and trace.get("run_id") == row["id"]
        and trace.get("user_id") == bundle.bindings["user_id"]
        and _time(_need(trace, "as_of"), "original trace time") == at
        and digest_value({key: value for key, value in trace.items() if key != "trace_hash"})
        == _digest(_need(trace, "trace_hash"), "trace hash"),
        "Original decision trace/hash/identity/time differs",
    )
    input_fields = (
        "schema_version",
        "simulation",
        "user_id",
        "phase",
        "as_of",
        "algorithm_versions",
        "inputs",
        "sources",
        "policies",
    )
    _check(
        digest_value({key: _need(trace, key) for key in input_fields})
        == _digest(_need(trace, "input_hash"), "trace input hash"),
        "Original decision input hash differs",
    )
    return row


def _a2_unit(
    bundle: TraceBundle, expected: dict[str, Any], record: dict[str, Any] | None
) -> dict[str, Any]:
    if record is None:
        raise Missing("Predeclared decision has no original observation; stays in denominator")
    row = _decision(bundle, _need(record, "decision_ref"))
    at = _time(row["as_of"], "decision as_of")
    _check(
        at == _time(_need(expected, "consuming_at"), "frozen decision time"),
        "Actual decision time differs from independent opportunity",
    )
    requirements = [
        _object(item, "required evidence")
        for item in _list(_need(expected, "required_evidence"), "required evidence")
    ]
    if not requirements:
        raise Missing("No independently verified substantive required-evidence manifest exists")
    ids = [_identity(_need(item, "evidence_id"), "required evidence") for item in requirements]
    _check(len(ids) == len(set(ids)), "Required evidence manifest duplicates an identity")
    observed = _object(_need(record, "evidence_refs"), "observed evidence references")
    _check(set(observed) == set(ids), "Required evidence originals are incomplete or rebound")
    for requirement in requirements:
        original = _evidence(bundle, observed[requirement["evidence_id"]], requirement, at)
        _check(
            requirement["evidence_id"]
            in _unique_ids(_need(row, "evidence_ids"), "decision original evidence ids"),
            "Required evidence identity is not consumed by original decision",
        )
        sources = _list(row["input_snapshot"]["decision_trace"]["sources"], "decision sources")
        source = [
            item for item in sources if isinstance(item, dict) and item.get("id") == original["id"]
        ]
        _check(
            len(source) == 1
            and source[0].get("user_id") == original["user_id"]
            and source[0].get("content") == original["content"]
            and source[0].get("content_hash")
            == source[0].get("captured_content_hash")
            == original["content_hash"]
            and source[0].get("content_integrity") == "VERIFIED"
            and source[0].get("status_at_decision") == "VALID",
            "Required evidence original is absent or differs from captured decision source",
        )
    return {
        "decision_run_id": row["id"],
        "raw_refs": [record["_original_ref"], record["decision_ref"], *observed.values()],
        "required_count": len(ids),
    }


def _audit(bundle: TraceBundle, record: dict[str, Any]) -> dict[str, Any]:
    try:
        bundle.source_guard()
    except (ObservationError, OSError) as error:
        raise Missing(f"Current verifier/source binding cannot be verified: {error}") from error
    from app.domain.audit_chain import (
        parse_checkpoint,
        parse_event,
        parse_subject,
        subject_hash,
        verify_epoch,
    )
    from app.domain.audit_chain_types import AuditHead, AuditVerification, ReferenceBundle

    payload = _object(_need(record, "originals"), "complete audit originals")
    head_text = _need(payload, "head_text")
    _check(type(head_text) is str and bool(head_text), "Original head must be original JSON text")
    head = AuditHead.model_validate_json(head_text)
    _check(
        str(head.user_id) == bundle.bindings["user_id"]
        and str(head.epoch_id) == bundle.bindings["isolated_db_epoch"],
        "Original head is bound to another owner or epoch",
    )
    checkpoint_text = _need(payload, "checkpoint_text")
    checkpoint = parse_checkpoint(checkpoint_text)
    _check(
        bundle.begin <= checkpoint.captured_at <= bundle.end,
        "Audit checkpoint is outside this registered run",
    )
    events = _list(_need(payload, "event_texts"), "original audit events")
    subjects = [
        parse_subject(text)
        for text in _list(_need(payload, "subject_texts"), "original audit subjects")
    ]
    current = [
        parse_subject(text)
        for text in _list(_need(payload, "current_subject_texts"), "current audit originals")
    ]
    manifest = _object(_need(payload, "reference_manifest"), "independent completeness manifest")
    raw_result = _object(
        bundle.resolve(_need(record, "verification_ref"), kind="HTTP_EXCHANGES"),
        "original verifier output",
    )
    claimed = AuditVerification.model_validate_json(json.dumps(raw_result, allow_nan=False))
    event_ids = _unique_ids(_need(manifest, "event_ids"), "registered original audit event ids")
    _check(
        len(events)
        == len(event_ids)
        == head.event_count
        == _integer(_need(manifest, "event_count"), "manifest event count"),
        "Original audit event/head/manifest counts differ",
    )
    actual = verify_epoch(
        events,
        head=head,
        expected_user_id=UUID(bundle.bindings["user_id"]),
        references=ReferenceBundle(subjects=subjects, current_subjects=current),
        checkpoint=checkpoint,
        checkpoint_mode="EXACT",
        event_limit=10000,
        byte_limit=64 * 1024 * 1024,
    )
    _check(
        digest_value(claimed.model_dump(mode="json"))
        == digest_value(actual.model_dump(mode="json")),
        "Original verifier output differs from actual complete typed verification",
    )
    _check(
        actual.status == actual.chain_status == actual.reference_status == "VALID"
        and actual.checkpoint_status == "VERIFIED"
        and not actual.errors
        and not actual.errors_truncated
        and actual.verified_through_sequence == len(events),
        f"Actual audit integrity verification is {actual.status}",
    )
    typed_events = [parse_event(text) for text in events]
    _check(
        [str(event.id) for event in typed_events] == event_ids,
        "Original event identities/order differ from independent manifest",
    )
    historical = {(s.kind, str(s.id), subject_hash(s)) for s in subjects}
    latest = {(s.kind, str(s.id), subject_hash(s)) for s in current}
    for field, original_set, originals in (
        ("subject_keys", historical, subjects),
        ("current_keys", latest, current),
    ):
        registered = [
            _object(key, "original subject key") for key in _list(_need(manifest, field), field)
        ]
        keys = {(key["kind"], key["id"], key["snapshot_hash"]) for key in registered}
        _check(
            len(original_set) == len(originals) == len(registered) == len(keys)
            and keys == original_set,
            "Audit original reference inventory is incomplete",
        )
    if len({(s.kind, str(s.id)) for s in current}) < len({(s.kind, str(s.id)) for s in subjects}):
        raise Missing("Complete current original subjects are missing")
    _check(
        {(s.kind, str(s.id)) for s in subjects} == {(s.kind, str(s.id)) for s in current},
        "Complete current original subjects are required for every historical identity",
    )
    current_refs = _list(_need(payload, "current_business_refs"), "current business originals")
    if len(current_refs) < len(current):
        raise Missing("Current business original references are missing")
    _check(len(current_refs) == len(current), "Current business original references are missing")
    latest_ids: set[tuple[str, str]] = set()
    for item in current_refs:
        descriptor = _object(item, "current business descriptor")
        table = _text(_need(descriptor, "table"), "original table")
        identity = _identity(_need(descriptor, "id"), "current business identity")
        candidates = [
            s for s in current if s.kind == TABLE_KINDS.get(table) and str(s.id) == identity
        ]
        _check(len(candidates) == 1, "Current original has no unique matching audit subject")
        row = bundle.row(_need(descriptor, "ref"), table, identity, scope=candidates[0].scope)
        _check(
            len(candidates) == 1 and digest_value(candidates[0].data) == digest_value(row),
            "Current audit subject differs from its actual business original",
        )
        latest_ids.add((candidates[0].kind, identity))
    _check(len(latest_ids) == len(current), "Current business reference identity is duplicated")
    return {
        "raw_refs": [record["_original_ref"], record["verification_ref"]],
        "verification": actual.model_dump(mode="json"),
        "_events": typed_events,
        "_subjects": subjects,
        "_current": current,
    }


def _audit_action_refs(
    audit: dict[str, Any],
    records: list[tuple[str, dict[str, Any]]],
    action_id: str,
    decision_id: str,
) -> None:
    from app.domain.audit_chain import subject_hash

    relevant = [event for event in audit["_events"] if str(event.action_plan_id) == action_id]
    _check(bool(relevant), "Original action has no actual typed audit event")
    _check(
        any(str(event.decision_run_id) == decision_id for event in relevant),
        "Original audit events do not bind the action's decision",
    )
    for kind, row in records:
        subjects = [
            s
            for s in audit["_subjects"]
            if s.kind == kind
            and str(s.id) == row["id"]
            and digest_value(s.data) == digest_value(row)
        ]
        _check(len(subjects) == 1, "Trace row has no matching full original audit snapshot")
        subject = subjects[0]
        _check(
            any(
                ref.kind == kind
                and str(ref.id) == row["id"]
                and ref.snapshot_hash == subject_hash(subject)
                for event in relevant
                for ref in event.payload.references
            ),
            "Trace row has no exact original audit reference/hash",
        )


def _action_unit(
    bundle: TraceBundle,
    action_id: str,
    record: dict[str, Any] | None,
    requirements: dict[str, dict[str, Any]],
    audits: dict[str, dict[str, Any]],
    a2: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if record is None:
        raise Missing("Actual logical action has no complete original trace record")
    requirement = requirements.get(_text(_need(record, "opportunity_id"), "action opportunity"))
    if requirement is None:
        raise Missing("Actual action has no independently registered trace requirement")
    action = bundle.row(_need(record, "action_ref"), "action_plans", action_id)
    decision = _decision(bundle, _need(record, "decision_ref"))
    _check(action.get("decision_run_id") == decision["id"], "Action/decision identity differs")
    request = _object(_need(action, "request"), "original action request")
    _check(
        digest_value(request) == _digest(_need(action, "request_hash"), "request hash"),
        "Original action request hash differs",
    )
    if "execution" not in request:
        raise Missing("Legacy action trace calculator is not implemented")
    command = _object(request["execution"], "original command")
    effect = dict(_object(_need(command, "effect"), "original economic effect"))
    _check(
        set(command) == {"effect", "effect_hash"} and set(effect) == EFFECT_FIELDS,
        "Full supported economic effect schema is required; omitted defaults are not originals",
    )
    effect_digest = dict(effect)
    for field, keys in (
        ("cash_uses", ("account_id",)),
        ("income_uses", ("origin_transaction_id", "account_id")),
    ):

        def sort_key(item: dict[str, Any], keys: tuple[str, ...] = keys) -> tuple[Any, ...]:
            return tuple(item[key] for key in keys)

        effect_digest[field] = sorted(_list(_need(effect, field), field), key=sort_key)
    versions = _unique_ids(_need(effect, "policy_version_ids"), "effect versions")
    effect_digest["policy_version_ids"] = sorted(versions)
    if effect.get("liability") is not None:
        liability = dict(_object(effect["liability"], "effect liability"))
        liability["evidence_ids"] = sorted(
            _unique_ids(_need(liability, "evidence_ids"), "liability evidence")
        )
        effect_digest["liability"] = liability
    digest = _digest(_need(command, "effect_hash"), "original effect digest")
    _check(
        digest_value(effect_digest) == digest and effect.get("simulation") is True,
        "Original full economic effect hash differs",
    )
    _check(
        effect.get("operation_id") == action_id
        and effect.get("user_id") == bundle.bindings["user_id"]
        and ACTION_TYPES.get(_text(effect.get("action_type"), "effect action type"))
        == action.get("action_type")
        and _integer(effect.get("amount_cents"), "effect amount")
        == _integer(_need(action, "amount_cents"), "action amount")
        > 0,
        "Original economic effect identity/type/amount is rebound",
    )
    for field in ("goal_id", "product_id", "policy_version_id"):
        _check(
            field in effect and effect[field] == action.get(field),
            "Original action subject is rebound",
        )
    _check(
        action.get("destination_account_id")
        == (
            effect.get("destination_account_id")
            if effect.get("destination_account_id") != action.get("source_account_id")
            else None
        ),
        "Original destination identity is rebound",
    )
    cash_uses = [_object(item, "effect cash use") for item in effect["cash_uses"]]
    _check(
        action.get("source_account_id")
        == (cash_uses[0]["account_id"] if cash_uses else effect.get("position_account_id")),
        "Original source account identity is rebound",
    )
    operation = bundle.row(_need(record, "bank_ref"), "bank_operations")
    _check(
        operation.get("action_plan_id") == action_id
        and operation.get("request") == command
        and digest_value(command) == operation.get("request_hash")
        and operation.get("idempotency_key") == action.get("idempotency_key")
        and operation.get("business_key") == effect.get("business_key")
        and operation.get("operation_type") == effect.get("action_type")
        and operation.get("id") == effect.get("operation_id"),
        "Original bank/request/action identity is rebound",
    )
    at = _time(_need(operation, "requested_at"), "bank requested_at")
    created = _time(_need(action, "created_at"), "action created_at")
    decision_at = _time(decision["as_of"], "decision as_of")
    _check(
        bundle.begin <= decision_at <= created <= at <= bundle.end
        and _time(_need(effect, "valid_from"), "effect valid_from")
        <= at
        < _time(_need(effect, "expires_at"), "effect expires_at")
        and _time(_need(operation, "available_at"), "bank available_at") >= at,
        "Original trace chronology is invalid",
    )
    evidence_result = a2.get(
        _text(_need(record, "decision_opportunity_id"), "decision opportunity")
    )
    if evidence_result is None or evidence_result["verified"] is None:
        raise Missing("Action's independently required decision evidence is missing")
    _check(
        evidence_result["verified"] is True
        and evidence_result["decision_run_id"] == decision["id"],
        "Action's original decision evidence failed independent checks",
    )
    policy_refs = _object(_need(record, "version_refs"), "original policy version references")
    _check(
        set(policy_refs)
        == set(versions)
        == set(_unique_ids(_need(decision, "policy_version_ids"), "decision versions")),
        "Action/decision/effect policy reference set differs",
    )
    trace_rows = [
        ("ACTION_PLAN", action),
        ("DECISION_RUN", decision),
        ("BANK_OPERATION", operation),
    ]
    decision_record = next(
        (
            row
            for row in bundle.rows("TRACE_RECORDS", "decisions", "opportunity_id").values()
            if row.get("opportunity_id") == record["decision_opportunity_id"]
        ),
        None,
    )
    if decision_record is None:
        raise Missing("Original action decision-evidence links are unavailable")
    trace_rows.extend(
        ("EVIDENCE", bundle.row(ref, "evidence_items"))
        for ref in _object(_need(decision_record, "evidence_refs"), "evidence refs").values()
    )
    confirmations = _object(_need(record, "policy_consent_refs"), "policy consent originals")
    _check(set(confirmations) == set(versions), "Policy consent original set differs")
    for version_id in versions:
        version = bundle.row(policy_refs[version_id], "policy_versions", version_id)
        configuration = _object(_need(version, "configuration"), "policy configuration")
        _check(
            digest_value(configuration) == version.get("content_hash"),
            "Original policy configuration hash differs",
        )
        consent = bundle.row(confirmations[version_id], "evidence_items")
        content = _object(_need(consent, "content"), "policy confirmation content")
        confirmed = _time(_need(version, "confirmed_at"), "policy confirmed_at")
        _check(
            consent.get("evidence_level") == "USER_CONFIRMED_POLICY"
            and consent.get("source_type") == "POLICY_CONFIRMATION"
            and consent.get("source_ref") == version_id
            and digest_value(content) == consent.get("content_hash")
            and content == version.get("confirmation")
            and content.get("accepted") is True
            and content.get("user_id") == bundle.bindings["user_id"]
            and content.get("version_id") == version_id
            and content.get("policy_id") == version.get("policy_id")
            and content.get("reviewed_hash") == version.get("content_hash")
            and _time(_need(content, "confirmed_at"), "consent time") == confirmed <= decision_at,
            "Original policy consent/hash/identity/time differs",
        )
        _check(
            _time(_need(consent, "observed_at"), "policy consent observed") <= at
            and _time(_need(consent, "valid_from"), "policy consent validity") <= at
            and consent.get("status") == "VALID"
            and (consent.get("valid_to") is None or at < _time(consent["valid_to"], "consent end")),
            "Original policy consent evidence was not timely valid",
        )
        trace_rows += [("POLICY_VERSION", version), ("EVIDENCE", consent)]
    confirmation_mode = _text(_need(requirement, "consent_mode"), "frozen consent mode")
    _check(confirmation_mode in {"ACTION", "POLICY"}, "No registered consent mechanism")
    if confirmation_mode == "POLICY":
        _check(bool(versions), "Policy consent mechanism has no actual original version")
    else:
        proof = bundle.row(_need(record, "action_consent_ref"), "evidence_items")
        content = _object(_need(proof, "content"), "action consent content")
        expected_id = str(uuid5(UUID(action_id), "confirmation:" + digest))
        _check(
            proof["id"] == request.get("confirmation_evidence_id") == expected_id
            and proof.get("evidence_level") == "USER_CONFIRMED_ACTION"
            and proof.get("source_type") == "USER_ACTION_CONFIRMATION"
            and proof.get("source_ref") == action_id
            and proof.get("status") == "VALID"
            and digest_value(content) == proof.get("content_hash")
            and content.get("simulation") is True
            and content.get("accepted") is True
            and content.get("action_id") == action_id
            and content.get("effect_hash") == digest
            and content.get("user_id") == bundle.bindings["user_id"]
            and _time(_need(effect, "valid_from"), "effect start")
            <= _time(_need(content, "confirmed_at"), "action consent time")
            <= _time(_need(proof, "observed_at"), "consent observed_at")
            <= at
            < _time(_need(content, "valid_until"), "consent valid until"),
            "Original exact affirmative action consent is rebound or mistimed",
        )
        _check(
            _time(_need(proof, "valid_from"), "action proof validity") <= at
            and proof.get("valid_to") is not None
            and at < _time(proof["valid_to"], "action proof validity end")
            and _time(_need(action, "authorized_at"), "action authorized_at")
            == _time(content["confirmed_at"], "original confirmed time"),
            "Original action confirmation window/authorization time differs",
        )
        trace_rows.append(("EVIDENCE", proof))
    receipts = _list(_need(record, "receipt_refs"), "actual receipt references")
    if not receipts:
        raise Missing("Actual action has no original bank/application receipt")
    receipt_ids: list[str] = []
    for ref in receipts:
        receipt = bundle.row(ref, "action_receipts")
        _check(
            receipt.get("action_plan_id") == action_id
            and _time(_need(receipt, "occurred_at"), "receipt occurred_at") >= at,
            "Original receipt action/time differs",
        )
        response = _object(_need(receipt, "response"), "original bank response")
        _check(
            response.get("bank_operation_id") == operation["id"],
            "Original receipt refers to another bank operation",
        )
        receipt_ids.append(receipt["id"])
        trace_rows.append(("ACTION_RECEIPT", receipt))
    _check(len(receipt_ids) == len(set(receipt_ids)), "Actual receipt reference duplicated")
    checkpoint_id = _text(_need(record, "audit_checkpoint_id"), "action audit checkpoint")
    audit = audits.get(checkpoint_id)
    if audit is None or audit.get("verified") is None:
        raise Missing("Original action audit checkpoint is incomplete or missing")
    _check(audit["verified"] is True, "Original action audit checkpoint failed integrity")
    _audit_action_refs(audit, trace_rows, action_id, decision["id"])
    return {
        "raw_refs": [
            record["_original_ref"],
            record["action_ref"],
            record["decision_ref"],
            record["bank_ref"],
            *receipts,
            *policy_refs.values(),
            *confirmations.values(),
        ],
        "decision_run_id": decision["id"],
        "receipt_ids": receipt_ids,
    }


def _actual_actions(bundle: TraceBundle, http: dict[str, dict[str, Any]]) -> list[str]:
    snapshots = [
        raw for raw in bundle.present("TRACE_SNAPSHOT") if raw["payload"].get("role") == "FINAL"
    ]
    if len(snapshots) != 1:
        raise Missing("A1 needs one complete final original inventory, including failed actions")
    snapshot = snapshots[0]["payload"]
    coverage = _object(_need(snapshot, "coverage"), "complete final scope")
    _check(
        coverage.get("complete") is True
        and coverage.get("user_id") == bundle.bindings["user_id"]
        and coverage.get("epoch_id") == bundle.bindings["isolated_db_epoch"]
        and set(_unique_ids(_need(coverage, "tables"), "covered tables")) >= set(INVENTORY_TABLES),
        "Final inventory scope/coverage differs",
    )
    _check(
        _time(_need(snapshot, "captured_at"), "final capture") >= bundle.end,
        "Final inventory predates end of attempted run",
    )
    tables = _object(_need(snapshot, "tables"), "final original tables")
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for table in INVENTORY_TABLES:
        rows = [_object(row, table) for row in _list(_need(tables, table), table)]
        ids: list[str] = []
        for row in rows:
            _owner(row, bundle.bindings["user_id"])
            ids.append(_identity(_need(row, "id"), "actual row id"))
        _check(len(ids) == len(set(ids)), "Final inventory contains duplicate original rows")
        indexes[table] = dict(zip(ids, rows, strict=True))
    http_inventory = _unique_ids(_need(coverage, "execution_http_ids"), "actual execution HTTP ids")
    actual_http = {
        identity for identity, row in http.items() if row.get("operation") == "EXECUTE_ACTION"
    }
    _check(set(http_inventory) == actual_http, "Execution HTTP original inventory is incomplete")
    actions = {
        row["action_plan_id"] for table in INVENTORY_TABLES[1:] for row in indexes[table].values()
    }
    actions |= {
        _identity(_need(http[identity], "action_id"), "HTTP action") for identity in http_inventory
    }
    actions |= {
        row["id"]
        for row in indexes["action_plans"].values()
        if row.get("status") in {"SUBMITTED", "SUCCEEDED", "FAILED", "UNKNOWN", "RECONCILED"}
    }
    # Missing action rows remain actual-action denominator entries, never disappear.
    return sorted(_identity(value, "actual logical action") for value in actions)


def _native_result(bundle: TraceBundle, ref: Any) -> dict[str, Any]:
    _check(bundle.v2, "Native failure transport requires explicit observation V2")
    _check(
        _object(ref, "native result ref").get("json_pointer") == "/payload/result",
        "Native result must be the complete original returned object",
    )
    result = _object(bundle.resolve(ref, kind="SCENARIO_RESULT"), "actual original Scenario result")
    return _validate_native_result(bundle, result)


def _conditional_result(bundle: TraceBundle, ref: Any) -> dict[str, Any]:
    """Whole actual conditional run; no conversion to a ScenarioResult prefix."""
    _check(bundle.v2, "Conditional service originals require observation V2")
    _check(_object(ref, "conditional run locator").get("json_pointer") == "/payload/result",
           "Conditional run must be the complete original object")
    result = _object(bundle.resolve(ref, kind="CONDITIONAL_SERVICE_RUN"), "actual conditional run")
    native = bundle.frozen_view.case_binding["native_execution_binding"]
    schedule = _object(_need(native, "authored_schedule_binding"), "whole frozen author/control binding")
    _check(result.get("protocol") == "bounded-funds-frozen-conditional-run-result-v1"
           and result.get("bindings") == bundle.bindings
           and result.get("status") == "ACTUAL_CONDITIONAL_SERVICE_OBSERVATIONS_NOT_ECONOMIC_ACCEPTANCE"
           and result.get("original_case_input_sha256") == bundle.bindings["input_sha256"]
           and result.get("validated_execution_input_sha256") == native["validated_execution_input_sha256"]
           and result.get("complete_authored_input_ref") == schedule["complete_authored_input_ref"]
           and result.get("all_opportunities_retained") is True
           and result.get("financial_effect_evidence") is False
           and result.get("independent_metrics_verified") is False
           and result.get("is_scenario_result_prefix") is False,
           "Conditional original identities/input/control/false flags differ")
    registration = bundle.frozen_view.captured_original(_object(
        _need(result, "trusted_schedule_registration_ref"), "actual trusted schedule registration"))
    _check(registration.get("protocol") == "bounded-funds-trusted-frozen-schedule-registration-v1"
           and registration.get("bindings") == bundle.bindings
           and registration.get("manifest_sha256") == bundle.frozen_view.archive_sha256
           and registration.get("typed_execution_sha256") == native["validated_execution_input_sha256"]
           and registration.get("source_inventory_sha256")
           == digest_value(bundle.frozen_view.archive["source_inventory"]),
           "Actual conditional registration differs from the frozen originals")
    database = _text(_need(registration, "database_name"), "actual isolated conditional database")
    _check(re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None,
           "Conditional run database is not a generated isolated test database")
    bundle.frozen_view.source_originals({"scripts/mvp_frozen_schedule_runtime.py",
                                         "scripts/mvp_schedule_control.py"})
    declared = native["validated_execution_input"]["steps"]
    nodes = [row for row in schedule["control_schedule"]["nodes"] if "step_id" in row]
    rows = [_object(row, "actual conditional step") for row in _list(_need(result, "steps"), "steps")]
    refs = [_object(row, "actual conditional step byte locator")
            for row in _list(_need(result, "step_original_refs"), "step byte originals")]
    _check(len(rows) == len(refs) == len(declared) == len(nodes),
           "Conditional run dropped an original step/opportunity")
    prior_ids: set[str] = set()
    prior_end = 0
    for row, original, node, descriptor in zip(rows, declared, nodes, refs, strict=True):
        identity = _text(_need(row, "step_id"), "actual conditional step id")
        _check(identity not in prior_ids and identity == original["step_id"] == node["step_id"]
               == descriptor.get("step_id") and row.get("kind") == original["kind"]
               and _time(_need(row, "at"), "actual conditional clock")
               == _time(original["at"], "frozen conditional clock"),
               "Conditional step order/identity/kind/clock differs")
        _check(bundle.frozen_view.captured_original(_object(_need(descriptor, "ref"),
               "actual original step bytes")) == row,
               "Conditional step differs from its original complete byte capture")
        dispatch = _object(_need(row, "dispatch"), "actual original branch")
        _check(dispatch.get("protocol") == "bounded-funds-schedule-dispatch-decision-v1"
               and dispatch.get("opportunity_denominator_retained") is True
               and dispatch.get("financial_permission_verified") is False
               and dispatch.get("financial_effect_evidence") is False
               and dispatch.get("original_service_authorization_still_required") is True
               and row.get("financial_effect_evidence") is False,
               "Conditional branch cannot claim permission or remove denominator")
        dependencies = _unique_ids(_need(dispatch, "required_original_step_ids"), "branch source identities")
        _check(set(dependencies) <= prior_ids, "Conditional branch has a future/unknown original source")
        _text(_need(dispatch, "reason"), "actual branch reason")
        sql = _object(_need(row, "actual_sql_context"), "actual original SQL context")
        _check(sql.get("database_name") == database and sql.get("user_id") == bundle.bindings["user_id"]
               and sql.get("isolated_db_epoch") == bundle.bindings["isolated_db_epoch"]
               and sql.get("isolation_level") == "REPEATABLE READ" and sql.get("read_only") is True
               and _integer(_need(sql, "backend_pid"), "actual SQL backend pid") > 0,
               "Original conditional SQL owner/epoch/transaction differs")
        timing = _object(_need(row, "actual_timing"), "actual original monotonic timing")
        start, end = _integer(timing.get("start_ns"), "step start"), _integer(timing.get("end_ns"), "step end")
        _check(timing.get("clock") == "perf_counter_ns" and prior_end <= start <= end
               and _integer(timing.get("duration_ns"), "step duration") == end - start,
               "Conditional actual timing is missing/overlapping/inconsistent")
        prior_end = end
        outcomes = [key for key in ("result", "error", "skip") if key in row]
        _check(len(outcomes) == 1, "Conditional step has no original outcome or conflicting outcomes")
        if dispatch.get("status") != "DISPATCH_ORIGINAL_SERVICE":
            _check(outcomes == ["skip"] and row["skip"] == dispatch,
                   "A nondispatched branch invented a service result")
        if "error" in row:
            problem = _object(row["error"], "actual original conditional error")
            _check(_integer(_need(problem, "status_code"), "actual business refusal") >= 400,
                   "Conditional error is not an actual business rejection")
            _text(_need(problem, "code"), "actual conditional error code")
        prior_ids.add(identity)
    return result


def _validate_native_result(bundle: TraceBundle, result: dict[str, Any]) -> dict[str, Any]:
    """Check original Scenario identities, ordered steps and declared clocks."""
    _check(bundle.v2, "Native result validation requires explicit observation V2")
    _check(
        result.get("protocol") == "bounded-funds-scenario-v1"
        and result.get("scenario_id") == bundle.bindings["case_id"]
        and result.get("purpose") == bundle.bindings["purpose"]
        and result.get("input_sha256")
        == bundle.frozen_view.case_binding["native_execution_binding"][
            "validated_execution_input_sha256"
        ]
        and result.get("status") in {"EXECUTED", "FAILED", "PROPERTY_FAILED"},
        "Actual native Scenario result identity/purpose/typed input differs",
    )
    rows = [
        _object(row, "actual executed native step")
        for row in _list(_need(result, "steps"), "original native steps")
    ]
    ids = [_text(_need(row, "step_id"), "actual native step id") for row in rows]
    _check(len(ids) == len(set(ids)), "Actual native step identity is duplicated")
    declared = bundle.frozen_view.case_binding["native_execution_binding"][
        "validated_execution_input"
    ]["steps"]
    _check(len(rows) <= len(declared), "Native result contains undeclared executed steps")
    for actual, original in zip(rows, declared, strict=False):
        _check(
            actual.get("step_id") == original.get("step_id")
            and actual.get("kind") == original.get("kind")
            and _time(_need(actual, "at"), "actual native clock")
            == _time(_need(original, "at"), "frozen native clock"),
            "Native executed step order/kind/clock differs from frozen input",
        )
    return result


def _failure_transport(
    bundle: TraceBundle,
    expected: dict[str, Any],
    record: dict[str, Any],
    http: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], datetime, bool]:
    channel = record.get("failure_channel", "HTTP")
    if channel == "SCENARIO_STEP":
        result = _native_result(bundle, _need(record, "scenario_result_ref"))
        descriptor = _object(_need(record, "service_step_ref"), "actual native failed step locator")
        index = _integer(_need(record, "step_index"), "actual native failed step index")
        parent = _object(record["scenario_result_ref"], "native result locator")
        _check(
            descriptor.get("artifact_sha256") == parent.get("artifact_sha256")
            and descriptor.get("json_pointer")
            == parent.get("json_pointer", "") + "/steps/" + str(index),
            "Native failed step locator is not its original result row",
        )
        step = _object(
            bundle.resolve(descriptor, kind="SCENARIO_RESULT"), "original native failed step"
        )
        _check(
            index < len(result["steps"]) and step == result["steps"][index],
            "Native failed step index differs",
        )
        problem = _object(_need(step, "error"), "actual native business error")
        _check(
            _integer(_need(problem, "status_code"), "native business error status") >= 400,
            "Native result has no actual failed business step",
        )
        at = _time(_need(step, "at"), "actual native step clock")
        _check(bundle.begin <= at <= bundle.end, "Native failure is outside the frozen run window")
        causal_ids = _unique_ids(_need(expected, "causal_step_ids"), "frozen native causal steps")
        _check(step["step_id"] in causal_ids, "Native failed step is outside frozen causal scope")
        related = [
            (offset, row)
            for offset, row in enumerate(result["steps"])
            if row["step_id"] in causal_ids and isinstance(row.get("error"), dict)
        ]
        _check(
            bool(related) and related[0][0] == index,
            "Native failure is not its first actual causal error",
        )
        # Private inspection view, never a fabricated HTTP raw original.
        return (
            {
                "exchange_id": None,
                "step_id": step["step_id"],
                "user_id": bundle.bindings["user_id"],
                "response_body": {
                    "error_code": _text(_need(problem, "code"), "actual native error code")
                },
                "business_status_code": problem["status_code"],
            },
            at,
            True,
        )
    _check(channel == "HTTP", "Unsupported actual failure transport")
    exchange = _object(
        bundle.resolve(_need(record, "http_ref"), kind="HTTP_EXCHANGES"),
        "original failed HTTP exchange",
    )
    identity = _text(_need(exchange, "exchange_id"), "failure exchange id")
    _check(
        identity in http and http[identity]["_original_ref"] == record["http_ref"],
        "Failure HTTP reference is rebound",
    )
    at = _time(_need(exchange, "started_at"), "failed request start")
    _check(
        bundle.begin
        <= at
        <= _time(_need(exchange, "finished_at"), "failed response")
        <= bundle.end,
        "Failure HTTP clocks are outside this run",
    )
    status = _integer(_need(exchange, "status_code"), "HTTP code")
    if status < 400:
        _check(bundle.v2 and 200 <= status < 300, "Failure is not an original failed HTTP exchange")
        parent = _object(record["http_ref"], "business HTTP exchange locator")
        descriptor = _object(
            _need(record, "business_step_ref"), "actual HTTP200 business failed step"
        )
        index = _integer(_need(record, "step_index"), "actual HTTP200 failed step index")
        _check(
            descriptor.get("artifact_sha256") == parent.get("artifact_sha256")
            and descriptor.get("json_pointer")
            == parent.get("json_pointer", "") + "/response_body/steps/" + str(index),
            "HTTP200 business error locator is rebound",
        )
        result = _object(_need(exchange, "response_body"), "original HTTP200 Scenario response")
        _validate_native_result(bundle, result)
        step = _object(
            bundle.resolve(descriptor, kind="HTTP_EXCHANGES"), "actual HTTP200 native step"
        )
        _check(
            index < len(_list(_need(result, "steps"), "actual HTTP200 native steps"))
            and result["steps"][index] == step
            and step.get("step_id") == exchange.get("step_id"),
            "HTTP200 failed native step identity differs",
        )
        problem = _object(_need(step, "error"), "original HTTP200 business error")
        _check(
            _integer(_need(problem, "status_code"), "HTTP200 business status") >= 400,
            "HTTP200 response has no original business rejection",
        )
        causal_ids = _unique_ids(_need(expected, "causal_step_ids"), "HTTP200 causal steps")
        related = [
            (offset, row)
            for offset, row in enumerate(result["steps"])
            if row["step_id"] in causal_ids and isinstance(row.get("error"), dict)
        ]
        _check(
            bool(related) and related[0][0] == index,
            "HTTP200 failure is not its first actual causal error",
        )
        exchange = dict(
            exchange,
            response_body={
                "error_code": _text(_need(problem, "code"), "actual HTTP200 business code")
            },
            business_status_code=problem["status_code"],
        )
    return exchange, at, False


def _failure_unit(
    bundle: TraceBundle,
    expected: dict[str, Any] | None,
    record: dict[str, Any] | None,
    http: dict[str, dict[str, Any]],
    audits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if record is None:
        raise Missing("Predeclared failure has no original error/cause observation")
    if expected is None:
        raise Missing("Unexpected actual failure has no frozen independent causal oracle")
    if record.get("failure_channel") == "CONDITIONAL_SERVICE_STEP":
        return _conditional_failure_unit(bundle, expected, record, audits)
    exchange, at, native = _failure_transport(bundle, expected, record, http)
    exchange_id = exchange.get("exchange_id")
    _owner(exchange, bundle.bindings["user_id"])
    error = _object(
        bundle.resolve(_need(record, "error_ref"), kind="ERROR_RECORDS"), "original error record"
    )
    _owner(error, bundle.bindings["user_id"])
    _check(
        error.get("exchange_id") == exchange_id
        and error.get("failure_id") == record.get("failure_id")
        and error.get("step_id") == exchange.get("step_id")
        and error.get("error_code") == record.get("error_code") == expected.get("error_code")
        and error.get("cause_code") == record.get("cause_code") == expected.get("cause_code")
        and _time(_need(error, "occurred_at"), "original error time") >= at,
        "Actual failure/code/cause does not identify frozen expected cause",
    )
    response = _object(_need(exchange, "response_body"), "original error response")
    _check(
        response.get("error_code") == error["error_code"],
        "Actual HTTP error response does not identify original error code",
    )
    step_id = _text(_need(exchange, "step_id"), "actual failed step")
    _check(
        step_id == record.get("first_failing_step") == expected.get("first_failing_step"),
        "Failure localization points to another first failed step",
    )
    cause_key = _text(_need(error, "logical_cause_key"), "actual logical cause")
    related = [
        row
        for row in http.values()
        if row.get("logical_cause_key") == cause_key
        and type(row.get("status_code")) is int
        and (
            row["status_code"] >= 400
            or (
                bundle.v2
                and isinstance(row.get("response_body"), dict)
                and any(
                    isinstance(step.get("error"), dict)
                    for step in row["response_body"].get("steps", [])
                    if isinstance(step, dict)
                )
            )
        )
    ]
    _check(
        native
        or (
            bool(related)
            and min(related, key=lambda row: _integer(_need(row, "sequence"), "HTTP sequence"))[
                "step_id"
            ]
            == step_id
        ),
        "Recorded failure does not identify earliest actual causal error",
    )
    source = _object(_need(error, "source_ref"), "original error source")
    _check(
        source == _need(record, "source_ref") == _need(expected, "source_ref"),
        "Failure source differs from independent causal source",
    )
    source_path = _text(_need(source, "original_path"), "source path")
    descriptor = {
        "path": _text(_need(source, "path"), "archived source path"),
        "sha256": _digest(_need(source, "sha256"), "causal source hash"),
    }
    if bundle.v2:
        _check(
            descriptor["path"] == source_path,
            "V2 causal source must retain its original current path",
        )
        refs = bundle.frozen_view.source_originals({source_path})
        _check(
            refs[0]["sha256"] == descriptor["sha256"],
            "V2 causal source differs from actual frozen source",
        )
        original = bundle.frozen_view.read(Path(refs[0]["path"]), descriptor["sha256"]).decode(
            "utf-8"
        )
    else:
        _check(
            descriptor in bundle.registrations["source"]["files"],
            "Causal source has no source-bound original",
        )
        original = bundle._load_ref(descriptor, parse=False).decode("utf-8")
    first = _integer(_need(source, "line"), "original causal line")
    lines = original.splitlines()
    _check(
        0 < first <= len(lines)
        and lines[first - 1].strip()
        == _text(_need(source, "line_text"), "causal source text").strip()
        and _text(_need(source, "symbol"), "causal symbol") in original,
        "Causal source line/symbol is not present in original source bytes",
    )
    _check(source_path.endswith(".py"), "Causal source contract supports Python originals only")
    functions = [
        node
        for node in ast.walk(ast.parse(original))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == source["symbol"]
    ]
    _check(
        len(functions) == 1
        and functions[0].lineno <= first <= (functions[0].end_lineno or functions[0].lineno),
        "Causal source line is not inside the independently registered Python symbol",
    )
    trace = _object(
        bundle.resolve(_need(record, "trace_ref"), kind="ERROR_RECORDS"), "original causal trace"
    )
    _check(
        trace.get("failure_id") == record["failure_id"]
        and trace.get("step_id") == step_id
        and trace.get("logical_cause_key") == cause_key
        and trace.get("source_ref") == source
        and trace.get("error_code") == error["error_code"]
        and trace.get("cause_code") == error["cause_code"],
        "Causal trace does not bind original failure/source/code",
    )
    frames = _list(_need(trace, "frames"), "original error stack frames")
    _check(
        bool(frames) and frames[-1] == source, "Original causal stack leaf is not expected source"
    )
    _check(
        record.get("logical_cause_key") == error.get("logical_cause_key")
        and (native or error.get("logical_cause_key") == exchange.get("logical_cause_key")),
        "Actual logical cause is rebound",
    )
    _check(
        cause_key == _need(expected, "logical_cause_key"),
        "Actual failure belongs to another independently frozen logical cause",
    )
    checkpoint_id = _text(_need(record, "audit_checkpoint_id"), "failure audit checkpoint")
    audit = audits.get(checkpoint_id)
    if audit is None or audit.get("verified") is None:
        raise Missing("Failure's complete original audit run reference is unavailable")
    _check(audit["verified"] is True, "Failure's original audit checkpoint failed integrity")
    run_ref = _object(_need(record, "run_ref"), "exact experiment run reference")
    _check(
        run_ref == bundle.bindings
        and error.get("run_ref") == run_ref
        and trace.get("run_ref") == run_ref,
        "Failure's original audit/run cause reference does not identify expected cause",
    )
    audit_required = expected.get("audit_cause_required", True)
    _check(type(audit_required) is bool, "Audit causal requirement must be a frozen strict bool")
    if audit_required:
        event_id = _identity(_need(record, "audit_event_id"), "causal audit event")
        events = [event for event in audit["_events"] if str(event.id) == event_id]
        _check(
            len(events) == 1
            and events[0].payload.context.reason_code == error["cause_code"]
            and events[0].payload.context.cause_ref == record["failure_id"],
            "Failure lacks its original causal audit event",
        )
    else:
        _check(
            bundle.v2
            and error["error_code"] in {"INVALID_SCENARIO_STEP", "INVALID_SCENARIO_INPUT"}
            and exchange.get("business_status_code", exchange.get("status_code")) == 422
            and record.get("audit_event_id") is None,
            "Only V2 original DTO/input refusals can explicitly have no causal audit event",
        )
    return {
        "raw_refs": [
            record["_original_ref"],
            record.get("http_ref") if not native else record["service_step_ref"],
            record["error_ref"],
            record["trace_ref"],
        ],
        "failure_id": record["failure_id"],
        "logical_cause_key": cause_key,
        "expected_cause": expected["cause_code"],
    }


def _conditional_failure_unit(bundle: TraceBundle, expected: dict[str, Any],
                              record: dict[str, Any], audits: dict[str, Any]) -> dict[str, Any]:
    _check(expected.get("localization_contract") == "bounded-funds-conditional-stack-localization-v1",
           "Conditional localization needs its explicit frozen independent contract")
    result = _conditional_result(bundle, _need(record, "conditional_run_ref"))
    index = _integer(_need(record, "step_index"), "actual conditional failed step index")
    _check(index < len(result["steps"]), "Conditional failed step index is absent")
    step = result["steps"][index]
    _check(step["step_id"] == expected.get("first_failing_step") == record.get("first_failing_step"),
           "Conditional localization selects a different actual step")
    at = _time(step["at"], "actual conditional failure clock")
    _check(bundle.begin <= at <= bundle.end, "Conditional failure is outside its registered window")
    causal = _unique_ids(_need(expected, "causal_step_ids"), "frozen conditional causal steps")
    _check(set(causal) <= {row["step_id"] for row in result["steps"]}, "Unknown frozen causal step")
    errors = [(offset, row) for offset, row in enumerate(result["steps"])
              if row["step_id"] in causal and "error" in row]
    _check(bool(errors) and errors[0][0] == index, "Conditional localization is not the first actual causal error")
    original = _object(bundle.resolve(_need(record, "error_ref"), kind="ERROR_RECORDS"),
                       "actual conditional error capture")
    _owner(original, bundle.bindings["user_id"])
    _check(original.get("failure_channel") == "CONDITIONAL_SERVICE_STEP"
           and original.get("failure_id") == record.get("failure_id") == step["step_id"]
           and original.get("step_id") == step["step_id"] and original.get("step_index") == index
           and original.get("independent_causal_oracle_verified") is False
           and _time(original.get("occurred_at"), "actual error capture clock") == at
           and original.get("step_ref") == result["step_original_refs"][index]["ref"]
           and original["conditional_run_ref"].get("sha256")
           == record["conditional_run_ref"]["artifact_sha256"]
           and original.get("error") == step.get("error"),
           "Conditional error capture does not bind its original run/step/outcome")
    problem = _object(_need(step, "error"), "actual original exception")
    _check(problem.get("code") == expected.get("error_code")
           and problem.get("exception_type") == _text(_need(expected, "exception_type"), "frozen exception type")
           and problem.get("independent_causal_oracle_verified") is False,
           "Actual conditional exception differs from its independent frozen expectation")
    frames = _list(_need(problem, "frames"), "actual original traceback frames")
    _check(bool(frames), "Actual conditional exception has no original traceback")
    leaf = _object(frames[-1], "actual last traceback frame")
    _check(leaf.get("is_registered_source") is True, "Actual traceback leaf source is not registered")
    names = ("original_path", "sha256", "line", "line_text", "symbol")
    source = {name: _need(leaf, name) for name in names}
    source["path"] = source["original_path"]
    leaf_path = Path(_text(_need(leaf, "path"), "actual original stack path"))
    current_root = bundle.frozen_view.source_root
    _check((leaf_path if leaf_path.is_absolute() else current_root / leaf_path).resolve()
           == (current_root / source["original_path"]).resolve(),
           "Actual leaf path and registered relative source differ")
    captured_leaf = _object(_need(problem, "stack_leaf_source_ref"), "actual recorded last frame")
    _check(all(captured_leaf.get(name) == source[name] for name in names)
           and captured_leaf.get("path") in {leaf["path"], source["path"]},
           "Recorded stack leaf is not the actual last traceback frame")
    _check(source == _need(expected, "source_ref"), "Actual exception leaf differs from frozen source cause")
    descriptor = bundle.frozen_view.source_originals({source["original_path"]})[0]
    _check(descriptor["sha256"] == source["sha256"], "Actual leaf source SHA differs from the freeze")
    raw = bundle.frozen_view.read(Path(descriptor["path"]), descriptor["sha256"]).decode("utf-8")
    lines = raw.splitlines()
    line = _integer(source["line"], "actual stack line")
    _check(0 < line <= len(lines) and lines[line - 1] == source["line_text"], "Actual source stack text differs")
    symbols = [node for node in ast.walk(ast.parse(raw))
               if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
               and node.name == source["symbol"]
               and node.lineno <= line <= (node.end_lineno or node.lineno)]
    _check(len(symbols) == 1, "Actual stack line is outside its original Python symbol")
    text = problem.get("formatted_traceback")
    _check(type(text) is str and 0 < len(text.encode("utf-8")) <= 16 * 1024 * 1024
           and ("in " + source["symbol"]) in text
           and source["line_text"].strip() in text,
           "Actual formatted traceback does not contain its original leaf")
    checkpoint = _text(_need(record, "audit_checkpoint_id"), "actual failure audit checkpoint")
    _check(checkpoint in audits and audits[checkpoint].get("verified") is True,
           "Conditional failure lacks its complete verified original audit run")
    _check(record.get("run_ref") == bundle.bindings, "Conditional failure run reference is rebound")
    # This labels the exact original code/stack and step. It makes no independent
    # claim that an exception wrapper's code is the root financial cause.
    return {"failure_id": record["failure_id"], "first_failing_step": step["step_id"],
            "actual_exception_code": problem["code"], "exception_type": problem["exception_type"],
            "source_ref": source, "financial_cause_status": "MISSING",
            "financial_cause_missing_reason": "No counterfactual financial attribution is inferred from a stack",
            "raw_refs": [record["_original_ref"], record["conditional_run_ref"], record["error_ref"],
                         result["step_original_refs"][index]["ref"]]}


def _failure_capture(
    bundle: TraceBundle, http: dict[str, dict[str, Any]], errors: dict[str, dict[str, Any]]
) -> None:
    conditional = bundle.present("CONDITIONAL_SERVICE_RUN") if bundle.v2 else []
    if conditional:
        _check(len(conditional) == 1 and not bundle.present("SCENARIO_RESULT"),
               "One original conditional run is required without a fake ScenarioResult")
        original = conditional[0]
        result = _conditional_result(bundle, {"artifact_sha256": original["ref"]["sha256"],
            "json_pointer": "/payload/result", "value_sha256": digest_value(original["payload"]["result"])})
        actual = {row["step_id"] for row in result["steps"] if "error" in row}
        captured = [raw for raw in bundle.present("ERROR_RECORDS")
                    if "complete_actual_step_order" in raw["payload"]]
        _check(len(captured) == 1, "Complete actual conditional error inventory is missing")
        payload = captured[0]["payload"]
        _check(payload.get("complete_actual_step_order") == [row["step_id"] for row in result["steps"]]
               and payload["conditional_run_ref"].get("sha256") == original["ref"]["sha256"]
               and payload.get("independent_causal_oracle_verified") is False
               and {row["step_id"] for row in errors.values()
                    if row.get("failure_channel") == "CONDITIONAL_SERVICE_STEP"} == actual,
               "Conditional error denominator/order differs from actual complete run")
        for row in errors.values():
            if row.get("failure_channel") != "CONDITIONAL_SERVICE_STEP":
                continue
            index = _integer(_need(row, "step_index"), "actual error step index")
            _check(index < len(result["steps"]) and row.get("step_id") == result["steps"][index]["step_id"]
                   and row.get("error") == result["steps"][index].get("error")
                   and row.get("step_ref") == result["step_original_refs"][index]["ref"],
                   "Original conditional error inventory has an altered/duplicate cause")
        if not http:
            _check(all(row.get("failure_channel") == "CONDITIONAL_SERVICE_STEP" for row in errors.values()),
                   "Nonconditional errors lack actual HTTP capture")
            return
    native_raw = bundle.present("SCENARIO_RESULT") if bundle.v2 else []
    if native_raw:
        _check(len(native_raw) == 1, "One complete original Scenario result is required")
        original = native_raw[0]
        capture = _object(_need(original["payload"], "capture"), "actual native error capture")
        result = _native_result(
            bundle,
            {
                "artifact_sha256": original["ref"]["sha256"],
                "json_pointer": "/payload/result",
                "value_sha256": digest_value(_need(original["payload"], "result")),
            },
        )
        failed = {row["step_id"] for row in result["steps"] if isinstance(row.get("error"), dict)}
        native_errors = {
            row["step_id"]
            for row in errors.values()
            if row.get("failure_channel") == "SCENARIO_STEP"
        }
        _check(
            capture.get("complete") is True
            and capture.get("run_ref") == bundle.bindings
            and set(_unique_ids(_need(capture, "failed_step_ids"), "actual native failure ids"))
            == failed
            == native_errors,
            "Actual native failure inventory is missing/incomplete",
        )
        _check(
            set(_unique_ids(_need(capture, "error_record_ids"), "actual native error records"))
            == {
                identity
                for identity, row in errors.items()
                if row.get("failure_channel") == "SCENARIO_STEP"
            },
            "Native error original denominator differs",
        )
        if not http:
            _check(
                all(row.get("failure_channel") == "SCENARIO_STEP" for row in errors.values()),
                "HTTP errors exist without actual HTTP capture",
            )
            return
    captures = [
        _object(raw["payload"].get("capture"), "original HTTP/error capture")
        for raw in bundle.present("HTTP_EXCHANGES")
    ]
    if len(captures) != 1:
        raise Missing("Complete actual HTTP/error inventory was not captured")
    capture = captures[0]
    _check(
        capture.get("complete") is True and capture.get("run_ref") == bundle.bindings,
        "HTTP/error capture is incomplete or rebound",
    )
    _check(
        set(_unique_ids(_need(capture, "exchange_ids"), "captured HTTP ids")) == set(http)
        and set(_unique_ids(_need(capture, "error_record_ids"), "captured error ids"))
        == set(errors),
        "Actual HTTP/error original inventory is incomplete",
    )


def _refs(record: dict[str, Any] | None) -> list[Any]:
    if record is None:
        return []
    return [
        value
        for key, value in record.items()
        if key == "_original_ref" or key.endswith("_ref") or key.endswith("_refs")
    ]


def observe(manifest_path: Path) -> dict[str, Any]:
    bundle = TraceBundle(manifest_path)
    decisions = bundle.rows("TRACE_RECORDS", "decisions", "opportunity_id")
    action_records = bundle.rows("TRACE_RECORDS", "actions", "action_id")
    failures = bundle.rows("TRACE_RECORDS", "failures", "failure_id")
    audit_records = bundle.rows("AUDIT_ORIGINALS", "checkpoints", "checkpoint_id")
    http = bundle.rows("HTTP_EXCHANGES", "exchanges", "exchange_id")
    errors = bundle.rows("ERROR_RECORDS", "errors", "failure_id")
    decision_units = [
        _result(
            expected["opportunity_id"],
            lambda expected=expected: _a2_unit(
                bundle, expected, decisions.get(expected["opportunity_id"])
            ),
            refs=_refs(decisions.get(expected["opportunity_id"])),
        )
        for expected in bundle.contract["decision_opportunities"]
    ]
    a2 = {unit["unit_id"]: unit for unit in decision_units}
    audit_units = []
    for expected in bundle.contract["audit_checkpoints"]:
        checkpoint_id = expected["checkpoint_id"]

        def audit_operation(checkpoint_id: str = checkpoint_id) -> dict[str, Any]:
            if checkpoint_id not in audit_records:
                raise Missing("Registered audit checkpoint originals were not captured")
            return _audit(bundle, audit_records[checkpoint_id])

        audit_units.append(
            _result(checkpoint_id, audit_operation, refs=_refs(audit_records.get(checkpoint_id)))
        )
    audits = {unit["unit_id"]: unit for unit in audit_units}
    inventory_error = None
    try:
        action_ids = _actual_actions(bundle, http)
        _check(set(action_records) <= set(action_ids), "Trace claims a nonactual action")
    except (Missing, ObservationError, KeyError, ValueError) as error:
        inventory_error = str(error)
        action_ids = sorted(action_records)
    requirements = {row["opportunity_id"]: row for row in bundle.contract["action_requirements"]}
    action_units = [
        _result(
            identity,
            lambda identity=identity: _action_unit(
                bundle, identity, action_records.get(identity), requirements, audits, a2
            ),
            refs=_refs(action_records.get(identity)),
        )
        for identity in action_ids
    ]
    failure_units = []
    claimed_causes: set[str] = set()
    for expected in bundle.contract["failure_opportunities"]:
        opportunity_id = expected["opportunity_id"]
        matches = [
            record for record in failures.values() if record.get("opportunity_id") == opportunity_id
        ]
        _check(len(matches) <= 1, "A frozen failure opportunity has multiple actual causes")
        record = matches[0] if matches else None
        registered_cause = _text(_need(expected, "logical_cause_key"), "frozen logical cause")
        _check(registered_cause not in claimed_causes, "Frozen logical cause counted twice")
        claimed_causes.add(registered_cause)
        failure_units.append(
            _result(
                opportunity_id,
                lambda expected=expected, record=record: _failure_unit(
                    bundle, expected, record, http, audits
                ),
                refs=_refs(record),
            )
        )
    # Unreported actual HTTP failures also stay visible; retries count once by original causal key.
    actual_causes = {
        _text(_need(row, "logical_cause_key"), "actual HTTP failure cause")
        for row in http.values()
        if type(row.get("status_code")) is int and row["status_code"] >= 400
    }
    actual_causes |= {
        _text(_need(row, "logical_cause_key"), "actual reported failure cause")
        for row in failures.values()
    }
    actual_causes |= {
        _text(_need(row, "logical_cause_key"), "actual error cause") for row in errors.values()
    }
    for cause in sorted(actual_causes - claimed_causes):
        failure_units.append(
            _result(
                "UNEXPECTED:" + cause,
                lambda: _failure_unit(bundle, None, {}, http, audits),
                refs=[
                    row["_original_ref"]
                    for row in list(errors.values()) + list(http.values())
                    if row.get("logical_cause_key") == cause
                ],
            )
        )
    capture_missing = None
    try:
        _failure_capture(bundle, http, errors)
    except (Missing, ObservationError) as error:
        capture_missing = str(error)
    # Remove in-process typed verifier objects from the serializable evidence record.
    public_audits = [
        {key: value for key, value in unit.items() if not key.startswith("_")}
        for unit in audit_units
    ]
    metrics = {
        "A1": _rate("A1", action_units, missing=inventory_error, run_status=bundle.run_status),
        "A2": _rate("A2", decision_units, run_status=bundle.run_status),
        "A3": _rate("A3", public_audits, run_status=bundle.run_status),
        "A4": _rate("A4", failure_units, missing=capture_missing, run_status=bundle.run_status),
    }
    bundle.unchanged()
    return {
        "protocol": PROTOCOL,
        "bindings": bundle.bindings,
        "run_status": bundle.run_status,
        "evidence_scope": "PARTIAL_AUDIT_TRACE_OBSERVATIONS_ONLY",
        "financial_effect_evidence": False,
        "metrics": metrics,
        "missing_originals": bundle.missing_originals,
        "source_reads": {
            path.relative_to(ROOT).as_posix(): digest
            for path, digest in bundle.current_source_hashes.items()
        },
        "uncovered": [
            "S1--S5 and E1--E5 are outside this calculator",
            "No experiment adapter or real case/arm observations supplied here",
            "Causal source attribution must be recorded by the actual adapter",
            "Legacy action trace and absent independent oracle remain MISSING",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = observe(args.run)
        with args.output.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(result, target, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            target.write("\n")
    except (ObservationError, OSError, ValueError) as error:
        parser.error(str(error))
    print("PARTIAL_AUDIT_TRACE_OBSERVATIONS_ONLY; financial_effect_evidence=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
