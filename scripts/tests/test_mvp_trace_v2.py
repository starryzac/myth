"""TOOL_ONLY native/HTTP200 error transport risks, never formal measurement evidence."""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from scripts.mvp_observations import ObservationError
from scripts.mvp_trace_metrics import (
    TraceBundle,
    _failure_capture,
    _failure_transport,
    _failure_unit,
    digest_value,
)

WHEN = "2026-10-05T10:00:00+00:00"


class Originals:
    """Pure transport-only reader double: no actual Bundle or financial result."""

    def __init__(self) -> None:
        self.v2 = True
        self.bindings = {
            "case_id": "TOOL_ONLY_ERROR",
            "purpose": "MVP_FROZEN",
            "user_id": "00000000-0000-4000-8000-000000000001",
        }
        self.begin = datetime.fromisoformat(WHEN)
        self.end = datetime.fromisoformat("2026-10-05T11:00:00+00:00")
        self.steps: list[dict[str, Any]] = [
            {
                "step_id": "prepare",
                "kind": "PREPARE_ACTION",
                "at": WHEN,
                "error": {
                    "code": "INVALID_SCENARIO_STEP",
                    "status_code": 422,
                    "message": "TOOL_ONLY",
                },
            },
            {
                "step_id": "repair",
                "kind": "PREPARE_ACTION",
                "at": WHEN,
                "result": {"simulation": True},
            },
        ]
        self.frozen_view = SimpleNamespace(
            case_binding={
                "native_execution_binding": {
                    "validated_execution_input_sha256": "a" * 64,
                    "validated_execution_input": {
                        "steps": [
                            {
                                key: value
                                for key, value in row.items()
                                if key in {"step_id", "kind", "at"}
                            }
                            for row in self.steps
                        ]
                    },
                }
            }
        )
        self.result: dict[str, Any] = {
            "protocol": "bounded-funds-scenario-v1",
            "scenario_id": "TOOL_ONLY_ERROR",
            "purpose": "MVP_FROZEN",
            "input_sha256": "a" * 64,
            "status": "EXECUTED",
            "steps": self.steps,
        }
        self.payload: dict[str, Any] = {
            "result": self.result,
            "capture": {
                "complete": True,
                "run_ref": self.bindings,
                "failed_step_ids": ["prepare"],
                "error_record_ids": ["error-1"],
            },
        }
        self.raw = {"kind": "SCENARIO_RESULT", "ref": {"sha256": "b" * 64}, "payload": self.payload}
        self.values: dict[str, dict[str, Any]] = {
            "b" * 64: {"kind": "SCENARIO_RESULT", "payload": self.payload}
        }

    def present(self, kind: str) -> list[dict[str, Any]]:
        return [self.raw] if kind == "SCENARIO_RESULT" else []

    def ref(self, pointer: str, *, artifact: str = "b" * 64) -> dict[str, Any]:
        value: Any = self.values[artifact]
        for token in pointer.split("/")[1:]:
            value = value[int(token)] if isinstance(value, list) else value[token]
        return {
            "artifact_sha256": artifact,
            "json_pointer": pointer,
            "value_sha256": digest_value(value),
        }

    def resolve(self, ref: Any, *, kind: str) -> Any:
        original = self.values[ref["artifact_sha256"]]
        assert original["kind"] == kind
        assert ref == self.ref(ref["json_pointer"], artifact=ref["artifact_sha256"])
        value: Any = original
        for token in ref["json_pointer"].split("/")[1:]:
            value = value[int(token)] if isinstance(value, list) else value[token]
        return value

    def record(self) -> dict[str, Any]:
        return {
            "failure_channel": "SCENARIO_STEP",
            "scenario_result_ref": self.ref("/payload/result"),
            "service_step_ref": self.ref("/payload/result/steps/0"),
            "step_index": 0,
        }

    def expected(self) -> dict[str, Any]:
        return {"causal_step_ids": ["prepare", "repair"]}


