"""Private V2 frozen-byte bridge; no SQL, service, or authority result.

The provider must retain its actual same-lock SQL/user/epoch guard separately.
An instance is private to exactly one provider invocation. Nothing is cached globally.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import CodeType, FunctionType, ModuleType
from typing import Any, cast
from uuid import UUID

PROTOCOL = "mvp-arm-provider-registry-v2"
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


class BridgeRefused(ValueError):
    pass


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise BridgeRefused(reason)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict(raw: bytes) -> Any:
    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            check(key not in result, "DUPLICATE_ORIGINAL_JSON_KEY")
            result[key] = value
        return result

    def finite(value: str) -> Any:
        raise BridgeRefused("NONFINITE_ORIGINAL_JSON:" + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)


def time(value: Any) -> datetime:
    check(isinstance(value, str), "MISSING_ORIGINAL_CLOCK")
    result = datetime.fromisoformat(value)
    check(result.tzinfo is not None and result.utcoffset() is not None, "NAIVE_ORIGINAL_CLOCK")
    return result.astimezone(UTC)


def original(root: Path, ref: dict[str, Any]) -> bytes:
    check(set(ref) == {"path", "sha256"}, "EXACT_ORIGINAL_FILE_DESCRIPTOR_REQUIRED")
    path = Path(ref["path"])
    check(
        path.is_absolute() and path.resolve().is_relative_to(root.resolve()) and path.is_file(),
        "MISSING_OR_EXTERNAL_ORIGINAL",
    )
    check(
        isinstance(ref["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is not None,
        "INVALID_ORIGINAL_SHA256",
    )
    data = path.read_bytes()
    check(digest(data) == ref["sha256"], "ORIGINAL_BYTE_DRIFT")
    return data


def bind_functions(module: ModuleType, path: Path, names: tuple[str, ...]) -> None:
    """Availability integrity of pure graph/reference functions, never financial authority."""
    compiled = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
    codes = {code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)}
    for name in names:
        function = getattr(module, name, None)
        check(
            isinstance(function, FunctionType)
            and function.__code__ == codes.get(name)
            and Path(inspect.getfile(function)).resolve() == path.resolve(),
            "LOADED_GRAPH_OR_REFERENCE_FUNCTION_DRIFT",
        )


def read_previous(
    root: Path,
    registration: dict[str, Any],
    bindings: dict[str, str],
    steps: list[dict[str, Any]],
    current_index: int,
) -> tuple[dict[str, dict[str, Any]], dict[Path, bytes]]:
    entries = registration["previous_step_originals"]
    check(
        isinstance(entries, list)
        and digest(canonical(entries)) == registration["previous_step_inventory_sha256"],
        "PREVIOUS_ORIGINAL_INVENTORY_DRIFT",
    )
    previous: dict[str, dict[str, Any]] = {}
    bytes_by_path: dict[Path, bytes] = {}
    indexes = {step["step_id"]: index for index, step in enumerate(steps)}
    last = -1
    for entry in entries:
        check(set(entry) == {"step_id", "ref"}, "EXACT_PREVIOUS_STEP_DESCRIPTOR_REQUIRED")
        step_id = entry["step_id"]
        check(
            step_id in indexes
            and last < indexes[step_id] < current_index
            and step_id not in previous,
            "FUTURE_CURRENT_DUPLICATE_OR_REORDERED_STEP",
        )
        last = indexes[step_id]
        raw = original(root, entry["ref"])
        path = Path(entry["ref"]["path"]).resolve()
        check(path not in bytes_by_path, "ALIASED_PREVIOUS_ORIGINAL")
        body = strict(raw)
        check(
            body.get("protocol") == "mvp-raw-observation-v1"
            and body.get("kind") == "SCENARIO_SERVICE_STEP_RESULT"
            and body.get("bindings") == bindings,
            "STEP_RAW_PROTOCOL_OR_RUN_OWNER_ARM_EPOCH_MISMATCH",
        )
        payload = body.get("payload", {})
        step = steps[last]
        check(
            payload.get("step_id") == step_id
            and payload.get("kind") == step["kind"]
            and time(payload.get("at")) == time(step["at"]),
            "STEP_RAW_FROZEN_INPUT_OR_CLOCK_MISMATCH",
        )
        check(
            payload.get("capture_origin") == "PRODUCTION_SERVICE_CALL"
            and "result" in payload
            and isinstance(payload["result"], dict),
            "NO_ACTUAL_PRIOR_SERVICE_RESULT",
        )
        check(
            "error" not in payload and "expected_error" not in payload,
            "FAILED_STEP_CANNOT_SUPPLY_ACTUAL_RESULT",
        )
        previous[step_id] = deepcopy(payload)
        bytes_by_path[path] = raw
    return previous, bytes_by_path


class FrozenInvocationBridge:
    """Single-invocation parsed graph; checkpoint rereads original/current bytes only."""

    def __init__(
        self,
        *,
        root: Path,
        root_registration_ref: dict[str, Any],
        bindings: dict[str, str],
        database_name: str,
        now: str,
    ) -> None:
        self.root = root.resolve()
        self.registration_ref = deepcopy(root_registration_ref)
        self.bindings = deepcopy(bindings)
        self.context_digest = digest(
            canonical([bindings, database_name, now, root_registration_ref])
        )
        self.database_name, self.now = database_name, now
        check(
            set(bindings) == BINDINGS
            and bindings["purpose"] in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}
            and bindings["execution_mode"] == "SERVICE_INTEGRATION"
            and bindings["arm_id"] in {"B0", "B1", "B2", "B3", "P"}
            and bindings["seed_version"] == "mvp-301-v6",
            "EXACT_FROZEN_CONTEXT_REQUIRED",
        )
        for name in ("experiment_run_id", "user_id", "isolated_db_epoch"):
            check(str(UUID(bindings[name])) == bindings[name], "NONCANONICAL_RUNTIME_IDENTITY")
        check(
            re.fullmatch(r"bf_test_[0-9a-f]{32}", database_name) is not None,
            "FORMAL_OR_SHARED_DATABASE_EXCLUDED",
        )
        self.registration_bytes = original(self.root, self.registration_ref)
        reg = strict(self.registration_bytes)
        check(
            reg.get("protocol") == PROTOCOL
            and reg.get("bindings") == bindings
            and reg.get("database_name") == database_name,
            "EXTERNAL_ROOT_REGISTRATION_BINDING_MISMATCH",
        )
        self.registration = reg
        from app.services import scenario_references

        from scripts import mvp_corpus_v2 as corpus

        self.corpus_module = corpus
        self.reference_module = scenario_references
        self.corpus_source = self.root / "scripts/mvp_corpus_v2.py"
        self.reference_source = self.root / "apps/api/app/services/scenario_references.py"
        bind_functions(corpus, self.corpus_source, ("prepare_frozen_case", "revalidate_archive"))
        bind_functions(scenario_references, self.reference_source, ("resolve_inputs",))
        directory = Path(reg["corpus_directory"])
        check(
            not directory.is_absolute() and ".." not in directory.parts, "EXTERNAL_CORPUS_DIRECTORY"
        )
        self.archive = (self.root / directory).resolve()
        check(self.archive.is_relative_to(self.root), "EXTERNAL_CORPUS_DIRECTORY")
        self.prepared = corpus.prepare_frozen_case(
            self.archive,
            self.root,
            reg["manifest_sha256"],
            bindings["case_id"],
            bindings["purpose"],
        )
        check(
            self.prepared.get("status") == "RUNTIME_INPUT_REVALIDATED_NOT_EXECUTED",
            "FRESH_COMPLETE_CASE_REVALIDATION_REFUSED:" + str(self.prepared.get("reason")),
        )
        draft = corpus.revalidate_archive(self.archive, self.root, reg["manifest_sha256"])
        check(isinstance(draft, corpus.FrozenDraft), "ORIGINAL_FROZEN_ARCHIVE_MAPPING_MISSING")
        self.draft = cast(corpus.FrozenDraft, draft)
        check(
            self.prepared["original_case_input_sha256"] == bindings["input_sha256"]
            and self.prepared["validated_execution_input_sha256"] == reg["typed_execution_sha256"]
            and digest(canonical(self.prepared["source_inventory"]))
            == reg["source_inventory_sha256"],
            "ORIGINAL_INPUT_TYPED_SOURCE_BINDING_MISMATCH",
        )
        entries = [
            entry
            for entry in self.draft.manifest["cases"]
            if entry["case_id"] == bindings["case_id"]
        ]
        check(len(entries) == 1, "NO_UNIQUE_FROZEN_CASE_ENTRY")
        entry = entries[0]
        refs = {
            "input": entry["input_ref"],
            "oracle": entry["oracle_ref"],
            "rule": entry["rule_refs"][bindings["arm_id"]],
            "source": self.draft.manifest["source_ref"],
            "design": self.draft.manifest["design_ref"],
        }
        self.artifacts: dict[str, Any] = {}
        for name, ref in refs.items():
            check(
                ref["sha256"] == bindings[name + "_sha256"],
                "REAL_ARTIFACT_BINDING_DIFFERENT:" + name,
            )
            self.artifacts[name] = self.draft.load_ref(ref)
        check(
            self.artifacts["input"]["intended_purpose"] == bindings["purpose"],
            "ORIGINAL_INPUT_PURPOSE_DIFFERS",
        )
        steps = self.prepared["validated_execution_input"]["steps"]
        current = [
            index for index, step in enumerate(steps) if step["step_id"] == reg["scenario_step_id"]
        ]
        check(len(current) == 1, "NO_UNIQUE_FROZEN_CURRENT_STEP")
        self.step = steps[current[0]]
        check(time(self.step["at"]) == time(now), "CLOCK_NOT_CURRENT_FROZEN_STEP")
        self.previous, self.previous_bytes = read_previous(
            self.root, reg, bindings, steps, current[0]
        )
        self.source_paths = dict(self.draft.source_paths)
        required = {
            "scripts/mvp_corpus_v2.py",
            "scripts/mvp_native_schema.py",
            "scripts/mvp_corpus.py",
            "scripts/mvp_arm_executor.py",
            "scripts/mvp_observations.py",
            "scripts/mvp_trace_metrics.py",
            "apps/api/app/services/scenario_references.py",
            "apps/api/app/services/experiment_arms.py",
        }
        check(required <= self.source_paths.keys(), "COMPLETE_BRIDGE_RUNTIME_SOURCE_UNREGISTERED")
        this_path = Path(__file__).resolve()
        check(
            this_path.is_relative_to(self.root)
            and self.source_paths.get(this_path.relative_to(self.root).as_posix())
            == digest(this_path.read_bytes()),
            "THIS_EXACT_BRIDGE_SOURCE_UNREGISTERED",
        )
        self.parsed_digest = self._parsed_digest()
        self.draft.unchanged()
        self.checkpoint()

    def _parsed_digest(self) -> str:
        return digest(
            canonical(
                [
                    self.registration,
                    self.prepared,
                    self.artifacts,
                    self.previous,
                    self.step,
                    self.source_paths,
                    self.draft.manifest,
                ]
            )
        )

    def checkpoint(self) -> dict[str, Any]:
        """Called again at each original SQL guard; this does not replace that guard."""
        check(
            self.context_digest
            == digest(
                canonical([self.bindings, self.database_name, self.now, self.registration_ref])
            ),
            "INVOCATION_CONTEXT_MUTATED",
        )
        check(self.parsed_digest == self._parsed_digest(), "READONLY_PARSED_REGISTRATION_MUTATED")
        check(
            original(self.root, self.registration_ref) == self.registration_bytes,
            "ROOT_REGISTRY_CHANGED_WITHIN_INVOCATION",
        )
        self.draft.unchanged()
        for name, expected in self.source_paths.items():
            check(
                digest((self.root / name).read_bytes()) == expected,
                "CURRENT_SOURCE_CHANGED_WITHIN_INVOCATION",
            )
        for path, raw in self.previous_bytes.items():
            check(path.read_bytes() == raw, "PRIOR_STEP_ORIGINAL_CHANGED_WITHIN_INVOCATION")
        bind_functions(
            self.corpus_module, self.corpus_source, ("prepare_frozen_case", "revalidate_archive")
        )
        bind_functions(self.reference_module, self.reference_source, ("resolve_inputs",))
        return {
            "status": "FROZEN_BYTES_RECHECKED_SQL_OWNER_EPOCH_GUARD_STILL_REQUIRED",
            "financial_permission_verified": False,
            "financial_effect_evidence": False,
        }

    def opportunity(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        self.checkpoint()
        kinds = {
            "PREPARE": "PREPARE_ACTION",
            "CONFIRM": "CONFIRM_ACTION",
            "EXECUTE": "EXECUTE_ACTION",
        }
        check(
            operation in kinds and self.step["kind"] == kinds[operation],
            "NOT_IMPLEMENTED:OPERATION_STEP_KIND_DIFFERS",
        )
        rule = self.artifacts["rule"]
        schedule = rule.get("opportunities")
        check(isinstance(schedule, list), "NOT_IMPLEMENTED:FROZEN_RULE_SCHEDULE_MISSING")
        matches = [
            row
            for row in schedule
            if row.get("opportunity_id") == self.registration["opportunity_id"]
            and row.get("step_id") == self.step["step_id"]
            and row.get("operation") == operation
        ]
        check(
            len(matches) == 1 and self.registration["operation"] == operation,
            "NO_UNIQUE_REGISTERED_ARM_OPPORTUNITY",
        )
        expected, refs = self.reference_module.resolve_inputs(matches[0]["inputs"], self.previous)
        check(expected == request, "SMALL_REQUEST_DIFFERS_FROM_ACTUAL_FROZEN_OPPORTUNITY")
        step_inputs, step_refs = self.reference_module.resolve_inputs(
            self.step["inputs"], self.previous
        )
        check(step_inputs == request, "SMALL_REQUEST_DIFFERS_FROM_ACTUAL_FROZEN_STEP")
        self.checkpoint()
        return {
            "opportunity_id": matches[0]["opportunity_id"],
            "original_rule_value_sha256": digest(canonical(rule)),
            "resolved_request": expected,
            "resolution_refs": refs,
            "step_resolution_refs": step_refs,
            "financial_permission_verified": False,
            "financial_effect_evidence": False,
        }

    def source_descriptor(self, ref: dict[str, Any]) -> dict[str, str]:
        """Exact frozen current source→retained mapping; global SOURCE bytes are unchanged."""
        self.checkpoint()
        name = ref.get("path")
        check(
            isinstance(name, str) and self.source_paths.get(name) == ref.get("sha256"),
            "SOURCE_REF_ABSENT_FROM_ORIGINAL_SOURCE_FILES",
        )
        current = (self.root / str(name)).resolve()
        check(current in self.draft.backing, "SOURCE_RETAINED_ORIGINAL_MISSING")
        retained = self.draft.backing[current]
        check(current.read_bytes() == retained.read_bytes(), "CURRENT_RETAINED_SOURCE_DIFFERENT")
        return {"original_path": name, "archived_path": str(retained), "sha256": ref["sha256"]}

    def rule_inputs(self, opportunity_id: str) -> dict[str, Any]:
        """Resolve only this opportunity's arithmetic fields; the complete RULE is unchanged."""
        self.checkpoint()
        result = resolve_rule_inputs(
            self.bindings,
            self.registration_ref,
            self.artifacts["rule"],
            opportunity_id,
            self.previous,
            self.reference_module.resolve_inputs,
        )
        self.checkpoint()
        return result


