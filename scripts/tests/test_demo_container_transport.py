"""TOOL_ONLY fixtures: pure transport checks, never Docker/finance/deployment evidence."""

from __future__ import annotations

import ast
import base64
import copy
import gzip
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "tool_only_demo_container_transport", ROOT / "scripts/demo_container_transport.py"
)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)


def raw(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


@pytest.fixture
def native() -> dict[str, Any]:
    owner_uuid = "1" * 32
    project = "bf-demo-" + owner_uuid
    database = "bf_test_" + owner_uuid
    current = {
        name: TOOL.sha((ROOT / name).read_bytes())
        for name in (
            "pyproject.toml",
            "uv.lock",
            "alembic.ini",
            "scripts/scenario_runner_rpc.py",
            "scripts/seed_demo.py",
            "deploy/initialize_demo.py",
            "apps/api/app/services/scenario_types.py",
            "apps/api/app/domain/audit_chain_types.py",
            "apps/api/app/db/models.py",
            "pnpm-lock.yaml",
        )
    }
    digest = TOOL.source_digest(current)
    owner_run = "TOOL_ONLY_owner"
    head = "a" * 40
    owner = {
        "tool_input_context": "TOOL_ONLY",
        "owner_uuid": owner_uuid,
        "run_id": owner_run,
        "project": project,
        "database": database,
        "compose_file": "deploy/compose.demo.yaml",
        "formal_database_touched": False,
        "ports": {"api": 18049, "web": 15181},
        "source_before": current.copy(),
        "source_digest": digest,
        "source_head": head,
        "images": {"BF_DEMO_POSTGRES_IMAGE": {"inspect": {"Id": "sha256:" + "d" * 64}}},
    }
    image_inputs = {}
    for service, image_id in (("api", "b"), ("web", "c")):
        lock = "uv.lock" if service == "api" else "pnpm-lock.yaml"
        label = (
            "io.bounded-funds.uv-lock-sha256"
            if service == "api"
            else "io.bounded-funds.pnpm-lock-sha256"
        )
        image_inputs[service] = [
            {
                "Id": "sha256:" + image_id * 64,
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {
                    "Labels": {
                        "io.bounded-funds.purpose": TOOL.PURPOSE,
                        "io.bounded-funds.source-digest": digest,
                        "org.opencontainers.image.revision": head,
                        label: current[lock],
                    }
                },
            }
        ]
    containers = []
    db_id = "d" * 64
    for service, identity in (("api", "a"), ("web", "b"), ("db", "d"), ("init", "c")):
        image_id = "d" if service == "db" else "c" if service == "web" else "b"
        labels = {
            "com.docker.compose.service": service,
            "com.docker.compose.project": project,
            "io.bounded-funds.owner-uuid": owner_uuid,
            "io.bounded-funds.run-id": owner_run,
            "io.bounded-funds.purpose": TOOL.PURPOSE,
        }
        if service != "db":
            labels.update(
                {
                    "io.bounded-funds.source-digest": digest,
                    "org.opencontainers.image.revision": head,
                }
            )
        networks: dict[str, Any] = (
            {} if service in {"api", "init"} else {project + "_demo_internal": {}}
        )
        if service == "web":
            networks[project + "_demo_frontend"] = {}
        containers.append(
            {
                "Id": identity * 64,
                "Image": "sha256:" + image_id * 64,
                "Name": f"/{project}-{service}-1",
                "Config": {
                    "Labels": labels,
                    "Cmd": ["postgres", "-p", "54329"] if service == "db" else ["TOOL_ONLY"],
                    "Env": [
                        "SIMULATION_MODE=true",
                        "LLM_ENABLED=false",
                        f"DATABASE_URL=postgresql+psycopg://bf_demo:TOOL_ONLY@127.0.0.1:54329/{database}",
                    ],
                },
                "State": {
                    "Paused": False,
                    "Restarting": False,
                    "OOMKilled": False,
                    "Dead": False,
                    "Running": service != "init",
                    "Status": "exited" if service == "init" else "running",
                    "ExitCode": 0,
                    "Health": {"Status": "healthy"},
                },
                "HostConfig": {
                    "Privileged": False,
                    "Binds": None,
                    "NetworkMode": "container:" + db_id
                    if service in {"api", "init"}
                    else project + ("_demo_frontend" if service == "web" else "_demo_internal"),
                    "ReadonlyRootfs": service in {"api", "init"},
                    "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18049"}]}
                    if service == "db"
                    else {},
                },
                "Mounts": [
                    {
                        "Type": "volume",
                        "Name": project + "_demo_pgdata",
                        "Destination": "/var/lib/postgresql/data",
                    }
                ]
                if service == "db"
                else [],
                "NetworkSettings": {
                    "Networks": networks,
                    "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "15181"}]}
                    if service == "web"
                    else {},
                },
            }
        )
    start = {
        "tool_input_context": "TOOL_ONLY",
        "protocol": "bounded-funds-demo-deployment-v1",
        "action": "start",
        "status": "PASSED",
        "run_id": "TOOL_ONLY_start",
        "owner_uuid": owner_uuid,
        "owner_run_id": owner_run,
        "project": project,
        "database": database,
        "formal_database_touched": False,
        "registered_source_current": True,
        "source_before": current.copy(),
        "source_after": current.copy(),
        "commands": [
            {"label": label, "exit_code": 0}
            for label in ("start", "after-inspect", "api-image", "web-image")
        ],
    }
    return {
        "owner": owner,
        "start": start,
        "containers": containers,
        "images": image_inputs,
        "current": current,
        "head": head,
    }


