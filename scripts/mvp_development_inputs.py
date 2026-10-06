"""Register original DEVELOPMENT requests and exact novelty checks; never run services.

Six whole public requests include native seed/epoch/template prerequisites. Their
input definitions are complete; successful business continuations are unobserved.
Legacy provider opportunities remain partial. No database, RPC, bank, or browser
execution is implemented by this tool.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.mvp_authored_schedule import compile_case
from scripts.mvp_corpus_v2 import MVP_QUOTAS, normalized_fingerprint, production_sources
from scripts.mvp_native_schema import SOURCES, NativeAdapter, canonical, strict_json

PROTOCOL = "bounded-funds-development-public-input-inventory-v1"
METHOD = "SIX_WHOLE_PUBLIC_NATIVE_REQUESTS_WITH_ORIGINAL_PRECONDITIONS_V1"
STATUS = "REGISTERED_6_PUBLIC_NATIVE_REQUESTS_WITH_PARTIAL_LEGACY_INVENTORY"
EVENTS = (
    "SALARY_RECEIVED",
    "CREATE_CAR_GOAL",
    "LARGE_CONSUMPTION",
    "AUTO_REDEEM",
    "FIXED_EARLY_WITHDRAWAL",
    "CHANGE_RENT",
)
REQUIREMENTS = {
    "SALARY_RECEIVED": ["CAR_GOAL", "LIQUID_ASSET"],
    "CREATE_CAR_GOAL": ["CAR_GOAL"],
    "LARGE_CONSUMPTION": [],
    "AUTO_REDEEM": [],
    "FIXED_EARLY_WITHDRAWAL": ["FIXED_ASSET"],
    "CHANGE_RENT": ["RENT"],
}
ADDITIONAL_SOURCES = (
    "scripts/mvp_development_inputs.py",
    "scripts/mvp_native_schema.py",
    "scripts/mvp_corpus_v2.py",
    "scripts/mvp_authored_schedule.py",
    "apps/web/tests/e2e/w1-demo.spec.ts",
    "docs/experiments/mvp-case-design.md",
)
ANCHOR = datetime(2026, 10, 5, 0, tzinfo=UTC)


class DevelopmentError(ValueError):
    """Missing originals, source drift, unsupported flows, or novelty conflicts."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DevelopmentError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def descriptor(path: Path, raw: bytes) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": digest(raw), "bytes": len(raw)}


def obj(value: Any, message: str) -> dict[str, Any]:
    require(isinstance(value, dict), message)
    return dict(value)


def seq(value: Any, message: str) -> list[Any]:
    require(isinstance(value, list), message)
    return list(value)


def owned(root: Path, value: str) -> Path:
    candidate = root / value
    require(".." not in candidate.parts, "Parent traversal original refused")
    require(
        not any(parent.is_symlink() for parent in (candidate, *candidate.parents)),
        "Symlink original refused",
    )
    path = candidate.resolve()
    require(path.is_relative_to(root.resolve()) and path != root.resolve(), "Path outside root")
    return path


def reference(step_id: str, pointer: str) -> dict[str, Any]:
    return {"$ref": {"step_id": step_id, "pointer": pointer}}


