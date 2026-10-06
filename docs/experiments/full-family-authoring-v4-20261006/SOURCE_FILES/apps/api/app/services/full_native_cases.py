"""Execute authored DEVELOPMENT inputs through original MVP services and fixed FULL APIs.

No seed/reset, arm selector, economic oracle, or frozen acceptance is installed here.
Every not-dispatched original opportunity remains in the returned denominator.
"""

from datetime import datetime
from hashlib import sha256
from time import perf_counter_ns
from typing import Annotated, Any, Literal, Self, get_args
from uuid import UUID

from app.db.audit_guard import audit_command_guard
from app.domain.full_experiment_cases import original_json
from app.domain.full_native_operations import OPERATIONS
from app.services.full_native_steps import FullNativeSteps, require_native_target
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_references import MAX_BYTES, resolve_inputs
from app.services.scenario_runner import ScenarioRunner, _fault
from app.services.scenario_types import (
    ExpectedScenarioError,
    InitialState,
    ScenarioModel,
    ScenarioStep,
)
from pydantic import Field, StrictStr, ValidationError, field_validator, model_validator
from sqlalchemy.engine import Engine

LEGACY_KINDS = frozenset(get_args(ScenarioStep.model_fields["kind"].annotation))
UNIMPLEMENTED_AUTHOR_KINDS = frozenset({"VERIFY_TAMPERED_ORIGINAL_COPY"})


