"""Read-only original-byte export with conservative source-bound native proof gates."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import json
import os
import re
import runpy
import struct
import subprocess
import sys
import zlib
from datetime import UTC, datetime
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any, cast
from uuid import UUID
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
MAX_SNAPSHOT_DECODED_BYTES = 512 * 1024 * 1024
SNAPSHOT_READ_CHUNK_BYTES = 1024 * 1024
VERSION = "bounded-funds-evidence-request-v1"
PURPOSES = {"DEVELOPMENT", "MVP_ACCEPTANCE", "FULL_ACCEPTANCE", "TOOL_ONLY"}
REQUIRED = (
    "final_four_commands",
    "backend_coverage_and_properties",
    "frontend_interactions",
    "six_business_e2e",
    "three_demo_rounds",
    "offline_three_golden_chains",
    "frozen_24_cases",
    "five_baseline_arms",
    "fourteen_metrics",
    "original_financial_chain",
    "audit_chain",
    "screenshots_and_recording",
    "complete_mvp_documents",
    "eight_figures",
    "proposal_actual_pages",
    "recording_actual_duration",
    "manual_consistency_review",
    "formal_history_preservation",
)
ROLES = {
    "SOURCE",
    "INPUT",
    "ORACLE",
    "RULE",
    "COMMAND_MANIFEST",
    "COMMAND_LOG",
    "RESULT",
    "POLICY",
    "DECISION",
    "RECEIPT",
    "METRICS",
    "FAILURE",
    "SCREENSHOT",
    "VIDEO",
    "AUDIT",
    "MATERIAL",
    "COVERAGE",
    "HYPOTHESIS",
    "DEPLOYMENT",
    "DIAGNOSTIC",
}
CONFIG = {
    "pyproject.toml",
    "uv.lock",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "package.json",
    "alembic.ini",
    "docker-compose.yml",
    "Makefile",
    "make.cmd",
    "apps/web/package.json",
    "apps/web/tsconfig.json",
    "apps/web/vite.config.ts",
    "apps/web/playwright.config.ts",
    "apps/web/eslint.config.js",
    "apps/web/index.html",
    "deploy/compose.demo.yaml",
    "deploy/api.Dockerfile",
    "deploy/web.Dockerfile",
    "deploy/nginx.conf",
    "deploy/.dockerignore",
    ".dockerignore",
}
SOURCE_ROOTS = (
    "apps/api/app",
    "apps/api/alembic",
    "apps/web/src",
    "apps/web/tests",
    "scripts",
    "deploy",
    "packages/contracts",
)
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".css", ".json", ".html"}
SECRET_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".aws", ".codex", ".agents"}
CAPABILITIES = {
    "UTF8_TEXT_V1": "UTF-8 decodability only; no claim or financial conclusion validation",
    "STRICT_JSON_V1": "JSON syntax without duplicate keys/nonfinite constants only",
    "STRICT_JSONL_V1": "Every nonblank original line is strict JSON only; empty is unverified",
    "PNG_HEADER_V1": "PNG signature only; no genuine capture/run/content validation",
}
NATIVE_VALIDATOR = "MVP_NATIVE_V2"
ORIGINAL_FINANCIAL_ORACLE_SHA256 = (
    "5f45cf3879c8fc74354bc6129e67f72788aa9ed0d1d6c2dbaf9f62531727ee35"
)
TYPED_RESET_ADAPTER_SHA256 = "1ac1bdbfe1ca8bd243f1dd3e959a13812c5cd6af045cb6f231af023b580eaac0"
SUPPORTED_NATIVE = {
    "final_four_commands",
    "backend_coverage_and_properties",
    "frontend_interactions",
    "six_business_e2e",
    "three_demo_rounds",
    "original_financial_chain",
    "formal_history_preservation",
    "frozen_24_cases",
    "proposal_actual_pages",
    "audit_chain",
    "offline_three_golden_chains",
}
UNSUPPORTED_NATIVE = {
    "five_baseline_arms": "Actual distinct five-arm service adapter is absent",
    "fourteen_metrics": (
        "Complete fourteen-metric independent denominator/oracle validator is not implemented"
    ),
    "screenshots_and_recording": "Native capture/session and complete video contract is absent",
    "complete_mvp_documents": "No complete material-content/claim reviewer has run",
    "eight_figures": "No actual eight-figure content/data reviewer has run",
    "recording_actual_duration": "No trusted complete video decoder registered in this runtime",
    "manual_consistency_review": "No actual per-claim source/number/content review originals yet",
}
PARTIAL_NATIVE = {"audit_chain", "screenshots_and_recording"}
NATIVE_BUSINESS_TABLES = {
    "accounts",
    "action_plans",
    "action_receipts",
    "action_resource_reservations",
    "asset_positions",
    "asset_products",
    "audit_epochs",
    "audit_events",
    "audit_subject_snapshots",
    "bank_operations",
    "credit_card_bills",
    "decision_constraints",
    "decision_runs",
    "evidence_items",
    "external_bank_facts",
    "goals",
    "policies",
    "policy_proposals",
    "policy_versions",
    "simulated_bank_postings",
    "simulated_bank_redemptions",
    "transactions",
    "users",
}


class RequestError(ValueError):
    pass


class MissingProof(ValueError):
    pass


class UnsupportedProof(ValueError):
    pass


class PartialProof(ValueError):
    """Useful original recomputation that explicitly cannot close the whole group."""

    def __init__(self, observations: dict[str, Any], uncovered: list[str]) -> None:
        self.observations = observations
        self.uncovered = uncovered
        super().__init__("Native bridge is partial: " + "; ".join(uncovered))


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_digest(mapping: dict[str, str]) -> str:
    return digest(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RequestError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def nonfinite(value: str) -> None:
    raise RequestError(f"Nonfinite JSON value: {value}")


def strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8-sig"), object_pairs_hook=pairs_no_duplicates, parse_constant=nonfinite
    )


def bounded_snapshot_bytes(raw: bytes) -> bytes:
    """Read complete original gzip members/CRC with bounded streaming decompression."""
    output = BytesIO()
    with gzip.GzipFile(fileobj=BytesIO(raw)) as compressed:
        while True:
            remaining = MAX_SNAPSHOT_DECODED_BYTES - output.tell()
            chunk = compressed.read(min(SNAPSHOT_READ_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            need(len(chunk) <= remaining, "Snapshot exceeds bounded 512MiB decode budget")
            output.write(chunk)
    return output.getvalue()


def identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise RequestError(f"{label} must be a bounded plain identity")
    if value.casefold() in {"con", "prn", "aux", "nul"} or re.fullmatch(
        r"(?:com|lpt)[1-9]", value.casefold()
    ):
        raise RequestError(f"{label} is a Windows reserved identity")
    return value


def sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise RequestError("Expected an original lowercase SHA256")
    return value


def relative_path(name: Any) -> Path:
    if not isinstance(name, str) or not name or ":" in name or name.startswith(("/", "\\")):
        raise RequestError("Input paths must be plain repository-relative paths")
    parts = name.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} or part.rstrip(" .") != part for part in parts):
        raise RequestError("Traversal, empty path parts and Windows trailing aliases are refused")
    if any(part.casefold() in SECRET_PARTS or part.casefold().startswith(".env") for part in parts):
        raise RequestError("Sensitive/cache paths are outside the evidence contract")
    for part in parts:
        base = part.split(".")[0].casefold()
        if (
            base in {"con", "prn", "aux", "nul"}
            or re.fullmatch(r"(?:com|lpt)[1-9]", base)
            or any(ord(char) < 32 or char in '<>"|?*' for char in part)
        ):
            raise RequestError("Windows reserved or nonplain path components are refused")
    return Path(*parts)


def resolve_input(root: Path, name: Any, role: str) -> Path:
    original = relative_path(name)
    actual = (root / original).resolve()
    if not actual.is_relative_to(root.resolve()):
        raise RequestError("Resolved input escapes repository (including links/junctions)")
    relative = actual.relative_to(root.resolve()).as_posix()
    relative_path(relative)  # Recheck the resolved target for sensitive path aliases.
    if role == "SOURCE":
        okay = relative in CONFIG or (
            actual.suffix.casefold() in SOURCE_SUFFIXES
            and any(relative.startswith(prefix + "/") for prefix in SOURCE_ROOTS)
        )
    else:
        okay = any(
            relative.startswith(prefix + "/")
            for prefix in ("docs", ".runtime", "data/scenarios", "output/playwright")
        ) or (role == "MATERIAL" and relative == "README.md")
    if not okay:
        raise RequestError(f"Artifact role {role} does not allow path: {relative}")
    return actual


def inventory(root: Path) -> list[str]:
    files = {name for name in CONFIG if (root / name).is_file()}
    for prefix in SOURCE_ROOTS:
        for path in (root / prefix).rglob("*"):
            if path.is_file() and not any(
                part.casefold() in SECRET_PARTS or part.casefold().startswith(".env")
                for part in path.parts
            ):
                if path.suffix.casefold() in SOURCE_SUFFIXES:
                    files.add(path.relative_to(root).as_posix())
    return sorted(files)


def read_stable(path: Path, expected_sha: str, expected_size: int | None = None) -> bytes:
    if not path.is_file():
        raise RequestError(f"Not an original regular file: {path.name}")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise RequestError(f"Original changed while being read: {path.name}")
    if digest(raw) != sha(expected_sha):
        raise RequestError(f"Original SHA256 mismatch: {path.name}")
    if expected_size is not None and (
        type(expected_size) is not int or expected_size < 0 or len(raw) != expected_size
    ):
        raise RequestError(f"Original byte size mismatch: {path.name}")
    return raw


def output_target(root: Path, name: Path) -> Path:
    output = (name if name.is_absolute() else root / name).resolve()
    allowed = (root / ".runtime/evidence_packages").resolve()
    if (
        not allowed.is_relative_to(root.resolve())
        or not output.is_relative_to(allowed)
        or output == allowed
    ):
        raise RequestError("Output must be a new child of repository .runtime/evidence_packages")
    if output.exists():
        raise RequestError("Output already exists; original package will not be overwritten")
    for part in output.relative_to(allowed).parts:
        identity(part, "output directory component")
    return output


def content_check(name: str, raw: bytes) -> dict[str, str]:
    if name not in CAPABILITIES:
        return {"status": "UNVERIFIED", "reason": "No implemented semantic validator for this name"}
    try:
        if name == "UTF8_TEXT_V1":
            raw.decode("utf-8-sig")
        elif name == "STRICT_JSON_V1":
            strict_json(raw)
        elif name == "STRICT_JSONL_V1":
            lines = [line for line in raw.splitlines() if line.strip()]
            if not lines:
                return {
                    "status": "UNVERIFIED",
                    "reason": "Empty JSONL proves no actual failure/run coverage",
                }
            for line in lines:
                strict_json(line)
        elif not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RequestError("Original PNG signature is absent")
    except (ValueError, UnicodeError) as error:
        return {"status": "UNVERIFIED", "reason": str(error)}
    return {"status": "CONTENT_ONLY_VERIFIED", "reason": CAPABILITIES[name]}


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def obj(value: Any, label: str) -> dict[str, Any]:
    need(isinstance(value, dict), f"{label} must be an original object")
    return cast(dict[str, Any], value)


def rows(value: Any, label: str) -> list[Any]:
    need(isinstance(value, list), f"{label} must be an original list")
    return cast(list[Any], value)


def count(value: Any, label: str) -> int:
    need(type(value) is int and value >= 0, f"{label} must be a nonnegative integer")
    return cast(int, value)


def aware(value: Any) -> datetime:
    need(isinstance(value, str), "Actual timestamp is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    need(result.utcoffset() is not None, "Actual timestamp must include timezone")
    return result


class Originals:
    """Resolve only explicitly supplied original bytes; never arbitrary report paths/code."""

    def __init__(
        self,
        root: Path,
        files: dict[str, str],
        prepared: list[tuple[dict[str, Any], bytes | None]],
        head: str = "",
        *,
        tool_test_only: bool = False,
    ) -> None:
        self.root = root
        self.files = files
        self.head = head
        self.tool_test_only = tool_test_only
        self.allowed: set[str] | None = None
        self.entries = {record["artifact_id"]: (record, raw) for record, raw in prepared}

    def original(self, ref: Any, *, historical: bool = False) -> tuple[dict[str, Any], bytes]:
        if not isinstance(ref, str) or ref not in self.entries:
            raise MissingProof("Explicit original artifact ID is missing")
        if self.allowed is not None and ref not in self.allowed:
            raise MissingProof(
                "Native checker referenced an original outside this check's artifact_ids"
            )
        record, raw = self.entries[ref]
        if raw is None:
            raise MissingProof(f"Declared original is missing: {ref}")
        need(
            historical or record["source_context"] == "CURRENT_SOURCE",
            "Historical context cannot close current acceptance",
        )
        need(
            self.tool_test_only
            or record.get("evidence_kind") not in {"TOOL_ONLY", "TOOL_TEST_ONLY"},
            "TOOL_ONLY is never product evidence",
        )
        return record, raw

    def json(self, ref: Any, *, historical: bool = False) -> dict[str, Any]:
        _, raw = self.original(ref, historical=historical)
        value = obj(strict_json(raw), "original JSON")
        need(
            self.tool_test_only
            or value.get("evidence_kind") not in {"TOOL_ONLY", "TOOL_TEST_ONLY"},
            "Synthetic TOOL_ONLY originals cannot pass product acceptance",
        )
        return value

    def by_path(self, path: Any, *, historical: bool = False) -> tuple[dict[str, Any], bytes]:
        need(isinstance(path, str), "Original native path is missing")
        target = relative_path(path).as_posix().casefold()
        matches = [
            name
            for name, (record, _) in self.entries.items()
            if record["resolved_repository_path"].casefold() == target
        ]
        need(len(matches) == 1, "Native original path is not explicitly supplied once")
        return self.original(matches[0], historical=historical)

    def current_map(self, value: Any) -> None:
        mapping = obj(value, "actual complete source inventory")
        normalized = {name.replace("\\", "/"): value for name, value in mapping.items()}
        need(len(normalized) == len(mapping), "Duplicate normalized source paths")
        need(
            all(normalized.get(name) == expected for name, expected in self.files.items()),
            "Native source inventory is missing or differs from current registered source",
        )

    def current_subset(self, value: Any, prefixes: tuple[str, ...]) -> None:
        """Verify a native producer's declared relevant scope under the outer full freeze."""
        mapping = obj(value, "actual native producer source scope")
        normalized = {name.replace("\\", "/"): checksum for name, checksum in mapping.items()}
        required = {name for name in self.files if name.startswith(prefixes)}
        need(
            bool(normalized) and len(normalized) == len(mapping) and required.issubset(normalized),
            "Native producer omitted relevant source scope",
        )
        for name, checksum in normalized.items():
            path = (self.root / relative_path(name)).resolve()
            need(
                path.is_relative_to(self.root) and digest(path.read_bytes()) == sha(checksum),
                "Native producer source differs from current relevant source",
            )

    def scoped(self, spec: Any) -> tuple[dict[str, Any], str]:
        spec = obj(spec, "scoped original references")
        manifest = self.json(spec.get("manifest"))
        record, _ = self.original(spec.get("manifest"))
        need(manifest.get("run_id") == record["run_id"], "Native scoped run ID mismatch")
        need(
            type(manifest.get("exit_code")) is int and manifest["exit_code"] == 0,
            "Actual scoped command did not exit zero",
        )
        need(manifest.get("status") == "PASSED", "An original failed scoped result stays failed")
        need(
            not self.head or manifest.get("git_head") == self.head,
            "Native scoped Git HEAD mismatch",
        )
        need(
            aware(manifest.get("finished_at")) >= aware(manifest.get("started_at")),
            "Scoped actual finish precedes start",
        )
        command = rows(manifest.get("command"), "actual command argv")
        need(
            bool(command) and all(isinstance(item, str) and item for item in command),
            "Actual scoped argv is absent",
        )
        before = self.json(spec.get("source_before"))
        after = self.json(spec.get("source_after"))
        need(before == after, "Actual command source changed")
        self.current_map(before)
        _, log = self.original(spec.get("log"))
        need(digest(log) == manifest.get("log_sha256"), "Actual scoped output log SHA mismatch")
        for ref in (spec.get("source_before"), spec.get("source_after"), spec.get("log")):
            original, _ = self.original(ref)
            need(original["run_id"] == record["run_id"], "Scoped raw files mix original runs")
        return manifest, log.decode("utf-8-sig")

    def tool(self, name: str) -> dict[str, Any]:
        path = self.root / name
        need(
            name in self.files and digest(path.read_bytes()) == self.files[name],
            "Trusted pure checker is outside the bound current source",
        )
        return runpy.run_path(str(path))

    def snapshot(self, metadata: Any) -> dict[str, Any]:
        metadata = obj(metadata, "native snapshot metadata")
        _, raw = self.by_path(metadata.get("path"))
        need(digest(raw) == metadata.get("sha256"), "Original compressed snapshot SHA mismatch")
        payload = bounded_snapshot_bytes(raw)
        need(digest(payload) == metadata.get("data_sha256"), "Original snapshot data SHA mismatch")
        data = obj(strict_json(payload), "original physical snapshot")
        physical = rows(metadata.get("physical_tables"), "actual physical table registry")
        need(
            len(set(physical)) == len(physical) == 24
            and set(physical) == NATIVE_BUSINESS_TABLES | {"alembic_version"},
            "Native physical24 registry is incomplete",
        )
        split_heads = "alembic_version" not in data
        need(
            set(data) | ({"alembic_version"} if split_heads else set()) == set(physical)
            and len(data) == (23 if split_heads else 24),
            "Native business tables differ from the actual physical24 registry",
        )
        need(
            metadata.get("bytes", len(payload)) == len(payload),
            "Snapshot uncompressed byte count mismatch",
        )
        actual_counts = {name: len(rows(value, "physical table")) for name, value in data.items()}
        if "row_counts" in metadata:
            expected_counts = obj(metadata["row_counts"], "actual separately recorded table counts")
            need(
                expected_counts == actual_counts
                and all(type(value) is int for value in expected_counts.values()),
                "Snapshot per-table row counts mismatch",
            )
            if "row_count" in metadata:
                need(
                    sum(actual_counts.values()) == count(metadata["row_count"], "actual row count"),
                    "Snapshot total row count differs from recorded table counts",
                )
        else:
            need(
                sum(actual_counts.values()) == count(metadata.get("row_count"), "actual row count"),
                "Snapshot row count mismatch",
            )
        if split_heads:
            # The original coordinator gzip contains 23 business tables; it separately
            # captures actual SELECT version_num rows in hashed manifest metadata.
            heads = rows(
                metadata.get("metadata_heads"), "actual separately captured migration heads"
            )
            need(
                bool(heads)
                and all(
                    isinstance(head, str) and re.fullmatch(r"[a-zA-Z0-9_]+", head) for head in heads
                )
                and sorted(set(heads)) == heads,
                "Native migration heads are missing, duplicated or malformed",
            )
            data["alembic_version"] = [{"version_num": head} for head in heads]
        return data


