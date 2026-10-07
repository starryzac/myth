"""Manage only owned Zhiyu extension rounds; preserve old Demo services and all histories."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "zhiyu-next"
PROTOCOL = "zhiyu-next-owned-simulation-round-v1"
PROTECTED_PORTS = frozenset(range(19000, 19007)) | frozenset(range(19173, 19180))
sys.path.insert(0, str(ROOT / "apps/api"))
sys.path.insert(0, str(ROOT))


def now() -> str:
    return datetime.now(UTC).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_json(path: Path, value: Any, *, private: bool = False) -> None:
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if private:
        temporary.chmod(0o600)
    temporary.replace(path)


def require_runtime_path() -> None:
    require(RUNTIME.resolve() == RUNTIME.absolute(), "Runtime cannot redirect to another directory")
    require(ROOT.name == "bounded-funds-next", "Launcher requires the independent extension root")


def require_independent_dependencies(*, web: bool = False) -> None:
    expected = ROOT / ".venv"
    require(
        expected.resolve() == expected.absolute() and Path(sys.prefix).resolve() == expected,
        "Run the extension's own .venv interpreter; shared Demo dependencies are forbidden",
    )
    if web:
        dependencies = ROOT / "apps/web/node_modules"
        require(
            dependencies.resolve() == dependencies.absolute()
            and (dependencies / "vite/bin/vite.js").is_file(),
            "Independent Web dependencies required; node_modules cannot redirect elsewhere",
        )


def require_ports(api_port: int, web_port: int, *, available: bool = False) -> None:
    require(
        1024 <= api_port <= 65535
        and 1024 <= web_port <= 65535
        and api_port != web_port
        and api_port not in PROTECTED_PORTS
        and web_port not in PROTECTED_PORTS,
        "Use two nonprivileged extension ports outside the retained Demo port ranges",
    )
    if available:
        for port in (api_port, web_port):
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", port))


def owned_environment(environment: dict[str, str]) -> Any:
    from app.zhiyu_next_isolation import require_zhiyu_next_environment

    url = require_zhiyu_next_environment(environment)
    require(environment.get("ZHIYU_NEXT_ROOT") == str(ROOT), "Extension root marker mismatch")
    require(
        environment.get("ZHIYU_NEXT_WORKER_ENABLED", "false") == "false",
        "Launcher never enables a worker; use its separate explicit owned entry",
    )
    # The narrow historical facade can be reused only with this same new namespace.
    require(
        environment.get("ZHIYU_DEMO_DATABASE") == url.database
        and environment.get("ZHIYU_DEMO_ROUND") == environment["ZHIYU_NEXT_ROUND"],
        "Reused facade aliases must point to this extension namespace",
    )
    return url


def current_round(requested: str | None = None) -> Path:
    import re

    from app.zhiyu_next_isolation import ROUND_PATTERN

    require_runtime_path()
    if requested is None:
        pointer = RUNTIME / "current.txt"
        require(pointer.is_file() and not pointer.is_symlink(), "Run prepare before using current")
        requested = pointer.read_text(encoding="utf-8").strip()
    require(
        re.fullmatch(ROUND_PATTERN, requested) is not None, "Invalid extension round identifier"
    )
    directory = RUNTIME / requested
    require(
        directory.resolve() == directory.absolute() and directory.parent == RUNTIME,
        "Round cannot redirect or escape its owned runtime directory",
    )
    return directory


def read_round(directory: Path) -> tuple[dict[str, Any], dict[str, str]]:
    require(current_round(directory.name) == directory, "Round directory ownership mismatch")
    metadata = json.loads((directory / "round.json").read_text(encoding="utf-8"))
    environment = json.loads((directory / "environment.private.json").read_text(encoding="utf-8"))
    require(metadata["protocol"] == PROTOCOL, "Unrecognized extension registration")
    url = owned_environment(environment)
    require(
        metadata["round"] == directory.name
        and metadata["root"] == str(ROOT)
        and metadata["database"] == url.database
        and metadata["environment_id"] == url.database
        and metadata["owner"] == url.username
        and metadata["variant"] == "zhiyu-next"
        and metadata["worker_enabled"] is False,
        "Registered extension identity and actual connection disagree",
    )
    require_ports(metadata["ports"]["api"], metadata["ports"]["web"])
    require(
        environment["API_PROXY_TARGET"] == f"http://127.0.0.1:{metadata['ports']['api']}",
        "Web proxy must target this round's API",
    )
    require(
        environment.get("ZHIYU_NEXT_WEB_PORT") == str(metadata["ports"]["web"]),
        "Browser origin must use this round's registered Web port",
    )
    return metadata, environment


def install_asset_catalogue(directory: Path) -> dict[str, Any]:
    """Append only server presets in one exact development round; no financial grant."""
    from app.db.session import create_database_engine
    from app.domain.demo_identity import DEMO_USER_ID
    from app.services.zhiyu_asset_catalog import ensure_zhiyu_asset_catalog

    require_runtime_path()
    require_independent_dependencies()
    metadata, environment = read_round(directory)
    require(
        metadata["status"] in {"PREPARING", "PREPARED", "START_FAILED", "RUNNING"}
        and metadata["simulation"] is True
        and metadata["worker_enabled"] is False,
        "Only an exact owned development round with its worker disabled is eligible",
    )
    expected_epoch = UUID(metadata["epoch_id"])
    previous = {key: os.environ.get(key) for key in environment}
    engine = None
    try:
        os.environ.update(environment)
        engine = create_database_engine(environment["DATABASE_URL"])
        original = ensure_zhiyu_asset_catalog(
            engine, DEMO_USER_ID, expected_epoch, datetime.now(UTC), purpose="DEVELOPMENT"
        )
    finally:
        if engine is not None:
            engine.dispose()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    evidence = directory / "asset-catalogue-registration.json"
    if evidence.exists():
        require(not evidence.is_symlink(), "Catalogue receipt must not redirect")
        require(
            json.loads(evidence.read_text(encoding="utf-8")) == original,
            "Preserve the original catalogue receipt; do not replace a different one",
        )
    else:
        write_json(evidence, original)
    return original


def prepare(api_port: int, web_port: int) -> Path:
    from alembic import command
    from alembic.config import Config
    from app.db.session import create_database_engine
    from app.db.settings import DatabaseSettings
    from app.domain.demo_identity import DEMO_USER_ID
    from app.services.audit_chain import current_audit_epoch
    from psycopg import connect, sql
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from deploy.initialize_demo import initialize

    require_runtime_path()
    require_independent_dependencies()
    require_ports(api_port, web_port, available=True)
    source = make_url(
        os.environ.get("ZHIYU_NEXT_ADMIN_DATABASE_URL")
        or DatabaseSettings(_env_file=None).database_url  # type: ignore[call-arg]
    )
    require(
        source.drivername == "postgresql+psycopg"
        and source.host in {"127.0.0.1", "localhost", "::1"}
        and source.port == 54329
        and not source.query
        and not (source.username or "").startswith(("zhiyu_demo_", "zhiyu_next_")),
        "Administration requires a local management role; Demo/extension credentials rejected",
    )
    identity = uuid4().hex
    name, owner = "bf_test_" + identity, "zhiyu_next_" + identity
    password = secrets.token_hex(24)
    directory = RUNTIME / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + identity)
    directory.mkdir(parents=True, exist_ok=False)
    environment = {
        "DATABASE_URL": source.set(
            username=owner, password=password, database=name
        ).render_as_string(hide_password=False),
        "ZHIYU_NEXT_DATABASE": name,
        "ZHIYU_NEXT_ENVIRONMENT_ID": name,
        "ZHIYU_NEXT_ROUND": directory.name,
        "ZHIYU_NEXT_ROOT": str(ROOT),
        "ZHIYU_NEXT_VARIANT": "zhiyu-next",
        "ZHIYU_NEXT_WORKER_ENABLED": "false",
        "ZHIYU_NEXT_WEB_PORT": str(web_port),
        "ZHIYU_DEMO_DATABASE": name,
        "ZHIYU_DEMO_ROUND": directory.name,
        "SIMULATION_MODE": "true",
        "LLM_ENABLED": "false",
        "VITE_ZHIYU_NEXT": "true",
        "API_PROXY_TARGET": f"http://127.0.0.1:{api_port}",
        "PYTHONPATH": str(ROOT / "apps/api"),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    owned_environment(environment)
    metadata: dict[str, Any] = {
        "protocol": PROTOCOL,
        "variant": "zhiyu-next",
        "round": directory.name,
        "root": str(ROOT),
        "created_at": now(),
        "database": name,
        "environment_id": name,
        "owner": owner,
        "simulation": True,
        "worker_enabled": False,
        "ports": {"api": api_port, "web": web_port},
        "status": "PREPARING",
        "phase": "registration",
        "processes": [],
    }
    write_json(directory / "round.json", metadata)
    write_json(directory / "environment.private.json", environment, private=True)
    try:
        with connect(
            host=source.host,
            port=54329,
            user=source.username,
            password=source.password,
            dbname="postgres",
            connect_timeout=5,
            autocommit=True,
        ) as administration:
            metadata["phase"] = "create_independent_owner"
            write_json(directory / "round.json", metadata)
            administration.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(owner), sql.Literal(password))
            )
            metadata["phase"] = "create_owned_database"
            write_json(directory / "round.json", metadata)
            administration.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(owner)
                )
            )
            administration.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
        configuration = Config(str(ROOT / "alembic.ini"))
        configuration.set_main_option("script_location", str(ROOT / "apps/api/alembic"))
        configuration.set_main_option(
            "sqlalchemy.url", environment["DATABASE_URL"].replace("%", "%%")
        )
        metadata["phase"] = "migrate_owned_database"
        write_json(directory / "round.json", metadata)
        command.upgrade(configuration, "head")
        engine = create_database_engine(environment["DATABASE_URL"])
        try:
            metadata["phase"] = "initialize_owned_database"
            write_json(directory / "round.json", metadata)
            summary = initialize(engine)
            with Session(engine) as session:
                epoch = current_audit_epoch(session, DEMO_USER_ID)
                if epoch is None:
                    raise ValueError("Fresh simulation audit epoch is missing")
                metadata["epoch_id"] = str(epoch.id)
            write_json(directory / "initialization.json", summary)
        finally:
            engine.dispose()
        metadata["phase"] = "append_server_owned_asset_catalogue"
        write_json(directory / "round.json", metadata)
        install_asset_catalogue(directory)
        metadata.update(status="PREPARED", phase="ready", prepared_at=now())
        write_json(directory / "round.json", metadata)
        pointer = RUNTIME / "current.txt.new"
        pointer.write_text(directory.name + "\n", encoding="utf-8")
        pointer.replace(RUNTIME / "current.txt")
    except BaseException:
        metadata["status"] = "PREPARE_FAILED"
        write_json(directory / "round.json", metadata)
        failure = directory / "failure.private.log"
        failure.write_text(traceback.format_exc(), encoding="utf-8")
        failure.chmod(0o600)
        print(f"Preparation failed; retained extension round: {directory.name}", file=sys.stderr)
        raise
    return directory


def probe(url: str, metadata: dict[str, Any]) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=3) as response:
            value = json.load(response)
        matched = (
            value.get("simulation") is True
            and value.get("variant") == "zhiyu-next"
            and value.get("environment_id") == metadata["environment_id"]
            and value.get("round_id") == metadata["round"]
            and value.get("epoch_id") == metadata["epoch_id"]
        )
        return {"reachable": True, "identity_matches": matched, "epoch_id": value.get("epoch_id")}
    except (OSError, URLError, TimeoutError, ValueError, AttributeError):
        return {"reachable": False, "identity_matches": False}


def database_identity(environment: dict[str, str], metadata: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import create_database_engine
    from app.domain.demo_identity import DEMO_USER_ID
    from app.services.audit_chain import current_audit_epoch
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    url = owned_environment(environment)
    engine = create_database_engine(environment["DATABASE_URL"])
    try:
        with Session(engine) as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            row = session.execute(
                text(
                    "SELECT current_database(), current_user, rolsuper, rolcreatedb, "
                    "rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            epoch = current_audit_epoch(session, DEMO_USER_ID)
            return {
                "reachable": True,
                "name_matches": row[0] == url.database,
                "owner_matches": row[1] == url.username,
                "low_privilege": not any(row[2:]),
                "epoch_matches": epoch is not None and str(epoch.id) == metadata["epoch_id"],
            }
    finally:
        engine.dispose()


def status(directory: Path) -> dict[str, Any]:
    metadata, environment = read_round(directory)
    require(
        metadata["status"] in {"PREPARED", "STARTING", "RUNNING", "START_FAILED"},
        "Extension round is not prepared",
    )
    UUID(metadata["epoch_id"])
    try:
        database = database_identity(environment, metadata)
    except Exception:
        database = {"reachable": False}
    api_port, web_port = metadata["ports"]["api"], metadata["ports"]["web"]
    return {
        "round": directory.name,
        "environment_id": metadata["environment_id"],
        "epoch_id": metadata["epoch_id"],
        "simulation": True,
        "variant": "zhiyu-next",
        "worker_enabled": False,
        "database": metadata["database"],
        "registration_status": metadata["status"],
        "database_check": database,
        "api": probe(f"http://127.0.0.1:{api_port}/api/v1/zhiyu-next/environment", metadata),
        "web": probe(f"http://127.0.0.1:{web_port}/api/v1/zhiyu-next/environment", metadata),
        "url": f"http://127.0.0.1:{web_port}/zhiyu-next.html",
        "recorded_processes": metadata["processes"],
    }


def start(directory: Path) -> dict[str, Any]:
    metadata, environment = read_round(directory)
    require(metadata["status"] in {"PREPARED", "START_FAILED"}, "Round is not ready for startup")
    require_independent_dependencies(web=True)
    require(all(database_identity(environment, metadata).values()), "Owned database check failed")
    node = shutil.which("node")
    require(node is not None, "Installed Node runtime required")
    require((ROOT / "apps/web/zhiyu-next.html").is_file(), "Extension browser entry is missing")
    api_port, web_port = metadata["ports"]["api"], metadata["ports"]["web"]
    require_ports(api_port, web_port, available=True)
    launch_id = uuid4().hex[:8]
    commands = [
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--no-access-log",
                "app.zhiyu_next_main:create_zhiyu_next_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
        ),
        (
            "web",
            [
                str(node),
                str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
                str(ROOT / "apps/web"),
                "--config",
                str(ROOT / "apps/web/vite.zhiyu-next.config.ts"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
            ],
        ),
    ]
    processes: list[subprocess.Popen[bytes]] = []
    metadata["status"] = "STARTING"
    write_json(directory / "round.json", metadata)
    child_environment = {**os.environ, **environment}
    child_environment.pop("ZHIYU_NEXT_ADMIN_DATABASE_URL", None)
    try:
        for label, arguments in commands:
            output = directory / f"{label}-{launch_id}.log"
            with output.open("xb") as log:
                process = subprocess.Popen(
                    arguments,
                    cwd=ROOT,
                    env=child_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    start_new_session=os.name != "nt",
                )
            processes.append(process)
            metadata["processes"].append(
                {
                    "label": label,
                    "pid": process.pid,
                    "arguments": arguments,
                    "cwd": str(ROOT),
                    "environment_id": metadata["environment_id"],
                    "round": directory.name,
                    "started_at": now(),
                    "log": output.name,
                }
            )
            write_json(directory / "round.json", metadata)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            require(all(process.poll() is None for process in processes), "An owned service exited")
            api = probe(f"http://127.0.0.1:{api_port}/api/v1/zhiyu-next/environment", metadata)
            web = probe(f"http://127.0.0.1:{web_port}/api/v1/zhiyu-next/environment", metadata)
            if api.get("identity_matches") and web.get("identity_matches"):
                metadata.update(status="RUNNING", started_at=now())
                write_json(directory / "round.json", metadata)
                return status(directory)
            time.sleep(0.25)
        raise TimeoutError("Owned extension API/Web readiness timed out; inspect retained logs")
    except BaseException:
        metadata["status"] = "START_FAILED"
        write_json(directory / "round.json", metadata)
        # Preserve processes and unresolved evidence. There is deliberately no bulk termination.
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "start", "status", "new-round", "install-asset-catalogue")
    )
    parser.add_argument("--round", help="Exact extension round; default: extension current pointer")
    parser.add_argument("--api-port", type=int, default=19200)
    parser.add_argument("--web-port", type=int, default=19273)
    arguments = parser.parse_args()
    try:
        if arguments.command in {"prepare", "new-round"}:
            require(arguments.round is None, "Creation cannot accept an existing round identifier")
            if arguments.command == "prepare" and (RUNTIME / "current.txt").is_file():
                directory = current_round()
                metadata, _ = read_round(directory)
                require(
                    metadata["status"] in {"PREPARED", "RUNNING", "START_FAILED"},
                    "Registered extension round requires inspection",
                )
            else:
                directory = prepare(arguments.api_port, arguments.web_port)
            metadata, _ = read_round(directory)
            result = {
                "round": directory.name,
                "database": metadata["database"],
                "epoch_id": metadata["epoch_id"],
                "status": metadata["status"],
                "ports": metadata["ports"],
                "start": ".venv/Scripts/python.exe scripts/zhiyu_next.py start --round "
                + directory.name,
            }
        else:
            directory = current_round(arguments.round)
            if arguments.command == "install-asset-catalogue":
                require(arguments.round is not None, "Catalogue install requires an exact round")
                result = install_asset_catalogue(directory)
            else:
                result = start(directory) if arguments.command == "start" else status(directory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            f"Zhiyu extension command failed ({type(error).__name__}); "
            "inspect this extension round's registration and retained private/service logs.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