def build_request(event: str, *, seed_sha256: str, source_sha256: str) -> dict[str, Any]:
    """New source-derived request, never a retrospective capture or success result."""
    require(event in EVENTS, "Unsupported public event")
    steps: list[dict[str, Any]] = []

    def add(kind: str, inputs: dict[str, Any], step_id: str) -> None:
        at = ANCHOR + timedelta(seconds=len(steps))
        steps.append({"step_id": step_id, "kind": kind, "at": at.isoformat(), "inputs": inputs})

    # A real full snapshot supplies the freshly seeded original epoch. No UUID
    # substitution, reset, empty snapshot payload, or assumed financial result.
    add("SNAPSHOT", {}, "seed-original")
    epoch = reference("seed-original", "/result/tables/audit_epochs/0/id")

    def template(kind: str) -> None:
        name = kind.lower().replace("_", "-")
        prepare = "prepare-" + name
        add("PREPARE_TEMPLATE", {"kind": kind, "expected_epoch_id": epoch}, prepare)
        add(
            "CONFIRM_TEMPLATE",
            {
                "proposal_id": reference(prepare, "/result/proposal_id"),
                "reviewed_hash": reference(prepare, "/result/configuration_hash"),
                "accepted": True,
            },
            "confirm-" + name,
        )

    def event_step(kind: str, name: str) -> None:
        add("DEMO_EVENT", {"event_kind": kind, "expected_epoch_id": epoch}, name)

    if event == "CREATE_CAR_GOAL":
        template("CAR_GOAL")
        event_step(event, "create-goal")
    elif event == "CHANGE_RENT":
        template("RENT")
        event_step(event, "rent-original-change")
        pointer = "/result/policy_change/"
        add(
            "CHANGE_POLICY",
            {
                key: reference("rent-original-change", pointer + key)
                for key in (
                    "policy_id",
                    "expected_version_id",
                    "configuration",
                    "reviewed_hash",
                    "reason",
                    "idempotency_key",
                )
            }
            | {"accepted": True},
            "rent-explicit-user-confirmation",
        )
        event_step(event, "rent-original-after-confirmation")
    else:
        # This is the current RealUI.core/recovery/fixed prerequisite route;
        # authority and bank facts are obtained by the original public services.
        template("CAR_GOAL")
        template("LIQUID_ASSET")
        event_step("CREATE_CAR_GOAL", "create-goal")
        event_step("SALARY_RECEIVED", "salary-original")
        if event in {"LARGE_CONSUMPTION", "AUTO_REDEEM", "FIXED_EARLY_WITHDRAWAL"}:
            event_step("LARGE_CONSUMPTION", "consumption-original")
        if event in {"AUTO_REDEEM", "FIXED_EARLY_WITHDRAWAL"}:
            event_step("AUTO_REDEEM", "recovery-original")
        if event == "FIXED_EARLY_WITHDRAWAL":
            template("FIXED_ASSET")
            event_step(event, "fixed-original-first-request")
    add("SNAPSHOT", {}, "final-original")
    case_id = "DEV-PUBLIC-V1-" + event
    return {
        "protocol": "bounded-funds-case-input-v2",
        "case_id": case_id,
        "family_id": "PUBLIC_DEMO_REQUEST",
        "intended_purpose": "DEVELOPMENT",
        "seed_version": "mvp-301-v6",
        "seed_sha256": seed_sha256,
        "source_sha256": source_sha256,
        "execution_input": {
            "protocol": "bounded-funds-scenario-v1",
            "scenario_id": case_id,
            "purpose": "DEVELOPMENT",
            "dataset_id": "public-demo-source-derived-definitions-v1",
            "family_id": "PUBLIC_DEMO_REQUEST",
            "initial_state": {
                "seed_version": "mvp-301-v6",
                "mode": "SEED_NEW",
                "expected_epoch_id": None,
            },
            "steps": steps,
            "expected_properties": ["BANK_LEDGER_VALID", "AUDIT_VALID", "NO_LOSS_AUTOMATIC"],
        },
        "data_origin": {
            "definition_method": METHOD,
            "kind": "NEW_SOURCE_DERIVED_DEVELOPMENT_REQUEST_DEFINITION",
            "public_event": event,
            "original_call_preconditions": REQUIREMENTS[event],
            "whole_request_input_defined": True,
            "captured_historical_input": False,
            "runtime_execution": "NOT_RUN",
            "business_completion": "UNVERIFIED",
            "financial_effect_evidence": False,
            "continuation_boundary": (
                "BLOCKED_LINEAR_SCENARIO_HAS_NO_CONDITIONAL_ASK_ACTION_SELECTOR"
                if event == "FIXED_EARLY_WITHDRAWAL"
                else "ACTUAL_WAITING_BLOCKED_UNKNOWN_RESULTS_MUST_BE_RETAINED"
            ),
            "synthetic_actor_review": "EXPLICIT_EXACT_TEMPLATE_OR_ORIGINAL_RENT_CHANGE",
        },
    }


