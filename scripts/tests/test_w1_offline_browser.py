"""TOOL_ONLY protocol fixtures; no Docker, browser, bank or offline acceptance."""

from __future__ import annotations

import base64
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from scripts import w1_offline_boundary as boundary
from scripts import w1_offline_browser as tool
from scripts.demo_container_transport import validate_owned_transport
from scripts.demo_loopback_relay import relay_program, request_frame
from scripts.tests.test_demo_container_transport import native as native
from scripts.tests.test_demo_container_transport import packed, raw
from scripts.tests.test_demo_offline_network import record
from scripts.tests.test_offline_endpoint_accounting import accounting
from scripts.tests.test_offline_endpoint_accounting import verify as accounting_verify


def test_default_no_inputs_or_connections(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("TOOL_ONLY default must not read/start any prerequisite")

    monkeypatch.setattr(tool, "run_actual", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert tool.main([]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "PREPARED_NOT_EXECUTED"
    assert value["connection_made"] is value["financial_operations_executed"] is False
    assert value["complete_all7"] is False and value["three_rounds"] == "NOT_RUN"


def test_explicit_run_missing_prerequisites_never_calls_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "run_actual", lambda args: pytest.fail("Incomplete run admitted"))
    with pytest.raises(ValueError, match="Five explicit"):
        tool.main(["--run", "--registration", "TOOL_ONLY.json"])


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:15183/",
        "http://localhost:15183/",
        "http://127.0.0.1:15181/",
        "http://127.0.0.1/",
        "http://example.com:15183/",
        "http://user@127.0.0.1:15183/",
        "http://127.0.0.1:15183.evil/",
        "file:///tmp/TOOL_ONLY",
        "ws://127.0.0.1:15183/",
        "http://[::1]:15183/",
        "data:text/plain,TOOL_ONLY",
    ],
)
def test_non_relay_resource_url_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        tool.local_url(url)


def test_loopback_original_path_query() -> None:
    assert tool.local_url(tool.RELAY_URL + "/assets/app.js?version=1") == "/assets/app.js?version=1"


def request(operation: str = "checkpoint") -> dict[str, Any]:
    value = {
        "protocol": tool.BROKER_PROTOCOL,
        "id": str(uuid4()),
        "scenario_id": "w1-TOOLONLY",
        "operation": operation,
        "expected_epoch_id": str(uuid4()),
    }
    if operation == "checkpoint":
        value.update(label="independent-case-start-before", mode="BEGIN")
    elif operation == "ingest_goal_income":
        value["goal_id"] = str(uuid4())
    else:
        value["action_id"] = str(uuid4())
    return value


@pytest.mark.parametrize(
    "operation", ["checkpoint", "ingest_goal_income", "verify_legacy_recovery"]
)
def test_original_bounded_broker_dto(operation: str) -> None:
    value = request(operation)
    assert tool.validate_broker(value, value["id"] + ".request.json") == value


@pytest.mark.parametrize(
    "field",
    [
        "clock",
        "amount_cents",
        "balance_cents",
        "autonomy_level",
        "result",
        "fault",
        "effect_hash",
        "policy_id",
    ],
)
def test_browser_cannot_supply_financial_outputs(field: str) -> None:
    value = request("ingest_goal_income")
    value[field] = "TOOL_ONLY"
    with pytest.raises(ValueError, match="clock/amount/authority"):
        tool.validate_broker(value, value["id"] + ".request.json")


@pytest.mark.parametrize("case", ["filename", "epoch", "scenario", "mode", "rent", "round"])
def test_broker_exact_scope_negative(case: str) -> None:
    value = request()
    filename = value["id"] + ".request.json"
    if case == "filename":
        filename = str(uuid4()) + ".request.json"
    elif case == "epoch":
        value["expected_epoch_id"] = "not-uuid"
    elif case == "scenario":
        value["scenario_id"] = "../../outside"
    elif case == "mode":
        value["mode"] = "ROUND_COMPLETE"
    else:
        value["operation"] = "prepare_rent_old_action" if case == "rent" else "verify_round"
    with pytest.raises(ValueError):
        tool.validate_broker(value, filename)


