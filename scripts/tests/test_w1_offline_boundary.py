"""TOOL_ONLY: synthetic raw probes and mocked processes; no Docker or finance."""

from __future__ import annotations

import ast
import base64
import json
import runpy
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts import w1_offline_boundary as tool
from scripts.demo_container_transport import sha
from scripts.tests.test_demo_container_transport import native as native
from scripts.tests.test_demo_container_transport import packed, raw
from scripts.tests.test_demo_offline_network import network_fixture

V4 = (
    "Iface\tDestination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
    "eth0 000013AC 00000000 0001 0 0 0 0000FFFF 0 0 0\n"
)
V6 = "0" * 32 + " 00 " + "0" * 32 + " 00 " + "0" * 32 + " ffffffff 00000001 00000000 00200200 lo\n"


def api_fixture() -> dict[str, Any]:
    return {
        "tool_input_context": "TOOL_ONLY",
        "protocol": "bounded-funds-api-egress-probe-v1",
        "route_files": {
            "/proc/net/route": V4,
            "/proc/net/ipv6_route": V6,
            "/etc/resolv.conf": "nameserver 127.0.0.1\n",
        },
        "targets": [
            {
                "host": host,
                "port": port,
                "timeout_seconds": 3,
                "result": "ERROR",
                "errno": 101,
                "message": "[Errno 101] Network is unreachable",
                "wall_seconds": 0.001,
            }
            for host, port in tool.IP_TARGETS
        ],
        "dns": {
            "argv": ["/opt/bf-venv/bin/python", "-c", tool.DNS_CODE],
            "exit_code": 0,
            "timeout_seconds": 4,
            "stdout_base64": base64.b64encode(
                raw(
                    {
                        "name": "example.com",
                        "result": "GAIERROR",
                        "errno": -3,
                        "message": "Temporary failure in name resolution",
                    }
                )
            ).decode(),
            "stderr_base64": "",
        },
    }


def box_fixture() -> dict[str, tuple[int | None, bytes, bytes]]:
    result: dict[str, tuple[int | None, bytes, bytes]] = {
        "inventory": (0, b"cat\nip\nwget\nnslookup\ntimeout\n", b""),
        "route4": (0, b"172.19.0.0/16 dev eth0 scope link src 172.19.0.2\n", b""),
        "route6": (0, b"", b""),
        "resolver": (0, b"nameserver 127.0.0.1\n", b""),
        "ip-0": (1, b"", b"wget: network is unreachable\n"),
        "ip-1": (1, b"", b"wget: no route to host\n"),
        "dns": (1, b"", b"Server 127.0.0.1: REFUSED\n"),
    }
    result.update(
        {
            name + "-help": (0, b"", b"BusyBox TOOL_ONLY help\n")
            for name in ("ip", "wget", "nslookup", "timeout", "cat")
        }
    )
    return result


@pytest.fixture
def tool_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tool.ROOT / ".runtime/tool-tests/w1-offline-boundary" / uuid4().hex
    root.mkdir(parents=True)
    monkeypatch.setattr(tool, "ROOT", root)
    return root


