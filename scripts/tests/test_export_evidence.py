"""TOOL_TEST_ONLY byte-archive fixtures; no financial or acceptance outcome evidence."""

from __future__ import annotations

import ast
import copy
import gzip
import hashlib
import json
import runpy
import struct
import sys
import zlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = runpy.run_path(str(ROOT / "scripts/export_evidence.py"))


def save_request(root: Path, request: dict[str, Any]) -> Path:
    path = root / ".runtime/request.json"
    path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def fixture() -> tuple[Path, dict[str, Any], bytes]:
    # This Windows sandbox cannot read pytest's mode-0700 tmp_path directories.
    # Keep fresh TOOL_TEST_ONLY artifacts under the permitted repository root.
    root = ROOT / ".runtime/tool_tests/export_evidence" / uuid4().hex / "tool-fixture"
    source = root / "apps/api/app/main.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# TOOL_TEST_ONLY source\n")
    original = root / ".runtime/inputs/original.json"
    original.parent.mkdir(parents=True)
    raw = b'{ "status": "FAILED", "reason": "TOOL_TEST_ONLY" }\r\n'
    original.write_bytes(raw)
    files = {name: TOOL["digest"]((root / name).read_bytes()) for name in TOOL["inventory"](root)}
    source_hash = TOOL["source_digest"](files)
    request = {
        "manifest_version": TOOL["VERSION"],
        "package_run_id": "tool-package",
        "purpose": "DEVELOPMENT",
        "status": "PASSED",
        "source": {"git_head": "a" * 40, "files": files, "source_sha256": source_hash},
        "runs": {
            "tool-run": {
                "purpose": "DEVELOPMENT",
                "source_sha256": source_hash,
                "source_context": "CURRENT_SOURCE",
            }
        },
        "artifacts": [
            {
                "artifact_id": "original-failure",
                "role": "FAILURE",
                "path": ".runtime/inputs/original.json",
                "sha256": TOOL["digest"](raw),
                "size_bytes": len(raw),
                "run_id": "tool-run",
                "purpose": "DEVELOPMENT",
                "source_sha256": source_hash,
                "validator": "STRICT_JSON_V1",
            }
        ],
        "checks": [],
    }
    return root, request, raw


def run_export(root: Path, request: dict[str, Any]) -> tuple[dict[str, Any], int]:
    return cast(
        tuple[dict[str, Any], int],
        TOOL["export_package"](save_request(root, request), root, "a" * 40),
    )