def result_report() -> dict[str, Any]:
    return {
        "suites": [
            {
                "specs": [
                    {
                        "title": title,
                        "id": "TOOL_ONLY_case" + str(index),
                        "ok": True,
                        "tests": [
                            {"projectName": "edge", "results": [{"status": "passed", "retry": 0}]}
                        ],
                    }
                    for index, title in enumerate(tool.CASE_TITLES)
                ]
            }
        ],
        "errors": [],
    }


def test_four_original_names_one_attempt_only() -> None:
    value = tool.validate_results(result_report())
    assert value["count"] == 4 and value["attempts_per_case"] == 1
    assert value["complete_all7"] is False and value["three_rounds"] == "NOT_RUN"


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "title",
        "skip",
        "retry",
        "two_attempts",
        "chrome",
        "error",
        "same_id",
    ],
)
def test_actual_case_denominator_fail_closed(case: str) -> None:
    report = result_report()
    specs = report["suites"][0]["specs"]
    if case == "missing":
        specs.pop()
    elif case == "duplicate":
        specs.append(copy.deepcopy(specs[0]))
    elif case == "title":
        specs[0]["title"] = "TOOL_ONLY fabricated chain"
    elif case == "same_id":
        specs[0]["id"] = specs[1]["id"]
    elif case == "error":
        report["errors"] = [{"message": "TOOL_ONLY"}]
    else:
        test = specs[0]["tests"][0]
        if case == "chrome":
            test["projectName"] = "chromium"
        elif case == "two_attempts":
            test["results"].append(copy.deepcopy(test["results"][0]))
        elif case == "retry":
            test["results"][0]["retry"] = 1
        else:
            test["results"][0]["status"] = "skipped"
    with pytest.raises(ValueError):
        tool.validate_results(report)


def reset_rows() -> tuple[list[dict[str, Any]], dict[str, str]]:
    cases = tool.validate_results(result_report())["case_ids"]
    rows: list[dict[str, Any]] = []
    for identity in cases.values():
        scenario = "w1-" + re.sub(r"[^a-z0-9]", "", identity, flags=re.I)[:64]
        rows += [
            {"scenario_id": scenario, "label": "independent-case-start-before", "mode": "BEGIN"},
            {
                "scenario_id": scenario,
                "label": "independent-case-start-after",
                "mode": "RESET",
                "result": {"oracle": {"TOOL_ONLY": True}},
            },
        ]
    return rows, cases


