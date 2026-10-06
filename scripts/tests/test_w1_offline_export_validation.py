"""TOOL_ONLY mutation checks; these never run Docker, UI, DB or financial paths."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import export_evidence as E
from scripts import w1_offline_export_validation as candidate

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fixture_root():
    # Windows sandbox denies pytest's mode0700 system-temp fixture. Use one new
    # workspace-local default-mode directory; retain it without cleanup/overwrite.
    root = REPO_ROOT / ".runtime/W1-offline-export-integration-fixtures" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def complete_flags():
    return {
        "evidence_kind": "TOOL_ONLY",
        "protocol": "bounded-funds-offline-browser-v1",
        "run_id": "offline-tool-only",
        "status": "FOUR_OFFLINE_UI_CASES_VERIFIED",
        "exit_code": 0,
        "playwright_exit_code": 0,
        "source_before": {"sample.py": "a" * 64},
        "source_after": {"sample.py": "a" * 64},
        "tool_source_before": {"checker.py": "b" * 64},
        "tool_source_after": {"checker.py": "b" * 64},
        "source_head": "c" * 40,
        "source_head_after": "c" * 40,
        "source_equal": True,
        "task_closed": False,
        "complete_all7": False,
        "three_rounds": "NOT_RUN",
        "case_denominator": 4,
        "offline_scope": "THREE_GOLDEN_CHAINS_WITH_FIXED_LOSS_CASE_NOT_ALL7_OR_THREE_ROUNDS",
        "started_at": "2026-10-05T06:00:00+00:00",
        "finished_at": "2026-10-05T06:01:00+00:00",
    }


def test_synthetic_success_flags_cannot_enter_product_gate():
    with pytest.raises(ValueError, match="TOOL_ONLY"):
        candidate.manifest_gate(complete_flags(), "offline-tool-only", "c" * 40)


def test_flag_gate_only_admits_next_tool_only_gate():
    assert (
        candidate.manifest_gate(complete_flags(), "offline-tool-only", "c" * 40, tool_only=True)
        is None
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "PASSED"),
        ("exit_code", True),
        ("exit_code", 1),
        ("playwright_exit_code", None),
        ("source_after", {}),
        ("tool_source_after", {}),
        ("source_head_after", "d" * 40),
        ("source_equal", False),
        ("task_closed", True),
        ("complete_all7", True),
        ("three_rounds", "PASSED"),
        ("case_denominator", 3),
        ("case_denominator", True),
        ("offline_scope", "OFFLINE"),
        ("finished_at", "2026-10-05T05:59:00+00:00"),
    ],
)
def test_success_strings_do_not_bypass_source_denominator_and_actual_exit(key, value):
    manifest = complete_flags()
    manifest[key] = value
    with pytest.raises(ValueError):
        candidate.manifest_gate(manifest, "offline-tool-only", "c" * 40, tool_only=True)


def checkpoint_fixture():
    titles = ("salary", "new goal income", "lossless recovery", "separate fixed loss")
    cases = {title: "case" + str(index) for index, title in enumerate(titles)}
    checkpoints = [
        {"scenario_id": "w1-" + cases[title], "label": label, "mode": mode}
        for title, rows in zip(titles, candidate.CHECKPOINTS, strict=True)
        for label, mode in rows
    ]
    return titles, cases, checkpoints


def test_full_fixed_checkpoint_denominator_is_22_and_not_a_financial_result():
    titles, cases, checkpoints = checkpoint_fixture()
    assert len(checkpoints) == 22
    assert candidate.checkpoint_denominator(checkpoints, cases, titles) is None


@pytest.mark.parametrize(
    "mutation", ["omit", "duplicate", "foreign", "reset_is_money", "reorder", "missing_goal_income"]
)
def test_fixed_actual_chain_gates_cannot_be_omitted_or_renamed(mutation):
    titles, cases, checkpoints = checkpoint_fixture()
    if mutation == "omit":
        checkpoints.pop()
    elif mutation == "duplicate":
        checkpoints.append(copy.deepcopy(checkpoints[-1]))
    elif mutation == "foreign":
        checkpoints[-1]["scenario_id"] = "w1-foreign"
    elif mutation == "reset_is_money":
        checkpoints[1]["mode"] = "MONEY"
    elif mutation == "reorder":
        checkpoints[0], checkpoints[1] = checkpoints[1], checkpoints[0]
    else:
        row = next(row for row in checkpoints if row["label"] == "new-goal-later-fixed-income")
        row["label"] = "renamed-success"
    with pytest.raises(ValueError):
        candidate.checkpoint_denominator(checkpoints, cases, titles)


def capture_fixture(tmp_path):
    raw_private, raw_public = b"TOOL_ONLY original bytes", b"TOOL_ONLY transformed bytes"
    prepared = []
    metadata = {"label": "capture", "public_transformation": "REDACTED_REENCODING_V1_NOT_ORIGINAL"}
    for prefix, raw in (("private_original", raw_private), ("public", raw_public)):
        name = ".runtime/tool-only/" + prefix + ".original"
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        prepared.append(
            (
                {
                    "artifact_id": prefix,
                    "resolved_repository_path": name,
                    "run_id": "offline-tool-only",
                    "source_context": "CURRENT_SOURCE",
                    "evidence_kind": "TOOL_ONLY",
                },
                raw,
            )
        )
        metadata.update(
            {
                prefix + "_path": name,
                prefix + "_sha256": hashlib.sha256(raw).hexdigest(),
                prefix + "_bytes": len(raw),
            }
        )
    return E.Originals(tmp_path, {}, prepared, tool_test_only=True), {"artifacts": [metadata]}


def test_public_transform_is_distinct_from_original_and_both_explicitly_bound(fixture_root):
    proof, manifest = capture_fixture(fixture_root)
    assert candidate.bind_capture(proof, manifest) is None


@pytest.mark.parametrize(
    "mutation", ["unregistered", "hash", "size_bool", "transform_claim", "duplicate"]
)
def test_capture_paths_hashes_bytes_and_provenance_cannot_be_weakened(fixture_root, mutation):
    proof, manifest = capture_fixture(fixture_root)
    row = manifest["artifacts"][0]
    if mutation == "unregistered":
        proof.allowed = {"private_original"}
    elif mutation == "hash":
        row["private_original_sha256"] = "f" * 64
    elif mutation == "size_bool":
        row["private_original_bytes"] = True
    elif mutation == "transform_claim":
        row["public_transformation"] = "UNMODIFIED_ORIGINAL"
    else:
        manifest["artifacts"].append(copy.deepcopy(row))
    with pytest.raises((ValueError, E.MissingProof)):
        candidate.bind_capture(proof, manifest)
