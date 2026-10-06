"""Pure TOOL_TEST_ONLY wiring fixtures; no database, Docker, browser or acceptance run."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts import w1_final_acceptance as final

PROJECT = Path(__file__).resolve().parents[2]
AT = "2026-10-05T01:00:00+00:00"
DB = "bf_test_" + "a" * 32


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(final.encoded(value))


def descriptor(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": final.sha(path.read_bytes())}


@pytest.fixture
def sandbox() -> Path:
    # Project-local disposable TOOL_ONLY fixtures avoid the host's unavailable tmpdir ACL.
    path = PROJECT / ".runtime/w1-final-pure-fixtures" / uuid4().hex
    path.mkdir(parents=True)
    return path


class ReadyFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        for relative, content in {
            "scripts/export_evidence.py": "UNSUPPORTED_NATIVE = {}\n",
            "scripts/verify_formal_preservation.py": "# synthetic fixture only\n",
            "scripts/tasks.py": (
                f"W1_FINAL_OUTPUT_PROTOCOL = {final.OUTPUT_PROTOCOL!r}\n"
                "W1_FINAL_OUTPUT_GROUPS = ('backend_coverage_and_properties',)\n"
                "def source_state():\n    configuration = {'.dockerignore'}\n"
                "def final_acceptance_outputs():\n    print('TOOL_ONLY')\n"
                "final_acceptance_outputs()\n"
            ),
            ".dockerignore": "synthetic_fixture_only\n",
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        files = {
            name: final.sha((root / name).read_bytes())
            for name in (
                "scripts/export_evidence.py",
                "scripts/tasks.py",
                ".dockerignore",
                "scripts/verify_formal_preservation.py",
            )
        }
        self.source: dict[str, Any] = {
            "git_head": "a" * 40,
            "files": files,
            "source_sha256": final.sha(
                json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
            ),
            "all_source_files": files,
        }
        self.template: dict[str, Any] = {
            "manifest_version": "bounded-funds-evidence-request-v1",
            "purpose": "MVP_ACCEPTANCE",
            "source": self.package_source(),
        }
        self.template_path = root / ".runtime/template/request.json"
        dump(self.template_path, self.template)
        self.directory = root / ".runtime/evidence_packages/pure-only"
        dump(self.directory / "request.original.json", self.template)
        self.package: dict[str, Any] = {
            "manifest_version": "bounded-funds-evidence-package-v1",
            "purpose": "MVP_ACCEPTANCE",
            "synthetic_tool_only": False,
            "source": self.package_source(),
            "current_cli_execution": {
                "exporter_sha256": files["scripts/export_evidence.py"],
                "actual_argv": ["scripts/export_evidence.py"],
                "started_at": AT,
            },
            "requirements": [
                {"requirement_id": name, "status": "VERIFIED"} for name in final.REQUIRED
            ],
        }
        self.index: list[dict[str, Any]] = [self.indexed("request.original.json")]
        dump(root / "docs/baseline.json", {"classification": "TOOL_ONLY"})
        dump(root / "docs/w0-index.json", {"classification": "TOOL_ONLY"})
        self.registration: dict[str, Any] = {
            "protocol": final.PROTOCOL,
            "purpose": "MVP_ACCEPTANCE",
            "source": self.source,
            "server": {
                "host": "127.0.0.1",
                "port": 54329,
                "username": "bounded",
                "formal_database": "bounded_funds",
                "admin_database": "postgres",
                "container": "bounded-funds-db-1",
            },
            "export_request_template": descriptor(root, self.template_path),
            "deferred_groups": {},
            "formal_anchor": {
                "baseline": descriptor(root, root / "docs/baseline.json"),
                "w0_index": descriptor(root, root / "docs/w0-index.json"),
            },
        }
        self.refresh()

    def package_source(self) -> dict[str, Any]:
        return {key: self.source[key] for key in ("git_head", "files", "source_sha256")}

    def indexed(self, name: str) -> dict[str, Any]:
        raw = (self.directory / name).read_bytes()
        return {"path": name, "sha256": final.sha(raw), "size_bytes": len(raw)}

    def refresh(self) -> None:
        dump(self.directory / "manifest.json", self.package)
        dump(self.directory / "index.json", self.index)
        self.registration.update(
            readiness_package=descriptor(self.root, self.directory / "manifest.json"),
            readiness_index=descriptor(self.root, self.directory / "index.json"),
        )

    def plan(self) -> dict[str, Any]:
        return final.validate_plan(self.registration, self.root, self.source)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/etc",
        "../docs/x",
        "docs/../x",
        "C:/x",
        "a//b",
        "docs/.env",
        ".git/a",
        "docs/NUL.json",
        "docs/x. ",
        "a\\..\\b",
    ],
)
def test_relative_path_rejects_private_aliases(name: str) -> None:
    with pytest.raises(final.Refused):
        final.plain_relative(name)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_original_strict_json(raw: bytes) -> None:
    with pytest.raises(final.Refused):
        final.decode(raw)


def test_pure_validation_never_calls_execution(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = ReadyFixture(sandbox)
    path = sandbox / ".runtime/registration.json"
    dump(path, fixture.registration)
    monkeypatch.setattr(final, "ROOT", sandbox)
    monkeypatch.setattr(final, "source_state", lambda root: fixture.source)
    monkeypatch.setattr(
        final, "execute", lambda *args: pytest.fail("SQL/process path must not run")
    )
    assert final.main(["--prerequisites", path.relative_to(sandbox).as_posix()]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VALIDATED_NOT_RUN" and result["ready"] is True
    assert result["task_closed"] is False


def test_missing_gate_refuses_explicit_run_before_any_capability(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = ReadyFixture(sandbox)
    fixture.package["requirements"][1]["status"] = "MISSING"
    fixture.refresh()
    path = sandbox / ".runtime/registration.json"
    dump(path, fixture.registration)
    monkeypatch.setattr(final, "ROOT", sandbox)
    monkeypatch.setattr(final, "source_state", lambda root: fixture.source)
    monkeypatch.setattr(final, "execute", lambda *args: pytest.fail("must refuse before execution"))
    assert final.main(["--prerequisites", path.relative_to(sandbox).as_posix(), "--run"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED_NOT_RUN"


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "localhost"),
        ("host", "192.0.2.1"),
        ("port", 5432),
        ("formal_database", DB),
        ("username", "postgres"),
    ],
)
def test_server_override_refused(sandbox: Path, field: str, value: Any) -> None:
    fixture = ReadyFixture(sandbox)
    fixture.registration["server"][field] = value
    with pytest.raises(final.Refused):
        fixture.plan()


@pytest.mark.parametrize("mutation", ["TOOL", "DEV", "head", "source", "duplicate", "missing"])
def test_package_claim_cannot_replace_current_originals(sandbox: Path, mutation: str) -> None:
    fixture = ReadyFixture(sandbox)
    if mutation == "TOOL":
        fixture.package["synthetic_tool_only"] = True
    elif mutation == "DEV":
        fixture.package["purpose"] = "DEVELOPMENT"
    elif mutation == "head":
        fixture.registration["source"] = {**fixture.source, "git_head": "b" * 40}
    elif mutation == "source":
        fixture.package["source"] = {**fixture.package_source(), "source_sha256": "b" * 64}
    elif mutation == "duplicate":
        fixture.package["requirements"].append(fixture.package["requirements"][0])
    else:
        fixture.package["requirements"].pop()
    fixture.refresh()
    with pytest.raises(final.Refused):
        fixture.plan()


def test_index_bytes_not_manifest_flags(sandbox: Path) -> None:
    fixture = ReadyFixture(sandbox)
    assert fixture.plan()["ready"] is True
    (fixture.directory / "request.original.json").write_bytes(b"changed")
    with pytest.raises(final.Refused, match="bytes changed"):
        fixture.plan()


def test_deferred_adapter_source_hash_and_implemented_group(sandbox: Path) -> None:
    fixture = ReadyFixture(sandbox)
    name = "backend_coverage_and_properties"
    next(row for row in fixture.package["requirements"] if row["requirement_id"] == name)[
        "status"
    ] = "MISSING"
    fixture.registration["deferred_groups"][name] = {
        "status": "DEFERRED_FROM_CURRENT_CHECK",
        "producer_path": "scripts/tasks.py",
        "producer_sha256": fixture.source["files"]["scripts/tasks.py"],
        "protocol": final.OUTPUT_PROTOCOL,
    }
    fixture.refresh()
    assert fixture.plan()["ready"] is True
    fixture.registration["deferred_groups"][name]["producer_sha256"] = "b" * 64
    with pytest.raises(final.Refused):
        fixture.plan()


def test_unimplemented_current_group_is_missing_not_boolean_ready(sandbox: Path) -> None:
    fixture = ReadyFixture(sandbox)
    name = "audit_chain"
    next(row for row in fixture.package["requirements"] if row["requirement_id"] == name)[
        "status"
    ] = "UNVERIFIED"
    fixture.registration["deferred_groups"][name] = {
        "status": "DEFERRED_FROM_CURRENT_CHECK",
        "producer_path": "scripts/tasks.py",
        "producer_sha256": fixture.source["files"]["scripts/tasks.py"],
        "protocol": final.OUTPUT_PROTOCOL,
    }
    fixture.refresh()
    plan = fixture.plan()
    assert plan["ready"] is False and any(name in row for row in plan["missing_prerequisites"])


def test_only_permitted_six_checks_and_formal_can_defer(sandbox: Path) -> None:
    fixture = ReadyFixture(sandbox)
    fixture.registration["deferred_groups"]["fourteen_metrics"] = {
        "status": "DEFERRED_FROM_CURRENT_CHECK"
    }
    with pytest.raises(final.Refused):
        fixture.plan()


def test_child_environment_removes_inherited_financial_and_compose_controls(sandbox: Path) -> None:
    inherited = {
        "PATH": "native",
        "BF_RESET": "1",
        "bf_cache": "1",
        "COMPOSE_FILE": "evil",
        "PGPASSWORD": "old",
        "DATABASE_URL": "formal",
        "PYTEST_ADDOPTS": "--cov-append",
        "COVERAGE_FILE": "old",
        "W1_FINAL_DB_PASSWORD": "sensitive",
        "PYTHONHOME": "old",
    }
    child = final.child_environment(inherited, "p@ss$ word", DB, sandbox / "context.json")
    assert child["PATH"] == "native"
    assert all(key not in child for key in inherited if key not in {"PATH", "DATABASE_URL"})
    assert child["DATABASE_URL"] != inherited["DATABASE_URL"]
    assert child["DATABASE_URL"].endswith("@127.0.0.1:54329/" + DB)
    assert "p%40ss%24%20word" in child["DATABASE_URL"]


@pytest.mark.parametrize(
    "name", ["bounded_funds", "bf_test_a", 'bf_test_";DROP', "bf_test_" + "G" * 32]
)
def test_child_owns_only_generated_uuid_database(sandbox: Path, name: str) -> None:
    with pytest.raises(final.Refused):
        final.child_environment({}, "secret", name, sandbox / "context.json")


def inspection() -> list[dict[str, Any]]:
    return [
        {
            "Id": "d" * 64,
            "Name": "/bounded-funds-db-1",
            "State": {"Running": True},
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "bounded-funds",
                    "com.docker.compose.service": "db",
                }
            },
            "NetworkSettings": {
                "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54329"}]},
                "Networks": {"bounded": {"IPAddress": "172.18.0.2"}},
            },
        }
    ]


@pytest.mark.parametrize(
    "mutation", ["public", "port", "name", "owner", "not_running", "two_networks"]
)
def test_docker_native_binding_rejects_unsafe_or_ambiguous_target(mutation: str) -> None:
    rows = inspection()
    item = rows[0]
    if mutation == "public":
        item["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
    elif mutation == "port":
        item["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = "5432"
    elif mutation == "name":
        item["Name"] = "/other"
    elif mutation == "owner":
        item["Config"]["Labels"]["com.docker.compose.project"] = "other"
    elif mutation == "not_running":
        item["State"]["Running"] = False
    else:
        item["NetworkSettings"]["Networks"]["second"] = {"IPAddress": "172.19.0.2"}
    with pytest.raises(final.Refused):
        final.docker_endpoint(rows)


def identity() -> dict[str, Any]:
    return {
        "query": final.IDENTITY_SQL,
        "captured_at": AT,
        "rows": [
            {
                "database": DB,
                "server_address": "172.18.0.2/32",
                "server_port": 5432,
                "database_user": "bounded",
            }
        ],
    }


def test_actual_sql_internal_endpoint_is_preserved() -> None:
    assert final.docker_endpoint(inspection()) == ("d" * 64, "172.18.0.2")
    probe = identity()
    final.verify_identity(probe, DB, "172.18.0.2")
    assert probe["rows"][0]["server_port"] == 5432


def test_outer_wrapper_uses_exact_current_label_without_guessing_latest(sandbox: Path) -> None:
    root = sandbox / "docs/progress/evidence/W1"
    outer = root / "owner-check-original"
    inner = root / "native-backend-newer"
    raw = (
        "EVIDENCE_DIRECTORY=" + str(outer) + "\nEVIDENCE_DIRECTORY=" + str(inner) + "\n"
    ).encode()
    assert final.labeled_wrapper_output(raw, "owner-check", sandbox) == outer.resolve()
    with pytest.raises(final.Refused):
        final.labeled_wrapper_output(
            raw + ("EVIDENCE_DIRECTORY=" + str(outer) + "\n").encode(), "owner-check", sandbox
        )
    with pytest.raises(final.Refused):
        final.labeled_wrapper_output(raw, "absent-check", sandbox)


@pytest.mark.parametrize("mutation", ["formal", "host", "port", "user", "naive", "two_rows"])
def test_sql_identity_refuses_wrong_database_or_claimed_client_endpoint(mutation: str) -> None:
    probe = identity()
    if mutation == "formal":
        probe["rows"][0]["database"] = "bounded_funds"
    elif mutation == "host":
        probe["rows"][0]["server_address"] = "127.0.0.1"
    elif mutation == "port":
        probe["rows"][0]["server_port"] = 54329
    elif mutation == "user":
        probe["rows"][0]["database_user"] = "postgres"
    elif mutation == "naive":
        probe["captured_at"] = "2026-10-05T01:00:00"
    else:
        probe["rows"].append(copy.deepcopy(probe["rows"][0]))
    with pytest.raises(final.Refused):
        final.verify_identity(probe, DB, "172.18.0.2")


def task_namespace(root: Path) -> dict[str, Any]:
    tree = ast.parse((PROJECT / "scripts/tasks.py").read_text(encoding="utf-8"))
    body: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))
    ]
    body.extend(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id.startswith("W1_FINAL_OUTPUT_")
            for target in node.targets
        )
    )
    namespace: dict[str, Any] = {
        "ROOT": root,
        "RUN_ID": "native-pure-only",
        "RUN_DIRECTORY": root / ".runtime/quality/native-pure-only",
        "COMMANDS": [],
        "ACCEPTANCE_GROUPS": {},
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
            "tasks_fixture",
            "exec",
        ),
        namespace,
    )
    return namespace


def test_tasks_wrapper_executes_original_arguments_and_rejects_false_exit(sandbox: Path) -> None:
    ns = task_namespace(sandbox)
    mapping = {".dockerignore": "a" * 64}
    ns["source_state"] = lambda: {"git_revision": "a" * 40, "source_sha256": mapping}
    ns["command"] = lambda *args: ["C:/native/pnpm.cmd", *args[1:]]
    captured = []

    def fake_run(*args: str) -> None:
        captured.append(args)
        actual = list(args[args.index("--") + 1 :])
        directory = sandbox / "docs/progress/evidence/W1/native-pure-only-frontend-pure-wrapper"
        dump(directory / "source.before.json", mapping)
        dump(directory / "source.after.json", mapping)
        (directory / "output.log").write_text(
            "TOOL_ONLY actual synthetic child\n", encoding="utf-8"
        )
        dump(
            directory / "manifest.json",
            {
                "run_id": "pure-scoped",
                "label": "native-pure-only-frontend",
                "command": actual,
                "status": "PASSED",
                "exit_code": 0,
                "git_head": "a" * 40,
                "all_source_stable": True,
                "log_sha256": hashlib.sha256((directory / "output.log").read_bytes()).hexdigest(),
            },
        )
        native = ns["RUN_DIRECTORY"]
        native.mkdir(parents=True, exist_ok=True)
        (native / "01-uv.log").write_text(
            "EVIDENCE_DIRECTORY="
            + str(directory)
            + "\nEVIDENCE_DIRECTORY="
            + str(sandbox / "docs/progress/evidence/W1/unrelated-inner-wrapper")
            + "\n",
            encoding="utf-8",
        )
        ns["COMMANDS"].append({"log": "01-uv.log"})

    ns["run"] = fake_run
    result = ns["scoped_run"]("frontend", "pnpm", "--dir", "apps/web", "test")
    assert result["requested_argv"] == ["pnpm", "--dir", "apps/web", "test"]
    assert captured[0][-5:] == ("--", "C:/native/pnpm.cmd", "--dir", "apps/web", "test")
    assert ".dockerignore" in captured[0] and "docs/spec/" in captured[0]
    path = result["paths"]["manifest"]
    original_run = ns["run"]

    def false_exit(*args: str) -> None:
        original_run(*args)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["exit_code"] = False
        dump(path, value)

    ns["run"] = false_exit
    with pytest.raises(RuntimeError, match="scoped argv"):
        ns["scoped_run"]("frontend", "pnpm", "--dir", "apps/web", "test")


def test_backend_full_original_cov_argv_and_local_env_restore(sandbox: Path) -> None:
    ns = task_namespace(sandbox)
    calls = []

    def fake_scoped(label: str, *args: str) -> dict[str, Any]:
        calls.append((label, args))
        return {"run_id": "pure"}

    ns["scoped_run"] = fake_scoped
    ns["uv"] = lambda *args: calls.append(("verify", args))
    prior = ns["os"].environ.get("COVERAGE_FILE")
    ns["os"].environ["COVERAGE_FILE"] = "original-only"
    try:
        ns["backend_acceptance"]()
        assert ns["os"].environ["COVERAGE_FILE"] == "original-only"
    finally:
        if prior is None:
            ns["os"].environ.pop("COVERAGE_FILE", None)
        else:
            ns["os"].environ["COVERAGE_FILE"] = prior
    args = calls[0][1]
    assert args[:7] == ("uv", "run", "--frozen", "python", "-m", "pytest", "apps/api/app/tests")
    assert "scripts/tests" in args and "--cov" in args
    assert "--cov-append" not in args
    assert "backend_coverage_and_properties" in ns["ACCEPTANCE_GROUPS"]


def test_native_bundle_without_context_cannot_label_product_acceptance(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = task_namespace(sandbox)
    monkeypatch.delenv("BOUNDEDFUNDS_FINAL_CONTEXT", raising=False)
    assert ns["final_acceptance_outputs"]() is None
    assert not (ns["RUN_DIRECTORY"] / "acceptance-outputs.json").exists()


def native_bundle_fixture(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], Path]:
    ns = task_namespace(root)
    source_path = root / "scripts/tasks.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes((PROJECT / "scripts/tasks.py").read_bytes())
    files = {"scripts/tasks.py": final.sha(source_path.read_bytes())}
    source = {
        "git_head": "a" * 40,
        "files": files,
        "source_sha256": "f" * 64,
        "all_source_files": files,
    }
    ns["source_state"] = lambda: {"git_revision": source["git_head"], "source_sha256": files}
    context = root / "docs/progress/evidence/W1/pure/context.json"
    dump(
        context,
        {
            "protocol": "bounded-funds-final-context-v1",
            "owner_run_id": "pure-owner",
            "database": DB,
            "source": source,
            "deferred_groups": ["frontend_interactions"],
        },
    )
    monkeypatch.setenv("BOUNDEDFUNDS_FINAL_CONTEXT", str(context))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://bounded:private@127.0.0.1:54329/" + DB)
    directory = root / "docs/progress/evidence/W1/pure-scoped"
    paths = {
        "manifest": directory / "manifest.json",
        "source_before": directory / "source.before.json",
        "source_after": directory / "source.after.json",
        "log": directory / "output.log",
    }
    for path in paths.values():
        dump(path, {"classification": "TOOL_ONLY"})
    ns["register_acceptance_group"](
        "frontend_interactions", {"run_id": "pure-scoped", "paths": paths}
    )
    ns["RUN_DIRECTORY"].mkdir(parents=True)
    return ns, context


def test_native_bundle_preserves_exact_task_and_original_bytes(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns, _ = native_bundle_fixture(sandbox, monkeypatch)
    result = ns["final_acceptance_outputs"]()
    path = sandbox / result["path"]
    assert result["sha256"] == final.sha(path.read_bytes())
    bundle = json.loads(path.read_bytes())
    assert bundle["owner_run_id"] == "pure-owner" and bundle["task_run_id"] == ns["RUN_ID"]
    assert bundle["producer_sha256"] == final.sha((sandbox / "scripts/tasks.py").read_bytes())
    assert len(bundle["artifacts"]) == 4 and bundle["checks"][0]["validator"] == "MVP_NATIVE_V2"
    for row in bundle["artifacts"]:
        raw = (sandbox / row["path"]).read_bytes()
        assert row["sha256"] == final.sha(raw) and row["size_bytes"] == len(raw)
    with pytest.raises(FileExistsError):
        ns["final_acceptance_outputs"]()


@pytest.mark.parametrize("mutation", ["formal_db", "stale_source", "missing_audit"])
def test_native_bundle_requires_own_database_current_source_and_every_adapter(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    ns, path = native_bundle_fixture(sandbox, monkeypatch)
    context = json.loads(path.read_bytes())
    if mutation == "formal_db":
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+psycopg://bounded:x@127.0.0.1:54329/bounded_funds"
        )
    elif mutation == "stale_source":
        context["source"]["git_head"] = "b" * 40
    else:
        context["deferred_groups"].append("audit_chain")
    dump(path, context)
    with pytest.raises(RuntimeError):
        ns["final_acceptance_outputs"]()
    assert not (ns["RUN_DIRECTORY"] / "acceptance-outputs.json").exists()


@pytest.mark.parametrize("mutation", ["NONE", "bytes", "development", "old_source", "run"])
def test_nested_adapter_retains_actual_original_run_binding(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    ns, context_path = native_bundle_fixture(sandbox, monkeypatch)
    context = json.loads(context_path.read_bytes())
    context["deferred_groups"] = ["audit_chain"]
    dump(context_path, context)
    path = sandbox / "docs/progress/evidence/W1/pure-observer/manifest.json"
    dump(path, {"classification": "TOOL_ONLY"})
    record = {
        "artifact_id": "observer-original",
        "path": path.relative_to(sandbox).as_posix(),
        "role": "RESULT",
        "run_id": "original-observer-run",
        "purpose": "MVP_ACCEPTANCE",
        "source_sha256": "f" * 64,
        "sha256": final.sha(path.read_bytes()),
        "size_bytes": path.stat().st_size,
        "validator": "STRICT_JSON_V1",
    }
    runs = {
        "original-observer-run": {
            "purpose": "MVP_ACCEPTANCE",
            "source_context": "CURRENT_SOURCE",
            "source_sha256": "f" * 64,
        }
    }
    if mutation == "bytes":
        record["sha256"] = "b" * 64
    elif mutation == "development":
        record["purpose"] = "DEVELOPMENT"
    elif mutation == "old_source":
        runs["original-observer-run"]["source_sha256"] = "b" * 64
    elif mutation == "run":
        record["run_id"] = "another-run"
    check = {
        "requirement_id": "audit_chain",
        "validator": "MVP_NATIVE_V2",
        "artifact_ids": ["observer-original"],
        "inputs": {"observer": "observer-original"},
    }
    ns["register_acceptance_originals"]("audit_chain", check, [record], runs)
    if mutation == "NONE":
        result = ns["final_acceptance_outputs"]()
        bundle = json.loads((sandbox / result["path"]).read_bytes())
        assert bundle["artifacts"][0]["run_id"] == "original-observer-run"
        assert bundle["checks"][0]["inputs"] == check["inputs"]
    else:
        with pytest.raises(RuntimeError):
            ns["final_acceptance_outputs"]()


def test_scoped_config_records_only_existing_physical_files(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import run_scoped_check

    existing = [".dockerignore", "docs/spec/mvp-coverage-scope.json"]
    for name in existing:
        path = sandbox / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TOOL_ONLY")
    listing = "\0".join([*existing, "docs/spec/mvp-coverage.ini"]) + "\0"
    monkeypatch.setattr(run_scoped_check, "ROOT", sandbox)

    class Result:
        stdout = listing.encode()

    monkeypatch.setattr("scripts.run_scoped_check.subprocess.run", lambda *args, **kwargs: Result())
    captured = run_scoped_check.sources()
    assert set(captured) == set(existing)
    assert set(captured.values()) == {final.sha(b"TOOL_ONLY")}


class CloseFixture:
    def __init__(self, root: Path) -> None:
        self.directory = root / "closed-pure"
        self.directory.mkdir()
        self.source: dict[str, Any] = {
            "git_head": "a" * 40,
            "files": {"scripts/export_evidence.py": "e" * 64},
            "source_sha256": "f" * 64,
        }
        self.package: dict[str, Any] = {
            "package_run_id": "pure-only-export",
            "package_status": "EXPORTED",
            "exit_code": 0,
            "purpose": "MVP_ACCEPTANCE",
            "synthetic_tool_only": False,
            "source": self.source,
            "acceptance_status": "EXTERNAL_EXIT_UNVERIFIED",
            "task_closed": False,
            "current_cli_execution": {
                "actual_argv": ["scripts/export_evidence.py"],
                "exporter_sha256": "e" * 64,
                "started_at": AT,
                "outer_task_exit": "OUTER_TASK_EXIT_PENDING",
            },
            "requirements": [
                {"requirement_id": name, "status": "VERIFIED"} for name in final.REQUIRED
            ],
        }
        source = {"git_revision": self.source["git_head"], "source_sha256": self.source["files"]}
        self.task: dict[str, Any] = {
            "target": "export-evidence",
            "successful": True,
            "source_stable": True,
            "run_id": "pure-task",
            "source": source,
            "source_after": source,
            "commands": [
                {
                    "command": ["uv", "run", "--frozen", "python", "scripts/export_evidence.py"],
                    "exit_code": 0,
                    "started_at": AT,
                }
            ],
        }
        self.scoped: dict[str, Any] = {
            "status": "PASSED",
            "exit_code": 0,
            "all_source_stable": True,
            "git_head": self.source["git_head"],
            "run_id": "pure-scoped",
        }
        dump(self.directory / "request.original.json", {"classification": "TOOL_ONLY"})
        raw = (self.directory / "request.original.json").read_bytes()
        self.index = [
            {"path": "request.original.json", "sha256": final.sha(raw), "size_bytes": len(raw)}
        ]
        self.refresh()

    def refresh(self) -> None:
        dump(self.directory / "manifest.json", self.package)
        dump(self.directory / "index.json", self.index)
        self.log = final.encoded(
            {
                "package_run_id": self.package["package_run_id"],
                "package_status": "EXPORTED",
                "exit_code": 0,
                "package_manifest_sha256": final.sha(
                    (self.directory / "manifest.json").read_bytes()
                ),
                "package_index_sha256": final.sha((self.directory / "index.json").read_bytes()),
            }
        )

    def close(self) -> dict[str, Any]:
        return final.closure(
            self.package, self.index, self.directory, self.task, self.scoped, self.source, self.log
        )


def test_fourth_command_closed_only_after_original_cli_and_byte_recheck(sandbox: Path) -> None:
    fixture = CloseFixture(sandbox)
    result = fixture.close()
    assert result["four_commands_status"] == "VERIFIED" and result["task_closed"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_children",
        "child_failed",
        "false_exit",
        "closed",
        "missing_group",
        "stale_source",
        "wrong_cli",
        "log_sha",
        "index_bytes",
        "duplicate_index",
    ],
)
def test_final_closure_refuses_flags_without_original_exit_and_bytes(
    sandbox: Path, mutation: str
) -> None:
    fixture = CloseFixture(sandbox)
    if mutation == "empty_children":
        fixture.task["commands"] = []
    elif mutation == "child_failed":
        fixture.task["commands"][0]["exit_code"] = 1
    elif mutation == "false_exit":
        fixture.scoped["exit_code"] = False
    elif mutation == "closed":
        fixture.package["task_closed"] = True
    elif mutation == "missing_group":
        fixture.package["requirements"].pop()
    elif mutation == "stale_source":
        fixture.task["source_after"] = {"git_revision": "b" * 40, "source_sha256": {}}
    elif mutation == "wrong_cli":
        fixture.package["current_cli_execution"]["actual_argv"] = ["fake.py"]
    elif mutation == "duplicate_index":
        fixture.index.append(fixture.index[0])
    fixture.refresh()
    if mutation == "log_sha":
        value = json.loads(fixture.log)
        value["package_index_sha256"] = "b" * 64
        fixture.log = final.encoded(value)
    elif mutation == "index_bytes":
        (fixture.directory / "request.original.json").write_bytes(b"changed")
    with pytest.raises(final.Refused):
        fixture.close()
