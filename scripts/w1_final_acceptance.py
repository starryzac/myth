"""Explicit, source-frozen MVP four-command coordinator; default never connects."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import io
import ipaddress
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-final-prerequisites-v1"
OUTPUT_PROTOCOL = "bounded-funds-native-check-evidence-v1"
PRODUCER = "scripts/w1_final_acceptance.py"
IDENTITY_SQL = (
    "SELECT current_database() AS database, inet_server_addr()::text AS server_address, "
    "inet_server_port() AS server_port, current_user AS database_user"
)
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
CHECK_DEFERRED = frozenset(
    {
        "backend_coverage_and_properties",
        "frontend_interactions",
        "six_business_e2e",
        "three_demo_rounds",
        "original_financial_chain",
        "audit_chain",
    }
)
ALLOWED_DEFERRED = CHECK_DEFERRED | {"formal_history_preservation"}
SOURCE_CONFIG = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "package.json",
        "alembic.ini",
        "docker-compose.yml",
        "Makefile",
        "make.cmd",
        ".dockerignore",
    }
)
PRIVATE_PARTS = frozenset({".git", ".venv", "node_modules", ".aws", ".codex", ".agents"})


class Refused(ValueError):
    """A required real observation or implemented adapter is unavailable."""


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise Refused(reason)


def obj(value: Any, label: str) -> dict[str, Any]:
    need(type(value) is dict, f"Missing object: {label}")
    return cast(dict[str, Any], value)


def seq(value: Any, label: str) -> list[Any]:
    need(type(value) is list, f"Missing array: {label}")
    return cast(list[Any], value)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def hash_value(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())


def sha_string(value: Any) -> str:
    need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "Invalid SHA256")
    return cast(str, value)


def aware(value: Any) -> datetime:
    need(type(value) is str, "Missing aware timestamp")
    result = datetime.fromisoformat(value)
    need(result.tzinfo is not None and result.utcoffset() is not None, "Naive timestamp refused")
    return result.astimezone(UTC)


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        need(key not in result, "Duplicate original JSON key")
        result[key] = value
    return result


def nonfinite(value: str) -> Any:
    raise Refused(f"Nonfinite JSON scalar {value}")


def decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=nonfinite)


def plain_relative(value: Any) -> Path:
    need(type(value) is str and bool(value) and ":" not in value, "Plain relative path required")
    parts = value.replace("\\", "/").split("/")
    need(
        all(part and part not in {".", ".."} and part.rstrip(" .") == part for part in parts),
        "Path traversal/alias refused",
    )
    for part in parts:
        base = part.split(".")[0].casefold()
        need(
            part.casefold() not in PRIVATE_PARTS and not part.casefold().startswith(".env"),
            "Private path refused",
        )
        need(
            base not in {"con", "prn", "aux", "nul"}
            and re.fullmatch(r"(?:com|lpt)[1-9]", base) is None,
            "Windows reserved path refused",
        )
        need(not any(ord(char) < 32 or char in '<>"|?*' for char in part), "Nonplain path refused")
    return Path(*parts)


def resolve(root: Path, value: Any, *, evidence: bool = True) -> Path:
    relative = plain_relative(value)
    location = (root / relative).resolve()
    need(location.is_relative_to(root.resolve()), "Resolved path escaped repository")
    if evidence:
        need(
            relative.parts[0] in {".runtime", "docs", "data", "output"},
            "Unregistered evidence root",
        )
    return location


def original(root: Path, descriptor: Any) -> tuple[Path, bytes]:
    reference = obj(descriptor, "original descriptor")
    need(set(reference) == {"path", "sha256"}, "Original descriptor shape differs")
    path = resolve(root, reference["path"])
    need(path.is_file(), f"Original file missing: {reference['path']}")
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    need(
        (before.st_size, before.st_mtime_ns, before.st_ino)
        == (after.st_size, after.st_mtime_ns, after.st_ino),
        "Original changed while read",
    )
    need(sha(raw) == sha_string(reference["sha256"]), "Original byte SHA mismatch")
    return path, raw


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(encoded(value))


def source_state(root: Path) -> dict[str, Any]:
    """Full exporter inventory plus extra tracked code/config; Git reads only."""
    exporter = runpy.run_path(str(root / "scripts/export_evidence.py"), run_name="final_contract")
    names = exporter["inventory"](root)
    files = {name: sha((root / name).read_bytes()) for name in names}
    tracked = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    full = dict(files)
    for name in set(tracked) - {""}:
        path = root / name
        if path.is_file() and (
            name in SOURCE_CONFIG
            or name.startswith(("apps/", "scripts/", "deploy/", "packages/contracts/"))
            or (name.startswith("docs/spec/") and path.suffix in {".json", ".ini", ".yaml"})
        ):
            full[name] = sha(path.read_bytes())
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {
        "git_head": head,
        "files": files,
        "source_sha256": exporter["source_digest"](files),
        "all_source_files": dict(sorted(full.items())),
    }


def constants(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return result


def adapter_gaps(root: Path, source: dict[str, Any], requirement: str, adapter: Any) -> list[str]:
    entry = obj(adapter, "source-bound deferred producer adapter")
    path = (
        "scripts/verify_formal_preservation.py"
        if requirement == "formal_history_preservation"
        else "scripts/tasks.py"
    )
    need(
        entry.get("producer_path") == path
        and entry.get("producer_sha256") == source["files"].get(path),
        "Deferred adapter is not the current allowlisted producer",
    )
    if requirement == "formal_history_preservation":
        need(entry.get("protocol") == "FORMAL_PRESERVATION_V1", "Unknown formal adapter contract")
        return []
    need(entry.get("protocol") == OUTPUT_PROTOCOL, "Unknown native check output adapter protocol")
    registered = constants(root / path)
    if registered.get(
        "W1_FINAL_OUTPUT_PROTOCOL"
    ) != OUTPUT_PROTOCOL or requirement not in registered.get("W1_FINAL_OUTPUT_GROUPS", ()):
        return [f"{requirement}: tasks.py has no implemented native output adapter"]
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "final_acceptance_outputs"
    ]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "final_acceptance_outputs"
    ]
    if (
        not functions
        or not calls
        or not any(
            isinstance(node, ast.Call) for function in functions for node in ast.walk(function)
        )
    ):
        return [f"{requirement}: native output adapter has no callable capture implementation"]
    return []


def validate_plan(
    registration: dict[str, Any], root: Path, current: dict[str, Any]
) -> dict[str, Any]:
    """Structural/original-byte readiness; no process, SQL, browser or Docker call."""
    value = obj(registration, "explicit final prerequisites")
    need(
        value.get("protocol") == PROTOCOL and value.get("purpose") == "MVP_ACCEPTANCE",
        "Explicit MVP prerequisite protocol required",
    )
    need(value.get("source") == current, "Full registered source/HEAD changed")
    target = obj(value.get("server"), "fixed local server")
    need(
        target
        == {
            "host": "127.0.0.1",
            "port": 54329,
            "username": "bounded",
            "formal_database": "bounded_funds",
            "admin_database": "postgres",
            "container": "bounded-funds-db-1",
        },
        "Remote/formal/default write target or server override refused",
    )
    _, template_raw = original(root, value.get("export_request_template"))
    template = obj(decode(template_raw), "actual exporter request template")
    need(
        template.get("manifest_version") == "bounded-funds-evidence-request-v1"
        and template.get("purpose") == "MVP_ACCEPTANCE"
        and template.get("source")
        == {key: current[key] for key in ("git_head", "files", "source_sha256")},
        "Export template purpose/source differs",
    )
    package_path, package_raw = original(root, value.get("readiness_package"))
    package = obj(decode(package_raw), "real readiness package")
    need(
        package_path.name == "manifest.json"
        and package_path.parent.is_relative_to((root / ".runtime/evidence_packages").resolve()),
        "Readiness must name original native package manifest",
    )
    need(
        package.get("manifest_version") == "bounded-funds-evidence-package-v1"
        and package.get("purpose") == "MVP_ACCEPTANCE"
        and package.get("synthetic_tool_only") is False,
        "Development/TOOL_ONLY package cannot authorize final run",
    )
    need(package.get("source") == template["source"], "Readiness package source is stale")
    cli = obj(package.get("current_cli_execution"), "original exporter CLI")
    need(
        cli.get("exporter_sha256") == current["files"].get("scripts/export_evidence.py")
        and type(cli.get("actual_argv")) is list
        and bool(cli["actual_argv"])
        and all(type(arg) is str for arg in cli["actual_argv"])
        and Path(cli["actual_argv"][0]).name == "export_evidence.py",
        "Readiness lacks native current exporter CLI capture",
    )
    aware(cli.get("started_at"))
    index_path, index_raw = original(root, value.get("readiness_index"))
    need(
        index_path == package_path.parent / "index.json",
        "Readiness index belongs to another package",
    )
    entries = seq(decode(index_raw), "native index")
    indexed: dict[str, bytes] = {}
    for entry in entries:
        record = obj(entry, "indexed original")
        relative = plain_relative(record.get("path"))
        path = (package_path.parent / relative).resolve()
        need(
            path.is_relative_to(package_path.parent) and path.is_file(),
            "Package index escaped or missing",
        )
        need(relative.as_posix() not in indexed, "Duplicate package index identity")
        raw = path.read_bytes()
        need(
            type(record.get("size_bytes")) is int
            and len(raw) == record["size_bytes"]
            and sha(raw) == sha_string(record.get("sha256")),
            "Readiness indexed bytes changed",
        )
        indexed[relative.as_posix()] = raw
    if "manifest.json" in indexed:
        need(indexed["manifest.json"] == package_raw, "Indexed package manifest differs")
    request = obj(decode(indexed.get("request.original.json", b"{}")), "indexed actual request")
    need(
        request.get("purpose") == "MVP_ACCEPTANCE" and request.get("source") == template["source"],
        "Readiness original request missing/stale",
    )
    requirements = {
        obj(row, "readiness requirement")["requirement_id"]: row
        for row in seq(package.get("requirements"), "readiness requirements")
    }
    need(
        len(requirements) == len(package["requirements"]) and set(requirements) == set(REQUIRED),
        "All eighteen original requirement groups required",
    )
    deferred = obj(value.get("deferred_groups"), "explicit deferred groups")
    need(
        set(deferred) <= ALLOWED_DEFERRED, "Only actual current-check/formal groups may be deferred"
    )
    missing: list[str] = []
    task_tree = ast.parse((root / "scripts/tasks.py").read_text(encoding="utf-8"))
    configuration: set[str] = set()
    for function in task_tree.body:
        if isinstance(function, ast.FunctionDef) and function.name == "source_state":
            for node in ast.walk(function):
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "configuration"
                    for target in node.targets
                ):
                    configuration = set(ast.literal_eval(node.value))
    missing.extend(
        f"tasks.source_state: registered root config omitted: {name}"
        for name in current["files"]
        if "/" not in name and name not in configuration
    )
    for requirement in REQUIRED:
        if requirement == "final_four_commands":
            continue
        if requirement in deferred:
            need(
                requirements[requirement].get("status") == "DEFERRED_FROM_CURRENT_CHECK"
                or requirements[requirement].get("status") in {"MISSING", "UNVERIFIED"},
                "Deferral cannot relabel an original verified/failing run",
            )
            entry = obj(deferred[requirement], "deferred adapter")
            need(
                entry.get("status") == "DEFERRED_FROM_CURRENT_CHECK",
                "Explicit true deferral required",
            )
            missing.extend(adapter_gaps(root, current, requirement, entry))
        elif requirements[requirement].get("status") != "VERIFIED":
            missing.append(
                f"{requirement}: prerequisite status={requirements[requirement].get('status')}"
            )
    exporter_constants = constants(root / "scripts/export_evidence.py")
    unsupported = obj(
        exporter_constants.get("UNSUPPORTED_NATIVE", {}), "exporter unsupported groups"
    )
    missing.extend(
        f"{requirement}: current native exporter has no whole-group validator"
        for requirement in unsupported
    )
    need(value.get("formal_anchor") is not None, "Original W0 preservation anchor/index required")
    for name in ("baseline", "w0_index"):
        original(root, obj(value["formal_anchor"], "W0 anchors").get(name))
    return {
        "status": "VALIDATED_NOT_RUN",
        "ready": not missing,
        "missing_prerequisites": missing,
        "deferred_groups": sorted(deferred),
        "template": template,
        "task_closed": False,
    }


def child_environment(
    inherited: dict[str, str], password: str, database: str, context: Path
) -> dict[str, str]:
    need(
        re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None,
        "Generated owned UUID database required",
    )
    environment = {
        key: value
        for key, value in inherited.items()
        if not key.upper().startswith(("BF_", "COMPOSE_", "PG"))
        and key.upper()
        not in {
            "DATABASE_URL",
            "BOUNDEDFUNDS_EVIDENCE_REQUEST",
            "BOUNDEDFUNDS_FINAL_CONTEXT",
            "PYTHONPATH",
            "PYTHONHOME",
            "PYTEST_ADDOPTS",
            "COVERAGE_FILE",
            "W1_FINAL_DB_PASSWORD",
        }
    }
    environment.update(
        DATABASE_URL=(
            f"postgresql+psycopg://bounded:{quote(password, safe='')}@127.0.0.1:54329/{database}"
        ),
        PYTHONUTF8="1",
        PYTHONPATH=str(ROOT / "apps/api"),
        BOUNDEDFUNDS_FINAL_CONTEXT=str(context),
    )
    return environment


def docker_endpoint(inspected: Any) -> tuple[str, str]:
    records = seq(inspected, "native Docker inspect")
    need(len(records) == 1, "Exactly one actual db container required")
    container = obj(records[0], "actual db container")
    identity = container.get("Id")
    need(
        type(identity) is str
        and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
        and container.get("Name") == "/bounded-funds-db-1",
        "Actual container name/ID differs",
    )
    need(
        obj(container.get("State"), "container state").get("Running") is True,
        "Native db container is not running",
    )
    labels = obj(obj(container.get("Config"), "container config").get("Labels"), "Compose labels")
    need(
        labels.get("com.docker.compose.project") == "bounded-funds"
        and labels.get("com.docker.compose.service") == "db",
        "Actual Compose ownership differs",
    )
    network = obj(container.get("NetworkSettings"), "native network settings")
    need(
        obj(network.get("Ports"), "actual ports").get("5432/tcp")
        == [{"HostIp": "127.0.0.1", "HostPort": "54329"}],
        "Published endpoint is not exact local54329",
    )
    addresses = {
        obj(row, "network endpoint").get("IPAddress")
        for row in obj(network.get("Networks"), "actual networks").values()
    } - {""}
    need(
        len(addresses) == 1
        and all(
            type(address) is str
            and isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address)
            for address in addresses
        ),
        "Ambiguous actual db network IP",
    )
    return cast(str, identity), cast(str, next(iter(addresses)))


def verify_identity(probe: dict[str, Any], database: str, endpoint: str) -> None:
    need(probe.get("query") == IDENTITY_SQL, "SQL identity query changed")
    aware(probe.get("captured_at"))
    rows = seq(probe.get("rows"), "actual identity rows")
    need(len(rows) == 1, "One actual SQL identity row required")
    row = obj(rows[0], "actual SQL row")
    need(
        set(row) == {"database", "server_address", "server_port", "database_user"}
        and row["database"] == database
        and row["server_address"] in {endpoint, endpoint + "/32"}
        and row["server_port"] == 5432
        and row["database_user"] == "bounded",
        "Native SQL database/server/user differs",
    )
    need(type(row["server_port"]) is int, "SQL port must be strict integer")


def native_command(target: str) -> list[str]:
    need(
        target in {"bootstrap", "seed", "check", "export-evidence"},
        "Only original four native targets permitted",
    )
    need(os.name == "nt", "This make.cmd acceptance entry requires actual Windows")
    executable = shutil.which("cmd.exe")
    need(executable is not None, "Native Windows cmd.exe missing")
    return [cast(str, executable), "/d", "/c", "make.cmd", target]


def labeled_wrapper_output(raw: bytes, label: str, root: Path) -> Path:
    """Nested children may print their own wrapper paths; retain this exact command label."""
    matches = re.findall(rb"(?m)^EVIDENCE_DIRECTORY=(.+)\r?$", raw)
    outputs = [Path(match.decode().strip()).resolve() for match in matches]
    outputs = [path for path in outputs if path.name.startswith(label + "-")]
    need(len(outputs) == 1, "Actual labeled outer wrapper evidence path ambiguous/missing")
    output = outputs[0]
    need(
        output.is_relative_to((root / "docs/progress/evidence/W1").resolve()),
        "Wrapper output escaped W1",
    )
    return output


def closure(
    package: dict[str, Any],
    index: list[dict[str, Any]],
    directory: Path,
    task: dict[str, Any],
    scoped: dict[str, Any],
    source: dict[str, Any],
    export_log: bytes,
) -> dict[str, Any]:
    """Close only after actual export task+wrapper exits and original byte recheck."""
    need(
        task.get("target") == "export-evidence"
        and task.get("successful") is True
        and task.get("source_stable") is True,
        "Export native task did not finish successfully",
    )
    for phase in ("source", "source_after"):
        actual = obj(task.get(phase), "export task original source")
        mapping = obj(actual.get("source_sha256"), "export task full source")
        need(
            actual.get("git_revision") == source["git_head"]
            and all(mapping.get(name) == digest for name, digest in source["files"].items()),
            "Export task source/HEAD differs",
        )
    children = seq(task.get("commands"), "export task actual children")
    exports = [
        row
        for row in children
        if row.get("command") == ["uv", "run", "--frozen", "python", "scripts/export_evidence.py"]
    ]
    need(
        len(exports) == 1
        and type(exports[0].get("exit_code")) is int
        and exports[0]["exit_code"] == 0,
        "Actual native exporter child is absent or failed",
    )
    need(bool(index), "Actual package index is empty")
    need(
        scoped.get("status") == "PASSED"
        and type(scoped.get("exit_code")) is int
        and scoped["exit_code"] == 0
        and scoped.get("all_source_stable") is True
        and scoped.get("git_head") == source["git_head"],
        "Actual export outer exit/source failed",
    )
    need(
        package.get("package_status") == "EXPORTED"
        and type(package.get("exit_code")) is int
        and package["exit_code"] == 0
        and package.get("purpose") == "MVP_ACCEPTANCE"
        and package.get("synthetic_tool_only") is False,
        "Fourth command did not export complete actual evidence",
    )
    need(
        package.get("source")
        == {key: source[key] for key in ("git_head", "files", "source_sha256")},
        "Export package source differs",
    )
    need(
        package.get("acceptance_status") == "EXTERNAL_EXIT_UNVERIFIED"
        and package.get("task_closed") is False,
        "Package must preserve initial external-exit boundary",
    )
    cli = obj(package.get("current_cli_execution"), "actual exporter CLI")
    need(
        cli.get("actual_argv") == ["scripts/export_evidence.py"]
        and cli.get("exporter_sha256") == source["files"].get("scripts/export_evidence.py")
        and cli.get("outer_task_exit") == "OUTER_TASK_EXIT_PENDING",
        "Actual fourth command CLI binding differs",
    )
    need(
        aware(exports[0].get("started_at")) <= aware(cli.get("started_at")),
        "Exporter CLI predates its actual task child",
    )
    lines = export_log.decode("utf-8").splitlines()
    start = [i for i, line in enumerate(lines) if line == "{"]
    need(len(start) == 1, "Actual exporter terminal result absent/ambiguous")
    summary = obj(decode("\n".join(lines[start[0] :]).encode()), "actual exporter terminal result")
    need(
        summary.get("package_run_id") == package.get("package_run_id")
        and summary.get("package_status") == "EXPORTED"
        and type(summary.get("exit_code")) is int
        and summary["exit_code"] == 0
        and summary.get("package_manifest_sha256")
        == sha((directory / "manifest.json").read_bytes())
        and summary.get("package_index_sha256") == sha((directory / "index.json").read_bytes()),
        "Actual exporter log/closed package original SHA differs",
    )
    rows = seq(package.get("requirements"), "all original groups")
    need(
        len(rows) == len(REQUIRED)
        and {row.get("requirement_id") for row in rows} == set(REQUIRED)
        and all(row.get("status") == "VERIFIED" for row in rows),
        "Not all eighteen groups independently verified",
    )
    indexed: set[str] = set()
    for item in index:
        need(item.get("path") not in indexed, "Duplicate closed package index path")
        indexed.add(item["path"])
        path = (directory / plain_relative(item.get("path"))).resolve()
        need(
            path.is_relative_to(directory.resolve()) and path.is_file(),
            "Export index path escaped/missing",
        )
        raw = path.read_bytes()
        need(
            sha(raw) == sha_string(item.get("sha256"))
            and type(item.get("size_bytes")) is int
            and len(raw) == item["size_bytes"],
            "Export indexed original bytes changed after task exit",
        )
    return {
        "protocol": "bounded-funds-final-closure-v1",
        "package_run_id": package.get("package_run_id"),
        "export_task_run_id": task.get("run_id"),
        "export_scoped_run_id": scoped.get("run_id"),
        "observed_outer_exit_code": 0,
        "four_commands_status": "VERIFIED",
        "task_closed": False,
        "scope": "Four commands completed; task closure requires separate review",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prerequisites", required=True)
    parser.add_argument("--run", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        registration_path = resolve(ROOT, arguments.prerequisites)
        raw = registration_path.read_bytes()
        current = source_state(ROOT)
        plan = validate_plan(obj(decode(raw), "registration"), ROOT, current)
        if not arguments.run:
            print(
                encoded({key: value for key, value in plan.items() if key != "template"}).decode(),
                end="",
            )
            return 0
        need(
            plan["ready"] is True,
            "Final prerequisites unavailable: " + "; ".join(plan["missing_prerequisites"]),
        )
        return execute(obj(decode(raw), "registration"), raw, current, plan)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(
            encoded(
                {"status": "REFUSED_NOT_RUN", "error": str(error), "task_closed": False}
            ).decode(),
            end="",
        )
        return 2


def execute(
    registration: dict[str, Any],
    registration_raw: bytes,
    frozen: dict[str, Any],
    plan: dict[str, Any],
) -> int:
    """Actual future --run path. Unit tests never call this SQL/process capability."""
    password = os.environ.get("W1_FINAL_DB_PASSWORD")
    need(
        type(password) is str and bool(password) and not any(char in password for char in "\r\n\0"),
        "Set private W1_FINAL_DB_PASSWORD; it is never written to proof",
    )
    password = cast(str, password)
    owner = "final-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    database = "bf_test_" + uuid4().hex
    directory = ROOT / "docs/progress/evidence/W1" / owner
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "prerequisites.original.json").write_bytes(registration_raw)
    context = directory / "context.json"
    write_new(
        context,
        {
            "protocol": "bounded-funds-final-context-v1",
            "owner_run_id": owner,
            "database": database,
            "source": frozen,
            "deferred_groups": plan["deferred_groups"],
        },
    )
    environment = child_environment(dict(os.environ), password, database, context)
    state: dict[str, Any] = {
        "protocol": "bounded-funds-final-acceptance-v1",
        "status": "INCOMPLETE",
        "run_id": owner,
        "database": database,
        "producer_path": PRODUCER,
        "producer_sha256": frozen["files"][PRODUCER],
        "source_before": frozen,
        "commands": [],
        "started_at": datetime.now(UTC).isoformat(),
        "task_closed": False,
    }
    created = False
    artifact_records: list[dict[str, Any]] = []
    runs: dict[str, Any] = {}
    new_checks: list[dict[str, Any]] = []
    formal_before: dict[str, Any] | None = None
    formal_after: dict[str, Any] | None = None

    def artifact(path: Path, run_id: str, role: str = "RESULT") -> str:
        identity = "final_" + sha(str(path.relative_to(ROOT)).encode())[:24]
        raw = path.read_bytes()
        runs[run_id] = {
            "purpose": "MVP_ACCEPTANCE",
            "source_sha256": frozen["source_sha256"],
            "source_context": "CURRENT_SOURCE",
        }
        artifact_records.append(
            {
                "artifact_id": identity,
                "path": path.relative_to(ROOT).as_posix(),
                "role": role,
                "run_id": run_id,
                "purpose": "MVP_ACCEPTANCE",
                "source_sha256": frozen["source_sha256"],
                "sha256": sha(raw),
                "size_bytes": len(raw),
                "validator": (
                    "STRICT_JSON_V1"
                    if path.suffix == ".json"
                    else "NATIVE_PROOF_ONLY"
                    if path.suffix == ".gz"
                    else "UTF8_TEXT_V1"
                ),
            }
        )
        return identity

    def observe(arguments: list[str], label: str, env: dict[str, str] | None = None) -> bytes:
        print("FINAL_PHASE=" + label, flush=True)
        started = datetime.now(UTC)
        result = subprocess.run(arguments, cwd=ROOT, env=env or environment, capture_output=True)
        safe = (result.stdout + result.stderr).replace(password.encode(), b"[REDACTED]")
        log = directory / (label + ".log")
        with log.open("xb") as output:
            output.write(safe)
        state["commands"].append(
            {
                "argv": arguments,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "exit_code": result.returncode,
                "log": log.relative_to(ROOT).as_posix(),
                "log_sha256": sha(safe),
            }
        )
        need(result.returncode == 0, f"Actual {label} failed; original log retained")
        need(source_state(ROOT) == frozen, "Source or HEAD drift during actual command")
        return result.stdout

    def scoped(
        command: list[str], label: str, env: dict[str, str] | None = None
    ) -> tuple[Path, dict[str, Any], dict[str, str]]:
        wrapper = [
            sys.executable,
            str(ROOT / "scripts/run_scoped_check.py"),
            "--task",
            "W1",
            "--label",
            owner + "-" + label,
        ]
        for prefix in sorted(
            set(["apps/", "scripts/", "deploy/", "packages/contracts/", "docs/spec/"])
            | SOURCE_CONFIG
        ):
            wrapper += ["--source-prefix", prefix]
        raw = observe([*wrapper, "--", *command], label, env)
        output = labeled_wrapper_output(raw, owner + "-" + label, ROOT)
        manifest = obj(decode((output / "manifest.json").read_bytes()), "actual scoped manifest")
        need(
            manifest.get("command") == command
            and manifest.get("label") == owner + "-" + label
            and manifest.get("status") == "PASSED"
            and type(manifest.get("exit_code")) is int
            and manifest.get("exit_code") == 0
            and manifest.get("all_source_stable") is True
            and manifest.get("git_head") == frozen["git_head"],
            "Actual scoped command/source mismatch",
        )
        before = obj(decode((output / "source.before.json").read_bytes()), "actual wrapper before")
        after = obj(decode((output / "source.after.json").read_bytes()), "actual wrapper after")
        need(
            before == after
            and all(before.get(name) == digest for name, digest in frozen["files"].items())
            and manifest.get("log_sha256") == sha((output / "output.log").read_bytes()),
            "Actual full wrapper source or output hash differs",
        )
        refs = {
            key: artifact(
                output / name,
                manifest["run_id"],
                "COMMAND_LOG" if key == "log" else "COMMAND_MANIFEST",
            )
            for key, name in (
                ("manifest", "manifest.json"),
                ("source_before", "source.before.json"),
                ("source_after", "source.after.json"),
                ("log", "output.log"),
            )
        }
        return output, manifest, refs

    def task(target: str) -> tuple[dict[str, Any], dict[str, Any]]:
        output, _, refs = scoped(native_command(target), target)
        raw = (output / "output.log").read_bytes()
        identifiers = re.findall(rb"(?m)^run_id=([A-Za-z0-9_-]+)\r?$", raw)
        need(len(identifiers) == 1, "Native task run identity is ambiguous/missing")
        task_run = identifiers[0].decode().strip()
        task_directory = resolve(ROOT, ".runtime/quality/" + task_run)
        task_manifest = obj(
            decode((task_directory / "manifest.json").read_bytes()), "native task manifest"
        )
        need(
            task_manifest.get("run_id") == task_run
            and task_manifest.get("target") == target
            and task_manifest.get("successful") is True
            and task_manifest.get("source_stable") is True,
            "Native task did not complete unchanged",
        )
        for phase in ("source", "source_after"):
            source = obj(task_manifest.get(phase), "native source")
            mapping = obj(source.get("source_sha256"), "native all sources")
            need(
                source.get("git_revision") == frozen["git_head"]
                and all(mapping.get(name) == digest for name, digest in frozen["files"].items()),
                "Native task current full source mismatch",
            )
        for child in seq(task_manifest.get("commands"), "native executed children"):
            need(
                type(child.get("exit_code")) is int and child["exit_code"] == 0,
                "Actual native child failed",
            )
            aware(child.get("started_at"))
            name = plain_relative(child.get("log"))
            path = (task_directory / name).resolve()
            need(
                path.is_relative_to(task_directory) and path.is_file() and path.stat().st_size > 0,
                "Native child original log missing/escaped",
            )
            artifact(path, task_run, "COMMAND_LOG")
        return task_manifest, {
            "scoped": refs,
            "task_manifest": artifact(
                task_directory / "manifest.json", task_run, "COMMAND_MANIFEST"
            ),
        }

    def formal(phase: str) -> dict[str, Any]:
        env = dict(
            environment,
            DATABASE_URL=(
                f"postgresql+psycopg://bounded:{quote(password, safe='')}"
                "@127.0.0.1:54329/bounded_funds"
            ),
        )
        output, _, _ = scoped(
            [
                sys.executable,
                "scripts/verify_formal_preservation.py",
                "--task",
                "W1",
                "--phase",
                phase,
            ],
            "formal-" + phase,
            env,
        )
        lines = (output / "output.log").read_text(encoding="utf-8")
        candidates = [
            obj(decode(line.encode()), "actual formal stdout")
            for line in lines.splitlines()
            if line.startswith('{"status":')
        ]
        need(
            len(candidates) == 1 and candidates[0].get("status") == "PASSED",
            "Actual formal preservation producer did not pass",
        )
        location = Path(candidates[0]["directory"]).resolve()
        need(
            location.is_relative_to((ROOT / "docs/progress/evidence/W1").resolve()),
            "Formal original output escaped W1",
        )
        result = obj(decode((location / "result.json").read_bytes()), "formal capture")
        return {
            "result": result,
            "result_id": artifact(location / "result.json", result["run_id"]),
            "snapshot_id": artifact(location / "snapshot.json.gz", result["run_id"], "AUDIT"),
        }

    try:
        exporter = runpy.run_path(
            str(ROOT / "scripts/export_evidence.py"), run_name="final_recheck"
        )
        # Independently recompute prior semantic gates before any SQL mutation.
        readiness_path, _ = original(ROOT, registration["readiness_package"])
        preflight_request = readiness_path.parent / "request.original.json"
        preflight_output = ROOT / ".runtime/evidence_packages" / (owner + "-readiness-recheck")
        exporter["export_package"](preflight_request, ROOT, frozen["git_head"], preflight_output)
        recomputed = obj(
            decode((preflight_output / "manifest.json").read_bytes()), "fresh semantic readiness"
        )
        prior = {row["requirement_id"]: row for row in recomputed["requirements"]}
        need(
            all(
                prior[name]["status"] == "VERIFIED"
                for name in REQUIRED
                if name != "final_four_commands" and name not in plan["deferred_groups"]
            ),
            "Semantic prerequisite recheck failed before database creation",
        )
        formal_before = formal("before")
        docker_format = (
            '[{"Id":{{json .Id}},"Name":{{json .Name}},"State":{{json .State}},'
            '"Config":{"Labels":{{json .Config.Labels}}},'
            '"NetworkSettings":{{json .NetworkSettings}}}]'
        )
        docker_raw = observe(
            ["docker", "inspect", "--format", docker_format, "bounded-funds-db-1"], "server-inspect"
        )
        container_id, endpoint = docker_endpoint(decode(docker_raw))
        safe_inspect = docker_raw.replace(password.encode(), b"[REDACTED]")
        inspect_path = directory / "server-inspect.json"
        with inspect_path.open("xb") as stream:
            stream.write(safe_inspect)
        inspect_ref = artifact(inspect_path, owner)
        import psycopg
        from psycopg.rows import dict_row

        def connect(name: str, *, autocommit: bool = False) -> Any:
            return psycopg.connect(
                host="127.0.0.1",
                port=54329,
                user="bounded",
                password=password,
                dbname=name,
                autocommit=autocommit,
                row_factory=dict_row,
            )

        def identity(phase: str) -> str:
            with connect(database) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                rows = list(connection.execute(IDENTITY_SQL).fetchall())
            capture = {
                "phase": phase,
                "captured_at": datetime.now(UTC).isoformat(),
                "query": IDENTITY_SQL,
                "rows": rows,
            }
            verify_identity(capture, database, endpoint)
            path = directory / ("seed-identity-" + phase + ".json")
            write_new(path, capture)
            return artifact(path, owner)

        with connect("postgres", autocommit=True) as administration:
            admin_probe = {
                "query": IDENTITY_SQL,
                "captured_at": datetime.now(UTC).isoformat(),
                "rows": list(administration.execute(IDENTITY_SQL).fetchall()),
            }
            verify_identity(admin_probe, "postgres", endpoint)
            observed = administration.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s) AS preexisting",
                (database,),
            ).fetchone()
            need(
                observed is not None and observed["preexisting"] is False,
                "Generated database already exists; never reuse/drop it",
            )
            sql = f'CREATE DATABASE "{database}"'
            administration.execute(sql)
            created = True
        creation = directory / "database-creation.json"
        write_new(
            creation,
            {
                "database": database,
                "owner_run_id": owner,
                "preexisting": False,
                "preexisting_query": (
                    "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s) AS preexisting"
                ),
                "preexisting_rows": [observed],
                "admin_identity": admin_probe,
                "sql": sql,
                "executed_at": datetime.now(UTC).isoformat(),
            },
        )
        creation_ref = artifact(creation, owner)
        command_refs = {}
        _, command_refs["bootstrap"] = task("bootstrap")
        before_ref = identity("before")
        seed_task, command_refs["seed"] = task("seed")
        after_ref = identity("after")
        isolation_path = directory / "seed-isolation.json"
        write_new(
            isolation_path,
            {
                "protocol": "bounded-funds-final-isolation-v1",
                "producer_path": PRODUCER,
                "producer_sha256": frozen["files"][PRODUCER],
                "database": database,
                "seed_task_run_id": seed_task["run_id"],
                "owner_run_id": owner,
                "engine_target": {"host": "127.0.0.1", "port": 54329, "database": database},
                "server_binding": {
                    "mode": "HOST_PUBLISHED_DOCKER_V1",
                    "container_id": container_id,
                    "docker_inspect": inspect_ref,
                },
                "identity_before": before_ref,
                "identity_after": after_ref,
                "creation_original": creation_ref,
            },
        )
        isolation_ref = artifact(isolation_path, owner)
        check_task, command_refs["check"] = task("check")
        check_deferred = set(plan["deferred_groups"]) & CHECK_DEFERRED
        if check_deferred:
            output_path, output_raw = original(ROOT, check_task.get("acceptance_outputs"))
            need(
                output_path.parent.name == check_task["run_id"],
                "Native acceptance output belongs to another check run",
            )
            bundle = obj(decode(output_raw), "actual native acceptance bundle")
            need(
                bundle.get("protocol") == OUTPUT_PROTOCOL
                and bundle.get("producer_path") == "scripts/tasks.py"
                and bundle.get("producer_sha256") == frozen["files"]["scripts/tasks.py"]
                and bundle.get("owner_run_id") == owner
                and bundle.get("task_run_id") == check_task["run_id"]
                and bundle.get("source") == frozen,
                "Native deferred outputs source/run mismatch",
            )
            checks = seq(bundle.get("checks"), "actual deferred semantic inputs")
            need(
                {row["requirement_id"] for row in checks} == check_deferred
                and len(checks) == len(check_deferred),
                "Actual deferred groups incomplete/duplicated",
            )
            artifact_records.extend(seq(bundle.get("artifacts"), "actual deferred originals"))
            runs.update(obj(bundle.get("runs"), "original deferred run bindings"))
            new_checks.extend(checks)
        formal_after = formal("after")
        need(
            formal_before["result"]["snapshot"] == formal_after["result"]["snapshot"]
            or formal_before["result"]["snapshot"]["sha256"]
            == formal_after["result"]["snapshot"]["sha256"],
            "Actual formal business history changed",
        )
        if "formal_history_preservation" in plan["deferred_groups"]:
            anchor = registration["formal_anchor"]
            baseline_path, _ = original(ROOT, anchor["baseline"])
            index_path, _ = original(ROOT, anchor["w0_index"])
            original_request = plan["template"]
            by_path = {
                entry["path"]: entry["artifact_id"] for entry in original_request["artifacts"]
            }
            need(
                baseline_path.relative_to(ROOT).as_posix() in by_path
                and index_path.relative_to(ROOT).as_posix() in by_path,
                "Original template omits W0 historical anchors/index",
            )
            original_check = next(
                row
                for row in original_request["checks"]
                if row["requirement_id"] == "formal_history_preservation"
            )
            replacement = copy.deepcopy(original_check)
            replacement["inputs"].update(
                before_result=formal_before["result_id"],
                before_snapshot=formal_before["snapshot_id"],
                after_result=formal_after["result_id"],
                after_snapshot=formal_after["snapshot_id"],
            )
            replacement["artifact_ids"] = sorted(
                set(replacement["artifact_ids"])
                | {
                    formal_before["result_id"],
                    formal_before["snapshot_id"],
                    formal_after["result_id"],
                    formal_after["snapshot_id"],
                }
            )
            new_checks.append(replacement)
        request = copy.deepcopy(plan["template"])
        request["package_run_id"] = owner + "-export"
        request["output"] = ".runtime/evidence_packages/" + request["package_run_id"]
        existing_ids = {entry["artifact_id"] for entry in request["artifacts"]}
        need(
            not (existing_ids & {entry["artifact_id"] for entry in artifact_records}),
            "New original artifact identity collides",
        )
        request["artifacts"].extend(artifact_records)
        need(not (set(request["runs"]) & set(runs)), "New original run identity collides")
        request["runs"].update(runs)
        final_check = {
            "requirement_id": "final_four_commands",
            "validator": "MVP_NATIVE_V2",
            "artifact_ids": [entry["artifact_id"] for entry in artifact_records],
            "inputs": {"commands": command_refs, "seed_isolation": isolation_ref},
        }
        replacements = {row["requirement_id"]: row for row in [*new_checks, final_check]}
        request["checks"] = [
            row for row in request["checks"] if row["requirement_id"] not in replacements
        ] + list(replacements.values())
        request_path = directory / "export-request.json"
        write_new(request_path, request)
        environment["BOUNDEDFUNDS_EVIDENCE_REQUEST"] = str(request_path)
        export_task, export_refs = task("export-evidence")
        package_directory = resolve(ROOT, request["output"])
        package = obj(
            decode((package_directory / "manifest.json").read_bytes()), "actual exported package"
        )
        index = seq(
            decode((package_directory / "index.json").read_bytes()), "actual exported index"
        )
        need(
            package.get("package_run_id") == request["package_run_id"],
            "Actual fourth command package run differs",
        )
        scoped_record = next(
            entry
            for entry in artifact_records
            if entry["artifact_id"] == export_refs["scoped"]["manifest"]
        )
        export_scoped = obj(
            decode(resolve(ROOT, scoped_record["path"]).read_bytes()),
            "actual export outer manifest",
        )
        export_children = [
            row
            for row in export_task["commands"]
            if row["command"] == ["uv", "run", "--frozen", "python", "scripts/export_evidence.py"]
        ]
        need(len(export_children) == 1, "Exactly one actual exporter child required")
        export_log = (
            ROOT
            / ".runtime/quality"
            / export_task["run_id"]
            / plain_relative(export_children[0]["log"])
        ).read_bytes()
        result = closure(
            package, index, package_directory, export_task, export_scoped, frozen, export_log
        )
        result.update(
            package_manifest_sha256=sha((package_directory / "manifest.json").read_bytes()),
            package_index_sha256=sha((package_directory / "index.json").read_bytes()),
            export_task_manifest=export_refs["task_manifest"],
            export_scoped=export_refs["scoped"],
        )
        write_new(directory / "closure.json", result)
        state["status"] = "FOUR_COMMANDS_VERIFIED"
    except BaseException as error:
        state.update(
            status="FAILED",
            error={
                "type": type(error).__name__,
                "message": str(error).replace(password, "[REDACTED]"),
            },
        )
    finally:
        if formal_before is not None:
            try:
                # Always attempt an actual RO preservation capture after a
                # failed command too; never infer no writes from an env flag.
                post = formal("checkpoint")
                state["formal_after_commands"] = post
                need(
                    post["result"]["snapshot"]["sha256"]
                    == formal_before["result"]["snapshot"]["sha256"],
                    "Actual final formal history changed",
                )
            except BaseException as error:
                state.update(
                    status="FAILED",
                    formal_after_error={
                        "type": type(error).__name__,
                        "message": str(error).replace(password, "[REDACTED]"),
                    },
                )
        if created:
            try:
                need(
                    re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None,
                    "Exact owned cleanup target required",
                )
                with connect("postgres", autocommit=True) as administration:
                    administration.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
                    absent = administration.execute(
                        "SELECT NOT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s) AS absent",
                        (database,),
                    ).fetchone()
                need(
                    absent is not None and absent["absent"] is True,
                    "Owned database cleanup absence unverified",
                )
                state["cleanup"] = {
                    "database": database,
                    "status": "VERIFIED_ABSENT",
                    "captured_at": datetime.now(UTC).isoformat(),
                    "rows": [absent],
                }
            except BaseException as error:
                state.update(
                    status="FAILED",
                    cleanup={
                        "database": database,
                        "status": "UNVERIFIED",
                        "error_type": type(error).__name__,
                    },
                )
        else:
            state["cleanup"] = {"database": database, "status": "NOT_CREATED_NO_DROP"}
        state["source_after"] = source_state(ROOT)
        if state["source_after"] != frozen:
            state["status"] = "SOURCE_CHANGED"
        state["finished_at"] = datetime.now(UTC).isoformat()
        write_new(directory / "manifest.json", state)
    print(
        encoded(
            {
                "status": state["status"],
                "directory": directory.relative_to(ROOT).as_posix(),
                "task_closed": False,
            }
        ).decode(),
        end="",
    )
    return 0 if state["status"] == "FOUR_COMMANDS_VERIFIED" else 1


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
