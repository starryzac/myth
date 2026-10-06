"""Mixed DEVELOPMENT contract risks. Every fake below is explicitly TOOL_ONLY, no PG."""

import copy
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

import pytest
from app.domain.demo_identity import DEMO_USER_ID
from app.services import full_native_cases as module
from app.services.scenario_runner import ScenarioRunner
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

NOW = "2026-10-04T10:00:00+08:00"
EPOCH = UUID(int=3)


def step(identity: str, kind: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"step_id": identity, "kind": kind, "at": NOW, "inputs": inputs or {}}


def case(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "protocol": "full-family-case-input-v1",
        "profile": "FULL",
        "purpose": "DEVELOPMENT",
        "family_id": "TOOL_ONLY",
        "scenario_id": "TOOL_ONLY",
        "initial_state": {"mode": "EXISTING"},
        "steps": steps,
    }


def raw(value: dict[str, Any]) -> bytes:
    return json.dumps(value, allow_nan=False).encode()


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "FULL_FAMILY_FROZEN"),
        ("profile", "MVP"),
        ("success", True),
        ("bank_authority", True),
        ("frozen_case_sha256", "0" * 64),
    ],
)
def test_no_frozen_promotion_or_result_permission_injection(field: str, value: Any) -> None:
    body = case([step("read", "FULL_ANNUAL_READ")])
    body[field] = value
    with pytest.raises(ValueError):
        module.validate_full_development_case(raw(body))


@pytest.mark.parametrize(
    "change", ["seed", "unknown", "future_ref", "extra_step", "duplicate", "naive"]
)
def test_strict_existing_originals(change: str) -> None:
    body = case([step("read", "FULL_ANNUAL_READ")])
    if change == "seed":
        body["initial_state"]["mode"] = "SEED_NEW"
    elif change == "unknown":
        body["steps"][0]["kind"] = "RESET_DATABASE"
    elif change == "future_ref":
        body["steps"][0]["inputs"] = {
            "body": {"$ref": {"step_id": "later", "pointer": "/result/x"}}
        }
    elif change == "extra_step":
        body["steps"][0]["permission"] = "USER"
    elif change == "duplicate":
        body["steps"].append(copy.deepcopy(body["steps"][0]))
    else:
        body["steps"][0]["at"] = "2026-10-04T10:00:00"
    with pytest.raises(ValueError):
        module.validate_full_development_case(raw(body))


def test_duplicate_json_keys_and_nonfinite_input_are_rejected() -> None:
    for original in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
        with pytest.raises(ValueError):
            module.validate_full_development_case(original)


def test_actual_target_guard_precedes_database_connection() -> None:
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError):
            module.FullNativeCaseRunner(engine, DEMO_USER_ID).run(
                raw(case([step("read", "FULL_ANNUAL_READ")])), EPOCH
            )
    finally:
        engine.dispose()


class ToolOnlyNative:
    observations: list[tuple[str, dict[str, Any]]] = []
    refusal = False

    def __init__(self, engine: Engine, user: UUID, epoch: UUID, now: datetime):
        self.engine, self.user, self.epoch, self.now = engine, user, epoch, now

    def __enter__(self) -> "ToolOnlyNative":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def _context(self, now: datetime) -> None:
        self.now = now

    def dispatch(self, kind: str, inputs: dict[str, Any], now: datetime) -> dict[str, Any]:
        self.observations.append((kind, inputs))
        body = {"original_id": str(EPOCH), "tool_only": True}
        status = 200
        if self.refusal and kind == "FULL_POLICY_VALIDATE":
            status, body = 422, {"error": {"code": "VALIDATION_ERROR", "message": "TOOL_ONLY"}}
        text = json.dumps(body)
        return {
            "status_code": status,
            "result": body,
            "original_response_text": text,
            "response_body_sha256": sha256(text.encode()).hexdigest(),
        }


@pytest.fixture
def tool_only_runner(monkeypatch: pytest.MonkeyPatch) -> module.FullNativeCaseRunner:
    monkeypatch.setattr(module, "FullNativeSteps", ToolOnlyNative)
    monkeypatch.setattr(module, "require_native_target", lambda *args: datetime.now(UTC))
    monkeypatch.setattr(module, "audit_command_guard", lambda *args: nullcontext())
    monkeypatch.setattr(
        ScenarioRunner,
        "_dispatch",
        lambda self, step: {
            "legacy_original": step.inputs,
            "tool_only": True,
        },
    )
    ToolOnlyNative.observations, ToolOnlyNative.refusal = [], False
    return module.FullNativeCaseRunner(create_engine("sqlite://"), DEMO_USER_ID)


