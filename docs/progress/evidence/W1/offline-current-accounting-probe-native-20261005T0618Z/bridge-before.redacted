"""Pure Docker endpoint accounting revision; original raw evidence is never transformed."""

from __future__ import annotations

import copy
import ipaddress
import json
from pathlib import Path
from typing import Any

from scripts.demo_container_transport import OwnedTransport, object_value, require, sha
from scripts.demo_offline_network import NetworkTransition, networks, validate_transition

ROOT = Path(__file__).resolve().parents[1]
METHOD = "W1_DOCKER_NETWORK_STATUS_ENDPOINT_ACCOUNTING_V1"
FROZEN = {
    "scripts/demo_container_transport.py": (
        "7ba9223b5d50d022b794633748ba2bfc678353e9ac05645271df09929ea8dcd5"
    ),
    "scripts/demo_offline_network.py": (
        "b0eb689526962cea76765b885decdfbb0a1e96cbb58598b75e0b660dcfa814f2"
    ),
}


def typed(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def validate_transition_accounting(
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
    """Only the disconnected Web's real frontend IPv4 allocation may be released."""
    require(
        all(sha((ROOT / name).read_bytes()) == checksum for name, checksum in FROZEN.items()),
        "Frozen original transition dependencies changed",
    )
    original_failure = None
    try:
        result = validate_transition(
            owned,
            transition,
            containers_before_raw,
            containers_after_raw,
            networks_before_raw,
            networks_after_raw,
            command_raw,
            command_stdout,
            command_stderr,
            current_source,
            current_head,
        )
    except ValueError as error:
        # This original guard follows all command/hash/source/namespace/container
        # checks. Every complete network field is independently rechecked below.
        require(
            str(error) == "Original network definition/ownership changed",
            "Original transition rejected a different safety gate",
        )
        original_failure = str(error)
        result = {}
    previous, following = networks(networks_before_raw, owned), networks(networks_after_raw, owned)
    adjustments = []
    for name, before in previous.items():
        after = following[name]
        if name.endswith("_demo_internal"):
            require(
                typed(before) == typed(after) and after.get("Internal") is True,
                "Internal definition/status/attachments changed",
            )
            continue
        require(
            after.get("Internal") is False and after.get("Containers") == {},
            "Actual disconnected frontend still has attachments",
        )
        compare_after = copy.deepcopy(after)
        require(
            before.get("Status") is None
            or typed(before.get("Status")) != typed(after.get("Status")),
            "Native endpoint status present but release counters unchanged",
        )
        if typed(before.get("Status")) != typed(after.get("Status")):
            old_status, new_status = (
                object_value(before.get("Status")),
                object_value(after.get("Status")),
            )
            old_subnets = object_value(object_value(old_status.get("IPAM")).get("Subnets"))
            new_subnets = object_value(object_value(new_status.get("IPAM")).get("Subnets"))
            require(set(old_subnets) == set(new_subnets), "Actual IPAM status subnet set changed")
            changed = [
                subnet
                for subnet in old_subnets
                if typed(old_subnets[subnet]) != typed(new_subnets[subnet])
            ]
            require(
                len(changed) == 1,
                "Only one real disconnected IPv4 endpoint accounting change allowed",
            )
            subnet_name = changed[0]
            subnet = ipaddress.IPv4Network(subnet_name, strict=True)
            attachment = object_value(
                object_value(before.get("Containers")).get(transition.web_container_id)
            )
            interface = ipaddress.IPv4Interface(attachment["IPv4Address"])
            require(
                interface.network == subnet
                and interface.ip not in {subnet.network_address, subnet.broadcast_address},
                "Disconnected actual Web endpoint does not belong to changed subnet",
            )
            configuration = object_value(before.get("IPAM")).get("Config")
            require(
                isinstance(configuration, list)
                and any(
                    type(row) is dict and row.get("Subnet") == subnet_name for row in configuration
                ),
                "Changed allocation subnet is not the actual original network configuration",
            )
            old_counts, new_counts = (
                object_value(old_subnets[subnet_name]),
                object_value(new_subnets[subnet_name]),
            )
            require(
                all(
                    type(row.get(key)) is int and row[key] >= 0
                    for row in (old_counts, new_counts)
                    for key in ("IPsInUse", "DynamicIPsAvailable")
                ),
                "Native endpoint counters missing/noninteger/negative",
            )
            require(
                old_counts["IPsInUse"] == new_counts["IPsInUse"] + 1
                and old_counts["DynamicIPsAvailable"] + 1 == new_counts["DynamicIPsAvailable"]
                and old_counts["IPsInUse"] + old_counts["DynamicIPsAvailable"]
                == subnet.num_addresses
                and new_counts["IPsInUse"] + new_counts["DynamicIPsAvailable"]
                == subnet.num_addresses,
                "Actual endpoint release must be precisely one with complete subnet denominator",
            )
            # A comparison-only copy restores precisely two observed runtime
            # counters. Original JSON/raw/hash and command records remain intact.
            compare_counts = compare_after["Status"]["IPAM"]["Subnets"][subnet_name]
            compare_counts["IPsInUse"] = old_counts["IPsInUse"]
            compare_counts["DynamicIPsAvailable"] = old_counts["DynamicIPsAvailable"]
            adjustments.append(
                {
                    "network_id": before["Id"],
                    "subnet": subnet_name,
                    "endpoint_container_id": transition.web_container_id,
                    "before": old_counts.copy(),
                    "after": new_counts.copy(),
                }
            )
        require(
            typed({key: value for key, value in before.items() if key != "Containers"})
            == typed({key: value for key, value in compare_after.items() if key != "Containers"}),
            "Unknown network definition/ownership/status field changed",
        )
    require(
        not original_failure or len(adjustments) == 1,
        "Original network definition rejection is not explained by exact endpoint accounting",
    )
    return {
        **result,
        "protocol": "bounded-funds-offline-network-observation-v1",
        "status": "TOPOLOGY_ONLY_EGRESS_UNVERIFIED",
        "method": METHOD,
        "owner_uuid": owned.owner_uuid,
        "source_digest": owned.source_digest,
        "api_container_id": transition.api_container_id,
        "db_container_id": transition.db_container_id,
        "web_container_id": transition.web_container_id,
        "command_sha256": sha(command_raw),
        "containers_after_sha256": sha(containers_after_raw),
        "networks_after_sha256": sha(networks_after_raw),
        "original_guard_rejection_retained": original_failure,
        "actual_accounting_adjustments": adjustments,
        "offline_accepted": False,
        "uncovered": [
            "actual per-container route/IP/DNS probes",
            "relay/browser/three golden chains and original financial audit",
        ],
    }
