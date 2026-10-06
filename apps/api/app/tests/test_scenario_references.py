"""Pure pointer/expectation risks; no database or financial acceptance claims."""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from app.services import scenario_references as references
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import InitialState, Scenario, ScenarioStep
from pydantic import ValidationError
from sqlalchemy import create_engine

NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


def ref(step: str = "prior", pointer: str = "/result/action_id") -> dict[str, Any]:
    return {"$ref": {"step_id": step, "pointer": pointer}}


def step(identity: str, **changes: Any) -> ScenarioStep:
    return ScenarioStep.model_validate(
        {"step_id": identity, "kind": "EXECUTE_ACTION", "at": NOW, "inputs": {}} | changes
    )


def scenario(steps: list[ScenarioStep]) -> Scenario:
    return Scenario(
        scenario_id="pure-result-pointer-risk",
        purpose="DEVELOPMENT",
        dataset_id="tool-risk-not-frozen",
        family_id="tool-risk-not-financial",
        initial_state=InitialState(),
        steps=steps,
    )


@pytest.mark.parametrize(
    "value",
    [
        ref("future"),
        ref(pointer="/error/code"),
        ref(pointer="/result"),
        ref(pointer="/result/a~2b"),
        {"$ref": {"step_id": "prior", "pointer": "/result/x", "grant": True}},
        ref() | {"authorized": True},
        {"$ref": "prior"},
    ],
)
def test_invalid_or_forward_reference_is_rejected_before_any_runner(value: Any) -> None:
    with pytest.raises(ValidationError):
        scenario([step("prior"), step("later", inputs={"action_id": value})])


def test_same_step_and_cycles_are_rejected() -> None:
    with pytest.raises(ValidationError):
        scenario([step("prior", inputs={"action_id": ref()})])
    with pytest.raises(ValidationError):
        scenario(
            [
                step("prior", inputs={"action_id": ref("later")}),
                step("later", inputs={"action_id": ref()}),
            ]
        )


def test_real_pointer_copy_escaping_and_digests_bind_original_row() -> None:
    original: dict[str, Any] = {
        "step_id": "prior",
        "result": {"a/b": [{"~key": {"literal": ref()}}]},
    }
    pointer = "/result/a~1b/0/~0key"
    resolved, observations = references.resolve_inputs(
        {"copy": ref(pointer=pointer)}, {"prior": original}
    )
    assert resolved == {"copy": {"literal": ref()}}
    assert observations == [
        {
            "method": references.METHOD,
            "step_id": "prior",
            "pointer": pointer,
            "original_row_sha256": hashlib.sha256(references.canonical(original)).hexdigest(),
            "value_sha256": hashlib.sha256(references.canonical(resolved["copy"])).hexdigest(),
        }
    ]
    resolved["copy"]["literal"]["$ref"]["step_id"] = "changed"
    assert original["result"]["a/b"][0]["~key"]["literal"] == ref()


@pytest.mark.parametrize(
    "pointer", ["/result/items/01", "/result/items/-", "/result/items/1", "/result/missing"]
)
def test_missing_or_noncanonical_actual_pointer_is_rejected(pointer: str) -> None:
    with pytest.raises(ValueError):
        references.resolve_inputs(
            {"value": ref(pointer=pointer)}, {"prior": {"result": {"items": [1]}}}
        )


def test_expanded_reference_subtrees_obey_node_depth_and_cumulative_byte_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(references, "MAX_NODES", 5)
    with pytest.raises(ValueError, match="traversal budget"):
        references.resolve_inputs(
            {"copy": ref(pointer="/result/data")}, {"prior": {"result": {"data": list(range(8))}}}
        )
    monkeypatch.setattr(references, "MAX_NODES", 10_000)
    monkeypatch.setattr(references, "MAX_DEPTH", 2)
    with pytest.raises(ValueError, match="traversal budget"):
        references.resolve_inputs(
            {"copy": ref(pointer="/result/data")}, {"prior": {"result": {"data": [[[1]]]}}}
        )
    monkeypatch.setattr(references, "MAX_DEPTH", 24)
    monkeypatch.setattr(references, "MAX_BYTES", 300)
    with pytest.raises(ValueError, match="cumulative byte budget"):
        references.resolve_inputs(
            {str(i): ref(pointer="/result/data") for i in range(3)},
            {"prior": {"result": {"data": "x" * 150}}},
        )


@pytest.mark.parametrize(
    "expected",
    [
        {"code": "BAD", "status_code": True},
        {"code": "bad", "status_code": 409},
        {"code": "BAD", "status_code": 200},
    ],
)
def test_expected_refusal_contract_is_strict(expected: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        step("prior", expected_error=expected)


@pytest.mark.parametrize("error", [None, "OTHER", "EXPECTED"])
def test_only_exact_original_registered_error_continues_and_real_success_is_retained(
    monkeypatch: pytest.MonkeyPatch,
    error: str | None,
) -> None:
    dispatched: list[str] = []

    def dispatch(self: ScenarioRunner, body: ScenarioStep) -> dict[str, Any]:
        dispatched.append(body.step_id)
        if body.step_id == "prior" and error is not None:
            raise PolicyLifecycleError(error, "original refusal", 409)
        return {"actual_result": body.step_id}

    monkeypatch.setattr(ScenarioRunner, "_dispatch", dispatch)
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32)
    try:
        actual = ScenarioRunner(engine, uuid4())._execute(
            scenario(
                [
                    step("prior", expected_error={"code": "EXPECTED", "status_code": 409}),
                    step("later"),
                ]
            )
        )
    finally:
        engine.dispose()
    assert actual.steps[0]["expected_error_matched"] is (error == "EXPECTED")
    assert dispatched == (["prior", "later"] if error == "EXPECTED" else ["prior"])
    assert actual.status == ("EXECUTED" if error == "EXPECTED" else "FAILED")
    if error is None:
        assert actual.steps[0]["result"] == {"actual_result": "prior"}
        assert "error" not in actual.steps[0]
        assert actual.steps[0]["expectation_failure"] == "EXPECTED_ERROR_NOT_OBSERVED"
    else:
        assert actual.steps[0]["error"] == {
            "code": error,
            "message": "original refusal",
            "status_code": 409,
        }


def test_runtime_pointer_uses_actual_dispatch_result_and_missing_field_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[dict[str, Any]] = []

    def dispatch(self: ScenarioRunner, body: ScenarioStep) -> dict[str, Any]:
        dispatched.append(body.inputs)
        return {"action_id": "actual-original-id"}

    monkeypatch.setattr(ScenarioRunner, "_dispatch", dispatch)
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32)
    try:
        runner = ScenarioRunner(engine, uuid4())
        actual = runner._execute(
            scenario([step("prior"), step("later", inputs={"action_id": ref()})])
        )
        assert actual.status == "EXECUTED"
        assert dispatched == [{}, {"action_id": "actual-original-id"}]
        assert actual.steps[1]["resolved_inputs"] == dispatched[1]
        missing = runner._execute(
            scenario(
                [
                    step("prior"),
                    step("later", inputs={"action_id": ref(pointer="/result/absent")}),
                    step("never"),
                ]
            )
        )
        assert missing.status == "FAILED" and len(missing.steps) == 2
        assert missing.steps[1]["error"]["code"] == "INVALID_SCENARIO_INPUT"
    finally:
        engine.dispose()
