"""TOOL_ONLY corpus fixtures; no real cases, oracle effects, PostgreSQL or browser."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts.mvp_corpus import (
    ARMS,
    CASE_PROTOCOL,
    DRAFT_PROTOCOL,
    MVP_QUOTAS,
    CorpusError,
    Draft,
    canonical,
    digest,
    freeze,
    independent_python,
    main,
    normalized_fingerprint,
    quotas,
    read_json,
    runtime_kinds,
    validate,
    verify,
)

WHEN = "2026-10-05T00:00:00+00:00"


@pytest.fixture
def tmp_path() -> Path:
    base = Path(__file__).resolve().parents[2] / ".runtime" / "W1-corpus-tool-fixtures"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir(exist_ok=False)
    return path


def original(root: Path, name: str, value: Any) -> dict[str, str]:
    data = canonical(value)
    (root / name).write_bytes(data)
    return {"path": name, "sha256": digest(data)}


def case(case_id: str, *, purpose: str = "TOOL_ONLY", count: int = 2) -> dict[str, Any]:
    return {
        "protocol": CASE_PROTOCOL,
        "case_id": case_id,
        "family_id": "TOOL_FAMILY",
        "intended_purpose": purpose,
        "seed_version": "mvp-301-v6",
        "initial_state": {"mode": "SEED_NEW"},
        "steps": [
            {
                "step_id": f"tool-step-{index}",
                "kind": "TOOL_STEP",
                "at": WHEN,
                "inputs": {"kind": "CREDIT", "amount_cents": 12 + index},
                "fault": "NONE",
            }
            for index in range(count)
        ],
        "data_origin": {
            "kind": "TOOL_ONLY",
            "scope": "Parser/byte/arithmetic fixtures, not financial case data",
        },
    }


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.current = root / "current"
        self.current.mkdir()
        self.code = (
            b"# TOOL_ONLY, not a financial oracle or service adapter\n"
            b"def independent_count(rows):\n    return len(rows)\n"
        )
        (self.current / "contract.py").write_bytes(self.code)
        (root / "oracle.py").write_bytes(self.code)
        self.oracle_source = {"path": "oracle.py", "sha256": digest(self.code)}
        self.source: dict[str, Any] = {
            "purpose": "TOOL_ONLY",
            "files": [{"path": "contract.py", "sha256": digest(self.code)}],
        }
        self.seed: dict[str, Any] = {
            "purpose": "TOOL_ONLY",
            "seed_version": "mvp-301-v6",
            "seed_source_sha256": digest(self.code),
        }
        self.schema = {
            "purpose": "TOOL_ONLY",
            "contract_source_sha256": digest(self.code),
            "contract_status": "SUPPORTED_INPUTS_REGISTERED",
            "initial_state_schema": {
                "type": "object",
                "required": ["mode"],
                "properties": {"mode": {"type": "string", "const": "SEED_NEW"}},
                "additionalProperties": False,
            },
            "step_schemas": {
                "TOOL_STEP": {
                    "type": "object",
                    "required": ["kind", "amount_cents"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["CREDIT", "DEBIT"]},
                        "amount_cents": {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                }
            },
            "faults": ["NONE"],
        }
        self.design = {"purpose": "TOOL_ONLY", "scope": "No real case quota or runtime effect"}
        self.development = case("tool-dev", purpose="DEVELOPMENT", count=1)
        self.inputs = [case("tool-case-1")]
        self.oracle_overrides: dict[str, Any] = {}
        self.rules: dict[str, dict[str, Any]] = {
            arm: {
                "purpose": "TOOL_ONLY",
                "arm_id": arm,
                "mechanism_id": f"tool-mechanism-{arm}",
                "execution_mode": "TOOL_ONLY",
                "implementation_source_refs": [self.oracle_source],
            }
            for arm in ARMS
        }
        self.manifest: dict[str, Any] = {
            "protocol": DRAFT_PROTOCOL,
            "purpose": "TOOL_ONLY",
            "profile": "TOOL",
        }
        self.save()

    def save(self) -> Path:
        facts = original(
            self.root,
            "initial-facts.json",
            {"purpose": "TOOL_ONLY", "scope": "No actual bank state"},
        )
        self.seed["initial_fact_refs"] = [facts]
        for name, value in (
            ("schema", self.schema),
            ("seed", self.seed),
            ("source", self.source),
            ("design", self.design),
        ):
            self.manifest[f"{name}_ref"] = original(self.root, f"{name}.json", value)
        development = original(self.root, "development-input.json", self.development)
        self.manifest["development_inventory_ref"] = original(
            self.root,
            "development-inventory.json",
            {
                "purpose": "DEVELOPMENT",
                "inventory_status": "REGISTERED_COMPLETE",
                "inputs": [development],
            },
        )
        rule_refs = {
            arm: original(self.root, f"rule-{arm}.json", rule) for arm, rule in self.rules.items()
        }
        registrations = []
        for index, value in enumerate(self.inputs):
            value["seed_sha256"] = self.manifest["seed_ref"]["sha256"]
            value["source_sha256"] = self.manifest["source_ref"]["sha256"]
            input_ref = original(self.root, f"input-{index}.json", value)
            oracle = {
                "case_id": value["case_id"],
                "purpose": "TOOL_ONLY",
                "input_sha256": input_ref["sha256"],
                "seed_sha256": self.manifest["seed_ref"]["sha256"],
                "source_sha256": self.manifest["source_ref"]["sha256"],
                "design_sha256": self.manifest["design_ref"]["sha256"],
                "rule_sha256_by_arm": {arm: ref["sha256"] for arm, ref in rule_refs.items()},
                "implementation_origin": "INDEPENDENT_INTEGER_ORACLE",
                "source_refs": [self.oracle_source],
                "metric_calculators": {"E2": "independent_count"},
                "protection_timeline": [],
                "permission_intervals": [],
                "due_checkpoints": [],
                "safe_auto_opportunity_ids": [],
                "required_evidence_manifest": [],
                "audit_checkpoint_ids": [],
                "expected_causes": [],
            }
            oracle.update(self.oracle_overrides)
            oracle_ref = original(self.root, f"oracle-{index}.json", oracle)
            registrations.append(
                {
                    "case_id": value["case_id"],
                    "family_id": value["family_id"],
                    "input_ref": input_ref,
                    "oracle_ref": oracle_ref,
                    "rule_refs": rule_refs,
                }
            )
        self.manifest["cases"] = registrations
        original(self.root, "draft.json", self.manifest)
        return self.root / "draft.json"


def test_tool_structure_freeze_and_verified_bytes_are_not_runtime_effects(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    result = validate(tmp_path / "draft.json", fixture.current)
    assert result["status"] == "VALID_CORPUS_STRUCTURE", result
    assert result["purpose"] == "TOOL_ONLY" and result["financial_effect_evidence"] is False
    destination = tmp_path / "freeze-v1"
    frozen = freeze(tmp_path / "draft.json", destination, fixture.current)
    assert frozen["purpose"] == "TOOL_ONLY" and frozen["financial_effect_evidence"] is False
    checked = verify(destination, fixture.current, frozen["manifest_sha256"])
    assert checked["status"] == "VERIFIED_FROZEN_BYTES_AND_CURRENT_SOURCE", checked
    assert checked["financial_effect_evidence"] is False


def test_mvp_quota_is_exact_24_with_the_six_original_counts() -> None:
    families = [family for family, count in MVP_QUOTAS.items() for _ in range(count)]
    assert len(families) == 24
    quotas(families, MVP_QUOTAS)
    with pytest.raises(CorpusError, match="quotas differ"):
        quotas(families[:-1], MVP_QUOTAS)
    with pytest.raises(CorpusError, match="quotas differ"):
        quotas(families + ["EXTRA"], MVP_QUOTAS)


def test_full_family_quota_is_driven_by_explicit_actual_schema() -> None:
    quotas(["F-A", "F-A", "F-B"], {"F-A": 2, "F-B": 1})
    with pytest.raises(CorpusError, match="quotas differ"):
        quotas(["F-A", "F-B"], {"F-A": 2, "F-B": 1})
    with pytest.raises(CorpusError, match="positive integer"):
        quotas([], {})


def test_amount_uuid_title_changes_alone_do_not_create_new_development_flow(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    candidate = copy.deepcopy(fixture.development)
    candidate.update(case_id="renamed-tool-case", intended_purpose="TOOL_ONLY", title="new title")
    candidate["steps"][0].update(step_id="renamed-step", at="2026-10-06T00:00:00+00:00")
    candidate["steps"][0]["inputs"]["amount_cents"] = 999
    fixture.inputs = [candidate]
    fixture.save()
    result = validate(tmp_path / "draft.json", fixture.current)
    assert result["status"] == "INVALID" and "development flow" in result["reason"]
    assert not (tmp_path / "actual-mvp-freeze").exists()


def test_normalization_preserves_topology_boolean_and_reference_relationships() -> None:
    baseline = case("fixture")
    renamed = copy.deepcopy(baseline)
    renamed["steps"][0]["step_id"] = "changed-name"
    assert normalized_fingerprint(baseline, flow=True) == normalized_fingerprint(renamed, flow=True)
    renamed["steps"][1]["inputs"]["accepted"] = True
    assert normalized_fingerprint(baseline, flow=True) != normalized_fingerprint(renamed, flow=True)
    reference = copy.deepcopy(baseline)
    reference["steps"][1]["inputs"] = {"$ref": "tool-step-0#/result/id"}
    second = copy.deepcopy(reference)
    second["steps"][0]["step_id"] = "renamed-prior"
    second["steps"][1]["inputs"]["$ref"] = "renamed-prior#/result/id"
    assert normalized_fingerprint(reference, flow=True) == normalized_fingerprint(second, flow=True)


def test_new_case_cannot_only_relabel_another_new_input(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    duplicate = copy.deepcopy(fixture.inputs[0])
    duplicate["case_id"] = "another-tool-id"
    duplicate["title"] = "new title"
    fixture.inputs.append(duplicate)
    fixture.save()
    result = validate(tmp_path / "draft.json", fixture.current)
    assert result["status"] == "INVALID" and "identifiers/titles" in result["reason"]


@pytest.mark.parametrize(
    "kind", ["unsupported", "missing_field", "naive_clock", "backwards_clock", "unsupported_fault"]
)
def test_missing_or_unsupported_actual_runner_inputs_are_invalid(tmp_path: Path, kind: str) -> None:
    fixture = Fixture(tmp_path)
    steps = fixture.inputs[0]["steps"]
    if kind == "unsupported":
        steps[0]["kind"] = "RUN_RECOVERY"
    elif kind == "missing_field":
        del steps[0]["inputs"]["amount_cents"]
    elif kind == "naive_clock":
        steps[0]["at"] = "2026-10-05T00:00:00"
    elif kind == "backwards_clock":
        steps[1]["at"] = "2026-10-04T00:00:00+00:00"
    else:
        steps[0]["fault"] = "UNIMPLEMENTED_RECOVERY_FAULT"
    fixture.save()
    assert validate(tmp_path / "draft.json", fixture.current)["status"] == "INVALID"


def test_missing_original_and_current_source_drift_are_invalid(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.manifest["seed_ref"]["path"] = "never-captured.json"
    original(tmp_path, "draft.json", fixture.manifest)
    assert "Missing original" in validate(tmp_path / "draft.json", fixture.current)["reason"]
    fixture.save()
    (fixture.current / "contract.py").write_bytes(b"# TOOL_ONLY changed source\n")
    result = validate(tmp_path / "draft.json", fixture.current)
    assert result["status"] == "INVALID" and "Current source drift" in result["reason"]


def test_source_paths_cannot_escape_explicit_root(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.source["files"][0]["path"] = "../oracle.py"
    fixture.save()
    assert "escapes" in validate(tmp_path / "draft.json", fixture.current)["reason"]


@pytest.mark.parametrize(
    "binding",
    ["input_sha256", "seed_sha256", "source_sha256", "design_sha256", "rule_sha256_by_arm"],
)
def test_oracle_exact_original_binding_rejects_rebound_source_or_rules(
    tmp_path: Path, binding: str
) -> None:
    fixture = Fixture(tmp_path)
    fixture.oracle_overrides[binding] = "a" * 64 if binding != "rule_sha256_by_arm" else {}
    fixture.save()
    assert validate(tmp_path / "draft.json", fixture.current)["status"] == "INVALID"


@pytest.mark.parametrize(
    "code",
    [
        (
            b"from app.services.boundary import compute_user_boundary\n"
            b"def oracle(x): return compute_user_boundary(x)\n"
        ),
        b"import importlib\ndef oracle(x): return x\n",
        b"def oracle(x): return __import__('app.services.execution')\n",
        b"def oracle(x): return eval(x)\n",
    ],
)
def test_independent_oracle_cannot_import_p_or_dynamic_evaluators(code: bytes) -> None:
    with pytest.raises(CorpusError):
        independent_python(code)


def test_runtime_capabilities_need_both_actual_typed_and_dispatched_kinds() -> None:
    contract = (
        b"from typing import Literal\nclass ScenarioStep:\n"
        b"    kind: Literal['EXECUTE_ACTION', 'RUN_RECOVERY']\n"
    )
    runner = (
        b"def _dispatch(step):\n    if step.kind == 'EXECUTE_ACTION':\n"
        b"        return {'scope': 'TOOL_ONLY'}\n"
    )
    assert runtime_kinds(contract, runner) == {"EXECUTE_ACTION"}
    with pytest.raises(CorpusError, match="No actual typed"):
        runtime_kinds(b"# TOOL_ONLY nonexistent typed kinds", runner)


def test_five_groups_cannot_relabel_one_mechanism(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.rules["B1"]["mechanism_id"] = fixture.rules["B0"]["mechanism_id"]
    fixture.save()
    assert "relabel" in validate(tmp_path / "draft.json", fixture.current)["reason"]


def test_purpose_cannot_silently_convert_tool_or_development_into_mvp(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.manifest.update(purpose="MVP_FROZEN", profile="MVP")
    fixture.save()
    result = validate(tmp_path / "draft.json", fixture.current)
    assert result["status"] == "INVALID" and "another corpus purpose" in result["reason"]


def test_second_freeze_refuses_to_modify_old_manifest_and_inputs(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    destination = tmp_path / "freeze-v1"
    freeze(tmp_path / "draft.json", destination, fixture.current)
    before = {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
    with pytest.raises(CorpusError, match="already exists"):
        freeze(tmp_path / "draft.json", destination, fixture.current)
    assert all(path.read_bytes() == data for path, data in before.items())


def test_archive_and_manifest_tampering_require_external_digest_and_actual_bytes(
    tmp_path: Path,
) -> None:
    fixture = Fixture(tmp_path)
    destination = tmp_path / "freeze-v1"
    frozen = freeze(tmp_path / "draft.json", destination, fixture.current)
    assert verify(destination, fixture.current, "a" * 64)["status"] == "INVALID"
    manifest = read_json((destination / "manifest.json").read_bytes())
    path = destination / manifest["files"][0]["path"]
    path.write_bytes(b"TOOL_ONLY corrupted archived original")
    result = verify(destination, fixture.current, frozen["manifest_sha256"])
    assert result["status"] == "INVALID" and "byte/hash/size" in result["reason"]


def test_verify_requires_current_source_without_drift(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    destination = tmp_path / "freeze-v1"
    frozen = freeze(tmp_path / "draft.json", destination, fixture.current)
    (fixture.current / "contract.py").write_bytes(b"# TOOL_ONLY source changed after freeze")
    result = verify(destination, fixture.current, frozen["manifest_sha256"])
    assert result["status"] == "INVALID" and "Current source drift" in result["reason"]


def test_unregistered_nested_manifest_cannot_hide_from_file_inventory(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    destination = tmp_path / "freeze-v1"
    frozen = freeze(tmp_path / "draft.json", destination, fixture.current)
    (destination / "originals" / "manifest.json").write_bytes(b"TOOL_ONLY unregistered nested file")
    assert verify(destination, fixture.current, frozen["manifest_sha256"])["status"] == "INVALID"


def test_mid_freeze_failure_is_retained_and_never_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Fixture(tmp_path)
    original_guard = Draft.unchanged
    calls = 0

    def interrupted(self: Draft) -> None:
        nonlocal calls
        calls += 1
        original_guard(self)
        if calls == 2:
            raise CorpusError("TOOL_ONLY injected source drift after copy")

    monkeypatch.setattr(Draft, "unchanged", interrupted)
    destination = tmp_path / "failed-freeze-v1"
    with pytest.raises(CorpusError, match="after copy"):
        freeze(tmp_path / "draft.json", destination, fixture.current)
    failure = (destination / "INCOMPLETE.json").read_bytes()
    assert not (destination / "manifest.json").exists()
    with pytest.raises(CorpusError, match="already exists"):
        freeze(tmp_path / "draft.json", destination, fixture.current)
    assert (destination / "INCOMPLETE.json").read_bytes() == failure


def test_cli_reports_invalid_without_overwriting_prior_result(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    report = tmp_path / "validation.json"
    assert (
        main(
            [
                "validate",
                "--input",
                str(tmp_path / "draft.json"),
                "--source-root",
                str(fixture.current),
                "--output",
                str(report),
            ]
        )
        == 0
    )
    before = report.read_bytes()
    with pytest.raises(SystemExit):
        main(
            [
                "validate",
                "--input",
                str(tmp_path / "draft.json"),
                "--source-root",
                str(fixture.current),
                "--output",
                str(report),
            ]
        )
    assert report.read_bytes() == before


def test_duplicate_json_and_nonfinite_originals_are_rejected() -> None:
    with pytest.raises(CorpusError, match="Duplicate JSON"):
        read_json(b'{"x":1,"x":2}')
    with pytest.raises(CorpusError, match="Nonfinite"):
        read_json(b'{"x":NaN}')
