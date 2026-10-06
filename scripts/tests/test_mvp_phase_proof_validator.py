"""TOOL_TEST_ONLY synthetic byte graphs; never product, SQL, or money execution."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts import mvp_phase_proof_validator as v

TIME = "2026-10-03T16:01:00Z"
OBSERVED = "2026-10-05T08:54:18+00:00"
STARTED = "2026-10-05T08:54:19Z"
FINISHED = "2026-10-05T08:54:20+00:00"


class Fixture:
    def __init__(self, *, rollback: bool = False) -> None:
        base = Path(__file__).resolve().parents[2] / ".runtime/W1-phase-validator-tool-fixtures"
        self.root = (base / uuid4().hex).resolve()
        self.root.mkdir(parents=True)
        self.owner, self.action, self.call, self.epoch = (str(uuid4()) for _ in range(4))
        self.database = "bf_test_" + uuid4().hex
        self.expected = {
            "experiment_run_id": str(uuid4()),
            "case_id": "TOOL_TEST_ONLY",
            "arm_id": "P",
            "execution_mode": "SERVICE_INTEGRATION",
            "input_sha256": "",
            "oracle_sha256": "",
            "design_sha256": "",
            "rule_sha256": "",
            "source_sha256": "",
            "seed_version": "mvp-301-v6",
            "isolated_db_epoch": self.epoch,
            "purpose": "DEVELOPMENT",
            "user_id": self.owner,
        }
        self.originals: dict[str, str] = {}
        self.artifacts: dict[str, dict[str, str]] = {}
        source_rows = []
        for name in (
            "apps/api/app/services/execution_observations.py",
            "apps/api/app/services/experiment_arms.py",
            "scripts/mvp_arm_executor.py",
            "scripts/mvp_observations.py",
            "scripts/mvp_trace_metrics.py",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = b"# TOOL_TEST_ONLY synthetic source, no financial evaluator\n"
            path.write_bytes(raw)
            archive = self.file(raw)
            source_rows.append({"original_path": name, **archive})
        for name in ("input", "oracle", "design", "rule", "source"):
            body: dict[str, Any] = {
                "case_id": "TOOL_TEST_ONLY",
                "purpose": "DEVELOPMENT",
                "evidence": "TOOL_TEST_ONLY",
            }
            if name == "source":
                body["implementation_files"] = source_rows
            if name == "input":
                body["registered_business_clocks"] = [TIME]
            ref = self.file(v.canonical(body))
            self.artifacts[name] = ref
            self.expected[name + "_sha256"] = ref["sha256"]
        self.registry = self.file(
            v.canonical(
                {
                    "protocol": "mvp-arm-provider-registry-v1",
                    "bindings": self.expected,
                    "database_name": self.database,
                    "artifact_refs": self.artifacts,
                }
            )
        )
        effect: dict[str, Any] = {
            "operation_id": self.action,
            "user_id": self.owner,
            "cash_uses": [],
            "income_uses": [],
            "policy_version_ids": [],
            "liability": None,
        }
        self.effect_hash = v.sha(v.canonical(effect))
        command = {"effect": effect, "effect_hash": self.effect_hash}
        request = {"execution": command}
        self.action_row = {
            "id": self.action,
            "user_id": self.owner,
            "request": request,
            "request_hash": v.sha(v.canonical(request)),
        }
        self.bank_row = {
            "id": self.action,
            "user_id": self.owner,
            "action_plan_id": self.action,
            "request": command,
            "request_hash": v.sha(v.canonical(command)),
        }
        posting = {"id": str(uuid4()), "operation_id": self.action, "user_id": self.owner}
        receipt = {
            "id": str(uuid4()),
            "user_id": self.owner,
            "action_plan_id": self.action,
            "receipt_ref": "bank-operation:" + self.action,
            "response": {"bank_operation_id": self.action, "posting_ids": [posting["id"]]},
        }
        self.phases: list[list[dict[str, Any]]] = []
        self.probes: list[dict[str, Any]] = []
        for index, kind in enumerate(
            ("APPLICATION_RESERVATION", "INDEPENDENT_BANK", "APPLICATION_PROJECTION")
        ):
            aborted = rollback and kind == "APPLICATION_PROJECTION"
            identity = {
                "database_name": self.database,
                "backend_pid": 100 + index,
                "transaction_id": str(2**32 + 50 + index),
            }
            common = {
                "protocol": "execution-live-phase-observation-v2",
                "bindings": self.expected,
                "service_call_id": self.call,
                "action_id": self.action,
                "user_id": self.owner,
                "epoch_id": self.epoch,
                "phase": kind,
                "phase_id": str(uuid4()),
                "sql_identity": identity,
                "business_clock": TIME,
                "observed_at": OBSERVED,
                "financial_effect_verified": False,
                "outer_after_commit_observed": False,
                "outer_after_rollback_observed": False,
                "live": {"nested_savepoint_active": False},
            }
            states = ["LIVE_TRANSACTION_BEGIN"]
            if kind != "INDEPENDENT_BANK":
                states.append("USER_LOCK_RETURNED")
            if not aborted:
                states.append("BEFORE_OUTER_COMMIT")
            rows = [{**deepcopy(common), "state": state} for state in states]
            rows.append(
                {
                    **deepcopy(common),
                    "state": "ROLLED_BACK" if aborted else "COMMITTED",
                    "outer_after_commit_observed": not aborted,
                    "outer_after_rollback_observed": aborted,
                }
            )
            self.phases.append(rows)
            tables: dict[str, Any] = {name: [] for name in v.TABLES}
            tables["users"] = [{"id": self.owner, "is_simulated": True}]
            tables["action_plans"] = [deepcopy(self.action_row)]
            if index > 0:
                tables["bank_operations"] = [deepcopy(self.bank_row)]
            if index == 2 and not aborted:
                tables["action_receipts"], tables["simulated_bank_postings"] = (
                    [deepcopy(receipt)],
                    [deepcopy(posting)],
                )
            queries: list[dict[str, Any]] = [
                {"sql": "SET TRANSACTION READ ONLY", "parameters": {}},
                {"sql": v.STATUS_SQL, "parameters": {"target_xid": identity["transaction_id"]}},
            ]
            for name in sorted(v.TABLES):
                owner_column = "id" if name == "users" else "user_id"
                clause = name + "." + owner_column + " = :owner"
                parameters = {"owner": self.owner}
                if name not in {"users", "accounts", "evidence_items"}:
                    key = (
                        "id"
                        if name == "action_plans"
                        else "operation_id"
                        if name == "simulated_bank_postings"
                        else "action_plan_id"
                    )
                    clause += " AND " + name + "." + key + " = :action"
                    parameters["action"] = self.action
                queries.append(
                    {
                        "table": name,
                        "sql": "SELECT "
                        + name
                        + ".id FROM "
                        + name
                        + " WHERE "
                        + clause
                        + " ORDER BY "
                        + name
                        + ".id",
                        "parameters": parameters,
                    }
                )
            self.probes.append(
                {
                    "protocol": "execution-independent-phase-recheck-v2",
                    "bindings": self.expected,
                    "service_call_id": self.call,
                    "action_id": self.action,
                    "user_id": self.owner,
                    "epoch_id": self.epoch,
                    "phase": kind,
                    "phase_id": common["phase_id"],
                    "terminal_state": rows[-1]["state"],
                    "target_sql_identity": deepcopy(identity),
                    "probe_sql_identity": {
                        "database_name": self.database,
                        "backend_pid": 200 + index,
                        "transaction_id": str(2**32 + 100 + index),
                        "isolation": "repeatable read",
                        "read_only": "on",
                    },
                    "readonly_outer_context_exited": "NORMAL",
                    "capture_state": "CAPTURED_VALIDATOR_PENDING",
                    "missing_reason": None,
                    "target_status": "aborted" if aborted else "committed",
                    "expected_target_status": "aborted" if aborted else "committed",
                    "capability": {"procedure": "pg_xact_status(xid8)", "may_execute": True},
                    "financial_effect_verified": False,
                    "independent_phase_proof_verified": False,
                    "business_clock": TIME,
                    "started_at": STARTED,
                    "finished_at": FINISHED,
                    "tables": tables,
                    "queries": queries,
                }
            )
        self.snapshot: dict[str, Any] = {
            "users": [{"id": self.owner, "is_simulated": True}],
            "audit_epochs": [{"id": self.epoch, "user_id": self.owner, "status": "OPEN"}],
        }

    def file(self, raw: bytes) -> dict[str, str]:
        path = self.root / "originals" / (uuid4().hex + ".json")
        path.parent.mkdir(exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)
        return {"path": str(path), "sha256": v.sha(raw)}

    def raw(self, kind: str, payload: dict[str, Any], pointer: str) -> dict[str, str]:
        body = {
            "protocol": "mvp-raw-observation-v1",
            "kind": kind,
            "bindings": self.expected,
            "service_call_id": self.call,
            "payload": payload,
        }
        ref = self.file(v.canonical(body))
        self.originals[ref["sha256"]] = ref["path"]
        return {
            "artifact_sha256": ref["sha256"],
            "json_pointer": pointer,
            "value_sha256": v.sha(v.canonical(v.pointer(body, pointer))),
        }

    def capture(self) -> dict[str, Any]:
        refs, probes = [], []
        for rows, probe in zip(self.phases, self.probes, strict=True):
            phase_refs = [
                self.raw("ACTUAL_LIVE_PHASE", {"phase": row}, "/payload/phase") for row in rows
            ]
            refs.extend(phase_refs)
            probe["terminal_value_sha256"] = phase_refs[-1]["value_sha256"]
            probe["table_inventory"] = {
                name: {"row_ids": [row["id"] for row in rows], "sha256": v.sha(v.canonical(rows))}
                for name, rows in probe["tables"].items()
            }
            probes.append(
                self.raw("ACTUAL_INDEPENDENT_PHASE_RECHECK", {"recheck": probe}, "/payload/recheck")
            )
        snapshot_ref = self.raw("BUSINESS_SNAPSHOT", {"tables": self.snapshot}, "/payload/tables")
        payload = {
            "capture_origin": "PRODUCTION_SERVICE_CALL",
            "economic_execution_verified": False,
            "effect_hash": self.effect_hash,
            "action_id": self.action,
            "service_call_id": self.call,
            "before_snapshot_ref": snapshot_ref,
            "after_snapshot_ref": snapshot_ref,
            "live_phase_observation_refs": refs,
            "independent_phase_recheck_refs": probes,
        }
        envelope = self.raw("ARM_EXECUTION", payload, "")
        return {
            "protocol": "mvp-arm-original-capture-v1",
            "artifact_ref": envelope,
            "original_paths": self.originals,
        }

    def validate(self) -> dict[str, Any]:
        return dict(
            v.validate_capture(self.capture(), self.expected, self.registry, workspace=self.root)
        )


@pytest.mark.parametrize("rollback", [False, True])
def test_synthetic_complete_graph_never_promotes_runtime_or_economics(rollback: bool) -> None:
    result = Fixture(rollback=rollback).validate()
    assert result["transaction_artifacts_verified"] is True, result
    assert result["actual_runtime_evidence_verified"] is False
    assert result["economic_execution_verified"] is False
    assert result["execution_mode"] is None
    assert result["raw_flags_modified"] is False
    assert result["missing_reason"] == "ACTUAL_RUN_TO_CAPTURE_BYTE_BINDING_NOT_IMPLEMENTED"
    assert len(result["phases"]) == 3


@pytest.mark.parametrize(
    ("section", "key", "value", "reason"),
    [
        (
            "raw",
            "protocol",
            "execution-live-phase-observation-v1",
            "OLD_OR_UNSUPPORTED_PHASE_PROTOCOL",
        ),
        ("raw", "service_call_id", str(uuid4()), "LIVE_PHASE_CALL_OR_BINDINGS_MISMATCH"),
        ("raw", "user_id", str(uuid4()), "LIVE_PHASE_OWNER_OR_IDENTITY_MISMATCH"),
        ("raw", "action_id", str(uuid4()), "LIVE_PHASE_OWNER_OR_IDENTITY_MISMATCH"),
        ("raw", "epoch_id", str(uuid4()), "LIVE_PHASE_OWNER_OR_IDENTITY_MISMATCH"),
        ("raw", "outer_after_commit_observed", False, "CONTRADICTORY_OR_SAVEPOINT_COMMIT_FLAGS"),
        ("raw", "financial_effect_verified", True, "RAW_FLAGS_WERE_PROMOTED"),
        ("probe", "service_call_id", str(uuid4()), "PHASE_OR_PROBE_CALL_BINDING_MISMATCH"),
        ("probe", "phase_id", str(uuid4()), "MISSING_OR_DUPLICATE_RECHECK_PHASE"),
        ("probe", "user_id", str(uuid4()), "PHASE_OR_PROBE_OWNER_ACTION_EPOCH_MISMATCH"),
        ("probe", "business_clock", "2026-10-03T16:02:00Z", "BUSINESS_CLOCK_MISMATCH"),
        ("probe", "started_at", "2026-10-05T08:00:00Z", "PROBE_CAPTURE_TIME_ORDER_INVALID"),
        ("probe", "finished_at", "2026-10-05T08:54:20", "NAIVE_TIME"),
        ("probe", "target_status", "in progress", "TARGET_STATUS_NOT_FINAL_OR_MISSING"),
        ("probe", "target_status", "aborted", "ACTUAL_TRANSACTION_STATUS_CONTRADICTS_ORIGINAL"),
        (
            "probe",
            "expected_target_status",
            "aborted",
            "ACTUAL_TRANSACTION_STATUS_CONTRADICTS_ORIGINAL",
        ),
        ("probe", "readonly_outer_context_exited", "FAILED", "MISSING_COMPLETED_READONLY_PROBE"),
        ("probe", "capture_state", "MISSING", "PRODUCER_RECHECK_EXPLICITLY_INCOMPLETE"),
        ("probe", "financial_effect_verified", True, "RAW_FLAGS_WERE_PROMOTED"),
        ("probe", "independent_phase_proof_verified", True, "RAW_FLAGS_WERE_PROMOTED"),
    ],
)
def test_bound_original_semantic_failure(section: str, key: str, value: Any, reason: str) -> None:
    fixture = Fixture()
    body = fixture.phases[0][-1] if section == "raw" else fixture.probes[0]
    body[key] = value
    result = fixture.validate()
    assert result["transaction_artifacts_verified"] is False, result
    assert result["missing_reason"] == reason, result


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("transaction_id", "4294967346", "SAME_TRANSACTION_NOT_INDEPENDENT"),
        ("transaction_id", "-1", "INVALID_FULL_XID8"),
        ("transaction_id", "18446744073709551616", "XID8_OVERFLOW"),
        ("backend_pid", 100, "SAME_CONNECTION_NOT_INDEPENDENT"),
        ("backend_pid", True, "INVALID_BACKEND_PID"),
        ("database_name", "formal_history", "CROSS_DATABASE_PROBE"),
        ("isolation", "read committed", "PROBE_NOT_RR_READ_ONLY"),
        ("read_only", "off", "PROBE_NOT_RR_READ_ONLY"),
    ],
)
def test_independent_sql_identity(key: str, value: Any, reason: str) -> None:
    fixture = Fixture()
    fixture.probes[0]["probe_sql_identity"][key] = value
    result = fixture.validate()
    assert result["transaction_artifacts_verified"] is False, result
    assert result["missing_reason"] == reason, result


@pytest.mark.parametrize(
    "case",
    [
        "begin",
        "lock",
        "before",
        "savepoint",
        "owner",
        "sql",
        "status_sql",
        "action",
        "effect",
        "receipt",
        "postings",
        "epoch",
        "user",
        "source",
        "bytes",
        "missing",
        "pointer",
        "value",
        "partial",
        "current_source",
    ],
)
def test_missing_or_tampered_originals(case: str) -> None:
    f = Fixture()
    if case == "begin":
        del f.phases[0][0]
    elif case == "lock":
        del f.phases[0][1]
    elif case == "before":
        del f.phases[0][-2]
    elif case == "savepoint":
        f.phases[0][-2]["live"]["nested_savepoint_active"] = True
    elif case == "owner":
        f.probes[0]["tables"]["action_plans"][0]["user_id"] = str(uuid4())
    elif case == "sql":
        f.probes[0]["queries"] = [q for q in f.probes[0]["queries"] if q.get("table") != "users"]
    elif case == "status_sql":
        f.probes[0]["queries"][1]["parameters"] = {"target_xid": "1"}
    elif case == "action":
        f.probes[0]["tables"]["action_plans"] = []
    elif case == "effect":
        row = f.probes[0]["tables"]["action_plans"][0]
        row["request"]["execution"]["effect"]["liability"] = {"evidence_ids": []}
        row["request_hash"] = v.sha(v.canonical(row["request"]))
    elif case == "receipt":
        f.probes[2]["tables"]["action_receipts"][0]["response"]["bank_operation_id"] = str(uuid4())
    elif case == "postings":
        f.probes[2]["tables"]["action_receipts"][0]["response"]["posting_ids"] = []
    elif case == "epoch":
        f.snapshot["audit_epochs"][0]["status"] = "CLOSED"
    elif case == "user":
        f.snapshot["users"][0]["is_simulated"] = False
    elif case == "partial":
        f.phases, f.probes = f.phases[:1], f.probes[:1]
    capture = f.capture()
    if case == "bytes":
        Path(next(iter(f.originals.values()))).write_bytes(b"{}")
    elif case == "missing":
        Path(next(iter(f.originals.values()))).unlink()
    elif case == "source":
        (f.root / "apps/api/app/services/experiment_arms.py").write_bytes(b"# drift")
    elif case == "current_source":
        (f.root / "apps/api/app/new_runtime.py").write_bytes(b"# unregistered")
    elif case == "pointer":
        capture["artifact_ref"]["json_pointer"] = "/payload/missing"
    elif case == "value":
        capture["artifact_ref"]["value_sha256"] = "0" * 64
    result = v.validate_capture(capture, f.expected, f.registry, workspace=f.root)
    assert result["transaction_artifacts_verified"] is False, result
    assert result["status"] in {"INVALID", "MISSING"}, result
    assert result["actual_runtime_evidence_verified"] is False


def test_fake_passed_actual_run_file_is_not_attestation() -> None:
    f = Fixture()
    actual = f.file(
        v.canonical({"status": "PASSED", "tests": "3 PASS", "evidence": "TOOL_TEST_ONLY"})
    )
    result = v.validate_capture(
        f.capture(), f.expected, f.registry, workspace=f.root, actual_run_ref=actual
    )
    assert result["transaction_artifacts_verified"] is True
    assert result["actual_runtime_evidence_verified"] is False
    assert result["economic_execution_verified"] is False
    assert result["missing_reason"] == "ACTUAL_RUN_BINDING_ADAPTER_NOT_IMPLEMENTED"


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_strict_json_rejects_duplicate_and_nonfinite(raw: bytes) -> None:
    with pytest.raises(v.EvidenceError):
        v.strict(raw)


def test_equivalent_clock_representation_retains_original_bytes() -> None:
    f = Fixture()
    for probe in f.probes:
        probe["business_clock"] = "2026-10-03T16:01:00+00:00"
    capture = f.capture()
    before = {key: Path(path).read_bytes() for key, path in f.originals.items()}
    result = v.validate_capture(capture, f.expected, f.registry, workspace=f.root)
    assert result["transaction_artifacts_verified"] is True, result
    assert before == {key: Path(path).read_bytes() for key, path in f.originals.items()}


def test_no_cross_invocation_source_or_original_cache() -> None:
    f = Fixture()
    capture = f.capture()
    assert (
        v.validate_capture(capture, f.expected, f.registry, workspace=f.root)[
            "transaction_artifacts_verified"
        ]
        is True
    )
    Path(next(iter(f.originals.values()))).write_bytes(json.dumps({"status": "PASSED"}).encode())
    assert (
        v.validate_capture(capture, f.expected, f.registry, workspace=f.root)["status"] == "INVALID"
    )


def test_new_provider_registry_is_missing_until_exact_source_clock_adapter_exists() -> None:
    f = Fixture()
    registry = v.strict(Path(f.registry["path"]).read_bytes())
    registry["protocol"] = "mvp-arm-provider-registry-v2"
    f.registry = f.file(v.canonical(registry))
    result = f.validate()
    assert result["status"] == "MISSING"
    assert result["missing_reason"] == "REGISTRY_V2_SOURCE_CLOCK_ADAPTER_NOT_IMPLEMENTED"
    assert result["transaction_artifacts_verified"] is False
    assert result["actual_runtime_evidence_verified"] is False
    assert result["economic_execution_verified"] is False