def test_second_native_command_preserves_one_initial_prerequisite_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOOL_ONLY subprocess seam; no Docker or browser is started."""
    destination = tool.ROOT / ".runtime" / ("TOOL_ONLY_offline_commands_" + uuid4().hex)
    (destination / "native").mkdir(parents=True)
    ready = cast(
        tool.Readiness,
        SimpleNamespace(transport=SimpleNamespace(database="bf_test_" + "a" * 32)),
    )
    manifest: dict[str, Any] = {"commands": [], "artifacts": []}
    runner = tool.ActualRun(destination, manifest, ready, ModuleType("TOOL_ONLY"), {}, "", {})
    monkeypatch.setattr(runner, "stable", lambda: None)
    actual_inputs: list[tuple[list[str], bytes]] = []

    def native(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        actual_inputs.append((argv, kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=b"TOOL_ONLY stdout", stderr=b"")

    monkeypatch.setattr(subprocess, "run", native)
    runner.retain("native/prerequisite-probe.json", b"TOOL_ONLY initial raw probe")
    assert runner.command("first", ["TOOL_ONLY", "one"], b"first stdin") == b"TOOL_ONLY stdout"
    assert runner.command("second", ["TOOL_ONLY", "two"], b"second stdin") == b"TOOL_ONLY stdout"
    assert actual_inputs == [
        (["TOOL_ONLY", "one"], b"first stdin"),
        (["TOOL_ONLY", "two"], b"second stdin"),
    ]
    assert len(manifest["commands"]) == 2
    initial = [row for row in manifest["artifacts"] if "prerequisite-" in row["path"]]
    assert len(initial) == 1
    assert (destination / "native/prerequisite-probe.json").read_bytes() == (
        b"TOOL_ONLY initial raw probe"
    )


def test_all_four_same_named_case_reset_pairs_retained() -> None:
    rows, cases = reset_rows()
    tool.validate_reset_scope(rows, cases)


@pytest.mark.parametrize(
    "case", ["missing", "wrong_scenario", "wrong_label", "no_begin", "no_oracle", "duplicate"]
)
def test_reset_original_assertions_cannot_be_dropped(case: str) -> None:
    rows, cases = reset_rows()
    if case == "missing":
        rows.pop()
    elif case == "wrong_scenario":
        rows[1]["scenario_id"] = "w1-other"
    elif case == "wrong_label":
        rows[1]["label"] = "pretend-complete"
    elif case == "no_begin":
        rows[0]["mode"] = "MONEY"
    elif case == "no_oracle":
        rows[1]["result"] = {}
    else:
        rows += rows[:2]
    with pytest.raises(ValueError):
        tool.validate_reset_scope(rows, cases)


def test_child_env_no_authority_or_formal_dsn_inheritance() -> None:
    result = tool.child_environment(
        {
            "PATH": "TOOL_ONLY_PATH",
            "BF_FAULT": "1",
            "COMPOSE_PROJECT_NAME": "formal",
            "PGDATABASE": "formal",
            "DATABASE_URL": "TOOL_ONLY_formal",
            "NODE_OPTIONS": "--require evil",
            "BOUNDEDFUNDS_FINAL_CONTEXT": "old",
            "VITE_API_BASE_URL": "https://external.invalid",
        },
        tool.ROOT / "output/playwright/TOOL_ONLY",
        "bf_test_" + "1" * 32,
    )
    assert result["PATH"] == "TOOL_ONLY_PATH"
    assert all(
        key not in result
        for key in (
            "BF_FAULT",
            "COMPOSE_PROJECT_NAME",
            "PGDATABASE",
            "NODE_OPTIONS",
            "BOUNDEDFUNDS_FINAL_CONTEXT",
            "VITE_API_BASE_URL",
        )
    )
    assert (
        result["DATABASE_URL"] == "postgresql+psycopg://bf_demo@127.0.0.1:54329/bf_test_" + "1" * 32
    )
    assert result["BF_W1_WEB_PORT"] == "15183" and result["BF_W1_SERVERS_EXTERNAL"] == "1"
    with pytest.raises(ValueError):
        tool.child_environment({}, Path("TOOL_ONLY"), "bounded_funds")


def test_generated_observer_does_not_modify_original_spec() -> None:
    before = (tool.ROOT / tool.SPEC).read_bytes()
    config, wrapper = tool.generated_files(tool.ROOT / "output/playwright/TOOL_ONLY")
    assert (tool.ROOT / tool.SPEC).read_bytes() == before
    assert (tool.ROOT / tool.SPEC).as_uri().encode() in wrapper
    assert wrapper.index(b"offlineTest.beforeEach") < wrapper.index(b"await import(")
    assert b"context.on('request'" in wrapper and b"context.on('response'" in wrapper
    assert b"requestfailed" in wrapper and b"websocket" in wrapper
    assert b".route(" not in wrapper and b".fulfill(" not in wrapper
    assert b'"retries": 0' in config and b'"workers": 1' in config
    assert b'"channel": "msedge"' in config and tool.RELAY_URL.encode() in config
    assert b"webServer" not in config
    for title in tool.CASE_TITLES:
        assert title.encode() in config or title.encode("unicode_escape") in config


def test_full24_snapshot_retained_and_migration_view_separated() -> None:
    data: dict[str, list[dict[str, Any]]] = {"table" + str(index): [] for index in range(23)}
    data["alembic_version"] = [{"version_num": "0007"}]
    decoded = SimpleNamespace(snapshot_data=raw(data), snapshot_gzip=b"TOOL_ONLY_ORIGINAL_GZIP")
    business, meta = tool.snapshot_business(decoded)
    assert len(business) == 23 and "alembic_version" not in business
    assert len(meta["physical_tables"]) == 24 and meta["row_count"] == 1
    assert meta["metadata_heads"] == ["0007"] and meta["data_sha256"] == tool.sha(raw(data))
    data.pop("table1")
    with pytest.raises(ValueError, match="physical24"):
        tool.snapshot_business(SimpleNamespace(snapshot_data=raw(data)))


def test_generated_esm_and_config_parse_without_import_or_browser() -> None:
    directory = tool.ROOT / ".runtime/tool_tests/offline_browser_syntax" / uuid4().hex
    directory.mkdir(parents=True)
    config, wrapper = tool.generated_files(directory)
    node = shutil.which("node")
    assert node is not None
    for name, value in (("config.cjs", config), ("observer.mjs", wrapper)):
        path = directory / name
        path.write_bytes(value)
        result = subprocess.run([node, "--check", str(path)], capture_output=True, check=False)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    # --check parses only; it does not import the original spec or start any engine.


def test_strict_original_json_duplicate_nonfinite_and_naive_clock() -> None:
    for value in (b'{"same":1,"same":2}', b'{"v":NaN}', b'{"v":Infinity}'):
        with pytest.raises(ValueError):
            tool.original(value)
    with pytest.raises(ValueError, match="Naive"):
        tool.aware("2026-10-05T12:00:00")


@pytest.fixture
def probe_fixture(native: dict[str, Any]) -> dict[str, Any]:
    for row in native["containers"]:
        if row["Config"]["Labels"]["com.docker.compose.service"] == "web":
            internal = native["owner"]["project"] + "_demo_internal"
            row["NetworkSettings"]["Networks"][internal]["IPAddress"] = "172.24.0.3"
    owned, _, after, _, networks_after = accounting(native)
    directory = (
        tool.ROOT / ".runtime/w1-offline-boundary-private" / ("TOOL_ONLY_browser_" + uuid4().hex)
    )
    directory.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "tool_input_context": "TOOL_ONLY",
        "protocol": "bounded-funds-offline-reobserve-v1",
        "action": "probe",
        "method": "W1_DOCKER_NETWORK_STATUS_ENDPOINT_ACCOUNTING_V1",
        "status": "EGRESS_BOUNDARY_OBSERVED",
        "exit_code": 0,
        "offline_accepted": False,
        "disconnect_executed_by_this_run": False,
        "source_before": native["current"],
        "source_after": native["current"],
        "source_head": native["head"],
        "source_head_after": native["head"],
        "owner_uuid": owned.owner_uuid,
        "owner_run_id": owned.owner_run_id,
        "source_digest": owned.source_digest,
        "artifacts": [],
        "commands": [],
    }

    def save(label: str, value: bytes) -> None:
        path = directory / (label + ".original")
        path.write_bytes(value)
        manifest["artifacts"].append(
            {
                "label": label,
                "private_original_path": path.relative_to(tool.ROOT).as_posix(),
                "private_original_sha256": tool.sha(value),
                "private_original_bytes": len(value),
            }
        )

    for label, name, field in (
        ("producer", "scripts/w1_offline_reobserve.py", "producer"),
        ("bridge", "scripts/demo_offline_endpoint_accounting.py", "bridge"),
        ("source-helper", "scripts/w1_demo_deployment.py", "source_helper"),
    ):
        value = (tool.ROOT / name).read_bytes()
        for phase in ("before", "after"):
            save(label + "-" + phase, value)
        manifest[field + "_sha256"] = manifest[field + "_after_sha256"] = tool.sha(value)
    for phase in ("before", "after"):
        save(phase + "-containers-stdout", after)
        save(phase + "-networks-stdout", networks_after)

    def command(
        label: str,
        argv: tuple[str, ...],
        code: int,
        stdout: bytes,
        stderr: bytes,
        stdin: bytes = b"",
    ) -> None:
        for suffix, value in (("stdout", stdout), ("stderr", stderr)):
            save(label + "-" + suffix, value)
        if stdin:
            save(label + "-stdin", stdin)
        manifest["commands"].append(
            {
                "label": label,
                "argv": list(argv),
                "exit_code": code,
                "stdout_sha256": tool.sha(stdout),
                "stderr_sha256": tool.sha(stderr),
                "stdin_sha256": tool.sha(stdin),
                "started_at": "2026-10-05T01:00:00+00:00",
                "finished_at": "2026-10-05T01:00:01+00:00",
            }
        )

    api = {
        "protocol": "bounded-funds-api-egress-probe-v1",
        "route_files": {
            "/proc/net/route": (
                "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
                "eth0 000018AC 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
            ),
            "/proc/net/ipv6_route": "",
            "/etc/resolv.conf": "nameserver127.0.0.11",
        },
        "targets": [
            {
                "host": host,
                "port": port,
                "timeout_seconds": 3,
                "result": "ERROR",
                "errno": 101,
                "message": "Network is unreachable",
            }
            for host, port in boundary.IP_TARGETS
        ],
        "dns": {
            "argv": ["/opt/bf-venv/bin/python", "-c", boundary.DNS_CODE],
            "exit_code": 0,
            "timeout_seconds": 4,
            "stdout_base64": base64.b64encode(
                raw(
                    {
                        "name": boundary.DNS_NAME,
                        "result": "GAIERROR",
                        "errno": -2,
                        "message": "Name is not known",
                    }
                )
            ).decode(),
            "stderr_base64": "",
        },
    }
    command(
        "api-probe",
        ("docker", "exec", "-i", owned.api_container_id, "/opt/bf-venv/bin/python", "-"),
        0,
        raw(api),
        b"",
        boundary.API_PROBE,
    )
    observations = {"api": boundary.classify_api_probe(raw(api), 0)}
    for service, identity in (("db", "d" * 64), ("web", "b" * 64)):
        outputs: dict[str, tuple[int | None, bytes, bytes]] = {}
        for label, argv in boundary.busybox_commands(identity):
            stdout, stderr, code = b"", b"", 0
            if label == "inventory":
                stdout = b"ip\nwget\nnslookup\ntimeout\ncat\n"
            elif label.endswith("-help"):
                stdout = b"TOOL_ONLY BusyBox help"
            elif label == "route4":
                stdout = b"172.24.0.0/16 dev eth0\n"
            elif label == "resolver":
                stdout = b"nameserver 127.0.0.11\n"
            elif label.startswith("ip-"):
                code, stderr = 1, b"wget: network is unreachable"
            elif label == "dns":
                code, stderr = 1, b"DNS SERVFAIL"
            outputs[label] = (code, stdout, stderr)
            command(service + "-" + label, argv, code, stdout, stderr)
        observations[service] = boundary.classify_busybox(outputs)
    manifest["probes"] = observations
    assert all(row["observed"] is True for row in observations.values())
    return {
        "manifest": manifest,
        "owned": owned,
        "current": native["current"],
        "head": native["head"],
        "save": save,
        "directory": directory,
        "command": command,
    }


def test_probe_recomputed_from_complete_native_stdout_not_status(
    probe_fixture: dict[str, Any],
) -> None:
    fixture = probe_fixture
    containers, networks = tool.verify_probe(
        fixture["manifest"], fixture["owned"], fixture["current"], fixture["head"]
    )
    assert containers and networks and fixture["manifest"]["tool_input_context"] == "TOOL_ONLY"


@pytest.fixture
def readiness_fixture(native: dict[str, Any], probe_fixture: dict[str, Any]) -> dict[str, Any]:
    f = probe_fixture
    suffix = "TOOL_ONLY_browser_" + uuid4().hex
    registration_dir = tool.ROOT / ".runtime/w1-deployment" / suffix
    public = tool.ROOT / "docs/progress/evidence/W1" / suffix
    private = tool.ROOT / ".runtime/w1-offline-boundary-private" / suffix
    relay_private = tool.ROOT / ".runtime/w1-private-captures" / suffix
    for path in (registration_dir, public, private, relay_private):
        path.mkdir(parents=True)
    owner_raw, start_raw, _, images = packed(native)
    inputs = accounting(native)
    owned, before, after, nb, na = inputs
    prepared, command_raw, stdout, stderr = record(native, inputs)
    prior: dict[str, Any] = {
        "tool_input_context": "TOOL_ONLY",
        "protocol": boundary.PROTOCOL,
        "action": "isolate",
        "producer_sha256": tool.sha((tool.ROOT / "scripts/w1_offline_boundary.py").read_bytes()),
        "source_before": native["current"],
        "source_after": native["current"],
        "source_head": native["head"],
        "source_head_after": native["head"],
        "status": "FAILED",
        "artifacts": [],
        "commands": [
            {
                "label": "disconnect",
                "argv": list(prepared.argv),
                "stdout_sha256": tool.sha(stdout),
                "stderr_sha256": tool.sha(stderr),
                "stdin_sha256": tool.sha(b""),
                "exit_code": 0,
                "started_at": "2026-10-05T01:00:00+00:00",
                "finished_at": "2026-10-05T01:00:01+00:00",
            }
        ],
    }
    prior["producer_after_sha256"] = prior["producer_sha256"]
    captures = {
        "registration": owner_raw,
        "start-manifest": start_raw,
        "before-containers-stdout": before,
        "after-containers-stdout": after,
        "before-networks-stdout": nb,
        "after-networks-stdout": na,
        "disconnect-command": command_raw,
        "disconnect-stdout": stdout,
        "disconnect-stderr": stderr,
    }
    for label, value in captures.items():
        path = private / (label + ".original")
        path.write_bytes(value)
        prior["artifacts"].append(
            {
                "label": label,
                "private_original_path": path.relative_to(tool.ROOT).as_posix(),
                "private_original_sha256": tool.sha(value),
                "private_original_bytes": len(value),
            }
        )
    for name, image in images.items():
        (public / (name + "-image.log")).write_bytes(image)
        for phase in ("before", "after"):
            label = phase + "-" + name + "-image-stdout"
            path = private / (label + ".original")
            path.write_bytes(image)
            prior["artifacts"].append(
                {
                    "label": label,
                    "private_original_path": path.relative_to(tool.ROOT).as_posix(),
                    "private_original_sha256": tool.sha(image),
                    "private_original_bytes": len(image),
                }
            )
            f["save"](label, image)
    prior_raw = raw(prior)
    for label, value in (
        ("registration", owner_raw),
        ("start-manifest", start_raw),
        ("prior-isolation-manifest", prior_raw),
    ):
        f["save"](label, value)
    f["manifest"]["prior_transition_revalidated"] = accounting_verify(native, inputs)
    after_rows = json.loads(after)
    relay_inspect = raw(
        [
            row
            for row in after_rows
            if row["Config"]["Labels"]["com.docker.compose.service"] in {"api", "web"}
        ]
    )
    inspect_path = relay_private / "inspect.original"
    inspect_path.write_bytes(relay_inspect)
    relay = {
        "tool_input_context": "TOOL_ONLY",
        "protocol": "bounded-funds-loopback-relay-v1",
        "status": "RUNNING",
        "listen_host": "127.0.0.1",
        "listen_port": 15183,
        "owner_uuid": owned.owner_uuid,
        "api_container_id": owned.api_container_id,
        "web_container_id": prepared.web_container_id,
        "web_internal_ip": "172.24.0.3",
        "source_before": native["current"],
        "source_head": native["head"],
        "registration_sha256": owned.registration_sha256,
        "start_manifest_sha256": owned.start_manifest_sha256,
        "producer_sha256": tool.sha((tool.ROOT / "scripts/demo_loopback_relay.py").read_bytes()),
        "transport_header_change": "Connection: close",
        "financial_body_transformed": False,
        "automatic_retries": 0,
        "errors": [],
        "requests": [],
        "inspect_capture": {
            "private_original_path": inspect_path.relative_to(tool.ROOT).as_posix(),
            "private_original_sha256": tool.sha(relay_inspect),
            "public_provenance": "REGISTERED_PASSWORD_LITERAL_REDACTION_V1",
        },
    }
    files = {
        "registration": registration_dir / "manifest.json",
        "start": public / "start.json",
        "isolation": public / "isolation.json",
        "probe": public / "probe.json",
        "relay": public / "relay.json",
    }
    originals = {
        "registration": owner_raw,
        "start": start_raw,
        "isolation": prior_raw,
        "probe": raw(f["manifest"]),
        "relay": raw(relay),
    }
    for name, path in files.items():
        path.write_bytes(originals[name])
    return {
        "paths": {name: path.relative_to(tool.ROOT).as_posix() for name, path in files.items()},
        "current": native["current"],
        "head": native["head"],
        "files": files,
        "originals": originals,
    }


def test_entire_readiness_rebinds_originals_and_preserves_old_failed_isolation(
    readiness_fixture: dict[str, Any],
) -> None:
    f = readiness_fixture
    ready = tool.validate_readiness(f["paths"], f["current"], f["head"])
    assert ready.relay_initial_count == 0 and ready.transport.database.startswith("bf_test_")
    assert json.loads(f["files"]["isolation"].read_bytes())["status"] == "FAILED"
    relay = json.loads(f["files"]["relay"].read_bytes())
    relay["requests"] = [{"status": "FORWARDED_ONCE", "TOOL_ONLY": True}]
    f["files"]["relay"].write_bytes(raw(relay))
    later = tool.validate_readiness(f["paths"], f["current"], f["head"], f["originals"])
    assert later.relay_initial_count == 0  # Initial original bytes, not the growing live file.


@pytest.mark.parametrize(
    "case",
    [
        "head",
        "source",
        "relay_ip",
        "relay_id",
        "relay_retry",
        "relay_port",
        "relay_producer",
        "relay_failed",
        "probe_missing",
        "start_changed",
    ],
)
def test_current_owner_complete_readiness_fail_closed(
    readiness_fixture: dict[str, Any], case: str
) -> None:
    f = readiness_fixture
    current, head = f["current"], f["head"]
    if case == "head":
        head = "f" * 40
    elif case == "source":
        current = {**current, "uv.lock": "0" * 64}
    else:
        name = (
            "probe" if case == "probe_missing" else "start" if case == "start_changed" else "relay"
        )
        value = json.loads(f["files"][name].read_bytes())
        if name == "probe":
            value["probes"] = {"api": {"observed": True}}
        elif name == "start":
            value["status"] = "FAILED"
        else:
            fields = {
                "relay_ip": ("web_internal_ip", "1.1.1.1"),
                "relay_id": ("api_container_id", "f" * 64),
                "relay_retry": ("automatic_retries", 1),
                "relay_port": ("listen_port", 15181),
                "relay_producer": ("producer_sha256", "0" * 64),
                "relay_failed": ("status", "FAILED"),
            }
            key, replacement = fields[case]
            value[key] = replacement
        f["files"][name].write_bytes(raw(value))
    with pytest.raises(ValueError):
        tool.validate_readiness(f["paths"], current, head)


@pytest.mark.parametrize(
    "case",
    [
        "status",
        "owner",
        "source",
        "head",
        "missing",
        "bytes",
        "argv",
        "exit",
        "stdin",
        "declared",
        "timeout",
    ],
)
def test_probe_flags_cannot_replace_original_endpoint_proofs(
    probe_fixture: dict[str, Any], case: str
) -> None:
    fixture = probe_fixture
    value = copy.deepcopy(fixture["manifest"])
    if case in {"status", "owner", "head"}:
        value[{"status": "status", "owner": "owner_uuid", "head": "source_head"}[case]] = (
            "TOOL_ONLY_wrong"
        )
    elif case == "source":
        value["source_after"] = {}
    elif case == "missing":
        value["artifacts"] = [row for row in value["artifacts"] if row["label"] != "db-dns-stdout"]
    elif case == "bytes":
        value["artifacts"][-1]["private_original_bytes"] += 1
    elif case == "declared":
        value["probes"]["api"]["observed"] = False
    else:
        row = next(row for row in value["commands"] if row["label"] == "api-probe")
        if case == "argv":
            row["argv"][3] = "foreign" * 9
        elif case == "stdin":
            row["stdin_sha256"] = "0" * 64
        elif case == "timeout":
            row["exit_code"] = None
        else:
            row["exit_code"] = True
    with pytest.raises(ValueError):
        tool.verify_probe(value, fixture["owned"], fixture["current"], fixture["head"])


@pytest.fixture
def network_fixture() -> dict[str, Any]:
    directory = tool.ROOT / ".runtime/tool_tests/offline_browser" / uuid4().hex
    destination, relay_dir = directory / "browser", directory / "relay"
    destination.mkdir(parents=True)
    relay_dir.mkdir()
    transport = SimpleNamespace(api_container_id="a" * 64)
    ready = tool.Readiness(
        directory,
        directory,
        directory,
        directory,
        relay_dir / "manifest.json",
        transport,
        {},
        "a" * 40,
        b"TOOL_ONLY",
        b"TOOL_ONLY",
        (),
        0,
        (),
        (),
    )
    cases = tool.validate_results(result_report())["case_ids"]
    relay: dict[str, Any] = {"web_internal_ip": "172.24.0.3", "requests": []}
    for ordinal, (title, identity) in enumerate(cases.items(), 1):
        folder = destination / "playwright/test-results" / str(ordinal) / "offline-all-http"
        folder.mkdir(parents=True)
        url = tool.RELAY_URL + "/TOOL_ONLY/case" + str(ordinal)
        body = b"TOOL_ONLY response"
        (folder / "response.body").write_bytes(body)
        (folder / "inventory.json").write_bytes(
            raw(
                {
                    "protocol": "bounded-funds-offline-all-http-v1",
                    "title": title,
                    "test_id": identity,
                    "requested_channel": "msedge",
                    "errors": [],
                    "events": [
                        {
                            "kind": "REQUEST",
                            "url": url,
                            "method": "GET",
                            "body_sha256": tool.sha(b""),
                            "body_base64": "",
                        },
                        {
                            "kind": "RESPONSE",
                            "url": url,
                            "method": "GET",
                            "body_file": "response.body",
                            "body_sha256": tool.sha(body),
                            "bytes": len(body),
                        },
                    ],
                }
            )
        )
        headers = (
            f"GET /TOOL_ONLY/case{ordinal} HTTP/1.1\r\n"
            "Host: 127.0.0.1:15183\r\nConnection: keep-alive\r\n\r\n"
        ).encode()
        response = b"HTTP/1.1 200 OK\r\n\r\n" + body
        forward = request_frame(headers, b"", 15183)
        prefix = relay_dir / f"request-{ordinal:05d}"
        for suffix, value in (
            ("client.http", headers),
            ("forwarded.http", forward),
            ("response.http", response),
            ("stderr.log", b""),
        ):
            Path(str(prefix) + "-" + suffix).write_bytes(value)
        relay["requests"].append(
            {
                "status": "FORWARDED_ONCE",
                "exit_code": 0,
                "client_sha256": tool.sha(headers),
                "forwarded_sha256": tool.sha(forward),
                "body_sha256": tool.sha(b""),
                "response_sha256": tool.sha(response),
                "command": [
                    "docker",
                    "exec",
                    "-i",
                    transport.api_container_id,
                    "/opt/bf-venv/bin/python",
                    "-c",
                    relay_program(relay["web_internal_ip"]).decode(),
                ],
            }
        )
    return {
        "destination": destination,
        "ready": ready,
        "relay": relay,
        "cases": cases,
        "dir": directory,
    }


def test_actual_complete_resource_relay_denominator(network_fixture: dict[str, Any]) -> None:
    f = network_fixture
    result = tool.validate_network_originals(f["destination"], f["ready"], f["relay"], f["cases"])
    assert result["request_count"] == 4 and result["all_urls_exact_loopback"] is True
    assert result["body_transformed"] is False


@pytest.mark.parametrize(
    "case",
    [
        "missing_case",
        "wrong_test",
        "external",
        "body",
        "relay_bytes",
        "foreign_argv",
        "retry",
        "extra",
        "missing_request",
        "response_path",
    ],
)
def test_no_omitted_injected_or_foreign_actual_http(
    network_fixture: dict[str, Any], case: str
) -> None:
    f = network_fixture
    inventory_path = next((f["destination"] / "playwright/test-results").rglob("inventory.json"))
    value = json.loads(inventory_path.read_bytes())
    if case == "missing_case":
        inventory_path.rename(inventory_path.with_suffix(".missing"))
    elif case == "relay_bytes":
        path = f["ready"].relay.parent / "request-00001-client.http"
        path.write_bytes(path.read_bytes() + b"TOOL_ONLY_CHANGED")
    elif case == "foreign_argv":
        f["relay"]["requests"][0]["command"][3] = "f" * 64
    elif case == "retry":
        f["relay"]["requests"][0]["status"] = "RETRIED"
    elif case == "extra":
        f["relay"]["requests"].append(copy.deepcopy(f["relay"]["requests"][0]))
    elif case == "missing_request":
        value["events"] = value["events"][1:]
        inventory_path.write_bytes(raw(value))
    else:
        if case == "wrong_test":
            value["test_id"] = "TOOL_ONLY_other"
        elif case == "external":
            value["events"][0]["url"] = "https://example.com/"
        elif case == "response_path":
            value["events"][1]["body_file"] = "../outside.body"
        else:
            value["events"][0]["body_base64"] = base64.b64encode(b"TOOL_ONLY_BODY").decode()
        inventory_path.write_bytes(raw(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        tool.validate_network_originals(f["destination"], f["ready"], f["relay"], f["cases"])


def test_missing_new512_adapter_has_no64_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class Missing:
        def __truediv__(self, _: str) -> Missing:
            return self

        def is_file(self) -> bool:
            return False

    monkeypatch.setattr(tool, "ROOT", Missing())
    with pytest.raises(ValueError, match="NOT_IMPLEMENTED"):
        tool.snapshot_adapter()


def test_original_container_constructor_still_valid(native: dict[str, Any]) -> None:
    value = validate_owned_transport(
        *packed(native),
        native["current"],
        native["head"],
    )
    assert value.database.startswith("bf_test_")
    assert native["owner"]["tool_input_context"] == "TOOL_ONLY"
