"""Operate one registered simulated Docker project; retain its volume and all history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_state() -> dict[str, str]:
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode("utf-8")
        .split("\0")
    )
    configuration = {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "alembic.ini",
        ".dockerignore",
        "scripts/seed_demo.py",
        "scripts/scenario_runner_rpc.py",
    }
    return {
        name: sha((ROOT / name).read_bytes())
        for name in sorted(set(names) - {""})
        if (ROOT / name).is_file()
        and (
            name.startswith(("apps/api/", "apps/web/", "packages/contracts/", "deploy/"))
            or name in configuration
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--registration", required=True)
    parser.add_argument("--action", choices=("build", "start", "inspect", "stop"), required=True)
    args = parser.parse_args()
    if not args.run:
        print("PREPARED_NOT_EXECUTED: no Docker mutation or connection")
        return 0
    registration = (ROOT / args.registration).resolve()
    require(
        registration.is_relative_to((ROOT / ".runtime/w1-deployment").resolve())
        and registration.name == "manifest.json",
        "Exact private deployment registration required",
    )
    raw_owner = registration.read_bytes()
    owner = json.loads(raw_owner)
    identity = owner["owner_uuid"]
    require(re.fullmatch(r"[0-9a-f]{32}", identity) is not None, "Owned UUID required")
    project = f"bf-demo-{identity}"
    database = f"bf_test_{identity}"
    require(
        owner["project"] == project and owner["database"] == database, "Owner identity mismatch"
    )
    require(owner["compose_file"] == "deploy/compose.demo.yaml", "Separate demo Compose required")
    env_path = (ROOT / owner["private_env_path"]).resolve()
    require(
        env_path.parent == registration.parent and env_path.name == "demo.env",
        "Private env escaped",
    )
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        require(key.startswith("BF_") and key not in values, "Unexpected or duplicate env key")
        values[key] = value
    require(values["BF_DEMO_UUID"] == identity, "Private owner mismatch")
    require(values["BF_DEMO_RUN_ID"] == owner["run_id"], "Private run mismatch")
    password = values["BF_DEMO_PASSWORD"]
    require(
        re.fullmatch(r"[0-9a-f]{48}", password) is not None, "Independent hex password required"
    )
    frozen = source_state()
    source_current = frozen == owner["source_before"]
    if args.action in {"build", "start"}:
        require(source_current, "Registered deployment source drift")
    digest = sha(json.dumps(owner["source_before"], sort_keys=True, separators=(",", ":")).encode())
    require(
        digest == owner["source_digest"] == values["BF_DEMO_SOURCE_DIGEST"],
        "Source digest mismatch",
    )
    for key in ("BF_UV_LOCK_SHA256", "BF_PNPM_LOCK_SHA256"):
        name = "uv.lock" if key == "BF_UV_LOCK_SHA256" else "pnpm-lock.yaml"
        require(values[key] == owner["source_before"][name], "Original dependency lock mismatch")
    require(
        values["BF_DEMO_API_IMAGE"] == f"bounded-funds-api:demo-{identity}",
        "API image owner mismatch",
    )
    require(
        values["BF_DEMO_WEB_IMAGE"] == f"bounded-funds-web:demo-{identity}",
        "Web image owner mismatch",
    )
    for service in ("api", "web"):
        key = f"BF_DEMO_{service.upper()}_PORT"
        require(values[key] == str(owner["ports"][service]), "Port registration mismatch")
        require(owner["ports"][service] in {18049, 15181}, "Unregistered public port")
    for key, original in owner["images"].items():
        require(values[key] in original["inspect"]["RepoDigests"], "Unregistered base digest")
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("BF_", "COMPOSE_"))
    }
    run_id = f"deployment-{args.action}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    destination = ROOT / "docs/progress/evidence/W1" / run_id
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "registration.json").write_bytes(raw_owner)
    manifest: dict[str, Any] = {
        "protocol": "bounded-funds-demo-deployment-v1",
        "status": "INCOMPLETE",
        "action": args.action,
        "run_id": run_id,
        "owner_run_id": owner["run_id"],
        "owner_uuid": identity,
        "project": project,
        "database": database,
        "registration_sha256": sha(raw_owner),
        "registered_source_current": source_current,
        "source_before": frozen,
        "producer_path": "scripts/w1_demo_deployment.py",
        "producer_sha256": sha(Path(__file__).read_bytes()),
        "commands": [],
        "formal_database_touched": False,
        "volume_policy": "RETAIN: never delete containers, volumes, databases or history",
        "network_boundary": "LOCAL_DEPLOYMENT_ONLY: external network isolation not yet verified",
        "started_at": datetime.now(UTC).isoformat(),
    }

    def dump(path: Path, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False, indent=2).replace(password, "[REDACTED]")
        path.write_text(encoded + "\n", encoding="utf-8")

    def command(arguments: list[str], label: str, timeout: int = 60) -> bytes:
        print(f"deployment_phase={label} status=STARTED", flush=True)
        started = datetime.now(UTC)
        result = subprocess.run(
            arguments, cwd=ROOT, env=environment, capture_output=True, timeout=timeout
        )
        safe = (
            (result.stdout + result.stderr)
            .decode("utf-8", errors="replace")
            .replace(password, "[REDACTED]")
        )
        (destination / f"{label}.log").write_text(safe, encoding="utf-8")
        manifest["commands"].append(
            {
                "command": arguments,
                "label": label,
                "exit_code": result.returncode,
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "log_sha256": sha(safe.encode("utf-8")),
            }
        )
        print(f"deployment_phase={label} exit_code={result.returncode}", flush=True)
        require(
            result.returncode == 0, f"Actual Docker {label} failed; original redacted log retained"
        )
        return result.stdout

    def own_containers(label: str) -> list[dict[str, Any]]:
        listing = (
            command(
                ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                f"{label}-list",
            )
            .decode()
            .split()
        )
        result: list[dict[str, Any]] = []
        if listing:
            actual = json.loads(command(["docker", "inspect", *listing], f"{label}-inspect"))
            # Inspect contains the private generated password; all captured logs are redacted.
            for item in actual:
                labels = item["Config"]["Labels"] or {}
                require(
                    labels.get("io.bounded-funds.owner-uuid") == identity,
                    "Container has another owner",
                )
                require(
                    labels.get("io.bounded-funds.run-id") == owner["run_id"],
                    "Container has another run",
                )
                result.append(
                    {
                        "id": item["Id"],
                        "name": item["Name"],
                        "image_id": item["Image"],
                        "labels": labels,
                        "state": item["State"],
                        "ports": item["NetworkSettings"]["Ports"],
                        "networks": item["NetworkSettings"]["Networks"],
                        "network_mode": item["HostConfig"]["NetworkMode"],
                        "mounts": item["Mounts"],
                    }
                )
        return result

    compose = [
        "docker",
        "compose",
        "--env-file",
        str(env_path),
        "-f",
        "deploy/compose.demo.yaml",
        "-p",
        project,
    ]
    try:
        actual_engine = json.loads(command(["docker", "info", "--format", "{{json .}}"], "engine"))
        require(actual_engine["OSType"] == "linux", "Actual Linux daemon required")
        manifest["daemon"] = {
            key: actual_engine.get(key) for key in ("ID", "OSType", "Architecture", "ServerVersion")
        }
        configuration = json.loads(
            command([*compose, "config", "--format", "json"], "compose-config")
        )
        require(configuration["name"] == project, "Compose project escaped owner")
        require(
            set(configuration["services"]) == {"db", "init", "api", "web"}, "Unexpected service"
        )
        manifest["containers_before"] = own_containers("before")
        if args.action == "start":
            for service in ("api", "web"):
                image = json.loads(
                    command(
                        ["docker", "image", "inspect", values[f"BF_DEMO_{service.upper()}_IMAGE"]],
                        f"{service}-image",
                    )
                )[0]
                labels = image["Config"]["Labels"]
                require(
                    image["Os"] == "linux" and image["Architecture"] == "amd64",
                    "Image platform mismatch",
                )
                require(
                    labels.get("io.bounded-funds.source-digest") == digest,
                    "Image source digest mismatch",
                )
                require(
                    labels.get("org.opencontainers.image.revision") == owner["source_head"],
                    "Image HEAD mismatch",
                )
            if not manifest["containers_before"]:
                volumes = (
                    command(
                        ["docker", "volume", "ls", "-q", "--filter", f"name=^{project}_"],
                        "preexisting-volumes",
                    )
                    .decode()
                    .split()
                )
                require(not volumes, "Unregistered preexisting project volume; preserve it")
            command(
                [
                    *compose,
                    "up",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    "--wait-timeout",
                    "240",
                ],
                "start",
                300,
            )
        elif args.action == "build":
            require(not manifest["containers_before"], "Do not rebuild a running project")
            command([*compose, "build", "api", "web"], "build", 1200)
        elif args.action == "stop":
            require(bool(manifest["containers_before"]), "No owned containers to stop")
            command([*compose, "stop", "--timeout", "30"], "stop", 90)
        manifest["containers_after"] = own_containers("after")
        command([*compose, "ps", "--all", "--format", "json"], "services")
        if manifest["containers_after"]:
            command([*compose, "logs", "--no-color", "--timestamps"], "service-logs", 90)
        require(source_state() == frozen, "Source drift during actual deployment")
        manifest["status"] = "PASSED"
    except BaseException as error:
        manifest["status"] = "FAILED"
        manifest["error"] = {
            "type": type(error).__name__,
            "message": str(error).replace(password, "[REDACTED]"),
        }
    finally:
        manifest["source_after"] = source_state()
        manifest["producer_after_sha256"] = sha(Path(__file__).read_bytes())
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest["artifact_hashes"] = {
            path.name: sha(path.read_bytes()) for path in destination.iterdir() if path.is_file()
        }
        dump(destination / "manifest.json", manifest)
    print(f"DEPLOYMENT={manifest['status']}; output={destination.relative_to(ROOT)}", flush=True)
    return 0 if manifest["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
