"""Read bound original experiment files; never execute or authorize financial actions.

The independent integer ledger diagnostics are not safety-metric calculators. Only
E2 (recorded synthetic actor events) and E5 (recorded monotonic timing) are supplied.
Unsupported or incomplete measurements stay null. TOOL_TEST_ONLY cannot become
financial-effect evidence. No application financial evaluator is imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

RUN_PROTOCOL = "mvp-observation-run-v1"
RAW_PROTOCOL = "mvp-raw-observation-v1"
REG_PROTOCOL = "mvp-observation-registration-v1"
OUTPUT_PROTOCOL = "mvp-observations-v1"
BINDINGS = (
    "experiment_run_id",
    "case_id",
    "arm_id",
    "execution_mode",
    "input_sha256",
    "oracle_sha256",
    "design_sha256",
    "rule_sha256",
    "source_sha256",
    "seed_version",
    "isolated_db_epoch",
    "purpose",
    "user_id",
)
METRIC_IDS = ("S1", "S2", "S3", "S4", "S5", "E1", "E2", "E3", "E4", "E5", "A1", "A2", "A3", "A4")
MAX_INT = 2**63 - 1
POSTING_FIELDS = (
    "id",
    "created_at",
    "user_id",
    "ledger_key",
    "ledger_dimension",
    "ledger_metadata",
    "account_id",
    "position_id",
    "redemption_id",
    "operation_id",
    "external_fact_id",
    "leg_ref",
    "previous_posting_id",
    "sequence_number",
    "entry_kind",
    "balance_before_cents",
    "delta_cents",
    "balance_after_cents",
    "occurred_at",
)


class ObservationError(ValueError):
    """A binding/original-file failure prevents publishing measurements."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObservationError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ObservationError(f"{label} must be an explicitly registered list")
    return value


def _integer(value: Any, label: str, *, signed: bool = False) -> int:
    lower = -MAX_INT if signed else 0
    if type(value) is not int or not lower <= value <= MAX_INT:
        raise ObservationError(f"{label} must be a bounded integer, not bool/float/string")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ObservationError(f"{label} must be a nonempty bounded string")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ObservationError(f"{label} must be lowercase SHA256")
    return value


def _identity(value: Any, label: str) -> str:
    canonical = _text(value, label)
    try:
        if str(UUID(canonical)) != canonical:
            raise ValueError("noncanonical UUID")
    except ValueError as error:
        raise ObservationError(f"{label} must be a canonical UUID") from error
    return canonical


def _time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, label))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("naive time")
        return parsed
    except ValueError as error:
        raise ObservationError(f"{label} must be an aware ISO timestamp") from error


def _unique_ids(value: Any, label: str) -> list[str]:
    items = [_text(item, label) for item in _list(value, label)]
    if len(set(items)) != len(items):
        raise ObservationError(f"{label} contains duplicate identities")
    return items


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise ObservationError(f"Nonfinite JSON value: {value}")


def _missing(
    reason: str, *, status: str = "MISSING", refs: list[Any] | None = None
) -> dict[str, Any]:
    return {
        "status": status,
        "value": None,
        "numerator": None,
        "denominator": None,
        "applicable_units": [],
        "missing_reason": reason,
        "raw_refs": refs or [],
    }


