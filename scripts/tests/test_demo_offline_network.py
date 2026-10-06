"""TOOL_ONLY network originals; never Docker/finance/offline-product evidence."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.demo_container_transport import sha
from scripts.demo_offline_network import build_transition, validate_transition
from scripts.tests.test_demo_container_transport import native as native
from scripts.tests.test_demo_container_transport import packed, raw, service, transport


def network_fixture(native: dict[str, Any]) -> tuple[Any, bytes, bytes, bytes, bytes]:
    owned = transport(native)
    before = packed(native)[2]
    project = owned.project
    network = []
    for kind, identity, attachments in (
        (
            "internal",
            "e",
            {"d" * 64: {"Name": project + "-db-1"}, "b" * 64: {"Name": project + "-web-1"}},
        ),
        ("frontend", "f", {"b" * 64: {"Name": project + "-web-1"}}),
    ):
        network.append(
            {
                "Id": identity * 64,
                "Name": project + "_demo_" + kind,
                "Internal": kind == "internal",
                "Driver": "bridge",
                "Scope": "local",
                "Labels": {
                    "com.docker.compose.project": project,
                    "io.bounded-funds.owner-uuid": owned.owner_uuid,
                    "io.bounded-funds.run-id": owned.owner_run_id,
                    "io.bounded-funds.purpose": "ISOLATED_SIMULATED_DEMO",
                },
                "IPAM": {"Driver": "default"},
                "Options": {},
                "Containers": attachments,
            }
        )
    after = copy.deepcopy(native)
    service(after, "web")["NetworkSettings"]["Networks"].pop(project + "_demo_frontend")
    networks_after = copy.deepcopy(network)
    networks_after[1]["Containers"] = {}
    return owned, before, raw(after["containers"]), raw(network), raw(networks_after)


def record(
    native: dict[str, Any], inputs: tuple[Any, bytes, bytes, bytes, bytes]
) -> tuple[Any, bytes, bytes, bytes]:
    owned, before, after, network_before, network_after = inputs
    prepared = build_transition(owned, before, network_before, native["current"], native["head"])
    stdout, stderr = b"TOOL_ONLY executed command fixture\n", b""
    command = {
        "tool_input_context": "TOOL_ONLY",
        "protocol": "bounded-funds-network-transition-command-v1",
        "owner_uuid": owned.owner_uuid,
        "owner_run_id": owned.owner_run_id,
        "source_digest": owned.source_digest,
        "source_head": owned.source_head,
        "argv": list(prepared.argv),
        "exit_code": 0,
        "stdout_sha256": sha(stdout),
        "stderr_sha256": sha(stderr),
        "containers_before_sha256": sha(before),
        "containers_after_sha256": sha(after),
        "networks_before_sha256": sha(network_before),
        "networks_after_sha256": sha(network_after),
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
    }
    return prepared, raw(command), stdout, stderr


def verify(
    native: dict[str, Any], inputs: tuple[Any, bytes, bytes, bytes, bytes]
) -> dict[str, Any]:
    owned, before, after, network_before, network_after = inputs
    prepared, command, stdout, stderr = record(native, inputs)
    return validate_transition(
        owned,
        prepared,
        before,
        after,
        network_before,
        network_after,
        command,
        stdout,
        stderr,
        native["current"],
        native["head"],
    )


def test_tool_only_disconnect_is_exact_owned_immutable_network_and_web(
    native: dict[str, Any],
) -> None:
    inputs = network_fixture(native)
    prepared, _, _, _ = record(native, inputs)
    assert prepared.argv == ("docker", "network", "disconnect", "f" * 64, "b" * 64)
    result = verify(native, inputs)
    assert result["status"] == "TOPOLOGY_ONLY_EGRESS_UNVERIFIED"
    assert result["offline_accepted"] is False and len(result["uncovered"]) == 4


@pytest.mark.parametrize(
    "case",
    [
        "noninternal",
        "foreign_network_label",
        "foreign_attached_container",
        "mutable_network",
        "source_drift",
        "different_before_bytes",
        "web_not_detached",
        "internal_removed",
        "api_namespace_changed",
        "db_network_changed",
        "web_image_changed",
        "web_config_changed",
        "web_unhealthy",
        "init_failed",
        "internal_flag_changed",
        "foreign_after_attach",
    ],
)
def test_tool_only_network_transition_rejects_mutations(native: dict[str, Any], case: str) -> None:
    owned, before, after, network_before, network_after = network_fixture(native)
    import json

    before_networks, after_networks, after_containers = (
        json.loads(network_before),
        json.loads(network_after),
        json.loads(after),
    )
    if case == "noninternal":
        before_networks[0]["Internal"] = False
    elif case == "foreign_network_label":
        before_networks[0]["Labels"]["io.bounded-funds.owner-uuid"] = "0" * 32
    elif case == "foreign_attached_container":
        before_networks[1]["Containers"]["0" * 64] = {}
    elif case == "mutable_network":
        before_networks[1]["Id"] = "bridge"
    elif case == "source_drift":
        native["current"]["uv.lock"] = "0" * 64
    elif case == "different_before_bytes":
        before += b"\n"
    elif case == "web_not_detached":
        after_containers[1]["NetworkSettings"]["Networks"][owned.project + "_demo_frontend"] = {}
    elif case == "internal_removed":
        after_containers[1]["NetworkSettings"]["Networks"] = {}
    elif case == "api_namespace_changed":
        after_containers[0]["HostConfig"]["NetworkMode"] = "host"
    elif case == "db_network_changed":
        after_containers[2]["NetworkSettings"]["Networks"]["bridge"] = {}
    elif case == "web_image_changed":
        after_containers[1]["Image"] = "sha256:" + "0" * 64
    elif case == "web_config_changed":
        after_containers[1]["Config"]["Env"] = ["TOOL_ONLY=true"]
    elif case == "web_unhealthy":
        after_containers[1]["State"]["Health"]["Status"] = "starting"
    elif case == "init_failed":
        after_containers[3]["State"]["ExitCode"] = 1
    elif case == "internal_flag_changed":
        after_networks[0]["Internal"] = False
    elif case == "foreign_after_attach":
        after_networks[1]["Containers"]["0" * 64] = {}
    else:
        raise AssertionError(case)
    with pytest.raises(ValueError):
        verify(
            native,
            (owned, before, raw(after_containers), raw(before_networks), raw(after_networks)),
        )


@pytest.mark.parametrize(
    "case",
    [
        "argv",
        "exit",
        "false_exit",
        "source",
        "owner",
        "naive_time",
        "backwards_time",
        "stdout_hash",
        "after_hash",
    ],
)
def test_tool_only_transition_requires_original_command_bytes_time_and_hashes(
    native: dict[str, Any], case: str
) -> None:
    import json

    inputs = network_fixture(native)
    owned, before, after, network_before, network_after = inputs
    prepared, command, stdout, stderr = record(native, inputs)
    value = json.loads(command)
    if case == "argv":
        value["argv"][-1] = "bounded-funds-web-1"
    elif case == "exit":
        value["exit_code"] = 1
    elif case == "false_exit":
        value["exit_code"] = False
    elif case == "source":
        value["source_digest"] = "0" * 64
    elif case == "owner":
        value["owner_uuid"] = "0" * 32
    elif case == "naive_time":
        value["started_at"] = "2026-01-01T00:00:00"
    elif case == "backwards_time":
        value["finished_at"] = "2025-01-01T00:00:00+00:00"
    elif case == "stdout_hash":
        stdout += b"changed"
    elif case == "after_hash":
        value["containers_after_sha256"] = "0" * 64
    else:
        raise AssertionError(case)
    with pytest.raises(ValueError):
        validate_transition(
            owned,
            prepared,
            before,
            after,
            network_before,
            network_after,
            raw(value),
            stdout,
            stderr,
            native["current"],
            native["head"],
        )
