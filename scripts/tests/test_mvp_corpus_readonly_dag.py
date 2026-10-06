"""TOOL_ONLY corpus/readonly-registration bridge; never calculator or finance execution."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".runtime/W1-corpus-readonly-DAG-installed-TOOL_ONLY-fixtures"
from scripts import mvp_corpus_v2 as TOOL  # noqa: E402
from scripts.mvp_readonly_oracle_dag import (  # noqa: E402
    METHOD,
    build_registration,
    sha,
    strict_json,
)


class Originals:
    def __init__(self) -> None:
        self.root = HERE / "TOOL_ONLY-fixtures" / uuid4().hex
        path = self.root / "scripts/mvp_observations.py"
        path.parent.mkdir(parents=True, exist_ok=False)
        path.write_text("import json\ndef observe():\n    return {}\n", encoding="utf-8")
        raw, files = build_registration(self.root, {"E2", "E5"})
        registration = strict_json(raw)
        self.sources = {row["path"]: row["sha256"] for row in registration["source_files"]}
        self.oracle = {
            "metric_calculator_mode": METHOD,
            "readonly_registration_ref": self.store("registration.json", raw),
            "readonly_source_archive_refs": {
                name: self.store("archives/" + name, data) for name, data in files.items()
            },
            "metric_calculators": registration["metric_bindings"],
        }

    def store(self, name: str, raw: bytes) -> dict[str, str]:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        return {"path": name, "sha256": sha(raw)}

    def load(self, value: Any, *, parse: bool = True) -> Any:
        assert set(value) == {"path", "sha256"}
        path = TOOL.inside(self.root, value["path"])
        raw = path.read_bytes()
        if sha(raw) != value["sha256"]:
            raise TOOL.CorpusError("Original artifact byte hash differs")
        return json.loads(raw) if parse else raw

    def verify(self, purpose: str = "TOOL_ONLY") -> Any:
        return TOOL.validate_readonly_calculators(
            self.oracle,
            source_root=self.root,
            source_paths=self.sources,
            load_ref=self.load,
            purpose=purpose,
        )


def test_explicit_readonly_mode_registers_output_slots_without_running() -> None:
    originals = Originals()
    proof = originals.verify()
    assert proof["status"] == "REGISTERED_NOT_EXECUTED"
    assert proof["metric_ids"] == ["E2", "E5"]
    assert proof["financial_effect_evidence"] is False
    assert proof["registration_sha256"] == originals.oracle["readonly_registration_ref"]["sha256"]


@pytest.mark.parametrize("mode", [None, "STDLIB_SINGLE_FILE_V1", "UNKNOWN_MODE", [], {}])
def test_new_registration_cannot_enter_legacy_or_unknown_mode(mode: Any) -> None:
    originals = Originals()
    originals.oracle["metric_calculator_mode"] = mode
    with pytest.raises(TOOL.CorpusError):
        originals.verify()


def test_legacy_without_new_refs_returns_to_original_strict_check() -> None:
    originals = Originals()
    for mode in (None, "STDLIB_SINGLE_FILE_V1"):
        assert (
            TOOL.validate_readonly_calculators(
                {"metric_calculator_mode": mode},
                source_root=originals.root,
                source_paths={},
                load_ref=lambda *a, **k: pytest.fail("legacy must not read DAG"),
                purpose="TOOL_ONLY",
            )
            is None
        )


@pytest.mark.parametrize("changed", ["module", "function", "pointer"])
def test_original_metric_module_function_pointer_cannot_be_rebound(changed: str) -> None:
    originals = Originals()
    originals.oracle["metric_calculators"]["E2"][changed] = "unexpected"
    with pytest.raises(TOOL.CorpusError, match="rebound"):
        originals.verify()


def test_readonly_dependency_requires_actual_current_source_inventory() -> None:
    originals = Originals()
    originals.sources.clear()
    with pytest.raises(TOOL.CorpusError, match="source inventory"):
        originals.verify()


@pytest.mark.parametrize("change", ["missing", "extra", "changed"])
def test_original_archive_is_exact_complete_and_not_self_hashing(change: str) -> None:
    originals = Originals()
    refs = originals.oracle["readonly_source_archive_refs"]
    if change == "missing":
        refs.clear()
    elif change == "extra":
        refs["unexpected.py"] = originals.store("unexpected.py", b"pass\n")
    else:
        descriptor = next(iter(refs.values()))
        path = originals.root / descriptor["path"]
        path.write_bytes(b"pass\n")
        descriptor["sha256"] = sha(path.read_bytes())
    with pytest.raises(TOOL.CorpusError):
        originals.verify()


def test_changed_external_registration_hash_is_rejected() -> None:
    originals = Originals()
    originals.oracle["readonly_registration_ref"]["sha256"] = "0" * 64
    with pytest.raises(TOOL.CorpusError, match="byte hash"):
        originals.verify()


def test_two_slot_tool_registration_cannot_meet_formal_fourteen_requirement() -> None:
    originals = Originals()
    with pytest.raises(TOOL.CorpusError, match="Fourteen"):
        originals.verify("MVP_FROZEN")
