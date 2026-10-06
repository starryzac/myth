"""TOOL_ONLY frozen-byte locator risks, never formal cases or financial measurements."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from scripts import mvp_observation_v2 as v2
from scripts.mvp_observations import BINDINGS, METRIC_IDS, Bundle, ObservationError

ROOT = Path(__file__).resolve().parents[2]
PURPOSE = "MVP_FROZEN"


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def tmp_path() -> Path:
    path = ROOT / ".runtime/W1-observation-v2-TOOL_ONLY-fixtures" / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


class Fixture:
    """Typed-proof doubles explicitly limited to pure locator/parser risk tests."""

    def __init__(self, root: Path):
        self.root = root
        self.source = root / "current"
        self.archive = root / "frozen"
        self.run = root / "run"
        for path in (self.source, self.archive / "originals", self.run):
            path.mkdir(parents=True)
        self.files: list[dict[str, Any]] = []
        self.physical: dict[str, Path] = {}
        self.sources: dict[str, str] = {}
        for name in sorted(
            v2.READONLY_SOURCES
            | {"apps/api/app/main.py", "apps/api/alembic/env.py", "pyproject.toml", "alembic.ini"}
        ):
            content = b"# TOOL_ONLY source; no executable financial result\n"
            self.add_source(name, content)
        lock = b"".join(
            (f'[[package]]\nname = "{name}"\nversion = "1"\n'.encode())
            for name in (
                "pydantic",
                "pydantic-core",
                "annotated-types",
                "typing-extensions",
                "typing-inspection",
            )
        )
        self.add_source("uv.lock", lock)
        source_ref = self.original(
            "SOURCE.json",
            {
                "purpose": PURPOSE,
                "files": [
                    {"path": name, "sha256": digest}
                    for name, digest in sorted(self.sources.items())
                ],
            },
        )
        design_ref = self.original(
            "DESIGN.json", {"purpose": PURPOSE, "status": "TOOL_ONLY_SCHEMA_SHAPE"}
        )
        facts_ref = self.original(
            "SEED-FACTS.json", {"purpose": PURPOSE, "financial_effect_evidence": False}
        )
        seed_ref = self.original(
            "SEED.json",
            {"purpose": PURPOSE, "seed_version": "mvp-301-v6", "initial_fact_refs": [facts_ref]},
        )
        rule_refs = {
            arm: self.original(
                arm + "-RULE.json",
                {
                    "purpose": PURPOSE,
                    "arm_id": arm,
                    "execution_mode": "SERVICE_INTEGRATION",
                    "mechanism_id": "TOOL_ONLY_" + arm,
                },
            )
            for arm in ("B0", "B1", "B2", "B3", "P")
        }
        fragment: dict[str, Any] = {
            "scenario_id": "TOOL_ONLY-LOCATORS",
            "purpose": PURPOSE,
            "steps": [],
            "frozen_case_sha256": None,
        }
        input_ref = self.original(
            "INPUT.json",
            {
                "protocol": v2.CASE_PROTOCOL,
                "case_id": "TOOL_ONLY-LOCATORS",
                "family_id": "NORMAL",
                "intended_purpose": PURPOSE,
                "seed_version": "mvp-301-v6",
                "source_sha256": source_ref["sha256"],
                "design_sha256": design_ref["sha256"],
                "seed_sha256": seed_ref["sha256"],
                "execution_input": fragment,
            },
        )
        runtime_file = Path(sys.prefix) / "pyvenv.cfg"
        assert runtime_file.is_file()
        runtime_bytes = runtime_file.read_bytes()
        archive_refs: dict[str, Any] = {}
        distributions = {}
        for name in (
            "pydantic",
            "pydantic-core",
            "annotated-types",
            "typing-extensions",
            "typing-inspection",
        ):
            distributions[name] = {
                "version": "1",
                "files": [
                    {
                        "path": "pyvenv.cfg",
                        "sha256": sha(runtime_bytes),
                        "bytes": len(runtime_bytes),
                    }
                ],
            }
            archive_refs["typed-runtime/" + name + "/pyvenv.cfg"] = self.retain(
                "typing-" + name + ".bin", runtime_bytes
            )
        # Actual native/DAG semantic validation belongs to the trusted outer
        # driver. These deliberately small doubles exercise byte binding only.
        calculators = {
            metric: {
                "module": "scripts.mvp_observations",
                "function": "observe",
                "pointer": "/metrics/" + metric,
            }
            for metric in METRIC_IDS
        }
        archive_refs.update(
            {
                name: self.retain(
                    "readonly-" + str(index) + ".bin", self.physical[name].read_bytes()
                )
                for index, name in enumerate(sorted(v2.READONLY_SOURCES))
            }
        )
        readonly_ref = self.original(
            "READONLY.json",
            {
                "protocol": "bounded-funds-readonly-oracle-dag-v2",
                "method": v2.READONLY_MODE,
                "metric_bindings": calculators,
                "source_files": [
                    {"path": name, "sha256": self.sources[name]}
                    for name in sorted(v2.READONLY_SOURCES)
                ],
                "framework": {
                    "protocol": "LOCKED_IMMUTABLE_TYPED_MODEL_RUNTIME_V1",
                    "python": sys.version,
                    "lock_sha256": self.sources["uv.lock"],
                    "distributions": distributions,
                },
            },
        )
        oracle_ref = self.original(
            "ORACLE.json",
            {
                "case_id": "TOOL_ONLY-LOCATORS",
                "purpose": PURPOSE,
                "source_sha256": source_ref["sha256"],
                "design_sha256": design_ref["sha256"],
                "seed_sha256": seed_ref["sha256"],
                "input_sha256": input_ref["sha256"],
                "rule_sha256_by_arm": {arm: ref["sha256"] for arm, ref in rule_refs.items()},
                "metric_calculator_mode": v2.READONLY_MODE,
                "metric_calculators": calculators,
                "readonly_registration_ref": readonly_ref,
                "readonly_source_archive_refs": archive_refs,
            },
        )
        draft_ref = self.original(
            "DRAFT.json",
            {
                "protocol": v2.DRAFT_PROTOCOL,
                "purpose": PURPOSE,
                "source_ref": source_ref,
                "design_ref": design_ref,
                "seed_ref": seed_ref,
                "cases": [
                    {
                        "case_id": "TOOL_ONLY-LOCATORS",
                        "family_id": "NORMAL",
                        "input_ref": input_ref,
                        "oracle_ref": oracle_ref,
                        "rule_refs": rule_refs,
                    }
                ],
            },
        )
        validated = dict(fragment, frozen_case_sha256=input_ref["sha256"])
        self.native = {
            "conversion_method": "ORIGINAL_CASE_INPUT_SHA_BINDING_V1",
            "original_case_input_sha256": input_ref["sha256"],
            "input_json_sha256": sha(encoded(fragment)),
            "converted_execution_input_sha256": sha(encoded(validated)),
            "validated_execution_input_sha256": sha(encoded(validated)),
            "validated_execution_input": validated,
        }
        self.frozen: dict[str, Any] = {
            "protocol": v2.FROZEN_PROTOCOL,
            "purpose": PURPOSE,
            "status": "IMMUTABLE_CORPUS_BYTES_REGISTERED",
            "draft_original_path": draft_ref["path"],
            "source_inventory": self.sources,
            "files": self.files,
            "cases": [
                {
                    "case_id": "TOOL_ONLY-LOCATORS",
                    "input_sha256": input_ref["sha256"],
                    "native_execution_binding": self.native,
                }
            ],
        }
        self.bindings = {
            "experiment_run_id": str(uuid4()),
            "case_id": "TOOL_ONLY-LOCATORS",
            "arm_id": "P",
            "execution_mode": "SERVICE_INTEGRATION",
            "input_sha256": input_ref["sha256"],
            "oracle_sha256": oracle_ref["sha256"],
            "design_sha256": design_ref["sha256"],
            "rule_sha256": rule_refs["P"]["sha256"],
            "source_sha256": source_ref["sha256"],
            "seed_version": "mvp-301-v6",
            "isolated_db_epoch": str(uuid4()),
            "purpose": PURPOSE,
            "user_id": str(uuid4()),
        }
        assert set(self.bindings) == set(BINDINGS)
        self.manifest: dict[str, Any] = {
            "protocol": v2.RUN_PROTOCOL_V2,
            **self.bindings,
            "run_status": "NOT_RUN",
            "isolated_database": "bf_test_" + uuid4().hex,
            "artifact_refs": {
                "input": input_ref,
                "oracle": oracle_ref,
                "design": design_ref,
                "rule": rule_refs["P"],
                "source": source_ref,
            },
            "raw_refs": [],
        }
        self.sync()

    def retain(self, name: str, content: bytes, *, source: str | None = None) -> dict[str, Any]:
        path = self.archive / "originals" / (f"{len(self.files):05d}-" + name.replace("/", "_"))
        path.write_bytes(content)
        self.files.append(
            {
                "path": path.relative_to(self.archive).as_posix(),
                "sha256": sha(content),
                "size_bytes": len(content),
                "current_source_relative_path": source,
                "original_artifact_relative_path": None if source else name,
            }
        )
        self.physical[source or name] = path
        return {"path": source or name, "sha256": sha(content)}

    def original(self, name: str, value: dict[str, Any]) -> dict[str, Any]:
        return self.retain(name, encoded(value))

    def add_source(self, name: str, content: bytes) -> None:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.sources[name] = sha(content)
        self.retain(name, content, source=name)

    def sync(self) -> None:
        path = self.archive / "manifest.json"
        path.write_bytes(encoded(self.frozen))
        self.manifest["frozen_archive"] = {
            "manifest_path": str(path),
            "manifest_sha256": sha(path.read_bytes()),
        }
        result = {
            "protocol": "bounded-funds-frozen-runtime-input-v2",
            "status": "RUNTIME_INPUT_REVALIDATED_NOT_EXECUTED",
            "purpose": PURPOSE,
            "case_id": self.bindings["case_id"],
            "manifest_sha256": sha(path.read_bytes()),
            "original_case_input_sha256": self.bindings["input_sha256"],
            "validated_execution_input_sha256": self.native["validated_execution_input_sha256"],
            "source_inventory": self.sources,
        }
        raw = encoded(
            {
                "protocol": v2.RAW_PROTOCOL_V2,
                "kind": "FROZEN_RUNTIME_REVALIDATION",
                "bindings": self.bindings,
                "payload": {"result": result},
            }
        )
        proof = self.run / "pre-run.json"
        proof.write_bytes(raw)
        ref = {"path": proof.name, "sha256": sha(raw)}
        self.manifest["trusted_outer_validation_ref"] = ref
        self.manifest["raw_refs"] = [{**ref, "kind": "FROZEN_RUNTIME_REVALIDATION"}]
        (self.run / "manifest.json").write_bytes(encoded(self.manifest))

    def read(self) -> v2.FrozenOriginalView:
        return v2.FrozenOriginalView(
            self.manifest, self.run, self.bindings, source_root=self.source
        )


def test_unmodified_globals_and_whole_input_byte_binding(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    view = fixture.read()
    assert "case_id" not in view.registrations["source"]
    assert "case_id" not in view.registrations["design"]
    assert view.registrations["input"]["protocol"] == v2.CASE_PROTOCOL
    assert "registration_status" not in view.registrations["input"]
    assert view.archive_sha256 == fixture.manifest["frozen_archive"]["manifest_sha256"]
    assert (
        view.case_binding["native_execution_binding"]["validated_execution_input"][
            "frozen_case_sha256"
        ]
        == fixture.bindings["input_sha256"]
    )
    view.unchanged()


def test_bundle_uses_v2_original_view_without_v1_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Fixture(tmp_path)
    original_class = v2.FrozenOriginalView

    def view(
        manifest: dict[str, Any], run_root: Path, bindings: dict[str, str]
    ) -> v2.FrozenOriginalView:
        return original_class(manifest, run_root, bindings, source_root=fixture.source)

    monkeypatch.setattr(v2, "FrozenOriginalView", view)
    bundle = Bundle(fixture.run / "manifest.json")
    assert bundle.v2
    assert bundle.registrations["source"]["files"][0]["path"].startswith("alembic")
    assert bundle.kinds("FROZEN_RUNTIME_REVALIDATION")
    bundle.unchanged()


@pytest.mark.parametrize("binding", BINDINGS)
def test_every_binding_rejected_when_rebound(tmp_path: Path, binding: str) -> None:
    fixture = Fixture(tmp_path)
    fixture.bindings[binding] = "REBOUND"
    with pytest.raises((ObservationError, KeyError)):
        fixture.read()


@pytest.mark.parametrize("kind", ("input", "oracle", "design", "source", "rule"))
def test_registration_locator_cannot_wrap_or_rebind_original(tmp_path: Path, kind: str) -> None:
    fixture = Fixture(tmp_path)
    fixture.manifest["artifact_refs"][kind] = {
        "path": "invented-v1-wrapper.json",
        "sha256": fixture.bindings[kind + "_sha256"],
    }
    with pytest.raises(ObservationError, match="rebound"):
        fixture.read()


def test_all_original_inventory_bytes_checked_not_only_selected_case(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.physical["B0-RULE.json"].write_bytes(b"altered unrelated arm")
    with pytest.raises(ObservationError, match="SHA differs"):
        fixture.read()


@pytest.mark.parametrize(
    "change",
    (
        "extra_file",
        "extra_directory",
        "missing_file",
        "duplicate_file",
        "byte_count",
        "source_omit",
        "current_changed",
        "added_production",
        "source_alias",
    ),
)
def test_full_original_and_current_inventory_is_exact(tmp_path: Path, change: str) -> None:
    fixture = Fixture(tmp_path)
    if change == "extra_file":
        (fixture.archive / "unregistered.json").write_bytes(b"{}")
    elif change == "extra_directory":
        (fixture.archive / "unregistered").mkdir()
    elif change == "missing_file":
        fixture.physical["SEED-FACTS.json"].unlink()
    elif change == "duplicate_file":
        fixture.files.append(copy.deepcopy(fixture.files[0]))
    elif change == "byte_count":
        fixture.files[0]["size_bytes"] = True
    elif change == "source_omit":
        del fixture.frozen["source_inventory"]["alembic.ini"]
    elif change == "current_changed":
        (fixture.source / "scripts/mvp_observations.py").write_bytes(b"changed")
    elif change == "added_production":
        (fixture.source / "apps/api/app/new.py").write_bytes(b"# new production source")
    elif change == "source_alias":
        duplicate = copy.deepcopy(fixture.files[0])
        duplicate["path"] = "originals/alias.bin"
        duplicate["current_source_relative_path"] = "alembic.ini/../alembic.ini"
        (fixture.archive / duplicate["path"]).write_bytes(
            fixture.physical["alembic.ini"].read_bytes()
        )
        fixture.files.append(duplicate)
    fixture.sync()
    with pytest.raises(ObservationError):
        fixture.read()


@pytest.mark.parametrize(
    "field",
    (
        "original_case_input_sha256",
        "input_json_sha256",
        "converted_execution_input_sha256",
        "validated_execution_input_sha256",
        "conversion_method",
    ),
)
def test_native_byte_proof_cannot_rebind_sha(tmp_path: Path, field: str) -> None:
    fixture = Fixture(tmp_path)
    fixture.native[field] = "0" * 64
    fixture.sync()
    with pytest.raises(ObservationError, match="binding|SHA"):
        fixture.read()


def test_initial_purpose_cannot_promote_development(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.bindings["purpose"] = "DEVELOPMENT"
    with pytest.raises(ObservationError, match="cannot be promoted"):
        fixture.read()


def test_outer_proof_must_be_original_registered_and_exact(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    fixture.manifest["raw_refs"] = []
    with pytest.raises(ObservationError, match="not a registered"):
        fixture.read()


def test_outer_proof_stale_source_or_case_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    proof = json.loads((fixture.run / "pre-run.json").read_bytes())
    proof["payload"]["result"]["source_inventory"] = {}
    raw = encoded(proof)
    (fixture.run / "pre-run.json").write_bytes(raw)
    fixture.manifest["trusted_outer_validation_ref"]["sha256"] = sha(raw)
    fixture.manifest["raw_refs"][0]["sha256"] = sha(raw)
    with pytest.raises(ObservationError, match="source/execution"):
        fixture.read()


def test_change_during_readonly_invocation_rejected(tmp_path: Path) -> None:
    fixture = Fixture(tmp_path)
    view = fixture.read()
    (fixture.source / "scripts/mvp_observations.py").write_bytes(b"changed after read")
    with pytest.raises(ObservationError, match="during this readonly"):
        view.unchanged()


@pytest.mark.parametrize("raw", (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b"\xff", b"[]"))
def test_strict_original_json_rejects_ambiguous_values(raw: bytes) -> None:
    with pytest.raises(ObservationError):
        v2._parse(raw)


def test_budget_v2_is_explicit_and_v1_remains_64mib() -> None:
    assert v2.MAX_ORIGINAL_BYTES_V2 == 512 * 1024 * 1024
    bundle = object.__new__(Bundle)
    bundle.v2 = False
    bundle.read_hashes = {}

    class Original:
        name = "TOOL_ONLY-budget"

        def stat(self) -> SimpleNamespace:
            return SimpleNamespace(st_size=64 * 1024 * 1024 + 1)

        def read_bytes(self) -> bytes:
            return b"{}"

    with pytest.raises(ObservationError, match="64 MiB"):
        bundle._read(Original())  # type: ignore[arg-type]
    bundle.v2 = True
    assert bundle._read(Original()) == {}  # type: ignore[arg-type]