@pytest.mark.parametrize("action", ["observe", "isolate", "probe"])
def test_tool_only_default_has_no_file_reads_processes_or_connections(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool_root: Path,
    action: str,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No process or source discovery permitted without --run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(runpy, "run_path", forbidden)
    assert (
        tool.main(
            [
                "--action",
                action,
                "--registration",
                "missing",
                "--start",
                "missing",
                "--output",
                "no-created-directory",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert (
        result["status"] == "PREPARED_NOT_EXECUTED"
        and result["connection_made"] is False
        and result["offline_accepted"] is False
    )
    assert not (tool_root / "no-created-directory").exists()


def test_tool_only_api_probe_observes_only_recorded_no_route_ip_and_dns() -> None:
    result = tool.classify_api_probe(raw(api_fixture()), 0)
    assert result["observed"] is True
    assert tool.routes_without_default(V4, V6) is True


@pytest.mark.parametrize(
    "case",
    [
        "default4",
        "default6",
        "connected",
        "timeout",
        "wrong_endpoint",
        "only_one_ip",
        "dns_resolved",
        "dns_timeout",
        "dns_boolean_exit",
        "dns_wrong_argv",
        "dns_wrong_timeout",
        "dns_timedout_gai",
        "missing_route",
        "bool_exit",
    ],
)
def test_tool_only_api_probe_never_calls_incomplete_results_observed(case: str) -> None:
    value = api_fixture()
    if case == "default4":
        value["route_files"]["/proc/net/route"] += (
            "eth0 00000000 010013AC 0003 0 0 0 00000000 0 0 0\n"
        )
    elif case == "default6":
        value["route_files"]["/proc/net/ipv6_route"] = V6.replace("00200200", "00000001")
    elif case == "connected":
        value["targets"][0]["result"] = "CONNECTED"
    elif case == "timeout":
        value["targets"][0]["errno"] = None
    elif case == "wrong_endpoint":
        value["targets"][0]["host"] = "127.0.0.1"
    elif case == "only_one_ip":
        value["targets"].pop()
    elif case == "dns_resolved":
        value["dns"]["stdout_base64"] = base64.b64encode(
            raw({"name": "example.com", "result": "RESOLVED", "rows": []})
        ).decode()
    elif case == "dns_timeout":
        value["dns"] = {"result": "TIMEOUT_UNVERIFIED"}
    elif case == "dns_boolean_exit":
        value["dns"]["exit_code"] = False
    elif case == "dns_wrong_argv":
        value["dns"]["argv"][2] = "print('success')"
    elif case == "dns_wrong_timeout":
        value["dns"]["timeout_seconds"] = 40
    elif case == "dns_timedout_gai":
        value["dns"]["stdout_base64"] = base64.b64encode(
            raw(
                {
                    "name": "example.com",
                    "result": "GAIERROR",
                    "errno": -3,
                    "message": "operation timed out",
                }
            )
        ).decode()
    elif case == "missing_route":
        value["route_files"]["/proc/net/route"] = {"errno": 2}
    if case == "only_one_ip":
        with pytest.raises(ValueError):
            tool.classify_api_probe(raw(value), 0)
    else:
        assert (
            tool.classify_api_probe(raw(value), False if case == "bool_exit" else 0)["observed"]
            is False
        )


@pytest.mark.parametrize("exit_code", [None, 1, 124, 137])
def test_tool_only_api_outer_timeout_or_failure_is_deferred(exit_code: int | None) -> None:
    assert tool.classify_api_probe(b"not-complete-original", exit_code)["observed"] is False


def test_tool_only_busybox_all_real_outputs_required() -> None:
    assert tool.classify_busybox(box_fixture())["observed"] is True


@pytest.mark.parametrize(
    "case",
    [
        "missing_inventory",
        "missing_ip",
        "missing_probe",
        "empty_help",
        "failed_help",
        "route_failure",
        "empty_route",
        "empty_resolver",
        "default4",
        "default6",
        "ip_connected",
        "ip_timeout124",
        "ip_timeout137",
        "ip_exception",
        "ip_unknown",
        "dns_resolved",
        "dns_timeout",
        "dns_unrun",
        "boolean_route",
    ],
)
def test_tool_only_busybox_incomplete_or_unknown_is_not_offline(case: str) -> None:
    value = box_fixture()
    if case == "missing_inventory":
        value.pop("inventory")
    elif case == "missing_ip":
        value["inventory"] = (0, b"cat\nwget\nnslookup\ntimeout", b"")
    elif case == "missing_probe":
        value.pop("ip-1")
    elif case == "empty_help":
        value["wget-help"] = (0, b"", b"")
    elif case == "failed_help":
        value["ip-help"] = (1, b"", b"unknown applet")
    elif case == "route_failure":
        value["route6"] = (1, b"", b"unsupported")
    elif case == "empty_route":
        value["route4"] = (0, b"", b"")
    elif case == "empty_resolver":
        value["resolver"] = (0, b"", b"")
    elif case in {"default4", "default6"}:
        value["route" + case[-1]] = (0, b"default via 172.19.0.1\n", b"")
    elif case == "ip_connected":
        value["ip-0"] = (0, b"actual remote response", b"")
    elif case == "ip_timeout124":
        value["ip-0"] = (124, b"", b"network is unreachable")
    elif case == "ip_timeout137":
        value["ip-0"] = (137, b"", b"network is unreachable")
    elif case == "ip_exception":
        value["ip-0"] = (None, b"", b"timeout")
    elif case == "ip_unknown":
        value["ip-0"] = (1, b"", b"unknown error")
    elif case == "dns_resolved":
        value["dns"] = (0, b"Address 1.2.3.4", b"")
    elif case == "dns_timeout":
        value["dns"] = (1, b"", b"REFUSED; query timed out")
    elif case == "dns_unrun":
        value.pop("dns")
    elif case == "boolean_route":
        value["route4"] = (False, b"172.19.0.0/16 dev eth0", b"")
    assert tool.classify_busybox(value)["observed"] is False


def test_tool_only_probe_argv_fixed_immutable_no_shell() -> None:
    commands = dict(tool.busybox_commands("b" * 64))
    assert (
        commands["ip-0"][-1] == "http://1.1.1.1:443/"
        and commands["ip-1"][-1] == "http://8.8.8.8:53/"
    )
    assert commands["dns"][-1] == "example.com"
    assert all(
        command[:4] == ("docker", "exec", "b" * 64, "/bin/busybox") for command in commands.values()
    )
    for invalid in ("web", "b" * 12, "b" * 64 + " && unsafe"):
        with pytest.raises(ValueError):
            tool.busybox_commands(invalid)
    tree = ast.parse(tool.API_PROBE)
    dns_code = next(
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and any(isinstance(target, ast.Name) and target.id == "dns_code" for target in node.targets)
    )
    assert dns_code == tool.DNS_CODE
    assert not any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree))


def test_tool_only_capture_preserves_raw_and_explicitly_transforms_public(tool_root: Path) -> None:
    public, private = tool_root / "public", tool_root / "private"
    public.mkdir()
    private.mkdir()
    manifest: dict[str, Any] = {"artifacts": [], "commands": [], "tool_input_context": "TOOL_ONLY"}
    capture = tool.Capture(public, private, manifest)
    original = (
        b'[ {"Password":"secret-1", "Env":["POSTGRES_PASSWORD=secret-2",'
        b'"DATABASE_URL=postgresql+psycopg://bf_demo:secret-3@127.0.0.1:54329/bf_test_x"]} ]\r\n'
    )
    location = capture.save("inspect", original)
    assert location.read_bytes() == original
    transformed = (public / "inspect.redacted").read_bytes()
    assert not any(password in transformed for password in (b"secret-1", b"secret-2", b"secret-3"))
    assert b"127.0.0.1:54329/bf_test_x" in transformed
    artifact = manifest["artifacts"][0]
    assert artifact["private_original_sha256"] == sha(original)
    assert artifact["public_sha256"] == sha(transformed)
    assert (
        artifact["public_transformation"] == "REDACTED_REENCODING_V1_NOT_ORIGINAL"
        and original != transformed
    )
    with pytest.raises(FileExistsError):
        capture.save("inspect", original)
    with pytest.raises(ValueError):
        capture.save("../escaped", original)


def test_tool_only_image_json_representation_difference_preserves_both_raw_hashes() -> None:
    value = [
        {
            "Id": "sha256:" + "a" * 64,
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"Labels": {"source": "b" * 64}},
            "Metadata": {"size": 10},
        }
    ]
    before = raw(value)
    actual = json.dumps(value, indent=4).encode() + b"\n"
    proof = tool.admit_image_observation(before, actual)
    assert before != actual and proof["raw_bytes_equal"] is False
    assert proof["start_original_sha256"] == sha(before) and proof["fresh_original_sha256"] == sha(
        actual
    )
    assert proof["complete_typed_json_equal"] is True


@pytest.mark.parametrize(
    "case",
    [
        "id",
        "os",
        "architecture",
        "label",
        "extra_label",
        "extra_field",
        "numeric_type",
        "boolean_type",
        "duplicate_key",
    ],
)
def test_tool_only_image_bridge_rejects_any_value_or_type_difference(case: str) -> None:
    before = [
        {
            "Id": "sha256:" + "a" * 64,
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"Labels": {"source": "b" * 64}},
            "Metadata": {"size": 10},
        }
    ]
    after = json.loads(raw(before))
    if case == "id":
        after[0]["Id"] = "sha256:" + "c" * 64
    elif case == "os":
        after[0]["Os"] = "windows"
    elif case == "architecture":
        after[0]["Architecture"] = "arm64"
    elif case == "label":
        after[0]["Config"]["Labels"]["source"] = "c" * 64
    elif case == "extra_label":
        after[0]["Config"]["Labels"]["new"] = "unknown"
    elif case == "extra_field":
        after[0]["Descriptor"] = {}
    elif case == "numeric_type":
        after[0]["Metadata"]["size"] = 10.0
    elif case == "boolean_type":
        after[0]["Metadata"]["size"] = True
    after_raw = b'[{"Id": "first", "Id": "second"}]' if case == "duplicate_key" else raw(after)
    with pytest.raises(ValueError):
        tool.admit_image_observation(raw(before), after_raw)