def coverage_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    manifest, log = proof.scoped(inputs.get("scoped"))
    need(
        "pytest" in " ".join(manifest["command"]) and "passed" in log,
        "Coverage producer must be the actual successful pytest command",
    )
    tool = proof.tool("scripts/verify_mvp_coverage.py")
    scope = proof.json(inputs.get("scope"))
    scope_path = proof.root / "docs/spec/mvp-coverage-scope.json"
    need(strict_json(scope_path.read_bytes()) == scope, "Coverage core scope was changed")
    tool["validate_scope"](scope)
    coverage = proof.json(inputs.get("coverage"))
    statistics = proof.json(inputs.get("hypothesis"))
    _, coverage_raw = proof.original(inputs.get("coverage"))
    expected = tool["source_hashes"](proof.root, scope)
    bindings = {
        "source_hashes_before": expected,
        "source_hashes_after": expected,
        "scope_sha256": digest(scope_path.read_bytes()),
        "coverage_json_sha256": digest(coverage_raw),
        "counter_sha256": digest((proof.root / "scripts/mvp_hypothesis_counter.py").read_bytes()),
        "verifier_sha256": proof.files["scripts/verify_mvp_coverage.py"],
        "coverage_config_path": scope["coverage"]["config_path"],
        "coverage_config_sha256": digest(
            (proof.root / scope["coverage"]["config_path"]).read_bytes()
        ),
    }
    need(
        all(statistics.get(key) == value for key, value in bindings.items()),
        "Actual coverage/property original source/config/statistics binding mismatch",
    )
    before = {
        key: bindings[key]
        for key in ("scope_sha256", "counter_sha256", "verifier_sha256", "coverage_config_sha256")
    }
    need(
        statistics.get("bindings_before") == statistics.get("bindings_after") == before,
        "Scope/configuration/counter/verifier changed during measurement",
    )
    result = tool["coverage_gate"](coverage, scope, tool["production_files"](proof.root, scope))
    generated = tool["hypothesis_gate"](
        statistics, scope, tool["property_nodes"](proof.root, scope)
    )
    need(result["passed"] and generated["passed"], "Original 85/95/1000 gate failed")
    return {"coverage": result, "hypothesis": generated}


def frontend_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    manifest, log = proof.scoped(inputs.get("scoped"))
    argv = " ".join(manifest["command"])
    need("apps/web" in argv and "test" in argv, "Not an actual Web interaction test command")
    required = (
        "App.test.tsx",
        "dashboard.test.ts",
        "PolicyCenterPage.test.tsx",
        "GoalsPage.test.tsx",
        "DecisionTracePage.test.tsx",
        "DemoConsolePage.test.tsx",
    )
    source_paths = ("apps/web/src/App.test.tsx", "apps/web/src/api/dashboard.test.ts") + tuple(
        "apps/web/src/pages/" + name for name in required[2:]
    )
    need(
        all(name in proof.files for name in source_paths),
        "Original critical component test source is missing from current inventory",
    )
    registered_minimum = sum(
        len(re.findall(r"\b(?:test|it)\s*\(", (proof.root / name).read_text(encoding="utf-8")))
        for name in source_paths
    )
    need(registered_minimum > 0, "Empty component files are not interaction tests")
    log = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log)
    need(
        all(name in log for name in required), "Critical component interaction originals are absent"
    )
    files = re.findall(r"Test Files\s+(\d+)\s+passed", log)
    tests = re.findall(r"Tests\s+(\d+)\s+passed", log)
    need(
        len(files) == len(tests) == 1
        and int(files[0]) >= len(required)
        and int(tests[0]) >= registered_minimum,
        "Actual Vitest file/test denominator is missing",
    )
    need(not re.search(r"\d+\s+(?:failed|skipped|todo)", log), "Web cases failed/skipped")
    return {"actual_files": int(files[0]), "actual_tests": int(tests[0]), "required": required}


