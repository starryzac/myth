"""Single-attempt loopback ingress to one owned Web container through its API namespace.

This transport forwards original HTTP bodies to nginx. It has no financial routes,
retry, outcome injection, external URL, permission cache, or offline-success claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socketserver
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.demo_container_transport import original_json, validate_owned_transport
from scripts.w1_demo_deployment import ROOT, source_state

MAX_BODY = 16 * 1024 * 1024
MAX_HEADERS = 65536
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def capture_inspect(
    raw: bytes, password: str, private_path: Path, public_path: Path
) -> dict[str, str]:
    """Retain original Docker metadata privately; disclose the public redaction."""
    require(re.fullmatch(r"[0-9a-f]{48}", password) is not None, "Registered private password")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    with private_path.open("xb") as stream:
        stream.write(raw)
    public = raw.replace(password.encode(), b"[REDACTED]")
    with public_path.open("xb") as stream:
        stream.write(public)
    return {
        "private_original_sha256": sha(raw),
        "public_sha256": sha(public),
        "public_provenance": "REGISTERED_PASSWORD_LITERAL_REDACTION_V1",
        "private_original_path": str(private_path),
    }


def request_frame(headers: bytes, body: bytes, port: int) -> bytes:
    """Preserve method/path/body; replace only the transport Connection header."""
    require(len(headers) <= MAX_HEADERS and headers.endswith(b"\r\n\r\n"), "Header framing")
    lines = headers[:-4].split(b"\r\n")
    first = lines[0].decode("ascii")
    match = re.fullmatch(r"([A-Z]+) (/[^\s]*) (HTTP/1\.[01])", first)
    require(match is not None and match[1] in METHODS, "Unsupported original HTTP request")
    values: dict[str, list[str]] = {}
    retained = [lines[0]]
    for line in lines[1:]:
        require(b":" in line and not line.startswith((b" ", b"\t")), "Header syntax")
        name, value = line.split(b":", 1)
        key = name.decode("ascii").lower()
        require(re.fullmatch(r"[a-z0-9!#$%&'*+.^_`|~-]+", key) is not None, "Header name")
        values.setdefault(key, []).append(value.decode("latin1").strip())
        if key != "connection":
            retained.append(line)
    require(values.get("host") in ([f"127.0.0.1:{port}"], [f"localhost:{port}"]), "Loopback Host")
    require(
        "transfer-encoding" not in values and "upgrade" not in values, "Framing/upgrade refused"
    )
    lengths = values.get("content-length", ["0"])
    require(len(lengths) == 1 and re.fullmatch(r"[0-9]+", lengths[0]) is not None, "Content length")
    require(int(lengths[0]) == len(body) <= MAX_BODY, "Original body framing/budget")
    origins = values.get("origin", [])
    require(
        not origins or origins in ([f"http://127.0.0.1:{port}"], [f"http://localhost:{port}"]),
        "Loopback Origin",
    )
    return b"\r\n".join([*retained, b"Connection: close", b"", b""]) + body


def relay_program(web_ip: str) -> bytes:
    require(re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", web_ip) is not None, "Owned Web IPv4")
    require(all(0 <= int(piece) <= 255 for piece in web_ip.split(".")), "IPv4 components")
    return (
        "import socket,sys\n"
        "request=sys.stdin.buffer.read(16842753)\n"
        "if len(request)>16842752: raise ValueError('request budget')\n"
        f"target=({web_ip!r},80)\n"
        "with socket.create_connection(target,timeout=10) as upstream:\n"
        " upstream.settimeout(600)\n"
        " upstream.sendall(request)\n"
        " total=0\n"
        " while True:\n"
        "  part=upstream.recv(65536)\n"
        "  if not part: break\n"
        "  total+=len(part)\n"
        "  if total>33554432: raise ValueError('response budget')\n"
        "  sys.stdout.buffer.write(part)\n"
        " sys.stdout.buffer.flush()\n"
    ).encode()


def current_web_endpoint(
    api: dict[str, Any],
    web: dict[str, Any],
    registered_web: dict[str, Any],
    *,
    api_id: str,
    db_id: str,
    api_image_id: str,
    project: str,
) -> str:
    """Accept only the same registered services and original Web configuration."""
    require(api["Id"] == api_id and web["Id"] == registered_web["Id"], "Immutable ID")
    require(api["State"]["Running"] and web["State"]["Running"], "Owned services running")
    require(
        api["State"]["Health"]["Status"] == web["State"]["Health"]["Status"] == "healthy",
        "Current owned services unhealthy",
    )
    require(
        api["Image"] == api_image_id and web["Image"] == registered_web["Image"],
        "Immutable image",
    )
    require(
        api["HostConfig"]["NetworkMode"] == "container:" + db_id
        and api["HostConfig"]["ReadonlyRootfs"] is True
        and api["Mounts"] == []
        and web["Config"] == registered_web["Config"]
        and web["HostConfig"] == registered_web["HostConfig"]
        and web["Mounts"] == registered_web["Mounts"] == [],
        "Current namespace, registered complete Web settings, and API readonly rootfs",
    )
    internal = project + "_demo_internal"
    networks = web["NetworkSettings"]["Networks"]
    require(internal in networks, "Owned internal Web endpoint")
    require(set(networks) <= {internal, project + "_demo_frontend"}, "Unexpected Web network")
    return str(networks[internal]["IPAddress"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--registration", required=True)
    parser.add_argument("--start-manifest", required=True)
    parser.add_argument("--port", type=int, default=15183)
    parser.add_argument("--max-seconds", type=int, default=5400)
    args = parser.parse_args()
    require(1024 <= args.port <= 65535 and 1 <= args.max_seconds <= 14400, "Bounded local ingress")
    if not args.run:
        print("PREPARED_NOT_EXECUTED: no socket, Docker, or financial request")
        return 0
    registration = (ROOT / args.registration).resolve()
    start_path = (ROOT / args.start_manifest).resolve()
    require(registration.is_relative_to(ROOT / ".runtime/w1-deployment"), "Registration scope")
    require(start_path.is_relative_to(ROOT / "docs/progress/evidence/W1"), "Actual start scope")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    before = source_state()
    original_inspect = (start_path.parent / "after-inspect.log").read_bytes()
    transport = validate_owned_transport(
        registration.read_bytes(),
        start_path.read_bytes(),
        original_inspect,
        {name: (start_path.parent / f"{name}-image.log").read_bytes() for name in ("api", "web")},
        before,
        head,
    )
    items = original_json(original_inspect)
    web = next(
        item for item in items if item["Config"]["Labels"]["com.docker.compose.service"] == "web"
    )
    web_id = web["Id"]
    fresh = subprocess.check_output(
        ["docker", "inspect", transport.api_container_id, web_id], cwd=ROOT
    )
    current = original_json(fresh)
    api_actual, web_actual = current
    web_ip = current_web_endpoint(
        api_actual,
        web_actual,
        web,
        api_id=transport.api_container_id,
        db_id=transport.db_container_id,
        api_image_id=transport.api_image_id,
        project=transport.project,
    )
    program = relay_program(web_ip)
    argv = [
        "docker",
        "exec",
        "-i",
        transport.api_container_id,
        "/opt/bf-venv/bin/python",
        "-c",
        program.decode(),
    ]
    run_id = datetime.now(UTC).strftime("loopback-relay-%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    destination = ROOT / "docs/progress/evidence/W1" / run_id
    destination.mkdir(exist_ok=False)
    stop_path = destination / "STOP"
    owner = json.loads(registration.read_bytes())
    private_env = (ROOT / owner["private_env_path"]).resolve()
    require(
        private_env.parent == registration.parent and private_env.name == "demo.env",
        "Private env scope",
    )
    values = dict(
        line.split("=", 1) for line in private_env.read_text(encoding="utf-8").splitlines()
    )
    private_capture = ROOT / ".runtime/w1-private-captures" / run_id / "actual-inspect-initial.json"
    inspect_capture = capture_inspect(
        fresh,
        values["BF_DEMO_PASSWORD"],
        private_capture,
        destination / "actual-inspect-initial.json",
    )
    manifest: dict[str, Any] = {
        "protocol": "bounded-funds-loopback-relay-v1",
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": datetime.now(UTC).isoformat(),
        "listen_host": "127.0.0.1",
        "listen_port": args.port,
        "owner_uuid": transport.owner_uuid,
        "api_container_id": transport.api_container_id,
        "web_container_id": web_id,
        "web_internal_ip": web_ip,
        "actual_web_readonly_rootfs": web_actual["HostConfig"]["ReadonlyRootfs"],
        "source_before": before,
        "source_head": head,
        "producer_sha256": sha(Path(__file__).read_bytes()),
        "registration_sha256": transport.registration_sha256,
        "start_manifest_sha256": transport.start_manifest_sha256,
        "inspect_capture": inspect_capture,
        "transport_header_change": "Connection: close",
        "financial_body_transformed": False,
        "automatic_retries": 0,
        "offline_verified": False,
        "requests": [],
        "errors": [],
    }
    guard = threading.Lock()

    def save() -> None:
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(610)
            record: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "attempts": 1}
            with guard:
                ordinal = len(manifest["requests"]) + 1
                manifest["requests"].append(record)
                save()
            prefix = f"request-{ordinal:05}"
            try:
                require(self.client_address[0] == "127.0.0.1", "Loopback client")
                require(source_state() == before, "Deployment source drift before request")
                lines = []
                total = 0
                while True:
                    line = self.rfile.readline(MAX_HEADERS + 1)
                    total += len(line)
                    require(bool(line) and total <= MAX_HEADERS, "Request header budget")
                    lines.append(line)
                    if line == b"\r\n":
                        break
                headers = b"".join(lines)
                lengths = [
                    line.split(b":", 1)[1].strip()
                    for line in lines[1:-1]
                    if line.split(b":", 1)[0].lower() == b"content-length"
                ]
                require(
                    len(lengths) <= 1
                    and (not lengths or re.fullmatch(rb"[0-9]+", lengths[0]) is not None),
                    "Original length syntax",
                )
                size = int(lengths[0]) if lengths else 0
                require(size <= MAX_BODY, "Body budget")
                body = self.rfile.read(size)
                raw = headers + body
                forwarded = request_frame(headers, body, args.port)
                (destination / f"{prefix}-client.http").write_bytes(raw)
                (destination / f"{prefix}-forwarded.http").write_bytes(forwarded)
                completed = subprocess.run(
                    argv, input=forwarded, capture_output=True, cwd=ROOT, timeout=620
                )
                (destination / f"{prefix}-response.http").write_bytes(completed.stdout)
                (destination / f"{prefix}-stderr.log").write_bytes(completed.stderr)
                record.update(
                    {
                        "command": argv,
                        "exit_code": completed.returncode,
                        "client_sha256": sha(raw),
                        "forwarded_sha256": sha(forwarded),
                        "body_sha256": sha(body),
                        "response_sha256": sha(completed.stdout),
                    }
                )
                require(
                    completed.returncode == 0
                    and re.match(rb"HTTP/1\.[01] [1-5][0-9]{2} ", completed.stdout) is not None,
                    "Actual upstream response",
                )
                require(source_state() == before, "Deployment source drift after request")
                self.connection.sendall(completed.stdout)
                record["status"] = "FORWARDED_ONCE"
            except Exception as error:
                record["status"] = "FAILED"
                record["error"] = {"type": type(error).__name__, "message": str(error)}
                with guard:
                    manifest["errors"].append(prefix)
                # No upstream retry, fallback response, or financial success injection.
            finally:
                record["finished_at"] = datetime.now(UTC).isoformat()
                with guard:
                    save()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = False
        daemon_threads = False

    save()
    print(f"LOOPBACK_RELAY={run_id}; STOP_FILE={stop_path.relative_to(ROOT)}", flush=True)
    try:
        with Server(("127.0.0.1", args.port), Handler) as server:
            server.timeout = 0.5
            until = time.monotonic() + args.max_seconds
            while not stop_path.exists() and time.monotonic() < until:
                server.handle_request()
        require(stop_path.exists(), "Ingress lifetime expired without owned stop")
        manifest["status"] = "PASSED" if not manifest["errors"] else "FAILED"
    except BaseException as error:
        manifest["status"] = "FAILED"
        manifest["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        manifest["source_after"] = source_state()
        if manifest["source_after"] != before:
            manifest["status"] = "FAILED"
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest["artifact_hashes"] = {
            path.name: sha(path.read_bytes())
            for path in destination.iterdir()
            if path.is_file() and path.name != "manifest.json"
        }
        save()
    print(f"LOOPBACK_RELAY={manifest['status']}", flush=True)
    return 0 if manifest["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