@pytest.mark.parametrize("mode", ["exit", "timeout", "spawn"])
def test_tool_only_capture_actual_mocked_outcome_no_fake_zero(
    monkeypatch: pytest.MonkeyPatch, tool_root: Path, mode: str
) -> None:
    public, private = tool_root / "public", tool_root / "private"
    public.mkdir()
    private.mkdir()
    manifest: dict[str, Any] = {"artifacts": [], "commands": [], "tool_input_context": "TOOL_ONLY"}
    capture = tool.Capture(public, private, manifest)
    calls = []

    def process(argv: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        if mode == "timeout":
            raise subprocess.TimeoutExpired(
                argv, 3, output=b"partial original", stderr=b"partial error"
            )
        if mode == "spawn":
            raise FileNotFoundError(2, "TOOL_ONLY no binary")
        return subprocess.CompletedProcess(argv, 1, b"original output", b"original error")

    monkeypatch.setattr(subprocess, "run", process)
    result = capture.command(
        "probe", ("docker", "exec", "a" * 64, "fixed"), stdin=b"TOOL_ONLY stdin", timeout=3
    )
    assert len(calls) == 1 and calls[0][1]["timeout"] == 3 and "shell" not in calls[0][1]
    record = manifest["commands"][0]
    assert record["exit_code"] == result[0] and record["stdout_sha256"] == sha(result[1])
    assert record["finished_at"] >= record["started_at"]
    assert (
        record["outcome"]
        == {"exit": "EXITED", "timeout": "TIMEOUT_UNVERIFIED", "spawn": "SPAWN_FAILED"}[mode]
    )
    assert result[0] == 1 if mode == "exit" else result[0] is None


def test_tool_only_fresh_isolated_inspect_retains_namespace_and_health(
    native: dict[str, Any],
) -> None:
    _, _, after, _, networks = network_fixture(native)
    tool.same_isolated(after, after, networks, networks)
    for mutation in ("Image", "HostConfig", "NetworkSettings", "unhealthy", "init_failed"):
        changed = json.loads(after)
        if mutation == "unhealthy":
            changed[0]["State"]["Health"]["Status"] = "unhealthy"
        elif mutation == "init_failed":
            next(
                row
                for row in changed
                if row["Config"]["Labels"]["com.docker.compose.service"] == "init"
            )["State"]["ExitCode"] = 1
        else:
            changed[0][mutation] = None
        with pytest.raises(ValueError):
            tool.same_isolated(after, raw(changed), networks, networks)


@pytest.mark.parametrize("action", ["observe", "isolate", "probe"])
def test_tool_only_cli_mocked_native_ownership_never_invokes_financial(
    monkeypatch: pytest.MonkeyPatch, native: dict[str, Any], tool_root: Path, action: str
) -> None:
    actual_root = Path(__file__).resolve().parents[2]
    for name in tool.PINNED:
        path = tool_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((actual_root / name).read_bytes())
    (tool_root / "scripts/w1_demo_deployment.py").write_bytes(
        b"# TOOL_ONLY source discovery fixture\n"
    )
    monkeypatch.setattr(
        runpy, "run_path", lambda _: {"source_state": lambda: native["current"].copy()}
    )
    registration = tool_root / ".runtime/w1-deployment/TOOL_ONLY/manifest.json"
    start = tool_root / "docs/progress/evidence/W1/TOOL_ONLY-start/manifest.json"
    registration.parent.mkdir(parents=True)
    start.parent.mkdir(parents=True)
    owner, start_raw, before, images = packed(native)
    registration.write_bytes(owner)
    start.write_bytes(start_raw)
    (start.parent / "after-inspect.log").write_bytes(before)
    for name, image in images.items():
        (start.parent / (name + "-image.log")).write_bytes(image)
    _, _, after, before_networks, after_networks = network_fixture(native)
    detached = False
    calls: list[tuple[str, ...]] = []

    def process(argv: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        nonlocal detached
        calls.append(argv)
        if argv[:3] == ("git", "rev-parse", "HEAD"):
            output = native["head"].encode() + b"\n"
        elif argv[:3] == ("docker", "network", "disconnect"):
            assert argv == ("docker", "network", "disconnect", "f" * 64, "b" * 64) and not detached
            detached = True
            output = b""
        elif argv[:2] == ("docker", "inspect"):
            output = after if detached else before
        elif argv[:3] == ("docker", "network", "inspect"):
            output = after_networks if detached else before_networks
        elif argv[:3] == ("docker", "image", "inspect"):
            output = next(
                image for image in images.values() if json.loads(image)[0]["Id"] == argv[-1]
            )
            output = json.dumps(json.loads(output), indent=4).encode() + b"\n"
        elif argv[:3] == ("docker", "exec", "-i"):
            assert argv == ("docker", "exec", "-i", "a" * 64, "/opt/bf-venv/bin/python", "-")
            assert kwargs["input"] == tool.API_PROBE
            output = raw(api_fixture())
        elif argv[:2] == ("docker", "exec"):
            assert argv[2] in {"b" * 64, "d" * 64}
            probe = next(
                label for label, command in tool.busybox_commands(argv[2]) if argv == command
            )
            code, stdout, stderr = box_fixture()[probe]
            assert code is not None
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        else:
            raise AssertionError("Unexpected or financial command")
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(subprocess, "run", process)
    prior = "docs/progress/evidence/W1/TOOL_ONLY-prior"
    if action == "probe":
        assert (
            tool.main(
                [
                    "--run",
                    "--action",
                    "isolate",
                    "--registration",
                    str(registration),
                    "--start",
                    str(start),
                    "--output",
                    prior,
                ]
            )
            == 0
        )
        calls.clear()
    output = "docs/progress/evidence/W1/TOOL_ONLY-" + action
    assert (
        tool.main(
            [
                "--run",
                "--action",
                action,
                "--registration",
                str(registration),
                "--start",
                str(start),
                "--output",
                output,
                *(["--prior-isolation", prior + "/manifest.json"] if action == "probe" else []),
            ]
        )
        == 0
    )
    result = json.loads((tool_root / output / "manifest.json").read_bytes())
    assert result["offline_accepted"] is False and result["financial_operations_executed"] is False
    assert result["source_before"] == result["source_after"] == native["current"]
    assert result["producer_sha256"] == result["producer_after_sha256"]
    assert result["image_representation_bindings"]["before-api"]["raw_bytes_equal"] is False
    assert (
        result["image_representation_bindings"]["before-api"]["complete_typed_json_equal"] is True
    )
    assert sum(argv[:3] == ("docker", "network", "disconnect") for argv in calls) == (
        1 if action == "isolate" else 0
    )
    assert any(argv[:2] == ("docker", "exec") for argv in calls) is (action == "probe")
    if action == "probe":
        assert result["status"] == "EGRESS_BOUNDARY_OBSERVED"
        assert result["three_golden_chains"] == "NOT_RUN" and set(result["probes"]) == {
            "api",
            "db",
            "web",
        }
    assert all(row["public_transformation"].endswith("NOT_ORIGINAL") for row in result["artifacts"])
