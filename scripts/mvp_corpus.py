"""Validate and archive explicitly authored corpora; never run financial cases.

The freeze is an immutable byte/source/registered-schema checkpoint, not evidence
of a successful case, a sound oracle, or completed financial acceptance.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

DRAFT_PROTOCOL = "bounded-funds-corpus-draft-v1"
FROZEN_PROTOCOL = "bounded-funds-corpus-freeze-v1"
CASE_PROTOCOL = "bounded-funds-case-input-v1"
MVP_QUOTAS = {
    "NORMAL": 6,
    "GOAL_OBLIGATION_CONFLICT": 6,
    "CONSUMPTION_SHRINK": 4,
    "FIXED_LIQUIDITY_CONFLICT": 4,
    "POLICY_VERSION": 2,
    "TRANSFER_AMBIGUITY": 2,
}
PURPOSES = {"DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN", "TOOL_ONLY"}
ARMS = {"B0", "B1", "B2", "B3", "P"}
METRICS = {"S1", "S2", "S3", "S4", "S5", "E1", "E2", "E3", "E4", "E5", "A1", "A2", "A3", "A4"}
ORACLE_STDLIB = {
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "re",
    "statistics",
    "typing",
    "uuid",
    "zoneinfo",
    "__future__",
}
LABEL_KEYS = {
    "title",
    "name",
    "description",
    "label",
    "case_id",
    "family_id",
    "scenario_id",
    "dataset_id",
    "data_origin",
}


class CorpusError(ValueError):
    """Missing originals, drift, unsupported schema or invalid corpus structure."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def obj(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorpusError(f"{label}: object required")
    return value


def seq(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CorpusError(f"{label}: explicit list required")
    return value


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CorpusError(f"{label}: nonempty string required")
    return value


def sha(value: Any) -> str:
    registered = text(value, "sha256")
    if re.fullmatch(r"[0-9a-f]{64}", registered) is None:
        raise CorpusError("sha256: lowercase digest required")
    return registered


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise CorpusError(f"Nonfinite JSON: {value}")


def read_json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CorpusError("Original is not strict UTF-8 JSON") from error


def inside(root: Path, relative: Any) -> Path:
    relative_path = Path(text(relative, "original path"))
    path = (root / relative_path).resolve()
    if (
        relative_path.is_absolute()
        or not path.is_relative_to(root.resolve())
        or path == root.resolve()
    ):
        raise CorpusError("Original path escapes its declared root")
    return path


def validate_schema(value: Any, schema: dict[str, Any], where: str = "input") -> None:
    """Small explicit schema subset; unknown keywords are refused, never ignored."""
    known = {
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
    }
    if set(schema) - known:
        raise CorpusError(f"{where}: unsupported schema keyword")
    kind = schema.get("type")
    types = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if kind not in types or not types[kind]:
        raise CorpusError(f"{where}: registered type {kind!r} differs")
    if "const" in schema and value != schema["const"]:
        raise CorpusError(f"{where}: registered const differs")
    if "enum" in schema and value not in schema["enum"]:
        raise CorpusError(f"{where}: outside registered enum")
    if kind == "object":
        properties = obj(schema.get("properties", {}), "schema properties")
        required = [
            text(key, "required schema field")
            for key in seq(schema.get("required", []), "schema required keys")
        ]
        if not set(required) <= set(value):
            raise CorpusError(f"{where}: required input fields are missing")
        if schema.get("additionalProperties", False) is not False:
            raise CorpusError(f"{where}: permissive extra input schema is not supported")
        if set(value) - set(properties):
            raise CorpusError(f"{where}: unregistered input fields")
        for key, field in value.items():
            validate_schema(field, obj(properties[key], "property schema"), f"{where}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100000):
            raise CorpusError(f"{where}: array size outside registered bounds")
        item = obj(schema.get("items"), "registered items schema")
        for index, field in enumerate(value):
            validate_schema(field, item, f"{where}[{index}]")
    elif kind == "integer":
        if not schema.get("minimum", -(2**63 - 1)) <= value <= schema.get("maximum", 2**63 - 1):
            raise CorpusError(f"{where}: integer outside registered bounds")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 100000):
            raise CorpusError(f"{where}: string outside registered bounds")


def normalized_fingerprint(case: dict[str, Any], *, flow: bool) -> str:
    """Retain topology, enums and booleans; normalize labels, UUIDs and step aliases.

    Flow additionally normalizes integer/date values, so changed money, dates,
    identifiers or titles alone cannot turn a development flow into a new case.
    """
    steps = seq(case.get("steps"), "normalized steps")
    step_names = {
        text(obj(step, "step").get("step_id"), "step_id"): f"step-{index}"
        for index, step in enumerate(steps)
    }
    identifiers: dict[str, int] = {}

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                name: walk(field, name)
                for name, field in sorted(value.items())
                if name not in LABEL_KEYS
                and name
                not in {
                    "intended_purpose",
                    "initial_fact_refs",
                    "protocol",
                    "seed_version",
                    "rule_sha256_by_arm",
                }
                and not name.endswith("_sha256")
            }
        if isinstance(value, list):
            return [walk(field) for field in value]
        if isinstance(value, str):
            if key == "step_id":
                return step_names.get(value, value)
            if key == "$ref":
                owner, separator, pointer = value.partition("#")
                if not separator or owner not in step_names:
                    raise CorpusError("A reference has no registered original step")
                return step_names[owner] + "#" + pointer
            try:
                UUID(value)
                if value not in identifiers:
                    identifiers[value] = len(identifiers)
                return {"uuid_alias": identifiers[value]}
            except ValueError:
                pass
            if flow and (
                key in {"at", "valid_from", "valid_until", "occurred_at", "deadline", "as_of"}
                or key.endswith("_id")
                or key.endswith("_ref")
            ):
                return {"registered_value_type": "string"}
        if flow and type(value) is int:
            return {"registered_value_type": "integer"}
        return value

    return digest(canonical(walk(case)))


