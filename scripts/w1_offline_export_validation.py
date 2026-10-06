"""Read-only native offline exporter; actual completed originals are required.

The first integration has TOOL_ONLY gate tests. No Docker/browser/financial
acceptance is inferred from flags or from installation of this checker.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import Any

from scripts import export_evidence as E

CORE_PREFIXES = (
    "apps/api/app/",
    "apps/api/alembic/",
    "apps/web/src/",
    "apps/web/tests/",
    "packages/contracts/",
    "deploy/",
)
CHECKPOINTS = (
    (
        ("independent-case-start-before", "BEGIN"),
        ("independent-case-start-after", "RESET"),
        ("salary-real-legs", "MONEY"),
        ("replay-before", "BEGIN"),
        ("salary-replay-zero-write", "READ_ONLY"),
    ),
    (
        ("independent-case-start-before", "BEGIN"),
        ("independent-case-start-after", "RESET"),
        ("goal-before", "BEGIN"),
        ("goal-confirmed-zero-owned", "MONEY"),
        ("new-goal-later-fixed-income", "MONEY"),
        ("natural-goal-later-income-real-allocation", "MONEY"),
    ),
    (
        ("independent-case-start-before", "BEGIN"),
        ("independent-case-start-after", "RESET"),
        ("salary-real-legs", "MONEY"),
        ("consumption-real-recovery", "MONEY"),
        ("legacy-recovery-read-zero-write", "READ_ONLY"),
    ),
    (
        ("independent-case-start-before", "BEGIN"),
        ("independent-case-start-after", "RESET"),
        ("salary-real-legs", "MONEY"),
        ("consumption-real-recovery", "MONEY"),
        ("legacy-recovery-read-zero-write", "READ_ONLY"),
        ("fixed-real-levels-and-loss-confirmation", "MONEY"),
    ),
)


def manifest_gate(
    manifest: dict[str, Any], run_id: str, head: str, *, tool_only: bool = False
) -> None:
    """Flags admit the next gate only; they never establish financial/offline proof."""
    E.need(
        tool_only or manifest.get("evidence_kind") not in {"TOOL_ONLY", "TOOL_TEST_ONLY"},
        "TOOL_ONLY never closes actual offline acceptance",
    )
    E.need(
        manifest.get("protocol") == "bounded-funds-offline-browser-v1"
        and manifest.get("run_id") == run_id
        and manifest.get("status") == "FOUR_OFFLINE_UI_CASES_VERIFIED"
        and type(manifest.get("exit_code")) is int
        and manifest["exit_code"] == 0
        and type(manifest.get("playwright_exit_code")) is int
        and manifest["playwright_exit_code"] == 0
        and manifest.get("source_before") == manifest.get("source_after")
        and bool(manifest.get("source_before"))
        and manifest.get("tool_source_before") == manifest.get("tool_source_after")
        and bool(manifest.get("tool_source_before"))
        and manifest.get("source_head") == manifest.get("source_head_after") == head
        and manifest.get("source_equal") is True
        and manifest.get("task_closed") is False
        and manifest.get("complete_all7") is False
        and manifest.get("three_rounds") == "NOT_RUN"
        and type(manifest.get("case_denominator")) is int
        and manifest["case_denominator"] == 4
        and manifest.get("offline_scope")
        == "THREE_GOLDEN_CHAINS_WITH_FIXED_LOSS_CASE_NOT_ALL7_OR_THREE_ROUNDS",
        "Actual source-bound completed four-case offline native run is missing",
    )
    E.need(
        E.aware(manifest.get("started_at")) < E.aware(manifest.get("finished_at")),
        "Actual native run has no positive time interval",
    )


def relative(proof: E.Originals, value: Any) -> str:
    E.need(isinstance(value, str), "Explicit native repository path missing")
    path = (proof.root / value).resolve()
    E.need(path.is_relative_to(proof.root.resolve()), "Native path escaped current repository")
    return E.relative_path(path.relative_to(proof.root.resolve()).as_posix()).as_posix()


def bound(proof: E.Originals, value: Any) -> tuple[dict[str, Any], bytes]:
    name = relative(proof, value)
    record, raw = proof.by_path(name)
    E.need(
        E.digest((proof.root / name).read_bytes()) == E.digest(raw),
        "Explicit native file changed since export preparation",
    )
    return record, raw


def bind_capture(proof: E.Originals, manifest: dict[str, Any]) -> None:
    """Register every private original and every separately labelled redacted transform."""
    entries = E.rows(manifest.get("artifacts"), "complete capture artifacts")
    E.need(
        bool(entries) and len({row["label"] for row in entries}) == len(entries),
        "Capture label denominator is empty or duplicated",
    )
    for row in entries:
        E.need(
            row.get("public_transformation") == "REDACTED_REENCODING_V1_NOT_ORIGINAL",
            "Public transformed bytes must remain explicitly distinct from originals",
        )
        for actual_prefix in ("private_original", "public"):
            E.need(actual_prefix + "_path" in row, "Original/transform capture pair missing")
            _, raw = bound(proof, row[actual_prefix + "_path"])
            E.need(
                E.digest(raw) == row[actual_prefix + "_sha256"]
                and type(row[actual_prefix + "_bytes"]) is int
                and len(raw) == row[actual_prefix + "_bytes"],
                "Original/transform hash or actual byte denominator differs",
            )


def raw_capture(proof: E.Originals, manifest: dict[str, Any], label: str) -> bytes:
    found = [row for row in manifest["artifacts"] if row.get("label") == label]
    E.need(len(found) == 1, "Actual original capture missing/duplicated: " + label)
    row = found[0]
    _, raw = bound(proof, row["private_original_path"])
    E.need(
        E.digest(raw) == row["private_original_sha256"]
        and type(row["private_original_bytes"]) is int
        and len(raw) == row["private_original_bytes"],
        "Native original bytes differ",
    )
    return raw


def checkpoint_denominator(
    checkpoints: list[dict[str, Any]], cases: dict[str, str], titles: tuple[str, ...]
) -> None:
    E.need(
        tuple(cases) == titles or set(cases) == set(titles), "Exact four actual case IDs missing"
    )
    expected = ["w1-" + re.sub(r"[^a-z0-9]", "", cases[title], flags=re.I)[:64] for title in titles]
    E.need(len(set(expected)) == 4, "Actual case scenario IDs collide")
    E.need(
        len(checkpoints) == sum(map(len, CHECKPOINTS)),
        "Complete fixed checkpoint denominator missing",
    )
    for scenario, expected_rows in zip(expected, CHECKPOINTS, strict=True):
        actual = [
            (row.get("label"), row.get("mode"))
            for row in checkpoints
            if row.get("scenario_id") == scenario
        ]
        E.need(
            actual == list(expected_rows),
            "Actual case omitted/reordered its financial/zero-write gate",
        )
    E.need({row["scenario_id"] for row in checkpoints} == set(expected), "Foreign checkpoint case")


def replay_readiness(proof: E.Originals, native: Any, manifest: dict[str, Any], base: str) -> Any:
    """Use retained initial relay bytes, never the later mutable relay manifest."""
    from scripts import w1_offline_boundary as boundary
    from scripts.demo_container_transport import validate_owned_transport
    from scripts.demo_offline_endpoint_accounting import validate_transition_accounting
    from scripts.demo_offline_network import build_transition, containers, networks

    paths = E.obj(manifest.get("prerequisites"), "five exact deployment original paths")
    E.need(
        set(paths) == {"registration", "start", "isolation", "probe", "relay"},
        "Five prerequisite paths required",
    )
    raw = {name: bound(proof, base + "/native/prerequisite-" + name + ".json")[1] for name in paths}
    files = {name: proof.root / relative(proof, path) for name, path in paths.items()}
    # Four immutable original prerequisites must still equal their retained bytes.
    for name in ("registration", "start", "isolation", "probe"):
        E.need(
            bound(proof, files[name].as_posix())[1] == raw[name], "Prerequisite original changed"
        )
    prior, probe, relay = [
        E.obj(E.strict_json(raw[name]), "original prerequisite")
        for name in ("isolation", "probe", "relay")
    ]
    for original in (prior, probe):
        bind_capture(proof, original)
    current, head = manifest["source_before"], manifest["source_head"]
    E.need(
        prior.get("protocol") == boundary.PROTOCOL
        and prior.get("action") == "isolate"
        and prior.get("source_before") == prior.get("source_after") == current
        and prior.get("source_head") == prior.get("source_head_after") == head
        and prior.get("status") in {"FAILED", "ISOLATED_TOPOLOGY_EGRESS_UNVERIFIED"},
        "Original actual isolation/source changed; failed original stays failed",
    )
    for name, label in (("registration", "registration"), ("start", "start-manifest")):
        E.need(
            raw_capture(proof, prior, label) == raw[name]
            and raw_capture(proof, probe, label) == raw[name],
            "Native prerequisite linkage differs",
        )
    E.need(
        raw_capture(proof, probe, "prior-isolation-manifest") == raw["isolation"],
        "Probe did not bind this executed isolation",
    )
    images = {
        name: bound(proof, (files["start"].parent / (name + "-image.log")).as_posix())[1]
        for name in ("api", "web")
    }
    before, after = [
        raw_capture(proof, prior, phase + "-containers-stdout") for phase in ("before", "after")
    ]
    nb, na = [
        raw_capture(proof, prior, phase + "-networks-stdout") for phase in ("before", "after")
    ]
    owned = validate_owned_transport(
        raw["registration"],
        raw["start"],
        before,
        images,
        current,
        head,
        require_started_inspect=False,
    )
    transition = build_transition(owned, before, nb, current, head)
    dc, out, err = native.native_command(prior, "disconnect", transition.argv)
    E.need(type(dc) is int and dc == 0, "Actual original disconnect did not exit zero")
    result = validate_transition_accounting(
        owned,
        transition,
        before,
        after,
        nb,
        na,
        raw_capture(proof, prior, "disconnect-command"),
        out,
        err,
        current,
        head,
    )
    E.need(
        result == probe.get("prior_transition_revalidated"), "Actual endpoint accounting changed"
    )
    observed, observed_nets = native.verify_probe(probe, owned, current, head)
    boundary.same_isolated(after, observed, na, observed_nets)
    for name, image in images.items():
        for original in (prior, probe):
            for phase in ("before", "after"):
                boundary.admit_image_observation(
                    image, raw_capture(proof, original, phase + "-" + name + "-image-stdout")
                )
    native.verify_relay(relay, owned, current, head)
    E.need(
        relay.get("web_container_id") == transition.web_container_id,
        "Initial relay did not bind actual immutable Web",
    )
    parsed_nets = networks(observed_nets, owned)
    actual = containers(observed)
    E.need(
        set(actual["web"]["NetworkSettings"]["Networks"]) == {owned.project + "_demo_internal"},
        "Actual Web retains egress network",
    )
    initial_hashes = {name: E.digest(value) for name, value in raw.items() if name != "relay"}
    E.need(
        initial_hashes == manifest.get("prerequisite_original_sha256"),
        "Initial prerequisite original digest denominator differs",
    )
    return native.Readiness(
        files["registration"],
        files["start"],
        files["isolation"],
        files["probe"],
        files["relay"],
        owned,
        current,
        head,
        observed,
        observed_nets,
        tuple(sorted(row["Id"] for row in parsed_nets.values())),
        len(relay["requests"]),
        tuple(sorted(initial_hashes.items())),
        tuple(sorted(raw.items())),
    )


def command_original(
    proof: E.Originals,
    manifest: dict[str, Any],
    label: str,
    argv: tuple[str, ...],
    stdin: bytes,
    base: str,
) -> bytes:
    entries = [row for row in manifest["commands"] if row.get("label") == label]
    E.need(len(entries) == 1, "Original command missing or duplicated: " + label)
    row = entries[0]
    _, actual_input = bound(proof, base + "/native/" + label + ".stdin")
    _, stdout = bound(proof, row["stdout"]["path"])
    _, stderr = bound(proof, row["stderr"]["path"])
    E.need(
        row.get("argv") == list(argv)
        and actual_input == stdin
        and row.get("stdin_sha256") == E.digest(stdin)
        and row["stdout"].get("sha256") == E.digest(stdout)
        and row["stderr"].get("sha256") == E.digest(stderr)
        and row["stdout"].get("bytes") == len(stdout)
        and row["stderr"].get("bytes") == len(stderr)
        and type(row.get("exit_code")) is int
        and row["exit_code"] == 0
        and E.aware(manifest["started_at"])
        <= E.aware(row["started_at"])
        <= E.aware(row["finished_at"])
        <= E.aware(manifest["finished_at"]),
        "Actual command argv/stdin/original bytes/exit/time differ",
    )
    return stdout


def browser_session_originals(
    proof: E.Originals, base: str, manifest: dict[str, Any], cases: dict[str, str]
) -> int:
    """Bind actual Edge sessions/video/PNG/HTTP trace bytes to all four case IDs."""
    expected = {"w1-" + re.sub(r"[^a-z0-9]", "", case, flags=re.I)[:64] for case in cases.values()}
    seen = set()
    for name in manifest["artifact_hashes"]:
        if not name.endswith("/actual-browser.json"):
            continue
        _, raw = bound(proof, base + "/" + name)
        browser = E.obj(E.strict_json(raw), "actual Edge session identity")
        scenario = browser.get("scenario_id")
        E.need(
            scenario in expected
            and scenario not in seen
            and browser.get("run_id") == manifest["run_id"]
            and browser.get("requested_channel") == "msedge"
            and isinstance(browser.get("actual_engine_version"), str)
            and bool(browser["actual_engine_version"]),
            "Actual browser session identity differs",
        )
        seen.add(scenario)
        folder = name.rsplit("/", 1)[0]
        _, trace = bound(proof, base + "/" + folder + "/trace.zip")
        capture = E.trace_capture_bridge(trace)
        E.need(
            capture["context"]["options"]["userAgent"] == browser.get("actual_user_agent"),
            "Original Edge trace user agent differs",
        )
        videos = [
            value
            for value in manifest["artifact_hashes"]
            if value.startswith(folder + "/") and value.endswith(".webm")
        ]
        E.need(len(videos) == 1, "Actual Edge trace-bound complete session video missing")
        _, video = bound(proof, base + "/" + videos[0])
        E.need(
            E.digest(video) == capture["video_sha256"], "Native video/trace resource bytes differ"
        )
        pngs = [
            value
            for value in manifest["artifact_hashes"]
            if value.startswith(folder + "/") and value.endswith(".native.png")
        ]
        E.need(
            bool(pngs) and len(pngs) <= capture["screenshot_calls"], "Original screenshots missing"
        )
        for value in pngs:
            _, png = bound(proof, base + "/" + value)
            _, text = bound(proof, base + "/" + value[:-4] + ".txt")
            E.png_structure(png)
            E.need(bool(text.decode("utf-8").strip()), "Original screenshot body text missing")
    E.need(seen == expected, "Actual Edge trace/video session denominator differs")
    return len(seen)


def _offline_three_golden_chains(proof: E.Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    """Candidate strict native producer bridge. It reads originals only."""
    scoped, _ = proof.scoped(inputs.get("scoped"))
    record, raw = proof.original(inputs.get("offline_manifest"))
    manifest = E.obj(E.strict_json(raw), "native offline browser manifest")
    manifest_gate(manifest, record["run_id"], proof.head, tool_only=proof.tool_test_only)
    before = manifest["source_before"]
    proof.current_subset(before, CORE_PREFIXES)
    E.need(manifest["tool_source_before"] == manifest["tool_source_after"], "Tool source drift")
    for name, expected in manifest["tool_source_before"].items():
        _, tool_raw = bound(proof, name)
        E.need(E.digest(tool_raw) == expected, "Actual original tool source changed")
    name = "scripts/w1_offline_browser.py"
    E.need(
        name in proof.files and proof.files[name] == manifest["tool_source_before"].get(name),
        "Native offline producer is outside current frozen source",
    )
    native = importlib.import_module("scripts.w1_offline_browser")
    E.need(
        isinstance(native.__file__, str)
        and Path(native.__file__).resolve() == (proof.root / name).resolve(),
        "Foreign pure producer",
    )
    base = Path(record["resolved_repository_path"]).parent.as_posix()
    E.need(base.startswith("output/playwright/offline-"), "Native offline output scope changed")
    index = E.obj(manifest.get("artifact_hashes"), "complete offline original index")
    # Exclude only the root mutable manifest, not any nested original manifests.
    actual_files = {
        path.relative_to(proof.root / base).as_posix()
        for path in (proof.root / base).rglob("*")
        if path.is_file() and path != proof.root / base / "manifest.json"
    }
    E.need(bool(index) and set(index) == actual_files, "Full offline artifact denominator changed")
    for relative_name, expected in index.items():
        item, original = bound(proof, base + "/" + E.relative_path(relative_name).as_posix())
        E.need(
            item["run_id"] == record["run_id"] and E.digest(original) == E.sha(expected),
            "Offline child originals mix actual runs or changed bytes",
        )
    ready = replay_readiness(proof, native, manifest, base)
    E.need(
        manifest.get("owner_uuid") == ready.transport.owner_uuid
        and manifest.get("owner_run_id") == ready.transport.owner_run_id
        and manifest.get("database") == ready.transport.database
        and manifest.get("project") == ready.transport.project,
        "Actual UI run differs from immutable owned image/namespace",
    )
    args = scoped["command"]
    E.need(
        len(args) == 15
        and Path(args[1]).resolve() == proof.root / name
        and args[2] == "--run"
        and args[-2] == "--output"
        and relative(proof, args[-1]) == base,
        "Actual scoped command is not this explicit offline run",
    )
    expected_args = {
        "--" + key: relative(proof, value) for key, value in manifest["prerequisites"].items()
    }
    E.need(
        {args[i]: relative(proof, args[i + 1]) for i in range(3, 13, 2)} == expected_args,
        "Actual scoped prerequisite arguments differ",
    )
    result = E.obj(
        E.strict_json(bound(proof, base + "/playwright/results.json")[1]), "Edge originals"
    )
    actual_results = native.validate_results(result)
    E.need(
        actual_results == manifest.get("actual_results")
        and manifest.get("case_titles") == list(native.CASE_TITLES),
        "Actual result denominator differs",
    )

    def duration_gate(suites: list[dict[str, Any]]) -> None:
        for suite in suites:
            for spec in suite.get("specs", []):
                attempt = spec["tests"][0]["results"][0]
                E.need(
                    type(attempt.get("duration")) in {int, float} and attempt["duration"] > 0,
                    "An unexecuted browser attempt cannot close acceptance",
                )
            duration_gate(suite.get("suites", []))

    duration_gate(E.rows(result.get("suites"), "actual native suites"))
    session_count = browser_session_originals(proof, base, manifest, actual_results["case_ids"])
    checkpoints = E.rows(manifest.get("checkpoints"), "all native financial checkpoints")
    checkpoint_denominator(checkpoints, actual_results["case_ids"], native.CASE_TITLES)
    native.validate_reset_scope(checkpoints, actual_results["case_ids"])
    config, wrapper = native.generated_files(proof.root / base)
    E.need(
        bound(proof, base + "/generated/offline.config.cjs")[1] == config
        and bound(proof, base + "/generated/offline-wrapper.spec.mts")[1] == wrapper,
        "Executed browser wrapper/config changed the original UI/HTTP capture",
    )
    oracle = E.native_oracle(proof, inputs.get("oracle_source"))
    adapter_record, adapter_raw = proof.original(inputs.get("reset_adapter_source"))
    E.need(
        adapter_record["resolved_repository_path"] == "scripts/browser_checkpoint_oracles.py"
        and E.digest(adapter_raw) == E.TYPED_RESET_ADAPTER_SHA256,
        "Actual typed reset source is not the frozen exact adapter",
    )
    reset = proof.tool("scripts/browser_checkpoint_oracles.py")
    transport512 = importlib.import_module("scripts.demo_container_transport512")
    modules = E.audit_modules(proof)
    snapshots: list[tuple[dict[str, Any], Any]] = []
    metadata_all = [manifest["baseline"]] + [row["result"]["snapshot"] for row in checkpoints]
    metadata_all += [manifest["final_snapshot"]]
    expected_labels = []
    for number, metadata in enumerate(metadata_all, 1):
        path = relative(proof, metadata["path"])
        stem = Path(path).name.removesuffix(".json.gz")
        E.need(
            stem.startswith(f"{number:04d}-")
            and Path(path).parent.as_posix() == base + "/checkpoints",
            "Actual snapshot sequence/path is not the complete original sequence",
        )
        prepared = transport512.build_snapshot_command512(
            ready.transport, record["run_id"] + "-" + stem
        )
        stdout = command_original(
            proof, manifest, "snapshot-" + stem, prepared.argv, prepared.stdin, base
        )
        decoded = transport512.decode_snapshot_output512(ready.transport, prepared, stdout)
        _, gzip_raw = bound(proof, path)
        business, actual_meta = native.snapshot_business(decoded)
        E.need(
            gzip_raw == decoded.snapshot_gzip
            and all(metadata.get(key) == value for key, value in actual_meta.items())
            and metadata.get("compressed_bytes") == len(gzip_raw)
            and metadata.get("audit_original")
            == {
                key: value for key, value in decoded.report.items() if key != "snapshot_gzip_base64"
            },
            "Actual full24 gzip/source/framing/audit originals differ",
        )
        physical = E.obj(E.strict_json(decoded.snapshot_data), "actual full physical24")
        replayed = E.audit_snapshot_bridge(physical, modules)
        expected = {(row["user_id"], row["epoch_id"]): row for row in replayed["verifications"]}
        observations = decoded.report["observations"]
        E.need(
            len(observations) == len(expected)
            and all(
                row["verification"] == expected[(row["user_id"], row["epoch_id"])]
                for row in observations
            ),
            "Actual full service result differs from original-domain full-reference replay",
        )
        snapshots.append((business, decoded))
        expected_labels.append("snapshot-" + stem)
    E.need(
        [row["label"] for row in manifest["commands"] if row["label"].startswith("snapshot-")]
        == expected_labels,
        "An original snapshot command was omitted or added outside financial denominator",
    )
    baseline = snapshots[0][0]
    E.need(
        oracle["money_oracle"](baseline, baseline) == manifest.get("baseline_oracle"),
        "Actual baseline full-ledger oracle differs",
    )
    previous = None
    previous_bytes = None
    previous_heads = None
    for row, (business, decoded) in zip(checkpoints, snapshots[1:-1], strict=True):
        heads = [
            item["version_num"] for item in E.strict_json(decoded.snapshot_data)["alembic_version"]
        ]
        mode = row["mode"]
        if mode != "BEGIN":
            E.need(
                previous is not None and previous_heads == heads,
                "Checkpoint lost prior rows/schema",
            )
            if mode == "READ_ONLY":
                E.need(
                    previous_bytes == decoded.snapshot_data,
                    "Read/replay mutated full physical24 bytes",
                )
                E.need(
                    "oracle" not in row["result"], "Read-only checkpoint substituted a money result"
                )
            elif mode == "RESET":
                E.need(
                    reset["reset_oracle"](previous, business, baseline)
                    == row["result"].get("oracle"),
                    "Actual reset did not preserve every original business/audit/seal field",
                )
            else:
                E.need(
                    oracle["money_oracle"](previous, business) == row["result"].get("oracle"),
                    "Actual integer bank ledger/economic origins/cash/principal proof differs",
                )
        previous, previous_bytes, previous_heads = business, decoded.snapshot_data, heads
    E.need(
        snapshots[-1][1].snapshot_data == previous_bytes,
        "Final owned-alive all-user audit snapshot differs from last settled financial checkpoint",
    )
    # Reconstruct each financial RPC from the original DTO/source-bound builder. No
    # replacement bank status or arbitrary shell command is admitted.
    from scripts.demo_container_transport import build_rpc_command

    rpc_rows = E.rows(manifest.get("rpc"), "all actual original financial RPCs")
    E.need(
        sum(row["input"].get("operation") == "ingest_goal_income" for row in rpc_rows) == 1
        and sum(row["input"].get("operation") == "verify_legacy_recovery" for row in rpc_rows) >= 2
        and all(
            row["input"].get("operation") in {"ingest_goal_income", "verify_legacy_recovery"}
            for row in rpc_rows
        ),
        "Fixed original goal-income/recovery proof RPC denominator differs",
    )
    commands = [row for row in manifest["commands"] if row["label"].startswith("rpc-")]
    E.need(len(commands) == len(rpc_rows), "Original RPC command/result denominator differs")
    for rpc, command_row in zip(rpc_rows, commands, strict=True):
        prepared = build_rpc_command(ready.transport, rpc["input"])
        stdout = command_original(
            proof, manifest, command_row["label"], prepared.argv, prepared.stdin, base
        )
        response = E.obj(E.strict_json(stdout), "actual original RPC output")
        E.need(
            E.digest(stdout) == rpc["stdout_sha256"]
            and response.get("result") == rpc["result"]
            and response.get("protocol") == "bounded-funds-scenario-v1"
            and response.get("operation") == rpc["input"]["operation"]
            and response.get("scenario_id") == rpc["input"]["scenario_id"]
            and response.get("status") == "PASSED",
            "Actual original DTO/RPC output differs",
        )
    # Every observer must be explicitly packaged, actual exit0, and compare the entire
    # isolated topology. Its original native probe is classified again, not trusted flags.
    from scripts import w1_offline_boundary as boundary

    observations = E.rows(
        manifest.get("topology_observations"), "every actual topology observation"
    )
    E.need(
        bool(observations)
        and observations[-1].get("probe") is True
        and sum(row.get("probe") is True for row in observations) == 1
        and len(observations) == len(metadata_all) + len(rpc_rows) + 1
        and len({row["path"] for row in observations}) == len(observations)
        and len([row for row in manifest["commands"] if row["label"].startswith("observe-")])
        == len(observations),
        "Final actual all-container probe is absent or denominator changed",
    )
    for row in observations:
        _, observed_raw = bound(proof, row["path"])
        E.need(E.digest(observed_raw) == row["sha256"], "Actual observer original changed")
        observed = E.obj(E.strict_json(observed_raw), "actual observer")
        label = Path(row["path"]).parent.name.removeprefix(Path(base).name + "-")
        E.need(
            Path(row["path"]).parent.name == Path(base).name + "-" + label,
            "Original observer does not belong to this browser run",
        )
        E.need(
            bound(proof, base + "/native/" + label + ".observation-manifest.json")[1]
            == observed_raw,
            "Actual observer original differs from retained native copy",
        )
        command_rows = [
            item for item in manifest["commands"] if item["label"] == "observe-" + label
        ]
        E.need(len(command_rows) == 1, "Actual observer native command missing or duplicated")
        observer_output = Path(relative(proof, row["path"])).parent.as_posix()
        expected_argv = (
            command_rows[0]["argv"][0],
            str(proof.root / "scripts/w1_offline_reobserve.py"),
            "--run",
            "--action",
            "probe" if row["probe"] else "observe",
            "--registration",
            str(ready.registration),
            "--start",
            str(ready.start),
            "--prior-isolation",
            str(ready.isolation),
            "--output",
            observer_output,
        )
        observer_stdout = command_original(
            proof, manifest, "observe-" + label, expected_argv, b"", base
        )
        terminal = E.obj(E.strict_json(observer_stdout), "original native observer terminal output")
        E.need(
            terminal.get("manifest") == relative(proof, row["path"])
            and terminal.get("status") == observed.get("status")
            and type(terminal.get("exit_code")) is int
            and terminal["exit_code"] == 0,
            "Actual native observer exit does not match the original terminal output",
        )
        bind_capture(proof, observed)
        E.need(
            observed.get("source_before") == observed.get("source_after") == before
            and observed.get("source_head") == observed.get("source_head_after") == proof.head
            and observed.get("owner_uuid") == ready.transport.owner_uuid
            and observed.get("owner_run_id") == ready.transport.owner_run_id
            and type(observed.get("exit_code")) is int
            and observed["exit_code"] == 0
            and E.aware(manifest["started_at"])
            <= E.aware(observed["started_at"])
            <= E.aware(observed["finished_at"])
            <= E.aware(manifest["finished_at"]),
            "Actual topology observer source/owner/exit differs",
        )
        boundary.same_isolated(
            ready.isolated_containers,
            raw_capture(proof, observed, "after-containers-stdout"),
            ready.isolated_networks,
            raw_capture(proof, observed, "after-networks-stdout"),
        )
        if row["probe"]:
            native.verify_probe(observed, ready.transport, before, proof.head)
    # Native per-request relay bytes live outside the child folder and need explicit
    # artifact_ids too. Never silently reopen an unregistered adjacent original.
    relay_final = E.obj(
        E.strict_json(bound(proof, base + "/native/relay-final-observed.json")[1]),
        "actual final observed relay",
    )
    native.verify_relay(relay_final, ready.transport, before, proof.head)
    relay_originals = E.rows(
        manifest.get("network_originals", {}).get("relay_originals"),
        "all run-specific actual relay original bytes",
    )
    for entry in relay_originals:
        E.need(
            entry["original_directory"] == ready.relay.parent.relative_to(proof.root).as_posix(),
            "Original relay directory changed",
        )
        prefix = entry["original_directory"] + f"/request-{entry['ordinal']:05d}"
        for suffix, field in (
            ("-client.http", "client_sha256"),
            ("-forwarded.http", "forwarded_sha256"),
            ("-response.http", "response_sha256"),
            ("-stderr.log", "stderr_sha256"),
        ):
            _, original = bound(proof, prefix + suffix)
            E.need(E.digest(original) == entry[field], "Original relay stream changed")
    network = native.validate_network_originals(
        proof.root / base, ready, relay_final, actual_results["case_ids"]
    )
    E.need(
        network == manifest.get("network_originals"),
        "Complete browser HTTP/WS/relay closure differs",
    )
    return {
        "method": "NATIVE_OFFLINE_ORIGINALS_FULL_REPLAY_V1",
        "actual_cases": 4,
        "golden_chains": 3,
        "actual_checkpoint_count": len(checkpoints),
        "actual_edge_trace_bound_sessions": session_count,
        "actual_physical_table_denominator": 24,
        "actual_reset_count": 4,
        "all_user_epoch_final_count": len(snapshots[-1][1].report["observations"]),
        "network": network,
        "owner_uuid": ready.transport.owner_uuid,
        "boundary": "Owned containers observed without egress; browser uses same-origin "
        "loopback ingress."
        " No host-wide firewall claim, all7/three-round claim or task closure.",
    }


def offline_three_golden_chains(proof: E.Originals, inputs: dict[str, Any]) -> dict[str, Any]:
    """Standalone exports use the bound repository's original models, then restore path."""
    api = str(proof.root / "apps/api")
    sys.path.insert(0, api)
    try:
        return _offline_three_golden_chains(proof, inputs)
    finally:
        sys.path.remove(api)