class FullDevelopmentStep(ScenarioModel):
    step_id: Annotated[StrictStr, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    kind: StrictStr
    at: datetime
    inputs: dict[str, Any]
    fault: Literal["NONE", "DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"] = "NONE"
    expected_error: ExpectedScenarioError | None = None

    @field_validator("at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Original aware simulation clock required")
        return value

    @model_validator(mode="after")
    def actual_kind(self) -> Self:
        if self.kind not in LEGACY_KINDS | OPERATIONS.keys() | UNIMPLEMENTED_AUTHOR_KINDS:
            raise ValueError("Unknown native operation; no arbitrary service dispatch")
        if self.kind in LEGACY_KINDS:
            ScenarioStep.model_validate_json(self.model_dump_json())
        # FULL faults and the explicit copy operation are retained as author gaps,
        # and must produce MISSING before dispatch, never masquerade as injections.
        return self


class FullDevelopmentCase(ScenarioModel):
    protocol: Literal["full-family-case-input-v1"]
    profile: Literal["FULL"]
    purpose: Literal["DEVELOPMENT"]
    family_id: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    scenario_id: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    initial_state: InitialState
    steps: Annotated[list[FullDevelopmentStep], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def existing_original_order(self) -> Self:
        if self.initial_state.mode != "EXISTING":
            raise ValueError("FULL development execution cannot seed/reset any history")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("Original step identities must be unique")
        if any(b.at < a.at for a, b in zip(self.steps, self.steps[1:], strict=False)):
            raise ValueError("Original clock cannot move backwards")
        previous: dict[str, dict[str, Any]] = {}
        for step in self.steps:
            resolve_inputs(step.inputs, previous, validate_only=True)
            previous[step.step_id] = {}
        return self


def validate_full_development_case(raw: bytes) -> FullDevelopmentCase:
    if len(raw) > MAX_BYTES:
        raise ValueError("Original FULL development INPUT exceeds 1MiB")
    original_json(raw)  # Duplicate keys/nonfinite numbers are never silently normalised.
    return FullDevelopmentCase.model_validate_json(raw)


def _http_result(original: dict[str, Any], row: dict[str, Any]) -> None:
    """Keep the whole native capture; references see only actual successful body fields."""
    status = original.get("status_code")
    body = original.get("result")
    if type(status) is not int or not 100 <= status <= 599 or not isinstance(body, dict):
        raise ValueError("Actual native HTTP capture is incomplete")
    text = original.get("original_response_text")
    digest = original.get("response_body_sha256")
    if (
        not isinstance(text, str)
        or sha256(text.encode("utf-8")).hexdigest() != digest
        or original_json(text.encode("utf-8")) != body
    ):
        raise ValueError("Original HTTP bytes/body/hash mismatch")
    row["native_response"] = original
    row["status_code"] = status
    if status >= 400:
        detail = body.get("error")
        row["error"] = {
            "code": detail.get("code", "HTTP_ERROR") if isinstance(detail, dict) else "HTTP_ERROR",
            "message": detail.get("message", "Actual API refusal")
            if isinstance(detail, dict)
            else "Actual API refusal",
            "status_code": status,
        }
    else:
        row["result"] = body


def _expected(row: dict[str, Any], step: FullDevelopmentStep) -> bool:
    problem = row.get("error")
    if step.expected_error is None:
        return problem is None and "missing" not in row
    matched = (
        isinstance(problem, dict)
        and problem.get("code") == step.expected_error.code
        and problem.get("status_code") == step.expected_error.status_code
    )
    row["expected_error"] = step.expected_error.model_dump(mode="json")
    row["expected_error_matched"] = matched
    if problem is None:
        row["expectation_failure"] = "EXPECTED_ERROR_NOT_OBSERVED"
    return matched


class FullNativeCaseRunner:
    """Invocation-local cookies/results only; current owner/epoch checked for every step."""

    def __init__(self, engine: Engine, user_id: UUID):
        self.engine, self.user_id = engine, user_id

    def run(self, raw: bytes, expected_epoch_id: UUID) -> dict[str, Any]:
        case = validate_full_development_case(raw)
        require_native_target(self.engine, self.user_id, case.steps[0].at)
        if not isinstance(expected_epoch_id, UUID) or (
            case.initial_state.expected_epoch_id is not None
            and case.initial_state.expected_epoch_id != expected_epoch_id
        ):
            raise ValueError("Actual owned OPEN epoch does not match original case")
        legacy = ScenarioRunner(self.engine, self.user_id)
        rows: list[dict[str, Any]] = []
        previous: dict[str, dict[str, Any]] = {}
        stopped = False
        with FullNativeSteps(
            self.engine, self.user_id, expected_epoch_id, case.steps[0].at
        ) as native:
            for index, step in enumerate(case.steps):
                row: dict[str, Any] = {
                    "step_id": step.step_id,
                    "step_index": index,
                    "kind": step.kind,
                    "at": step.at.isoformat(),
                    "opportunity_denominator_retained": True,
                    "financial_effect_verified": False,
                }
                if stopped:
                    row["skip"] = "NOT_DISPATCHED_AFTER_ORIGINAL_FAILURE_OR_MISSING"
                    rows.append(row)
                    continue
                start = perf_counter_ns()
                try:
                    resolved, refs = resolve_inputs(step.inputs, previous)
                    row["resolved_inputs"] = resolved
                    row["input_references"] = refs
                    if step.kind in UNIMPLEMENTED_AUTHOR_KINDS or (
                        step.kind in OPERATIONS and step.fault != "NONE"
                    ):
                        row["missing"] = "FULL_NATIVE_FAULT_OR_COPY_CAPABILITY_NOT_IMPLEMENTED"
                    elif step.kind in OPERATIONS:
                        _http_result(native.dispatch(step.kind, resolved, step.at), row)
                    else:
                        # Fresh actual owner/OPEN epoch before MVP service dispatch too.
                        native._context(step.at)
                        typed = ScenarioStep.model_validate_json(
                            step.model_copy(update={"inputs": resolved}).model_dump_json()
                        )
                        with audit_command_guard(self.engine, self.user_id):
                            with _fault(step.fault, step.kind, self.engine, self.user_id, resolved):
                                row["result"] = legacy._dispatch(typed)
                except PolicyLifecycleError as error:
                    row["error"] = {
                        "code": error.code,
                        "message": error.message,
                        "status_code": error.status_code,
                    }
                except ValidationError as error:
                    row["error"] = {
                        "code": "INVALID_SCENARIO_STEP",
                        "message": str(error),
                        "status_code": 422,
                    }
                except ValueError as error:
                    row["error"] = {
                        "code": "INVALID_SCENARIO_INPUT",
                        "message": str(error),
                        "status_code": 409,
                    }
                except TimeoutError as error:
                    row["error"] = {
                        "code": "SIMULATED_BANK_RESPONSE_LOST"
                        if step.kind == "EXECUTE_ACTION" and step.fault == "DROP_BANK_RESPONSE"
                        else "ACTUAL_TIMEOUT",
                        "message": str(error),
                        "status_code": 409,
                    }
                finally:
                    end = perf_counter_ns()
                    row["actual_timing"] = {
                        "clock": "perf_counter_ns",
                        "start_ns": start,
                        "end_ns": end,
                        "duration_ns": end - start,
                    }
                stopped = not _expected(row, step)
                rows.append(row)
                previous[step.step_id] = row
        return {
            "protocol": "full-native-development-case-result-v1",
            "purpose": "DEVELOPMENT",
            "profile": "FULL",
            "family_id": case.family_id,
            "scenario_id": case.scenario_id,
            "original_input_sha256": sha256(raw).hexdigest(),
            "expected_epoch_id": str(expected_epoch_id),
            "steps": rows,
            "original_step_count": len(case.steps),
            "returned_step_count": len(rows),
            "status": "MISSING_NATIVE_CAPABILITY"
            if any("missing" in r for r in rows)
            else "FAILED"
            if stopped
            else "ACTUAL_SERVICE_OBSERVATIONS_NOT_ECONOMIC_ACCEPTANCE",
            "bank_authority_granted": False,
            "financial_acceptance": False,
            "frozen_acceptance": False,
            "seven_arm_experiment_performed": False,
            "metric_results": None,
        }
