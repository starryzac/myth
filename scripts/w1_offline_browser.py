"""Prepare four original UI cases against one verified owned offline Compose deployment.

The default performs no read, connection, Docker command or browser launch. Only
explicit --run admits current source-bound raw prerequisites. No database/seed/
container/volume/relay lifecycle or financial outcome injection is implemented.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-offline-browser-v1"
BROKER_PROTOCOL = "bounded-funds-scenario-v1"
RELAY_URL = "http://127.0.0.1:15183"
CASE_TITLES = (
    "工资到账至真实目标分配与自动申购",
    "自然语言新目标修订确认、零归属建立与后续新收入授权分配",
    "大额消费触发合法自动无损赎回",
    "定存损失单独询问并绑定原报价回执",
)
SPEC = "apps/web/tests/e2e/w1-demo.spec.ts"
KINDS = {"BEGIN", "READ_ONLY", "MONEY", "RESET"}
Snapshot = dict[str, list[dict[str, Any]]]


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        require(key not in result, "Duplicate original JSON key")
        result[key] = value
    return result


def nonfinite(value: str) -> Any:
    raise ValueError("Nonfinite original JSON number: " + value)


def original(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=nonfinite)


def obj(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "Original object missing")
    return cast(dict[str, Any], value)


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    require(
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port == 15183
        and parsed.username is None
        and parsed.password is None
        and parsed.netloc == "127.0.0.1:15183",
        "Every actual resource must use the exact registered loopback relay",
    )
    return parsed.path + ("?" + parsed.query if parsed.query else "") or "/"


def aware(value: Any) -> datetime:
    require(isinstance(value, str), "Original aware timestamp missing")
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None and result.utcoffset() is not None, "Naive original clock")
    return result


def write_new(path: Path, raw: bytes) -> dict[str, Any]:
    with path.open("xb") as handle:
        handle.write(raw)
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw), "bytes": len(raw)}


def proof_path(value: str, prefix: str) -> Path:
    path = (ROOT / value).resolve()
    require(path.is_relative_to((ROOT / prefix).resolve()) and path.is_file(), "Original scope")
    return path


def artifact(raw_manifest: dict[str, Any], label: str) -> bytes:
    entries = [row for row in raw_manifest["artifacts"] if row.get("label") == label]
    require(len(entries) == 1, "Original capture missing or repeated: " + label)
    row = obj(entries[0])
    path = proof_path(row["private_original_path"], ".runtime/w1-offline-boundary-private")
    raw = path.read_bytes()
    require(
        row.get("private_original_sha256") == sha(raw)
        and type(row.get("private_original_bytes")) is int
        and row["private_original_bytes"] == len(raw),
        "Native original byte hash/denominator changed",
    )
    return raw


def native_command(
    manifest: dict[str, Any], label: str, argv: tuple[str, ...], stdin: bytes = b""
) -> tuple[int | None, bytes, bytes]:
    rows = [row for row in manifest["commands"] if row.get("label") == label]
    require(len(rows) == 1, "Actual command missing or repeated: " + label)
    row = obj(rows[0])
    stdout, stderr = artifact(manifest, label + "-stdout"), artifact(manifest, label + "-stderr")
    require(
        row.get("argv") == list(argv)
        and row.get("stdout_sha256") == sha(stdout)
        and row.get("stderr_sha256") == sha(stderr)
        and row.get("stdin_sha256") == sha(stdin)
        and aware(row.get("started_at")) <= aware(row.get("finished_at")),
        "Actual command argv, source bytes or timestamps differ",
    )
    if stdin:
        require(artifact(manifest, label + "-stdin") == stdin, "Actual command stdin differs")
    code = row.get("exit_code")
    require(code is None or type(code) is int, "Actual native exit is not an integer")
    return code, stdout, stderr


@dataclass(frozen=True)
class Readiness:
    registration: Path
    start: Path
    isolation: Path
    probe: Path
    relay: Path
    transport: Any
    current_source: dict[str, str]
    current_head: str
    isolated_containers: bytes
    isolated_networks: bytes
    network_ids: tuple[str, ...]
    relay_initial_count: int
    prerequisite_hashes: tuple[tuple[str, str], ...]
    prerequisite_bytes: tuple[tuple[str, bytes], ...]


def verify_probe(
    probe: dict[str, Any], transport: Any, current: dict[str, str], head: str
) -> tuple[bytes, bytes]:
    from scripts import w1_offline_boundary as boundary
    from scripts.demo_offline_endpoint_accounting import METHOD
    from scripts.demo_offline_network import containers

    require(
        probe.get("protocol") == "bounded-funds-offline-reobserve-v1"
        and probe.get("action") == "probe"
        and probe.get("method") == METHOD
        and probe.get("status") == "EGRESS_BOUNDARY_OBSERVED"
        and type(probe.get("exit_code")) is int
        and probe["exit_code"] == 0
        and probe.get("offline_accepted") is False
        and probe.get("disconnect_executed_by_this_run") is False
        and probe.get("source_before") == probe.get("source_after") == current
        and probe.get("source_head") == probe.get("source_head_after") == head
        and probe.get("owner_uuid") == transport.owner_uuid
        and probe.get("owner_run_id") == transport.owner_run_id
        and probe.get("source_digest") == transport.source_digest,
        "Current actual source-bound isolated probe missing",
    )
    for label, name, field in (
        ("producer", "scripts/w1_offline_reobserve.py", "producer"),
        ("bridge", "scripts/demo_offline_endpoint_accounting.py", "bridge"),
        ("source-helper", "scripts/w1_demo_deployment.py", "source_helper"),
    ):
        raw = (ROOT / name).read_bytes()
        require(
            artifact(probe, label + "-before") == artifact(probe, label + "-after") == raw
            and probe.get(field + "_sha256") == probe.get(field + "_after_sha256") == sha(raw),
            "Original probe producer changed",
        )
    before, after = (
        artifact(probe, "before-containers-stdout"),
        artifact(probe, "after-containers-stdout"),
    )
    networks_before, networks_after = (
        artifact(probe, "before-networks-stdout"),
        artifact(probe, "after-networks-stdout"),
    )
    boundary.same_isolated(before, after, networks_before, networks_after)
    actual = containers(before)
    code, stdout, _ = native_command(
        probe,
        "api-probe",
        ("docker", "exec", "-i", transport.api_container_id, "/opt/bf-venv/bin/python", "-"),
        boundary.API_PROBE,
    )
    observations = {"api": boundary.classify_api_probe(stdout, code)}
    for name in ("db", "web"):
        outputs = {
            label: native_command(probe, name + "-" + label, argv)
            for label, argv in boundary.busybox_commands(actual[name]["Id"])
        }
        observations[name] = boundary.classify_busybox(outputs)
    require(
        observations == probe.get("probes")
        and all(value.get("observed") is True for value in observations.values()),
        "Original complete per-container route/IP/DNS probes do not establish this boundary",
    )
    return after, networks_after


def verify_relay(
    manifest: dict[str, Any], transport: Any, current: dict[str, str], head: str
) -> None:
    require(
        manifest.get("protocol") == "bounded-funds-loopback-relay-v1"
        and manifest.get("status") == "RUNNING"
        and manifest.get("listen_host") == "127.0.0.1"
        and type(manifest.get("listen_port")) is int
        and manifest["listen_port"] == 15183
        and manifest.get("owner_uuid") == transport.owner_uuid
        and manifest.get("api_container_id") == transport.api_container_id
        and manifest.get("source_before") == current
        and manifest.get("source_head") == head
        and manifest.get("registration_sha256") == transport.registration_sha256
        and manifest.get("start_manifest_sha256") == transport.start_manifest_sha256
        and manifest.get("producer_sha256")
        == sha((ROOT / "scripts/demo_loopback_relay.py").read_bytes())
        and manifest.get("transport_header_change") == "Connection: close"
        and manifest.get("financial_body_transformed") is False
        and type(manifest.get("automatic_retries")) is int
        and manifest["automatic_retries"] == 0
        and not manifest.get("errors"),
        "Actual original running relay ownership/source/transport differs",
    )
    require(type(manifest.get("requests")) is list, "Relay original request inventory missing")
    require(
        all(row.get("status") == "FORWARDED_ONCE" for row in manifest["requests"]),
        "Relay has incomplete/failed original requests",
    )


def validate_readiness(
    paths: dict[str, str],
    current: dict[str, str],
    head: str,
    originals: dict[str, bytes] | None = None,
) -> Readiness:
    from scripts import w1_offline_boundary as boundary
    from scripts.demo_container_transport import validate_owned_transport
    from scripts.demo_loopback_relay import current_web_endpoint
    from scripts.demo_offline_endpoint_accounting import FROZEN, validate_transition_accounting
    from scripts.demo_offline_network import build_transition, containers, networks

    require(
        set(paths) == {"registration", "start", "isolation", "probe", "relay"}, "Five originals"
    )
    files = {
        name: proof_path(
            value,
            ".runtime/w1-deployment" if name == "registration" else "docs/progress/evidence/W1",
        )
        for name, value in paths.items()
    }
    if originals is None:
        raw = {name: path.read_bytes() for name, path in files.items()}
    else:
        require(set(originals) == set(paths), "Five explicit initial raw originals required")
        raw = dict(originals)
        require(
            all(path.read_bytes() == raw[name] for name, path in files.items() if name != "relay"),
            "Immutable initial prerequisite differs from its original path",
        )
    prior, probe, relay = (obj(original(raw[name])) for name in ("isolation", "probe", "relay"))
    require(
        all(sha((ROOT / name).read_bytes()) == digest for name, digest in FROZEN.items()),
        "Frozen transport/network dependency changed",
    )
    require(
        prior.get("protocol") == boundary.PROTOCOL
        and prior.get("action") == "isolate"
        and prior.get("producer_sha256")
        == prior.get("producer_after_sha256")
        == sha((ROOT / "scripts/w1_offline_boundary.py").read_bytes())
        and prior.get("source_before") == prior.get("source_after") == current
        and prior.get("source_head") == prior.get("source_head_after") == head
        and prior.get("status") in {"FAILED", "ISOLATED_TOPOLOGY_EGRESS_UNVERIFIED"},
        "Original executed isolation/source differs; failure must remain recorded",
    )
    require(
        artifact(prior, "registration") == raw["registration"]
        and artifact(prior, "start-manifest") == raw["start"]
        and artifact(probe, "registration") == raw["registration"]
        and artifact(probe, "start-manifest") == raw["start"]
        and artifact(probe, "prior-isolation-manifest") == raw["isolation"],
        "Original registration/start/isolation linkage differs",
    )
    images = {
        name: (files["start"].parent / (name + "-image.log")).read_bytes()
        for name in ("api", "web")
    }
    before, after = (
        artifact(prior, "before-containers-stdout"),
        artifact(prior, "after-containers-stdout"),
    )
    nb, na = artifact(prior, "before-networks-stdout"), artifact(prior, "after-networks-stdout")
    owned = validate_owned_transport(
        raw["registration"],
        raw["start"],
        before,
        images,
        current,
        head,
        require_started_inspect=False,
    )
    transition = build_transition(owned, before, nb, current, head)
    dc, out, err = native_command(prior, "disconnect", transition.argv)
    require(type(dc) is int and dc == 0, "Original actual unique disconnect failed")
    result = validate_transition_accounting(
        owned,
        transition,
        before,
        after,
        nb,
        na,
        artifact(prior, "disconnect-command"),
        out,
        err,
        current,
        head,
    )
    require(result == probe.get("prior_transition_revalidated"), "Actual prior accounting differs")
    observed, observed_nets = verify_probe(probe, owned, current, head)
    boundary.same_isolated(after, observed, na, observed_nets)
    for name, image in images.items():
        for manifest in (prior, probe):
            for phase in ("before", "after"):
                boundary.admit_image_observation(
                    image, artifact(manifest, phase + "-" + name + "-image-stdout")
                )
    verify_relay(relay, owned, current, head)
    require(
        relay.get("web_container_id") == transition.web_container_id, "Relay Web identity differs"
    )
    net = networks(observed_nets, owned)
    actual = containers(observed)
    require(
        set(actual["web"]["NetworkSettings"]["Networks"]) == {owned.project + "_demo_internal"},
        "Actual Web still has an external network",
    )
    capture = obj(relay.get("inspect_capture"))
    inspect_path = proof_path(capture["private_original_path"], ".runtime/w1-private-captures")
    inspect_raw = inspect_path.read_bytes()
    require(
        sha(inspect_raw) == capture.get("private_original_sha256")
        and capture.get("public_provenance") == "REGISTERED_PASSWORD_LITERAL_REDACTION_V1",
        "Original relay initial Docker inspect bytes/provenance differ",
    )
    relay_rows = original(inspect_raw)
    require(
        type(relay_rows) is list and len(relay_rows) == 2, "Original API/Web relay inspect missing"
    )
    relay_services = {
        row["Config"]["Labels"]["com.docker.compose.service"]: row for row in relay_rows
    }
    require(set(relay_services) == {"api", "web"}, "Original relay inspect service denominator")
    expected_ip = current_web_endpoint(
        relay_services["api"],
        relay_services["web"],
        containers(before)["web"],
        api_id=owned.api_container_id,
        db_id=owned.db_container_id,
        api_image_id=owned.api_image_id,
        project=owned.project,
    )
    require(
        relay.get("web_internal_ip")
        == expected_ip
        == actual["web"]["NetworkSettings"]["Networks"][owned.project + "_demo_internal"][
            "IPAddress"
        ],
        "Actual relay endpoint is not the same owned original Web",
    )
    return Readiness(
        files["registration"],
        files["start"],
        files["isolation"],
        files["probe"],
        files["relay"],
        owned,
        copy.deepcopy(current),
        head,
        observed,
        observed_nets,
        tuple(sorted(row["Id"] for row in net.values())),
        len(relay["requests"]),
        tuple((name, sha(value)) for name, value in sorted(raw.items()) if name != "relay"),
        tuple(sorted(raw.items())),
    )


def validate_broker(request: dict[str, Any], filename: str) -> dict[str, Any]:
    identity = str(UUID(request["id"]))
    require(filename == identity + ".request.json", "Original broker filename identity differs")
    require(
        request.get("protocol") == BROKER_PROTOCOL
        and isinstance(request.get("scenario_id"), str)
        and re.fullmatch(r"w1-[A-Za-z0-9]{1,64}", request["scenario_id"]) is not None,
        "Original broker protocol/scenario differs",
    )
    expected = str(UUID(request["expected_epoch_id"]))
    common = {"protocol", "id", "scenario_id", "operation", "expected_epoch_id"}
    operation = request.get("operation")
    if operation == "checkpoint":
        require(set(request) == common | {"label", "mode"}, "Checkpoint may not supply outputs")
        require(
            isinstance(request["label"], str)
            and re.fullmatch(r"[a-z0-9-]{1,90}", request["label"]) is not None
            and request["mode"] in KINDS,
            "Original checkpoint label/mode differs",
        )
    else:
        keys = {"ingest_goal_income": "goal_id", "verify_legacy_recovery": "action_id"}
        require(operation in keys, "Operation is outside these four original UI cases")
        resource = keys[cast(str, operation)]
        require(set(request) == common | {resource}, "Browser cannot supply clock/amount/authority")
        request = {**request, resource: str(UUID(request[resource]))}
    return {**request, "id": identity, "expected_epoch_id": expected}


def snapshot_business(decoded: Any) -> tuple[Snapshot, dict[str, Any]]:
    data = obj(original(decoded.snapshot_data))
    require("alembic_version" in data and len(data) == 24, "Full physical24 snapshot required")
    heads = data["alembic_version"]
    require(
        type(heads) is list
        and all(type(row) is dict and set(row) == {"version_num"} for row in heads),
        "Original migration head rows differ",
    )
    return cast(
        Snapshot, {name: rows for name, rows in data.items() if name != "alembic_version"}
    ), {
        "metadata_heads": [row["version_num"] for row in heads],
        "row_count": sum(len(rows) for rows in data.values()),
        "physical_tables": sorted(data),
        "bytes": len(decoded.snapshot_data),
        "data_sha256": sha(decoded.snapshot_data),
        "sha256": sha(decoded.snapshot_gzip),
    }


def validate_results(report: dict[str, Any]) -> dict[str, Any]:
    specs: list[dict[str, Any]] = []

    def visit(suites: list[dict[str, Any]]) -> None:
        for suite in suites:
            specs.extend(suite.get("specs", []))
            visit(suite.get("suites", []))

    visit(report.get("suites", []))
    require(
        len(specs) == 4 and {row.get("title") for row in specs} == set(CASE_TITLES),
        "Four exact named actual results required",
    )
    for spec in specs:
        require(
            isinstance(spec.get("id"), str)
            and 1 <= len(spec["id"]) <= 128
            and bool(re.sub(r"[^a-z0-9]", "", spec["id"], flags=re.I)),
            "Original actual test identity missing",
        )
        tests = spec.get("tests", [])
        require(spec.get("ok") is True and len(tests) == 1, "Skipped/duplicate actual case")
        results = tests[0].get("results", [])
        require(
            len(results) == 1
            and results[0].get("status") == "passed"
            and results[0].get("retry", 0) == 0
            and tests[0].get("projectName") == "edge",
            "Each case needs one genuine passed Edge attempt without retry",
        )
    require(not report.get("errors"), "Actual Playwright global errors")
    require(len({row["id"] for row in specs}) == 4, "Actual test identity repeated")
    return {
        "count": 4,
        "titles": list(CASE_TITLES),
        "attempts_per_case": 1,
        "complete_all7": False,
        "three_rounds": "NOT_RUN",
        "case_ids": {row["title"]: row["id"] for row in specs},
    }


def validate_reset_scope(checkpoints: list[dict[str, Any]], cases: dict[str, str]) -> None:
    require(set(cases) == set(CASE_TITLES), "Original four case identities missing")
    expected = {
        "w1-" + re.sub(r"[^a-z0-9]", "", value, flags=re.I)[:64] for value in cases.values()
    }
    require(len(expected) == 4, "Actual scenario identities collide")
    resets = [(index, row) for index, row in enumerate(checkpoints) if row.get("mode") == "RESET"]
    require(len(resets) == 4, "All four original UI reset oracles required")
    require(
        {row.get("scenario_id") for _, row in resets} == expected,
        "Reset actual case identity differs",
    )
    for index, row in resets:
        require(index > 0, "Original reset preceding BEGIN missing")
        previous = checkpoints[index - 1]
        require(
            row.get("label") == "independent-case-start-after"
            and previous.get("scenario_id") == row["scenario_id"]
            and previous.get("label") == "independent-case-start-before"
            and previous.get("mode") == "BEGIN"
            and type(row.get("result")) is dict
            and type(row["result"].get("oracle")) is dict,
            "Original named same-case reset pair/oracle missing",
        )


def child_environment(parent: dict[str, str], destination: Path, database: str) -> dict[str, str]:
    require(re.fullmatch(r"bf_test_[0-9a-f]{32}", database) is not None, "Owned test identity")
    clean = {
        key: value
        for key, value in parent.items()
        if not key.startswith(("BF_", "COMPOSE_", "PG", "BOUNDEDFUNDS_"))
        and key
        not in {
            "DATABASE_URL",
            "API_PROXY_TARGET",
            "VITE_API_BASE_URL",
            "PLAYWRIGHT_CHANNEL",
            "NODE_OPTIONS",
            "CI",
        }
    }
    clean.update(
        PYTHONUTF8="1",
        PYTHONUNBUFFERED="1",
        BF_W1_BROWSER_RUN="1",
        BF_W1_SERVERS_EXTERNAL="1",
        BF_W1_RUN_DIRECTORY=str(destination),
        BF_W1_WEB_PORT="15183",
        BF_W1_API_PORT="15184",
        DATABASE_URL=f"postgresql+psycopg://bf_demo@127.0.0.1:54329/{database}",
    )
    # DATABASE_URL satisfies the original browser guard only; no host DB/API process exists.
    return clean


def snapshot_adapter() -> ModuleType:
    path = ROOT / "scripts/demo_container_transport512.py"
    require(path.is_file(), "NOT_IMPLEMENTED: explicit 512MiB producer/decoder adapter missing")
    module = importlib.import_module("scripts.demo_container_transport512")
    require(
        callable(getattr(module, "build_snapshot_command512", None))
        and callable(getattr(module, "decode_snapshot_output512", None)),
        "NOT_IMPLEMENTED: current 512MiB transport interface missing",
    )
    return module


def tool_sources() -> dict[str, str]:
    names = (
        "scripts/w1_offline_browser.py",
        "scripts/demo_container_transport.py",
        "scripts/demo_container_transport512.py",
        "scripts/demo_container_snapshot_512.py",
        "scripts/w1_offline_reobserve.py",
        "scripts/w1_offline_boundary.py",
        "scripts/demo_offline_endpoint_accounting.py",
        "scripts/demo_offline_network.py",
        "scripts/demo_loopback_relay.py",
        "scripts/browser_checkpoint_oracles.py",
        ".runtime/drive_mvp404_browser.py",
        "apps/web/playwright.config.ts",
        SPEC,
    )
    require(all((ROOT / name).is_file() for name in names), "Required original/tool source missing")
    return {name: sha((ROOT / name).read_bytes()) for name in names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    for name in ("registration", "start", "isolation", "probe", "relay", "output"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    if not args.run:
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "status": "PREPARED_NOT_EXECUTED",
                    "connection_made": False,
                    "financial_operations_executed": False,
                    "complete_all7": False,
                    "three_rounds": "NOT_RUN",
                }
            )
        )
        return 0
    require(
        all(
            getattr(args, name) for name in ("registration", "start", "isolation", "probe", "relay")
        ),
        "Five explicit original prerequisite paths required",
    )
    return run_actual(args)


def run_actual(args: argparse.Namespace) -> int:
    """Called exclusively after explicit --run; child lifecycle is Playwright only."""
    from scripts.w1_demo_deployment import source_state

    adapter = snapshot_adapter()
    before = source_state()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    paths = {
        name: str(getattr(args, name))
        for name in ("registration", "start", "isolation", "probe", "relay")
    }
    readiness = validate_readiness(paths, before, head)
    tools = tool_sources()
    destination = (
        (ROOT / args.output).resolve()
        if args.output
        else ROOT
        / "output/playwright"
        / ("offline-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
    )
    require(
        destination.is_relative_to(ROOT / "output/playwright")
        and destination != ROOT / "output/playwright"
        and not destination.exists(),
        "Fresh owned Playwright output required",
    )
    node = shutil.which("node")
    require(node is not None, "Installed original Node runtime required")
    destination.mkdir(parents=True)
    for name in ("broker", "checkpoints", "rpc", "playwright", "native", "generated"):
        (destination / name).mkdir()
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL,
        "run_id": destination.name,
        "status": "INCOMPLETE",
        "started_at": now(),
        "owner_uuid": readiness.transport.owner_uuid,
        "owner_run_id": readiness.transport.owner_run_id,
        "database": readiness.transport.database,
        "project": readiness.transport.project,
        "source_before": before,
        "source_head": head,
        "tool_source_before": tools,
        "prerequisites": paths,
        "prerequisite_original_sha256": dict(readiness.prerequisite_hashes),
        "relay_url": RELAY_URL,
        "case_titles": list(CASE_TITLES),
        "case_denominator": 4,
        "complete_all7": False,
        "three_rounds": "NOT_RUN",
        "formal_database_touched": False,
        "host_api_or_database_started": False,
        "seed_or_database_lifecycle_performed": False,
        "relay_lifecycle_owned": False,
        "commands": [],
        "checkpoints": [],
        "rpc": [],
        "artifacts": [],
        "production_sla_measured": False,
    }
    runner = ActualRun(destination, manifest, readiness, adapter, tools, head, before)
    return runner.run(str(node))


class ActualRun:
    """Actual subprocess capture. No injected result/callback success path exists."""

    def __init__(
        self,
        destination: Path,
        manifest: dict[str, Any],
        readiness: Readiness,
        adapter: ModuleType,
        tools: dict[str, str],
        head: str,
        source: dict[str, str],
    ):
        self.destination, self.manifest, self.ready, self.adapter = (
            destination,
            manifest,
            readiness,
            adapter,
        )
        self.tools, self.head, self.source = tools, head, source
        self.serial = 0
        self.previous: Snapshot | None = None
        self.previous_full: bytes | None = None
        self.previous_meta: dict[str, Any] | None = None
        self.baseline: Snapshot = {}
        self.handled: set[Path] = set()
        self.browser: subprocess.Popen[bytes] | None = None
        self.oracle: dict[str, Any] = {}
        self.reset_adapter: dict[str, Any] = {}
        self.generated_hashes: dict[str, str] = {}

    def save(self) -> None:
        (self.destination / "manifest.json").write_bytes(encode(self.manifest))

    def retain(self, name: str, raw: bytes) -> dict[str, Any]:
        entry = write_new(self.destination / name, raw)
        self.manifest["artifacts"].append(entry)
        return entry

    def stable(self) -> None:
        from scripts.w1_demo_deployment import source_state

        require(
            source_state() == self.source and tool_sources() == self.tools,
            "Source changed during actual UI run",
        )
        require(
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
            == self.head,
            "HEAD drift",
        )
        for name, digest in self.ready.prerequisite_hashes:
            require(
                sha(cast(Path, getattr(self.ready, name)).read_bytes()) == digest,
                "Prerequisite original drift",
            )
        for name, digest in self.generated_hashes.items():
            require(
                sha((self.destination / name).read_bytes()) == digest,
                "Generated observer/config drift",
            )

    def command(
        self, label: str, argv: list[str], stdin: bytes = b"", timeout: int = 7200
    ) -> bytes:
        self.stable()
        prefix = "native/" + label
        self.retain(prefix + ".stdin", stdin)
        row: dict[str, Any] = {
            "label": label,
            "argv": argv,
            "stdin_sha256": sha(stdin),
            "started_at": now(),
        }
        try:
            result = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                cwd=ROOT,
                timeout=timeout,
                env=child_environment(
                    dict(os.environ), self.destination, self.ready.transport.database
                ),
            )
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as error:
            code, stdout, stderr = None, error.stdout or b"", error.stderr or b""
        except OSError as error:
            code, stdout, stderr = (
                None,
                b"",
                encode({"error_type": type(error).__name__, "errno": error.errno}),
            )
        row.update(
            exit_code=code,
            finished_at=now(),
            stdout=self.retain(prefix + ".stdout", stdout),
            stderr=self.retain(prefix + ".stderr", stderr),
        )
        self.manifest["commands"].append(row)
        self.save()
        require(
            type(code) is int and code == 0,
            "Native command failed; original streams retained: " + label,
        )
        self.stable()
        return stdout

    def observe(self, label: str, *, probe: bool = False) -> dict[str, Any]:
        from scripts import w1_offline_boundary as boundary

        output = "docs/progress/evidence/W1/" + self.destination.name + "-" + label
        arguments = [
            sys.executable,
            str(ROOT / "scripts/w1_offline_reobserve.py"),
            "--run",
            "--action",
            "probe" if probe else "observe",
            "--registration",
            str(self.ready.registration),
            "--start",
            str(self.ready.start),
            "--prior-isolation",
            str(self.ready.isolation),
            "--output",
            output,
        ]
        self.command("observe-" + label, arguments)
        path = ROOT / output / "manifest.json"
        raw = path.read_bytes()
        self.retain("native/" + label + ".observation-manifest.json", raw)
        report = obj(original(raw))
        require(
            report.get("source_before") == report.get("source_after") == self.source
            and report.get("source_head") == report.get("source_head_after") == self.head
            and report.get("owner_uuid") == self.ready.transport.owner_uuid
            and report.get("owner_run_id") == self.ready.transport.owner_run_id
            and type(report.get("exit_code")) is int
            and report["exit_code"] == 0,
            "Actual fresh read-only observation source/owner/exit differs",
        )
        following, networks = (
            artifact(report, "after-containers-stdout"),
            artifact(report, "after-networks-stdout"),
        )
        boundary.same_isolated(
            self.ready.isolated_containers, following, self.ready.isolated_networks, networks
        )
        if probe:
            verify_probe(report, self.ready.transport, self.source, self.head)
        else:
            require(
                report.get("status") == "ISOLATED_TOPOLOGY_REOBSERVED_EGRESS_UNVERIFIED",
                "Fresh topology observation failed",
            )
        self.manifest.setdefault("topology_observations", []).append(
            {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw), "probe": probe}
        )
        return report

    def snapshot(self, label: str) -> tuple[Snapshot, dict[str, Any], bytes]:
        self.serial += 1
        stem = f"{self.serial:04d}-{label}"
        self.observe(stem)
        command = self.adapter.build_snapshot_command512(
            self.ready.transport, self.destination.name + "-" + stem
        )
        stdout = self.command("snapshot-" + stem, list(command.argv), command.stdin)
        decoded = self.adapter.decode_snapshot_output512(self.ready.transport, command, stdout)
        data, metadata = snapshot_business(decoded)
        gzip_meta = self.retain("checkpoints/" + stem + ".json.gz", decoded.snapshot_gzip)
        metadata["path"] = gzip_meta["path"]
        metadata["compressed_bytes"] = gzip_meta["bytes"]
        metadata["audit_original"] = decoded.report
        # Preserve helper framing separately instead of duplicating its base64 payload in manifest.
        metadata["audit_original"] = {
            key: value for key, value in decoded.report.items() if key != "snapshot_gzip_base64"
        }
        return data, metadata, decoded.snapshot_data

    def broker(self, path: Path) -> None:
        raw = path.read_bytes()
        request = validate_broker(obj(original(raw)), path.name)
        self.stable()
        operation = request["operation"]
        result: dict[str, Any]
        if operation == "checkpoint":
            data, meta, full = self.snapshot(request["label"])
            require(
                self.oracle["open_epoch"](data)["id"] == request["expected_epoch_id"],
                "Exact actual UI epoch differs",
            )
            result = {"snapshot": meta}
            mode = request["mode"]
            if mode != "BEGIN":
                require(
                    self.previous is not None and self.previous_meta is not None,
                    "Actual preceding checkpoint missing",
                )
                previous = cast(Snapshot, self.previous)
                previous_meta = cast(dict[str, Any], self.previous_meta)
                require(
                    previous_meta["metadata_heads"] == meta["metadata_heads"],
                    "Migration heads changed during UI",
                )
                if mode == "READ_ONLY":
                    require(
                        self.previous_full == full,
                        "Read/replay wrote a full physical24 original row",
                    )
                elif mode == "RESET":
                    result["oracle"] = self.reset_adapter["reset_oracle"](
                        previous, data, self.baseline
                    )
                else:
                    result["oracle"] = self.oracle["money_oracle"](previous, data)
            self.previous, self.previous_meta, self.previous_full = data, meta, full
            self.manifest["checkpoints"].append(
                {
                    "scenario_id": request["scenario_id"],
                    "label": request["label"],
                    "mode": mode,
                    "result": result,
                }
            )
        else:
            from scripts.demo_container_transport import build_rpc_command

            self.observe("rpc-" + request["id"])
            wire = {
                "protocol": BROKER_PROTOCOL,
                "purpose": "DEVELOPMENT",
                "scenario_id": request["scenario_id"],
                "operation": operation,
                "expected_epoch_id": request["expected_epoch_id"],
            }
            name = "goal_id" if operation == "ingest_goal_income" else "action_id"
            wire[name] = request[name]
            prepared = build_rpc_command(self.ready.transport, wire)
            stdout = self.command("rpc-" + request["id"], list(prepared.argv), prepared.stdin)
            response = obj(original(stdout))
            require(
                response.get("protocol") == BROKER_PROTOCOL
                and response.get("operation") == operation
                and response.get("scenario_id") == request["scenario_id"]
                and response.get("status") == "PASSED",
                "Actual original RPC identity/status differs",
            )
            result = obj(response["result"])
            if operation == "verify_legacy_recovery":
                require(
                    result.get("action_id") == wire["action_id"] and result.get("verified") is True,
                    "Original legacy recovery identity/proof differs",
                )
            else:
                require(
                    result.get("goal_id") == wire["goal_id"]
                    and result["request"]["kind"] == "INCOME"
                    and result["request"]["amount_cents"] == 200000
                    and result["result"]["bank_status"] == "SETTLED"
                    and result["result"]["projection_status"] == "PROJECTED",
                    "Original fixed simulated goal income was not actually projected",
                )
            self.manifest["rpc"].append(
                {"input": wire, "stdout_sha256": sha(stdout), "result": result}
            )
        reply = path.with_name(request["id"] + ".response.json")
        pending = reply.with_name(reply.name + ".pending")
        write_new(pending, encode({"id": request["id"], "status": "PASSED", "result": result}))
        pending.rename(reply)
        self.save()

    def run(self, node: str) -> int:
        self.save()
        code = 1
        handle: Any = None
        try:
            for name, raw in self.ready.prerequisite_bytes:
                self.retain("native/prerequisite-" + name + ".json", raw)
            # Explicit --run only: the immutable pure reset adapter imports the
            # local typed model registry. Do not depend on pytest's pythonpath.
            sys.path.insert(0, str(ROOT / "apps/api"))
            self.oracle = runpy.run_path(
                str(ROOT / ".runtime/drive_mvp404_browser.py"),
                run_name="offline_original_readonly_oracles",
            )
            self.reset_adapter = runpy.run_path(
                str(ROOT / "scripts/browser_checkpoint_oracles.py"),
                run_name="offline_typed_original_reset_adapter",
            )
            self.baseline, baseline_meta, _ = self.snapshot("owned-initial-baseline")
            self.manifest["baseline"] = baseline_meta
            self.manifest["baseline_oracle"] = self.oracle["money_oracle"](
                self.baseline, self.baseline
            )
            config, wrapper = generated_files(self.destination)
            self.retain("generated/offline.config.cjs", config)
            self.retain("generated/offline-wrapper.spec.mts", wrapper)
            self.generated_hashes = {
                "generated/offline.config.cjs": sha(config),
                "generated/offline-wrapper.spec.mts": sha(wrapper),
            }
            cli = ROOT / "apps/web/node_modules/@playwright/test/cli.js"
            require(cli.is_file(), "Frozen local Playwright executable missing")
            arguments = [
                node,
                str(cli),
                "test",
                "--config",
                str(self.destination / "generated/offline.config.cjs"),
                "--workers=1",
                "--retries=0",
                "--max-failures=1",
            ]
            handle = (self.destination / "native/playwright.log").open("xb")
            self.browser = subprocess.Popen(
                arguments,
                cwd=ROOT,
                env=child_environment(
                    dict(os.environ), self.destination, self.ready.transport.database
                ),
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.manifest["playwright_process"] = {
                "argv": arguments,
                "pid": self.browser.pid,
                "started_at": now(),
            }
            self.save()
            while self.browser.poll() is None:
                for path in sorted((self.destination / "broker").glob("*.request.json")):
                    if path not in self.handled:
                        self.broker(path)
                        self.handled.add(path)
                self.stable()
                time.sleep(0.1)
            self.manifest["playwright_exit_code"] = self.browser.returncode
            require(
                self.browser.returncode == 0, "Original Edge UI failed; all raw artifacts retained"
            )
            report = obj(original((self.destination / "playwright/results.json").read_bytes()))
            self.manifest["actual_results"] = validate_results(report)
            validate_reset_scope(
                self.manifest["checkpoints"], self.manifest["actual_results"]["case_ids"]
            )
            _, self.manifest["final_snapshot"], _ = self.snapshot("final-owned-alive-audit")
            self.observe("final-egress-probe", probe=True)
            relay_raw = self.ready.relay.read_bytes()
            self.retain("native/relay-final-observed.json", relay_raw)
            relay = obj(original(relay_raw))
            verify_relay(relay, self.ready.transport, self.source, self.head)
            self.manifest["network_originals"] = validate_network_originals(
                self.destination, self.ready, relay, self.manifest["actual_results"]["case_ids"]
            )
            self.stable()
            self.manifest["status"] = "FOUR_OFFLINE_UI_CASES_VERIFIED"
            self.manifest["offline_scope"] = (
                "THREE_GOLDEN_CHAINS_WITH_FIXED_LOSS_CASE_NOT_ALL7_OR_THREE_ROUNDS"
            )
            code = 0
        except BaseException as error:
            self.manifest.update(
                status="FAILED", error={"type": type(error).__name__, "message": str(error)}
            )
        finally:
            if self.browser is not None and self.browser.poll() is None:
                # Exact own child only; never stop relay, Docker services, DB or volumes.
                if os.name == "nt":
                    completed = subprocess.run(
                        ["taskkill", "/PID", str(self.browser.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                    self.retain("native/own-child-stop.stdout", completed.stdout)
                    self.retain("native/own-child-stop.stderr", completed.stderr)
                else:
                    self.browser.terminate()
                try:
                    self.browser.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.browser.kill()
                    self.browser.wait(timeout=10)
            if handle is not None:
                handle.close()
            if "final_snapshot" not in self.manifest:
                # Failure remains FAILED. Capture the alive owned ledger before returning,
                # after the exact own browser child has stopped; never clean the deployment.
                try:
                    _, metadata, _ = self.snapshot("failure-final-owned-alive-audit")
                    self.manifest["failure_final_snapshot"] = metadata
                except BaseException as error:
                    self.manifest["failure_final_audit"] = {
                        "status": "FAILED_UNVERIFIED",
                        "error_type": type(error).__name__,
                    }
            try:
                self.stable()
                self.manifest.update(
                    source_after=self.source,
                    source_head_after=self.head,
                    tool_source_after=self.tools,
                    source_equal=True,
                )
            except BaseException as error:
                code = 1
                self.manifest.update(
                    status="FAILED", source_equal=False, final_source_error=type(error).__name__
                )
            self.manifest.update(finished_at=now(), exit_code=code, task_closed=False)
            self.manifest["artifact_hashes"] = {
                path.relative_to(self.destination).as_posix(): sha(path.read_bytes())
                for path in sorted(self.destination.rglob("*"))
                if path.is_file() and path != self.destination / "manifest.json"
            }
            self.save()
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "status": self.manifest["status"],
                    "manifest": (self.destination / "manifest.json").relative_to(ROOT).as_posix(),
                    "complete_all7": False,
                    "three_rounds": "NOT_RUN",
                }
            )
        )
        return code


def generated_files(destination: Path) -> tuple[bytes, bytes]:
    """Observe all browser resources before importing the unchanged original spec."""
    package = (ROOT / "apps/web/node_modules/@playwright/test").resolve()
    config = (
        "const { defineConfig, devices } = require("
        + json.dumps(str(package))
        + ");\n"
        + "module.exports = defineConfig("
        + json.dumps(
            {
                "testDir": str(destination / "generated"),
                "testMatch": "offline-wrapper.spec.mts",
                "fullyParallel": False,
                "forbidOnly": True,
                "retries": 0,
                "workers": 1,
                "timeout": 1800000,
                "expect": {"timeout": 600000},
                "outputDir": str(destination / "playwright/test-results"),
                "reporter": [
                    ["list"],
                    ["json", {"outputFile": str(destination / "playwright/results.json")}],
                    [
                        "html",
                        {"outputFolder": str(destination / "playwright/html"), "open": "never"},
                    ],
                ],
                "use": {
                    "baseURL": RELAY_URL,
                    "trace": "on",
                    "video": "on",
                    "screenshot": "only-on-failure",
                    "serviceWorkers": "block",
                },
                "projects": [
                    {
                        "name": "edge",
                        "use": {"channel": "msedge", "viewport": {"width": 1280, "height": 720}},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + ");\n"
        + "module.exports.grep = new RegExp("
        + json.dumps("^(?:.*)(?:" + "|".join(re.escape(title) for title in CASE_TITLES) + ")$")
        + ");\n"
    ).encode()
    wrapper = (
        "import { test as offlineTest } from "
        + json.dumps((package / "index.mjs").as_uri())
        + ";\n"
        + """import * as fs from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
