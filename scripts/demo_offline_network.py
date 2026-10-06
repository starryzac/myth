"""Pure owned-network transition planning/checking; no Docker or financial execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from scripts.demo_container_transport import (
    OwnedTransport,
    list_value,
    object_value,
    original_json,
    require,
    sha,
    source_digest,
)


@dataclass(frozen=True)
class NetworkTransition:
    argv: tuple[str, ...]
    owner_uuid: str
    owner_run_id: str
    source_digest: str
    api_container_id: str
    db_container_id: str
    web_container_id: str
    internal_network_id: str
    frontend_network_id: str
    containers_before_sha256: str
    networks_before_sha256: str
    operation: str = "DISCONNECT_OWNED_WEB_FRONTEND_ONLY"


def containers(raw: bytes) -> dict[str, dict[str, Any]]:
    original = list_value(original_json(raw))
    require(len(original) == 4, "All four owned container originals required")
    result = {}
    for value in original:
        row = object_value(value)
        service = object_value(object_value(row.get("Config")).get("Labels")).get(
            "com.docker.compose.service"
        )
        require(
            service in {"api", "db", "web", "init"} and service not in result,
            "Foreign/duplicate original container",
        )
        result[cast(str, service)] = row
    return result


def networks(raw: bytes, owned: OwnedTransport) -> dict[str, dict[str, Any]]:
    original = list_value(original_json(raw))
    require(len(original) == 2, "Only exact two owned network originals admitted")
    result = {}
    for value in original:
        row = object_value(value)
        labels = object_value(row.get("Labels"))
        name = row.get("Name")
        require(
            name in {owned.project + "_demo_internal", owned.project + "_demo_frontend"}
            and name not in result
            and row.get("Driver") == "bridge"
            and row.get("Scope") == "local"
            and labels.get("com.docker.compose.project") == owned.project
            and labels.get("io.bounded-funds.owner-uuid") == owned.owner_uuid
            and labels.get("io.bounded-funds.run-id") == owned.owner_run_id
            and labels.get("io.bounded-funds.purpose") == "ISOLATED_SIMULATED_DEMO",
            "Network ownership/name/driver escaped",
        )
        result[cast(str, name)] = row
    return result


def current(owned: OwnedTransport, source: dict[str, str], head: str) -> None:
    require(
        source_digest(source) == owned.source_digest and head == owned.source_head,
        "Actual network transition source is no longer current",
    )


def build_transition(
    owned: OwnedTransport,
    containers_before_raw: bytes,
    networks_before_raw: bytes,
    current_source: dict[str, str],
    current_head: str,
) -> NetworkTransition:
    """Only after validate_owned_transport freshly admitted these exact original containers."""
    current(owned, current_source, current_head)
    require(
        sha(containers_before_raw) == owned.inspect_sha256, "Freshly verified inspect bytes differ"
    )
    before = containers(containers_before_raw)
    network = networks(networks_before_raw, owned)
    api_id, db_id, web_id = (before[name]["Id"] for name in ("api", "db", "web"))
    require(
        api_id == owned.api_container_id and db_id == owned.db_container_id,
        "Immutable original API/DB IDs differ",
    )
    internal = network[owned.project + "_demo_internal"]
    frontend = network[owned.project + "_demo_frontend"]
    require(
        internal.get("Internal") is True and frontend.get("Internal") is False,
        "Actual internal flag/original egress network differs",
    )
    require(
        set(object_value(internal.get("Containers"))) == {db_id, web_id}
        and set(object_value(frontend.get("Containers"))) == {web_id},
        "Owned networks have missing or foreign attached containers",
    )
    # Docker image/container/network IDs are always complete immutable hex IDs.
    import re

    require(
        all(
            isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
            for identity in (api_id, db_id, web_id, internal.get("Id"), frontend.get("Id"))
        ),
        "Full immutable network/container IDs required",
    )
    return NetworkTransition(
        ("docker", "network", "disconnect", frontend["Id"], web_id),
        owned.owner_uuid,
        owned.owner_run_id,
        owned.source_digest,
        api_id,
        db_id,
        web_id,
        internal["Id"],
        frontend["Id"],
        sha(containers_before_raw),
        sha(networks_before_raw),
    )


def aware(value: Any) -> datetime:
    require(isinstance(value, str), "Actual command timestamp missing")
    result = datetime.fromisoformat(value)
    require(
        result.tzinfo is not None and result.utcoffset() is not None,
        "Actual command timestamp naive",
    )
    return result


def validate_transition(
    owned: OwnedTransport,
    transition: NetworkTransition,
    containers_before_raw: bytes,
    containers_after_raw: bytes,
    networks_before_raw: bytes,
    networks_after_raw: bytes,
    command_raw: bytes,
    command_stdout: bytes,
    command_stderr: bytes,
    current_source: dict[str, str],
    current_head: str,
) -> dict[str, Any]:
    """Return topology observation only. Actual egress/browser/relay probes remain required."""
    expected = build_transition(
        owned, containers_before_raw, networks_before_raw, current_source, current_head
    )
    require(expected == transition, "Registered transition argv/source/inspect changed")
    original = object_value(original_json(command_raw))
    require(
        original.get("protocol") == "bounded-funds-network-transition-command-v1"
        and original.get("owner_uuid") == owned.owner_uuid
        and original.get("owner_run_id") == owned.owner_run_id
        and original.get("source_digest") == owned.source_digest
        and original.get("source_head") == current_head
        and original.get("argv") == list(transition.argv)
        and type(original.get("exit_code")) is int
        and original["exit_code"] == 0
        and original.get("stdout_sha256") == sha(command_stdout)
        and original.get("stderr_sha256") == sha(command_stderr)
        and original.get("containers_before_sha256") == sha(containers_before_raw)
        and original.get("containers_after_sha256") == sha(containers_after_raw)
        and original.get("networks_before_sha256") == sha(networks_before_raw)
        and original.get("networks_after_sha256") == sha(networks_after_raw)
        and aware(original.get("finished_at")) >= aware(original.get("started_at")),
        "Actual disconnect command/run/source/raw byte binding differs",
    )
    before, after = containers(containers_before_raw), containers(containers_after_raw)
    previous, following = networks(networks_before_raw, owned), networks(networks_after_raw, owned)
    for service in ("api", "db", "web", "init"):
        old, new = before[service], after[service]
        require(
            all(
                old.get(field) == new.get(field)
                for field in ("Id", "Image", "Name", "Config", "HostConfig", "Mounts")
            ),
            "Network-only transition replaced container/image/source/config/mount",
        )
        if service != "web":
            require(
                old.get("NetworkSettings") == new.get("NetworkSettings"),
                "Non-Web original network namespace changed",
            )
        else:
            old_networks = object_value(object_value(old.get("NetworkSettings")).get("Networks"))
            new_networks = object_value(object_value(new.get("NetworkSettings")).get("Networks"))
            require(
                set(old_networks)
                == {owned.project + "_demo_internal", owned.project + "_demo_frontend"}
                and set(new_networks) == {owned.project + "_demo_internal"}
                and new_networks[owned.project + "_demo_internal"]
                == old_networks[owned.project + "_demo_internal"],
                "Web egress remains attached or internal endpoint changed",
            )
        state = object_value(new.get("State"))
        require(
            state.get("Paused") is False
            and state.get("Restarting") is False
            and state.get("OOMKilled") is False
            and state.get("Dead") is False,
            "Container unstable after disconnect",
        )
        if service == "init":
            require(
                state.get("Running") is False
                and state.get("Status") == "exited"
                and type(state.get("ExitCode")) is int
                and state["ExitCode"] == 0,
                "Initializer history changed",
            )
        else:
            require(
                state.get("Running") is True
                and state.get("Status") == "running"
                and object_value(state.get("Health")).get("Status") == "healthy",
                "Actual service unhealthy after disconnect",
            )
    for name in previous:
        old, new = previous[name], following[name]
        require(
            {field: value for field, value in old.items() if field != "Containers"}
            == {field: value for field, value in new.items() if field != "Containers"},
            "Original network definition/ownership changed",
        )
        if name.endswith("_demo_internal"):
            require(
                new.get("Internal") is True and old.get("Containers") == new.get("Containers"),
                "Owned internal attachments changed",
            )
        else:
            require(
                new.get("Internal") is False and new.get("Containers") == {},
                "Owned external network still has attachments",
            )
    return {
        "protocol": "bounded-funds-offline-network-observation-v1",
        "status": "TOPOLOGY_ONLY_EGRESS_UNVERIFIED",
        "owner_uuid": owned.owner_uuid,
        "api_container_id": transition.api_container_id,
        "db_container_id": transition.db_container_id,
        "web_container_id": transition.web_container_id,
        "source_digest": owned.source_digest,
        "command_sha256": sha(command_raw),
        "containers_after_sha256": sha(containers_after_raw),
        "networks_after_sha256": sha(networks_after_raw),
        "offline_accepted": False,
        "uncovered": [
            "actual external IP and DNS probes for DB/API/Web namespaces",
            "actual loopback relay source/argv/request/response and health",
            "browser all requests localhost plus original HTTP receipts",
            "three golden chains and complete audit/financial/history proof",
        ],
    }
