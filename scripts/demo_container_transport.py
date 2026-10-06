"""Pure source/ownership validation and argv construction; never execute Docker or finance."""

from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import io
import json
import re
import runpy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "ISOLATED_SIMULATED_DEMO"
PROTOCOL = "bounded-funds-demo-container-transport-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in items:
        require(name not in result, "Duplicate original JSON key")
        result[name] = value
    return result


def _nonfinite(_: str) -> None:
    raise ValueError("Nonfinite original JSON number")


def original_json(raw: bytes) -> Any:
    require(len(raw) <= 32 * 1024 * 1024, "Original metadata byte budget exceeded")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_nonfinite)


def object_value(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "Original object missing")
    return cast(dict[str, Any], value)


def list_value(value: Any) -> list[Any]:
    require(type(value) is list, "Original list missing")
    return cast(list[Any], value)


def path_value(value: Any) -> str:
    require(isinstance(value, str) and bool(value), "Registered source path missing")
    name = cast(str, value)
    require(
        "\\" not in name and ":" not in name and not name.startswith("/"),
        "Noncanonical source path",
    )
    require(
        all(
            part
            and part not in {".", "..", ".git", ".venv", "node_modules", "__pycache__"}
            and not part.startswith(".env")
            for part in name.split("/")
        ),
        "Unsafe registered source path",
    )
    return name


def source_digest(files: dict[str, str]) -> str:
    for name, checksum in files.items():
        path_value(name)
        require(
            re.fullmatch(r"[0-9a-f]{64}", checksum) is not None, "Registered source hash malformed"
        )
    return sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())


def registered_business_tables(transport: OwnedTransport) -> set[str]:
    """Read original literal SQLAlchemy table declarations without loading financial code."""
    name = "apps/api/app/db/models.py"
    original = (ROOT / name).read_bytes()
    require(
        dict(transport.runtime_sources).get(name) == sha(original),
        "Original model registry source drift",
    )
    tables = {
        assignment.value.value
        for node in ast.parse(original).body
        if isinstance(node, ast.ClassDef)
        for assignment in node.body
        if isinstance(assignment, ast.Assign)
        if any(
            isinstance(target, ast.Name) and target.id == "__tablename__"
            for target in assignment.targets
        )
        and isinstance(assignment.value, ast.Constant)
        and isinstance(assignment.value.value, str)
    }
    require(
        len(tables) == 23 and "users" in tables and "audit_epochs" in tables,
        "Original 23-table model registry differs; update contract explicitly",
    )
    return tables


@dataclass(frozen=True)
class OwnedTransport:
    owner_uuid: str
    owner_run_id: str
    start_run_id: str
    database: str
    project: str
    source_head: str
    source_digest: str
    api_container_id: str
    db_container_id: str
    api_image_id: str
    runtime_sources: tuple[tuple[str, str], ...]
    registration_sha256: str
    start_manifest_sha256: str
    inspect_sha256: str
    network_boundary: str = (
        "DB_API_SHARED_LOOPBACK_NAMESPACE; NETWORK_INTERNAL_FLAG_UNVERIFIED; WEB_EGRESS_UNVERIFIED"
    )


@dataclass(frozen=True)
class PreparedCommand:
    argv: tuple[str, ...]
    stdin: bytes
    kind: str
    container_id: str
    source_digest: str

    @property
    def stdin_sha256(self) -> str:
        return sha(self.stdin)


@dataclass(frozen=True)
class DecodedSnapshot:
    original_stdout: bytes
    snapshot_gzip: bytes
    snapshot_data: bytes
    report: dict[str, Any]