class Bundle:
    """A fresh invocation's original-file read set, never retained authorization."""

    def __init__(self, manifest_path: Path):
        self.root = manifest_path.resolve().parent
        self.read_hashes: dict[Path, str] = {}
        self.manifest_path = manifest_path.resolve()
        self.v2 = False
        self.manifest = _object(self._read(self.manifest_path), "run manifest")
        self.v2 = self.manifest.get("protocol") == "mvp-observation-run-v2"
        self.frozen_view: Any = None
        if self.manifest.get("protocol") not in {RUN_PROTOCOL, "mvp-observation-run-v2"}:
            raise ObservationError("Unsupported run manifest protocol")
        self.bindings = {key: _text(self.manifest.get(key), key) for key in BINDINGS}
        for key, value in self.bindings.items():
            _text(value, key)
        for key in (
            "input_sha256",
            "oracle_sha256",
            "design_sha256",
            "rule_sha256",
            "source_sha256",
        ):
            _digest(self.bindings[key], key)
        for key in ("experiment_run_id", "isolated_db_epoch", "user_id"):
            _identity(self.bindings[key], key)
        if self.bindings["arm_id"] not in {"B0", "B1", "B2", "B3", "P"}:
            raise ObservationError("Unknown arm identity")
        if self.bindings["seed_version"] != "mvp-301-v6":
            raise ObservationError("Unregistered seed version")
        mode, purpose = self.bindings["execution_mode"], self.bindings["purpose"]
        if mode not in {"MODEL_ONLY", "SERVICE_INTEGRATION", "TOOL_TEST_ONLY"}:
            raise ObservationError("Unknown execution mode")
        if purpose not in {"DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN", "TOOL_TEST_ONLY"}:
            raise ObservationError("Unknown data purpose")
        if (mode == "TOOL_TEST_ONLY") != (purpose == "TOOL_TEST_ONLY"):
            raise ObservationError("Tool fixtures cannot claim actual experiment purpose/mode")
        self.run_status = self.manifest.get("run_status")
        if self.run_status not in {"COMPLETE", "FAILED", "RUNNING", "NOT_RUN", "NOT_IMPLEMENTED"}:
            raise ObservationError("Unknown run status")
        if mode == "SERVICE_INTEGRATION":
            database = self.manifest.get("isolated_database")
            if (
                not isinstance(database, str)
                or re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is None
            ):
                raise ObservationError(
                    "Actual integration records require a generated database identity"
                )
        self.registrations: dict[str, dict[str, Any]] = {}
        if self.v2:
            from scripts.mvp_observation_v2 import FrozenOriginalView

            self.frozen_view = FrozenOriginalView(self.manifest, self.root, self.bindings)
            self.registrations = dict(self.frozen_view.registrations)
            self.read_hashes.update(self.frozen_view.read_hashes)
        refs = _object(self.manifest.get("artifact_refs"), "artifact_refs")
        if set(refs) != {"input", "oracle", "design", "rule", "source"}:
            raise ObservationError("Every input/oracle/design/rule/source original is required")
        for name, ref in [] if self.v2 else refs.items():
            descriptor = _object(ref, f"{name} reference")
            if descriptor.get("sha256") != self.bindings[f"{name}_sha256"]:
                raise ObservationError(f"The original {name} digest is rebound")
            original = _object(self._load_ref(descriptor), f"{name} original")
            if (
                original.get("protocol") != REG_PROTOCOL
                or original.get("kind") != name.upper()
                or original.get("purpose") != purpose
                or original.get("case_id") != self.bindings["case_id"]
            ):
                raise ObservationError(f"The original {name} registration is rebound")
            if (
                purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}
                and original.get("registration_status") != "FROZEN"
            ):
                raise ObservationError(f"The original {name} is not frozen")
            if name == "rule" and original.get("arm_id") != self.bindings["arm_id"]:
                raise ObservationError("The original rule belongs to another arm")
            self.registrations[name] = original
        source_files = (
            []
            if self.v2
            else _list(self.registrations["source"].get("files"), "archived source files")
        )
        if not source_files and not self.v2:
            raise ObservationError(
                "A source hash without any archived source original is insufficient"
            )
        source_paths: set[Path] = set()
        for ref in source_files:
            descriptor = _object(ref, "source file descriptor")
            path = self._path(descriptor.get("path"))
            if path in source_paths:
                raise ObservationError("A source file is registered twice")
            source_paths.add(path)
            self._load_ref(descriptor, parse=False)
        self.raw: list[dict[str, Any]] = []
        seen_paths: set[Path] = set()
        for ref in _list(self.manifest.get("raw_refs"), "raw_refs"):
            descriptor = _object(ref, "raw original reference")
            path = self._path(descriptor.get("path"))
            if path in seen_paths:
                raise ObservationError("A raw original is registered twice")
            seen_paths.add(path)
            original = _object(self._load_ref(descriptor), "raw original")
            if (
                original.get("protocol")
                not in ({RAW_PROTOCOL, "mvp-raw-observation-v2"} if self.v2 else {RAW_PROTOCOL})
                or original.get("kind") != descriptor.get("kind")
                or original.get("bindings") != self.bindings
            ):
                raise ObservationError(
                    "A raw original's run/case/arm/source/purpose binding differs"
                )
            payload = _object(original.get("payload"), "raw original payload")
            self.raw.append(
                {
                    "kind": original["kind"],
                    "payload": payload,
                    "ref": descriptor,
                    "original": original,
                }
            )

    def _path(self, value: Any) -> Path:
        relative = Path(_text(value, "original relative path"))
        if relative.is_absolute():
            raise ObservationError("Original paths must be relative to this run directory")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ObservationError("Original path escapes this run directory")
        return path

    def _read(self, path: Path, *, parse: bool = True) -> Any:
        maximum = (512 if self.v2 else 64) * 1024 * 1024
        if path.stat().st_size > maximum:
            raise ObservationError(
                "V2 original exceeds the explicit 512 MiB read bound; history cannot be truncated"
                if self.v2
                else "Original file exceeds the explicit 64 MiB read bound"
            )
        original = path.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        if path in self.read_hashes and self.read_hashes[path] != digest:
            raise ObservationError("An original changed within this invocation")
        self.read_hashes[path] = digest
        if not parse:
            return original
        try:
            return json.loads(
                original.decode("utf-8"),
                object_pairs_hook=_json_pairs,
                parse_constant=_bad_constant,
            )
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ObservationError(f"Original is not strict UTF-8 JSON: {path.name}") from error

    def _load_ref(self, ref: dict[str, Any], *, parse: bool = True) -> Any:
        path = self._path(ref.get("path"))
        expected = _digest(ref.get("sha256"), "original digest")
        original = self._read(path, parse=parse)
        if self.read_hashes[path] != expected:
            raise ObservationError(f"Original bytes do not match registered digest: {path.name}")
        return original

    def unchanged(self) -> None:
        if self.v2:
            self.frozen_view.unchanged()
        for path, digest in self.read_hashes.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ObservationError(f"An original changed during observation: {path.name}")

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return [item for item in self.raw if item["kind"] == kind]

    def step_ref(self, value: Any, step_id: str) -> None:
        ref = _object(value, "original step reference")
        if set(ref) != {"artifact_sha256", "step_id"} or ref["step_id"] != step_id:
            raise ObservationError("An original step reference is rebound")
        matches = [
            item
            for item in self.kinds("STEP_LOG")
            if item["ref"]["sha256"] == ref["artifact_sha256"]
        ]
        if len(matches) != 1:
            raise ObservationError("An original step artifact is missing or ambiguous")
        rows = _list(matches[0]["payload"].get("steps"), "original steps")
        ids = [_text(_object(row, "step").get("step_id"), "step_id") for row in rows]
        if len(ids) != len(set(ids)) or ids.count(step_id) != 1:
            raise ObservationError("An original step identity is missing or duplicated")
        row = rows[ids.index(step_id)]
        if not isinstance(row.get("result"), dict) and not isinstance(row.get("error"), dict):
            raise ObservationError("The original step has no recorded result or error object")

    def review_ref(self, value: Any) -> None:
        ref = _object(value, "original exact review reference")
        digest = _digest(ref.get("artifact_sha256"), "review artifact")
        matches = [
            original
            for name, original in self.registrations.items()
            if self.manifest["artifact_refs"][name]["sha256"] == digest
        ]
        matches += [raw["original"] for raw in self.raw if raw["ref"]["sha256"] == digest]
        if len(matches) != 1:
            raise ObservationError("The original exact review artifact is missing or ambiguous")
        pointer = _text(ref.get("json_pointer"), "review pointer")
        if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
            raise ObservationError("The exact review JSON pointer is invalid")
        current: Any = matches[0]
        try:
            for escaped in pointer.split("/")[1:]:
                key = escaped.replace("~1", "/").replace("~0", "~")
                current = current[int(key)] if isinstance(current, list) else current[key]
            if current is None:
                raise ValueError("Null review")
        except (KeyError, TypeError, IndexError, ValueError) as error:
            raise ObservationError("The exact review pointer has no original value") from error

    def recovery_completion_ref(self, value: Any) -> None:
        """Check original settled-bank/receipt/posting linkage, not permission or safety."""
        ref = _object(value, "original recovery completion reference")
        if set(ref) != {"artifact_sha256", "action_id"}:
            raise ObservationError("Recovery completion needs its exact snapshot/action identity")
        action_id = _identity(ref["action_id"], "completed original action")
        matches = [
            raw
            for raw in self.kinds("BANK_SNAPSHOT")
            if raw["ref"]["sha256"] == ref["artifact_sha256"]
        ]
        if len(matches) != 1:
            raise ObservationError("The original recovery completion snapshot is absent")
        payload = matches[0]["payload"]
        tables = _object(payload.get("tables"), "completed original tables")
        user_id = self.bindings["user_id"]

        def owned(table: str) -> list[dict[str, Any]]:
            rows = [_object(row, table) for row in _list(tables.get(table), table)]
            if any(row.get("user_id") != user_id for row in rows):
                raise ObservationError("Completion snapshot includes foreign principal rows")
            return rows

        actions = [row for row in owned("action_plans") if row.get("id") == action_id]
        receipts = [
            row for row in owned("action_receipts") if row.get("action_plan_id") == action_id
        ]
        if (
            len(actions) != 1
            or actions[0].get("status") not in {"SUCCEEDED", "RECONCILED"}
            or len(receipts) != 1
            or receipts[0].get("status") != "SUCCEEDED"
        ):
            raise ObservationError(
                "Recovery lacks the actual completed action and unique projected receipt"
            )
        requests = [
            row for row in owned("bank_operations") if row.get("action_plan_id") == action_id
        ]
        if len(requests) != 1 or requests[0].get("status") != "SETTLED":
            raise ObservationError(
                "Recovery lacks an actual original independently settled bank operation"
            )
        bank, receipt = requests[0], receipts[0]
        if bank.get("operation_type") != "REDEEM_ASSET":
            raise ObservationError("Only an original modern redemption completion is implemented")
        bank_id = _identity(bank.get("id"), "bank operation identity")
        settled = _time(bank.get("settled_at"), "actual bank settlement time")
        if settled < _time(bank.get("available_at"), "actual availability") or settled != _time(
            receipt.get("occurred_at"), "actual receipt time"
        ):
            raise ObservationError("Original bank/receipt settlement times differ")
        if (
            _time(receipt.get("reconciled_at"), "actual projection time") < settled
            or _time(payload.get("captured_at"), "completion capture time") < settled
        ):
            raise ObservationError("Projection/capture precedes actual settlement")
        for field in ("executed_cents", "fee_cents", "loss_cents"):
            _integer(receipt.get(field), field)
        postings = owned("simulated_bank_postings")
        if ledger_continuity(postings, user_id)["status"] != "VERIFIED":
            raise ObservationError("Completion snapshot does not replay a full integer bank ledger")
        linked = {row["id"] for row in postings if row.get("operation_id") == bank_id}
        economic = [
            row
            for row in postings
            if row.get("operation_id") == bank_id and row["ledger_dimension"] == "ECONOMIC"
        ]
        if (
            len(economic) < 2
            or sum(row["delta_cents"] for row in economic) != 0
            or not any(
                row["position_id"] is not None and row["delta_cents"] < 0 for row in economic
            )
            or not any(row["account_id"] is not None and row["delta_cents"] > 0 for row in economic)
        ):
            raise ObservationError(
                "Original redemption economic legs do not independently conserve cash/principal"
            )
        response = _object(receipt.get("response"), "original receipt response")
        refs = _unique_ids(response.get("posting_ids"), "original receipt posting identities")
        if not refs or set(refs) != linked or response.get("bank_operation_id") != bank_id:
            raise ObservationError(
                "Original receipt does not match the actual settled bank posting set"
            )


