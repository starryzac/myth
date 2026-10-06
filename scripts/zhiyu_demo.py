"""Small local Zhiyu launcher; fresh owned rounds retain every database and history."""

from __future__ import annotations

import argparse
import json
import os
import re
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
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "zhiyu"
PROTOCOL = "zhiyu-owned-demo-round-v1"
sys.path.insert(0, str(ROOT / "apps/api"))
sys.path.insert(0, str(ROOT))


def now() -> str:
    return datetime.now(UTC).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if private:
        path.chmod(0o600)


def owned_environment(environment: dict[str, str]) -> Any:
    """Guard the actual connection before migration, initialization, read or launch."""
    from app.db.testing import require_test_database
    from sqlalchemy.engine import make_url

    url = make_url(environment["DATABASE_URL"])
    name = require_test_database(url.database)
    require(
        url.drivername == "postgresql+psycopg"
        and url.host in {"127.0.0.1", "localhost", "::1"}
        and url.port == 54329
        and not url.query,
        "Demo requires local PostgreSQL on port 54329",
    )
    require(environment.get("SIMULATION_MODE") == "true", "Simulation mode is mandatory")
    require(environment.get("ZHIYU_DEMO_DATABASE") == name, "Exact demo database marker mismatch")
    require(
        url.username == "zhiyu_demo_" + name.removeprefix("bf_test_")
        and re.fullmatch(r"[0-9a-f]{48}", url.password or "") is not None,
        "Independent registered demo owner credentials required",
    )
    return url


def current_round(requested: str | None = None) -> Path:
    if requested is None:
        pointer = RUNTIME / "current.txt"
        require(pointer.is_file(), "No prepared round; run prepare first")
        requested = pointer.read_text(encoding="utf-8").strip()
    require(
        re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}", requested) is not None,
        "Invalid round identifier",
    )
    directory = (RUNTIME / requested).resolve()
    require(directory.parent == RUNTIME.resolve(), "Round escaped the owned runtime directory")
    return directory


def read_round(directory: Path) -> tuple[dict[str, Any], dict[str, str]]:
    metadata = json.loads((directory / "round.json").read_text(encoding="utf-8"))
    environment = json.loads((directory / "environment.private.json").read_text(encoding="utf-8"))
    require(metadata["protocol"] == PROTOCOL, "Unrecognized demo registration")
    url = owned_environment(environment)
    require(
        metadata["round"] == directory.name
        and metadata["database"] == url.database
        and metadata["owner"] == url.username,
        "Round registration and actual connection disagree",
    )
    return metadata, environment


def prepare(api_port: int, web_port: int) -> Path:
    from alembic import command
    from alembic.config import Config
    from app.db.session import create_database_engine
    from app.db.settings import DatabaseSettings
    from psycopg import connect, sql
    from sqlalchemy.engine import make_url

    from deploy.initialize_demo import initialize

    require(
        1024 <= api_port <= 65535 and 1024 <= web_port <= 65535 and api_port != web_port,
        "Two different nonprivileged local ports required",
    )
    source = make_url(DatabaseSettings().database_url)
    require(
        source.drivername == "postgresql+psycopg"
        and source.host in {"127.0.0.1", "localhost", "::1"}
        and source.port == 54329,
        "Administration requires verified local PostgreSQL on port 54329",
    )
    identity = uuid4().hex
    name, owner, password = "bf_test_" + identity, "zhiyu_demo_" + identity, secrets.token_hex(24)
    directory = RUNTIME / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + identity)
    directory.mkdir(parents=True, exist_ok=False)
    url = source.set(username=owner, password=password, database=name)
    environment = {
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "ZHIYU_DEMO_DATABASE": name,
        "ZHIYU_DEMO_ROUND": directory.name,
        "SIMULATION_MODE": "true",
        "LLM_ENABLED": "false",
        "VITE_ZHIYU_DEMO": "true",
        "API_PROXY_TARGET": f"http://127.0.0.1:{api_port}",
        "PYTHONPATH": str(ROOT / "apps/api"),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    owned_environment(environment)
    metadata: dict[str, Any] = {
        "protocol": PROTOCOL,
        "round": directory.name,
        "created_at": now(),
        "database": name,
        "owner": owner,
        "simulation": True,
        "ports": {"api": api_port, "web": web_port},
        "status": "PREPARING",
        "phase": "registration",
        "processes": [],
    }
    write_json(directory / "round.json", metadata)
    write_json(directory / "environment.private.json", environment, private=True)
    try:
        # Explicit postgres administration; never use the configured formal database.
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
            # Permission changes affect this new exact namespace only.
            administration.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
        owned_environment(environment)
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
            write_json(directory / "initialization.json", summary)
        finally:
            engine.dispose()
        metadata.update(status="PREPARED", phase="ready", prepared_at=now())
        write_json(directory / "round.json", metadata)
        (RUNTIME / "current.txt").write_text(directory.name + "\n", encoding="utf-8")
    except BaseException:
        metadata["status"] = "PREPARE_FAILED"
        write_json(directory / "round.json", metadata)
        (directory / "failure.private.log").write_text(traceback.format_exc(), encoding="utf-8")
        (directory / "failure.private.log").chmod(0o600)
        print(f"Preparation failed; retained round: {directory.name}", file=sys.stderr)
        raise
    return directory


def probe(url: str, *, database: str | None = None) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=3) as response:
            if database is None:
                return {"reachable": response.status == 200}
            value = json.load(response)
        matched = value.get("simulation") is True and value.get("environment_id") == database
        return {"reachable": True, "round_matches": matched, "epoch_id": value.get("epoch_id")}
    except (OSError, URLError, TimeoutError, ValueError):
        return {"reachable": False, "round_matches": False}