def quotas(families: list[str], expected: dict[str, Any]) -> None:
    if not expected or any(
        type(count) is not int or not 0 < count <= 10000 for count in expected.values()
    ):
        raise CorpusError("Family quota schema must explicitly contain positive integer counts")
    if dict(Counter(families)) != expected:
        raise CorpusError(
            f"Case family quotas differ: actual={dict(Counter(families))}, expected={expected}"
        )


def independent_python(data: bytes) -> set[str]:
    """Reject imported P evaluators/dynamic imports; this is not oracle correctness proof."""
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (UnicodeError, SyntaxError) as error:
        raise CorpusError("Independent oracle original is not valid Python source") from error
    functions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [(node.module or "").split(".")[0]]
        else:
            modules = []
        if any(module not in ORACLE_STDLIB for module in modules):
            raise CorpusError("Independent oracle imports a P/app or unregistered evaluator")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "__import__"}
        ):
            raise CorpusError("Independent oracle uses unregistered dynamic execution/import")
    if not functions:
        raise CorpusError("Independent oracle has no implemented function original")
    return functions


def runtime_kinds(contract: bytes, runner: bytes) -> set[str]:
    """Read typed and dispatched step literals from actual archived source, without import."""
    try:
        contract_tree = ast.parse(contract.decode("utf-8"))
        runner_tree = ast.parse(runner.decode("utf-8"))
    except (UnicodeError, SyntaxError) as error:
        raise CorpusError("Registered actual ScenarioRunner source cannot be parsed") from error
    typed: set[str] = set()
    for node in ast.walk(contract_tree):
        if isinstance(node, ast.ClassDef) and node.name == "ScenarioStep":
            for member in node.body:
                if (
                    isinstance(member, ast.AnnAssign)
                    and isinstance(member.target, ast.Name)
                    and member.target.id == "kind"
                ):
                    typed.update(
                        child.value
                        for child in ast.walk(member.annotation)
                        if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    )
    dispatched: set[str] = set()
    for node in ast.walk(runner_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_dispatch":
            for branch in ast.walk(node):
                if (
                    isinstance(branch, ast.Compare)
                    and isinstance(branch.left, ast.Attribute)
                    and branch.left.attr == "kind"
                    and len(branch.ops) == 1
                    and isinstance(branch.ops[0], ast.Eq)
                ):
                    dispatched.update(
                        item.value
                        for item in branch.comparators
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    )
    supported = typed & dispatched
    if not supported:
        raise CorpusError("No actual typed and dispatched ScenarioRunner step kinds verified")
    return supported


def production_sources(source_root: Path) -> set[str]:
    """Formal corpora bind the complete financial API/migration source, not selected files."""
    root = source_root.resolve()
    api, migration = root / "apps/api/app", root / "apps/api/alembic"
    if not api.is_dir() or not migration.is_dir():
        raise CorpusError("Complete actual API and migration source trees are absent")
    result = {
        path.relative_to(root).as_posix()
        for base in (api, migration)
        for path in base.rglob("*.py")
        if "tests" not in path.relative_to(base).parts and "__pycache__" not in path.parts
    }
    for name in ("pyproject.toml", "uv.lock", "alembic.ini"):
        if not (root / name).is_file():
            raise CorpusError(f"Actual runtime configuration/lock original is missing: {name}")
        result.add(name)
    return result


class Draft:
    def __init__(self, path: Path, source_root: Path):
        self.root = path.resolve().parent
        self.source_root = source_root.resolve()
        self.originals: dict[Path, bytes] = {}
        self.source_paths: dict[str, str] = {}
        self.manifest = obj(self.load_path(path.resolve()), "corpus draft")
        if self.manifest.get("protocol") != DRAFT_PROTOCOL:
            raise CorpusError("Unsupported corpus draft protocol")
        self.purpose = self.manifest.get("purpose")
        if self.purpose not in PURPOSES:
            raise CorpusError("Unsupported corpus purpose")
        self.schema = obj(
            self.load_ref(self.manifest.get("schema_ref")), "registered corpus schema"
        )
        self.seed = obj(self.load_ref(self.manifest.get("seed_ref")), "seed original")
        self.source = obj(self.load_ref(self.manifest.get("source_ref")), "source original")
        self.design = obj(self.load_ref(self.manifest.get("design_ref")), "design original")
        for name, artifact in (
            ("schema", self.schema),
            ("seed", self.seed),
            ("source", self.source),
            ("design", self.design),
        ):
            if artifact.get("purpose") != self.purpose:
                raise CorpusError(f"Original {name} belongs to another corpus purpose")
        if self.seed.get("seed_version") != "mvp-301-v6" or not self.seed.get("initial_fact_refs"):
            raise CorpusError("Complete versioned seed original and initial facts are required")
        for ref in seq(self.seed["initial_fact_refs"], "seed initial facts"):
            self.load_ref(ref)
        files = seq(self.source.get("files"), "source inventory")
        if not files:
            raise CorpusError("No original source files registered")
        physical_sources = set()
        for value in files:
            ref = obj(value, "current source reference")
            relative = text(ref.get("path"), "source path")
            if relative in self.source_paths:
                raise CorpusError("Current source is registered twice")
            path = inside(self.source_root, relative)
            if path in physical_sources:
                raise CorpusError("The same physical current source is aliased twice")
            physical_sources.add(path)
            data = self.load_path(path, parse=False)
            expected = sha(ref.get("sha256"))
            if digest(data) != expected:
                raise CorpusError(f"Current source drift: {relative}")
            self.source_paths[relative] = expected
        contract_source = sha(self.schema.get("contract_source_sha256"))
        if contract_source not in self.source_paths.values():
            raise CorpusError("Registered input contract is not bound to current source bytes")
        if sha(self.seed.get("seed_source_sha256")) not in self.source_paths.values():
            raise CorpusError("Seed source is not bound to actual current source bytes")
        if self.schema.get("contract_status") != "SUPPORTED_INPUTS_REGISTERED":
            raise CorpusError("No supported runnable input contract registration")
        if self.purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}:
            if not production_sources(self.source_root) <= set(self.source_paths):
                raise CorpusError(
                    "The source inventory omits actual production API/migration/configuration files"
                )
            contract_path = text(
                self.schema.get("contract_source_path"), "actual contract source path"
            )
            runner_path = text(self.schema.get("runner_source_path"), "actual runner source path")
            if self.source_paths.get(contract_path) != contract_source or self.source_paths.get(
                runner_path
            ) != sha(self.schema.get("runner_source_sha256")):
                raise CorpusError("Actual typed contract/runner source paths or hashes are unbound")
            supported = runtime_kinds(
                self.originals[inside(self.source_root, contract_path)],
                self.originals[inside(self.source_root, runner_path)],
            )
            if not set(obj(self.schema.get("step_schemas"), "step schemas")) <= supported:
                raise CorpusError(
                    "Registered step schemas include unimplemented actual ScenarioRunner kinds"
                )
        if self.manifest.get("profile") not in {"MVP", "FULL", "DEVELOPMENT", "TOOL"}:
            raise CorpusError("Unknown corpus validation profile")
        if self.purpose == "MVP_FROZEN" and self.manifest["profile"] != "MVP":
            raise CorpusError("MVP purpose cannot use another quota profile")
        if self.purpose == "FULL_FAMILY_FROZEN" and self.manifest["profile"] != "FULL":
            raise CorpusError("FULL purpose requires its actual family schema")
        if self.purpose == "DEVELOPMENT" and self.manifest["profile"] != "DEVELOPMENT":
            raise CorpusError("Development data cannot claim a formal quota profile")
        self.development = obj(
            self.load_ref(self.manifest.get("development_inventory_ref")), "development inventory"
        )
        if (
            self.development.get("purpose") != "DEVELOPMENT"
            or self.development.get("inventory_status") != "REGISTERED_COMPLETE"
        ):
            raise CorpusError("Actual complete registered development inventory is absent")
        development_inputs = seq(self.development.get("inputs"), "development input originals")
        if not development_inputs:
            raise CorpusError("Development exclusion cannot use a vacuous empty inventory")
        self.development_flows = set()
        for ref in development_inputs:
            original = obj(self.load_ref(ref), "development original")
            if original.get("intended_purpose") != "DEVELOPMENT":
                raise CorpusError("Development original has another purpose")
            self.check_input(original, expected_purpose="DEVELOPMENT")
            self.development_flows.add(normalized_fingerprint(original, flow=True))
        self.cases: list[dict[str, Any]] = []
        ids, semantic = set(), set()
        for value in seq(self.manifest.get("cases"), "case originals"):
            entry = obj(value, "case registration")
            case_id = text(entry.get("case_id"), "registered case_id")
            family = text(entry.get("family_id"), "registered family_id")
            original = obj(self.load_ref(entry.get("input_ref")), "case original")
            self.check_input(original, expected_purpose=self.purpose)
            if (
                case_id in ids
                or original.get("case_id") != case_id
                or original.get("family_id") != family
            ):
                raise CorpusError("Case identity is missing, duplicated or rebound")
            ids.add(case_id)
            fingerprint = normalized_fingerprint(original, flow=False)
            flow = normalized_fingerprint(original, flow=True)
            if fingerprint in semantic:
                raise CorpusError("Cases differ only by normalized identifiers/titles")
            semantic.add(fingerprint)
            if self.purpose != "DEVELOPMENT" and flow in self.development_flows:
                raise CorpusError(
                    f"Case {case_id} only renames/rescales a registered development flow"
                )
            oracle = obj(self.load_ref(entry.get("oracle_ref")), "independent oracle")
            if (
                oracle.get("case_id") != case_id
                or oracle.get("purpose") != self.purpose
                or oracle.get("input_sha256") != entry["input_ref"]["sha256"]
                or oracle.get("implementation_origin") != "INDEPENDENT_INTEGER_ORACLE"
            ):
                raise CorpusError("Independent oracle identity/input/purpose binding differs")
            for name in ("seed", "source", "design"):
                if oracle.get(f"{name}_sha256") != self.manifest[f"{name}_ref"]["sha256"]:
                    raise CorpusError(f"Independent oracle {name} original binding differs")
            calculators = seq(oracle.get("source_refs"), "independent oracle sources")
            if not calculators:
                raise CorpusError("Independent oracle source originals are missing")
            calculator_functions = set()
            for ref in calculators:
                data = self.load_ref(ref, parse=False)
                if sha(obj(ref, "oracle source")["sha256"]) not in self.source_paths.values():
                    raise CorpusError(
                        "Oracle source is absent from the actual current source inventory"
                    )
                calculator_functions.update(independent_python(data))
            calculator_registry = obj(
                oracle.get("metric_calculators"), "independent metric calculator registration"
            )
            if (
                self.purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}
                and set(calculator_registry) != METRICS
            ):
                raise CorpusError("Fourteen independent metric calculators are not registered")
            if any(
                text(function, "registered calculator function") not in calculator_functions
                for function in calculator_registry.values()
            ):
                raise CorpusError(
                    "An independent calculator function has no actual source definition"
                )
            for key in (
                "protection_timeline",
                "permission_intervals",
                "due_checkpoints",
                "safe_auto_opportunity_ids",
                "required_evidence_manifest",
                "audit_checkpoint_ids",
                "expected_causes",
            ):
                seq(oracle.get(key), f"independent oracle {key}")
            rules = obj(entry.get("rule_refs"), "five original arm rules")
            if set(rules) != ARMS:
                raise CorpusError("All five distinct actual arm rule originals are required")
            if oracle.get("rule_sha256_by_arm") != {
                arm: ref["sha256"] for arm, ref in rules.items()
            }:
                raise CorpusError("Independent oracle's exact arm rule set differs")
            mechanisms = set()
            for arm, ref in rules.items():
                rule = obj(self.load_ref(ref), "original arm rule")
                if rule.get("arm_id") != arm or rule.get("purpose") != self.purpose:
                    raise CorpusError("Original rule arm/purpose binding differs")
                mechanism = text(rule.get("mechanism_id"), "mechanism_id")
                if mechanism in mechanisms:
                    raise CorpusError("Two arms only relabel the same registered mechanism")
                mechanisms.add(mechanism)
                for source_ref in seq(
                    rule.get("implementation_source_refs"), "rule implementation originals"
                ):
                    self.load_ref(source_ref, parse=False)
                    if (
                        sha(obj(source_ref, "arm source")["sha256"])
                        not in self.source_paths.values()
                    ):
                        raise CorpusError(
                            "Arm implementation source is absent from current source inventory"
                        )
                if not rule["implementation_source_refs"]:
                    raise CorpusError("An actual arm implementation original is missing")
                if (
                    self.purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}
                    and rule.get("execution_mode") != "SERVICE_INTEGRATION"
                ):
                    raise CorpusError(
                        "Model-only arm candidates cannot freeze a real-service comparison"
                    )
            self.cases.append(
                {
                    "case_id": case_id,
                    "family_id": family,
                    "input_sha256": entry["input_ref"]["sha256"],
                    "normalized_input_sha256": fingerprint,
                    "normalized_flow_sha256": flow,
                    "development_flow_different": flow not in self.development_flows,
                }
            )
        if not self.cases:
            raise CorpusError("No complete case originals")
        profile = self.manifest["profile"]
        families = [case["family_id"] for case in self.cases]
        if profile == "MVP":
            quotas(families, MVP_QUOTAS)
        elif profile == "FULL":
            family_schema = obj(
                self.load_ref(self.manifest.get("family_schema_ref")), "FULL family schema"
            )
            quotas(families, obj(family_schema.get("required_counts"), "FULL required counts"))
        self.unchanged()

    def load_path(self, path: Path, *, parse: bool = True) -> Any:
        if not path.is_file():
            raise CorpusError(f"Missing original: {path.name}")
        data = path.read_bytes()
        if path in self.originals and self.originals[path] != data:
            raise CorpusError("An original changed during this invocation")
        self.originals[path] = data
        return read_json(data) if parse else data

    def load_ref(self, value: Any, *, parse: bool = True) -> Any:
        ref = obj(value, "original descriptor")
        path = inside(self.root, ref.get("path"))
        original = self.load_path(path, parse=parse)
        if digest(self.originals[path]) != sha(ref.get("sha256")):
            raise CorpusError(f"Original byte hash differs: {path.name}")
        return original

    def check_input(self, original: dict[str, Any], *, expected_purpose: str) -> None:
        if (
            original.get("protocol") != CASE_PROTOCOL
            or original.get("intended_purpose") != expected_purpose
            or original.get("seed_version") != self.seed["seed_version"]
        ):
            raise CorpusError("Complete case protocol/purpose/seed binding differs")
        if expected_purpose != "DEVELOPMENT":
            for name in ("seed", "source"):
                if original.get(f"{name}_sha256") != self.manifest[f"{name}_ref"]["sha256"]:
                    raise CorpusError(f"Complete case {name} original binding differs")
        validate_schema(
            original.get("initial_state"),
            obj(self.schema.get("initial_state_schema"), "initial state schema"),
        )
        steps = seq(original.get("steps"), "case steps")
        if not steps:
            raise CorpusError("A case requires actual supported steps")
        kinds = obj(self.schema.get("step_schemas"), "supported step schemas")
        seen, previous = set(), None
        for value in steps:
            step = obj(value, "case step")
            identity = text(step.get("step_id"), "original step_id")
            kind = text(step.get("kind"), "original step kind")
            if identity in seen or kind not in kinds:
                raise CorpusError("Duplicate step or unsupported actual service input kind")
            try:
                clock = datetime.fromisoformat(text(step.get("at"), "step clock"))
                if (
                    clock.tzinfo is None
                    or clock.utcoffset() is None
                    or (previous is not None and clock < previous)
                ):
                    raise ValueError("Invalid clock")
            except ValueError as error:
                raise CorpusError("Case clocks must be aware and monotonic") from error
            validate_schema(
                step.get("inputs"), obj(kinds[kind], "registered step input"), f"step {identity}"
            )

            def backward_refs(field: Any) -> None:
                if isinstance(field, dict):
                    for key, item in field.items():
                        if key == "$ref":
                            owner, separator, pointer = text(item, "original reference").partition(
                                "#"
                            )
                            if not separator or owner not in seen or not pointer.startswith("/"):
                                raise CorpusError(
                                    "Case reference must bind a strict earlier original step"
                                )
                        else:
                            backward_refs(item)
                elif isinstance(field, list):
                    for item in field:
                        backward_refs(item)

            backward_refs(step["inputs"])
            if step.get("fault", "NONE") not in seq(self.schema.get("faults"), "registered faults"):
                raise CorpusError("Unsupported actual fault adapter")
            seen.add(identity)
            previous = clock

    def unchanged(self) -> None:
        for path, original in self.originals.items():
            if not path.is_file() or path.read_bytes() != original:
                raise CorpusError(f"Original/source drift during operation: {path.name}")