def test_mixed_original_body_references_preserve_entire_http_capture(
    tool_only_runner: module.FullNativeCaseRunner,
) -> None:
    body = case(
        [
            step("native", "FULL_POLICY_VALIDATE", {"body": {}}),
            step(
                "legacy",
                "LOOKUP_ACCOUNT",
                {
                    "external_ref": {
                        "$ref": {"step_id": "native", "pointer": "/result/original_id"},
                    }
                },
            ),
        ]
    )
    original = raw(body)
    value = tool_only_runner.run(original, EPOCH)
    assert value["original_input_sha256"] == sha256(original).hexdigest()
    assert value["steps"][0]["native_response"]["result"] == value["steps"][0]["result"]
    assert value["steps"][1]["result"]["legacy_original"] == {"external_ref": str(EPOCH)}
    assert value["steps"][1]["input_references"][0]["step_id"] == "native"
    assert value["financial_acceptance"] is False and value["metric_results"] is None


def test_unimplemented_full_fault_keeps_every_opportunity_without_fake_dispatch(
    tool_only_runner: module.FullNativeCaseRunner,
) -> None:
    missing = step("unknown", "FULL_ASSET_EXECUTE", {"body": {}})
    missing["fault"] = "DROP_BANK_RESPONSE"
    value = tool_only_runner.run(raw(case([missing, step("later", "FULL_ANNUAL_READ")])), EPOCH)
    assert value["status"] == "MISSING_NATIVE_CAPABILITY"
    assert len(value["steps"]) == value["original_step_count"] == 2
    assert "missing" in value["steps"][0]
    assert "skip" in value["steps"][1] and "result" not in value["steps"][1]
    assert ToolOnlyNative.observations == []


def test_expected_api_refusal_retains_error_bytes_but_cannot_supply_result_reference(
    tool_only_runner: module.FullNativeCaseRunner,
) -> None:
    ToolOnlyNative.refusal = True
    first = step("bad", "FULL_POLICY_VALIDATE", {"body": {}})
    first["expected_error"] = {"code": "VALIDATION_ERROR", "status_code": 422}
    body = case(
        [
            first,
            step(
                "ref",
                "LOOKUP_ACCOUNT",
                {
                    "external_ref": {
                        "$ref": {"step_id": "bad", "pointer": "/result/original_id"},
                    }
                },
            ),
        ]
    )
    value = tool_only_runner.run(raw(body), EPOCH)
    assert value["steps"][0]["expected_error_matched"] is True
    assert "native_response" in value["steps"][0] and "result" not in value["steps"][0]
    assert value["steps"][1]["error"]["code"] == "INVALID_SCENARIO_INPUT"
    assert value["status"] == "FAILED"


def test_success_against_expected_refusal_keeps_actual_result_and_stops(
    tool_only_runner: module.FullNativeCaseRunner,
) -> None:
    first = step("real-return", "FULL_POLICY_VALIDATE", {"body": {}})
    first["expected_error"] = {"code": "VALIDATION_ERROR", "status_code": 422}
    value = tool_only_runner.run(raw(case([first, step("later", "FULL_ANNUAL_READ")])), EPOCH)
    assert value["steps"][0]["expectation_failure"] == "EXPECTED_ERROR_NOT_OBSERVED"
    assert "result" in value["steps"][0] and value["status"] == "FAILED"
    assert "skip" in value["steps"][1]


def test_native_bytes_cannot_be_substituted_by_a_success_string() -> None:
    with pytest.raises(ValueError, match="bytes/body/hash"):
        module._http_result(
            {
                "status_code": 200,
                "result": {"success": True},
                "original_response_text": '{"success":false}',
                "response_body_sha256": "0" * 64,
            },
            {},
        )


def test_noninjected_timeout_is_not_reported_as_bank_commit_or_response_loss(
    tool_only_runner: module.FullNativeCaseRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timed_out(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("TOOL_ONLY_TRANSPORT_TIMEOUT_NO_BANK_EVIDENCE")

    monkeypatch.setattr(ToolOnlyNative, "dispatch", timed_out)
    value = tool_only_runner.run(raw(case([step("read", "FULL_ANNUAL_READ")])), EPOCH)
    assert value["steps"][0]["error"]["code"] == "ACTUAL_TIMEOUT"
    assert value["status"] == "FAILED" and value["financial_acceptance"] is False