def packed(native: dict[str, Any]) -> tuple[bytes, bytes, bytes, dict[str, bytes]]:
    owner_raw = raw(native["owner"])
    inspection_raw = raw(native["containers"])
    images = {name: raw(values) for name, values in native["images"].items()}
    start = copy.deepcopy(native["start"])
    start["registration_sha256"] = TOOL.sha(owner_raw)
    start["containers_after"] = [
        {"id": item["Id"], "image_id": item["Image"]} for item in native["containers"]
    ]
    start["artifact_hashes"] = {
        "after-inspect.log": TOOL.sha(inspection_raw),
        **{name + "-image.log": TOOL.sha(value) for name, value in images.items()},
    }
    return owner_raw, raw(start), inspection_raw, images


def transport(native: dict[str, Any]) -> Any:
    return TOOL.validate_owned_transport(*packed(native), native["current"], native["head"])


def service(native: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        item
        for item in native["containers"]
        if item["Config"]["Labels"]["com.docker.compose.service"] == name
    )


def test_tool_only_native_ownership_returns_immutable_ids_and_network_boundary(
    native: dict[str, Any],
) -> None:
    actual = transport(native)
    assert actual.api_container_id == "a" * 64 and actual.db_container_id == "d" * 64
    assert actual.registration_sha256 == TOOL.sha(packed(native)[0])
    assert "WEB_EGRESS_UNVERIFIED" in actual.network_boundary
    with pytest.raises(AttributeError):
        actual.api_container_id = "different"


def test_tool_only_docker_owned_named_volume_bind_serialization_is_supported(
    native: dict[str, Any],
) -> None:
    db = service(native, "db")
    db["HostConfig"]["Binds"] = [
        native["owner"]["project"] + "_demo_pgdata:/var/lib/postgresql/data:rw"
    ]
    assert transport(native).db_container_id == db["Id"]
    db["Mounts"][0]["Type"] = "bind"
    with pytest.raises(ValueError):
        transport(native)


def test_tool_only_native_redacted_password_preserves_original_endpoint_validation(
    native: dict[str, Any],
) -> None:
    for name in ("api", "init"):
        container = service(native, name)
        container["Config"]["Env"][2] = container["Config"]["Env"][2].replace(
            "TOOL_ONLY@", "[REDACTED]@"
        )
    assert transport(native).database == native["owner"]["database"]