def frozen_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    registry = proof.json(inputs.get("registry"))
    need(
        registry.get("protocol") == "bounded-funds-frozen-cases-v1"
        and registry.get("purpose") == "FROZEN_MVP",
        "Development designs are not frozen cases",
    )
    frozen_at = aware(registry.get("frozen_at"))
    cases = rows(registry.get("cases"), "frozen cases")
    need(len(cases) >= 24, "Original minimum24 frozen cases is not met")
    forbidden = proof.json(inputs.get("development_registry"))
    development_ids = set(rows(forbidden.get("case_ids"), "all development IDs"))
    development_hashes = set(rows(forbidden.get("input_sha256s"), "all development input hashes"))
    development_semantics = set(
        rows(forbidden.get("semantic_sha256s"), "all development semantic fingerprints")
    )
    need(
        bool(development_ids) and bool(development_hashes) and bool(development_semantics),
        "Known public development cases require a nonempty exclusion registry",
    )
    ids: set[str] = set()
    hashes: set[str] = set()
    semantic_hashes: set[str] = set()
    quota = dict.fromkeys(("N", "G", "C", "L", "V", "T"), 0)
    for case in cases:
        case = obj(case, "frozen case")
        case_id = identity(case.get("case_id"), "case_id")
        family = case.get("family")
        need(
            case_id not in ids and case_id not in development_ids and family in quota,
            "Duplicate, development, or unknown frozen case",
        )
        record, original = proof.original(case.get("input_artifact"))
        _, oracle = proof.original(case.get("oracle_artifact"))
        scenario = obj(strict_json(original), "frozen actual scenario input")
        need(
            scenario.get("protocol") == "bounded-funds-scenario-v1"
            and scenario.get("scenario_id") == case_id
            and bool(rows(scenario.get("steps"), "scenario steps")),
            "Frozen input is not the shared actual scenario protocol",
        )
        semantic_hash = digest(
            json.dumps(
                {
                    key: value
                    for key, value in scenario.items()
                    if key
                    not in {"scenario_id", "case_id", "run_id", "name", "description", "purpose"}
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        need(record["purpose"] == "MVP_ACCEPTANCE", "Development input cannot be relabeled frozen")
        need(
            digest(original) == case.get("input_sha256")
            and digest(oracle) == case.get("oracle_sha256"),
            "Frozen input/oracle original hash mismatch",
        )
        need(
            digest(original) not in hashes | development_hashes,
            "Renamed duplicate/development input cannot become a new frozen case",
        )
        need(
            semantic_hash not in semantic_hashes | development_semantics,
            "Renaming top-level case/run metadata is not a new frozen scenario",
        )
        oracle_value = obj(strict_json(oracle), "independent frozen oracle")
        need(
            oracle_value.get("case_id") == case_id
            and aware(oracle_value.get("registered_at")) <= frozen_at,
            "Independent oracle was not registered for this case before freeze",
        )
        need(
            bool(obj(oracle_value.get("expected"), "independent expected outcomes")),
            "An empty oracle cannot close a frozen case",
        )
        need(aware(case.get("created_at")) <= frozen_at, "Case was created after freeze")
        ids.add(case_id)
        hashes.add(digest(original))
        semantic_hashes.add(semantic_hash)
        quota[cast(str, family)] += 1
    need(
        all(
            quota[key] >= required for key, required in zip(quota, (6, 6, 4, 4, 2, 2), strict=True)
        ),
        "Original 6/6/4/4/2/2 frozen family minima are not met",
    )
    return {"actual_cases": len(ids), "family_counts": quota, "freeze": registry["frozen_at"]}


def native_oracle(proof: Originals, ref: Any) -> dict[str, Any]:
    record, raw = proof.original(ref)
    name = ".runtime/drive_mvp404_browser.py"
    need(
        record["resolved_repository_path"] == name
        and digest((proof.root / name).read_bytes()) == digest(raw)
        and digest(raw) == ORIGINAL_FINANCIAL_ORACLE_SHA256,
        "Only the exact original native independent oracle is admitted",
    )
    # run_name differs from __main__; the trusted module defines pure oracles only.
    return runpy.run_path(str(proof.root / name), run_name="export_native_read_only_oracles")


def native_reset_adapter(proof: Originals, ref: Any, method: Any) -> dict[str, Any]:
    """Use the registered typed decoder while retaining the exact frozen reset assertions."""
    record, raw = proof.original(ref)
    name = "scripts/browser_checkpoint_oracles.py"
    need(
        record["resolved_repository_path"] == name
        and name in proof.files
        and digest(raw) == proof.files[name] == TYPED_RESET_ADAPTER_SHA256
        and digest((proof.root / name).read_bytes()) == digest(raw),
        "Actual registered typed reset adapter source differs from its explicit revision",
    )
    method = obj(method, "actual browser reset methodology revision")
    expected = {
        "revision": "W1_TYPED_COLUMN_NORMALIZATION_V1",
        "original_path": ".runtime/drive_mvp404_browser.py",
        "original_sha256": ORIGINAL_FINANCIAL_ORACLE_SHA256,
        "adapter_path": name,
        "adapter_sha256": TYPED_RESET_ADAPTER_SHA256,
    }
    need(
        all(method.get(key) == value for key, value in expected.items()),
        "Original reset source/adapter/methodology binding is missing or changed",
    )
    sys.path.insert(0, str(proof.root / "apps/api"))
    try:
        return proof.tool(name)
    finally:
        sys.path.pop(0)


def named_round_snapshots(checkpoints: list[Any], scenario: str) -> list[dict[str, Any]]:
    """Bind each durable round to its original named pre-reset BEGIN in one actual case."""
    names = {f"round-{index}-reset-{phase}" for index in (1, 2, 3) for phase in ("before", "after")}
    named = [
        (index, obj(row, "native round checkpoint"))
        for index, row in enumerate(checkpoints)
        if isinstance(row, dict) and str(row.get("label", "")).startswith("round-")
    ]
    need(
        len(named) == 6 and {row.get("label") for _, row in named} == names,
        "Exactly the six original named round reset checkpoints are required",
    )
    need(
        all(row.get("scenario_id") == scenario for _, row in named),
        "Round reset originals mix actual scenario IDs",
    )
    by_name = {row["label"]: (index, row) for index, row in named}
    snapshots = []
    last_after = -1
    for number in (1, 2, 3):
        before_index, before = by_name[f"round-{number}-reset-before"]
        after_index, after = by_name[f"round-{number}-reset-after"]
        need(
            before.get("mode") == "BEGIN"
            and after.get("mode") == "RESET"
            and before_index > last_after
            and after_index == before_index + 1,
            "Original round reset before/after mode or chronology differs",
        )
        snapshots.append(
            obj(
                obj(before.get("result"), "original round BEGIN result").get("snapshot"),
                "original round complete pre-reset snapshot",
            )
        )
        last_after = after_index
    return snapshots


def browser_proof(proof: Originals, inputs: dict[str, Any], requirement: str) -> dict[str, Any]:
    scoped, _ = proof.scoped(inputs.get("scoped"))
    need(
        "scripts/w1_browser_acceptance.py" in " ".join(scoped["command"]),
        "Browser proof lacks actual guarded browser producer command",
    )
    manifest = proof.json(inputs.get("browser_manifest"))
    record, _ = proof.original(inputs.get("browser_manifest"))
    need(manifest.get("run_id") == record["run_id"], "Actual browser run ID mismatch")
    need(
        manifest.get("classification") == "ACTUAL_UI_ACCEPTANCE_NOT_PRODUCT_SLA",
        "Prepared/synthetic browser evidence is not an actual UI run",
    )
    need(not proof.head or manifest.get("commit") == proof.head, "Native browser Git HEAD mismatch")
    need(
        type(manifest.get("playwright_exit_code")) is int and manifest["playwright_exit_code"] == 0,
        "Actual Playwright process did not exit zero",
    )
    need(
        manifest.get("status") in {"PASSED", "PARTIAL_SCOPE_PASSED"},
        "An original failed browser run stays failed",
    )
    before = obj(manifest.get("source_before"), "browser original source before")
    after = obj(manifest.get("source_after"), "browser original source after")
    need(before == after, "Actual browser source drifted")
    for name, expected in before.items():
        path = relative_path(name)
        actual = (proof.root / path).resolve()
        need(
            actual.is_relative_to(proof.root) and digest(actual.read_bytes()) == sha(expected),
            "Actual browser source differs from current source",
        )
    base = Path(record["resolved_repository_path"]).parent.as_posix()
    artifact_hashes = obj(manifest.get("artifact_hashes"), "all native browser artifact hashes")
    need(bool(artifact_hashes), "Empty actual browser original index")
    for relative, expected in artifact_hashes.items():
        original, raw = proof.by_path(base + "/" + relative)
        need(
            original["run_id"] == record["run_id"] and digest(raw) == sha(expected),
            "Browser screenshot/video/checkpoint/HTTP originals mix runs or changed",
        )
    result = proof.json(inputs.get("playwright_results"))
    specs: list[dict[str, Any]] = []

    def visit(suites: Any) -> None:
        for suite in rows(suites, "Playwright suites"):
            suite = obj(suite, "Playwright suite")
            specs.extend(rows(suite.get("specs", []), "actual Playwright specs"))
            visit(suite.get("suites", []))

    visit(result.get("suites"))
    need(not result.get("errors") and bool(specs), "Playwright originals contain global errors")
    for spec in specs:
        tests = rows(spec.get("tests"), "actual Playwright tests")
        need(len(tests) == 1, "Browser test repeated or missing")
        attempts = rows(tests[0].get("results"), "actual browser attempts")
        need(
            len(attempts) == 1
            and attempts[0].get("status") == "passed"
            and float(attempts[0].get("duration", 0)) > 0,
            "Failed/skipped/retried/unexecuted browser case cannot close acceptance",
        )
    titles = [spec["title"] for spec in specs]
    need(len(set(titles)) == len(titles), "Repeated browser titles cannot inflate chains")
    source = (proof.root / "apps/web/tests/e2e/w1-demo.spec.ts").read_text(encoding="utf-8")
    original_titles = re.findall(r"test\('([^']+)'", source)
    need(len(original_titles) == 7, "Known six-chain/three-round native source contract changed")
    wanted = original_titles[:6] if requirement == "six_business_e2e" else original_titles[6:]
    need(set(wanted).issubset(titles), "Original named six chains/three rounds are missing")
    oracle = native_oracle(proof, inputs.get("oracle_source"))
    reset_adapter = native_reset_adapter(
        proof, inputs.get("reset_adapter_source"), manifest.get("reset_oracle_method")
    )
    baseline = proof.snapshot(manifest.get("baseline"))
    previous: dict[str, Any] | None = None
    resets = 0
    results = []
    checkpoints = rows(manifest.get("checkpoints"), "actual browser checkpoints")
    need(bool(checkpoints), "No actual bank checkpoints")
    for checkpoint in checkpoints:
        checkpoint = obj(checkpoint, "native checkpoint")
        current = proof.snapshot(obj(checkpoint.get("result"), "checkpoint result").get("snapshot"))
        mode = checkpoint.get("mode")
        need(mode in {"BEGIN", "MONEY", "READ_ONLY", "RESET"}, "Unknown native checkpoint mode")
        if mode != "BEGIN":
            need(previous is not None, "Checkpoint lacks preceding original")
            if mode == "READ_ONLY":
                need(previous == current, "Original GET/replay changed physical business rows")
                results.append({"read_only_equal": True})
            elif mode == "RESET":
                # Imports model definitions only; this trusted pure function never connects DB.
                sys.path.insert(0, str(proof.root / "apps/api"))
                try:
                    results.append(reset_adapter["reset_oracle"](previous, current, baseline))
                finally:
                    sys.path.pop(0)
                resets += 1
            else:
                results.append(oracle["money_oracle"](previous, current))
        previous = current
    if requirement == "three_demo_rounds":
        round_spec = next(spec for spec in specs if spec["title"] == original_titles[6])
        identifier = round_spec.get("id")
        need(
            isinstance(identifier, str) and bool(identifier),
            "Actual Playwright round test ID missing",
        )
        clean_id = re.sub(r"[^a-z0-9]", "", cast(str, identifier), flags=re.I)[:64]
        need(bool(clean_id), "Actual round test ID has no scenario identity")
        scenario = "w1-" + clean_id
        expected_rounds = named_round_snapshots(checkpoints, scenario)
        need(
            resets == len(specs) + 3,
            "Actual reset total must retain every case initialization plus three round resets",
        )
        round_refs = rows(inputs.get("round_snapshots"), "actual complete-round original refs")
        need(
            round_refs == expected_rounds,
            "Three durable round refs must be the unchanged named reset-before BEGIN originals",
        )
        round_results = [
            oracle["round_oracle"](proof.snapshot(metadata)) for metadata in round_refs
        ]
        need(
            len({row["epoch_id"] for row in round_results}) == 3,
            "Repeated epoch is not three actual rounds",
        )
        results.extend(round_results)
    report = {
        "titles": wanted,
        "checkpoint_count": len(checkpoints),
        "reset_count": resets,
        "recomputed_oracles": results,
    }
    if requirement == "three_demo_rounds":
        report.update(
            total_reset_count=resets,
            round_reset_count=3,
            round_scenario_id=scenario,
            round_snapshots_origin="ORIGINAL_ROUND_N_RESET_BEFORE_BEGIN",
        )
    return report


def audit_modules(proof: Originals) -> dict[str, Any]:
    """Load only current registered original algorithms/models; never open a session."""
    names = {
        "domain": "app.domain.audit_chain",
        "types": "app.domain.audit_chain_types",
        "models": "app.db.models",
        "service": "app.services.audit_chain",
    }
    api = str(proof.root / "apps/api")
    sys.path.insert(0, api)
    try:
        loaded = {}
        for key, module_name in names.items():
            path = "apps/api/" + module_name.replace(".", "/") + ".py"
            need(
                path in proof.files
                and digest((proof.root / path).read_bytes()) == proof.files[path],
                "Original audit algorithm/model is outside current source binding",
            )
            module = importlib.import_module(module_name)
            need(
                isinstance(module.__file__, str)
                and Path(module.__file__).resolve() == (proof.root / path).resolve(),
                "Original audit module resolved outside current repository",
            )
            loaded[key] = module
        loaded["normalizer"] = proof.tool("scripts/browser_checkpoint_oracles.py")[
            "normalized_original"
        ]
        need(
            set(loaded["models"].Base.metadata.tables) == NATIVE_BUSINESS_TABLES,
            "Known physical23 audit bridge/model registry changed",
        )
        return loaded
    finally:
        sys.path.remove(api)


def audit_snapshot_bridge(data: dict[str, Any], modules: dict[str, Any]) -> dict[str, Any]:
    """Recompute all registered epochs and typed originals with the product's exact domain."""
    domain, types, models, service = (
        modules[name] for name in ("domain", "types", "models", "service")
    )
    normalize = modules["normalizer"]

    def original_head(row: dict[str, Any]) -> Any:
        # The original service expects native mapped UUID attributes. Restore only
        # UUID columns identified by its actual SQLAlchemy model, never JSONB values.
        typed = dict(row)
        for column in models.AuditEpoch.__table__.columns:
            if column.type.python_type is UUID and typed[column.name] is not None:
                typed[column.name] = UUID(typed[column.name])
        return service._head(models.AuditEpoch(**typed))

    need(
        set(data) == NATIVE_BUSINESS_TABLES | {"alembic_version"},
        "Audit physical24 originals missing",
    )
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for table in models.Base.metadata.sorted_tables:
        table_rows = rows(data[table.name], "complete native audit table")
        normalized = [normalize(table, obj(row, "original mapped audit row")) for row in table_rows]
        need(
            len({row["id"] for row in normalized}) == len(normalized),
            "Duplicate original physical row identity",
        )
        indexed[table.name] = {row["id"]: row for row in normalized}
    epochs = indexed["audit_epochs"]
    need(bool(epochs), "No registered audit epochs; legacy history is never fabricated VALID")
    epoch_pairs = {(row["user_id"], row["id"]) for row in epochs.values()}
    for table_name in ("audit_events", "audit_subject_snapshots"):
        need(
            all(
                (row["user_id"], row["epoch_id"]) in epoch_pairs
                for row in indexed[table_name].values()
            ),
            "Legacy/orphan audit originals cannot be silently omitted",
        )
    results = []
    for row in sorted(epochs.values(), key=lambda item: (item["user_id"], item["epoch_number"])):
        epoch_id, user_id = row["id"], row["user_id"]
        head = original_head(row)
        captured = [
            s for s in indexed["audit_subject_snapshots"].values() if s["epoch_id"] == epoch_id
        ]
        need(
            len(captured) <= service.VERIFY_SUBJECT_LIMIT,
            "Original service subject budget exceeded",
        )
        saved_events = sorted(
            (event for event in indexed["audit_events"].values() if event["epoch_id"] == epoch_id),
            key=lambda event: event["sequence_number"],
        )
        need(
            len(saved_events) <= service.VERIFY_EVENT_LIMIT,
            "Original service event budget exceeded",
        )
        need(
            sum(len(s["canonical_text"].encode("utf-8")) for s in captured + saved_events)
            <= service.VERIFY_BYTE_LIMIT,
            "Original service total audit byte budget exceeded",
        )
        subjects, current, diagnostics = [], [], []
        current_keys: set[tuple[str, Any]] = set()
        for saved in captured:
            subject = domain.parse_subject(saved["canonical_text"])
            need(
                domain.subject_hash(subject) == saved["snapshot_hash"]
                and str(subject.user_id) == user_id
                and str(subject.epoch_id) == epoch_id
                and str(subject.id) == saved["entity_id"]
                and subject.kind == saved["kind"]
                and subject.scope == saved["scope"]
                and subject.snapshot_version == saved["snapshot_version"],
                "Permanent audit subject index differs from original canonical bytes",
            )
            subjects.append(subject)
            key = (subject.kind, subject.id)
            if head.status == "OPEN" and key not in current_keys:
                current_keys.add(key)
                model = service.SUBJECT_MODELS.get(subject.kind)
                need(model is not None, "Unknown original subject model")
                actual = indexed[model.__tablename__].get(str(subject.id))
                if actual is not None:
                    current.append(
                        domain.build_subject(
                            user_id=head.user_id,
                            epoch_id=head.epoch_id,
                            kind=subject.kind,
                            id=subject.id,
                            scope=subject.scope,
                            snapshot_version=service._snapshot_version(subject.kind, actual),
                            data=actual,
                        )
                    )
                elif subject.kind in {
                    "DECISION_RUN",
                    "ACTION_PLAN",
                    "ACTION_RECEIPT",
                    "POLICY_VERSION",
                    "EVIDENCE",
                    "BANK_OPERATION",
                    "BANK_REDEMPTION",
                    "BANK_POSTING",
                    "BANK_EXTERNAL_FACT",
                    "ASSET_PRODUCT",
                }:
                    diagnostics.append(
                        types.AuditDiagnostic(
                            code="CURRENT_ORIGINAL_MISSING",
                            reference=f"{subject.kind}:{subject.id}",
                            message="当前审计轮次已捕获的不可变原件不存在",
                        )
                    )
        events = []
        for saved in saved_events:
            event = domain.parse_event(saved["canonical_text"])
            projected = json.loads(
                domain.canonical_text(
                    event.model_dump(mode="python", exclude={"simulation", "appended_at"}), raw=True
                )
            )
            need(
                all(saved[field] == value for field, value in projected.items()),
                "Audit event column projection changed",
            )
            expected_clock = json.loads(
                domain.canonical_text({"created_at": event.appended_at}, raw=True)
            )["created_at"]
            need(
                saved["created_at"] == expected_clock,
                "Audit append clock differs from canonical original",
            )
            events.append(event)
        previous_seal = None
        if head.previous_epoch_id is not None:
            previous = epochs.get(str(head.previous_epoch_id))
            need(
                previous is not None
                and previous["user_id"] == user_id
                and previous["status"] == "SEALED"
                and previous["epoch_number"] + 1 == head.epoch_number
                and previous["seal_hash"] == head.previous_seal_hash,
                "Original predecessor epoch/seal/order changed",
            )
            previous = cast(dict[str, Any], previous)
            previous_seal = domain.parse_seal(previous["seal_canonical_text"])
            need(
                previous_seal.head == original_head(previous), "Prior seal/head projection changed"
            )
        if head.status == "SEALED":
            seal = domain.parse_seal(row["seal_canonical_text"])
            domain.verify_seal(seal, previous_seal)
            need(
                seal.head == head and seal.seal_hash == row["seal_hash"],
                "Original seal differs from independent stored head",
            )
            entries = sorted(
                (
                    {"kind": s["kind"], "id": s["entity_id"], "snapshot_hash": s["snapshot_hash"]}
                    for s in captured
                ),
                key=lambda s: (s["kind"], s["id"], s["snapshot_hash"]),
            )
            counts: dict[str, int] = {}
            for entry in entries:
                counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
            archive = {"entries": entries, "counts": counts}
            # Exact original SQL ordering, namespace and dedicated original encoder.
            archive_hash = digest(
                b"bounded-funds/audit-archive-v1\0" + domain.archive_manifest_bytes(archive)
            )
            need(
                archive_hash == row["archive_manifest_hash"]
                and counts == row["archive_record_counts"],
                "Complete original sealed archive index changed",
            )
        verified = domain.verify_epoch(
            events,
            head=head,
            expected_user_id=UUID(user_id),
            references=types.ReferenceBundle(
                subjects=subjects, current_subjects=current, original_errors=diagnostics
            ),
        )
        need(
            verified.status == verified.chain_status == verified.reference_status == "VALID"
            and not verified.errors
            and not verified.errors_truncated,
            "Original domain/full-reference audit recheck failed: " + verified.model_dump_json(),
        )
        results.append(verified.model_dump(mode="json"))
    return {
        "method": "ORIGINAL_DOMAIN_AND_TYPED_PHYSICAL_ORIGINALS",
        "epoch_count": len(results),
        "verifications": results,
        "migration_rows_origin": "ORIGINAL_MANIFEST_METADATA_HEADS_OR_RAW_TABLE",
    }


def audit_bridge(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    scoped, _ = proof.scoped(inputs.get("scoped"))
    if "observer" in inputs:
        return audit_observer_proof(proof, inputs, scoped)
    result = audit_snapshot_bridge(proof.snapshot(inputs.get("snapshot")), audit_modules(proof))
    raise PartialProof(
        result,
        [
            "Actual current-source read-only observer must call original verify_audit_chain "
            "for every epoch in the same snapshot",
            "OPEN original decision/current-reference/economic-receipt service gates "
            "are not independently executed by this pure bridge",
        ],
    )


def audit_observer_proof(
    proof: Originals, inputs: dict[str, Any], scoped: dict[str, Any]
) -> dict[str, Any]:
    """Combine independently replayed originals with the actual trusted service observer."""
    manifest = proof.json(inputs.get("observer"))
    record, _ = proof.original(inputs.get("observer"))
    producer = "scripts/w1_audit_observe.py"
    need(
        producer in proof.files and (proof.root / producer).is_file(),
        "Actual audit observer is not registered",
    )
    need(
        manifest.get("protocol") == "bounded-funds-audit-observation-v1"
        and manifest.get("producer_path") == producer
        and manifest.get("producer_sha256")
        == manifest.get("producer_after_sha256")
        == proof.files[producer]
        and digest((proof.root / producer).read_bytes()) == proof.files[producer],
        "Actual service observer source differs",
    )
    need(
        producer in " ".join(scoped["command"]) and "--run" in scoped["command"],
        "Prepared audit observer did not actually run",
    )
    need(
        manifest.get("run_id") == record["run_id"]
        and manifest.get("status") == "PASSED"
        and manifest.get("formal_database_touched") is False,
        "Original audit observation failed or run binding differs",
    )
    need(
        aware(manifest.get("started_at")) <= aware(manifest.get("finished_at")),
        "Actual audit observation clock differs",
    )
    before = obj(manifest.get("source_before"), "actual observer source before")
    need(bool(before) and before == manifest.get("source_after"), "Actual observer source drifted")
    normalized = {name.replace("\\", "/"): checksum for name, checksum in before.items()}
    required = {name for name in proof.files if name.startswith("apps/api/app/")}
    need(required.issubset(normalized), "Actual observer omitted financial API source binding")
    for path, checksum in normalized.items():
        candidate = (proof.root / relative_path(path)).resolve()
        need(
            candidate.is_relative_to(proof.root)
            and digest(candidate.read_bytes()) == sha(checksum),
            "Observed source is no longer current",
        )
    base = Path(record["resolved_repository_path"]).parent.as_posix()
    indexed = obj(manifest.get("artifact_hashes"), "all audit observer originals")
    for path, checksum in indexed.items():
        item, raw = proof.by_path(base + "/" + relative_path(path).as_posix())
        need(
            item["run_id"] == record["run_id"] and digest(raw) == sha(checksum),
            "Audit observer originals changed/mix runs",
        )
    initial_name = "owner-manifest-initial.json"
    if initial_name not in indexed:
        raise MissingProof(
            "Actual initial owner manifest bytes were not captured; "
            "old observation stays diagnostic"
        )
    need(
        relative_path(manifest.get("owner_manifest_initial_copy")).as_posix()
        == base + "/" + initial_name,
        "Original initial owner copy path differs from native artifact index",
    )
    _, owner_raw = proof.by_path(base + "/" + initial_name)
    owner = obj(strict_json(owner_raw), "actual initial owned browser manifest")
    need(
        digest(owner_raw) == manifest.get("owner_manifest_initial_sha256")
        and owner.get("run_id") == manifest.get("owner_run_id")
        and owner.get("status") == "INCOMPLETE"
        and owner.get("temporary_database_exited") is False
        and Path(relative_path(manifest.get("owner_manifest_path"))).parent.name == owner["run_id"],
        "Actual in-progress owner bytes differ",
    )
    database = manifest.get("database")
    need(
        isinstance(database, str)
        and re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None
        and owner.get("database") == database
        and manifest.get("client_target")
        == {"host": "127.0.0.1", "port": 54329, "database": database},
        "Actual audit observer escaped owned local test database",
    )
    transaction = obj(manifest.get("actual_transaction"), "actual same-snapshot SQL transaction")
    query = (
        "SELECT current_database() AS database, inet_server_addr()::text AS server_address, "
        "inet_server_port() AS server_port, current_user AS database_user"
    )
    need(
        transaction.get("isolation_query") == "SHOW transaction_isolation"
        and transaction.get("isolation") == "repeatable read"
        and transaction.get("readonly_query") == "SHOW transaction_read_only"
        and transaction.get("readonly") == "on"
        and transaction.get("identity_query") == query,
        "Actual observer transaction is not native read-only RR",
    )
    identities = rows(transaction.get("identity_rows"), "actual observer SQL identity rows")
    need(
        len(identities) == 1
        and obj(identities[0], "actual SQL identity").get("database") == database
        and type(identities[0].get("server_port")) is int
        and bool(identities[0].get("server_address"))
        and bool(identities[0].get("database_user")),
        "Actual observation SQL connected elsewhere",
    )
    metadata = obj(manifest.get("snapshot"), "actual service observer snapshot metadata")
    data = proof.snapshot(metadata)
    need(
        metadata.get("physical_query")
        == "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        and manifest.get("snapshot_after_data_sha256") == metadata.get("data_sha256"),
        "Same read-only snapshot changed or physical registry was not observed",
    )
    need(
        bool(data["users"]) and all(row.get("is_simulated") is True for row in data["users"]),
        "Actual observation includes non-simulated/no users",
    )
    user_ids = {row["id"] for row in data["users"]}
    epoch_users = {row["user_id"] for row in data["audit_epochs"]}
    need(user_ids == epoch_users, "Actual complete observer skipped a legacy/no-epoch user")
    replayed = audit_snapshot_bridge(data, audit_modules(proof))
    observations = rows(
        manifest.get("observations"), "all actual original service epoch observations"
    )
    epoch_map = {(row["user_id"], row["id"]): row for row in data["audit_epochs"]}
    seen: set[tuple[str, str]] = set()
    expected = {(row["user_id"], row["epoch_id"]): row for row in replayed["verifications"]}
    for observation in observations:
        observation = obj(observation, "actual original service observation")
        key = (observation.get("user_id"), observation.get("epoch_id"))
        need(
            key in epoch_map and key not in seen,
            "Actual service observer omitted/duplicated/mixed epochs",
        )
        key = cast(tuple[str, str], key)
        seen.add(key)
        epoch = epoch_map[key]
        need(
            observation.get("epoch_number") == epoch["epoch_number"]
            and observation.get("epoch_status") == epoch["status"]
            and observation.get("verification") == expected[key],
            "Original full service result differs from independent full-reference replay",
        )
    need(seen == set(epoch_map), "Actual service result did not cover every registered epoch")
    return {
        **replayed,
        "method": "ORIGINAL_DOMAIN_PLUS_ACTUAL_READ_ONLY_SERVICE_OBSERVER",
        "actual_service_producer_sha256": proof.files[producer],
        "owner_run_id": owner["run_id"],
        "actual_server_identity": identities[0],
        "original_full_service_gate_count": len(observations),
    }


def png_structure(raw: bytes) -> dict[str, int]:
    """Validate actual supported PNG bytes; no visual or capture-origin claim."""
    need(raw.startswith(b"\x89PNG\r\n\x1a\n"), "Original screenshot is not PNG")
    offset, compressed, chunks = 8, bytearray(), []
    width = height = channels = 0
    while offset < len(raw):
        need(offset + 12 <= len(raw), "Truncated PNG chunk")
        size = struct.unpack(">I", raw[offset : offset + 4])[0]
        need(
            size <= 64 * 1024 * 1024 and offset + size + 12 <= len(raw),
            "PNG chunk exceeds original bytes",
        )
        kind = raw[offset + 4 : offset + 8]
        value = raw[offset + 8 : offset + 8 + size]
        crc = struct.unpack(">I", raw[offset + 8 + size : offset + 12 + size])[0]
        need(zlib.crc32(kind + value) & 0xFFFFFFFF == crc, "Original PNG CRC changed")
        chunks.append(kind)
        if kind == b"IHDR":
            need(len(chunks) == 1 and size == 13, "PNG header order/length differs")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", value
            )
            need(
                0 < width <= 20000
                and 0 < height <= 20000
                and depth == 8
                and color in {2, 6}
                and compression == filtering == interlace == 0,
                "Unsupported screenshot PNG structure",
            )
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            compressed.extend(value)
        elif kind == b"IEND":
            need(size == 0 and offset + 12 == len(raw), "PNG trailer/trailing bytes differ")
        offset += size + 12
    need(bool(chunks) and chunks[-1] == b"IEND" and bool(compressed), "PNG data/trailer missing")
    expected = height * (1 + width * channels)
    need(0 < expected <= 64 * 1024 * 1024, "PNG screenshot decode budget exceeded")
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(bytes(compressed), expected + 1)
    need(
        decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail
        and len(decoded) == expected,
        "PNG image scanlines are truncated, excessive or corrupted",
    )
    need(
        all(decoded[index] <= 4 for index in range(0, expected, 1 + width * channels)),
        "PNG scanline filter unsupported",
    )
    return {"width": width, "height": height, "decoded_scanline_bytes": len(decoded)}


def trace_capture_bridge(raw: bytes) -> dict[str, Any]:
    """Inspect bounded original Playwright v8 trace resources without extracting files."""
    need(len(raw) <= 64 * 1024 * 1024, "Native trace archive exceeds byte budget")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            need(
                len(entries) <= 3000 and len(set(names)) == len(names),
                "Trace duplicate/member budget exceeded",
            )
            need(
                sum(entry.file_size for entry in entries) <= 128 * 1024 * 1024,
                "Trace decoded budget exceeded",
            )
            for entry in entries:
                relative_path(entry.filename)
                need(
                    not entry.is_dir() and not entry.flag_bits & 1,
                    "Encrypted/directory trace member unsupported",
                )
            need(
                "test.trace" in names and "0-trace.trace" in names,
                "Original native session traces missing",
            )

            def events(name: str) -> list[dict[str, Any]]:
                value = archive.read(name)
                need(len(value) <= 32 * 1024 * 1024, "Trace JSONL budget exceeded")
                return [
                    obj(strict_json(line), "native trace event")
                    for line in value.splitlines()
                    if line.strip()
                ]

            runner, browser = events("test.trace"), events("0-trace.trace")
            contexts = [event for event in browser if event.get("type") == "context-options"]
            need(len(contexts) == 1, "Native browser trace context is missing or repeated")
            context = contexts[0]
            need(
                context.get("version") == 8
                and context.get("origin") == "library"
                and context.get("browserName") == "chromium"
                and context.get("channel") == "msedge",
                "Unknown/synthetic browser trace protocol/channel",
            )
            options = obj(context.get("options"), "actual browser options")
            need(
                isinstance(options.get("recordVideo"), dict)
                and bool(context.get("contextId"))
                and isinstance(options.get("userAgent"), str)
                and "Edg/" in options["userAgent"],
                "Original Edge context/video recording was not configured",
            )
            starts = [
                event
                for event in browser
                if event.get("type") == "before" and event.get("method") == "screenshot"
            ]
            need(bool(starts), "No actual screenshot call in original trace")
            endings = {
                event.get("callId"): event for event in browser if event.get("type") == "after"
            }
            for start in starts:
                end = endings.get(start.get("callId"))
                need(
                    end is not None
                    and not end.get("error")
                    and end.get("endTime", 0) >= start.get("startTime", 0)
                    and obj(start.get("params"), "native screenshot params").get("fullPage")
                    is True,
                    "Native screenshot did not complete a full-page capture",
                )
            videos = [
                attachment
                for event in runner
                for attachment in event.get("attachments", [])
                if attachment.get("name") == "video"
                and attachment.get("contentType") == "video/webm"
            ]
            need(len(videos) == 1, "One original complete-session video attachment is required")
            checksum = videos[0].get("sha1")
            need(
                isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{40}", checksum) is not None,
                "Native video resource identity is missing",
            )
            resource = "resources/" + checksum
            need(resource in names, "Original session video resource is missing")
            video = archive.read(resource)
            need(
                len(video) > 1024
                and video.startswith(b"\x1a\x45\xdf\xa3")
                and hashlib.sha1(video, usedforsecurity=False).hexdigest() == checksum,
                "Original WebM resource bytes differ from native Playwright attachment",
            )
            return {
                "context": context,
                "screenshot_calls": len(starts),
                "video_sha256": digest(video),
                "video_size_bytes": len(video),
                "video_native_sha1": checksum,
                "trace_member_count": len(entries),
            }
    except BadZipFile as error:
        raise ValueError("Corrupt native Playwright trace archive") from error


def media_bridge(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    proof.scoped(inputs.get("scoped"))
    manifest = proof.json(inputs.get("browser_manifest"))
    record, _ = proof.original(inputs.get("browser_manifest"))
    need(
        manifest.get("run_id") == record["run_id"]
        and manifest.get("classification") == "ACTUAL_UI_ACCEPTANCE_NOT_PRODUCT_SLA",
        "Native capture producer/run binding differs",
    )
    need(
        manifest.get("source_before") == manifest.get("source_after"),
        "Actual capture source drifted",
    )
    proof.current_subset(
        manifest.get("source_before"),
        ("apps/api/app/", "apps/web/src/", "apps/web/tests/", "packages/contracts/"),
    )
    base = Path(record["resolved_repository_path"]).parent.as_posix()
    originals = {}
    for path, checksum in obj(
        manifest.get("artifact_hashes"), "native capture original index"
    ).items():
        item, raw = proof.by_path(base + "/" + path)
        need(
            item["run_id"] == record["run_id"] and digest(raw) == sha(checksum),
            "Native capture mixes runs/changed bytes",
        )
        originals[path] = raw
    sessions = []
    for path, raw in originals.items():
        if not path.endswith("/actual-browser.json"):
            continue
        folder = path.rsplit("/", 1)[0]
        browser = obj(strict_json(raw), "actual browser identity")
        need(
            browser.get("run_id") == record["run_id"]
            and browser.get("requested_channel") == "msedge"
            and bool(browser.get("actual_engine_version"))
            and bool(browser.get("scenario_id")),
            "Actual browser/session identity differs",
        )
        trace_raw = originals.get(folder + "/trace.zip")
        need(trace_raw is not None, "Original session trace is missing")
        capture = trace_capture_bridge(cast(bytes, trace_raw))
        need(
            capture["context"]["options"]["userAgent"] == browser.get("actual_user_agent"),
            "Trace/browser user agent differs",
        )
        videos = [
            data
            for name, data in originals.items()
            if name.startswith(folder + "/") and name.endswith(".webm")
        ]
        need(
            len(videos) == 1 and digest(videos[0]) == capture["video_sha256"],
            "Session video and native trace resource differ",
        )
        screenshots = []
        for name, image in originals.items():
            if name.startswith(folder + "/") and name.endswith(".native.png"):
                text = originals.get(name[:-4] + ".txt")
                need(
                    text is not None and bool(text.decode("utf-8").strip()),
                    "Screenshot lacks original body text capture",
                )
                screenshots.append({"path": name, "sha256": digest(image), **png_structure(image)})
        need(
            bool(screenshots) and len(screenshots) <= capture["screenshot_calls"],
            "Capture screenshots differ from actual calls",
        )
        http_count = 0
        for name, payload in originals.items():
            if name.startswith(folder + "/native-http/") and name.endswith(
                ".request-response.json"
            ):
                http = obj(strict_json(payload), "actual native HTTP capture")
                need(
                    type(http.get("status")) is int
                    and 100 <= http["status"] <= 599
                    and isinstance(http.get("method"), str)
                    and isinstance(http.get("url"), str)
                    and "/api/v1/" in http["url"],
                    "Original HTTP response metadata malformed",
                )
                body_path = (
                    name.rsplit("/", 1)[0] + "/" + relative_path(http.get("body_file")).as_posix()
                )
                need(body_path in originals, "Actual captured HTTP body is missing")
                aware(http.get("finished_at"))
                http_count += 1
        need(http_count > 0, "No actual session native HTTP originals")
        sessions.append(
            {
                "scenario_id": browser["scenario_id"],
                "original_status": manifest.get("status"),
                "screenshots": screenshots,
                "http_capture_count": http_count,
                "trace": capture,
            }
        )
    need(bool(sessions), "No actual capture sessions")
    raise PartialProof(
        {"method": "NATIVE_TRACE_HTTP_PNG_SESSION_BYTES", "sessions": sessions},
        [
            "All original required screenshot keypoints and successful full-session "
            "coverage are not yet verified",
            "WebM bytes are native trace-bound resources; decoded frames/completeness/"
            "content review remain unverified",
            "A failed original browser run remains failed and cannot satisfy acceptance",
        ],
    )


def financial_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    proof.scoped(inputs.get("scoped"))
    oracle = native_oracle(proof, inputs.get("oracle_source"))
    pairs = rows(inputs.get("snapshot_pairs"), "original financial snapshot pairs")
    need(bool(pairs), "No actual financial chain pairs")
    results = []
    for pair in pairs:
        pair = obj(pair, "actual snapshot pair")
        before, after = proof.snapshot(pair.get("before")), proof.snapshot(pair.get("after"))
        need(bool(after.get("simulated_bank_postings")), "An empty ledger is not financial proof")
        results.append(oracle["money_oracle"](before, after))
    return {
        "pairs": len(pairs),
        "recomputed_integer_ledger_oracles": results,
        "boundary": "Bank/application states stay distinct; UNKNOWN does not imply zero cash",
    }


def formal_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    before = proof.json(inputs.get("before_result"), historical=True)
    after = proof.json(inputs.get("after_result"))
    need(
        before.get("database") == after.get("database") == "bounded_funds"
        and before.get("phase") == "before"
        and after.get("phase") == "after",
        "Not original formal before/after preservation captures",
    )
    need(
        before.get("status") == after.get("status") == "PASSED",
        "An original failed preservation capture stays failed",
    )
    need(
        after.get("tool_sha256") == proof.files["scripts/verify_formal_preservation.py"],
        "Actual preservation producer source mismatch",
    )
    _, baseline_raw = proof.original(inputs.get("formal_baseline"), historical=True)
    baseline = obj(strict_json(baseline_raw), "original W0 formal baseline")
    need(
        digest(baseline_raw) == before.get("baseline_sha256") == after.get("baseline_sha256"),
        "Formal snapshots are not anchored to the original W0 baseline bytes",
    )
    need(
        count(baseline.get("total_rows"), "W0 total rows") == 408
        and len(obj(baseline.get("tables"), "W0 all formal tables")) == 23,
        "Original W0 formal inventory is not the registered23/408 baseline",
    )
    decoded = []
    for phase, metadata in (("before", before), ("after", after)):
        record, raw = proof.original(inputs.get(phase + "_snapshot"), historical=phase == "before")
        payload = bounded_snapshot_bytes(raw)
        data = obj(strict_json(payload), "full formal physical snapshot")
        expected = obj(metadata.get("snapshot"), "native preservation snapshot summary")
        need(
            digest(payload) == expected.get("sha256")
            and digest(payload) == baseline.get("sha256")
            and len(data) == 23
            and set(data) == set(baseline["tables"])
            and expected.get("tables") == baseline["tables"]
            and sum(len(rows(value, "formal table")) for value in data.values()) == 408,
            "Original complete23/408 formal snapshot/hash differs",
        )
        need(
            Path(record["resolved_repository_path"]).name == expected.get("raw_snapshot"),
            "Formal snapshot is not the named original",
        )
        decoded.append(data)
    need(decoded[0] == decoded[1], "Original formal business history changed")
    need(
        all(
            not decoded[1][name]
            for name in ("audit_epochs", "audit_events", "audit_subject_snapshots")
        ),
        "Legacy formal history was altered or given a new genesis",
    )
    index_record, index_raw = proof.original(inputs.get("w0_index"), historical=True)
    index = obj(strict_json(index_raw), "original W0 evidence index")
    need(digest(index_raw) == after.get("w0_index_sha256"), "Actual W0 index binding differs")
    base = Path(index_record["resolved_repository_path"]).parent.as_posix()
    entries = obj(index.get("files"), "every original W0 file")
    need(bool(entries), "Empty W0 evidence index is not preservation")
    for name, expected in entries.items():
        _, original = proof.by_path(base + "/" + name, historical=True)
        need(digest(original) == sha(expected), "W0 original evidence/failure changed")
    return {
        "formal_rows": 408,
        "formal_tables": 23,
        "w0_files_rehashed": len(entries),
        "original_audit_state": "LEGACY_UNAUDITED",
        "after_run_id": after.get("run_id"),
    }


def seed_server_endpoint(proof: Originals, binding: Any, owner_run: str) -> tuple[str, int]:
    """Keep actual server SQL identity distinct from the guarded client DSN."""
    binding = obj(binding, "actual seed server binding")
    mode = binding.get("mode")
    if mode == "NAMESPACE_LOOPBACK_V1":
        return "127.0.0.1", 54329
    need(mode == "HOST_PUBLISHED_DOCKER_V1", "Unknown actual server/client endpoint binding")
    record, raw = proof.original(binding.get("docker_inspect"))
    need(record["run_id"] == owner_run, "Docker server identity mixes database owner runs")
    inspected = rows(strict_json(raw), "actual docker inspect stdout")
    need(len(inspected) == 1, "Exactly one actual Docker database server is required")
    container = obj(inspected[0], "actual Docker database server")
    identity = container.get("Id")
    need(
        isinstance(identity, str)
        and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
        and identity == binding.get("container_id")
        and container.get("Name") == "/bounded-funds-db-1"
        and obj(container.get("State"), "actual container state").get("Running") is True,
        "Actual published database server container identity/state changed",
    )
    labels = obj(
        obj(container.get("Config"), "actual Docker config").get("Labels"),
        "actual ownership labels",
    )
    need(
        labels.get("com.docker.compose.project") == "bounded-funds"
        and labels.get("com.docker.compose.service") == "db",
        "Actual server ownership labels differ",
    )
    network = obj(container.get("NetworkSettings"), "actual Docker network settings")
    ports = obj(network.get("Ports"), "actual published server ports")
    need(
        ports.get("5432/tcp") == [{"HostIp": "127.0.0.1", "HostPort": "54329"}],
        "Actual loopback54329 to container5432 publication is missing or exposed",
    )
    endpoints = obj(network.get("Networks"), "actual Docker networks")
    addresses = {
        obj(value, "actual network endpoint").get("IPAddress") for value in endpoints.values()
    }
    addresses.discard("")
    need(
        len(addresses) == 1
        and all(
            isinstance(address, str) and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", address)
            for address in addresses
        ),
        "Actual server network address is ambiguous or missing",
    )
    return cast(str, next(iter(addresses))), 5432


def seed_isolation_proof(
    proof: Originals, ref: Any, task_run_id: str, seed_started_at: datetime
) -> dict[str, Any]:
    """Admit a trusted coordinator's actual SQL identity originals, never a task flag."""
    isolation = proof.json(ref)
    record, _ = proof.original(ref)
    producer = "scripts/w1_final_acceptance.py"
    if producer not in proof.files or not (proof.root / producer).is_file():
        raise MissingProof("Actual final isolation coordinator is not implemented/registered")
    need(
        isolation.get("protocol") == "bounded-funds-final-isolation-v1"
        and isolation.get("producer_path") == producer
        and isolation.get("producer_sha256") == proof.files[producer],
        "Seed isolation is not the current native coordinator's original",
    )
    database = isolation.get("database")
    need(
        isinstance(database, str) and re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None,
        "Seed target is not an owned fresh test database; formal/default database refused",
    )
    need(
        isolation.get("seed_task_run_id") == task_run_id
        and isolation.get("owner_run_id") == record["run_id"],
        "Seed task and actual database owner originals are not bound",
    )
    engine = obj(isolation.get("engine_target"), "actual inherited seed engine target")
    need(
        engine == {"host": "127.0.0.1", "port": 54329, "database": database},
        "Seed inherited engine target does not satisfy real local isolation guard",
    )
    server_address, server_port = seed_server_endpoint(
        proof, isolation.get("server_binding"), record["run_id"]
    )
    query = (
        "SELECT current_database() AS database, inet_server_addr()::text AS server_address, "
        "inet_server_port() AS server_port, current_user AS database_user"
    )
    observed = []
    for phase in ("before", "after"):
        probe = proof.json(isolation.get("identity_" + phase))
        probe_record, _ = proof.original(isolation.get("identity_" + phase))
        need(
            probe_record["run_id"] == record["run_id"]
            and probe.get("query") == query
            and probe.get("phase") == phase,
            "Original before/after SQL identity query or run is missing",
        )
        result = rows(probe.get("rows"), "actual SQL identity rows")
        need(len(result) == 1, "Exactly one actual database identity row is required")
        row = obj(result[0], "actual SQL database identity row")
        need(
            row.get("database") == database
            and row.get("server_address") in {server_address, server_address + "/32"}
            and type(row.get("server_port")) is int
            and row["server_port"] == server_port
            and isinstance(row.get("database_user"), str)
            and bool(row["database_user"]),
            "Actual SQL server/database differs from the guarded seed target",
        )
        captured = aware(probe.get("captured_at"))
        need(
            captured <= seed_started_at if phase == "before" else captured >= seed_started_at,
            "Actual SQL identity capture does not bracket seed start",
        )
        observed.append(row)
    creation = proof.json(isolation.get("creation_original"))
    creation_record, _ = proof.original(isolation.get("creation_original"))
    need(
        creation_record["run_id"] == record["run_id"]
        and creation.get("database") == database
        and creation.get("owner_run_id") == record["run_id"]
        and creation.get("preexisting") is False
        and creation.get("sql") == f'CREATE DATABASE "{database}"',
        "Fresh owned database creation original is missing; exit zero alone is insufficient",
    )
    need(
        aware(creation.get("executed_at")) <= seed_started_at,
        "Database creation follows the seed it claims to isolate",
    )
    return {
        "database": database,
        "owner_run_id": record["run_id"],
        "sql_identity_rows": observed,
        "client_target": engine,
        "actual_server_endpoint": {"address": server_address, "port": server_port},
        "endpoint_method": isolation["server_binding"]["mode"],
    }


def native_backend_pytest_argv(argv: list[Any], root: Path) -> bool:
    """Recognize actual complete backend argv, including the current native wrapper."""
    if not argv or any(type(value) is not str or not value for value in argv):
        return False
    command = cast(list[str], argv)

    def binary(value: str) -> str:
        return (
            value.replace("\\", "/")
            .split("/")[-1]
            .casefold()
            .removesuffix(".exe")
            .removesuffix(".cmd")
        )

    if len(command) < 5 or binary(command[0]) != "uv" or command[1:3] != ["run", "--frozen"]:
        return False
    wrapper_path = command[4].replace("\\", "/")
    if binary(command[3]) == "python" and wrapper_path in {
        "scripts/run_scoped_check.py",
        (root / "scripts/run_scoped_check.py").as_posix(),
    }:
        if command.count("--") != 1:
            return False
        separator = command.index("--")
        options = command[5:separator]
        if len(options) < 6 or options[:2] != ["--task", "W1"] or options[2] != "--label":
            return False
        if not re.fullmatch(r"[A-Za-z0-9_-]+", options[3]):
            return False
        prefixes = options[4:]
        if len(prefixes) % 2 or any(
            prefixes[i] != "--source-prefix" for i in range(0, len(prefixes), 2)
        ):
            return False
        command = command[separator + 1 :]
        if len(command) < 5 or binary(command[0]) != "uv" or command[1:3] != ["run", "--frozen"]:
            return False
    if command[3:6] == ["python", "-m", "pytest"]:
        arguments = command[6:]
    elif binary(command[3]) == "pytest":
        arguments = command[4:]
    else:
        return False
    forbidden = (
        "--cov-append",
        "--no-cov",
        "--cov-fail-under",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--last-failed",
        "--lf",
        "--stepwise",
        "--sw",
        "--pyargs",
        "--collect-only",
        "--co",
        "--override-ini",
        "-o",
        "--config-file",
        "-c",
        "-k",
        "-m",
    )
    if any(arg == flag or arg.startswith(flag + "=") for arg in arguments for flag in forbidden):
        return False
    if any(
        arg.startswith(("-k", "-m", "-o", "-c")) and not arg.startswith("--") for arg in arguments
    ):
        return False
    if any(arg.startswith("--cov=") for arg in arguments) or arguments.count("--cov") != 1:
        return False
    if "--cov-config=docs/spec/mvp-coverage.ini" not in arguments:
        return False
    if any(
        arg.startswith("--cov-config=") and arg != "--cov-config=docs/spec/mvp-coverage.ini"
        for arg in arguments
    ):
        return False
    positional = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg == "-p":
            if index + 1 >= len(arguments):
                return False
            index += 2
            continue
        if not arg.startswith("-"):
            positional.append(arg.replace("\\", "/"))
        index += 1
    return sorted(positional) == ["apps/api/app/tests", "scripts/tests"]


def final_commands_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    commands = obj(inputs.get("commands"), "bootstrap/seed/check original command refs")
    need(set(commands) == {"bootstrap", "seed", "check"}, "Original first3 final commands missing")
    inspected = []
    isolation_result = None
    required = {
        "bootstrap": ("uv sync --frozen", "pnpm install --frozen-lockfile"),
        "seed": ("docker compose up", "alembic upgrade head", "scripts/seed_demo.py"),
        "check": (
            "ruff check",
            "ruff format --check",
            "mypy",
            "scripts/generate_openapi.py",
            "apps/web lint",
            "apps/web typecheck",
            "apps/web test",
            "apps/web e2e",
        ),
    }
    for target, refs in commands.items():
        refs = obj(refs, "original final task refs")
        scoped, _ = proof.scoped(refs.get("scoped"))
        need(
            scoped["command"][-1] == target
            and any("make.cmd" in arg or "tasks.py" in arg for arg in scoped["command"]),
            "Final original command did not invoke native target",
        )
        task = proof.json(refs.get("task_manifest"))
        record, _ = proof.original(refs.get("task_manifest"))
        need(
            task.get("run_id") == record["run_id"] and task.get("target") == target,
            "Original task identity/target mismatch",
        )
        need(task.get("successful") is True, "An original failed final task stays failed")
        need(task.get("source_stable") is True, "Actual native task source drifted")
        source = obj(task.get("source"), "native task source")
        after = obj(task.get("source_after"), "native task source after")
        need(source == after, "Actual native task source before/after differs")
        proof.current_map(source.get("source_sha256"))
        need(
            not proof.head or task["source"].get("git_revision") == proof.head,
            "Native final command Git HEAD mismatch",
        )
        children = rows(task.get("commands"), "native executed child commands")
        need(bool(children), "A success flag without child commands is not execution")
        if target == "seed":
            isolation_result = seed_isolation_proof(
                proof,
                inputs.get("seed_isolation"),
                task["run_id"],
                min(aware(child.get("started_at")) for child in children),
            )
        joined = []
        original_argvs = []
        base = Path(record["resolved_repository_path"]).parent.as_posix()
        for child in children:
            need(
                type(child.get("exit_code")) is int and child["exit_code"] == 0,
                "A final task child failed",
            )
            aware(child.get("started_at"))
            argv = rows(child.get("command"), "native child argv")
            original_argvs.append(argv)
            joined.append(" ".join(argv))
            log_record, log = proof.by_path(base + "/" + child["log"])
            need(
                log_record["run_id"] == record["run_id"] and bool(log.strip()),
                "Actual final task child log missing or from another run",
            )
        need(
            all(any(token in argv for argv in joined) for token in required[target]),
            "Original native final command omitted required real children",
        )
        if target == "check":
            need(
                any(native_backend_pytest_argv(argv, proof.root) for argv in original_argvs),
                "Actual final check omitted complete backend pytest targets/full coverage",
            )
        inspected.append(
            {"target": target, "task_run_id": task["run_id"], "child_count": len(children)}
        )
    return {
        "completed_native_commands": inspected,
        "actual_seed_isolation": isolation_result,
        "export_evidence": "CURRENT_CLI_BYTE_RECHECK_REQUIRED",
        "outer_task_exit": "OUTER_TASK_EXIT_PENDING",
    }


def proposal_proof(proof: Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    _, raw = proof.original(inputs.get("pdf"))
    executable = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native"
        / "poppler/Library/bin/pdfinfo.exe"
    )
    if not executable.is_file():
        raise MissingProof("Actual PDF page parser unavailable; Markdown chapters are not pages")
    result = subprocess.run(
        [str(executable), "-enc", "UTF-8", "-"],
        input=raw,
        capture_output=True,
        timeout=15,
        check=False,
    )
    need(result.returncode == 0, "Actual PDF could not be parsed")
    text = result.stdout.decode("utf-8")
    pages = re.findall(r"^Pages:\s+(\d+)\s*$", text, re.MULTILINE)
    need(len(pages) == 1 and 10 <= int(pages[0]) <= 12, "Actual proposal PDF is not10–12pages")
    need(
        not re.search(r"^Encrypted:\s+yes", text, re.MULTILINE), "Encrypted PDF cannot be reviewed"
    )
    return {
        "actual_pdf_pages": int(pages[0]),
        "parser_sha256": digest(executable.read_bytes()),
        "boundary": "Actual page structure only; separate material/visual review is required",
    }


def semantic_proof(proof: Originals, requirement: str, inputs: Any) -> dict[str, Any]:
    inputs = obj(inputs, "explicit native input references")
    if requirement == "offline_three_golden_chains":
        from scripts.w1_offline_export_validation import offline_three_golden_chains

        return offline_three_golden_chains(proof, inputs)
    if requirement == "audit_chain" and ("snapshot" in inputs or "observer" in inputs):
        return audit_bridge(proof, inputs)
    if requirement == "screenshots_and_recording" and "browser_manifest" in inputs:
        return media_bridge(proof, inputs)
    if requirement in UNSUPPORTED_NATIVE:
        raise UnsupportedProof(UNSUPPORTED_NATIVE[requirement])
    handlers = {
        "audit_chain": audit_bridge,
        "backend_coverage_and_properties": coverage_proof,
        "frontend_interactions": frontend_proof,
        "frozen_24_cases": frozen_proof,
        "original_financial_chain": financial_proof,
        "formal_history_preservation": formal_proof,
        "final_four_commands": final_commands_proof,
        "proposal_actual_pages": proposal_proof,
    }
    if requirement in {"six_business_e2e", "three_demo_rounds"}:
        return browser_proof(proof, inputs, requirement)
    if requirement not in handlers:
        raise MissingProof("Original mandatory requirement has no implemented semantic checker")
    return handlers[requirement](proof, inputs)


def export_package(
    manifest_path: Path,
    root: Path,
    current_head: str,
    output_override: Path | None = None,
    *,
    cli_execution: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    root = root.resolve()
    request_path = manifest_path.resolve()
    if not request_path.is_relative_to(root):
        raise RequestError("Request manifest must be inside the repository evidence roots")
    resolve_input(root, request_path.relative_to(root).as_posix(), "DIAGNOSTIC")
    request_raw = read_stable(request_path, digest(request_path.read_bytes()))
    request = strict_json(request_raw)
    if not isinstance(request, dict) or request.get("manifest_version") != VERSION:
        raise RequestError("Unsupported explicit evidence request schema")
    package_id = identity(request.get("package_run_id"), "package_run_id")
    purpose = request.get("purpose")
    if purpose not in PURPOSES:
        raise RequestError("Explicit package purpose is required")
    source = request.get("source")
    if not isinstance(source, dict) or source.get("git_head") != current_head:
        raise RequestError("Package is not bound to the current original Git HEAD")
    files = source.get("files")
    if not isinstance(files, dict) or set(files) != set(inventory(root)):
        raise RequestError(
            "Source inventory must include every current registered production/test/config file"
        )
    source_hash = source_digest(files)
    if sha(source.get("source_sha256")) != source_hash:
        raise RequestError("Explicit complete source digest mismatch")
    source_bytes = {
        name: read_stable(resolve_input(root, name, "SOURCE"), expected)
        for name, expected in files.items()
    }
    runs = request.get("runs")
    if not isinstance(runs, dict) or not runs:
        raise RequestError("Original run bindings are required, not guessed from newest logs")
    for run_id, binding in runs.items():
        identity(run_id, "run_id")
        if not isinstance(binding, dict) or binding.get("purpose") not in PURPOSES:
            raise RequestError("Each run needs its explicit original purpose")
        run_source = sha(binding.get("source_sha256"))
        source_context = binding.get("source_context")
        if source_context not in {"CURRENT_SOURCE", "HISTORICAL_CONTEXT"}:
            raise RequestError("Each run must declare CURRENT_SOURCE or HISTORICAL_CONTEXT")
        if source_context == "CURRENT_SOURCE" and run_source != source_hash:
            raise RequestError("Historical/different-source runs cannot be silently relabeled")
    entries = request.get("artifacts")
    if not isinstance(entries, list):
        raise RequestError("Artifacts must be an explicit list")
    output = output_target(
        root,
        output_override or Path(request.get("output", f".runtime/evidence_packages/{package_id}")),
    )
    prepared: list[tuple[dict[str, Any], bytes | None]] = []
    identities: set[str] = set()
    real_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RequestError("Every artifact needs its own explicit record")
        artifact_id = identity(entry.get("artifact_id"), "artifact_id")
        if artifact_id.casefold() in identities:
            raise RequestError("Repeated artifact ID (including Windows case aliases)")
        identities.add(artifact_id.casefold())
        role = entry.get("role")
        if role not in ROLES:
            raise RequestError("Artifact role is outside the registered allowlist")
        path = resolve_input(root, entry.get("path"), role)
        real_key = str(path).casefold()
        if real_key in real_paths:
            raise RequestError(
                "Repeated original real file cannot manufacture independent evidence"
            )
        real_paths.add(real_key)
        run_id = identity(entry.get("run_id"), "artifact run_id")
        if run_id not in runs or entry.get("purpose") != runs[run_id]["purpose"]:
            raise RequestError("Artifact original run/purpose binding mismatch")
        if sha(entry.get("source_sha256")) != runs[run_id]["source_sha256"]:
            raise RequestError("Artifact source binding mismatch")
        expected_sha = sha(entry.get("sha256"))
        size = entry.get("size_bytes")
        if type(size) is not int or size < 0:
            raise RequestError("Artifact size_bytes must be a nonnegative integer")
        if path.exists() and not path.is_file():
            raise RequestError("A directory is not an evidence original")
        raw = read_stable(path, expected_sha, size) if path.exists() else None
        validator = entry.get("validator", "NOT_IMPLEMENTED")
        if not isinstance(validator, str):
            raise RequestError("Artifact validator must name an implemented capability or gap")
        check = (
            content_check(validator, raw)
            if raw is not None
            else {"status": "MISSING", "reason": "Declared original file does not exist"}
        )
        prepared.append(
            (
                {
                    **entry,
                    "resolved_repository_path": path.relative_to(root).as_posix(),
                    "archive_path": f"archive/{artifact_id}/{path.name}"
                    if raw is not None
                    else None,
                    "byte_status": "ORIGINAL_BYTES_VERIFIED" if raw is not None else "MISSING",
                    "source_context": runs[run_id]["source_context"],
                    "run_binding_status": "EXPLICIT_DECLARATION_ONLY_NOT_RUN_VALIDATION",
                    "content_check": check,
                },
                raw,
            )
        )
    checks = request.get("checks", [])
    if not isinstance(checks, list):
        raise RequestError("Requirement checks must be an explicit list")
    requirements: dict[str, dict[str, Any]] = {}
    entry_map = {record["artifact_id"]: record for record, _ in prepared}
    proof = Originals(root, files, prepared, current_head)
    for check in checks:
        if not isinstance(check, dict):
            raise RequestError("Every requirement check must be an explicit record")
        requirement = check.get("requirement_id")
        if (
            not isinstance(requirement, str)
            or requirement not in REQUIRED
            or requirement in requirements
        ):
            raise RequestError("Unknown or duplicate original mandatory requirement")
        refs = check.get("artifact_ids")
        if (
            not isinstance(refs, list)
            or any(not isinstance(ref, str) for ref in refs)
            or len(refs) != len(set(refs))
            or any(ref not in entry_map for ref in refs)
        ):
            raise RequestError("Requirement has unknown/duplicate artifact references")
        present = bool(refs) and all(
            entry_map[ref]["byte_status"] == "ORIGINAL_BYTES_VERIFIED" for ref in refs
        )
        checked: dict[str, Any] = {
            "requirement_id": requirement,
            "artifact_ids": refs,
            "requested_validator": check.get("validator"),
            "status": "UNVERIFIED" if present else "MISSING",
            "reason": (
                "Original bytes alone are not semantic evidence; "
                "mandatory acceptance validator is not implemented"
            )
            if present
            else "Required originals were not supplied or are missing",
        }
        if present and check.get("validator") == NATIVE_VALIDATOR:
            proof.allowed = set(refs)
            try:
                checked["observations"] = semantic_proof(proof, requirement, check.get("inputs"))
                checked.update(
                    status="VERIFIED", reason="Native originals independently recomputed"
                )
            except PartialProof as error:
                checked.update(
                    status="UNVERIFIED",
                    reason=str(error),
                    observations=error.observations,
                    uncovered=error.uncovered,
                )
            except MissingProof as error:
                checked.update(status="MISSING", reason=str(error))
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
                checked.update(status="UNVERIFIED", reason=str(error))
            finally:
                proof.allowed = None
        requirements[requirement] = checked
    for requirement in REQUIRED:
        requirements.setdefault(
            requirement,
            {
                "requirement_id": requirement,
                "artifact_ids": [],
                "status": "MISSING",
                "reason": "Mandatory original evidence group was not supplied",
            },
        )
    # Re-read each source to refuse changes during artifact inspection. No financial calls.
    for name, raw in source_bytes.items():
        if read_stable(resolve_input(root, name, "SOURCE"), digest(raw)) != raw:
            raise RequestError("Current source changed while preparing the archive")
    if set(inventory(root)) != set(source_bytes):
        raise RequestError("Current source inventory changed while preparing the archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)

    def save(relative: str, raw: bytes) -> dict[str, Any]:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(raw)
        actual = destination.read_bytes()
        if actual != raw:
            raise RequestError("Archived bytes differ from original; package remains incomplete")
        return {"path": relative, "sha256": digest(actual), "size_bytes": len(actual)}

    index = [save("request.original.json", request_raw)]
    for name, raw in sorted(source_bytes.items()):
        index.append(save(f"source/{name}", raw))
    for record, raw in prepared:
        if raw is not None:
            index.append(save(record["archive_path"], raw))
    checked_groups = list(requirements.values())
    gaps = [row for row in checked_groups if row["status"] != "VERIFIED"]
    synthetic = purpose == "TOOL_ONLY" or request.get("evidence_kind") in {
        "TOOL_ONLY",
        "TOOL_TEST_ONLY",
    }
    ready = not gaps and purpose == "MVP_ACCEPTANCE" and not synthetic and cli_execution is not None
    package_status, exit_code = ("EXPORTED", 0) if ready else ("INCOMPLETE", 1)
    failures = [{"kind": "MANDATORY_EVIDENCE_GAP", **gap} for gap in gaps] + [
        {
            "kind": "ARTIFACT_CONTENT_GAP",
            "artifact_id": record["artifact_id"],
            **record["content_check"],
        }
        for record, _ in prepared
        if record["content_check"]["status"] != "CONTENT_ONLY_VERIFIED"
    ]
    index.append(
        save(
            "failures-and-gaps.jsonl",
            b"".join(
                (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in failures
            ),
        )
    )
    report = {
        "manifest_version": "bounded-funds-evidence-package-v1",
        "package_run_id": package_id,
        "purpose": purpose,
        "status": package_status,
        "package_status": package_status,
        "acceptance_status": "EXTERNAL_EXIT_UNVERIFIED" if ready else "INCOMPLETE",
        "exit_code": exit_code,
        "archive_status": "AVAILABLE_ORIGINAL_BYTES_VERIFIED",
        "declared_status_not_trusted": request.get("status"),
        "source": source,
        "runs": runs,
        "exported_at": datetime.now(UTC).isoformat(),
        "artifacts": [record for record, _ in prepared],
        "requirements": checked_groups,
        "implemented_content_capabilities": CAPABILITIES,
        "semantic_acceptance_validators": sorted(SUPPORTED_NATIVE),
        "unsupported_semantic_requirements": UNSUPPORTED_NATIVE,
        "partial_original_recomputation_bridges": sorted(PARTIAL_NATIVE),
        "current_cli_execution": cli_execution,
        "synthetic_tool_only": synthetic,
        "boundary": (
            "Read-only originals and explicit native semantic recomputation. "
            "No DB/browser execution or historical writes. "
            "No requirement closes; final outer task exit and closure remain external."
        ),
        "task_closed": False,
    }
    save("manifest.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    save("index.json", (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for item in index:
        actual = (output / item["path"]).read_bytes()
        need(
            digest(actual) == item["sha256"] and len(actual) == item["size_bytes"],
            "Final package byte recheck failed; outer command must remain nonzero",
        )
    return {
        "status": package_status,
        "package_status": package_status,
        "acceptance_status": report["acceptance_status"],
        "exit_code": exit_code,
        "directory": str(output),
        "package_run_id": package_id,
        "original_artifacts_archived": sum(raw is not None for _, raw in prepared),
        "source_files_archived": len(source_bytes),
        "indexed_files": len(index),
        "missing_or_unverified_requirements": len(gaps),
        "package_manifest_sha256": digest((output / "manifest.json").read_bytes()),
        "package_index_sha256": digest((output / "index.json").read_bytes()),
        "task_closed": False,
    }, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    location = args.manifest or os.environ.get("BOUNDEDFUNDS_EVIDENCE_REQUEST")
    try:
        if not location:
            raise RequestError(
                "Missing explicit --manifest or BOUNDEDFUNDS_EVIDENCE_REQUEST; no evidence selected"
            )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        actual_cli = {
            "run_id": "export-cli-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"),
            "actual_argv": sys.argv,
            "started_at": datetime.now(UTC).isoformat(),
            "exporter_sha256": digest(Path(__file__).read_bytes()),
            "outer_task_exit": "OUTER_TASK_EXIT_PENDING",
        }
        result, code = export_package(
            Path(location), ROOT, head, args.output, cli_execution=actual_cli
        )
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as error:
        result = {"status": "REJECTED", "exit_code": 2, "error": str(error), "task_closed": False}
        code = 2
    if isinstance(sys.stdout, TextIOWrapper):
        cast(TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