def ledger_continuity(rows: list[Any], user_id: str) -> dict[str, Any]:
    """Independent exact-integer replay of raw full history, not a P evaluator."""
    if not rows:
        return {
            "status": "NOT_APPLICABLE",
            "value": None,
            "missing_reason": "No registered posting units",
            "heads": {},
        }
    try:
        ids: set[str] = set()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in rows:
            row = _object(value, "posting")
            if not set(POSTING_FIELDS) <= set(row):
                raise ObservationError("A raw posting does not include all original columns")
            identity = _identity(row["id"], "posting id")
            if identity in ids or row["user_id"] != user_id:
                raise ObservationError("Duplicate or foreign posting identity")
            ids.add(identity)
            key = _text(row["ledger_key"], "ledger key")
            if row["ledger_dimension"] not in {
                "ECONOMIC",
                "GOAL_OWNERSHIP",
                "INCOME_LOCATION",
                "LIABILITY",
            }:
                raise ObservationError("Unknown original ledger dimension")
            _object(row["ledger_metadata"], "ledger metadata")
            _time(row["occurred_at"], "posting time")
            _time(row["created_at"], "posting created time")
            account, position = row["account_id"], row["position_id"]
            if account is not None:
                _identity(account, "posting account")
            if position is not None:
                _identity(position, "posting position")
            if row["ledger_dimension"] == "ECONOMIC" and (
                (account is not None and (position is not None or key != f"CASH:{account}"))
                or (position is not None and (account is not None or key != f"POSITION:{position}"))
                or (
                    account is None
                    and position is None
                    and not key.startswith(
                        ("PAYEE:", "FEE:", "LOSS:", "CLEARING:bounded-funds-external-v1:")
                    )
                )
            ):
                raise ObservationError(
                    "Economic ledger identity differs from its original account/position"
                )
            sequence = _integer(row["sequence_number"], "sequence number")
            before = _integer(row["balance_before_cents"], "before cents")
            after = _integer(row["balance_after_cents"], "after cents")
            delta = _integer(row["delta_cents"], "delta cents", signed=True)
            if sequence < 1 or before + delta != after:
                raise ObservationError("Posting sequence/conservation differs")
            grouped[key].append(row)
        heads: dict[str, dict[str, Any]] = {}
        for key, group in grouped.items():
            group.sort(key=lambda row: row["sequence_number"])
            previous = None
            for sequence, row in enumerate(group, 1):
                if row["sequence_number"] != sequence:
                    raise ObservationError(
                        "The full original posting sequence has a gap or duplicate"
                    )
                if previous is None:
                    if (
                        row["entry_kind"] != "OPENING"
                        or row["previous_posting_id"] is not None
                        or row["balance_before_cents"] != 0
                    ):
                        raise ObservationError("The full original ledger opening is missing")
                elif (
                    row["previous_posting_id"] != previous["id"]
                    or row["balance_before_cents"] != previous["balance_after_cents"]
                    or row["ledger_dimension"] != previous["ledger_dimension"]
                    or row["ledger_metadata"] != previous["ledger_metadata"]
                    or row["account_id"] != previous["account_id"]
                    or row["position_id"] != previous["position_id"]
                    or _time(row["occurred_at"], "posting time")
                    < _time(previous["occurred_at"], "previous time")
                ):
                    raise ObservationError(
                        "The original ledger predecessor/value/identity/time differs"
                    )
                previous = row
            assert previous is not None
            heads[key] = {
                "posting_id": previous["id"],
                "balance_cents": previous["balance_after_cents"],
                "ledger_dimension": previous["ledger_dimension"],
                "account_id": previous["account_id"],
                "position_id": previous["position_id"],
            }
        return {
            "status": "VERIFIED",
            "value": {"postings": len(rows), "ledgers": len(heads)},
            "missing_reason": None,
            "heads": heads,
        }
    except ObservationError as error:
        return {"status": "UNKNOWN", "value": None, "missing_reason": str(error), "heads": {}}