@pytest.mark.parametrize(
    "case",
    [
        "formal_name",
        "foreign_uuid",
        "wrong_project",
        "wrong_head",
        "wrong_digest",
        "current_source_drift",
        "recorded_source_drift",
        "old_image_current_flag",
        "failed_start",
        "true_not_exit_zero",
        "missing_actual_inspect",
        "foreign_purpose",
        "foreign_owner",
        "foreign_run",
        "foreign_name",
        "mutable_container_name",
        "unstable",
        "unhealthy",
        "init_not_finished",
        "init_failed",
        "foreign_image",
        "image_source",
        "lock_source",
        "windows_image",
        "wrong_architecture",
        "privileged",
        "foreign_bind",
        "wrong_db_image",
        "wrong_postgres_port",
        "postgres_host_publish",
        "db_foreign_network",
        "foreign_volume",
        "api_foreign_namespace",
        "api_writable_root",
        "api_mount",
        "api_extra_network",
        "false_simulation",
        "llm_enabled",
        "formal_dsn",
        "remote_dsn",
        "wrong_dsn_port",
        "wrong_dsn_user",
        "duplicated_env",
        "web_public_bind",
        "web_foreign_network",
        "formal_host_port",
        "missing_rpc_binding",
    ],
)
def test_tool_only_rejects_scope_and_native_identity_mutations(
    native: dict[str, Any], case: str
) -> None:
    owner, start = native["owner"], native["start"]
    api, db, init, web = (service(native, name) for name in ("api", "db", "init", "web"))
    if case == "formal_name":
        owner["database"] = "bounded_funds"
    elif case == "foreign_uuid":
        owner["owner_uuid"] = "not-uuid"
    elif case == "wrong_project":
        owner["project"] = "bounded-funds"
    elif case == "wrong_head":
        native["head"] = "e" * 40
    elif case == "wrong_digest":
        owner["source_digest"] = "e" * 64
    elif case == "current_source_drift":
        native["current"]["uv.lock"] = "e" * 64
    elif case == "recorded_source_drift":
        start["source_after"]["uv.lock"] = "e" * 64
    elif case == "old_image_current_flag":
        start["registered_source_current"] = False
    elif case == "failed_start":
        start["commands"][0]["exit_code"] = 1
    elif case == "true_not_exit_zero":
        start["commands"][0]["exit_code"] = False
    elif case == "missing_actual_inspect":
        start["commands"].pop(1)
    elif case == "foreign_purpose":
        api["Config"]["Labels"]["io.bounded-funds.purpose"] = "FORMAL"
    elif case == "foreign_owner":
        api["Config"]["Labels"]["io.bounded-funds.owner-uuid"] = "e" * 32
    elif case == "foreign_run":
        api["Config"]["Labels"]["io.bounded-funds.run-id"] = "another"
    elif case == "foreign_name":
        api["Name"] = "/bounded-funds-api-1"
    elif case == "mutable_container_name":
        api["Id"] = api["Name"]
    elif case == "unstable":
        api["State"]["Restarting"] = True
    elif case == "unhealthy":
        api["State"]["Health"]["Status"] = "starting"
    elif case == "init_not_finished":
        init["State"]["Running"] = True
    elif case == "init_failed":
        init["State"]["ExitCode"] = 1
    elif case == "foreign_image":
        api["Image"] = "sha256:" + "e" * 64
    elif case == "image_source":
        native["images"]["api"][0]["Config"]["Labels"]["io.bounded-funds.source-digest"] = "e" * 64
    elif case == "lock_source":
        native["images"]["api"][0]["Config"]["Labels"]["io.bounded-funds.uv-lock-sha256"] = "e" * 64
    elif case == "windows_image":
        native["images"]["api"][0]["Os"] = "windows"
    elif case == "wrong_architecture":
        native["images"]["api"][0]["Architecture"] = "arm64"
    elif case == "privileged":
        api["HostConfig"]["Privileged"] = True
    elif case == "foreign_bind":
        db["HostConfig"]["Binds"] = ["formal:/var/lib/postgresql/data"]
    elif case == "wrong_db_image":
        db["Image"] = "sha256:" + "e" * 64
    elif case == "wrong_postgres_port":
        db["Config"]["Cmd"] = ["postgres", "-p", "5432"]
    elif case == "postgres_host_publish":
        db["HostConfig"]["PortBindings"]["54329/tcp"] = [
            {"HostIp": "127.0.0.1", "HostPort": "54329"}
        ]
    elif case == "db_foreign_network":
        db["NetworkSettings"]["Networks"]["bridge"] = {}
    elif case == "foreign_volume":
        db["Mounts"][0]["Name"] = "bounded-funds_pgdata"
    elif case == "api_foreign_namespace":
        api["HostConfig"]["NetworkMode"] = "host"
    elif case == "api_writable_root":
        api["HostConfig"]["ReadonlyRootfs"] = False
    elif case == "api_mount":
        api["Mounts"] = [{"Type": "bind", "Source": "/formal"}]
    elif case == "api_extra_network":
        api["NetworkSettings"]["Networks"] = {"bridge": {}}
    elif case == "false_simulation":
        api["Config"]["Env"][0] = "SIMULATION_MODE=false"
    elif case == "llm_enabled":
        api["Config"]["Env"][1] = "LLM_ENABLED=true"
    elif case in {"formal_dsn", "remote_dsn", "wrong_dsn_port", "wrong_dsn_user"}:
        replacement = {
            "formal_dsn": (owner["database"], "bounded_funds"),
            "remote_dsn": ("127.0.0.1", "remote"),
            "wrong_dsn_port": (":54329/", ":5432/"),
            "wrong_dsn_user": ("bf_demo:", "postgres:"),
        }[case]
        api["Config"]["Env"][2] = api["Config"]["Env"][2].replace(*replacement)
    elif case == "duplicated_env":
        api["Config"]["Env"].append(api["Config"]["Env"][0])
    elif case == "web_public_bind":
        web["NetworkSettings"]["Ports"]["80/tcp"][0]["HostIp"] = "0.0.0.0"
    elif case == "web_foreign_network":
        web["NetworkSettings"]["Networks"]["bridge"] = {}
    elif case == "formal_host_port":
        owner["ports"]["api"] = 54329
    elif case == "missing_rpc_binding":
        owner["source_before"].pop("scripts/scenario_runner_rpc.py")
    else:
        raise AssertionError(case)
    with pytest.raises(ValueError):
        transport(native)


