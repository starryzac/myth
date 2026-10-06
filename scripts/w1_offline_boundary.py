"""Observe/isolate/probe one registered demo network; --run is required for Docker."""

from __future__ import annotations

import argparse
import base64
import json
import re
import runpy
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.demo_container_transport import (  # noqa: E402
    list_value,
    object_value,
    original_json,
    require,
    sha,
    validate_owned_transport,
)
from scripts.demo_offline_network import (  # noqa: E402
    build_transition,
    containers,
    validate_transition,
)

PROTOCOL = "bounded-funds-offline-boundary-v1"
PINNED = {
    "scripts/demo_container_transport.py": (
        "7ba9223b5d50d022b794633748ba2bfc678353e9ac05645271df09929ea8dcd5"
    ),
    "scripts/demo_offline_network.py": (
        "b0eb689526962cea76765b885decdfbb0a1e96cbb58598b75e0b660dcfa814f2"
    ),
}
IP_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53))
DNS_NAME = "example.com"
DNS_CODE = (
    'import json,socket\ntry:\n rows=socket.getaddrinfo("example.com",None);'
    'print(json.dumps({"name":"example.com","result":"RESOLVED","rows":rows}))'
    '\nexcept socket.gaierror as e:\n print(json.dumps({"name":"example.com",'
    '"result":"GAIERROR","errno":e.errno,"message":str(e)}))'
)
API_PROBE = rb"""
import base64, errno, json, pathlib, socket, subprocess, sys, time
result={"protocol":"bounded-funds-api-egress-probe-v1","targets":[],"route_files":{}}
for name in ("/proc/net/route","/proc/net/ipv6_route","/etc/resolv.conf"):
    try: result["route_files"][name]=pathlib.Path(name).read_text()
    except OSError as e: result["route_files"][name]={"error_type":type(e).__name__,"errno":e.errno}
for host,port in (("1.1.1.1",443),("8.8.8.8",53)):
    start=time.monotonic(); row={"host":host,"port":port,"timeout_seconds":3}
    try:
        with socket.create_connection((host,port),timeout=3): row["result"]="CONNECTED"
    except OSError as e:
        row.update(result="ERROR",error_type=type(e).__name__,errno=e.errno,message=str(e))
    row["wall_seconds"]=time.monotonic()-start; result["targets"].append(row)
dns_code=(
 'import json,socket\ntry:\n rows=socket.getaddrinfo("example.com",None);'
 'print(json.dumps({"name":"example.com","result":"RESOLVED","rows":rows}))'
 '\nexcept socket.gaierror as e:\n print(json.dumps({"name":"example.com",'
 '"result":"GAIERROR","errno":e.errno,"message":str(e)}))'
)
argv=[sys.executable,"-c",dns_code]; start=time.monotonic()
try:
    p=subprocess.run(argv,capture_output=True,timeout=4)
    result["dns"]={"argv":argv,"exit_code":p.returncode,
       "stdout_base64":base64.b64encode(p.stdout).decode(),
       "stderr_base64":base64.b64encode(p.stderr).decode(),
       "timeout_seconds":4,"wall_seconds":time.monotonic()-start}
except subprocess.TimeoutExpired as e:
    result["dns"]={"argv":argv,"result":"TIMEOUT_UNVERIFIED",
       "stdout_base64":base64.b64encode(e.stdout or b'').decode(),
       "stderr_base64":base64.b64encode(e.stderr or b'').decode(),
       "timeout_seconds":4,"wall_seconds":time.monotonic()-start}
print(json.dumps(result,sort_keys=True))
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"


def admit_image_observation(start_original: bytes, fresh_original: bytes) -> dict[str, Any]:
    """Exact typed JSON value equality; distinct raw captures remain separate evidence."""

    def canonical(raw: bytes) -> bytes:
        return json.dumps(
            original_json(raw),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()

    previous, current = canonical(start_original), canonical(fresh_original)
    require(previous == current, "Actual immutable image JSON fields differ from original start")
    return {
        "method": "STRICT_TYPED_JSON_VALUE_EQUALITY_WITH_DISTINCT_RAW_CAPTURES_V1",
        "start_original_sha256": sha(start_original),
        "start_original_bytes": len(start_original),
        "fresh_original_sha256": sha(fresh_original),
        "fresh_original_bytes": len(fresh_original),
        "raw_bytes_equal": start_original == fresh_original,
        "complete_typed_json_equal": True,
        "complete_typed_json_sha256": sha(current),
    }


def public_bytes(raw: bytes) -> bytes:
    """Explicit transformed public representation; original bytes remain private."""

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if "password" in key.lower() else clean(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            if value.startswith(("POSTGRES_PASSWORD=", "PGPASSWORD=")):
                return value.partition("=")[0] + "=[REDACTED]"
            return re.sub(
                r"(postgresql(?:\+psycopg)?://[^\s/@:]+:)[^\s/@]+(@)", r"\1[REDACTED]\2", value
            )
        return value

    try:
        return encode(clean(original_json(raw)))
    except (ValueError, UnicodeError):
        return str(clean(raw.decode("utf-8", errors="replace"))).encode()


def routes_without_default(ipv4: str, ipv6: str) -> bool:
    four = ipv4.splitlines()
    require(bool(four) and "Destination" in four[0], "Actual IPv4 route header missing")
    for line in four[1:]:
        fields = line.split()
        require(len(fields) >= 8, "Actual IPv4 route row incomplete")
        if fields[1] == "00000000" and fields[7] == "00000000":
            return False
    for line in ipv6.splitlines():
        fields = line.split()
        require(len(fields) >= 10, "Actual IPv6 route row incomplete")
        # Linux publishes an unreachable default placeholder: route flag 0x200
        # marks REJECT and must be recorded, not treated as usable default.
        if fields[0] == "0" * 32 and fields[1] == "00" and not (int(fields[8], 16) & 0x200):
            return False
    return True


def classify_api_probe(raw: bytes, exit_code: int | None) -> dict[str, Any]:
    if type(exit_code) is not int or exit_code != 0:
        return {"observed": False, "reason": "API_PRODUCER_EXIT_OR_TIMEOUT_UNVERIFIED"}
    probe = object_value(original_json(raw))
    require(
        probe.get("protocol") == "bounded-funds-api-egress-probe-v1",
        "Original API probe protocol differs",
    )
    routes = object_value(probe.get("route_files"))
    if not all(
        isinstance(routes.get(key), str)
        for key in ("/proc/net/route", "/proc/net/ipv6_route", "/etc/resolv.conf")
    ):
        return {"observed": False, "reason": "ROUTE_OR_RESOLVER_UNVERIFIED"}
    no_default = routes_without_default(routes["/proc/net/route"], routes["/proc/net/ipv6_route"])
    targets = list_value(probe.get("targets"))
    require(len(targets) == 2, "Two actual API IP endpoints required")
    failed = all(
        row.get("host") == host
        and row.get("port") == port
        and row.get("timeout_seconds") == 3
        and row.get("result") == "ERROR"
        and type(row.get("errno")) is int
        and row["errno"] in {101, 113}
        and isinstance(row.get("message"), str)
        and bool(row["message"])
        for row, (host, port) in zip(targets, IP_TARGETS, strict=True)
    )
    dns = object_value(probe.get("dns"))
    unresolved = False
    argv = dns.get("argv")
    if (
        type(dns.get("exit_code")) is int
        and dns["exit_code"] == 0
        and dns.get("timeout_seconds") == 4
        and isinstance(argv, list)
        and len(argv) == 3
        and argv[0] == "/opt/bf-venv/bin/python"
        and argv[1:] == ["-c", DNS_CODE]
    ):
        native = object_value(original_json(base64.b64decode(dns["stdout_base64"], validate=True)))
        unresolved = (
            native.get("name") == DNS_NAME
            and native.get("result") == "GAIERROR"
            and type(native.get("errno")) is int
            and native["errno"] in {-2, -3}
            and isinstance(native.get("message"), str)
            and bool(native["message"])
            and not re.search(r"timed? out|timeout", native["message"], re.I)
        )
    return {
        "observed": no_default and failed and unresolved,
        "no_default_route": no_default,
        "ip_failures_observed": failed,
        "dns_unresolved_observed": unresolved,
        "reason": "OBSERVED_TESTED_BOUNDARY"
        if no_default and failed and unresolved
        else "API_BOUNDARY_UNVERIFIED",
    }


def busybox_commands(container_id: str) -> list[tuple[str, tuple[str, ...]]]:
    require(
        re.fullmatch(r"[0-9a-f]{64}", container_id) is not None, "Immutable container ID required"
    )
    prefix = ("docker", "exec", container_id, "/bin/busybox")
    result: list[tuple[str, tuple[str, ...]]] = [("inventory", prefix + ("--list",))]
    result += [
        (name + "-help", prefix + (name, "--help"))
        for name in ("ip", "wget", "nslookup", "timeout", "cat")
    ]
    result += [
        ("route4", prefix + ("ip", "route", "show")),
        ("route6", prefix + ("ip", "-6", "route", "show")),
        ("resolver", prefix + ("cat", "/etc/resolv.conf")),
    ]
    result += [
        (
            f"ip-{index}",
            prefix
            + (
                "timeout",
                "4",
                "/bin/busybox",
                "wget",
                "-T",
                "3",
                "-O",
                "-",
                f"http://{host}:{port}/",
            ),
        )
        for index, (host, port) in enumerate(IP_TARGETS)
    ]
    result += [("dns", prefix + ("timeout", "4", "/bin/busybox", "nslookup", DNS_NAME))]
    return result


def classify_busybox(results: dict[str, tuple[int | None, bytes, bytes]]) -> dict[str, Any]:
    inventory = results.get("inventory")
    if (
        inventory is None
        or type(inventory[0]) is not int
        or inventory[0] != 0
        or not {"ip", "wget", "nslookup", "timeout", "cat"}.issubset(
            set(inventory[1].decode(errors="replace").splitlines())
        )
    ):
        return {"observed": False, "reason": "ACTUAL_BUSYBOX_INVENTORY_MISSING"}
    needed = [
        "ip-help",
        "wget-help",
        "nslookup-help",
        "timeout-help",
        "cat-help",
        "route4",
        "route6",
        "resolver",
        "ip-0",
        "ip-1",
        "dns",
    ]
    if not all(label in results for label in needed):
        return {"observed": False, "reason": "ACTUAL_PROBES_NOT_RUN"}
    if any(
        type(results[label][0]) is not int
        or results[label][0] != 0
        or not (results[label][1] + results[label][2]).strip()
        for label in needed[:5]
    ):
        return {"observed": False, "reason": "ACTUAL_BUSYBOX_HELP_UNVERIFIED"}
    if any(
        type(results[label][0]) is not int or results[label][0] != 0
        for label in ("route4", "route6", "resolver")
    ):
        return {"observed": False, "reason": "ACTUAL_ROUTES_RESOLVER_UNVERIFIED"}
    if not results["route4"][1].strip() or not results["resolver"][1].strip():
        return {"observed": False, "reason": "ACTUAL_ROUTES_RESOLVER_EMPTY"}
    no_default = not any(
        re.match(r"^\s*default(?:\s|$)", line)
        for label in ("route4", "route6")
        for line in results[label][1].decode(errors="replace").splitlines()
    )
    failed = True
    for label in ("ip-0", "ip-1"):
        code, stdout, stderr = results[label]
        text = (stdout + stderr).decode(errors="replace")
        failed &= (
            type(code) is int
            and code not in {0, 124, 137}
            and bool(re.search(r"network(?: is)? unreachable|no route to host", text, re.I))
            and not bool(re.search(r"timed? out|timeout|not found", text, re.I))
        )
    code, stdout, stderr = results["dns"]
    text = (stdout + stderr).decode(errors="replace")
    unresolved = (
        type(code) is int
        and code not in {0, 124, 137}
        and bool(re.search(r"SERVFAIL|REFUSED|NXDOMAIN|can't resolve", text, re.I))
        and not bool(re.search(r"timed? out|timeout|not found", text, re.I))
    )
    return {
        "observed": no_default and failed and unresolved,
        "no_default_route": no_default,
        "ip_failures_observed": failed,
        "dns_unresolved_observed": unresolved,
        "reason": "OBSERVED_TESTED_BOUNDARY"
        if no_default and failed and unresolved
        else "BUSYBOX_BOUNDARY_UNVERIFIED",
    }


class Capture:
    def __init__(self, public: Path, private: Path, manifest: dict[str, Any]) -> None:
        self.public, self.private, self.manifest = public, private, manifest

    def save(self, label: str, raw: bytes) -> Path:
        require(
            re.fullmatch(r"[A-Za-z0-9_-]+", label) is not None, "Safe fresh capture label required"
        )
        original = self.private / (label + ".original")
        transformed = self.public / (label + ".redacted")
        with original.open("xb") as handle:
            handle.write(raw)
        redacted = public_bytes(raw)
        with transformed.open("xb") as handle:
            handle.write(redacted)
        self.manifest["artifacts"].append(
            {
                "label": label,
                "private_original_path": original.relative_to(ROOT).as_posix(),
                "private_original_sha256": sha(raw),
                "private_original_bytes": len(raw),
                "public_path": transformed.relative_to(ROOT).as_posix(),
                "public_sha256": sha(redacted),
                "public_bytes": len(redacted),
                "public_transformation": "REDACTED_REENCODING_V1_NOT_ORIGINAL",
            }
        )
        return original

    def command(
        self, label: str, argv: tuple[str, ...], *, stdin: bytes = b"", timeout: int = 30
    ) -> tuple[int | None, bytes, bytes]:
        started = now()
        if stdin:
            self.save(label + "-stdin", stdin)
        outcome: str = "EXITED"
        try:
            result = subprocess.run(
                argv, cwd=ROOT, input=stdin, capture_output=True, timeout=timeout
            )
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as error:
            code, stdout, stderr, outcome = (
                None,
                error.stdout or b"",
                error.stderr or b"",
                "TIMEOUT_UNVERIFIED",
            )
        except OSError as error:
            code, stdout, stderr, outcome = (
                None,
                b"",
                encode({"error_type": type(error).__name__, "errno": error.errno}),
                "SPAWN_FAILED",
            )
        self.save(label + "-stdout", stdout)
        self.save(label + "-stderr", stderr)
        self.manifest["commands"].append(
            {
                "label": label,
                "argv": list(argv),
                "started_at": started,
                "finished_at": now(),
                "exit_code": code,
                "outcome": outcome,
                "stdout_sha256": sha(stdout),
                "stderr_sha256": sha(stderr),
                "stdin_sha256": sha(stdin),
            }
        )
        return code, stdout, stderr


def same_isolated(archival: bytes, fresh: bytes, old_network: bytes, fresh_network: bytes) -> None:
    old, new = containers(archival), containers(fresh)
    for name in old:
        require(
            all(
                old[name].get(field) == new[name].get(field)
                for field in (
                    "Id",
                    "Image",
                    "Name",
                    "Config",
                    "HostConfig",
                    "Mounts",
                    "NetworkSettings",
                )
            ),
            "Fresh isolated container identity/topology drift",
        )
        state = object_value(new[name].get("State"))
        require(
            not any(state.get(field) for field in ("Paused", "Restarting", "Dead", "OOMKilled")),
            "Fresh isolated state unstable",
        )
        if name != "init":
            require(
                state.get("Running") is True
                and object_value(state.get("Health")).get("Status") == "healthy",
                "Fresh isolated service unhealthy",
            )
        else:
            require(
                state.get("Running") is False
                and state.get("Status") == "exited"
                and type(state.get("ExitCode")) is int
                and state["ExitCode"] == 0,
                "Fresh initializer changed",
            )
    require(
        original_json(old_network) == original_json(fresh_network),
        "Fresh owned networks differ from isolated originals",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--action", choices=("observe", "isolate", "probe"), required=True)
    parser.add_argument("--registration", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prior-isolation")
    args = parser.parse_args(argv)
    if not args.run:
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "status": "PREPARED_NOT_EXECUTED",
                    "action": args.action,
                    "offline_accepted": False,
                    "connection_made": False,
                }
            )
        )
        return 0
    destination = (ROOT / args.output).resolve()
    require(
        destination.is_relative_to(ROOT / "docs/progress/evidence/W1") and not destination.exists(),
        "Fresh public W1 destination required",
    )
    destination.mkdir(parents=True)
    private = ROOT / ".runtime/w1-offline-boundary-private" / (destination.name + "-" + uuid4().hex)
    private.mkdir(parents=True)
    producer = Path(__file__).read_bytes()
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL,
        "run_id": destination.name,
        "action": args.action,
        "started_at": now(),
        "status": "INCOMPLETE",
        "producer_path": "scripts/w1_offline_boundary.py",
        "producer_sha256": sha(producer),
        "private_directory": private.relative_to(ROOT).as_posix(),
        "commands": [],
        "artifacts": [],
        "offline_accepted": False,
        "financial_operations_executed": False,
        "private_originals_security": "SEPARATE_RUNTIME_DIRECTORY_NOT_ACL_ATTESTED",
    }
    capture = Capture(destination, private, manifest)
    code = 1
    source_state = None
    try:
        capture.save("producer-before", producer)
        manifest["frozen_sources_before"] = {
            path: sha((ROOT / path).read_bytes()) for path in PINNED
        }
        require(
            all(sha((ROOT / path).read_bytes()) == checksum for path, checksum in PINNED.items()),
            "Frozen transport/network source changed",
        )
        source_helper = ROOT / "scripts/w1_demo_deployment.py"
        helper_raw = source_helper.read_bytes()
        capture.save("source-helper-before", helper_raw)
        manifest["source_helper_before_sha256"] = sha(helper_raw)
        source_state = runpy.run_path(str(source_helper))["source_state"]
        manifest["source_before"] = source_state()
        result, head_raw, _ = capture.command("head-before", ("git", "rev-parse", "HEAD"))
        require(result == 0, "Actual current HEAD observation failed")
        head = head_raw.decode().strip()
        manifest["source_head"] = head
        registration, start_path = (
            (ROOT / args.registration).resolve(),
            (ROOT / args.start).resolve(),
        )
        require(
            registration.is_relative_to(ROOT / ".runtime/w1-deployment")
            and registration.name == "manifest.json"
            and start_path.is_relative_to(ROOT / "docs/progress/evidence/W1")
            and start_path.name == "manifest.json",
            "Original registered deployment paths escaped",
        )
        registration_raw, start_raw = registration.read_bytes(), start_path.read_bytes()
        capture.save("registration", registration_raw)
        capture.save("start-manifest", start_raw)
        images = {
            name: (start_path.parent / (name + "-image.log")).read_bytes()
            for name in ("api", "web")
        }
        original_before = (start_path.parent / "after-inspect.log").read_bytes()
        owned = validate_owned_transport(
            registration_raw, start_raw, original_before, images, manifest["source_before"], head
        )
        manifest.update(
            owner_uuid=owned.owner_uuid,
            owner_run_id=owned.owner_run_id,
            source_digest=owned.source_digest,
            api_container_id=owned.api_container_id,
            db_container_id=owned.db_container_id,
        )
        ids = tuple(row["Id"] for row in original_json(original_before))

        def inspect(label: str) -> tuple[bytes, bytes, dict[str, bytes]]:
            result, four, _ = capture.command(label + "-containers", ("docker", "inspect", *ids))
            require(result == 0, "Actual container inspection failed")
            result, network, _ = capture.command(
                label + "-networks",
                (
                    "docker",
                    "network",
                    "inspect",
                    owned.project + "_demo_internal",
                    owned.project + "_demo_frontend",
                ),
            )
            require(result == 0, "Actual network inspection failed")
            fresh_images = {}
            for name, original in images.items():
                identity = original_json(original)[0]["Id"]
                result, image, _ = capture.command(
                    label + "-" + name + "-image", ("docker", "image", "inspect", identity)
                )
                require(result == 0, "Actual immutable image observation failed")
                proof = admit_image_observation(original, image)
                manifest.setdefault("image_representation_bindings", {})[label + "-" + name] = proof
                fresh_images[name] = image
            return four, network, fresh_images

        before, network_before, fresh_images = inspect("before")
        if args.action in {"observe", "isolate"}:
            owned = validate_owned_transport(
                registration_raw,
                start_raw,
                before,
                # Native start binds its own capture bytes; actual fresh image bytes
                # above have separately passed strict complete typed JSON equality.
                images,
                source_state(),
                head,
                require_started_inspect=False,
            )
            transition = build_transition(owned, before, network_before, source_state(), head)
            manifest["planned_argv"] = list(transition.argv)
            if args.action == "observe":
                manifest["status"], code = "OBSERVED_EGRESS_ATTACHED_NOT_OFFLINE", 0
            else:
                result, stdout, stderr = capture.command("disconnect", transition.argv)
                after, network_after, _ = inspect("after")
                command = {
                    **next(item for item in manifest["commands"] if item["label"] == "disconnect"),
                    "protocol": "bounded-funds-network-transition-command-v1",
                    "owner_uuid": owned.owner_uuid,
                    "owner_run_id": owned.owner_run_id,
                    "source_digest": owned.source_digest,
                    "source_head": head,
                    "containers_before_sha256": sha(before),
                    "containers_after_sha256": sha(after),
                    "networks_before_sha256": sha(network_before),
                    "networks_after_sha256": sha(network_after),
                }
                command_raw = encode(command)
                capture.save("disconnect-command", command_raw)
                manifest["transition"] = validate_transition(
                    owned,
                    transition,
                    before,
                    after,
                    network_before,
                    network_after,
                    command_raw,
                    stdout,
                    stderr,
                    source_state(),
                    head,
                )
                require(result == 0, "Actual disconnect failed or timed out")
                manifest["status"], code = "ISOLATED_TOPOLOGY_EGRESS_UNVERIFIED", 0
        else:
            require(bool(args.prior_isolation), "Actual prior isolate manifest required")
            prior_path = (ROOT / args.prior_isolation).resolve()
            require(
                prior_path.is_relative_to(ROOT / "docs/progress/evidence/W1")
                and prior_path.name == "manifest.json",
                "Prior isolation path escaped",
            )
            prior = object_value(original_json(prior_path.read_bytes()))
            capture.save("prior-isolation-manifest", prior_path.read_bytes())
            require(
                prior.get("protocol") == PROTOCOL
                and prior.get("action") == "isolate"
                and prior.get("status") == "ISOLATED_TOPOLOGY_EGRESS_UNVERIFIED"
                and prior.get("producer_sha256") == sha(producer)
                and prior.get("source_before") == prior.get("source_after") == source_state(),
                "Prior actual isolation/source differs",
            )

            def prior_bytes(label: str) -> bytes:
                matches = [item for item in prior["artifacts"] if item["label"] == label]
                require(len(matches) == 1, "Prior private original missing/duplicated")
                row = matches[0]
                require(isinstance(row.get("private_original_path"), str), "Prior path missing")
                path = (ROOT / str(row["private_original_path"])).resolve()
                require(
                    path.is_relative_to(ROOT / ".runtime/w1-offline-boundary-private"),
                    "Prior private original escaped",
                )
                raw = path.read_bytes()
                require(
                    sha(raw) == row["private_original_sha256"]
                    and len(raw) == row["private_original_bytes"],
                    "Prior original bytes changed",
                )
                return raw

            old_before, old_after = (
                prior_bytes("before-containers-stdout"),
                prior_bytes("after-containers-stdout"),
            )
            old_network_before, old_network_after = (
                prior_bytes("before-networks-stdout"),
                prior_bytes("after-networks-stdout"),
            )
            owned = validate_owned_transport(
                registration_raw,
                start_raw,
                old_before,
                images,
                source_state(),
                head,
                require_started_inspect=False,
            )
            transition = build_transition(
                owned, old_before, old_network_before, source_state(), head
            )
            validate_transition(
                owned,
                transition,
                old_before,
                old_after,
                old_network_before,
                old_network_after,
                prior_bytes("disconnect-command"),
                prior_bytes("disconnect-stdout"),
                prior_bytes("disconnect-stderr"),
                source_state(),
                head,
            )
            same_isolated(old_after, before, old_network_after, network_before)
            actual = containers(before)
            result, stdout, _ = capture.command(
                "api-probe",
                ("docker", "exec", "-i", owned.api_container_id, "/opt/bf-venv/bin/python", "-"),
                stdin=API_PROBE,
                timeout=16,
            )
            observations = {"api": classify_api_probe(stdout, result)}
            for name in ("db", "web"):
                outcomes = {}
                for label, probe_argv in busybox_commands(actual[name]["Id"]):
                    outcomes[label] = capture.command(name + "-" + label, probe_argv, timeout=7)
                    if label == "inventory" and (
                        outcomes[label][0] != 0
                        or not {"ip", "wget", "nslookup", "timeout", "cat"}.issubset(
                            set(outcomes[label][1].decode(errors="replace").splitlines())
                        )
                    ):
                        break
                observations[name] = classify_busybox(outcomes)
            after, network_after, _ = inspect("after-probes")
            same_isolated(before, after, network_before, network_after)
            manifest["probes"] = observations
            complete = all(item.get("observed") is True for item in observations.values())
            manifest["status"], code = (
                ("EGRESS_BOUNDARY_OBSERVED", 0) if complete else ("DEFERRED_EGRESS_UNVERIFIED", 3)
            )
            manifest["three_golden_chains"] = "NOT_RUN"
        manifest["source_after"] = source_state()
        require(
            manifest["source_after"] == manifest["source_before"]
            and Path(__file__).read_bytes() == producer,
            "Producer/source drift invalidates boundary observation",
        )
    except Exception as error:
        manifest.update(
            status="FAILED",
            error_type=type(error).__name__,
            error=(
                "Original action failed; private originals and transformed diagnostics retained"
            ),
        )
        code = 1
    finally:
        producer_after = Path(__file__).read_bytes()
        capture.save("producer-after", producer_after)
        manifest["producer_after_sha256"] = sha(producer_after)
        manifest["frozen_sources_after"] = {
            path: sha((ROOT / path).read_bytes()) for path in PINNED
        }
        if source_state is not None:
            try:
                manifest["source_after"] = source_state()
                helper_after = (ROOT / "scripts/w1_demo_deployment.py").read_bytes()
                capture.save("source-helper-after", helper_after)
                manifest["source_helper_after_sha256"] = sha(helper_after)
                head_code, head_after, _ = capture.command(
                    "head-after", ("git", "rev-parse", "HEAD")
                )
                manifest["source_head_after"] = head_after.decode().strip()
                require(
                    head_code == 0
                    and manifest.get("source_before") == manifest["source_after"]
                    and manifest.get("source_head") == manifest["source_head_after"]
                    and manifest.get("source_helper_before_sha256") == sha(helper_after)
                    and manifest.get("frozen_sources_before")
                    == manifest["frozen_sources_after"]
                    == PINNED
                    and producer_after == producer,
                    "Actual source/HEAD/producer drift",
                )
            except Exception as error:
                manifest.update(
                    status="FAILED",
                    error_type=type(error).__name__,
                    error="Final source/HEAD observation failed; originals retained",
                )
                code = 1
        manifest["finished_at"], manifest["exit_code"] = now(), code
        manifest["uncovered"] = [
            "No financial chain/audit/browser/recording acceptance performed",
            "No host/global network changes",
            "Probe boundary only for recorded endpoints; not universal network proof",
        ]
        with (destination / "manifest.json").open("xb") as handle:
            handle.write(encode(manifest))
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "exit_code": code,
                "manifest": (destination / "manifest.json").relative_to(ROOT).as_posix(),
                "offline_accepted": False,
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