def resolve_rule_inputs(
    bindings: dict[str, str],
    registration_ref: dict[str, Any],
    rule: dict[str, Any],
    opportunity_id: str,
    previous: dict[str, dict[str, Any]],
    resolver: Callable[
        [dict[str, Any], dict[str, dict[str, Any]]], tuple[dict[str, Any], list[dict[str, str]]]
    ],
) -> dict[str, Any]:
    """Pure derivation; the caller validates actual originals and the loaded resolver first."""
    algorithm = rule.get("arm_algorithm", rule)
    arm = bindings["arm_id"]
    keys = ["protocol", "arm_id", "case_id", "purpose", "seed_version", "cash_account_ids"]
    if arm == "B0":
        choices = [
            choice
            for choice in algorithm.get("manual_actions", [])
            if choice.get("opportunity_id") == opportunity_id
        ]
        check(len(choices) == 1, "ONE_REGISTERED_MANUAL_OPPORTUNITY_REQUIRED")
        check(
            type(choices[0].get("amount_cents")) is int and choices[0]["amount_cents"] >= 0,
            "MANUAL_AMOUNT_MUST_BE_FROZEN_LITERAL_NOT_PRIOR_P_OUTPUT",
        )
        raw_inputs = {key: deepcopy(algorithm[key]) for key in keys}
        raw_inputs["manual_actions"] = deepcopy(choices)
    elif arm == "B1":
        check(
            type(algorithm.get("threshold_cents")) is int and algorithm["threshold_cents"] >= 0,
            "SHARED_THRESHOLD_MUST_BE_FROZEN_LITERAL",
        )
        raw_inputs = {key: deepcopy(algorithm[key]) for key in [*keys, "threshold_cents"]}
    elif arm == "B2":
        check(
            isinstance(algorithm.get("timezone"), str)
            and isinstance(algorithm.get("horizon_end_at"), str),
            "SHARED_STATIC_CALENDAR_MUST_BE_FROZEN_LITERAL",
        )
        raw_inputs = {
            key: deepcopy(algorithm[key])
            for key in [*keys, "fixed_policy_version_ids", "timezone", "horizon_end_at"]
        }
    else:
        raise BridgeRefused("NOT_IMPLEMENTED:P_AND_B3_KEEP_ORIGINAL_PLANNER")
    resolved, refs = resolver(raw_inputs, previous)
    return {
        "protocol": "mvp-arm-rule-resolution-v2",
        "bindings": deepcopy(bindings),
        "root_registration_ref": deepcopy(registration_ref),
        "opportunity_id": opportunity_id,
        "original_rule_value_sha256": digest(canonical(rule)),
        "resolved_arm_algorithm": resolved,
        "resolved_arm_algorithm_value_sha256": digest(canonical(resolved)),
        "resolution_refs": refs,
        "financial_permission_verified": False,
        "financial_effect_evidence": False,
    }