def source_contract(raw: bytes) -> dict[str, Any]:
    tree = ast.parse(raw.decode("utf-8"))
    values: dict[str, Any] = {}
    locations: dict[str, dict[str, int]] = {}
    for node in tree.body:
        name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name, value = target.id, node.value
        if name in {"EVENT_KINDS", "_REQUIREMENTS"} and value is not None:
            values[str(name)] = ast.literal_eval(value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            locations[node.name] = {"line": node.lineno, "end_line": node.end_lineno or node.lineno}
    require(tuple(values.get("EVENT_KINDS", ())) == EVENTS, "Actual public events changed")
    require(values.get("_REQUIREMENTS") == REQUIREMENTS, "Actual public preconditions changed")
    require(
        {"run_demo_event", "template_configuration", "_view", "_advance_action"} <= set(locations),
        "Original public service functions absent",
    )
    return {"events": list(EVENTS), "requirements": REQUIREMENTS, "original_functions": locations}


def local_request_pattern(request: dict[str, Any]) -> str:
    """Diagnostic extraction only; this never converts a legacy record to a Scenario."""
    return normalized_fingerprint(
        {
            "execution_input": {
                "steps": [{"step_id": "request", "kind": "PREPARE_ACTION", "inputs": request}]
            }
        },
        flow=True,
    )


def compare_requests(
    public: list[dict[str, Any]],
    formal: list[dict[str, Any]],
    legacy_patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    public_flows = {case["case_id"]: normalized_fingerprint(case, flow=True) for case in public}
    conflicts: list[dict[str, Any]] = []
    local_overlaps: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    for case in formal:
        variants = obj(case["variants"], "Complete author variants missing")
        for variant, complete in variants.items():
            fingerprint = normalized_fingerprint(obj(complete, "Whole original missing"), flow=True)
            matches = [name for name, flow in public_flows.items() if flow == fingerprint]
            comparisons.append(
                {"case_id": case["case_id"], "variant": variant, "flow_signature": fingerprint}
            )
            for match in matches:
                conflicts.append(
                    {"case_id": case["case_id"], "variant": variant, "development_case_id": match}
                )
            execution = obj(complete["execution_input"], "Whole execution missing")
            for step in seq(execution["steps"], "Whole steps missing"):
                if step.get("kind") != "PREPARE_ACTION":
                    continue
                pattern = local_request_pattern(obj(step["inputs"], "Original DTO missing"))
                for legacy in legacy_patterns:
                    if legacy["flow_signature"] == pattern:
                        local_overlaps.append(
                            {
                                "case_id": case["case_id"],
                                "variant": variant,
                                "step_id": step["step_id"],
                                "legacy_registry": legacy["registry_path"],
                                "legacy_opportunity_index": legacy["opportunity_index"],
                            }
                        )
    return {
        "method": "ORIGINAL_MVP_CORPUS_V2_NORMALIZED_FINGERPRINT_FLOW_TRUE_UNCHANGED",
        "status": "FAILED_WHOLE_REQUEST_ISOMORPHISM" if conflicts else "NO_WHOLE_REQUEST_MATCH",
        "public_flow_signatures": public_flows,
        "formal_variants": comparisons,
        "whole_flow_conflicts": conflicts,
        "legacy_local_request_overlaps": local_overlaps,
        "legacy_local_pattern_scope": "DIAGNOSTIC_ONLY_NOT_COMPLETE_SCENARIO_OR_NOVELTY_PASS",
        "complete_workspace_novelty": "MISSING_COMPLETE_LEGACY_AND_ACL_INPUTS",
        "formal_frozen_acceptance": "NOT_RUN",
    }


class Capture:
    def __init__(self, root: Path, output: Path):
        self.root = root.resolve()
        self.output = output.resolve()
        require(
            self.output.is_relative_to(self.root / ".runtime")
            and self.output != self.root / ".runtime",
            "Fresh output must be inside project .runtime",
        )
        require(not self.output.exists(), "Output already exists; originals cannot be overwritten")
        self.output.mkdir(parents=True)
        self.originals: dict[Path, bytes] = {}
        self.archives: dict[Path, dict[str, Any]] = {}

    def read(self, path: Path, expected: str | None = None) -> bytes:
        path = owned(self.root, str(path))
        require(path.is_file(), "Original missing: " + path.relative_to(self.root).as_posix())
        raw = path.read_bytes()
        if expected is not None:
            require(digest(raw) == expected, "Original byte SHA mismatch: " + path.name)
        if path in self.originals:
            require(self.originals[path] == raw, "Original changed during capture: " + path.name)
        else:
            self.originals[path] = raw
            index = len(self.originals)
            archive = self.output / "originals" / f"{index:04d}-{path.name}"
            archive.parent.mkdir(exist_ok=True)
            archive.write_bytes(raw)
            self.archives[path] = {
                "original": descriptor(path, raw),
                "archive": descriptor(archive, raw),
                "byte_preservation": "EXACT_ORIGINAL_BYTES",
            }
        return raw

    def write(self, name: str, value: Any) -> dict[str, Any]:
        path = self.output / name
        require(path.resolve().is_relative_to(self.output), "Output path escapes capture")
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical(value)
        with path.open("xb") as stream:
            stream.write(raw)
        return {"path": name, "sha256": digest(raw), "bytes": len(raw)}

    def unchanged(self) -> None:
        for path, raw in self.originals.items():
            require(path.read_bytes() == raw, "Original/source changed: " + path.name)
            archive = Path(self.archives[path]["archive"]["path"])
            require(archive.read_bytes() == raw, "Archived original byte mismatch")


def legacy_inputs(
    capture: Capture, routing: dict[str, Any], adapter: NativeAdapter
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records, patterns = [], []
    entries = seq(routing.get("registries"), "Original routing registries absent")
    require(
        len(entries) == routing.get("registry_count") == 30, "Original 30 registry scope changed"
    )
    for row in entries:
        registry_path = owned(capture.root, row["registry_path"])
        registry_raw = capture.read(registry_path, row["registry_sha256"])
        registry = obj(strict_json(registry_raw), "Legacy registry object required")
        require(
            registry.get("protocol") == "mvp-arm-provider-registry-v1", "Legacy protocol changed"
        )
        bindings = obj(registry.get("bindings"), "Legacy bindings missing")
        require(bindings.get("purpose") == "DEVELOPMENT", "Legacy purpose cannot be relabelled")
        artifacts = obj(registry.get("artifact_refs"), "Legacy originals missing")
        preserved: dict[str, Any] = {}
        input_value: dict[str, Any] | None = None
        for role, value in artifacts.items():
            ref = obj(value, "Legacy descriptor required")
            path = owned(capture.root, ref["path"])
            raw = capture.read(path, ref["sha256"])
            preserved[role] = capture.archives[path]
            if role == "input":
                input_value = obj(strict_json(raw), "Legacy input must remain its original object")
        if input_value is None:
            raise DevelopmentError("Legacy actual INPUT missing")
        require(input_value.get("purpose") == "DEVELOPMENT", "Legacy INPUT purpose differs")
        local = []
        for index, opportunity in enumerate(
            seq(input_value.get("opportunities"), "Opportunities absent")
        ):
            request = obj(opportunity.get("request"), "Original local DTO absent")
            try:
                adapter.models["PREPARE_ACTION"].model_validate_json(canonical(request))
            except ValueError:
                local.append({"opportunity_index": index, "status": "UNNORMALIZABLE_ORIGINAL_DTO"})
                continue
            pattern = {
                "registry_path": str(registry_path),
                "opportunity_index": index,
                "flow_signature": local_request_pattern(request),
                "original_request_sha256": digest(canonical(request)),
                "method": "EXPLICIT_LOCAL_PREPARE_DTO_EXTRACTION_NOT_FULL_SCENARIO",
            }
            patterns.append(pattern)
            local.append(pattern | {"status": "LOCAL_DTO_PATTERN_ONLY"})
        records.append(
            {
                "registry": capture.archives[registry_path],
                "case_id": bindings["case_id"],
                "original_protocol": registry["protocol"],
                "purpose": "DEVELOPMENT",
                "preserved_artifacts": preserved,
                "whole_original_scenario": "MISSING",
                "runtime_preconditions_and_full_controls": "MISSING",
                "local_diagnostics": local,
            }
        )
    return records, patterns


def authored_inputs(
    capture: Capture,
    directory: Path,
    source_sha256: str,
    seed_sha256: str,
    design_sha256: str,
) -> list[dict[str, Any]]:
    paths = sorted(directory.glob("*.json"))
    require(len(paths) == 24, "All original 24 authored inputs required")
    result: list[dict[str, Any]] = []
    families: dict[str, int] = {}
    identities: set[str] = set()
    for path in paths:
        raw = capture.read(path)
        author = obj(strict_json(raw), "Original complete author required")
        require(author["case_id"] not in identities, "Duplicate authored case")
        identities.add(author["case_id"])
        family = author["family_id"]
        families[family] = families.get(family, 0) + 1
        ref = {"path": path.resolve().relative_to(capture.root).as_posix(), "sha256": digest(raw)}
        compiled, schedule = compile_case(
            raw,
            ref,
            seed_sha256=seed_sha256,
            source_sha256=source_sha256,
            design_sha256=design_sha256,
        )
        require(
            schedule["control_schedule"] == author["control_schedule"],
            "Original conditional control graph changed",
        )
        # Compare both true originals and the actual explicit conversion. This
        # prevents SEED_NEW->EXISTING being treated as input originality.
        result.append(
            {
                "case_id": author["case_id"],
                "author_original": capture.archives[path.resolve()],
                "original_control_sha256": digest(canonical(author["control_schedule"])),
                "original_actor_sha256": digest(canonical(author["arm_rule_author_inputs"])),
                "original_denominator_sha256": digest(
                    canonical(author["oracle_registration_draft"])
                ),
                "compiled_case_sha256": digest(canonical(compiled)),
                "variants": {
                    "ORIGINAL_AUTHORED_FULL_SCENARIO": author,
                    "COMPILED_FULL_SCENARIO": compiled,
                },
                "financial_execution": "NOT_RUN",
            }
        )
    require(families == MVP_QUOTAS, "Original 24 family quotas differ")
    return result


def register(
    root: Path, output: Path, routing_path: Path, author_directory: Path
) -> dict[str, Any]:
    capture = Capture(root, output)
    routing_raw = capture.read(routing_path)
    routing = obj(strict_json(routing_raw), "Original routing inventory required")
    require(
        routing.get("status") == "READ_ONLY_SOURCE_ROUTING_NOT_COMPLETE_DEVELOPMENT_CORPUS",
        "Routing observation cannot claim complete workspace inventory",
    )
    names = production_sources(root) | set(SOURCES) | set(ADDITIONAL_SOURCES)
    source_map = {name: digest(capture.read(root / name)) for name in sorted(names)}
    source = {
        "purpose": "DEVELOPMENT",
        "files": [{"path": name, "sha256": value} for name, value in source_map.items()],
    }
    source_ref = capture.write("source.json", source)
    public_contract = source_contract(
        capture.originals[root.resolve() / "apps/api/app/services/demo_console.py"]
    )
    adapter = NativeAdapter(root, source_map)
    schema_ref = capture.write("native-schema.json", adapter.registration("DEVELOPMENT"))
    seed_hash = source_map["apps/api/app/services/demo_seed.py"]
    cases = [
        build_request(event, seed_sha256=seed_hash, source_sha256=source_ref["sha256"])
        for event in EVENTS
    ]
    definitions = []
    for case in cases:
        original = canonical(case)
        proof = adapter.validate_execution(canonical(case["execution_input"]), "DEVELOPMENT")
        require(
            proof["runtime_source_results_verified"] is False, "Schema proof became runtime proof"
        )
        ref = capture.write("inputs/" + case["case_id"] + ".json", case)
        definitions.append(
            {
                "input_ref": ref,
                "absolute_input": descriptor(output / ref["path"], original),
                "native_validation": proof,
            }
        )
    legacy, patterns = legacy_inputs(capture, routing, adapter)
    formal = authored_inputs(
        capture,
        author_directory,
        source_ref["sha256"],
        seed_hash,
        source_map["docs/experiments/mvp-case-design.md"],
    )
    novelty = compare_requests(cases, formal, patterns)
    capture.write("novelty.json", novelty)
    capture.write(
        "authored-original-bindings.json",
        [{key: value for key, value in item.items() if key != "variants"} for item in formal],
    )
    capture.write("legacy-originals.json", legacy)
    capture.unchanged()
    adapter.unchanged()
    report = {
        "protocol": PROTOCOL,
        "method": METHOD,
        "status": STATUS,
        "purpose": "DEVELOPMENT",
        "inventory_status": "PARTIAL",
        "inventory_scope": (
            "SIX_ORIGINAL_PUBLIC_REQUESTS_PLUS_30_UNMODIFIED_PARTIAL_PROVIDER_INPUTS"
        ),
        "workspace_inventory": "PARTIAL_NOT_REGISTERED_COMPLETE",
        "captured_at": datetime.now(UTC).isoformat(),
        "source_ref": source_ref,
        "schema_ref": schema_ref,
        "source_contract": public_contract,
        "inputs": [
            {"path": item["input_ref"]["path"], "sha256": item["input_ref"]["sha256"]}
            for item in definitions
        ],
        "definitions": definitions,
        "legacy_registry_count": len(legacy),
        "legacy_normalizable_local_requests": len(patterns),
        "legacy_complete_scenarios": 0,
        "source_originals": list(capture.archives.values()),
        "routing_original": descriptor(routing_path, routing_raw),
        "unscanned_scope_original_limits": routing["limits"],
        "novelty_status": novelty["status"],
        "whole_flow_conflicts": novelty["whole_flow_conflicts"],
        "source_and_original_bytes_equal_after": True,
        "actual_service_or_financial_execution": "NOT_RUN",
        "actual_database_browser_docker": "NOT_RUN",
        "business_flow_completion": "UNVERIFIED_CONDITIONAL_CONTINUATIONS_BLOCKED",
        "corpus_v2_complete_inventory_gate": "REFUSED_PARTIAL_BY_ORIGINAL_STRICT_GATE",
        "formal_acceptance": "NOT_RUN",
        "task_closed": False,
    }
    capture.write("manifest.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--routing-inventory", type=Path)
    parser.add_argument("--authored-directory", type=Path)
    args = parser.parse_args(argv)
    if args.output is None:
        print("PREPARED_NOT_WRITTEN: explicit fresh --output required; no service connection")
        return 0
    if args.routing_inventory is None or args.authored_directory is None:
        parser.error("--output requires original --routing-inventory and --authored-directory")
    root = args.source_root.resolve()
    try:
        report = register(
            root,
            owned(root, str(args.output)),
            owned(root, str(args.routing_inventory)),
            owned(root, str(args.authored_directory)),
        )
    except (DevelopmentError, ValueError, OSError, KeyError, TypeError) as error:
        print(
            json.dumps({"status": "FAILED_UNVERIFIED", "reason": str(error), "task_closed": False})
        )
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "inventory_status": "PARTIAL",
                "novelty_status": report["novelty_status"],
                "task_closed": False,
            }
        )
    )
    return 2 if report["whole_flow_conflicts"] else 0


if __name__ == "__main__":
    sys.exit(main())