const inventories = new Map();
const checksum = (b) => createHash('sha256').update(b).digest('hex');
offlineTest.beforeEach(async ({ context, page }, info) => {
  const dir=info.outputPath('offline-all-http');
  await fs.mkdir(dir,{recursive:true});
  const state={events:[], errors:[], pending:new Set(),serial:0};
  inventories.set(info.testId,{dir,state});
  const valid=(url)=>{
    const u=new URL(url);
    if(u.protocol!=='http:'||u.hostname!=='127.0.0.1'||u.port!=='15183'||u.username||u.password)
      state.errors.push('Non-relay URL '+url);
  };
  context.on('request',request=>{
    valid(request.url());
    const body=request.postDataBuffer()??Buffer.alloc(0);
    state.events.push({kind:'REQUEST',url:request.url(),method:request.method(),
      body_sha256:checksum(body),body_base64:body.toString('base64'),
      at:new Date().toISOString()});
  });
  context.on('requestfailed',request=>{
    valid(request.url());
    state.events.push({kind:'FAILED',url:request.url(),method:request.method(),
      failure:request.failure(),at:new Date().toISOString()});
    state.errors.push('Actual failed request '+request.url());
  });
  context.on('response',response=>{
    valid(response.url()); const ordinal=++state.serial;
    const saved=(async()=>{
      const body=await response.body(); const file='response-'+ordinal+'.body';
      await fs.writeFile(join(dir,file),body,{flag:'wx'});
      state.events.push({kind:'RESPONSE',url:response.url(),method:response.request().method(),
        status:response.status(),body_file:file,body_sha256:checksum(body),bytes:body.length,
        at:new Date().toISOString()});
      if(response.status()>=500) state.errors.push('Actual server error '+response.status());
    })().catch(e=>state.errors.push(String(e)));
    state.pending.add(saved); void saved.finally(()=>state.pending.delete(saved));
  });
  page.on('websocket',socket=>{
    state.events.push({kind:'WEBSOCKET',url:socket.url()});
    state.errors.push('Unregistered websocket '+socket.url());
  });
});
offlineTest.afterEach(async ({},info)=>{
  const {dir,state}=inventories.get(info.testId);
  while(state.pending.size) await Promise.all([...state.pending]);
  await fs.writeFile(join(dir,'inventory.json'),JSON.stringify({
    protocol:'bounded-funds-offline-all-http-v1',title:info.title,test_id:info.testId,
    requested_channel:'msedge',events:state.events,errors:state.errors
  }),{flag:'wx'});
  if(state.errors.length) throw new Error(state.errors.join('\\n'));
});
"""
        + "await import("
        + json.dumps((ROOT / SPEC).as_uri())
        + ");\n"
    ).encode()
    return config, wrapper


def validate_network_originals(
    destination: Path, readiness: Readiness, relay: dict[str, Any], cases: dict[str, str]
) -> dict[str, Any]:
    from scripts.demo_loopback_relay import relay_program, request_frame

    counts: Counter[tuple[str, str, str]] = Counter()
    inventories = list(
        (destination / "playwright/test-results").rglob("offline-all-http/inventory.json")
    )
    require(len(inventories) == 4, "Actual complete per-case browser URL inventories missing")
    titles = []
    for path in inventories:
        inventory = obj(original(path.read_bytes()))
        require(
            inventory.get("protocol") == "bounded-funds-offline-all-http-v1"
            and not inventory.get("errors"),
            "Original browser URL capture failed",
        )
        titles.append(inventory["title"])
        require(
            inventory.get("test_id") == cases.get(inventory["title"])
            and inventory.get("requested_channel") == "msedge",
            "Original resource inventory actual test identity differs",
        )
        for event in inventory["events"]:
            path_url = local_url(event["url"])
            require(
                event["kind"] in {"REQUEST", "RESPONSE"}, "Failed/unregistered browser transport"
            )
            if event["kind"] == "REQUEST":
                body = base64.b64decode(event["body_base64"], validate=True)
                require(sha(body) == event["body_sha256"], "Original request body bytes differ")
                counts[(event["method"], path_url, event["body_sha256"])] += 1
            else:
                body_file = (path.parent / event["body_file"]).resolve()
                require(
                    body_file.parent == path.parent.resolve() and body_file.is_file(),
                    "Original response body path escaped",
                )
                body = body_file.read_bytes()
                require(
                    sha(body) == event["body_sha256"] and len(body) == event["bytes"],
                    "Original all-resource response bytes differ",
                )
    require(
        len(set(titles)) == 4 and set(titles) == set(CASE_TITLES),
        "All-resource capture case denominator differs",
    )
    for path in (destination / "playwright/test-results").rglob("native-http/*.assertion-get.json"):
        item = obj(original(path.read_bytes()))
        require(
            item["method"] == "GET"
            and type(item.get("status")) is int
            and 200 <= item["status"] < 300
            and path.with_suffix(".body").is_file(),
            "Original assertion helper issued a write or lacks an actual successful response",
        )
        counts[("GET", local_url(RELAY_URL + "/api/v1" + item["path"]), sha(b""))] += 1
    requests = relay["requests"][readiness.relay_initial_count :]
    require(bool(requests), "No actual relay request originals for this browser run")
    actual: Counter[tuple[str, str, str]] = Counter()
    relay_entries = []
    for ordinal, row in enumerate(requests, readiness.relay_initial_count + 1):
        prefix = f"request-{ordinal:05d}"
        client = (readiness.relay.parent / (prefix + "-client.http")).read_bytes()
        forwarded = (readiness.relay.parent / (prefix + "-forwarded.http")).read_bytes()
        response = (readiness.relay.parent / (prefix + "-response.http")).read_bytes()
        stderr = (readiness.relay.parent / (prefix + "-stderr.log")).read_bytes()
        headers, body = client.split(b"\r\n\r\n", 1)
        first = headers.split(b"\r\n", 1)[0].decode("ascii")
        match = re.fullmatch(r"([A-Z]+) (/\S*) HTTP/1\.[01]", first)
        require(match is not None, "Original relay request framing differs")
        assert match is not None
        require(
            row.get("status") == "FORWARDED_ONCE"
            and type(row.get("exit_code")) is int
            and row["exit_code"] == 0
            and row.get("client_sha256") == sha(client)
            and row.get("forwarded_sha256") == sha(forwarded)
            and row.get("body_sha256") == sha(body)
            and row.get("response_sha256") == sha(response)
            and forwarded == request_frame(headers + b"\r\n\r\n", body, 15183)
            and row.get("command")
            == [
                "docker",
                "exec",
                "-i",
                readiness.transport.api_container_id,
                "/opt/bf-venv/bin/python",
                "-c",
                relay_program(relay["web_internal_ip"]).decode(),
            ]
            and re.match(rb"HTTP/1\.[01] [1-5][0-9]{2} ", response) is not None,
            "Actual original relay bytes/one-attempt/body/command differ",
        )
        actual[(match[1], match[2], sha(body))] += 1
        relay_entries.append(
            {
                "ordinal": ordinal,
                "client_sha256": sha(client),
                "forwarded_sha256": sha(forwarded),
                "response_sha256": sha(response),
                "stderr_sha256": sha(stderr),
                "original_directory": readiness.relay.parent.relative_to(ROOT).as_posix(),
            }
        )
    require(
        actual == counts,
        "Full relay denominator differs from browser resources and original GET assertions",
    )
    return {
        "all_urls_exact_loopback": True,
        "browser_resource_cases": 4,
        "request_count": sum(actual.values()),
        "relay_originals": relay_entries,
        "body_transformed": False,
        "no_browser_or_host_firewall_claim": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