def validate_owned_transport(
    registration_raw: bytes,
    start_raw: bytes,
    container_inspect_raw: bytes,
    image_inspect_raw: dict[str, bytes],
    current_source: dict[str, str],
    current_head: str,
    *,
    require_started_inspect: bool = True,
) -> OwnedTransport:
    """Validate supplied original bytes, not a PASSED string or a mutable container name.

    Caller supplies a freshly captured inspect for each invocation. The initial-start
    raw inspect must match its original hash; later fresh inspections can be separately
    recorded and validated with require_started_inspect=False against immutable IDs.
    """
    owner = object_value(original_json(registration_raw))
    start = object_value(original_json(start_raw))
    identity = owner.get("owner_uuid")
    require(
        isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{32}", identity) is not None,
        "Owned demo UUID missing",
    )
    project, database = f"bf-demo-{identity}", f"bf_test_{identity}"
    require(
        owner.get("project") == project
        and owner.get("database") == database
        and owner.get("compose_file") == "deploy/compose.demo.yaml"
        and owner.get("formal_database_touched") is False,
        "Registration ownership escaped",
    )
    require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(owner.get("run_id", ""))) is not None,
        "Original owner run identity malformed",
    )
    ports = object_value(owner.get("ports"))
    require(
        set(ports) == {"api", "web"}
        and all(type(value) is int and 1024 <= value <= 65535 for value in ports.values())
        and ports["api"] != ports["web"]
        and not {ports["api"], ports["web"]} & {54329, 8000, 5173, 18047, 15179},
        "Independent registered demo ports required",
    )
    registered = object_value(owner.get("source_before"))
    require(
        registered == current_source
        and owner.get("source_head") == current_head
        and re.fullmatch(r"[0-9a-f]{40}", current_head) is not None,
        "Registered image source is no longer current; retain old evidence",
    )
    digest = source_digest(cast(dict[str, str], registered))
    require(digest == owner.get("source_digest"), "Registration source digest differs")
    require(
        start.get("protocol") == "bounded-funds-demo-deployment-v1"
        and start.get("action") == "start"
        and start.get("status") == "PASSED"
        and start.get("registration_sha256") == sha(registration_raw)
        and start.get("owner_uuid") == identity
        and start.get("owner_run_id") == owner.get("run_id")
        and start.get("project") == project
        and start.get("database") == database
        and start.get("formal_database_touched") is False
        and start.get("registered_source_current") is True
        and start.get("source_before") == start.get("source_after") == registered,
        "Actual deployment start/registration/source binding differs",
    )
    commands = list_value(start.get("commands"))
    require(
        isinstance(commands, list)
        and bool(commands)
        and all(
            type(item) is dict and type(item.get("exit_code")) is int and item["exit_code"] == 0
            for item in commands
        ),
        "Original deployment command failure remains failed",
    )
    require(
        {"start", "after-inspect", "api-image", "web-image"}.issubset(
            {item.get("label") for item in commands}
        ),
        "Actual start/image/inspect commands missing",
    )
    hashes = object_value(start.get("artifact_hashes"))
    if require_started_inspect:
        require(
            hashes.get("after-inspect.log") == sha(container_inspect_raw),
            "Actual start inspect bytes changed",
        )
    require(set(image_inspect_raw) == {"api", "web"}, "Actual API/Web image originals required")
    images: dict[str, str] = {}
    for service, raw in image_inspect_raw.items():
        require(
            hashes.get(service + "-image.log") == sha(raw), "Actual image inspect original changed"
        )
        parsed = original_json(raw)
        require(isinstance(parsed, list) and len(parsed) == 1, "Single actual image required")
        image = object_value(parsed[0])
        require(
            image.get("Os") == "linux" and image.get("Architecture") == "amd64",
            "Linux amd64 image required",
        )
        labels = object_value(object_value(image.get("Config")).get("Labels"))
        lock = "uv.lock" if service == "api" else "pnpm-lock.yaml"
        label = (
            "io.bounded-funds.uv-lock-sha256"
            if service == "api"
            else "io.bounded-funds.pnpm-lock-sha256"
        )
        require(
            labels.get("io.bounded-funds.purpose") == PURPOSE
            and labels.get("io.bounded-funds.source-digest") == digest
            and labels.get("org.opencontainers.image.revision") == current_head
            and labels.get(label) == registered.get(lock),
            "Actual image source/lock labels differ",
        )
        image_id = image.get("Id")
        require(
            isinstance(image_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None,
            "Immutable actual image ID missing",
        )
        images[service] = cast(str, image_id)
    parsed = original_json(container_inspect_raw)
    require(
        isinstance(parsed, list) and len(parsed) == 4,
        "Exact four owned service containers required",
    )
    containers: dict[str, dict[str, Any]] = {}
    original_summaries = list_value(start.get("containers_after"))
    require(
        isinstance(original_summaries, list)
        and len(original_summaries) == 4
        and all(type(item) is dict for item in original_summaries),
        "Four actual start identities required",
    )
    summaries = {item["id"]: item for item in original_summaries}
    require(len(summaries) == 4, "Actual start identity duplicated")
    for value in parsed:
        container = object_value(value)
        labels = object_value(object_value(container.get("Config")).get("Labels"))
        service = cast(str, labels.get("com.docker.compose.service"))
        require(
            service in {"api", "web", "db", "init"} and service not in containers,
            "Foreign/duplicate service container",
        )
        container_id = container.get("Id")
        require(
            isinstance(container_id, str)
            and re.fullmatch(r"[0-9a-f]{64}", container_id) is not None,
            "Immutable full container ID required",
        )
        require(
            container_id in summaries
            and summaries[container_id].get("image_id") == container.get("Image"),
            "Container replaced since registered actual start",
        )
        require(
            container.get("Name") == f"/{project}-{service}-1"
            and labels.get("com.docker.compose.project") == project
            and labels.get("io.bounded-funds.owner-uuid") == identity
            and labels.get("io.bounded-funds.run-id") == owner.get("run_id")
            and labels.get("io.bounded-funds.purpose") == PURPOSE,
            "Actual container ownership labels/name differ",
        )
        host = object_value(container.get("HostConfig"))
        permitted_binds: tuple[Any, ...] = (None, [])
        if service == "db":
            # Docker serializes Compose's named-volume short syntax into Binds;
            # Mounts below must separately confirm Type=volume and the owned Name.
            permitted_binds += ([project + "_demo_pgdata:/var/lib/postgresql/data:rw"],)
        require(
            host.get("Privileged") is False and host.get("Binds") in permitted_binds,
            "Privileged/foreign bind container refused",
        )
        state = object_value(container.get("State"))
        require(
            state.get("Paused") is False
            and state.get("Restarting") is False
            and state.get("OOMKilled") is False
            and state.get("Dead") is False,
            "Container unstable",
        )
        if service == "init":
            require(
                state.get("Running") is False
                and state.get("Status") == "exited"
                and type(state.get("ExitCode")) is int
                and state["ExitCode"] == 0,
                "Actual original initializer did not complete",
            )
        else:
            require(
                state.get("Running") is True
                and state.get("Status") == "running"
                and object_value(state.get("Health")).get("Status") == "healthy",
                "Actual service is not healthy",
            )
        if service in {"api", "init", "web"}:
            expected_image = images["api" if service == "init" else service]
            require(
                container.get("Image") == expected_image
                and labels.get("io.bounded-funds.source-digest") == digest
                and labels.get("org.opencontainers.image.revision") == current_head,
                "Container image source differs",
            )
        containers[service] = container
    db_id = containers["db"]["Id"]
    postgres = object_value(
        object_value(object_value(owner.get("images")).get("BF_DEMO_POSTGRES_IMAGE")).get("inspect")
    )
    require(
        containers["db"].get("Image") == postgres.get("Id"),
        "Actual PostgreSQL image differs from pin",
    )
    require(
        object_value(containers["db"]["Config"]).get("Cmd") == ["postgres", "-p", "54329"],
        "Actual namespace database port differs from original guard",
    )
    require(
        object_value(containers["db"]["HostConfig"]).get("NetworkMode")
        == project + "_demo_internal",
        "Database namespace escaped internal demo network",
    )
    network = object_value(containers["db"].get("NetworkSettings"))
    require(
        set(object_value(network.get("Networks"))) == {project + "_demo_internal"},
        "Database has another network",
    )
    db_ports = object_value(containers["db"]["HostConfig"]).get("PortBindings", {})
    require(
        db_ports == {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(owner["ports"]["api"])}]},
        "Database/API namespace port publication escaped; PostgreSQL host port refused",
    )
    mounts = containers["db"].get("Mounts")
    require(
        isinstance(mounts, list)
        and len(mounts) == 1
        and mounts[0].get("Type") == "volume"
        and mounts[0].get("Name") == project + "_demo_pgdata"
        and mounts[0].get("Destination") == "/var/lib/postgresql/data",
        "Owned independent database volume required",
    )
    for service in ("api", "init"):
        container = containers[service]
        host = object_value(container["HostConfig"])
        require(
            host.get("NetworkMode") == "container:" + db_id
            and host.get("ReadonlyRootfs") is True
            and host.get("PortBindings") in ({}, None)
            and container.get("Mounts") == [],
            "API/init namespace/read-only image escaped actual database container",
        )
        require(
            object_value(container["NetworkSettings"]).get("Networks") == {},
            "API/init may only inherit the exact database namespace",
        )
        values = list_value(object_value(container["Config"]).get("Env"))
        require(
            all(isinstance(value, str) and "=" in value for value in values),
            "Actual container environment malformed",
        )
        environment = dict(value.split("=", 1) for value in values)
        require(
            len(environment) == len(values)
            and environment.get("SIMULATION_MODE") == "true"
            and environment.get("LLM_ENABLED") == "false",
            "Simulation-only container mode differs",
        )
        # Use the exact application's pure SQLAlchemy URL parser; this also
        # handles original [REDACTED] log passwords without pretending IPv6.
        target = make_url(environment.get("DATABASE_URL", ""))
        require(
            target.drivername == "postgresql+psycopg"
            and target.host == "127.0.0.1"
            and target.port == 54329
            and target.username == "bf_demo"
            and target.database == database
            and not target.query,
            "Actual container DSN escaped original local test guard",
        )
    web_network = object_value(containers["web"]["NetworkSettings"])
    require(
        set(object_value(web_network.get("Networks")))
        == {project + "_demo_internal", project + "_demo_frontend"},
        "Web attached to another network",
    )
    require(
        web_network.get("Ports")
        == {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(owner["ports"]["web"])}]},
        "Original Web loopback port differs",
    )
    copied = (
        "pyproject.toml",
        "uv.lock",
        "alembic.ini",
        "scripts/scenario_runner_rpc.py",
        "scripts/seed_demo.py",
        "deploy/initialize_demo.py",
    )
    runtime = tuple(
        sorted(
            (name, checksum)
            for name, checksum in current_source.items()
            if name.startswith("apps/api/") or name in copied
        )
    )
    require(
        all(name in dict(runtime) for name in copied),
        "Copied financial RPC/config source bindings missing",
    )
    return OwnedTransport(
        cast(str, identity),
        owner["run_id"],
        start["run_id"],
        database,
        project,
        current_head,
        digest,
        containers["api"]["Id"],
        db_id,
        images["api"],
        runtime,
        sha(registration_raw),
        sha(start_raw),
        sha(container_inspect_raw),
    )