def _balances(rows: Any, user_id: str, column: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in _list(rows, "original table rows"):
        row = _object(value, "original table row")
        identity = _identity(row.get("id"), "row identity")
        if row.get("user_id") != user_id or identity in result:
            raise ObservationError("A balance row has foreign/duplicate ownership")
        _integer(row.get(column), column)
        result[identity] = row
    return result


def _snapshot_pair(before: dict[str, Any], after: dict[str, Any], user_id: str) -> dict[str, Any]:
    tables_before = _object(before.get("tables"), "before tables")
    tables_after = _object(after.get("tables"), "after tables")
    if _time(after.get("captured_at"), "after capture") < _time(
        before.get("captured_at"), "before capture"
    ):
        raise ObservationError("Snapshot capture time moved backwards")
    result: dict[str, Any] = {}
    old_heads, new_heads = {}, {}
    if "simulated_bank_postings" in tables_before and "simulated_bank_postings" in tables_after:
        old_postings = _list(tables_before["simulated_bank_postings"], "before postings")
        new_postings = _list(tables_after["simulated_bank_postings"], "after postings")
        old_ledger = ledger_continuity(old_postings, user_id)
        new_ledger = ledger_continuity(new_postings, user_id)
        result["ledger_before"], result["ledger_after"] = old_ledger, new_ledger
        old_heads, new_heads = old_ledger["heads"], new_ledger["heads"]
        if old_ledger["status"] == new_ledger["status"] == "VERIFIED":
            new_by_id = {row["id"]: row for row in new_postings}
            changed = [row["id"] for row in old_postings if new_by_id.get(row["id"]) != row]
            result["original_postings_preserved"] = {
                "status": "VERIFIED" if not changed else "UNKNOWN",
                "value": not changed,
                "changed_or_missing_ids": changed,
            }
        else:
            result["original_postings_preserved"] = {
                "status": "UNKNOWN",
                "value": None,
                "missing_reason": "Full before/after ledger replay was not verified",
            }
    else:
        result["ledger_before"] = result["ledger_after"] = _missing(
            "Full original posting table is absent"
        )
        result["original_postings_preserved"] = _missing("Full original posting table is absent")
    for table, column in (("accounts", "balance_cents"), ("goals", "allocated_cents")):
        if table not in tables_before or table not in tables_after:
            result[f"{table}_changes"] = _missing(f"Complete before/after {table} table is absent")
            continue
        try:
            previous = _balances(tables_before[table], user_id, column)
            current = _balances(tables_after[table], user_id, column)
            if set(previous) - set(current):
                raise ObservationError("A previously observed account/goal disappeared")
            changes = []
            for identity, row in current.items():
                old = previous.get(identity)
                if table == "accounts":
                    if old is not None and old.get("account_type") != row.get("account_type"):
                        raise ObservationError("An original account type changed")
                    kind = row.get("account_type")
                    if kind not in {
                        "CASH",
                        "GOAL",
                        "CREDIT_CARD",
                        "CASH_MANAGEMENT",
                        "FIXED_DEPOSIT",
                    }:
                        raise ObservationError("An original account type is missing")
                    for head, state in ((old_heads, old), (new_heads, row)):
                        if state is not None and (
                            f"CASH:{identity}" not in head
                            or head[f"CASH:{identity}"]["balance_cents"] != state[column]
                        ):
                            raise ObservationError(
                                "Actual account balance lacks a matching replayed bank cash head"
                            )
                value = {
                    "id": identity,
                    "before_cents": old[column] if old else 0,
                    "after_cents": row[column],
                    "delta_cents": row[column] - (old[column] if old else 0),
                    "created_between_snapshots": old is None,
                }
                if table == "accounts":
                    value["account_type"] = row["account_type"]
                changes.append(value)
            result[f"{table}_changes"] = {
                "status": "MEASURED" if changes else "NOT_APPLICABLE",
                "value": changes or None,
                "missing_reason": None if changes else "No original table units",
            }
        except ObservationError as error:
            result[f"{table}_changes"] = _missing(str(error), status="UNKNOWN")
    result["scope"] = (
        "Raw actual balances and full posting continuity only; "
        "not protected-funds, authority, availability or income-source safety"
    )
    return result


def bank_observations(bundle: Bundle) -> dict[str, Any]:
    snapshots = bundle.kinds("BANK_SNAPSHOT")
    if not snapshots:
        return _missing("No original bank snapshots")
    pairs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    results = []
    for item in snapshots:
        payload = item["payload"]
        checkpoint = _text(payload.get("checkpoint_id"), "bank checkpoint")
        phase = payload.get("phase")
        if phase not in {"BEFORE", "AFTER"} or phase in pairs[checkpoint]:
            raise ObservationError("Bank snapshot phase is unknown or duplicated")
        pairs[checkpoint][phase] = payload
    for checkpoint, pair in pairs.items():
        if set(pair) != {"BEFORE", "AFTER"}:
            results.append(
                {
                    "checkpoint_id": checkpoint,
                    **_missing("Both original before/after snapshots are required"),
                }
            )
        else:
            try:
                result = _snapshot_pair(pair["BEFORE"], pair["AFTER"], bundle.bindings["user_id"])
            except ObservationError as error:
                result = _missing(str(error), status="UNKNOWN")
            results.append({"checkpoint_id": checkpoint, **result})
    return {
        "status": "OBSERVED",
        "checkpoints": results,
        "raw_refs": [item["ref"] for item in snapshots],
    }


def actor_metric(bundle: Bundle) -> dict[str, Any]:
    logs = bundle.kinds("ACTOR_LOG")
    if len(logs) != 1:
        return _missing("Exactly one complete original actor log is required")
    item, payload = logs[0], logs[0]["payload"]
    expected: list[str] = []
    try:
        expected = _unique_ids(
            bundle.registrations["oracle"].get("actor_event_ids"), "registered actor identities"
        )
        manifest = _unique_ids(payload.get("event_manifest"), "actor event manifest")
        if set(expected) != set(manifest) or payload.get("capture_status") != "COMPLETE":
            raise ObservationError("Actor capture/registered event manifest is incomplete")
        events = _list(payload.get("events"), "original actor events")
        ids, units = [], []
        counts = {"INITIAL_AUTHORIZATION": 0, "RUNTIME_INTERVENTION": 0}
        for value in events:
            row = _object(value, "actor event")
            identity = _text(row.get("event_id"), "actor event id")
            if (
                row.get("actor_kind") != "SYNTHETIC_SCRIPTED_ACTOR"
                or row.get("event_type")
                not in {"MANUAL_CHOICE", "CLARIFICATION", "AFFIRMATIVE_CONFIRMATION"}
                or row.get("phase") not in counts
            ):
                raise ObservationError("An actor event is not a registered synthetic intervention")
            step_id = _text(row.get("step_id"), "actor original step")
            bundle.step_ref(row.get("raw_step_ref"), step_id)
            _time(row.get("occurred_at"), "actor event time")
            if row["event_type"] == "AFFIRMATIVE_CONFIRMATION":
                bundle.review_ref(row.get("exact_review_ref"))
            ids.append(identity)
            counts[row["phase"]] += 1
            units.append(
                {
                    "event_id": identity,
                    "event_type": row["event_type"],
                    "phase": row["phase"],
                    "step_id": step_id,
                }
            )
        if len(ids) != len(set(ids)) or set(ids) != set(manifest):
            raise ObservationError("An original actor event is missing, duplicated or unexpected")
        if not ids:
            return _missing(
                "No registered actor event units; empty logs are not effect evidence",
                status="NOT_APPLICABLE",
                refs=[item["ref"]],
            )
        return {
            "status": "MEASURED",
            "value": counts,
            "numerator": len(ids),
            "denominator": 1,
            "applicable_units": units,
            "missing_reason": None,
            "raw_refs": [item["ref"]],
            "actor_kind": "SYNTHETIC_SCRIPTED_ACTOR",
            "human_timing_or_study_claim": False,
        }
    except ObservationError as error:
        result = _missing(str(error), refs=[item["ref"]])
        result["denominator"] = 1
        result["applicable_units"] = expected
        return result


def timing_metric(bundle: Bundle) -> dict[str, Any]:
    logs = bundle.kinds("TIMING_LOG")
    if len(logs) != 1:
        return _missing("Exactly one original monotonic timing log is required")
    item, payload = logs[0], logs[0]["payload"]
    decisions: list[str] | None = None
    recoveries: list[str] | None = None
    series: dict[str, list[dict[str, Any]]] = {"decision": [], "recovery": []}
    try:
        decisions = _unique_ids(
            bundle.registrations["oracle"].get("decision_opportunity_ids"),
            "registered decision opportunities",
        )
        recoveries = _unique_ids(
            bundle.registrations["oracle"].get("recovery_opportunity_ids"),
            "registered recovery opportunities",
        )
        clock_id = _text(payload.get("clock_id"), "monotonic clock id")
        if payload.get("clock_semantics") != "perf_counter_ns":
            raise ObservationError(
                "Simulation datetime or configured timeout is not measured latency"
            )
        if payload.get("capture_status") != "COMPLETE":
            raise ObservationError("Timing capture is incomplete")
        for name, expected in (("decision", decisions), ("recovery", recoveries)):
            rows = _list(payload.get(f"{name}_samples"), f"{name} timing samples")
            ids = []
            for value in rows:
                row = _object(value, "timing sample")
                identity = _text(row.get("opportunity_id"), "timing opportunity")
                step_id = _text(row.get("step_id"), "timed original step")
                bundle.step_ref(row.get("raw_step_ref"), step_id)
                if row.get("clock_id") != clock_id:
                    raise ObservationError(
                        "Timing endpoints use different or absent monotonic clocks"
                    )
                ids.append(identity)
                if name == "decision":
                    start = _integer(row.get("start_ns"), "decision start ns")
                    end = _integer(row.get("end_ns"), "decision end ns")
                    if end < start:
                        raise ObservationError("Decision monotonic time moved backwards")
                    result = {
                        "opportunity_id": identity,
                        "elapsed_ns": end - start,
                        "censored": False,
                        "status": "MEASURED",
                    }
                else:
                    start = _integer(row.get("trigger_ns"), "recovery trigger ns")
                    observed = _integer(row.get("observed_until_ns"), "recovery observation ns")
                    censored = row.get("censored")
                    if type(censored) is not bool or observed < start:
                        raise ObservationError("Recovery censoring/observation clock is invalid")
                    endpoint = row.get("settled_projected_ns")
                    if censored:
                        if endpoint is not None:
                            raise ObservationError(
                                "A censored recovery cannot claim actual complete settlement"
                            )
                        result = {
                            "opportunity_id": identity,
                            "elapsed_ns": None,
                            "lower_bound_ns": observed - start,
                            "censored": True,
                            "status": "CENSORED",
                        }
                    else:
                        end = _integer(endpoint, "actual settled and projected ns")
                        if not start <= end <= observed:
                            raise ObservationError("Actual recovery endpoints are inconsistent")
                        bundle.recovery_completion_ref(row.get("completion_ref"))
                        result = {
                            "opportunity_id": identity,
                            "elapsed_ns": end - start,
                            "censored": False,
                            "status": "MEASURED",
                        }
                series[name].append(result)
            if len(ids) != len(set(ids)) or set(ids) != set(expected):
                raise ObservationError(
                    f"{name} observations omit/duplicate/add preregistered opportunities"
                )
        denominator = {"decision": len(decisions), "recovery": len(recoveries)}
        if not decisions and not recoveries:
            return _missing(
                "No registered timing opportunities", status="NOT_APPLICABLE", refs=[item["ref"]]
            )
        return {
            "status": "MEASURED",
            "value": {"clock_id": clock_id, "clock_semantics": "perf_counter_ns", **series},
            "numerator": {
                name: sum(not row["censored"] for row in rows) for name, rows in series.items()
            },
            "denominator": denominator,
            "applicable_units": decisions + recoveries,
            "missing_reason": None,
            "raw_refs": [item["ref"]],
        }
    except ObservationError as error:
        result = _missing(str(error), refs=[item["ref"]])
        result["denominator"] = {
            "decision": len(decisions) if decisions is not None else None,
            "recovery": len(recoveries) if recoveries is not None else None,
        }
        result["applicable_units"] = (decisions or []) + (recoveries or [])
        result["partial_observations"] = series
        return result


def observe(manifest_path: Path) -> dict[str, Any]:
    bundle = Bundle(manifest_path)
    not_run = bundle.run_status in {"NOT_RUN", "NOT_IMPLEMENTED"}
    metrics = {
        identity: _missing("Independent verified calculator not implemented")
        for identity in METRIC_IDS
    }
    if not_run:
        metrics = {
            identity: _missing(f"Run status {bundle.run_status}; no measurements", status="NOT_RUN")
            for identity in METRIC_IDS
        }
        bank = _missing("Run was not executed", status="NOT_RUN")
    else:
        bank = bank_observations(bundle)
        metrics["E2"], metrics["E5"] = actor_metric(bundle), timing_metric(bundle)
    bundle.unchanged()
    return {
        "protocol": OUTPUT_PROTOCOL,
        "bindings": bundle.bindings,
        "run_status": bundle.run_status,
        "manifest_sha256": bundle.read_hashes[bundle.manifest_path],
        "tool_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "financial_effect_evidence": False,
        "scope": (
            "Original-file integrity and partial observations; twelve independent "
            "financial/audit calculators and experimental acceptance remain incomplete"
        ),
        "implemented_metric_ids": ["E2", "E5"],
        "metrics": metrics,
        "bank_observations": bank,
        "registered_opportunities": {
            name: {"status": "REGISTERED", "ids": _unique_ids(value, name)}
            for name, value in bundle.registrations["oracle"].items()
            if name.endswith("_ids") and isinstance(value, list)
        },
        "original_file_count": len(bundle.read_hashes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Explicit original run manifest")
    parser.add_argument(
        "--output", type=Path, required=True, help="A new JSON output; existing paths are refused"
    )
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("The output already exists; preserve it and choose a fresh path")
    result = observe(args.run)
    # Exclusive creation protects old results even if another process races this check.
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "scope": "PARTIAL_OBSERVATIONS_ONLY",
                "financial_effect_evidence": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