def read_transport(
    originals: Originals, record: dict[str, Any] | None = None
) -> tuple[dict[str, Any], datetime, bool]:
    return _failure_transport(originals, originals.expected(), record or originals.record(), {})  # type: ignore[arg-type]


def test_actual_native_error_is_not_required_to_be_http() -> None:
    originals = Originals()
    view, at, native = read_transport(originals)
    assert native is True and view["exchange_id"] is None
    assert view["response_body"]["error_code"] == "INVALID_SCENARIO_STEP"
    assert view["business_status_code"] == 422 and at == originals.begin
    assert "exchange_id" not in originals.steps[0]
    assert originals.result["status"] == "EXECUTED"  # Matched refusal can continue.


@pytest.mark.parametrize(
    "change",
    (
        "v1",
        "wrong_case",
        "wrong_purpose",
        "wrong_input",
        "wrong_order",
        "wrong_kind",
        "wrong_time",
        "out_of_window",
        "missing_error",
        "false_status",
        "duplicate_step",
        "undeclared_step",
        "wrong_pointer",
        "later_error",
        "unknown_channel",
    ),
)
def test_native_binding_or_success_cannot_claim_failure(change: str) -> None:
    originals = Originals()
    if change == "v1":
        originals.v2 = False
    elif change.startswith("wrong_") and change in {"wrong_case", "wrong_purpose", "wrong_input"}:
        key = {
            "wrong_case": "scenario_id",
            "wrong_purpose": "purpose",
            "wrong_input": "input_sha256",
        }[change]
        originals.result[key] = "REBOUND"
    elif change == "wrong_order":
        originals.steps.reverse()
    elif change == "wrong_kind":
        originals.steps[0]["kind"] = "EXTERNAL_FACT"
    elif change == "wrong_time":
        originals.steps[0]["at"] = "2026-10-05T10:01:00+00:00"
    elif change == "out_of_window":
        originals.end = datetime.fromisoformat("2026-10-05T09:00:00+00:00")
    elif change == "missing_error":
        originals.steps[0].pop("error")
    elif change == "false_status":
        originals.steps[0]["error"]["status_code"] = True
    elif change == "duplicate_step":
        originals.steps[1]["step_id"] = "prepare"
    elif change == "undeclared_step":
        originals.steps.append({"step_id": "extra", "kind": "READ_ACTION", "at": WHEN})
    elif change == "later_error":
        originals.steps[1]["error"] = copy.deepcopy(originals.steps[0]["error"])
    record = originals.record()
    if change == "wrong_pointer":
        record["service_step_ref"] = originals.ref("/payload/result/steps/1")
    elif change == "later_error":
        record["step_index"] = 1
        record["service_step_ref"] = originals.ref("/payload/result/steps/1")
    elif change == "unknown_channel":
        record["failure_channel"] = "SUCCESS_STRING"
    with pytest.raises((ObservationError, AssertionError)):
        read_transport(originals, record)


def http200(originals: Originals, *, index: int = 0) -> tuple[dict[str, Any], datetime, bool]:
    exchange: dict[str, Any] = {
        "exchange_id": "http-1",
        "step_id": "prepare",
        "user_id": originals.bindings["user_id"],
        "started_at": WHEN,
        "finished_at": WHEN,
        "status_code": 200,
        "response_body": originals.result,
    }
    originals.values["c" * 64] = {"kind": "HTTP_EXCHANGES", "payload": {"exchanges": [exchange]}}
    http_ref = originals.ref("/payload/exchanges/0", artifact="c" * 64)
    inspected = dict(exchange, _original_ref=http_ref)
    record = {
        "failure_channel": "HTTP",
        "http_ref": http_ref,
        "business_step_ref": originals.ref(
            "/payload/exchanges/0/response_body/steps/" + str(index), artifact="c" * 64
        ),
        "step_index": index,
    }
    return _failure_transport(
        cast(TraceBundle, originals),
        originals.expected(),
        record,
        {"http-1": inspected},
    )