def _argv(transport: OwnedTransport) -> tuple[str, ...]:
    require(
        re.fullmatch(r"[0-9a-f]{64}", transport.api_container_id) is not None,
        "Full immutable API ID required",
    )
    return (
        "docker",
        "exec",
        "-i",
        "--workdir",
        "/workspace",
        transport.api_container_id,
        "/opt/bf-venv/bin/python",
    )


def build_snapshot_command(transport: OwnedTransport, run_id: str) -> PreparedCommand:
    require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id) is not None,
        "Explicit safe observation run required",
    )
    helper = ROOT / "scripts/demo_container_snapshot.py"
    raw = helper.read_bytes()
    config = {
        "protocol": PROTOCOL,
        "run_id": run_id,
        "owner_uuid": transport.owner_uuid,
        "owner_run_id": transport.owner_run_id,
        "database": transport.database,
        "source_head": transport.source_head,
        "source_digest": transport.source_digest,
        "api_container_id": transport.api_container_id,
        "runtime_sources": dict(transport.runtime_sources),
        "snapshot_helper_sha256": sha(raw),
    }
    literal = repr(json.dumps(config, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    stdin = raw + ("\nraise SystemExit(main(json.loads(" + literal + ")))\n").encode()
    return PreparedCommand(
        _argv(transport) + ("-",),
        stdin,
        "READ_ONLY_NATIVE_SNAPSHOT_AUDIT",
        transport.api_container_id,
        transport.source_digest,
    )


def build_rpc_command(transport: OwnedTransport, dto: dict[str, Any]) -> PreparedCommand:
    """Validate original DTO; never call ScenarioRunner, add clocks/results or execute argv."""
    payload = json.dumps(dto, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    original_json(payload)
    runtime = dict(transport.runtime_sources)
    for name in ("apps/api/app/services/scenario_types.py", "scripts/scenario_runner_rpc.py"):
        require(
            runtime.get(name) == sha((ROOT / name).read_bytes()), "Original RPC/DTO source drift"
        )
    module = runpy.run_path(str(ROOT / "apps/api/app/services/scenario_types.py"))
    module["ScenarioRPC"].model_validate_json(payload)
    return PreparedCommand(
        _argv(transport) + ("/workspace/scripts/scenario_runner_rpc.py",),
        payload + b"\n",
        "ORIGINAL_PRIVATE_SCENARIO_RPC_NOT_EXECUTED",
        transport.api_container_id,
        transport.source_digest,
    )


def decode_snapshot_output(
    transport: OwnedTransport,
    command: PreparedCommand,
    stdout: bytes,
) -> DecodedSnapshot:
    """Validate native framing and return original bytes; never rewrite/hash historical rows.

    Caller must retain stdout/stderr/exit code before calling this validator, including
    failed output. A validated frame does not prove Docker was executed or offline.
    """
    require(
        command.kind == "READ_ONLY_NATIVE_SNAPSHOT_AUDIT"
        and command.container_id == transport.api_container_id
        and command.source_digest == transport.source_digest,
        "Snapshot command binding differs",
    )
    report = object_value(original_json(stdout))
    require(
        report.get("protocol") == "bounded-funds-container-snapshot-v1"
        and report.get("status") == "PASSED"
        and report.get("owner_uuid") == transport.owner_uuid
        and report.get("owner_run_id") == transport.owner_run_id
        and report.get("database") == transport.database
        and report.get("api_container_id") == transport.api_container_id
        and report.get("source_head") == transport.source_head
        and report.get("source_digest") == transport.source_digest
        and report.get("runtime_source_before")
        == report.get("runtime_source_after")
        == dict(transport.runtime_sources),
        "Native snapshot source/ownership/status differs",
    )
    helper = (ROOT / "scripts/demo_container_snapshot.py").read_bytes()
    require(
        report.get("snapshot_helper_sha256") == sha(helper) and command.stdin.startswith(helper),
        "Executed snapshot helper differs",
    )
    literal = command.stdin[len(helper) :].decode()
    # Generated invocation is bounded and has no extra command/argument mutation.
    tree = ast.parse(literal)
    require(
        len(tree.body) == 1 and isinstance(tree.body[0], ast.Raise), "Snapshot invocation malformed"
    )
    constants = [item.value for item in ast.walk(tree) if isinstance(item, ast.Constant)]
    require(
        len(constants) == 1 and isinstance(constants[0], str), "Snapshot configuration malformed"
    )
    config = object_value(original_json(cast(str, constants[0]).encode()))
    expected = build_snapshot_command(transport, config["run_id"])
    require(
        expected == command and report.get("run_id") == config["run_id"],
        "Snapshot run/command differs",
    )
    transaction = object_value(report.get("actual_transaction"))
    require(
        transaction.get("isolation_query") == "SHOW transaction_isolation"
        and transaction.get("isolation") == "repeatable read"
        and transaction.get("readonly_query") == "SHOW transaction_read_only"
        and transaction.get("readonly") == "on",
        "Actual native read-only RR proof missing",
    )
    require(
        transaction.get("identity_query")
        == (
            "SELECT current_database() AS database, inet_server_addr()::text AS server_address, "
            "inet_server_port() AS server_port, current_user AS database_user"
        ),
        "Actual native identity SQL differs",
    )
    identities = list_value(transaction.get("identity_rows"))
    require(isinstance(identities, list) and len(identities) == 1, "Actual native identity missing")
    identity = object_value(identities[0])
    require(
        identity.get("database") == transport.database
        and identity.get("server_address") in {"127.0.0.1", "127.0.0.1/32"}
        and type(identity.get("server_port")) is int
        and identity["server_port"] == 54329
        and identity.get("database_user") == "bf_demo",
        "Actual native SQL endpoint differs",
    )
    encoded = report.get("snapshot_gzip_base64")
    require(isinstance(encoded, str), "Original gzip framing missing")
    raw = base64.b64decode(cast(str, encoded), validate=True)
    meta = object_value(report.get("snapshot"))
    require(
        meta.get("sha256") == sha(raw) and meta.get("compressed_bytes") == len(raw),
        "Original gzip bytes/hash differ",
    )
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        data_raw = stream.read(64 * 1024 * 1024 + 1)
    require(
        len(data_raw) <= 64 * 1024 * 1024
        and meta.get("bytes") == len(data_raw)
        and meta.get("data_sha256") == report.get("snapshot_after_data_sha256") == sha(data_raw),
        "Original rows/hash/read-only before-after proof differ",
    )
    # Parse actual rows after the decompression budget is enforced.
    data = object_value(
        json.loads(data_raw.decode(), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    )
    physical = meta.get("physical_tables")
    require(
        isinstance(physical, list)
        and len(physical) == len(set(physical)) == len(data) == 24
        and set(physical) == set(data)
        and set(data) == registered_business_tables(transport) | {"alembic_version"}
        and all(
            isinstance(rows, list) and all(type(row) is dict for row in rows)
            for rows in data.values()
        )
        and meta.get("row_counts") == {name: len(rows) for name, rows in data.items()},
        "Actual original physical24 row denominator differs",
    )
    require(
        meta.get("physical_query")
        == (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        ),
        "Actual original physical table SQL differs",
    )
    observations = list_value(report.get("observations"))
    require(
        isinstance(observations, list) and bool(observations), "Original audit observations missing"
    )
    users = list_value(data.get("users"))
    epochs = list_value(data.get("audit_epochs"))
    require(
        isinstance(users, list)
        and bool(users)
        and all(row.get("is_simulated") is True for row in users)
        and isinstance(epochs, list),
        "Original simulated users/epochs missing",
    )
    expected_pairs = {(row["user_id"], row["id"]) for row in epochs}
    require(
        {row["id"] for row in users} == {row["user_id"] for row in epochs},
        "User audit denominator incomplete",
    )
    type_source = "apps/api/app/domain/audit_chain_types.py"
    require(
        dict(transport.runtime_sources).get(type_source) == sha((ROOT / type_source).read_bytes()),
        "Original audit result contract source drift",
    )
    schema = runpy.run_path(str(ROOT / type_source))["AuditVerification"]
    actual_pairs = set()
    for observation in observations:
        row = object_value(observation)
        pair = row.get("user_id"), row.get("epoch_id")
        require(
            pair not in actual_pairs and pair in expected_pairs,
            "Original audit epoch duplicated/foreign",
        )
        epoch = next(item for item in epochs if (item["user_id"], item["id"]) == pair)
        verification = object_value(row.get("verification"))
        schema.model_validate_json(json.dumps(verification, allow_nan=False))
        require(
            row.get("epoch_number") == epoch.get("epoch_number")
            and row.get("epoch_status") == epoch.get("status")
            and verification.get("status") == "VALID"
            and verification.get("chain_status") == verification.get("reference_status") == "VALID"
            and verification.get("checkpoint_status") in {"VERIFIED", "NOT_REQUESTED"}
            and verification.get("errors") == []
            and verification.get("errors_truncated") is False
            and verification.get("epoch_id") == row.get("epoch_id")
            and verification.get("user_id") == row.get("user_id"),
            "Original audit result/epoch differs",
        )
        actual_pairs.add(pair)
    require(
        actual_pairs == expected_pairs, "Original all-user/all-epoch audit denominator incomplete"
    )
    return DecodedSnapshot(stdout, raw, data_raw, report)