def validate(path: Path, source_root: Path) -> dict[str, Any]:
    try:
        draft = Draft(path, source_root)
        return {
            "status": "VALID_CORPUS_STRUCTURE",
            "purpose": draft.purpose,
            "profile": draft.manifest["profile"],
            "cases": draft.cases,
            "original_count": len(draft.originals),
            "financial_effect_evidence": False,
            "scope": (
                "Actual registered bytes, quotas, input schemas and novelty against "
                "the registered development inventory; not runtime success or oracle correctness"
            ),
        }
    except (CorpusError, OSError) as error:
        return {"status": "INVALID", "reason": str(error), "financial_effect_evidence": False}


def freeze(path: Path, destination: Path, source_root: Path) -> dict[str, Any]:
    if destination.exists():
        raise CorpusError("Freeze destination already exists; preserve it and select a new version")
    draft = Draft(path, source_root)
    destination.mkdir(parents=False, exist_ok=False)
    files = []
    try:
        for index, (original_path, data) in enumerate(sorted(draft.originals.items())):
            relative = f"originals/{index:05d}-{original_path.name}"
            output = destination / relative
            output.parent.mkdir(exist_ok=True)
            with output.open("xb") as stream:
                stream.write(data)
            source_relative = next(
                (
                    name
                    for name in draft.source_paths
                    if inside(draft.source_root, name) == original_path
                ),
                None,
            )
            files.append(
                {
                    "path": relative,
                    "sha256": digest(data),
                    "size_bytes": len(data),
                    "current_source_relative_path": source_relative,
                }
            )
        draft.unchanged()
        manifest = {
            "protocol": FROZEN_PROTOCOL,
            "freeze_id": str(uuid4()),
            "purpose": draft.purpose,
            "profile": draft.manifest["profile"],
            "created_at": datetime.now(UTC).isoformat(),
            "status": "IMMUTABLE_CORPUS_BYTES_REGISTERED",
            "files": files,
            "cases": draft.cases,
            "source_inventory": draft.source_paths,
            "source_scope": "ALL_PRODUCTION_API_AND_REGISTERED_ADAPTERS"
            if draft.purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"}
            else "TOOL_OR_DEVELOPMENT_REGISTERED_SOURCE_FILES",
            "tool_source_sha256": digest(Path(__file__).read_bytes()),
            "financial_effect_evidence": False,
            "scope": (
                "Immutable complete original bytes and registered input/oracle/rule/seed/source "
                "identities; no case execution or financial acceptance"
            ),
        }
        with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(canonical(manifest).decode("utf-8") + "\n")
        return {**manifest, "manifest_sha256": digest((destination / "manifest.json").read_bytes())}
    except Exception as error:
        with (destination / "INCOMPLETE.json").open("x", encoding="utf-8") as stream:
            stream.write(
                canonical(
                    {
                        "status": "INCOMPLETE",
                        "reason": str(error),
                        "files_copied": files,
                        "financial_effect_evidence": False,
                    }
                ).decode("utf-8")
                + "\n"
            )
        raise