def test_archive_preserves_original_failure_bytes_hash_size_and_stays_incomplete(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, raw = fixture
    result, code = run_export(root, request)
    output = Path(result["directory"])
    assert code == result["exit_code"] == 1
    assert result["status"] == "INCOMPLETE" and result["task_closed"] is False
    archived = output / "archive/original-failure/original.json"
    assert archived.read_bytes() == raw == (root / ".runtime/inputs/original.json").read_bytes()
    assert (output / "request.original.json").read_bytes() == (
        root / ".runtime/request.json"
    ).read_bytes()
    report = json.loads((output / "manifest.json").read_bytes())
    assert report["declared_status_not_trusted"] == "PASSED"
    assert set(report["semantic_acceptance_validators"]) == TOOL["SUPPORTED_NATIVE"]
    assert set(report["unsupported_semantic_requirements"]) == set(TOOL["UNSUPPORTED_NATIVE"])
    assert len(report["requirements"]) == len(TOOL["REQUIRED"]) == 18
    assert {row["status"] for row in report["requirements"]} == {"MISSING"}
    assert (
        report["artifacts"][0]["run_binding_status"]
        == "EXPLICIT_DECLARATION_ONLY_NOT_RUN_VALIDATION"
    )
    index = json.loads((output / "index.json").read_bytes())
    for row in index:
        data = (output / row["path"]).read_bytes()
        assert TOOL["digest"](data) == row["sha256"] and len(data) == row["size_bytes"]


def test_all_self_reported_passed_checks_without_semantic_validators_remain_unverified(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    request["purpose"] = "MVP_ACCEPTANCE"
    request["checks"] = [
        {
            "requirement_id": name,
            "artifact_ids": ["original-failure"],
            "validator": "SELF_REPORTED_PASSED",
            "status": "PASSED",
        }
        for name in TOOL["REQUIRED"]
    ]
    result, code = run_export(root, request)
    report = json.loads((Path(result["directory"]) / "manifest.json").read_bytes())
    assert code == 1 and {row["status"] for row in report["requirements"]} == {"UNVERIFIED"}
    assert not report["task_closed"]


def test_historical_failed_original_retains_original_source_run_and_status(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, raw = fixture
    old_hash = "b" * 64
    request["runs"]["tool-run"]["source_sha256"] = old_hash
    request["runs"]["tool-run"]["source_context"] = "HISTORICAL_CONTEXT"
    request["artifacts"][0]["source_sha256"] = old_hash
    request["checks"] = [
        {"requirement_id": "final_four_commands", "artifact_ids": ["original-failure"]}
    ]
    result, code = run_export(root, request)
    output = Path(result["directory"])
    report = json.loads((output / "manifest.json").read_bytes())
    artifact = report["artifacts"][0]
    assert code == 1 and artifact["source_sha256"] == old_hash
    assert artifact["run_id"] == "tool-run" and artifact["source_context"] == "HISTORICAL_CONTEXT"
    assert (output / artifact["archive_path"]).read_bytes() == raw
    assert json.loads(raw)["status"] == "FAILED"
    assert report["requirements"][0]["status"] == "UNVERIFIED"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.json",
        "/outside.json",
        "C:/outside.json",
        "C:outside.json",
        "\\\\server\\share\\outside.json",
        ".runtime/../inputs/original.json",
        ".runtime//inputs/original.json",
        ".runtime/inputs/original.json:stream",
        ".runtime/inputs/original.json.",
        ".runtime/inputs/original.json ",
        ".runtime/.ENV/original.json",
        ".GIT/config",
        "node_modules/original.json",
        ".runtime/NUL.json",
        ".runtime/COM1/file.json",
        ".runtime/file?.json",
    ],
)
def test_traversal_windows_aliases_and_sensitive_paths_are_refused_before_any_output(
    fixture: tuple[Path, dict[str, Any], bytes],
    path: str,
) -> None:
    root, request, _ = fixture
    request["artifacts"][0]["path"] = path
    with pytest.raises(TOOL["RequestError"]):
        run_export(root, request)
    assert not (root / ".runtime/evidence_packages/tool-package").exists()


@pytest.mark.parametrize(
    "change", ["hash", "size", "bool_size", "run", "purpose", "source", "role"]
)
def test_hash_size_role_and_explicit_original_binding_mismatch_are_rejected(
    fixture: tuple[Path, dict[str, Any], bytes],
    change: str,
) -> None:
    root, request, _ = fixture
    artifact = request["artifacts"][0]
    changes = {
        "hash": ("sha256", "0" * 64),
        "size": ("size_bytes", 1),
        "bool_size": ("size_bytes", True),
        "run": ("run_id", "unknown"),
        "purpose": ("purpose", "MVP_ACCEPTANCE"),
        "source": ("source_sha256", "0" * 64),
        "role": ("role", "SUCCESS_BY_NAME"),
    }
    key, value = changes[change]
    artifact[key] = value
    with pytest.raises(TOOL["RequestError"]):
        run_export(root, request)


@pytest.mark.parametrize(
    "change", ["head", "source_inventory", "current_run_hash", "no_source_context"]
)
def test_incomplete_or_changed_current_source_cannot_be_labeled_current(
    fixture: tuple[Path, dict[str, Any], bytes],
    change: str,
) -> None:
    root, request, _ = fixture
    if change == "head":
        request["source"]["git_head"] = "b" * 40
    elif change == "source_inventory":
        request["source"]["files"] = {}
    elif change == "current_run_hash":
        request["runs"]["tool-run"]["source_sha256"] = "b" * 64
    else:
        del request["runs"]["tool-run"]["source_context"]
    with pytest.raises(TOOL["RequestError"]):
        run_export(root, request)


@pytest.mark.parametrize(
    "change", ["duplicate_id", "duplicate_file", "unknown_ref", "duplicate_ref", "duplicate_check"]
)
def test_duplicates_and_unknown_references_cannot_manufacture_independent_evidence(
    fixture: tuple[Path, dict[str, Any], bytes],
    change: str,
) -> None:
    root, request, _ = fixture
    first = request["artifacts"][0]
    if change in {"duplicate_id", "duplicate_file"}:
        second = dict(first)
        second["artifact_id"] = "ORIGINAL-FAILURE" if change == "duplicate_id" else "second"
        request["artifacts"].append(second)
    else:
        refs = ["missing"] if change == "unknown_ref" else ["original-failure"]
        if change == "duplicate_ref":
            refs *= 2
        check = {"requirement_id": "final_four_commands", "artifact_ids": refs}
        request["checks"] = [check, check] if change == "duplicate_check" else [check]
    with pytest.raises(TOOL["RequestError"]):
        run_export(root, request)


def test_missing_original_is_an_explicit_nonzero_diagnostic_not_a_zero_failure_claim(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    (root / ".runtime/inputs/original.json").unlink()
    result, code = run_export(root, request)
    report = json.loads((Path(result["directory"]) / "manifest.json").read_bytes())
    assert code == 1 and report["artifacts"][0]["byte_status"] == "MISSING"
    assert report["artifacts"][0]["archive_path"] is None
    assert report["artifacts"][0]["content_check"]["status"] == "MISSING"


def test_existing_output_is_never_overwritten_and_outside_output_is_refused(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    target = root / ".runtime/evidence_packages/tool-package"
    target.mkdir(parents=True)
    sentinel = target / "original.json"
    sentinel.write_bytes(b"ORIGINAL")
    with pytest.raises(TOOL["RequestError"], match="already exists"):
        run_export(root, request)
    assert sentinel.read_bytes() == b"ORIGINAL"
    request["output"] = ".runtime/inputs/forbidden"
    with pytest.raises(TOOL["RequestError"], match="new child"):
        run_export(root, request)
    assert not (root / request["output"]).exists()


def test_directory_input_unknown_capability_and_empty_failure_jsonl_cannot_validate_outcomes(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    request["artifacts"][0]["path"] = ".runtime/inputs"
    with pytest.raises(TOOL["RequestError"], match="directory"):
        run_export(root, request)
    assert (
        TOOL["content_check"]("FINANCIAL_PASSED", b'{"status":"PASSED"}')["status"] == "UNVERIFIED"
    )
    assert TOOL["content_check"]("STRICT_JSONL_V1", b"\r\n")["status"] == "UNVERIFIED"
    assert TOOL["content_check"]("PNG_HEADER_V1", b"fake-video")["status"] == "UNVERIFIED"


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"value":NaN}', b'{"value":Infinity}'])
def test_duplicate_json_keys_and_nonfinite_values_are_not_accepted(raw: bytes) -> None:
    with pytest.raises(TOOL["RequestError"]):
        TOOL["strict_json"](raw)


def test_source_changed_during_preparation_is_refused_without_output(
    fixture: tuple[Path, dict[str, Any], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, request, _ = fixture
    original_check = TOOL["content_check"]

    def mutate_source(name: str, raw: bytes) -> dict[str, str]:
        (root / "apps/api/app/main.py").write_bytes(b"# changed TOOL_TEST_ONLY\n")
        return cast(dict[str, str], original_check(name, raw))

    monkeypatch.setitem(TOOL["export_package"].__globals__, "content_check", mutate_source)
    with pytest.raises(TOOL["RequestError"], match="SHA256 mismatch"):
        run_export(root, request)
    assert not (root / ".runtime/evidence_packages/tool-package").exists()


def test_resolved_link_cannot_escape_the_repository_evidence_roots(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    outside = root.parent / "outside.json"
    outside.write_bytes(b"TOOL_TEST_ONLY")
    link = root / ".runtime/inputs/link.json"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Native symlink creation unavailable: {error}")
    request["artifacts"][0]["path"] = ".runtime/inputs/link.json"
    with pytest.raises(TOOL["RequestError"], match="escapes repository"):
        run_export(root, request)


def test_no_argument_without_explicit_environment_request_returns_nonzero_before_git(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["export_evidence.py"])
    monkeypatch.delenv("BOUNDEDFUNDS_EVIDENCE_REQUEST", raising=False)
    assert TOOL["main"]() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "REJECTED" and result["task_closed"] is False
    assert "Missing explicit" in result["error"]


def native_fixture(
    root: Path,
    files: dict[str, str],
    original_values: dict[str, Any],
) -> Any:
    prepared = []
    for name, value in original_values.items():
        raw = (
            value
            if isinstance(value, bytes)
            else value.encode("utf-8")
            if isinstance(value, str)
            else json.dumps(value).encode("utf-8")
        )
        path = root / ".runtime/native" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        prepared.append(
            (
                {
                    "artifact_id": name,
                    "resolved_repository_path": path.relative_to(root).as_posix(),
                    "run_id": "tool-run",
                    "source_context": "CURRENT_SOURCE",
                    "purpose": "TOOL_ONLY",
                    "evidence_kind": "TOOL_ONLY",
                    "role": "DIAGNOSTIC",
                },
                raw,
            )
        )
    return TOOL["Originals"](root, files, prepared, "a" * 40, tool_test_only=True)


def scoped_values(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    log = "TOOL_ONLY native command transcript\n1 passed\n"
    source = request["source"]["files"]
    data = {
        "manifest": {
            "run_id": "tool-run",
            "git_head": "a" * 40,
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-10-05T00:00:00+00:00",
            "finished_at": "2026-10-05T00:00:01+00:00",
            "command": ["TOOL_ONLY", "pytest"],
            "log_sha256": TOOL["digest"](log.encode()),
        },
        "before": source,
        "after": source,
        "log": log,
    }
    return data, {
        "manifest": "manifest",
        "source_before": "before",
        "source_after": "after",
        "log": "log",
    }


def test_native_scoped_checker_reads_actual_maps_and_log_not_only_passed_flag(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    values, refs = scoped_values(request)
    proof = native_fixture(root, request["source"]["files"], values)
    original, log = proof.scoped(refs)
    assert original["exit_code"] == 0 and "TOOL_ONLY" in log


def test_native_capture_source_scope_preserves_relevant_binding_with_outer_full_freeze(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    extra = root / "scripts/TOOL_ONLY_unrelated.py"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"# TOOL_ONLY unrelated tool\n")
    files = {
        **request["source"]["files"],
        "scripts/TOOL_ONLY_unrelated.py": TOOL["digest"](extra.read_bytes()),
    }
    proof = native_fixture(root, files, {})
    proof.current_subset(request["source"]["files"], ("apps/api/app/",))
    with pytest.raises(ValueError):
        proof.current_map(request["source"]["files"])
    with pytest.raises(ValueError):
        proof.current_subset({}, ("apps/api/app/",))
    with pytest.raises(ValueError):
        proof.current_subset({"apps/api/app/main.py": "b" * 64}, ("apps/api/app/",))


@pytest.mark.parametrize(
    "mutation", ["exit", "status", "head", "source_missing", "source_drift", "log", "clock"]
)
def test_native_scoped_malformed_or_failed_originals_do_not_turn_into_success(
    fixture: tuple[Path, dict[str, Any], bytes],
    mutation: str,
) -> None:
    root, request, _ = fixture
    values, refs = scoped_values(request)
    if mutation == "exit":
        values["manifest"]["exit_code"] = 1
    elif mutation == "status":
        values["manifest"]["status"] = "FAILED"
    elif mutation == "head":
        values["manifest"]["git_head"] = "b" * 40
    elif mutation == "source_missing":
        values["before"] = values["after"] = {}
    elif mutation == "source_drift":
        values["after"] = {}
    elif mutation == "log":
        values["log"] += "altered"
    else:
        values["manifest"]["finished_at"] = "2026-10-04T00:00:00+00:00"
    proof = native_fixture(root, request["source"]["files"], values)
    with pytest.raises(ValueError):
        proof.scoped(refs)


@pytest.mark.parametrize("requirement", sorted(TOOL["UNSUPPORTED_NATIVE"]))
def test_unimplemented_native_contract_stays_unverified_even_with_original_passed_json(
    fixture: tuple[Path, dict[str, Any], bytes],
    requirement: str,
) -> None:
    root, request, _ = fixture
    request["checks"] = [
        {
            "requirement_id": requirement,
            "artifact_ids": ["original-failure"],
            "validator": TOOL["NATIVE_VALIDATOR"],
            "inputs": {"status": "PASSED", "count": 9999},
        }
    ]
    result, code = run_export(root, request)
    report = json.loads((Path(result["directory"]) / "manifest.json").read_bytes())
    group = next(item for item in report["requirements"] if item["requirement_id"] == requirement)
    assert code == 1 and group["status"] == "UNVERIFIED"
    assert group["reason"] == TOOL["UNSUPPORTED_NATIVE"][requirement]


def test_all_ready_checker_stubs_tool_only_never_become_product_acceptance_or_zero_exit(
    fixture: tuple[Path, dict[str, Any], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This explicitly tests the final guard with checker stubs, not eighteen real proofs.
    root, request, _ = fixture
    request["purpose"] = "TOOL_ONLY"
    request["evidence_kind"] = "TOOL_ONLY"
    request["checks"] = [
        {
            "requirement_id": name,
            "artifact_ids": ["original-failure"],
            "validator": TOOL["NATIVE_VALIDATOR"],
            "inputs": {},
        }
        for name in TOOL["REQUIRED"]
    ]
    monkeypatch.setitem(
        TOOL["export_package"].__globals__,
        "semantic_proof",
        lambda *_: {"evidence_kind": "TOOL_ONLY_CHECKER_STUB"},
    )
    result, code = TOOL["export_package"](
        save_request(root, request), root, "a" * 40, cli_execution={"run_id": "tool-cli-stub"}
    )
    report = json.loads((Path(result["directory"]) / "manifest.json").read_bytes())
    assert {row["status"] for row in report["requirements"]} == {"VERIFIED"}
    assert code == 1 and report["synthetic_tool_only"] is True
    assert report["acceptance_status"] == "INCOMPLETE" and report["task_closed"] is False


def test_native_checker_wont_consume_synthetic_or_unlisted_original_to_close_product(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    values, _ = scoped_values(request)
    tool_proof = native_fixture(root, request["source"]["files"], values)
    product = TOOL["Originals"](root, request["source"]["files"], list(tool_proof.entries.values()))
    with pytest.raises(ValueError, match="TOOL_ONLY"):
        product.original("manifest")
    tool_proof.allowed = {"log"}
    with pytest.raises(ValueError, match="outside"):
        tool_proof.original("manifest")


def test_seed_exit_zero_cannot_replace_missing_actual_isolation_original(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    proof = native_fixture(root, request["source"]["files"], {"flag": {"exit_code": 0}})
    with pytest.raises(TOOL["MissingProof"], match="missing"):
        TOOL["seed_isolation_proof"](
            proof, None, "seed-run", TOOL["aware"]("2026-10-05T00:00:01+00:00")
        )


@pytest.mark.parametrize("mutation", ["source", "method", "original"])
def test_reset_adapter_binding_keeps_frozen_original_and_explicit_decoder_revision(
    fixture: tuple[Path, dict[str, Any], bytes], mutation: str
) -> None:
    root, request, _ = fixture
    name = "scripts/browser_checkpoint_oracles.py"
    raw = (ROOT / name).read_bytes()
    destination = root / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    files = {**request["source"]["files"], name: TOOL["digest"](raw)}
    record = {
        "artifact_id": "adapter",
        "resolved_repository_path": name,
        "source_context": "CURRENT_SOURCE",
        "purpose": "TOOL_ONLY",
        "evidence_kind": "TOOL_ONLY",
    }
    proof = TOOL["Originals"](root, files, [(record, raw)], tool_test_only=True)
    method = {
        "revision": "W1_TYPED_COLUMN_NORMALIZATION_V1",
        "original_path": ".runtime/drive_mvp404_browser.py",
        "original_sha256": TOOL["ORIGINAL_FINANCIAL_ORACLE_SHA256"],
        "adapter_path": name,
        "adapter_sha256": TOOL["TYPED_RESET_ADAPTER_SHA256"],
    }
    if mutation == "source":
        destination.write_bytes(raw + b"# TOOL_ONLY changed decoder\n")
    elif mutation == "method":
        method["revision"] = "SKIP_RESET"
    else:
        method["original_sha256"] = "b" * 64
    with pytest.raises(ValueError):
        TOOL["native_reset_adapter"](proof, "adapter", method)


@pytest.mark.parametrize(
    "mutation", ["none", "formal", "remote", "port", "owner", "preexisting", "sql", "clock"]
)
def test_tool_only_actual_seed_identity_schema_preserves_isolation_negatives(
    fixture: tuple[Path, dict[str, Any], bytes], mutation: str
) -> None:
    # Schema checker fixtures only. No SQL/seed executes; never product isolation proof.
    root, request, _ = fixture
    producer = "scripts/w1_final_acceptance.py"
    path = root / producer
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"# TOOL_ONLY final coordinator placeholder\n")
    files = {**request["source"]["files"], producer: TOOL["digest"](path.read_bytes())}
    database = "bf_test_" + "a" * 32
    query = (
        "SELECT current_database() AS database, inet_server_addr()::text AS server_address, "
        "inet_server_port() AS server_port, current_user AS database_user"
    )
    values: dict[str, Any] = {
        "isolation": {
            "protocol": "bounded-funds-final-isolation-v1",
            "producer_path": producer,
            "producer_sha256": files[producer],
            "database": database,
            "seed_task_run_id": "seed-run",
            "owner_run_id": "tool-run",
            "engine_target": {"host": "127.0.0.1", "port": 54329, "database": database},
            "server_binding": {"mode": "NAMESPACE_LOOPBACK_V1"},
            "identity_before": "before",
            "identity_after": "after",
            "creation_original": "creation",
        },
        "creation": {
            "database": database,
            "owner_run_id": "tool-run",
            "preexisting": False,
            "sql": f'CREATE DATABASE "{database}"',
            "executed_at": "2026-10-05T00:00:00+00:00",
        },
    }
    for phase, second in (("before", "00"), ("after", "02")):
        values[phase] = {
            "phase": phase,
            "query": query,
            "captured_at": f"2026-10-05T00:00:{second}+00:00",
            "rows": [
                {
                    "database": database,
                    "server_address": "127.0.0.1",
                    "server_port": 54329,
                    "database_user": "tool_actor",
                }
            ],
        }
    if mutation == "formal":
        values["isolation"]["database"] = "bounded_funds"
    elif mutation == "remote":
        values["after"]["rows"][0]["server_address"] = "10.0.0.1"
    elif mutation == "port":
        values["after"]["rows"][0]["server_port"] = 5432
    elif mutation == "owner":
        values["isolation"]["owner_run_id"] = "another-run"
    elif mutation == "preexisting":
        values["creation"]["preexisting"] = True
    elif mutation == "sql":
        values["before"]["query"] = "SELECT 'bf_test_aaaa'"
    elif mutation == "clock":
        values["before"]["captured_at"] = "2026-10-05T00:00:03+00:00"
    proof = native_fixture(root, files, values)
    args = (proof, "isolation", "seed-run", TOOL["aware"]("2026-10-05T00:00:01+00:00"))
    if mutation == "none":
        assert TOOL["seed_isolation_proof"](*args)["database"] == database
    else:
        with pytest.raises(ValueError):
            TOOL["seed_isolation_proof"](*args)


def tool_pdf(pages: int) -> bytes:
    """Actual blank TOOL_ONLY PDF syntax; never a proposal/visual quality proof."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for index in range(pages):
        identifier = 3 + index
        kids.append(f"{identifier} 0 R")
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")
    objects[1] = f"<< /Type /Pages /Count {pages} /Kids [{' '.join(kids)}] >>".encode()
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for identifier, body in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{identifier} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(raw)
    raw += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    raw += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return raw


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "missing_head",
        "duplicate_head",
        "missing_table",
        "unknown_table",
        "row_count",
        "compressed_hash",
    ],
)
def test_native_23_business_gzip_separate_original_migration_heads_are_explicit(
    fixture: tuple[Path, dict[str, Any], bytes],
    mutation: str,
) -> None:
    root, request, _ = fixture
    data: dict[str, Any] = {name: [] for name in TOOL["NATIVE_BUSINESS_TABLES"]}
    if mutation == "missing_table":
        data.pop("accounts")
    if mutation == "unknown_table":
        data["unexpected"] = []
    payload = json.dumps(data).encode()
    compressed = gzip.compress(payload, mtime=0)
    proof = native_fixture(root, request["source"]["files"], {"snapshot.gz": compressed})
    metadata = {
        "path": ".runtime/native/snapshot.gz",
        "sha256": TOOL["digest"](compressed),
        "data_sha256": TOOL["digest"](payload),
        "physical_tables": sorted(TOOL["NATIVE_BUSINESS_TABLES"] | {"alembic_version"}),
        "metadata_heads": ["0007_external_bank_facts"],
        "row_count": 0,
    }
    if mutation == "missing_head":
        metadata.pop("metadata_heads")
    elif mutation == "duplicate_head":
        metadata["metadata_heads"] *= 2
    elif mutation == "row_count":
        metadata["row_count"] = 1
    elif mutation == "compressed_hash":
        metadata["sha256"] = "b" * 64
    if mutation == "none":
        restored = proof.snapshot(metadata)
        assert len(json.loads(gzip.decompress(compressed))) == 23
        assert len(restored) == 24 and restored["alembic_version"] == [
            {"version_num": "0007_external_bank_facts"}
        ]
    else:
        with pytest.raises(ValueError):
            proof.snapshot(metadata)


@pytest.mark.parametrize(
    "mutation", ["none", "at_bound", "overflow", "truncated", "crc", "trailing", "members"]
)
def test_snapshot_streaming_full_eof_crc_and_exact_bound(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = TOOL["bounded_snapshot_bytes"]
    assert helper.__globals__["MAX_SNAPSHOT_DECODED_BYTES"] == 512 * 1024 * 1024
    payload = b"TOOL_ONLY" * 8
    raw = gzip.compress(payload, mtime=0)
    monkeypatch.setitem(helper.__globals__, "MAX_SNAPSHOT_DECODED_BYTES", len(payload))
    if mutation == "overflow":
        raw = gzip.compress(payload + b"x", mtime=0)
    elif mutation == "truncated":
        raw = raw[:-3]
    elif mutation == "crc":
        raw = raw[:-8] + bytes([raw[-8] ^ 1]) + raw[-7:]
    elif mutation == "trailing":
        raw += b"NOT_A_GZIP_MEMBER"
    elif mutation == "members":
        raw = gzip.compress(payload[:32], mtime=0) + gzip.compress(payload[32:], mtime=0)
    if mutation in {"none", "at_bound", "members"}:
        assert helper(raw) == payload
    else:
        with pytest.raises((ValueError, OSError, EOFError)):
            helper(raw)


def test_snapshot_decoder_reads_small_chunks_before_rejecting_total_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = TOOL["bounded_snapshot_bytes"]
    raw = gzip.compress(b"TOOL_ONLY" * 100, mtime=0)
    requests: list[int] = []

    class Tracing(gzip.GzipFile):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None
            requests.append(size)
            return super().read(size)

    monkeypatch.setitem(helper.__globals__, "MAX_SNAPSHOT_DECODED_BYTES", 16)
    monkeypatch.setitem(helper.__globals__, "SNAPSHOT_READ_CHUNK_BYTES", 4)
    monkeypatch.setattr(gzip, "GzipFile", Tracing)
    with pytest.raises(ValueError, match="decode budget"):
        helper(raw)
    assert requests == [4, 4, 4, 4, 1]


def test_snapshot_raw_hash_and_full_denominator_still_reject_partial_claims(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    root, request, _ = fixture
    data: dict[str, Any] = {name: [] for name in TOOL["NATIVE_BUSINESS_TABLES"]}
    data["transactions"] = [{"id": "TOOL_ONLY", "amount_cents": 12}]
    payload = json.dumps(data).encode()
    raw = gzip.compress(payload, mtime=0)
    proof = native_fixture(root, request["source"]["files"], {"snapshot.gz": raw})
    metadata = {
        "path": ".runtime/native/snapshot.gz",
        "sha256": TOOL["digest"](raw),
        "data_sha256": TOOL["digest"](payload),
        "bytes": len(payload),
        "physical_tables": sorted(TOOL["NATIVE_BUSINESS_TABLES"] | {"alembic_version"}),
        "metadata_heads": ["0007_external_bank_facts"],
        "row_counts": {name: len(value) for name, value in data.items()},
        "row_count": 1,
    }
    assert proof.snapshot(metadata)["transactions"] == data["transactions"]
    for mutation in ("row_count", "table_count", "bytes", "data_hash", "missing_table"):
        changed = copy.deepcopy(metadata)
        if mutation == "row_count":
            changed["row_count"] = 0
        elif mutation == "table_count":
            changed["row_counts"]["transactions"] = 0
        elif mutation == "bytes":
            changed["bytes"] = len(payload) - 1
        elif mutation == "data_hash":
            changed["data_sha256"] = "b" * 64
        else:
            changed["physical_tables"].pop()
        with pytest.raises(ValueError):
            proof.snapshot(changed)


def original_audit_limits() -> dict[str, int]:
    tree = ast.parse((ROOT / "apps/api/app/services/audit_chain.py").read_text(encoding="utf-8"))

    def integer(node: ast.AST) -> int:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        assert isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)
        return integer(node.left) * integer(node.right)

    limits = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "VERIFY_EVENT_LIMIT",
                    "VERIFY_SUBJECT_LIMIT",
                    "VERIFY_BYTE_LIMIT",
                }:
                    limits[target.id] = integer(node.value)
    return limits


def test_retained_failed_observer_complete_snapshot_over_old_64mib_is_readable(
    fixture: tuple[Path, dict[str, Any], bytes],
) -> None:
    """Read actual failed run originals; this tests container decoding, never audit VALID."""
    root, _, _ = fixture
    base = ROOT / "docs/progress/evidence/W1/audit-current-owned-20261005T0457Z"
    manifest_raw = (base / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest["status"] == "FAILED" and manifest["error"]["type"] == "OperationalError"
    metadata = manifest["snapshot"]
    raw = (ROOT / metadata["path"]).read_bytes()
    assert len(raw) == 8227118 and TOOL["digest"](raw) == metadata["sha256"]
    payload = TOOL["bounded_snapshot_bytes"](raw)
    assert len(payload) == metadata["bytes"] == 113098303 > 64 * 1024 * 1024
    assert TOOL["digest"](payload) == metadata["data_sha256"]
    data = json.loads(payload)
    assert (
        len(data) == 24
        and {name: len(value) for name, value in data.items()} == metadata["row_counts"]
    )
    limits = original_audit_limits()
    assert limits == {
        "VERIFY_EVENT_LIMIT": 10000,
        "VERIFY_SUBJECT_LIMIT": 20000,
        "VERIFY_BYTE_LIMIT": 64 * 1024 * 1024,
    }
    epochs = []
    for epoch in data["audit_epochs"]:
        events = [row for row in data["audit_events"] if row["epoch_id"] == epoch["id"]]
        subjects = [
            row for row in data["audit_subject_snapshots"] if row["epoch_id"] == epoch["id"]
        ]
        size = sum(len(row["canonical_text"].encode()) for row in events + subjects)
        epochs.append(
            {
                "epoch_id": epoch["id"],
                "user_id": epoch["user_id"],
                "status": epoch["status"],
                "event_rows": len(events),
                "subject_rows": len(subjects),
                "canonical_bytes": size,
                "within_original_resource_limits": len(events) <= limits["VERIFY_EVENT_LIMIT"]
                and len(subjects) <= limits["VERIFY_SUBJECT_LIMIT"]
                and size <= limits["VERIFY_BYTE_LIMIT"],
            }
        )
    report = {
        "classification": "READ_ONLY_RAW_ORIGINAL_ANALYSIS_NOT_AUDIT_VERIFICATION",
        "original_observer_status": manifest["status"],
        "original_manifest_path": str(base / "manifest.json"),
        "original_manifest_sha256": TOOL["digest"](manifest_raw),
        "snapshot": metadata,
        "decoded_bytes": len(payload),
        "tables": len(data),
        "row_counts": metadata["row_counts"],
        "total_rows": sum(metadata["row_counts"].values()),
        "epochs": epochs,
        "service_resource_limits": limits,
        "service_source_sha256": TOOL["digest"](
            (ROOT / "apps/api/app/services/audit_chain.py").read_bytes()
        ),
        "new_container_bound_bytes": TOOL["MAX_SNAPSHOT_DECODED_BYTES"],
        "audit_verification_status": "NOT_RUN",
        "database_browser_financial": "NOT_RUN",
    }
    report_path = root / ".runtime/retained-failed-snapshot-budget-analysis.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert (base / "manifest.json").read_bytes() == manifest_raw
    assert TOOL["digest"]((ROOT / metadata["path"]).read_bytes()) == metadata["sha256"]


def backend_argv() -> list[str]:
    return [
        "uv",
        "run",
        "--frozen",
        "python",
        "-m",
        "pytest",
        "apps/api/app/tests",
        "scripts/tests",
        "-p",
        "scripts.mvp_hypothesis_counter",
        "--cov",
        "--cov-config=docs/spec/mvp-coverage.ini",
        "--cov-report=json:TOOL_ONLY",
        "--hypothesis-show-statistics",
        "--mvp-coverage-json=TOOL_ONLY",
        "--mvp-hypothesis-output=TOOL_ONLY",
        "-p",
        "no:cacheprovider",
    ]


@pytest.mark.parametrize("wrapper", [False, True])
def test_complete_backend_real_argv_with_middle_paths_is_accepted(wrapper: bool) -> None:
    argv = backend_argv()
    assert "pytest --cov" not in " ".join(argv)
    if wrapper:
        argv = [
            "uv",
            "run",
            "--frozen",
            "python",
            "scripts/run_scoped_check.py",
            "--task",
            "W1",
            "--label",
            "actual-source-bound-child",
            "--source-prefix",
            "apps/",
            "--",
            *argv,
        ]
    assert TOOL["native_backend_pytest_argv"](argv, ROOT) is True


@pytest.mark.parametrize(
    "mutation",
    [
        "echo",
        "missing_target",
        "duplicate_target",
        "single_test",
        "missing_cov",
        "narrow_cov",
        "append",
        "no_cov",
        "marker",
        "keyword",
        "ignore",
        "deselect",
        "collect",
        "override_ini",
        "config",
        "wrong_cov_config",
        "false_launcher",
        "unfrozen",
        "wrapper_bad_task",
    ],
)
def test_native_backend_argv_rejects_text_tokens_subset_or_weakened_cov(mutation: str) -> None:
    argv = backend_argv()
    if mutation == "echo":
        argv = ["echo", "pytest", "--cov", "apps/api/app/tests", "scripts/tests"]
    elif mutation == "missing_target":
        argv.remove("scripts/tests")
    elif mutation == "duplicate_target":
        argv.append("scripts/tests")
    elif mutation == "single_test":
        argv[argv.index("apps/api/app/tests")] = "apps/api/app/tests/test_boundary.py"
    elif mutation == "missing_cov":
        argv.remove("--cov")
    elif mutation == "narrow_cov":
        argv[argv.index("--cov")] = "--cov=app.domain.boundary"
    elif mutation == "wrong_cov_config":
        argv[argv.index("--cov-config=docs/spec/mvp-coverage.ini")] = "--cov-config=TOOL_ONLY.ini"
    elif mutation == "false_launcher":
        argv[3:6] = ["python", "-c", "print('pytest --cov')"]
    elif mutation == "unfrozen":
        argv.remove("--frozen")
    elif mutation == "wrapper_bad_task":
        argv = [
            "uv",
            "run",
            "--frozen",
            "python",
            "scripts/run_scoped_check.py",
            "--task",
            "W8",
            "--label",
            "TOOL_ONLY",
            "--source-prefix",
            "apps/",
            "--",
            *argv,
        ]
    else:
        argv += {
            "append": ["--cov-append"],
            "no_cov": ["--no-cov"],
            "marker": ["-m", "not property"],
            "keyword": ["-k", "subset"],
            "ignore": ["--ignore=apps/api/app/tests/test_boundary.py"],
            "deselect": ["--deselect=TOOL_ONLY"],
            "collect": ["--collect-only"],
            "override_ini": ["-o", "addopts=--cov-append"],
            "config": ["-c", "TOOL_ONLY.ini"],
        }[mutation]
    assert TOOL["native_backend_pytest_argv"](argv, ROOT) is False


@pytest.mark.parametrize("selection", ["-ksubset", "-mproperty", "-oaddopts=--cov-append"])
def test_native_backend_attached_short_options_cannot_select_or_reconfigure(selection: str) -> None:
    assert TOOL["native_backend_pytest_argv"]([*backend_argv(), selection], ROOT) is False


def final_commands_values(
    request: dict[str, Any], backend: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    values: dict[str, Any] = {}
    references: dict[str, Any] = {}
    commands = {
        "bootstrap": [["uv", "sync", "--frozen"], ["pnpm", "install", "--frozen-lockfile"]],
        "seed": [
            ["docker", "compose", "up", "-d", "--wait", "db"],
            ["uv", "run", "--frozen", "alembic", "upgrade", "head"],
            ["uv", "run", "--frozen", "python", "scripts/seed_demo.py"],
        ],
        "check": [
            ["uv", "run", "--frozen", "ruff", "check", "apps/api", "scripts"],
            ["uv", "run", "--frozen", "ruff", "format", "--check", "apps/api", "scripts"],
            ["uv", "run", "--frozen", "mypy"],
            ["uv", "run", "--frozen", "python", "scripts/generate_openapi.py", "--check"],
            ["pnpm", "--dir", "apps/web", "lint"],
            ["pnpm", "--dir", "apps/web", "typecheck"],
            backend,
            ["pnpm", "--dir", "apps/web", "test"],
            ["pnpm", "--dir", "apps/web", "e2e"],
        ],
    }
    source = {
        "git_revision": request["source"]["git_head"],
        "source_sha256": request["source"]["files"],
    }
    for target, children in commands.items():
        scoped, refs = scoped_values(request)
        scoped["manifest"]["command"] = ["TOOL_ONLY", "make.cmd", target]
        for name, value in scoped.items():
            values[target + "-" + name] = value
        task: dict[str, Any] = {
            "run_id": "tool-run",
            "target": target,
            "successful": True,
            "source_stable": True,
            "source": source,
            "source_after": source,
            "commands": [],
        }
        for index, argv in enumerate(children):
            log = f"{target}-{index}.log"
            task["commands"].append(
                {
                    "command": argv,
                    "started_at": "2026-10-05T00:00:00+00:00",
                    "exit_code": 0,
                    "log": log,
                }
            )
            values[log] = "TOOL_ONLY synthetic native transcript; no command executed\n"
        values[target + "-task"] = task
        references[target] = {
            "scoped": {name: target + "-" + ref for name, ref in refs.items()},
            "task_manifest": target + "-task",
        }
    return values, {"commands": references, "seed_isolation": "TOOL_ONLY_CHECKER_STUB"}


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "wrapper",
        "missing_pytest",
        "echo_cov",
        "source_after",
        "drift",
        "failed_child",
        "missing_web",
        "missing_bootstrap",
    ],
)
def test_final_commands_keeps_all_three_native_children_source_and_log_gates(
    fixture: tuple[Path, dict[str, Any], bytes], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root, request, _ = fixture
    backend = backend_argv()
    if mutation == "wrapper":
        backend = [
            "uv",
            "run",
            "--frozen",
            "python",
            "scripts/run_scoped_check.py",
            "--task",
            "W1",
            "--label",
            "TOOL_ONLY",
            "--source-prefix",
            "apps/",
            "--",
            *backend,
        ]
    values, inputs = final_commands_values(request, backend)
    if mutation == "missing_pytest":
        values["check-task"]["commands"].pop(6)
    elif mutation == "echo_cov":
        values["check-task"]["commands"][6]["command"] = ["echo", "pytest --cov"]
    elif mutation == "source_after":
        values["check-task"]["source_after"] = {"git_revision": "b" * 40, "source_sha256": {}}
    elif mutation == "drift":
        values["check-task"]["source_stable"] = False
    elif mutation == "failed_child":
        values["check-task"]["commands"][0]["exit_code"] = 1
    elif mutation == "missing_web":
        values["check-task"]["commands"].pop()
    elif mutation == "missing_bootstrap":
        values["bootstrap-task"]["commands"].pop()
    proof = native_fixture(root, request["source"]["files"], values)
    monkeypatch.setitem(
        TOOL["final_commands_proof"].__globals__,
        "seed_isolation_proof",
        lambda *_: {"evidence_kind": "TOOL_ONLY_CHECKER_STUB"},
    )
    if mutation in {"none", "wrapper"}:
        result = TOOL["final_commands_proof"](proof, inputs)
        assert len(result["completed_native_commands"]) == 3
        assert result["outer_task_exit"] == "OUTER_TASK_EXIT_PENDING"
    else:
        with pytest.raises(ValueError):
            TOOL["final_commands_proof"](proof, inputs)


def round_checkpoint_rows() -> list[dict[str, Any]]:
    rows = []
    for case in range(7):
        for phase, mode in (("before", "BEGIN"), ("after", "RESET")):
            rows.append(
                {
                    "scenario_id": f"w1-case{case}",
                    "label": f"independent-case-start-{phase}",
                    "mode": mode,
                    "result": {"snapshot": {"path": f"TOOL_ONLY-initial-{case}-{phase}"}},
                }
            )
    for number in (1, 2, 3):
        for phase, mode in (("before", "BEGIN"), ("after", "RESET")):
            rows.append(
                {
                    "scenario_id": "w1-roundspec",
                    "label": f"round-{number}-reset-{phase}",
                    "mode": mode,
                    "result": {"snapshot": {"path": f"TOOL_ONLY-round-{number}-{phase}"}},
                }
            )
    return rows


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "duplicate",
        "missing",
        "scenario",
        "mode",
        "order",
        "non_adjacent",
        "fourth",
        "complete_label",
    ],
)
def test_named_round_scope_uses_same_case_three_original_reset_before_begin(mutation: str) -> None:
    checkpoints = round_checkpoint_rows()
    if mutation == "duplicate":
        checkpoints.append(copy.deepcopy(checkpoints[-1]))
    elif mutation == "missing":
        checkpoints.pop()
    elif mutation == "scenario":
        checkpoints[-1]["scenario_id"] = "w1-another-case"
    elif mutation == "mode":
        checkpoints[-2]["mode"] = "MONEY"
    elif mutation == "order":
        checkpoints[-6:] = checkpoints[-4:-2] + checkpoints[-6:-4] + checkpoints[-2:]
    elif mutation == "non_adjacent":
        checkpoints.insert(-1, {"label": "TOOL_ONLY-extra", "mode": "MONEY"})
    elif mutation == "fourth":
        checkpoints[-1]["label"] = "round-4-reset-after"
    elif mutation == "complete_label":
        checkpoints[-2]["label"] = "round-3-complete"
    if mutation == "none":
        results = TOOL["named_round_snapshots"](checkpoints, "w1-roundspec")
        assert [row["path"] for row in results] == [
            f"TOOL_ONLY-round-{index}-before" for index in (1, 2, 3)
        ]
        assert len([row for row in checkpoints if row["mode"] == "RESET"]) == 10
    else:
        with pytest.raises(ValueError):
            TOOL["named_round_snapshots"](checkpoints, "w1-roundspec")


@pytest.mark.parametrize(
    "mutation", ["none", "after_refs", "missing_initial", "initial_reset_failure"]
)
def test_tool_only_browser_scope_checks_all_ten_resets_and_three_named_original_rounds(
    fixture: tuple[Path, dict[str, Any], bytes], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """Explicit checker spies test dispatch only, never pretend a UI/financial proof."""
    root, _, _ = fixture
    source = root / "apps/web/tests/e2e/w1-demo.spec.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(f"test('TOOL_ONLY_{index}'" for index in range(7)), encoding="utf-8"
    )
    checkpoints = round_checkpoint_rows()
    round_refs = TOOL["named_round_snapshots"](checkpoints, "w1-roundspec")
    if mutation == "after_refs":
        round_refs = [
            row["result"]["snapshot"]
            for row in checkpoints
            if row["label"].startswith("round-") and row["mode"] == "RESET"
        ]
    elif mutation == "missing_initial":
        checkpoints = checkpoints[2:]
    raw = b"TOOL_ONLY native index fixture"
    manifest = {
        "run_id": "tool-browser",
        "classification": "ACTUAL_UI_ACCEPTANCE_NOT_PRODUCT_SLA",
        "commit": "a" * 40,
        "playwright_exit_code": 0,
        "status": "PASSED",
        "source_before": {},
        "source_after": {},
        "artifact_hashes": {"kept.raw": TOOL["digest"](raw)},
        "reset_oracle_method": {"evidence_kind": "TOOL_ONLY_CHECKER_STUB"},
        "baseline": {"path": "TOOL_ONLY-baseline"},
        "checkpoints": checkpoints,
    }
    results = {
        "suites": [
            {
                "specs": [
                    {
                        "id": "round-spec" if index == 6 else f"case-{index}",
                        "title": f"TOOL_ONLY_{index}",
                        "tests": [{"results": [{"status": "passed", "duration": 1}]}],
                    }
                    for index in range(7)
                ]
            }
        ]
    }

    class ToolOnlyProof:
        head = "a" * 40

        def __init__(self) -> None:
            self.root = root

        def scoped(self, _: Any) -> tuple[dict[str, Any], str]:
            return {"command": ["TOOL_ONLY", "scripts/w1_browser_acceptance.py"]}, "TOOL_ONLY"

        def json(self, ref: str) -> dict[str, Any]:
            return manifest if ref == "browser" else results

        def original(self, _: Any) -> tuple[dict[str, str], bytes]:
            return {
                "run_id": "tool-browser",
                "resolved_repository_path": ".runtime/native/manifest.json",
            }, raw

        def by_path(self, _: Any) -> tuple[dict[str, str], bytes]:
            return {"run_id": "tool-browser"}, raw

        def snapshot(self, metadata: dict[str, Any]) -> dict[str, Any]:
            return {"audit_epochs": [{"id": metadata["path"]}]}

    resets = []

    def reset(before: Any, after: Any, baseline: Any) -> dict[str, str]:
        resets.append((before, after, baseline))
        if mutation == "initial_reset_failure":
            raise ValueError("TOOL_ONLY original initial reset oracle rejects")
        return {"evidence_kind": "TOOL_ONLY_CHECKER_SPY"}

    monkeypatch.setitem(
        TOOL["browser_proof"].__globals__,
        "native_oracle",
        lambda *_: {
            "money_oracle": lambda *_: {"evidence_kind": "TOOL_ONLY_CHECKER_STUB"},
            "round_oracle": lambda data: {"epoch_id": data["audit_epochs"][0]["id"]},
        },
    )
    monkeypatch.setitem(
        TOOL["browser_proof"].__globals__,
        "native_reset_adapter",
        lambda *_: {"reset_oracle": reset},
    )
    inputs = {
        "browser_manifest": "browser",
        "playwright_results": "results",
        "round_snapshots": round_refs,
    }
    if mutation == "none":
        result = TOOL["browser_proof"](ToolOnlyProof(), inputs, "three_demo_rounds")
        assert len(resets) == result["total_reset_count"] == result["reset_count"] == 10
        assert result["round_reset_count"] == 3 and result["round_scenario_id"] == "w1-roundspec"
    else:
        with pytest.raises(ValueError):
            TOOL["browser_proof"](ToolOnlyProof(), inputs, "three_demo_rounds")


@pytest.mark.parametrize(
    "budget", ["VERIFY_EVENT_LIMIT", "VERIFY_SUBJECT_LIMIT", "VERIFY_BYTE_LIMIT"]
)
def test_larger_snapshot_container_does_not_relax_any_original_epoch_budget(
    monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    data, modules = tool_audit_snapshot()
    if budget == "VERIFY_SUBJECT_LIMIT":
        row: dict[str, Any] = {
            column.name: None for column in modules["models"].AuditSubjectSnapshot.__table__.columns
        }
        row.update(
            id=str(UUID(int=77)),
            user_id=str(UUID(int=2)),
            epoch_id=str(UUID(int=3)),
            kind="USER",
            entity_id=str(UUID(int=2)),
            scope="GENERAL",
            snapshot_version=1,
            canonical_text="TOOL_ONLY deliberately unparsed",
            snapshot_hash="b" * 64,
        )
        data["audit_subject_snapshots"] = [row]
    monkeypatch.setattr(modules["service"], budget, 0)
    with pytest.raises(ValueError, match="budget exceeded"):
        TOOL["audit_snapshot_bridge"](data, modules)


def tool_png() -> bytes:
    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("mutation", ["none", "crc", "truncated", "trailing"])
def test_tool_only_png_actual_scanline_and_crc_validation(
    mutation: str,
) -> None:
    raw = tool_png()
    if mutation == "crc":
        raw = raw[:41] + bytes([raw[41] ^ 1]) + raw[42:]
    elif mutation == "truncated":
        raw = raw[:-7]
    elif mutation == "trailing":
        raw += b"TOOL_ONLY"
    if mutation == "none":
        assert TOOL["png_structure"](raw) == {"width": 1, "height": 1, "decoded_scanline_bytes": 4}
    else:
        with pytest.raises(ValueError):
            TOOL["png_structure"](raw)


def tool_trace(mutation: str = "none") -> bytes:
    # TOOL_ONLY: fake WebM marker is deliberately not decoded and never product evidence.
    video = b"\x1a\x45\xdf\xa3" + b"TOOL_ONLY" * 200
    checksum = hashlib.sha1(video, usedforsecurity=False).hexdigest()
    context = {
        "type": "context-options",
        "version": 8,
        "origin": "library",
        "browserName": "chromium",
        "channel": "msedge",
        "contextId": "TOOL_ONLY",
        "options": {"recordVideo": {"dir": "TOOL_ONLY"}, "userAgent": "TOOL_ONLY Edg/1"},
    }
    browser = [
        context,
        {
            "type": "before",
            "method": "screenshot",
            "callId": "TOOL_ONLY",
            "startTime": 1,
            "params": {"fullPage": True},
        },
        {"type": "after", "callId": "TOOL_ONLY", "endTime": 2},
    ]
    runner = [
        {
            "type": "after",
            "attachments": [{"name": "video", "contentType": "video/webm", "sha1": checksum}],
        }
    ]
    if mutation == "channel":
        context["channel"] = "unknown"
    elif mutation == "version":
        context["version"] = 7
    elif mutation == "screenshot":
        browser[-1]["error"] = "TOOL_ONLY failed screenshot"
    elif mutation == "resource":
        video += b"changed"
    elif mutation == "video_missing":
        runner = []
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("0-trace.trace", b"\n".join(json.dumps(row).encode() for row in browser))
        archive.writestr("test.trace", b"\n".join(json.dumps(row).encode() for row in runner))
        archive.writestr("resources/" + checksum, video)
        if mutation == "traversal":
            archive.writestr("../escape", b"TOOL_ONLY")
    return stream.getvalue()


@pytest.mark.parametrize(
    "mutation",
    ["none", "channel", "version", "screenshot", "resource", "video_missing", "traversal"],
)
def test_tool_only_trace_bridge_preserves_native_attachment_and_session_negatives(
    mutation: str,
) -> None:
    if mutation == "none":
        observed = TOOL["trace_capture_bridge"](tool_trace())
        assert observed["screenshot_calls"] == 1 and observed["video_size_bytes"] > 1024
        assert "duration" not in observed and "decoded_frames" not in observed
    else:
        with pytest.raises(ValueError):
            TOOL["trace_capture_bridge"](tool_trace(mutation))


def test_partial_recomputation_is_recorded_but_never_closes_group(
    fixture: tuple[Path, dict[str, Any], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, request, _ = fixture
    request["checks"] = [
        {
            "requirement_id": "audit_chain",
            "artifact_ids": ["original-failure"],
            "validator": TOOL["NATIVE_VALIDATOR"],
            "inputs": {},
        }
    ]

    def partial(*_: Any) -> dict[str, Any]:
        raise TOOL["PartialProof"](
            {"evidence_kind": "TOOL_ONLY", "epoch_count": 1}, ["Actual service gate missing"]
        )

    monkeypatch.setitem(TOOL["export_package"].__globals__, "semantic_proof", partial)
    result, code = run_export(root, request)
    report = json.loads((Path(result["directory"]) / "manifest.json").read_bytes())
    audit = next(row for row in report["requirements"] if row["requirement_id"] == "audit_chain")
    assert code == 1 and audit["status"] == "UNVERIFIED"
    assert audit["observations"]["epoch_count"] == 1 and audit["uncovered"] == [
        "Actual service gate missing"
    ]


def tool_audit_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    # Synthetic originals through real pure algorithms, never a financial acceptance run.
    paths = [
        "apps/api/app/domain/audit_chain.py",
        "apps/api/app/domain/audit_chain_types.py",
        "apps/api/app/db/models.py",
        "apps/api/app/services/audit_chain.py",
        "scripts/browser_checkpoint_oracles.py",
    ]
    files = {name: TOOL["digest"]((ROOT / name).read_bytes()) for name in paths}
    modules = TOOL["audit_modules"](TOOL["Originals"](ROOT, files, [], tool_test_only=True))
    domain, types, models = (modules[name] for name in ("domain", "types", "models"))
    user, epoch, identity = UUID(int=2), UUID(int=3), UUID(int=5)
    now = datetime(2026, 10, 5, tzinfo=UTC)
    intent = types.AuditIntent(
        user_id=user,
        event_type="EPOCH_STARTED",
        aggregate_type="EPOCH",
        aggregate_id=epoch,
        correlation_id=epoch,
        idempotency_key="TOOL_ONLY_GENESIS",
        occurred_at=now,
        payload=types.AuditPayload(
            fact_key="TOOL_ONLY_GENESIS",
            correlation_kind="EPOCH",
            epoch_transition=types.AuditEpochTransition(kind="INIT", legacy_history=False),
        ),
    )
    event = domain.build_event(
        intent,
        event_id=identity,
        epoch_id=epoch,
        sequence_number=1,
        previous_hash=None,
        observed_at=now,
        appended_at=now,
    )
    head = types.AuditHead(
        user_id=user,
        epoch_id=epoch,
        epoch_number=1,
        event_count=1,
        last_sequence=1,
        last_event_id=identity,
        last_event_hash=event.event_hash,
        genesis_event_id=identity,
        genesis_event_hash=event.event_hash,
    )
    saved_epoch: dict[str, Any] = {
        column.name: None for column in models.AuditEpoch.__table__.columns
    }
    saved_epoch.update(head.model_dump(mode="json", exclude={"simulation", "epoch_id"}))
    saved_epoch.update(
        id=str(epoch),
        opened_at=now.isoformat(),
        created_at=now.isoformat(),
        archive_record_counts={},
    )
    saved_event: dict[str, Any] = {
        column.name: None for column in models.AuditEvent.__table__.columns
    }
    saved_event.update(event.model_dump(mode="json", exclude={"simulation", "appended_at"}))
    saved_event.update(
        canonical_text=domain.event_canonical_text(event), created_at=now.isoformat()
    )
    data: dict[str, Any] = {name: [] for name in TOOL["NATIVE_BUSINESS_TABLES"]}
    data.update(
        audit_epochs=[saved_epoch],
        audit_events=[saved_event],
        alembic_version=[{"version_num": "TOOL_ONLY"}],
    )
    return data, modules


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "head_count",
        "projection",
        "deleted_event",
        "orphan",
        "duplicate",
        "unknown_table",
        "float_money",
    ],
)
def test_tool_only_audit_bridge_calls_original_domain_and_keeps_tamper_negatives(
    mutation: str,
) -> None:
    data, modules = tool_audit_snapshot()
    if mutation == "head_count":
        data["audit_epochs"][0]["event_count"] = 2
    elif mutation == "projection":
        data["audit_events"][0]["event_hash"] = "b" * 64
    elif mutation == "deleted_event":
        data["audit_events"] = []
    elif mutation == "orphan":
        data["audit_events"][0]["epoch_id"] = str(UUID(int=99))
    elif mutation == "duplicate":
        data["audit_events"] *= 2
    elif mutation == "unknown_table":
        data["unregistered_table"] = []
    elif mutation == "float_money":
        model = modules["models"].Account
        row: dict[str, Any] = {column.name: None for column in model.__table__.columns}
        row.update(id=str(UUID(int=9)), user_id=str(UUID(int=2)), balance_cents=1.0)
        data["accounts"] = [row]
    if mutation == "none":
        result = TOOL["audit_snapshot_bridge"](data, modules)
        assert result["epoch_count"] == 1 and result["verifications"][0]["status"] == "VALID"
        assert result["method"] == "ORIGINAL_DOMAIN_AND_TYPED_PHYSICAL_ORIGINALS"
    else:
        with pytest.raises(ValueError):
            TOOL["audit_snapshot_bridge"](data, modules)


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "published_port",
        "public_host",
        "owner_label",
        "container",
        "not_running",
        "missing_ip",
    ],
)
def test_tool_only_host_published_identity_keeps_client_and_actual_server_distinct(
    fixture: tuple[Path, dict[str, Any], bytes],
    mutation: str,
) -> None:
    root, request, _ = fixture
    identity = "a" * 64
    container: dict[str, Any] = {
        "Id": identity,
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
            "Networks": {"bounded-funds_default": {"IPAddress": "172.25.0.2"}},
        },
    }
    if mutation == "published_port":
        container["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = "5432"
    elif mutation == "public_host":
        container["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
    elif mutation == "owner_label":
        container["Config"]["Labels"]["com.docker.compose.project"] = "other"
    elif mutation == "container":
        container["Id"] = "b" * 64
    elif mutation == "not_running":
        container["State"]["Running"] = False
    elif mutation == "missing_ip":
        container["NetworkSettings"]["Networks"] = {}
    proof = native_fixture(root, request["source"]["files"], {"inspect": [container]})
    binding = {
        "mode": "HOST_PUBLISHED_DOCKER_V1",
        "docker_inspect": "inspect",
        "container_id": identity,
    }
    if mutation == "none":
        assert TOOL["seed_server_endpoint"](proof, binding, "tool-run") == ("172.25.0.2", 5432)
    else:
        with pytest.raises(ValueError):
            TOOL["seed_server_endpoint"](proof, binding, "tool-run")


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "prepared",
        "source",
        "writable",
        "isolation",
        "identity",
        "missing_owner",
        "owner_hash",
        "snapshot_drift",
        "omitted_epoch",
        "service_tail",
    ],
)
def test_tool_only_observer_original_schema_requires_full_service_and_pure_replay(
    fixture: tuple[Path, dict[str, Any], bytes],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root, request, _ = fixture
    data, modules = tool_audit_snapshot()
    actor: dict[str, Any] = {
        column.name: None for column in modules["models"].User.__table__.columns
    }
    actor.update(id=str(UUID(int=2)), is_simulated=True)
    data["users"] = [actor]
    producer = "scripts/w1_audit_observe.py"
    path = root / producer
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"# TOOL_ONLY observer schema placeholder; no SQL executes\n")
    files = {**request["source"]["files"], producer: TOOL["digest"](path.read_bytes())}
    scoped, refs = scoped_values(request)
    scoped["before"] = scoped["after"] = files
    scoped["manifest"]["command"] = ["TOOL_ONLY", producer, "--run"]
    actual = TOOL["audit_snapshot_bridge"](data, modules)
    payload = json.dumps(data).encode()
    raw = gzip.compress(payload, mtime=0)
    owner = {
        "evidence_kind": "TOOL_ONLY",
        "run_id": "tool-owner",
        "status": "INCOMPLETE",
        "temporary_database_exited": False,
        "database": "bf_test_" + "a" * 32,
    }
    owner_raw = json.dumps(owner).encode()
    observer: dict[str, Any] = {
        "evidence_kind": "TOOL_ONLY",
        "protocol": "bounded-funds-audit-observation-v1",
        "run_id": "tool-run",
        "status": "PASSED",
        "formal_database_touched": False,
        "producer_path": producer,
        "producer_sha256": files[producer],
        "producer_after_sha256": files[producer],
        "started_at": "2026-10-05T00:00:00+00:00",
        "finished_at": "2026-10-05T00:00:01+00:00",
        "source_before": files,
        "source_after": files,
        "owner_run_id": "tool-owner",
        "owner_manifest_path": "output/playwright/tool-owner/manifest.json",
        "owner_manifest_initial_sha256": TOOL["digest"](owner_raw),
        "owner_manifest_initial_copy": ".runtime/native/owner-manifest-initial.json",
        "database": owner["database"],
        "client_target": {"host": "127.0.0.1", "port": 54329, "database": owner["database"]},
        "actual_transaction": {
            "isolation_query": "SHOW transaction_isolation",
            "isolation": "repeatable read",
            "readonly_query": "SHOW transaction_read_only",
            "readonly": "on",
            "identity_query": (
                "SELECT current_database() AS database, "
                "inet_server_addr()::text AS server_address, "
                "inet_server_port() AS server_port, current_user AS database_user"
            ),
            "identity_rows": [
                {
                    "database": owner["database"],
                    "server_port": 5432,
                    "server_address": "172.18.0.2/32",
                    "database_user": "TOOL_ONLY",
                }
            ],
        },
        "snapshot": {
            "path": ".runtime/native/snapshot.gz",
            "sha256": TOOL["digest"](raw),
            "data_sha256": TOOL["digest"](payload),
            "physical_tables": sorted(data),
            "bytes": len(payload),
            "row_counts": {name: len(items) for name, items in data.items()},
            "physical_query": (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            ),
        },
        "snapshot_after_data_sha256": TOOL["digest"](payload),
        "artifact_hashes": {
            "snapshot.gz": TOOL["digest"](raw),
            "owner-manifest-initial.json": TOOL["digest"](owner_raw),
        },
        "observations": [
            {
                "user_id": row["user_id"],
                "epoch_id": row["epoch_id"],
                "epoch_number": 1,
                "epoch_status": "OPEN",
                "verification": row,
            }
            for row in actual["verifications"]
        ],
    }
    if mutation == "prepared":
        scoped["manifest"]["command"].remove("--run")
    elif mutation == "source":
        observer["producer_after_sha256"] = "b" * 64
    elif mutation == "writable":
        observer["actual_transaction"]["readonly"] = "off"
    elif mutation == "isolation":
        observer["actual_transaction"]["isolation"] = "read committed"
    elif mutation == "identity":
        observer["actual_transaction"]["identity_rows"][0]["database"] = "bounded_funds"
    elif mutation == "missing_owner":
        observer["artifact_hashes"].pop("owner-manifest-initial.json")
    elif mutation == "owner_hash":
        observer["owner_manifest_initial_sha256"] = "b" * 64
    elif mutation == "snapshot_drift":
        observer["snapshot_after_data_sha256"] = "b" * 64
    elif mutation == "omitted_epoch":
        observer["observations"] = []
    elif mutation == "service_tail":
        observer["observations"][0]["verification"]["actual_tail_hash"] = "b" * 64
    values = {
        **scoped,
        "observer": observer,
        "snapshot.gz": raw,
        "owner-manifest-initial.json": owner_raw,
    }
    proof = native_fixture(root, files, values)
    monkeypatch.setitem(
        TOOL["audit_observer_proof"].__globals__, "audit_modules", lambda *_: modules
    )
    native_scoped, _ = proof.scoped(refs)
    if mutation == "none":
        result = TOOL["audit_observer_proof"](proof, {"observer": "observer"}, native_scoped)
        assert result["original_full_service_gate_count"] == 1
        assert result["actual_server_identity"]["server_address"] == "172.18.0.2/32"
    else:
        with pytest.raises(ValueError):
            TOOL["audit_observer_proof"](proof, {"observer": "observer"}, native_scoped)


@pytest.mark.parametrize("pages", [9, 10, 12, 13])
def test_actual_pdf_parser_counts_pdf_pages_not_declared_page_flags(
    fixture: tuple[Path, dict[str, Any], bytes],
    pages: int,
) -> None:
    root, request, _ = fixture
    binary = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native"
        / "poppler/Library/bin/pdfinfo.exe"
    )
    if not binary.is_file():
        pytest.skip("Actual native PDF parser unavailable")
    raw = tool_pdf(pages)
    record = {
        "artifact_id": "pdf",
        "resolved_repository_path": ".runtime/blank-tool-only.pdf",
        "source_context": "CURRENT_SOURCE",
        "purpose": "TOOL_ONLY",
        "evidence_kind": "TOOL_ONLY",
    }
    proof = TOOL["Originals"](
        root, request["source"]["files"], [(record, raw)], tool_test_only=True
    )
    if 10 <= pages <= 12:
        result = TOOL["proposal_proof"](proof, {"pdf": "pdf", "declared_pages": 999})
        assert result["actual_pdf_pages"] == pages
        assert "structure only" in result["boundary"]
    else:
        with pytest.raises(ValueError, match="10–12"):
            TOOL["proposal_proof"](proof, {"pdf": "pdf", "declared_pages": 10})
