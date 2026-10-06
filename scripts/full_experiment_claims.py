"""Read-only admission of an existing MVP number through its actual fixed calculator.

No runner, output writer, arbitrary calculator import, or FULL metric upgrade exists here.
TOOL_TEST_ONLY arithmetic can be checked, but cannot enter financial effect claims.
"""

import hashlib
import importlib
import inspect
import math
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any, Literal
from uuid import UUID

from app.domain.full_experiment_cases import original_json
from app.domain.full_experiment_mechanisms import Digest, Identifier, Model, value_digest
from pydantic import StrictFloat, StrictInt

ROOT = Path(__file__).resolve().parents[1]
CALCULATORS = {
    **dict.fromkeys(
        ("S1", "S2", "S3", "S4", "S5", "E1", "E3", "E4"), "scripts.mvp_financial_metrics"
    ),
    **dict.fromkeys(("E2", "E5"), "scripts.mvp_observations"),
    **dict.fromkeys(("A1", "A2", "A3", "A4"), "scripts.mvp_trace_metrics"),
}


class OriginalFile(Model):
    path: str
    sha256: Digest


class ExistingMvpClaim(Model):
    protocol: Literal["existing-mvp-metric-claim-v1"] = "existing-mvp-metric-claim-v1"
    claim_id: Identifier
    experiment_run_id: UUID
    case_id: Identifier
    arm_id: Literal["B0", "B1", "B2", "B3", "P"]
    metric_id: Literal[
        "S1", "S2", "S3", "S4", "S5", "E1", "E2", "E3", "E4", "E5", "A1", "A2", "A3", "A4"
    ]
    metric_field: Literal["value", "numerator", "denominator"] = "value"
    value: StrictInt | StrictFloat
    unit: str
    scope: Literal["MVP_ONLY"] = "MVP_ONLY"
    run_manifest: OriginalFile
    recorded_result: OriginalFile
    calculator_source: OriginalFile
    metric_definition: OriginalFile


class ClaimAdmission(Model):
    status: Literal["RECOMPUTED_MVP_NUMBER", "RECOMPUTED_TOOL_NUMBER", "MISSING"]
    value: StrictInt | StrictFloat | None
    permitted_in_mvp_scope: bool
    permitted_as_full_experiment_effect: Literal[False] = False
    financial_acceptance: Literal[False] = False
    missing_reason: str | None
    original_refs: list[OriginalFile]


def _read(ref: OriginalFile) -> bytes:
    path = Path(ref.path).resolve()
    if not path.is_file() or not path.is_relative_to(ROOT):
        raise ValueError("Missing/out-of-repository original")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Original claim/run/result exceeds explicit 16MiB metadata bound")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref.sha256:
        raise ValueError("Original claim artifact byte drift")
    return raw


def _calculator(claim: ExistingMvpClaim) -> Any:
    name = CALCULATORS[claim.metric_id]
    expected = ROOT / (name.replace(".", "/") + ".py")
    raw = _read(claim.calculator_source)
    if Path(claim.calculator_source.path).resolve() != expected:
        raise ValueError("A claim cannot choose an arbitrary calculator source")
    module = importlib.import_module(name)
    function = module.observe
    code = compile(raw, str(expected), "exec", dont_inherit=True)
    definitions = {row.co_name: row for row in code.co_consts if isinstance(row, CodeType)}
    if (
        not isinstance(function, FunctionType)
        or function.__code__ != definitions.get("observe")
        or Path(inspect.getfile(function)).resolve() != expected
    ):
        raise ValueError("Loaded fixed calculator differs from retained current source")
    return function


def admit_existing_mvp_claim(claim: ExistingMvpClaim) -> ClaimAdmission:
    """Recompute a whole original result; no flag can upgrade it to FULL experiment proof."""
    refs = [
        claim.run_manifest,
        claim.recorded_result,
        claim.calculator_source,
        claim.metric_definition,
    ]
    try:
        before = [_read(ref) for ref in refs]
        run = original_json(before[0])
        recorded = original_json(before[1])
        expected_definition = ROOT / "docs/spec/mvp-metrics.json"
        if Path(claim.metric_definition.path).resolve() != expected_definition:
            raise ValueError("Original current MVP metric definition required")
        definition = original_json(before[3])
        metrics = definition.get("metrics")
        if not isinstance(metrics, list):
            raise ValueError("Complete original MVP metric definitions missing")
        matched = [
            row for row in metrics if isinstance(row, dict) and row.get("id") == claim.metric_id
        ]
        if len(matched) != 1 or matched[0].get("unit") != claim.unit:
            raise ValueError("Original metric identity/unit differs")
        actual = _calculator(claim)(Path(claim.run_manifest.path).resolve())
        if value_digest(actual) != value_digest(recorded):
            raise ValueError("Recorded full result differs from actual independent recalculation")
        bindings = actual.get("bindings")
        if not isinstance(bindings, dict):
            raise ValueError("Original calculation bindings missing")
        expected_bindings = {
            "experiment_run_id": str(claim.experiment_run_id),
            "case_id": claim.case_id,
            "arm_id": claim.arm_id,
        }
        if any(
            bindings.get(key) != value or run.get(key) != value
            for key, value in expected_bindings.items()
        ):
            raise ValueError("Original run/case/arm identity differs")
        metric = actual["metrics"][claim.metric_id]
        if metric.get("status") != "MEASURED":
            raise ValueError("Missing/NOT_RUN/zero-denominator metric is not a measured number")
        value = metric.get(claim.metric_field)
        if type(value) not in {int, float} or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError("Requested metric is not a finite scalar numeric observation")
        if value != claim.value:
            raise ValueError("Claim value differs from original recomputed number")
        tool = bindings.get("purpose") == "TOOL_TEST_ONLY"
        actual_mvp = (
            bindings.get("purpose") == "MVP_FROZEN"
            and bindings.get("execution_mode") == "SERVICE_INTEGRATION"
        )
        if not tool and not actual_mvp:
            raise ValueError("Only original MVP or explicitly TOOL_TEST_ONLY scope is supported")
        if [_read(ref) for ref in refs] != before:
            raise ValueError("Original claim files changed during this read-only call")
        return ClaimAdmission(
            status="RECOMPUTED_TOOL_NUMBER" if tool else "RECOMPUTED_MVP_NUMBER",
            value=value,
            permitted_in_mvp_scope=actual_mvp,
            missing_reason=None,
            original_refs=refs,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        return ClaimAdmission(
            status="MISSING",
            value=None,
            permitted_in_mvp_scope=False,
            missing_reason=str(error),
            original_refs=refs,
        )
