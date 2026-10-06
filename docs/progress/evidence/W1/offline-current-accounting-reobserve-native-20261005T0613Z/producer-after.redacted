"""Fresh read-only observation/probes after an already executed owned disconnect."""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import w1_offline_boundary as original  # noqa: E402
from scripts.demo_container_transport import (  # noqa: E402
    object_value,
    original_json,
    require,
    sha,
    validate_owned_transport,
)
from scripts.demo_offline_endpoint_accounting import (  # noqa: E402
    FROZEN,
    METHOD,
    validate_transition_accounting,
)
from scripts.demo_offline_network import build_transition, containers  # noqa: E402

PROTOCOL = "bounded-funds-offline-reobserve-v1"
ORIGINAL_CLI_SHA = "032bf3482e7b36b9c175a5829fae3a4c1346aa25ab3cae5c567147903bf29bf7"


def prior_capture(prior: dict[str, Any], label: str) -> bytes:
    rows = [row for row in prior["artifacts"] if row["label"] == label]
    require(len(rows) == 1, "Original prior capture missing/duplicate")
    row = object_value(rows[0])
    path = (ROOT / str(row["private_original_path"])).resolve()
    require(
        path.is_relative_to(ROOT / ".runtime/w1-offline-boundary-private"),
        "Original prior private path escaped",
    )
    raw = path.read_bytes()
    require(
        sha(raw) == row["private_original_sha256"] and len(raw) == row["private_original_bytes"],
        "Original prior capture bytes changed",
    )
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--action", choices=("observe", "probe"), required=True)
    for name in ("registration", "start", "prior-isolation", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if not args.run:
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "status": "PREPARED_NOT_EXECUTED",
                    "connection_made": False,
                    "offline_accepted": False,
                }
            )
        )
        return 0
    destination = (ROOT / args.output).resolve()
    require(
        destination.is_relative_to(ROOT / "docs/progress/evidence/W1") and not destination.exists(),
        "Fresh public W1 destination required",
    )
    destination.mkdir(parents=True)
    private = ROOT / ".runtime/w1-offline-boundary-private" / (destination.name + "-" + uuid4().hex)
    private.mkdir(parents=True)
    own_source = Path(__file__).read_bytes()
    dependencies = {**FROZEN, "scripts/w1_offline_boundary.py": ORIGINAL_CLI_SHA}
    bridge_path = ROOT / "scripts/demo_offline_endpoint_accounting.py"
    bridge_source = bridge_path.read_bytes()
    helper_path = ROOT / "scripts/w1_demo_deployment.py"
    helper_source = helper_path.read_bytes()
    manifest: dict[str, Any] = {
        "protocol": PROTOCOL,
        "run_id": destination.name,
        "action": args.action,
        "method": METHOD,
        "status": "INCOMPLETE",
        "started_at": original.now(),
        "producer_sha256": sha(own_source),
        "bridge_sha256": sha(bridge_source),
        "source_helper_sha256": sha(helper_source),
        "private_directory": private.relative_to(ROOT).as_posix(),
        "artifacts": [],
        "commands": [],
        "offline_accepted": False,
        "financial_operations_executed": False,
        "disconnect_executed_by_this_run": False,
        "three_golden_chains": "NOT_RUN",
        "private_originals_security": "SEPARATE_RUNTIME_DIRECTORY_NOT_ACL_ATTESTED",
    }
    capture = original.Capture(destination, private, manifest)
    source_state = None
    code = 1
    try:
        capture.save("producer-before", own_source)
        capture.save("bridge-before", bridge_source)
        capture.save("source-helper-before", helper_source)
        require(
            all(
                sha((ROOT / name).read_bytes()) == checksum
                for name, checksum in dependencies.items()
            ),
            "Frozen dependencies changed",
        )
        source_state = runpy.run_path(str(helper_path))["source_state"]
        current = source_state()
        manifest["source_before"] = current
        exit_code, raw_head, _ = capture.command("head-before", ("git", "rev-parse", "HEAD"))
        require(exit_code == 0, "Actual HEAD observation failed")
        head = raw_head.decode().strip()
        manifest["source_head"] = head
        registration, start, prior_path = (
            (ROOT / value).resolve()
            for value in (args.registration, args.start, args.prior_isolation)
        )
        require(
            registration.is_relative_to(ROOT / ".runtime/w1-deployment")
            and registration.name == "manifest.json"
            and all(
                path.is_relative_to(ROOT / "docs/progress/evidence/W1")
                and path.name == "manifest.json"
                for path in (start, prior_path)
            ),
            "Original input path escaped",
        )
        registration_raw, start_raw, prior_raw = (
            registration.read_bytes(),
            start.read_bytes(),
            prior_path.read_bytes(),
        )
        for label, raw in (
            ("registration", registration_raw),
            ("start-manifest", start_raw),
            ("prior-isolation-manifest", prior_raw),
        ):
            capture.save(label, raw)
        prior = object_value(original_json(prior_raw))
        require(
            prior.get("protocol") == original.PROTOCOL
            and prior.get("action") == "isolate"
            and prior.get("producer_sha256")
            == prior.get("producer_after_sha256")
            == ORIGINAL_CLI_SHA
            and prior.get("source_before") == prior.get("source_after") == current
            and prior.get("source_head") == prior.get("source_head_after") == head,
            "Prior executed isolation/source/producer differs",
        )
        manifest["prior_actual_status_retained"] = prior.get("status")
        require(
            prior.get("status") in {"FAILED", "ISOLATED_TOPOLOGY_EGRESS_UNVERIFIED"},
            "Unknown prior isolation terminal status",
        )
        commands = [row for row in prior["commands"] if row.get("label") == "disconnect"]
        require(
            len(commands) == 1
            and type(commands[0].get("exit_code")) is int
            and commands[0]["exit_code"] == 0,
            "Exactly one originally executed successful disconnect required",
        )
        images = {
            name: (start.parent / (name + "-image.log")).read_bytes() for name in ("api", "web")
        }
        before, after = (
            prior_capture(prior, "before-containers-stdout"),
            prior_capture(prior, "after-containers-stdout"),
        )
        network_before, network_after = (
            prior_capture(prior, "before-networks-stdout"),
            prior_capture(prior, "after-networks-stdout"),
        )
        require(
            prior_capture(prior, "registration") == registration_raw
            and prior_capture(prior, "start-manifest") == start_raw,
            "Prior registration/start originals differ",
        )
        for name, image in images.items():
            for phase in ("before", "after"):
                original.admit_image_observation(
                    image, prior_capture(prior, phase + "-" + name + "-image-stdout")
                )
        owned = validate_owned_transport(
            registration_raw,
            start_raw,
            before,
            images,
            current,
            head,
            require_started_inspect=False,
        )
        transition = build_transition(owned, before, network_before, current, head)
        manifest["prior_transition_revalidated"] = validate_transition_accounting(
            owned,
            transition,
            before,
            after,
            network_before,
            network_after,
            prior_capture(prior, "disconnect-command"),
            prior_capture(prior, "disconnect-stdout"),
            prior_capture(prior, "disconnect-stderr"),
            current,
            head,
        )
        manifest.update(
            owner_uuid=owned.owner_uuid,
            owner_run_id=owned.owner_run_id,
            source_digest=owned.source_digest,
        )
        ids = tuple(row["Id"] for row in containers(after).values())

        def observe(label: str) -> tuple[bytes, bytes]:
            exit_code, fresh, _ = capture.command(
                label + "-containers", ("docker", "inspect", *ids)
            )
            require(exit_code == 0, "Fresh actual isolated container inspect failed")
            exit_code, network, _ = capture.command(
                label + "-networks",
                (
                    "docker",
                    "network",
                    "inspect",
                    transition.internal_network_id,
                    transition.frontend_network_id,
                ),
            )
            require(exit_code == 0, "Fresh actual immutable network inspect failed")
            for name, image in images.items():
                identity = original_json(image)[0]["Id"]
                exit_code, fresh_image, _ = capture.command(
                    label + "-" + name + "-image", ("docker", "image", "inspect", identity)
                )
                require(exit_code == 0, "Fresh actual image inspect failed")
                manifest.setdefault("image_representation_bindings", {})[label + "-" + name] = (
                    original.admit_image_observation(image, fresh_image)
                )
            return fresh, network

        fresh_before, fresh_network = observe("before")
        original.same_isolated(after, fresh_before, network_after, fresh_network)
        if args.action == "probe":
            exit_code, stdout, _ = capture.command(
                "api-probe",
                ("docker", "exec", "-i", owned.api_container_id, "/opt/bf-venv/bin/python", "-"),
                stdin=original.API_PROBE,
                timeout=16,
            )
            observations = {"api": original.classify_api_probe(stdout, exit_code)}
            actual = containers(fresh_before)
            for name in ("db", "web"):
                outputs = {}
                for label, probe_argv in original.busybox_commands(actual[name]["Id"]):
                    outputs[label] = capture.command(name + "-" + label, probe_argv, timeout=7)
                    if label == "inventory" and (
                        outputs[label][0] != 0
                        or not {"ip", "wget", "nslookup", "timeout", "cat"}.issubset(
                            set(outputs[label][1].decode(errors="replace").splitlines())
                        )
                    ):
                        break
                observations[name] = original.classify_busybox(outputs)
            manifest["probes"] = observations
            ready = all(row.get("observed") is True for row in observations.values())
            manifest["status"], code = (
                ("EGRESS_BOUNDARY_OBSERVED", 0) if ready else ("DEFERRED_EGRESS_UNVERIFIED", 3)
            )
        else:
            manifest["status"], code = "ISOLATED_TOPOLOGY_REOBSERVED_EGRESS_UNVERIFIED", 0
        fresh_after, network_final = observe("after")
        original.same_isolated(fresh_before, fresh_after, fresh_network, network_final)
    except Exception as error:
        manifest.update(
            status="FAILED",
            error_type=type(error).__name__,
            error="Read-only observation failed; originals and transformed diagnostics retained",
        )
        code = 1
    finally:
        try:
            producer_after, bridge_after, helper_after = (
                Path(__file__).read_bytes(),
                bridge_path.read_bytes(),
                helper_path.read_bytes(),
            )
            for label, raw in (
                ("producer-after", producer_after),
                ("bridge-after", bridge_after),
                ("source-helper-after", helper_after),
            ):
                capture.save(label, raw)
            manifest.update(
                producer_after_sha256=sha(producer_after),
                bridge_after_sha256=sha(bridge_after),
                source_helper_after_sha256=sha(helper_after),
            )
            if source_state is not None:
                manifest["source_after"] = source_state()
                exit_code, head_after, _ = capture.command(
                    "head-after", ("git", "rev-parse", "HEAD")
                )
                manifest["source_head_after"] = head_after.decode().strip()
                require(
                    exit_code == 0
                    and manifest.get("source_before") == manifest["source_after"]
                    and manifest.get("source_head") == manifest["source_head_after"]
                    and own_source == producer_after
                    and bridge_source == bridge_after
                    and helper_source == helper_after
                    and all(
                        sha((ROOT / name).read_bytes()) == checksum
                        for name, checksum in dependencies.items()
                    ),
                    "Actual source/HEAD/producer drift invalidates observation",
                )
        except Exception as error:
            manifest.update(
                status="FAILED",
                error_type=type(error).__name__,
                error="Final read-only source observation failed",
            )
            code = 1
        manifest.update(finished_at=original.now(), exit_code=code)
        with (destination / "manifest.json").open("xb") as handle:
            handle.write(original.encode(manifest))
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "exit_code": code,
                "manifest": (destination / "manifest.json").relative_to(ROOT).as_posix(),
                "offline_accepted": False,
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