def verify(destination: Path, source_root: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    try:
        original_manifest = (destination / "manifest.json").read_bytes()
        if digest(original_manifest) != sha(expected_manifest_sha256):
            raise CorpusError("Frozen manifest differs from the external registered digest")
        manifest = obj(read_json(original_manifest), "freeze manifest")
        if (
            manifest.get("protocol") != FROZEN_PROTOCOL
            or manifest.get("purpose") not in PURPOSES
            or manifest.get("status") != "IMMUTABLE_CORPUS_BYTES_REGISTERED"
        ):
            raise CorpusError("Frozen manifest protocol/purpose/status differs")
        files = seq(manifest.get("files"), "frozen originals")
        if not files:
            raise CorpusError("Frozen original inventory is empty")
        listed, source_records = set(), {}
        for value in files:
            ref = obj(value, "frozen file")
            path = inside(destination, ref.get("path"))
            if path in listed:
                raise CorpusError("Frozen file is registered twice")
            listed.add(path)
            data = path.read_bytes()
            if digest(data) != sha(ref.get("sha256")) or len(data) != ref.get("size_bytes"):
                raise CorpusError("An actual frozen original byte/hash/size differs")
            source_relative = ref.get("current_source_relative_path")
            if source_relative is not None:
                source_path = inside(source_root, source_relative)
                if source_path.read_bytes() != data:
                    raise CorpusError(f"Current source drift: {source_relative}")
                source_records[source_relative] = ref["sha256"]
        if source_records != manifest.get("source_inventory"):
            raise CorpusError("Complete current source inventory differs from archived originals")
        if manifest["purpose"] in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"} and not production_sources(
            source_root
        ) <= set(source_records):
            raise CorpusError("Actual production source was added after freeze or omitted")
        actual = {
            path.resolve()
            for path in destination.rglob("*")
            if path.is_file() and path.resolve() != (destination / "manifest.json").resolve()
        }
        if any(
            path.is_symlink()
            or (path.is_dir() and path.resolve() != (destination / "originals").resolve())
            for path in destination.rglob("*")
        ):
            raise CorpusError("Frozen directory has an unregistered directory or symlink")
        if actual != listed:
            raise CorpusError("Frozen directory contains missing or unregistered originals")
        return {
            "status": "VERIFIED_FROZEN_BYTES_AND_CURRENT_SOURCE",
            "freeze_id": manifest["freeze_id"],
            "purpose": manifest["purpose"],
            "manifest_sha256": digest((destination / "manifest.json").read_bytes()),
            "financial_effect_evidence": False,
        }
    except (CorpusError, OSError) as error:
        return {"status": "INVALID", "reason": str(error), "financial_effect_evidence": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["validate", "freeze", "verify"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--manifest-sha256", help="Externally registered freeze digest; required for verify"
    )
    args = parser.parse_args(argv)
    if args.operation == "freeze":
        result = freeze(args.input, args.output, args.source_root)
    else:
        if args.output.exists():
            parser.error("Output report exists; use a new path")
        if args.operation == "verify" and args.manifest_sha256 is None:
            parser.error("verify requires the external --manifest-sha256")
        result = (
            validate(args.input, args.source_root)
            if args.operation == "validate"
            else verify(
                args.input, args.source_root, text(args.manifest_sha256, "external manifest digest")
            )
        )
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(canonical(result).decode("utf-8") + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output.resolve()),
                "manifest_sha256": result.get("manifest_sha256"),
                "financial_effect_evidence": False,
            }
        )
    )
    return 1 if result["status"] == "INVALID" else 0


if __name__ == "__main__":
    raise SystemExit(main())