def test_http200_contains_actual_native_business_rejection() -> None:
    originals = Originals()
    view, _, native = http200(originals)
    assert native is False and view["response_body"] == {"error_code": "INVALID_SCENARIO_STEP"}
    original_exchange = originals.values["c" * 64]["payload"]["exchanges"][0]
    assert original_exchange["status_code"] == 200 and "steps" in original_exchange["response_body"]
    assert "_original_ref" not in original_exchange


@pytest.mark.parametrize("gap", ("input", "order", "kind", "clock", "first_cause", "success"))
def test_http200_cannot_bypass_declared_native_steps(gap: str) -> None:
    originals = Originals()
    index = 0
    if gap == "input":
        originals.result["input_sha256"] = "c" * 64
    elif gap == "order":
        originals.steps.reverse()
    elif gap == "kind":
        originals.steps[0]["kind"] = "EXTERNAL_FACT"
    elif gap == "clock":
        originals.steps[0]["at"] = "2026-10-05T10:01:00+00:00"
    elif gap == "first_cause":
        originals.steps[1]["error"] = copy.deepcopy(originals.steps[0]["error"])
        index = 1
    elif gap == "success":
        originals.steps[0].pop("error")
    with pytest.raises(ObservationError):
        http200(originals, index=index)


@pytest.mark.parametrize(
    "gap",
    (
        "capture_missing",
        "capture_incomplete",
        "wrong_run",
        "failed_step_omitted",
        "error_record_omitted",
        "actual_error_omitted",
        "wrong_channel",
    ),
)
def test_native_error_capture_keeps_actual_denominator(gap: str) -> None:
    originals = Originals()
    errors = {"error-1": {"step_id": "prepare", "failure_channel": "SCENARIO_STEP"}}
    if gap == "capture_missing":
        originals.payload.pop("capture")
    elif gap == "capture_incomplete":
        originals.payload["capture"]["complete"] = False
    elif gap == "wrong_run":
        originals.payload["capture"]["run_ref"] = {}
    elif gap == "failed_step_omitted":
        originals.payload["capture"]["failed_step_ids"] = []
    elif gap == "error_record_omitted":
        originals.payload["capture"]["error_record_ids"] = []
    elif gap == "actual_error_omitted":
        errors = {}
    elif gap == "wrong_channel":
        errors["error-1"]["failure_channel"] = "HTTP"
    with pytest.raises(ObservationError):
        _failure_capture(originals, {}, errors)  # type: ignore[arg-type]


def test_native_complete_capture_does_not_need_empty_http_success_wrapper() -> None:
    originals = Originals()
    _failure_capture(
        cast(TraceBundle, originals),
        {},
        {"error-1": {"step_id": "prepare", "failure_channel": "SCENARIO_STEP"}},
    )


