"""TOOL_ONLY actual-shaped counter fixtures; no Docker/finance/offline acceptance."""

from __future__ import annotations

import copy
import json
import subprocess
from typing import Any
from uuid import uuid4

import pytest

from scripts import w1_offline_reobserve as cli
from scripts.demo_offline_endpoint_accounting import validate_transition_accounting
from scripts.demo_offline_network import validate_transition
from scripts.tests.test_demo_container_transport import native as native
from scripts.tests.test_demo_container_transport import raw
from scripts.tests.test_demo_offline_network import network_fixture, record


def accounting(native: dict[str, Any]) -> tuple[Any, bytes, bytes, bytes, bytes]:
    owned, before, after, nb, na = network_fixture(native)
    old, new = json.loads(nb), json.loads(na)
    for networks in (old, new):
        networks[1]["IPAM"]["Config"] = [{"Subnet": "172.23.0.0/16"}]
    old[1]["Containers"]["b" * 64]["IPv4Address"] = "172.23.0.2/16"
    old[1]["Status"] = {
        "IPAM": {"Subnets": {"172.23.0.0/16": {"IPsInUse": 4, "DynamicIPsAvailable": 65532}}}
    }
    new[1]["Status"] = {
        "IPAM": {"Subnets": {"172.23.0.0/16": {"IPsInUse": 3, "DynamicIPsAvailable": 65533}}}
    }
    return owned, before, after, raw(old), raw(new)


def verify(
    native: dict[str, Any], inputs: tuple[Any, bytes, bytes, bytes, bytes]
) -> dict[str, Any]:
    owned, before, after, nb, na = inputs
    transition, command, stdout, stderr = record(native, inputs)
    return validate_transition_accounting(
        owned,
        transition,
        before,
        after,
        nb,
        na,
        command,
        stdout,
        stderr,
        native["current"],
        native["head"],
    )


def test_tool_only_exact_endpoint_accounting_preserves_original_failure(
    native: dict[str, Any],
) -> None:
    inputs = accounting(native)
    owned, before, after, nb, na = inputs
    prepared, command, stdout, stderr = record(native, inputs)
    with pytest.raises(ValueError, match="Original network definition/ownership changed"):
        validate_transition(
            owned,
            prepared,
            before,
            after,
            nb,
            na,
            command,
            stdout,
            stderr,
            native["current"],
            native["head"],
        )
    result = verify(native, inputs)
    assert (
        result["status"] == "TOPOLOGY_ONLY_EGRESS_UNVERIFIED"
        and result["offline_accepted"] is False
    )
    change = result["actual_accounting_adjustments"][0]
    assert change["before"] == {"IPsInUse": 4, "DynamicIPsAvailable": 65532}
    assert change["after"] == {"IPsInUse": 3, "DynamicIPsAvailable": 65533}
    assert (
        result["original_guard_rejection_retained"]
        == "Original network definition/ownership changed"
    )
    assert inputs == (owned, before, after, nb, na)


@pytest.mark.parametrize(
    "case",
    [
        "used_unchanged",
        "available_unchanged",
        "wrong_delta",
        "wrong_denominator",
        "float",
        "boolean",
        "negative",
        "extra_counter",
        "subnet_added",
        "wrong_subnet",
        "foreign_ip",
        "no_configuration",
        "unknown_status",
        "unknown_network",
        "label",
        "image",
        "namespace",
        "internal_status",
        "internal_attachment",
        "frontend_attached",
    ],
)
def test_tool_only_counter_revision_rejects_all_other_changes(
    native: dict[str, Any], case: str
) -> None:
    owned, before, after, nb, na = accounting(native)
    old, new, post = json.loads(nb), json.loads(na), json.loads(after)
    counts = new[1]["Status"]["IPAM"]["Subnets"]["172.23.0.0/16"]
    if case == "used_unchanged":
        counts["IPsInUse"] = 4
    elif case == "available_unchanged":
        counts["DynamicIPsAvailable"] = 65532
    elif case == "wrong_delta":
        counts.update(IPsInUse=2, DynamicIPsAvailable=65534)
    elif case == "wrong_denominator":
        old[1]["Status"]["IPAM"]["Subnets"]["172.23.0.0/16"]["DynamicIPsAvailable"] = 65531
        counts["DynamicIPsAvailable"] = 65532
    elif case == "float":
        counts["IPsInUse"] = 3.0
    elif case == "boolean":
        counts["IPsInUse"] = True
    elif case == "negative":
        counts["IPsInUse"] = -1
    elif case == "extra_counter":
        counts["unknown"] = 1
    elif case == "subnet_added":
        new[1]["Status"]["IPAM"]["Subnets"]["172.24.0.0/16"] = {}
    elif case == "wrong_subnet":
        new[1]["Status"]["IPAM"]["Subnets"] = {"172.24.0.0/16": counts}
    elif case == "foreign_ip":
        old[1]["Containers"]["b" * 64]["IPv4Address"] = "172.25.0.2/16"
    elif case == "no_configuration":
        old[1]["IPAM"]["Config"] = []
        new[1]["IPAM"]["Config"] = []
    elif case == "unknown_status":
        new[1]["Status"]["other"] = 1
    elif case == "unknown_network":
        new[1]["Options"]["new"] = "1"
    elif case == "label":
        new[1]["Labels"]["io.bounded-funds.owner-uuid"] = "f" * 32
    elif case == "image":
        post[0]["Image"] = "sha256:" + "f" * 64
    elif case == "namespace":
        post[0]["HostConfig"]["NetworkMode"] = "host"
    elif case == "internal_status":
        new[0]["Status"] = {"unknown": 1}
    elif case == "internal_attachment":
        new[0]["Containers"].pop("d" * 64)
    elif case == "frontend_attached":
        new[1]["Containers"] = copy.deepcopy(old[1]["Containers"])
    with pytest.raises(ValueError):
        verify(native, (owned, before, raw(post), raw(old), raw(new)))


def test_tool_only_original_command_byte_hash_failure_not_reclassified(
    native: dict[str, Any],
) -> None:
    inputs = accounting(native)
    owned, before, after, nb, na = inputs
    prepared, command, stdout, stderr = record(native, inputs)
    with pytest.raises(ValueError):
        validate_transition_accounting(
            owned,
            prepared,
            before,
            after,
            nb,
            na,
            command,
            stdout + b"changed",
            stderr,
            native["current"],
            native["head"],
        )


@pytest.mark.parametrize("action", ["observe", "probe"])
def test_tool_only_reobserve_default_never_reads_inputs_or_connects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No connection allowed")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert (
        cli.main(
            [
                "--action",
                action,
                "--registration",
                "missing",
                "--start",
                "missing",
                "--prior-isolation",
                "missing",
                "--output",
                "missing",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PREPARED_NOT_EXECUTED" and result["connection_made"] is False


def test_tool_only_prior_raw_hash_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    root = cli.ROOT / ".runtime/tool-tests/offline-accounting" / uuid4().hex
    monkeypatch.setattr(cli, "ROOT", root)
    path = root / ".runtime/w1-offline-boundary-private/TOOL_ONLY/original"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"TOOL_ONLY")
    prior = {
        "artifacts": [
            {
                "label": "raw",
                "private_original_path": path.relative_to(root).as_posix(),
                "private_original_sha256": "0" * 64,
                "private_original_bytes": 9,
            }
        ]
    }
    with pytest.raises(ValueError):
        cli.prior_capture(prior, "raw")
    with pytest.raises(ValueError):
        cli.prior_capture(prior, "missing")
