"""Readonly transaction-artifact validator; no DB/app/P oracle imports or raw edits.

Installation does not promote actual-run or economic evidence. The supported
registration is V1; V2 source/clock registration requires a separate adapter.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

BINDINGS = {
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
}
TABLES = {
    "action_plans",
    "action_resource_reservations",
    "bank_operations",
    "action_receipts",
    "simulated_bank_postings",
    "accounts",
    "evidence_items",
    "users",
}
PHASES = {"APPLICATION_RESERVATION", "INDEPENDENT_BANK", "APPLICATION_PROJECTION"}
TERMINALS = {"COMMITTED", "COMMITTED_THEN_ERROR", "ROLLED_BACK", "UNKNOWN_TRANSACTION_OUTCOME"}
STATUS_SQL = "SELECT pg_xact_status(CAST(:target_xid AS xid8)) AS target_status"


class EvidenceError(ValueError):
    def __init__(self, reason: str, *, missing: bool = False) -> None:
        super().__init__(reason)
        self.missing = missing


def need(condition: bool, reason: str, *, missing: bool = False) -> None:
    if not condition:
        raise EvidenceError(reason, missing=missing)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def strict(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            need(key not in result, "DUPLICATE_JSON_KEY:" + key)
            result[key] = value
        return result

    def finite(value: str) -> Any:
        raise EvidenceError("NONFINITE_JSON:" + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=finite)


def digest(value: Any) -> str:
    need(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "INVALID_SHA256",
    )
    return str(value)


def identity(value: Any) -> str:
    need(isinstance(value, str), "INVALID_UUID")
    need(str(UUID(value)) == value, "NONCANONICAL_UUID")
    return str(value)


def at(value: Any) -> datetime:
    need(isinstance(value, str), "MISSING_TIME", missing=True)
    result = datetime.fromisoformat(value)
    need(result.tzinfo is not None and result.utcoffset() is not None, "NAIVE_TIME")
    return result.astimezone(UTC)


def xid(value: Any) -> str:
    need(
        isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None,
        "INVALID_FULL_XID8",
    )
    need(int(value) < 2**64, "XID8_OVERFLOW")
    return str(value)


def pointer(body: Any, path: Any) -> Any:
    need(isinstance(path, str) and (path == "" or path.startswith("/")), "INVALID_JSON_POINTER")
    value = body
    for raw in path[1:].split("/") if path else []:
        need(re.search(r"~(?![01])", raw) is None, "INVALID_POINTER_ESCAPE")
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            need(re.fullmatch(r"0|[1-9][0-9]*", part) is not None, "INVALID_POINTER_INDEX")
            need(int(part) < len(value), "MISSING_POINTER_VALUE", missing=True)
            value = value[int(part)]
        else:
            need(isinstance(value, dict) and part in value, "MISSING_POINTER_VALUE", missing=True)
            value = value[part]
    return value


class Reader:
    def __init__(self, root: Path, bindings: dict[str, str], paths: dict[str, str]) -> None:
        self.root = root.resolve()
        self.bindings = bindings
        self.paths = dict(paths)

    def file(self, path: Any, expected_sha: Any) -> bytes:
        need(isinstance(path, str), "MISSING_ORIGINAL_PATH", missing=True)
        actual = Path(path)
        need(
            actual.is_absolute() and actual.resolve().is_relative_to(self.root),
            "ORIGINAL_OUTSIDE_WORKSPACE",
        )
        need(actual.is_file(), "MISSING_ORIGINAL_FILE", missing=True)
        need(
            actual.stat().st_size <= 256 * 1024 * 1024,
            "ORIGINAL_EXCEEDS_EXPLICIT_256MIB_BOUNDARY",
            missing=True,
        )
        raw = actual.read_bytes()
        need(sha(raw) == digest(expected_sha), "ORIGINAL_BYTE_SHA_MISMATCH")
        return raw

    def descriptor(self, reference: dict[str, Any]) -> Any:
        need(set(reference) == {"path", "sha256"}, "INVALID_FILE_DESCRIPTOR")
        return strict(self.file(reference["path"], reference["sha256"]))

    def resolve(self, reference: dict[str, Any], kind: str | None = None) -> Any:
        need(
            set(reference) == {"artifact_sha256", "json_pointer", "value_sha256"},
            "INVALID_RAW_REFERENCE",
        )
        key = digest(reference["artifact_sha256"])
        need(key in self.paths, "MISSING_RAW_ORIGINAL", missing=True)
        body = strict(self.file(self.paths[key], key))
        need(
            body.get("protocol") == "mvp-raw-observation-v1"
            and body.get("bindings") == self.bindings,
            "ORIGINAL_RUN_BINDINGS_MISMATCH",
        )
        if kind is not None:
            need(body.get("kind") == kind, "ORIGINAL_KIND_MISMATCH")
        value = pointer(body, reference["json_pointer"])
        need(
            sha(canonical(value)) == digest(reference["value_sha256"]),
            "ORIGINAL_VALUE_SHA_MISMATCH",
        )
        return value


def source_and_registration(
    reader: Reader, descriptor: dict[str, Any], expected: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    registry = reader.descriptor(descriptor)
    need(
        registry.get("protocol") != "mvp-arm-provider-registry-v2",
        "REGISTRY_V2_SOURCE_CLOCK_ADAPTER_NOT_IMPLEMENTED",
        missing=True,
    )
    need(
        registry.get("protocol") == "mvp-arm-provider-registry-v1"
        and registry.get("bindings") == expected,
        "REGISTRY_BINDINGS_MISMATCH",
    )
    database = registry.get("database_name")
    need(
        isinstance(database, str) and re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None,
        "DATABASE_NOT_ISOLATED_SIMULATION",
    )
    artifacts = {}
    for name in ("input", "oracle", "design", "rule", "source"):
        ref = registry["artifact_refs"][name]
        need(ref["sha256"] == expected[name + "_sha256"], "REGISTRATION_ARTIFACT_SHA_MISMATCH")
        body = reader.descriptor(ref)
        need(
            body.get("case_id") == expected["case_id"]
            and body.get("purpose") == expected["purpose"],
            "REGISTRATION_CASE_PURPOSE_MISMATCH",
        )
        artifacts[name] = body
    originals: dict[str, str] = {}
    for ref in artifacts["source"]["implementation_files"]:
        name = ref["original_path"]
        need(isinstance(name, str) and name not in originals, "DUPLICATE_SOURCE_PATH")
        current = (reader.root / name).resolve()
        need(
            current.is_relative_to(reader.root) and current.is_file(),
            "MISSING_CURRENT_SOURCE",
            missing=True,
        )
        archive = reader.file(ref["path"], ref["sha256"])
        need(current.read_bytes() == archive, "CURRENT_SOURCE_DRIFT")
        originals[name] = ref["sha256"]
    required = {
        p.relative_to(reader.root).as_posix()
        for p in (reader.root / "apps/api/app").rglob("*.py")
        if "tests" not in p.relative_to(reader.root / "apps/api/app").parts
    } | {
        "scripts/mvp_arm_executor.py",
        "scripts/mvp_observations.py",
        "scripts/mvp_trace_metrics.py",
    }
    need(required <= set(originals), "INCOMPLETE_CURRENT_SOURCE_INVENTORY", missing=True)
    need(
        {
            "apps/api/app/services/execution_observations.py",
            "apps/api/app/services/experiment_arms.py",
        }
        <= set(originals),
        "MISSING_PRODUCER_SOURCE",
        missing=True,
    )
    return {"database_name": database, "artifacts": artifacts}, originals


def table_inventory(probe: dict[str, Any], owner: str, action: str) -> None:
    tables, inventory = probe.get("tables"), probe.get("table_inventory")
    need(
        isinstance(tables, dict) and set(tables) == TABLES, "INCOMPLETE_SCOPED_TABLES", missing=True
    )
    need(
        isinstance(inventory, dict) and set(inventory) == TABLES,
        "INCOMPLETE_SCOPED_INVENTORY",
        missing=True,
    )
    tables = cast(dict[str, Any], tables)
    inventory = cast(dict[str, Any], inventory)
    for name, rows in tables.items():
        need(isinstance(rows, list), "INVALID_SCOPED_ROWS")
        ids = [identity(row["id"]) for row in rows]
        need(ids == sorted(set(ids)), "UNSORTED_OR_DUPLICATE_SCOPED_ROWS")
        need(
            inventory[name].get("row_ids") == ids
            and inventory[name].get("sha256") == sha(canonical(rows)),
            "SCOPED_INVENTORY_SHA_OR_IDS_MISMATCH",
        )
        queries = [item for item in probe.get("queries", []) if item.get("table") == name]
        need(len(queries) == 1, "MISSING_OR_DUPLICATE_SCOPED_SQL:" + name, missing=True)
        query = queries[0]
        owner_column = "id" if name == "users" else "user_id"
        sql = query.get("sql")
        parameters = query.get("parameters")
        need(isinstance(sql, str) and isinstance(parameters, dict), "INVALID_SCOPED_SQL")
        need(
            sql.startswith("SELECT ")
            and ("FROM " + name) in sql
            and (name + "." + owner_column + " = :") in sql
            and ("ORDER BY " + name + ".id") in sql,
            "SCOPED_SQL_MISSING_ORIGINAL_OWNER_FILTER",
        )
        need(
            bool(parameters)
            and set(parameters.values()) <= {owner, action}
            and owner in parameters.values(),
            "SCOPED_SQL_PARAMETER_BINDING_MISMATCH",
        )
        if name not in {"users", "accounts", "evidence_items"}:
            action_column = (
                "id"
                if name == "action_plans"
                else "operation_id"
                if name == "simulated_bank_postings"
                else "action_plan_id"
            )
            need(
                (name + "." + action_column + " = :") in sql and action in parameters.values(),
                "SCOPED_SQL_MISSING_ORIGINAL_ACTION_FILTER",
            )
        for row in rows:
            need(
                row.get("id") == owner if name == "users" else row.get("user_id") == owner,
                "FOREIGN_SCOPED_ROW_OWNER",
            )
            key = (
                "id"
                if name == "action_plans"
                else "operation_id"
                if name == "simulated_bank_postings"
                else "action_plan_id"
            )
            if name not in {"users", "accounts", "evidence_items"}:
                need(row.get(key) == action, "FOREIGN_SCOPED_ACTION_ROW")
    need(
        len(tables["users"]) == 1 and tables["users"][0].get("is_simulated") is True,
        "MISSING_OR_NON_SIMULATED_OWNED_USER",
        missing=True,
    )


def phase_recheck(
    raw: dict[str, Any],
    probe: dict[str, Any],
    raw_ref: dict[str, Any],
    expected: dict[str, str],
    call: str,
    action: str,
    database: str,
) -> dict[str, Any]:
    need(
        raw.get("protocol") == "execution-live-phase-observation-v2",
        "OLD_OR_UNSUPPORTED_PHASE_PROTOCOL",
        missing=True,
    )
    need(
        probe.get("protocol") == "execution-independent-phase-recheck-v2",
        "MISSING_V2_RECHECK",
        missing=True,
    )
    for body in (raw, probe):
        need(
            body.get("bindings") == expected and body.get("service_call_id") == call,
            "PHASE_OR_PROBE_CALL_BINDING_MISMATCH",
        )
        need(
            body.get("user_id") == expected["user_id"]
            and body.get("action_id") == action
            and body.get("epoch_id") == expected["isolated_db_epoch"],
            "PHASE_OR_PROBE_OWNER_ACTION_EPOCH_MISMATCH",
        )
    need(
        raw.get("phase") in PHASES
        and probe.get("phase") == raw["phase"]
        and identity(probe.get("phase_id")) == identity(raw.get("phase_id")),
        "PHASE_IDENTITY_MISMATCH",
    )
    need(
        probe.get("terminal_value_sha256") == raw_ref["value_sha256"]
        and probe.get("terminal_state") == raw.get("state"),
        "TERMINAL_ORIGINAL_HASH_MISMATCH",
    )
    need(
        probe.get("target_sql_identity") == raw.get("sql_identity"), "TARGET_SQL_IDENTITY_MISMATCH"
    )
    target, fresh = raw.get("sql_identity"), probe.get("probe_sql_identity")
    need(
        isinstance(target, dict) and isinstance(fresh, dict), "MISSING_SQL_IDENTITIES", missing=True
    )
    target = cast(dict[str, Any], target)
    fresh = cast(dict[str, Any], fresh)
    need(
        target.get("database_name") == fresh.get("database_name") == database,
        "CROSS_DATABASE_PROBE",
    )
    need(
        xid(target.get("transaction_id")) != xid(fresh.get("transaction_id")),
        "SAME_TRANSACTION_NOT_INDEPENDENT",
    )
    for value in (target.get("backend_pid"), fresh.get("backend_pid")):
        need(
            isinstance(value, int) and not isinstance(value, bool) and value > 0,
            "INVALID_BACKEND_PID",
        )
    need(target["backend_pid"] != fresh["backend_pid"], "SAME_CONNECTION_NOT_INDEPENDENT")
    need(
        fresh.get("isolation") == "repeatable read" and fresh.get("read_only") == "on",
        "PROBE_NOT_RR_READ_ONLY",
    )
    need(
        probe.get("readonly_outer_context_exited") == "NORMAL",
        "MISSING_COMPLETED_READONLY_PROBE",
        missing=True,
    )
    need(
        probe.get("capture_state") == "CAPTURED_VALIDATOR_PENDING"
        and probe.get("missing_reason") is None,
        "PRODUCER_RECHECK_EXPLICITLY_INCOMPLETE",
        missing=True,
    )
    need(
        probe.get("capability", {}).get("procedure") == "pg_xact_status(xid8)"
        and probe["capability"].get("may_execute") is True,
        "MISSING_ACTUAL_FUNCTION_CAPABILITY",
        missing=True,
    )
    state = raw.get("state")
    need(
        state in {"COMMITTED", "COMMITTED_THEN_ERROR", "ROLLED_BACK"},
        "UNKNOWN_OR_UNREACHED_TARGET_OUTCOME",
        missing=True,
    )
    expected_status = "aborted" if state == "ROLLED_BACK" else "committed"
    need(
        raw.get("outer_after_rollback_observed") is (expected_status == "aborted")
        and raw.get("outer_after_commit_observed") is (expected_status == "committed"),
        "CONTRADICTORY_OR_SAVEPOINT_COMMIT_FLAGS",
    )
    need(
        probe.get("target_status") in {"committed", "aborted"},
        "TARGET_STATUS_NOT_FINAL_OR_MISSING",
        missing=True,
    )
    need(
        probe["target_status"] == probe.get("expected_target_status") == expected_status,
        "ACTUAL_TRANSACTION_STATUS_CONTRADICTS_ORIGINAL",
    )
    need(
        raw.get("financial_effect_verified") is False
        and probe.get("financial_effect_verified") is False
        and probe.get("independent_phase_proof_verified") is False,
        "RAW_FLAGS_WERE_PROMOTED",
    )
    need(at(raw["business_clock"]) == at(probe["business_clock"]), "BUSINESS_CLOCK_MISMATCH")
    need(
        at(raw["observed_at"]) <= at(probe["started_at"]) <= at(probe["finished_at"]),
        "PROBE_CAPTURE_TIME_ORDER_INVALID",
    )
    sql = [item for item in probe.get("queries", []) if item.get("sql") == STATUS_SQL]
    need(
        len(sql) == 1 and sql[0].get("parameters") == {"target_xid": target["transaction_id"]},
        "MISSING_OR_FOREIGN_ACTUAL_XID_QUERY",
        missing=True,
    )
    need(
        any(item.get("sql") == "SET TRANSACTION READ ONLY" for item in probe["queries"]),
        "MISSING_READ_ONLY_SQL",
        missing=True,
    )
    table_inventory(probe, expected["user_id"], action)
    return {
        "phase": raw["phase"],
        "phase_id": raw["phase_id"],
        "target_xid": target["transaction_id"],
        "probe_xid": fresh["transaction_id"],
        "actual_status": probe["target_status"],
        "terminal_ref": raw_ref,
        "status": "TRANSACTION_ARTIFACTS_CONSISTENT",
    }


def request_and_persistence(probe: dict[str, Any], payload: dict[str, Any]) -> None:
    """Data integrity only: original request hashes/refs, never P authority or money judgment."""
    tables = probe["tables"]
    actions = tables["action_plans"]
    need(len(actions) == 1, "MISSING_OR_AMBIGUOUS_ORIGINAL_ACTION", missing=True)
    action = actions[0]
    request = action.get("request")
    need(
        isinstance(request, dict) and action.get("request_hash") == sha(canonical(request)),
        "ORIGINAL_ACTION_REQUEST_HASH_MISMATCH",
    )
    command = request.get("execution")
    need(
        isinstance(command, dict)
        and command.get("effect_hash") == digest(payload.get("effect_hash")),
        "ORIGINAL_ACTION_EFFECT_HASH_BINDING_MISMATCH",
    )
    effect = command.get("effect")
    need(
        isinstance(effect, dict)
        and effect.get("operation_id") == payload["action_id"]
        and effect.get("user_id") == probe["user_id"],
        "ORIGINAL_EFFECT_IDENTITY_MISMATCH",
    )
    # Only the frozen primitive canonical hash contract; no financial evaluator is imported.
    effect_value = strict(canonical(effect))
    effect_value["cash_uses"] = sorted(
        effect_value["cash_uses"], key=lambda item: item["account_id"]
    )
    effect_value["income_uses"] = sorted(
        effect_value["income_uses"],
        key=lambda item: (item["origin_transaction_id"], item["account_id"]),
    )
    effect_value["policy_version_ids"] = sorted(effect_value["policy_version_ids"])
    if effect_value["liability"] is not None:
        effect_value["liability"]["evidence_ids"] = sorted(
            effect_value["liability"]["evidence_ids"]
        )
    need(
        sha(canonical(effect_value)) == command["effect_hash"],
        "ORIGINAL_EFFECT_CANONICAL_HASH_MISMATCH",
    )
    if probe["phase"] in {"INDEPENDENT_BANK", "APPLICATION_PROJECTION"}:
        banks = tables["bank_operations"]
        need(len(banks) == 1, "MISSING_OR_AMBIGUOUS_ORIGINAL_BANK", missing=True)
        bank = banks[0]
        bank_request = bank.get("request")
        need(
            isinstance(bank_request, dict)
            and bank.get("request_hash") == sha(canonical(bank_request)),
            "ORIGINAL_BANK_REQUEST_HASH_MISMATCH",
        )
        need(
            bank_request == command and bank.get("id") == payload["action_id"],
            "BANK_ORIGINAL_REQUEST_OR_IDENTITY_MISMATCH",
        )
    if probe["phase"] == "APPLICATION_PROJECTION":
        receipts = tables["action_receipts"]
        if probe["target_status"] == "aborted":
            need(receipts == [], "RECEIPT_PRESENT_AFTER_PROJECTION_ROLLBACK")
        else:
            need(
                len(receipts) == 1 and bool(tables["simulated_bank_postings"]),
                "MISSING_ORIGINAL_RECEIPT_OR_POSTINGS",
                missing=True,
            )
            receipt = receipts[0]
            need(
                receipt.get("receipt_ref") == "bank-operation:" + payload["action_id"]
                and receipt.get("response", {}).get("bank_operation_id") == payload["action_id"],
                "ORIGINAL_RECEIPT_BANK_BINDING_MISMATCH",
            )
            need(
                receipt.get("response", {}).get("posting_ids")
                == sorted(row["id"] for row in tables["simulated_bank_postings"]),
                "ORIGINAL_RECEIPT_POSTING_REFS_MISMATCH",
            )


def complete_phase_graph(
    records: list[tuple[dict[str, Any], dict[str, Any]]], terminal: dict[str, Any]
) -> None:
    phase_id = terminal["phase_id"]
    rows = [row for row, _ in records if row.get("phase_id") == phase_id]
    beginnings = [row for row in rows if row.get("state") == "LIVE_TRANSACTION_BEGIN"]
    need(
        len(beginnings) == 1 and rows[0] is beginnings[0] and rows[-1] is terminal,
        "INCOMPLETE_OR_REORDERED_PHASE_BEGIN_TERMINAL",
        missing=True,
    )
    previous = at(beginnings[0]["observed_at"])
    for row in rows:
        need(row.get("sql_identity") == terminal["sql_identity"], "LIVE_PHASE_CHANGED_SQL_IDENTITY")
        need(
            row.get("phase") == terminal["phase"]
            and row.get("user_id") == terminal["user_id"]
            and row.get("action_id") == terminal["action_id"]
            and row.get("epoch_id") == terminal["epoch_id"],
            "LIVE_PHASE_OWNER_OR_IDENTITY_MISMATCH",
        )
        need(
            at(row["business_clock"]) == at(terminal["business_clock"]), "LIVE_PHASE_CLOCK_MISMATCH"
        )
        current = at(row["observed_at"])
        need(current >= previous, "LIVE_PHASE_CAPTURE_TIME_REORDERED")
        previous = current
        if row is not terminal:
            need(
                row.get("outer_after_commit_observed") is False
                and row.get("outer_after_rollback_observed") is False,
                "INTERMEDIATE_OR_SAVEPOINT_CLAIMS_OUTER_COMMIT",
            )
    if terminal["state"] in {"COMMITTED", "COMMITTED_THEN_ERROR"}:
        before = [row for row in rows if row.get("state") == "BEFORE_OUTER_COMMIT"]
        need(
            len(before) == 1 and rows[-2] is before[0],
            "MISSING_BEFORE_OUTER_COMMIT_ORIGINAL",
            missing=True,
        )
        need(
            before[0].get("live", {}).get("nested_savepoint_active") is False,
            "BEFORE_OUTER_COMMIT_STILL_SAVEPOINT",
        )
    if terminal["phase"] in {"APPLICATION_RESERVATION", "APPLICATION_PROJECTION"}:
        need(
            any(row.get("state") == "USER_LOCK_RETURNED" for row in rows),
            "MISSING_ACTUAL_USER_LOCK_CHECKPOINT",
            missing=True,
        )


def epoch_snapshots(reader: Reader, payload: dict[str, Any], expected: dict[str, str]) -> None:
    for key in ("before_snapshot_ref", "after_snapshot_ref"):
        need(isinstance(payload.get(key), dict), "MISSING_ORIGINAL_BUSINESS_SNAPSHOT", missing=True)
        tables = reader.resolve(payload[key], "BUSINESS_SNAPSHOT")
        epochs = tables.get("audit_epochs")
        need(isinstance(epochs, list), "MISSING_EPOCH_SNAPSHOT_ORIGINAL", missing=True)
        rows = [row for row in epochs if row.get("id") == expected["isolated_db_epoch"]]
        need(
            len(rows) == 1
            and rows[0].get("user_id") == expected["user_id"]
            and rows[0].get("status") == "OPEN",
            "ORIGINAL_EPOCH_OWNER_OR_OPEN_STATE_MISMATCH",
        )
        users = tables.get("users")
        need(
            isinstance(users, list)
            and len(users) == 1
            and users[0].get("id") == expected["user_id"]
            and users[0].get("is_simulated") is True,
            "MISSING_OWNED_USER_SNAPSHOT",
            missing=True,
        )


def validate_capture(
    capture: dict[str, Any],
    expected: dict[str, str],
    registry_ref: dict[str, Any],
    *,
    workspace: Path,
    actual_run_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "protocol": "phase-independent-validator-candidate-v2",
        "status": "MISSING",
        "transaction_artifacts_verified": False,
        "actual_runtime_evidence_verified": False,
        "economic_execution_verified": False,
        "execution_mode": None,
        "raw_flags_modified": False,
        "missing_reason": None,
        "phases": [],
        "category": None,
    }
    try:
        need(set(expected) == BINDINGS, "INCOMPLETE_EXPECTED_THIRTEEN_BINDINGS", missing=True)
        need(expected["execution_mode"] == "SERVICE_INTEGRATION", "NON_SERVICE_EXPECTED_MODE")
        need(
            expected["purpose"] in {"DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN"}
            and expected["arm_id"] in {"B0", "B1", "B2", "B3", "P"},
            "UNSUPPORTED_PURPOSE_OR_ARM",
        )
        need(expected["seed_version"] == "mvp-301-v6", "UNSUPPORTED_REGISTERED_SEED", missing=True)
        for name in ("experiment_run_id", "user_id", "isolated_db_epoch"):
            identity(expected[name])
        reader = Reader(workspace, expected, capture["original_paths"])
        need(
            capture.get("protocol") == "mvp-arm-original-capture-v1",
            "MISSING_ACTUAL_CAPTURE",
            missing=True,
        )
        registration, current_sources = source_and_registration(reader, registry_ref, expected)
        envelope = reader.resolve(capture["artifact_ref"], "ARM_EXECUTION")
        need(capture["artifact_ref"].get("json_pointer") == "", "INCOMPLETE_EXECUTION_ENVELOPE")
        payload = envelope["payload"]
        call, action = identity(payload.get("service_call_id")), identity(payload.get("action_id"))
        need(envelope.get("service_call_id") == call, "ENVELOPE_CALL_BINDING_MISMATCH")
        need(payload.get("capture_origin") == "PRODUCTION_SERVICE_CALL", "UNKNOWN_CAPTURE_ORIGIN")
        need(payload.get("economic_execution_verified") is False, "RAW_ECONOMIC_FLAG_WAS_PROMOTED")
        epoch_snapshots(reader, payload, expected)
        # Every advertised original, including callback-failure artifacts, is byte/binding checked.
        for key, path in reader.paths.items():
            body = strict(reader.file(path, key))
            need(
                body.get("bindings") == expected and body.get("service_call_id") == call,
                "UNUSED_RAW_CALL_OR_BINDINGS_MISMATCH",
            )
        phases = [
            (reader.resolve(ref, "ACTUAL_LIVE_PHASE"), ref)
            for ref in payload.get("live_phase_observation_refs", [])
        ]
        terminals: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for raw, ref in phases:
            need(
                raw.get("service_call_id") == call and raw.get("bindings") == expected,
                "LIVE_PHASE_CALL_OR_BINDINGS_MISMATCH",
            )
            if raw.get("state") in TERMINALS:
                phase_id = identity(raw.get("phase_id"))
                need(phase_id not in terminals, "DUPLICATE_TERMINAL_PHASE")
                terminals[phase_id] = (raw, ref)
        refs = payload.get("independent_phase_recheck_refs")
        need(
            isinstance(refs, list) and bool(refs),
            "MISSING_INDEPENDENT_RECHECK_ORIGINALS",
            missing=True,
        )
        checked = set()
        for ref in refs:
            probe = reader.resolve(ref, "ACTUAL_INDEPENDENT_PHASE_RECHECK")
            phase_id = identity(probe.get("phase_id"))
            need(
                phase_id in terminals and phase_id not in checked,
                "MISSING_OR_DUPLICATE_RECHECK_PHASE",
                missing=True,
            )
            raw, raw_ref = terminals[phase_id]
            complete_phase_graph(phases, raw)
            row = phase_recheck(
                raw, probe, raw_ref, expected, call, action, registration["database_name"]
            )
            request_and_persistence(probe, payload)
            registered = registration["artifacts"]["input"].get("registered_business_clocks", [])
            need(
                any(at(raw["business_clock"]) == at(value) for value in registered),
                "UNREGISTERED_BUSINESS_CLOCK",
            )
            row["recheck_ref"] = ref
            result["phases"].append(row)
            checked.add(phase_id)
        need(checked == set(terminals), "MISSING_RECHECK_FOR_REACHED_TERMINAL", missing=True)
        need(
            len({row["phase"] for row in result["phases"]}) == len(result["phases"]),
            "REPEATED_PHASE_KIND_IN_CALL",
        )
        need(
            len({row["target_xid"] for row in result["phases"]}) == len(result["phases"]),
            "TARGET_PHASE_XIDS_NOT_DISTINCT",
        )
        kinds = {row["phase"] for row in result["phases"]}
        if kinds == PHASES:
            statuses = {row["phase"]: row["actual_status"] for row in result["phases"]}
            if set(statuses.values()) == {"committed"}:
                result["category"] = "THREE_TRANSACTION_ARTIFACTS_CONSISTENT"
            elif statuses == {
                "APPLICATION_RESERVATION": "committed",
                "INDEPENDENT_BANK": "committed",
                "APPLICATION_PROJECTION": "aborted",
            }:
                result["category"] = "BANK_COMMITTED_PROJECTION_ROLLED_BACK_ARTIFACTS_CONSISTENT"
            else:
                raise EvidenceError("UNSUPPORTED_ACTUAL_TRANSACTION_PATH", missing=True)
        else:
            raise EvidenceError(
                "PARTIAL_OR_HISTORICAL_PATH_REQUIRES_SEPARATE_ORIGINAL_CLASSIFICATION", missing=True
            )
        for name, expected_sha in current_sources.items():
            need(
                sha((reader.root / name).read_bytes()) == expected_sha,
                "CURRENT_SOURCE_CHANGED_DURING_VALIDATION",
            )
        result["transaction_artifacts_verified"] = True
        result["status"] = "TRANSACTION_ARTIFACTS_CONSISTENT_ACTUAL_RUN_BINDING_MISSING"
        result["missing_reason"] = "ACTUAL_RUN_TO_CAPTURE_BYTE_BINDING_NOT_IMPLEMENTED"
        # No status/string/file name can promote a saved capture to actual runtime proof.
        if actual_run_ref is not None:
            reader.descriptor(actual_run_ref)
            result["actual_run_ref"] = actual_run_ref
            result["missing_reason"] = "ACTUAL_RUN_BINDING_ADAPTER_NOT_IMPLEMENTED"
    except EvidenceError as error:
        result["transaction_artifacts_verified"] = False
        result["status"] = "MISSING" if error.missing else "INVALID"
        result["missing_reason"] = str(error)
    except (KeyError, IndexError, TypeError, ValueError, OSError) as error:
        result["transaction_artifacts_verified"] = False
        result["status"] = (
            "MISSING" if isinstance(error, (KeyError, IndexError, OSError)) else "INVALID"
        )
        result["missing_reason"] = type(error).__name__
    return result