def native_gate() -> tuple[Originals, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """TOOL_ONLY gate fixture, not a verified formal audit or financial run."""
    originals = Originals()
    source_bytes = b'def fixture_refusal():\n    raise ValueError("TOOL_ONLY")\n'
    source: dict[str, Any] = {
        "path": "tool-only/refusal.py",
        "original_path": "tool-only/refusal.py",
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "line": 2,
        "line_text": '    raise ValueError("TOOL_ONLY")',
        "symbol": "fixture_refusal",
    }
    originals.frozen_view.source_originals = lambda paths: [
        {"path": "tool-only-archive.bin", "sha256": source["sha256"]}
    ]
    originals.frozen_view.read = lambda path, digest: source_bytes
    error: dict[str, Any] = {
        "user_id": originals.bindings["user_id"],
        "exchange_id": None,
        "failure_id": "error-1",
        "step_id": "prepare",
        "error_code": "INVALID_SCENARIO_STEP",
        "cause_code": "TOOL_ONLY_DTO_REFUSAL",
        "logical_cause_key": "TOOL_ONLY_CAUSE",
        "occurred_at": WHEN,
        "source_ref": source,
        "run_ref": originals.bindings,
    }
    trace: dict[str, Any] = {
        key: value for key, value in error.items() if key not in {"user_id", "exchange_id"}
    }
    trace["frames"] = [source]
    originals.values["c" * 64] = {
        "kind": "ERROR_RECORDS",
        "payload": {"errors": [error], "traces": [trace]},
    }
    expected = dict(
        originals.expected(),
        first_failing_step="prepare",
        error_code=error["error_code"],
        cause_code=error["cause_code"],
        logical_cause_key=error["logical_cause_key"],
        source_ref=source,
        audit_cause_required=False,
    )
    record = dict(
        originals.record(),
        failure_id="error-1",
        first_failing_step="prepare",
        error_code=error["error_code"],
        cause_code=error["cause_code"],
        logical_cause_key=error["logical_cause_key"],
        source_ref=source,
        error_ref=originals.ref("/payload/errors/0", artifact="c" * 64),
        trace_ref=originals.ref("/payload/traces/0", artifact="c" * 64),
        audit_checkpoint_id="TOOL_ONLY_GATE",
        audit_event_id=None,
        run_ref=originals.bindings,
        _original_ref={"TOOL_ONLY": True},
    )
    # Complete A3 verification is tested by the original typed-audit suite.
    # This double tests A4's source/stack/run gate after that stage has passed.
    audits = {"TOOL_ONLY_GATE": {"verified": True, "_events": []}}
    return originals, expected, record, audits


def test_native_dto_refusal_still_requires_source_stack_run_and_complete_audit_gate() -> None:
    originals, expected, record, audits = native_gate()
    result = _failure_unit(originals, expected, record, {}, audits)  # type: ignore[arg-type]
    assert result["failure_id"] == "error-1"
    assert result["raw_refs"][1] == record["service_step_ref"]


@pytest.mark.parametrize(
    "gap",
    (
        "source_hash",
        "source_line",
        "source_symbol",
        "stack_leaf",
        "trace_code",
        "run",
        "audit_missing",
        "audit_failed",
        "audit_cause_required",
        "not_dto",
        "false_http",
    ),
)
def test_native_transport_cannot_bypass_full_localization_gate(gap: str) -> None:
    originals, expected, record, audits = native_gate()
    error = originals.values["c" * 64]["payload"]["errors"][0]
    trace = originals.values["c" * 64]["payload"]["traces"][0]
    if gap == "source_hash":
        record["source_ref"] = dict(record["source_ref"], sha256="d" * 64)
    elif gap == "source_line":
        error["source_ref"]["line"] = 1
    elif gap == "source_symbol":
        error["source_ref"]["symbol"] = "another_symbol"
    elif gap == "stack_leaf":
        trace["frames"] = []
    elif gap == "trace_code":
        trace["error_code"] = "ANOTHER_ERROR"
    elif gap == "run":
        trace["run_ref"] = {}
    elif gap == "audit_missing":
        audits = {}
    elif gap == "audit_failed":
        audits["TOOL_ONLY_GATE"]["verified"] = False
    elif gap == "audit_cause_required":
        expected["audit_cause_required"] = True
    elif gap == "not_dto":
        originals.steps[0]["error"]["code"] = "TOOL_OTHER_ERROR"
        error["error_code"] = trace["error_code"] = record["error_code"] = "TOOL_OTHER_ERROR"
        expected["error_code"] = "TOOL_OTHER_ERROR"
    elif gap == "false_http":
        error["exchange_id"] = "not_native"
    record["error_ref"] = originals.ref("/payload/errors/0", artifact="c" * 64)
    record["trace_ref"] = originals.ref("/payload/traces/0", artifact="c" * 64)
    record["service_step_ref"] = originals.ref("/payload/result/steps/0")
    record["scenario_result_ref"] = originals.ref("/payload/result")
    with pytest.raises(ObservationError):
        _failure_unit(originals, expected, record, {}, audits)  # type: ignore[arg-type]