def test_tool_only_raw_hash_and_replaced_id_are_rejected(native: dict[str, Any]) -> None:
    owner, start, inspection, images = packed(native)
    with pytest.raises(ValueError, match="inspect bytes changed"):
        TOOL.validate_owned_transport(
            owner, start, inspection + b"\n", images, native["current"], native["head"]
        )
    changed = json.loads(inspection)
    changed[0]["Id"] = "e" * 64
    with pytest.raises(ValueError, match="replaced"):
        TOOL.validate_owned_transport(
            owner,
            start,
            raw(changed),
            images,
            native["current"],
            native["head"],
            require_started_inspect=False,
        )


@pytest.mark.parametrize("value", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_tool_only_original_json_rejects_ambiguous_numbers_keys(value: bytes) -> None:
    with pytest.raises(ValueError):
        TOOL.original_json(value)


@pytest.mark.parametrize(
    "path",
    ["../formal", "/formal", "C:/formal", "app\\module.py", ".env", "a/.env.secret", "a/.venv/x"],
)
def test_tool_only_source_paths_are_canonical(path: str) -> None:
    with pytest.raises(ValueError):
        TOOL.source_digest({path: "a" * 64})


def rpc() -> dict[str, Any]:
    return {
        "protocol": "bounded-funds-scenario-v1",
        "scenario_id": "TOOL_ONLY_rpc",
        "purpose": "DEVELOPMENT",
        "operation": "snapshot",
        "expected_epoch_id": "11111111-1111-1111-1111-111111111111",
    }


def test_tool_only_rpc_is_exact_original_entrypoint_stdin_without_execution(
    native: dict[str, Any],
) -> None:
    actual = transport(native)
    command = TOOL.build_rpc_command(actual, rpc())
    assert command.argv == (
        "docker",
        "exec",
        "-i",
        "--workdir",
        "/workspace",
        "a" * 64,
        "/opt/bf-venv/bin/python",
        "/workspace/scripts/scenario_runner_rpc.py",
    )
    assert json.loads(command.stdin) == rpc() and command.stdin.endswith(b"\n")
    assert command.kind == "ORIGINAL_PRIVATE_SCENARIO_RPC_NOT_EXECUTED"
    assert command.stdin_sha256 == TOOL.sha(command.stdin)


@pytest.mark.parametrize(
    "field,value",
    [
        ("at", "2026-01-01T00:00:00Z"),
        ("amount_cents", 1),
        ("fault", "DROP_BANK_RESPONSE"),
        ("bank_result", "SETTLED"),
        ("purpose", "MVP_FROZEN"),
        ("operation", "reset"),
        ("policy_id", "11111111-1111-1111-1111-111111111111"),
    ],
)
def test_tool_only_rpc_original_schema_refuses_new_authority_or_fields(
    native: dict[str, Any], field: str, value: Any
) -> None:
    body = rpc()
    body[field] = value
    with pytest.raises(ValueError):
        TOOL.build_rpc_command(transport(native), body)


def test_tool_only_snapshot_stdin_compiles_and_has_original_readonly_guards(
    native: dict[str, Any],
) -> None:
    command = TOOL.build_snapshot_command(transport(native), "TOOL_ONLY_snapshot")
    compile(command.stdin, "TOOL_ONLY_generated_stdin", "exec")
    assert command.argv[-1] == "-" and command.argv[5] == "a" * 64
    helper = (ROOT / "scripts/demo_container_snapshot.py").read_text()
    assert helper.index("require_test_database(database)") < helper.index(
        "engine = create_database_engine("
    )
    assert "SET TRANSACTION READ ONLY" in helper and "REPEATABLE READ" in helper
    assert (
        "Base.metadata.sorted_tables" in helper
        and "SELECT version_num FROM alembic_version" in helper
    )
    assert "for user in users:" in helper and "for epoch in epochs:" in helper
    assert "verify_audit_chain(session, user.id, epoch.id)" in helper
    assert "before == after" in helper
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(ast.parse(helper))
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert not calls & {"seed", "reset", "rpc", "add", "delete", "flush", "create_all", "drop_all"}


def frame(native: dict[str, Any], command: Any) -> dict[str, Any]:
    actual = transport(native)
    helper = (ROOT / "scripts/demo_container_snapshot.py").read_bytes()
    config = json.loads(
        cast(
            str,
            next(
                item.value
                for item in ast.walk(ast.parse(command.stdin[len(helper) :].decode()))
                if isinstance(item, ast.Constant)
            ),
        )
    )
    user_id, epoch_id = (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
    data: dict[str, list[dict[str, Any]]] = {
        name: [] for name in TOOL.registered_business_tables(actual)
    }
    data.update(
        {
            "users": [{"id": user_id, "is_simulated": True}],
            "audit_epochs": [
                {"id": epoch_id, "user_id": user_id, "epoch_number": 1, "status": "OPEN"}
            ],
            "alembic_version": [{"version_num": "TOOL_ONLY"}],
        }
    )
    data_raw = raw(data)
    compressed = gzip.compress(data_raw, mtime=0)
    return {
        "tool_input_context": "TOOL_ONLY",
        "protocol": "bounded-funds-container-snapshot-v1",
        "status": "PASSED",
        "run_id": config["run_id"],
        "owner_run_id": actual.owner_run_id,
        "owner_uuid": actual.owner_uuid,
        "database": actual.database,
        "api_container_id": actual.api_container_id,
        "source_head": actual.source_head,
        "source_digest": actual.source_digest,
        "snapshot_helper_sha256": TOOL.sha(helper),
        "runtime_source_before": dict(actual.runtime_sources),
        "runtime_source_after": dict(actual.runtime_sources),
        "actual_transaction": {
            "isolation_query": "SHOW transaction_isolation",
            "isolation": "repeatable read",
            "readonly_query": "SHOW transaction_read_only",
            "readonly": "on",
            "identity_query": "SELECT current_database() AS database, "
            "inet_server_addr()::text AS server_address, "
            "inet_server_port() AS server_port, current_user AS database_user",
            "identity_rows": [
                {
                    "database": actual.database,
                    "server_address": "127.0.0.1/32",
                    "server_port": 54329,
                    "database_user": "bf_demo",
                }
            ],
        },
        "snapshot_gzip_base64": base64.b64encode(compressed).decode(),
        "snapshot": {
            "sha256": TOOL.sha(compressed),
            "data_sha256": TOOL.sha(data_raw),
            "bytes": len(data_raw),
            "compressed_bytes": len(compressed),
            "physical_tables": sorted(data),
            "physical_query": "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name",
            "row_counts": {name: len(rows) for name, rows in data.items()},
        },
        "snapshot_after_data_sha256": TOOL.sha(data_raw),
        "observations": [
            {
                "user_id": user_id,
                "epoch_id": epoch_id,
                "epoch_number": 1,
                "epoch_status": "OPEN",
                "verification": {
                    "schema_version": "audit-verification-v1",
                    "simulation": True,
                    "user_id": user_id,
                    "epoch_id": epoch_id,
                    "status": "VALID",
                    "chain_status": "VALID",
                    "reference_status": "VALID",
                    "checkpoint_status": "NOT_REQUESTED",
                    "actual_count": 0,
                    "expected_count": 0,
                    "actual_tail_id": None,
                    "actual_tail_hash": None,
                    "expected_tail_id": None,
                    "expected_tail_hash": None,
                    "verified_through_sequence": 0,
                    "errors": [],
                    "warnings": [],
                    "errors_truncated": False,
                },
            }
        ],
    }


def test_tool_only_snapshot_decoder_preserves_raw_bytes_and_all_epoch_denominator(
    native: dict[str, Any],
) -> None:
    actual = transport(native)
    command = TOOL.build_snapshot_command(actual, "TOOL_ONLY_snapshot")
    original = raw(frame(native, command)) + b"\n"
    decoded = TOOL.decode_snapshot_output(actual, command, original)
    assert decoded.original_stdout == original
    assert gzip.decompress(decoded.snapshot_gzip) == decoded.snapshot_data
    assert len(json.loads(decoded.snapshot_data)) == 24


@pytest.mark.parametrize(
    "case",
    [
        "not_passed",
        "wrong_run",
        "wrong_source",
        "wrong_helper",
        "writable_transaction",
        "wrong_sql_database",
        "wrong_sql_port",
        "hash_changed",
        "before_after_changed",
        "row_count_changed",
        "missing_epoch",
        "duplicate_epoch",
        "foreign_epoch",
        "partial_verification",
        "tampered_reference",
    ],
)
def test_tool_only_snapshot_decoder_rejects_native_proof_and_denominator_mutations(
    native: dict[str, Any], case: str
) -> None:
    actual = transport(native)
    command = TOOL.build_snapshot_command(actual, "TOOL_ONLY_snapshot")
    value = frame(native, command)
    if case == "not_passed":
        value["status"] = "FAILED"
    elif case == "wrong_run":
        value["run_id"] = "another"
    elif case == "wrong_source":
        value["source_digest"] = "e" * 64
    elif case == "wrong_helper":
        value["snapshot_helper_sha256"] = "e" * 64
    elif case == "writable_transaction":
        value["actual_transaction"]["readonly"] = "off"
    elif case == "wrong_sql_database":
        value["actual_transaction"]["identity_rows"][0]["database"] = "bounded_funds"
    elif case == "wrong_sql_port":
        value["actual_transaction"]["identity_rows"][0]["server_port"] = 5432
    elif case == "hash_changed":
        value["snapshot"]["sha256"] = "e" * 64
    elif case == "before_after_changed":
        value["snapshot_after_data_sha256"] = "e" * 64
    elif case == "row_count_changed":
        value["snapshot"]["row_counts"]["users"] = 99
    elif case == "missing_epoch":
        value["observations"] = []
    elif case == "duplicate_epoch":
        value["observations"].append(copy.deepcopy(value["observations"][0]))
    elif case == "foreign_epoch":
        value["observations"][0]["epoch_id"] = "33333333-3333-3333-3333-333333333333"
    elif case == "partial_verification":
        value["observations"][0]["verification"] = {"status": "VALID"}
    elif case == "tampered_reference":
        value["observations"][0]["verification"]["reference_status"] = "TAMPERED"
    else:
        raise AssertionError(case)
    with pytest.raises(ValueError):
        TOOL.decode_snapshot_output(actual, command, raw(value))