def status(directory: Path) -> dict[str, Any]:
    from app.db.session import create_database_engine
    from sqlalchemy import text

    metadata, environment = read_round(directory)
    require(
        metadata["status"] in {"PREPARED", "STARTING", "RUNNING", "START_FAILED"},
        "Round is not prepared",
    )
    url = owned_environment(environment)
    database: dict[str, Any] = {"reachable": False}
    engine = create_database_engine(environment["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            actual = connection.execute(text("SELECT current_database(), current_user")).one()
            database = {
                "reachable": True,
                "name_matches": actual[0] == url.database,
                "owner_matches": actual[1] == url.username,
            }
    except Exception:
        pass
    finally:
        engine.dispose()
    api_port, web_port = metadata["ports"]["api"], metadata["ports"]["web"]
    return {
        "round": directory.name,
        "simulation": True,
        "database": metadata["database"],
        "registration_status": metadata["status"],
        "database_check": database,
        "api": probe(
            f"http://127.0.0.1:{api_port}/api/v1/zhiyu/environment", database=url.database
        ),
        "web": probe(
            f"http://127.0.0.1:{web_port}/api/v1/zhiyu/environment", database=url.database
        ),
        "url": f"http://127.0.0.1:{web_port}/zhiyu.html",
        "recorded_processes": metadata["processes"],
    }


def start(directory: Path) -> dict[str, Any]:
    metadata, environment = read_round(directory)
    require(metadata["status"] in {"PREPARED", "RUNNING", "START_FAILED"}, "Round is not prepared")
    node = shutil.which("node")
    vite = ROOT / "apps/web/node_modules/vite/bin/vite.js"
    require(node is not None and vite.is_file(), "Installed Node and Web dependencies required")
    require((ROOT / "apps/web/zhiyu.html").is_file(), "Zhiyu browser entry is missing")
    api_port, web_port = metadata["ports"]["api"], metadata["ports"]["web"]
    for port in (api_port, web_port):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", port))
    launch_id = uuid4().hex[:8]
    commands = [
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--no-access-log",
                "app.zhiyu_main:create_zhiyu_app",
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
                str(vite),
                str(ROOT / "apps/web"),
                "--config",
                str(ROOT / "apps/web/vite.zhiyu.config.ts"),
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
    try:
        for label, arguments in commands:
            output = directory / f"{label}-{launch_id}.log"
            with output.open("xb") as log:
                process = subprocess.Popen(
                    arguments,
                    cwd=ROOT,
                    env={**os.environ, **environment},
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
                    "started_at": now(),
                    "log": output.name,
                }
            )
            write_json(directory / "round.json", metadata)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            require(
                all(process.poll() is None for process in processes),
                "An owned service exited; inspect its retained log",
            )
            api = probe(
                f"http://127.0.0.1:{api_port}/api/v1/zhiyu/environment",
                database=metadata["database"],
            )
            web = probe(
                f"http://127.0.0.1:{web_port}/api/v1/zhiyu/environment",
                database=metadata["database"],
            )
            if api.get("round_matches") and web.get("round_matches"):
                metadata.update(status="RUNNING", started_at=now())
                write_json(directory / "round.json", metadata)
                return status(directory)
            time.sleep(0.25)
        raise TimeoutError("Owned API/Web readiness timed out; inspect retained service logs")
    except BaseException:
        metadata["status"] = "START_FAILED"
        write_json(directory / "round.json", metadata)
        # Even failed startup retains recorded processes and history; no broad termination.
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "start", "status", "new-round"))
    parser.add_argument(
        "--round", help="Exact existing round identifier; default: registered current round"
    )
    parser.add_argument("--api-port", type=int, default=19000)
    parser.add_argument("--web-port", type=int, default=19173)
    arguments = parser.parse_args()
    try:
        if arguments.command in {"prepare", "new-round"}:
            require(arguments.round is None, "Creation cannot accept an existing round identifier")
            if arguments.command == "prepare" and (RUNTIME / "current.txt").is_file():
                directory = current_round()
                metadata, _ = read_round(directory)
                require(
                    metadata["status"] in {"PREPARED", "RUNNING", "START_FAILED"},
                    "Registered round requires inspection",
                )
            else:
                directory = prepare(arguments.api_port, arguments.web_port)
            metadata, _ = read_round(directory)
            result = {
                "round": directory.name,
                "database": metadata["database"],
                "status": metadata["status"],
                "ports": metadata["ports"],
                "start": "python scripts/zhiyu_demo.py start --round " + directory.name,
            }
        else:
            directory = current_round(arguments.round)
            result = start(directory) if arguments.command == "start" else status(directory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # SQL/connection exceptions can include CREATE ROLE credentials; never print their body.
        print(
            f"Zhiyu command failed ({type(error).__name__}); "
            "inspect the registered round phase and retained private/service logs.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
