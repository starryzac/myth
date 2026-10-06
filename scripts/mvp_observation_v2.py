"""Readonly V2 locators for the real frozen corpus byte graph.

No native Scenario/financial/DB implementation is imported or executed. The
trusted outer driver revalidates the native corpus before a run. This reader
freshly checks that driver's original proof, the pinned freeze and current source
bytes, and exposes originals without inventing V1 registration wrappers.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from scripts.mvp_observations import (
    BINDINGS,
    METRIC_IDS,
    ObservationError,
    _digest,
    _integer,
    _list,
    _object,
    _text,
)

RUN_PROTOCOL_V2 = "mvp-observation-run-v2"
RAW_PROTOCOL_V2 = "mvp-raw-observation-v2"
FROZEN_PROTOCOL = "bounded-funds-corpus-freeze-v2"
DRAFT_PROTOCOL = "bounded-funds-corpus-draft-v2"
CASE_PROTOCOL = "bounded-funds-case-input-v2"
READONLY_MODE = "REGISTERED_READONLY_SYMBOL_CAPABILITY_DAG_V1"
MAX_ORIGINAL_BYTES_V2 = 512 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
READONLY_SOURCES = {
    "scripts/mvp_observations.py",
    "scripts/mvp_observation_v2.py",
    "scripts/mvp_trace_metrics.py",
    "scripts/mvp_financial_metrics.py",
    "scripts/mvp_financial_oracles.py",
    "scripts/mvp_readonly_oracle_dag.py",
}


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ObservationError(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        _check(key not in result, "Frozen original has duplicate JSON keys")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ObservationError("Frozen original has a nonfinite JSON number: " + value)


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        return _object(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite),
            "frozen original",
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ObservationError("Frozen original is not strict UTF8 JSON") from error


def _relative(root: Path, value: Any, label: str) -> Path:
    name = _text(value, label)
    relative = Path(name)
    _check(
        not relative.is_absolute() and ".." not in relative.parts and "." not in relative.parts,
        label + " is not a confined relative path",
    )
    result = (root / relative).resolve()
    _check(
        result.is_relative_to(root.resolve()) and result != root.resolve(),
        label + " escapes its root",
    )
    return result


def _production_paths(root: Path) -> set[str]:
    paths: set[str] = set()
    for directory in (root / "apps/api/app", root / "apps/api/alembic"):
        _check(directory.is_dir(), "Complete current API and migration trees are missing")
        paths.update(
            path.relative_to(root).as_posix()
            for path in directory.rglob("*.py")
            if "tests" not in path.relative_to(directory).parts and "__pycache__" not in path.parts
        )
    for name in ("pyproject.toml", "uv.lock", "alembic.ini"):
        _check((root / name).is_file(), "Current runtime config/lock original is missing")
        paths.add(name)
    return paths | READONLY_SOURCES


class FrozenOriginalView:
    """Fresh readonly original graph; no cached authority or semantic re-evaluation."""

    def __init__(
        self,
        manifest: dict[str, Any],
        run_root: Path,
        bindings: dict[str, str],
        *,
        source_root: Path = ROOT,
    ):
        self.run_root = run_root.resolve()
        self.source_root = source_root.resolve()
        self.bindings = dict(bindings)
        _check(set(self.bindings) == set(BINDINGS), "Exactly thirteen run bindings are required")
        self.read_hashes: dict[Path, str] = {}
        descriptor = _object(manifest.get("frozen_archive"), "explicit frozen archive locator")
        _check(
            set(descriptor) == {"manifest_path", "manifest_sha256"},
            "Frozen archive locator has unexpected or missing fields",
        )
        path = Path(_text(descriptor["manifest_path"], "frozen manifest path"))
        _check(path.is_absolute(), "Frozen manifest path must be explicitly absolute")
        _check(not path.is_symlink(), "Frozen manifest symlink is forbidden")
        self.archive_root = path.resolve().parent
        self.archive_manifest_path = path.resolve()
        self.archive_sha256 = _digest(
            descriptor["manifest_sha256"], "externally registered freeze SHA"
        )
        self.archive = _parse(self.read(path.resolve(), self.archive_sha256))
        purpose = bindings["purpose"]
        _check(
            purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"},
            "Observation V2 requires original formal purpose; development/tool cannot be promoted",
        )
        _check(
            self.archive.get("protocol") == FROZEN_PROTOCOL
            and self.archive.get("purpose") == purpose
            and self.archive.get("status") == "IMMUTABLE_CORPUS_BYTES_REGISTERED",
            "Frozen manifest protocol/purpose/status differs",
        )
        self.logical: dict[str, Path] = {}
        self.sources: dict[str, dict[str, Any]] = {}
        listed: set[Path] = set()
        current_paths: set[Path] = set()
        for value in _list(self.archive.get("files"), "complete frozen file inventory"):
            record = _object(value, "frozen file record")
            physical = _relative(self.archive_root, record.get("path"), "frozen file path")
            _check(
                physical not in listed and not physical.is_symlink(),
                "Frozen file is duplicated or a symlink",
            )
            listed.add(physical)
            expected = _digest(record.get("sha256"), "frozen file SHA")
            raw = self.read(physical, expected)
            _check(
                len(raw) == _integer(record.get("size_bytes"), "frozen original bytes"),
                "Frozen original byte count differs",
            )
            source_name = record.get("current_source_relative_path")
            artifact_name = record.get("original_artifact_relative_path")
            _check(
                source_name is not None or artifact_name is not None,
                "Frozen original has no registered graph identity",
            )
            if artifact_name is not None:
                name = _text(artifact_name, "original logical artifact path")
                _relative(self.archive_root, name, "original logical artifact path")
                _check(name not in self.logical, "Original artifact graph path is duplicated")
                self.logical[name] = physical
            if source_name is not None:
                name = _text(source_name, "original current source path")
                current = _relative(self.source_root, name, "original current source path")
                _check(name not in self.sources, "Frozen current source is duplicated")
                _check(current not in current_paths, "Current source is aliased twice")
                current_paths.add(current)
                _check(
                    self.read(current, expected) == raw,
                    "Current source differs from its full frozen original",
                )
                self.sources[name] = {
                    "original_path": name,
                    "path": str(physical),
                    "sha256": expected,
                }
        _check(bool(listed), "Frozen original inventory is empty")
        actual = {
            entry.resolve()
            for entry in self.archive_root.rglob("*")
            if entry.is_file() and entry.resolve() != self.archive_manifest_path
        }
        _check(
            not any(entry.is_symlink() for entry in self.archive_root.rglob("*")),
            "Frozen directory contains a symlink",
        )
        _check(
            not any(
                entry.is_dir() and entry.resolve() != (self.archive_root / "originals").resolve()
                for entry in self.archive_root.rglob("*")
            ),
            "Frozen directory has an unregistered directory",
        )
        _check(actual == listed, "Frozen directory contains missing/unregistered originals")
        _check(
            {name: ref["sha256"] for name, ref in self.sources.items()}
            == self.archive.get("source_inventory"),
            "Frozen complete source inventory differs",
        )
        _check(
            _production_paths(self.source_root).issubset(self.sources),
            "Actual production/config/readonly source was omitted or added after freeze",
        )
        draft_name = _text(self.archive.get("draft_original_path"), "frozen draft original path")
        self.draft = self.original(
            {"path": draft_name, "sha256": self.read_hashes[self.logical_path(draft_name)]}
        )
        _check(
            self.draft.get("protocol") == DRAFT_PROTOCOL and self.draft.get("purpose") == purpose,
            "Original corpus draft purpose/protocol differs",
        )
        entries = [
            _object(row, "original case registration")
            for row in _list(self.draft.get("cases"), "original case registrations")
        ]
        matches = [row for row in entries if row.get("case_id") == bindings["case_id"]]
        _check(len(matches) == 1, "Exactly one actual original case registration is required")
        selected = matches[0]
        frozen_cases = [
            _object(row, "frozen case binding")
            for row in _list(self.archive.get("cases"), "frozen case bindings")
        ]
        frozen_matches = [row for row in frozen_cases if row.get("case_id") == bindings["case_id"]]
        _check(len(frozen_matches) == 1, "Frozen case binding is missing/ambiguous")
        self.case_binding = frozen_matches[0]
        rule_refs = _object(selected.get("rule_refs"), "actual five rule originals")
        _check(
            set(rule_refs) == {"B0", "B1", "B2", "B3", "P"}, "Five original rules are not complete"
        )
        expected_refs = {
            "input": selected.get("input_ref"),
            "oracle": selected.get("oracle_ref"),
            "rule": rule_refs[bindings["arm_id"]],
            "source": self.draft.get("source_ref"),
            "design": self.draft.get("design_ref"),
        }
        run_refs = _object(manifest.get("artifact_refs"), "V2 exact original references")
        _check(
            set(run_refs) == set(expected_refs),
            "Every V2 input/oracle/design/rule/source original is required",
        )
        self.registrations: dict[str, dict[str, Any]] = {}
        for kind, expected_ref in expected_refs.items():
            original_ref = _object(expected_ref, "frozen " + kind + " reference")
            supplied = _object(run_refs[kind], "run " + kind + " locator")
            _check(
                supplied == original_ref
                and original_ref.get("sha256") == bindings[kind + "_sha256"],
                "Original " + kind + " locator/SHA is rebound",
            )
            self.registrations[kind] = self.original(original_ref)
        original_input = self.registrations["input"]
        _check(
            original_input.get("protocol") == CASE_PROTOCOL
            and original_input.get("intended_purpose") == purpose
            and original_input.get("case_id") == bindings["case_id"]
            and original_input.get("family_id") == selected.get("family_id"),
            "Actual Case INPUT protocol/case/family/purpose differs",
        )
        for name in ("source", "design", "oracle", "rule"):
            original = self.registrations[name]
            _check(original.get("purpose") == purpose, "Original " + name + " purpose differs")
            if "case_id" in original:
                _check(
                    original["case_id"] == bindings["case_id"],
                    "Original case-scoped " + name + " belongs to another case",
                )
        _check(
            self.registrations["rule"].get("arm_id") == bindings["arm_id"]
            and self.registrations["rule"].get("execution_mode") == bindings["execution_mode"],
            "Original actual arm rule identity/mode differs",
        )
        source_files = _list(self.registrations["source"].get("files"), "actual SOURCE files")
        source_map = {
            _text(_object(row, "SOURCE file").get("path"), "SOURCE path"): _digest(
                row.get("sha256"), "SOURCE SHA"
            )
            for row in source_files
        }
        _check(
            len(source_map) == len(source_files) and source_map == self.archive["source_inventory"],
            "Actual SOURCE current paths differ from full freeze/current originals",
        )
        seed_ref = _object(self.draft.get("seed_ref"), "original seed registration")
        self.seed = self.original(seed_ref)
        _check(
            self.seed.get("purpose") == purpose
            and self.seed.get("seed_version")
            == bindings["seed_version"]
            == original_input.get("seed_version"),
            "Original seed purpose/version differs",
        )
        for name in ("source", "design", "seed"):
            expected = seed_ref["sha256"] if name == "seed" else bindings[name + "_sha256"]
            _check(
                original_input.get(name + "_sha256") == expected,
                "Original Case " + name + " SHA differs",
            )
            _check(
                self.registrations["oracle"].get(name + "_sha256") == expected,
                "Original ORACLE " + name + " SHA differs",
            )
        _check(
            self.registrations["oracle"].get("input_sha256") == bindings["input_sha256"],
            "Original ORACLE input SHA differs",
        )
        _check(
            self.registrations["oracle"].get("rule_sha256_by_arm")
            == {arm: _object(ref, "rule original")["sha256"] for arm, ref in rule_refs.items()},
            "Original ORACLE five-rule SHA set differs",
        )
        mechanisms: set[str] = set()
        for arm, descriptor in rule_refs.items():
            rule = self.original(_object(descriptor, "actual arm rule"))
            _check(
                rule.get("purpose") == purpose and rule.get("arm_id") == arm,
                "Original arm rule is rebound",
            )
            mechanism = _text(rule.get("mechanism_id"), "actual arm mechanism")
            _check(mechanism not in mechanisms, "Two actual rules relabel the same mechanism")
            mechanisms.add(mechanism)
        self._native_byte_binding(original_input)
        self._readonly_sources(self.registrations["oracle"])
        self._original_reference_graph()
        self._outer_proof(manifest)
        self.native_originals: dict[str, dict[str, Any]] = {}
        captured_paths: set[Path] = set()
        for value in _list(manifest.get("native_original_refs", []), "native byte originals"):
            ref = _object(value, "native byte original")
            _check(set(ref) == {"path", "sha256", "original_path"},
                   "Native byte original requires copy path/SHA and exact original path")
            name = _text(ref["original_path"], "native actual original path")
            original_path = Path(name)
            _check(original_path.is_absolute() and original_path.resolve().is_relative_to(self.source_root),
                   "Native actual original is outside the registered workspace")
            physical = _relative(self.run_root, ref["path"], "native byte copy path")
            _check(name not in self.native_originals and physical not in captured_paths,
                   "Native original/copy path is duplicated or aliased")
            captured_paths.add(physical)
            expected = _digest(ref["sha256"], "native actual byte SHA")
            self.read(physical, expected)
            self.native_originals[name] = {"path": physical, "sha256": expected}

    def read(self, path: Path, expected: str) -> bytes:
        _check(
            path.is_file() and not path.is_symlink(),
            "Required frozen/current original file is missing or a symlink",
        )
        _check(
            path.stat().st_size <= MAX_ORIGINAL_BYTES_V2,
            "V2 original exceeds explicit 512MiB budget; history cannot be truncated",
        )
        raw = path.read_bytes()
        digest = _sha(raw)
        _check(
            digest == expected
            and (path not in self.read_hashes or self.read_hashes[path] == digest),
            "Frozen/current original changed or SHA differs",
        )
        self.read_hashes[path] = digest
        return raw

    def logical_path(self, name: str) -> Path:
        _check(name in self.logical, "Original logical artifact is absent from the actual freeze")
        return self.logical[name]

    def original(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        return _parse(self.original_bytes(descriptor))

    def original_bytes(self, descriptor: dict[str, Any]) -> bytes:
        _check(
            set(descriptor) == {"path", "sha256"},
            "Original artifact locator must have exact path/SHA",
        )
        name = _text(descriptor.get("path"), "original logical artifact")
        return self.read(
            self.logical_path(name), _digest(descriptor.get("sha256"), "original artifact SHA")
        )

    def captured_original(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        """Read a registered byte-identical copy; never follow an arbitrary native path."""
        _check(set(descriptor) == {"path", "sha256"}, "Exact native original locator required")
        name = _text(descriptor.get("path"), "actual native original identity")
        _check(name in self.native_originals, "Actual native byte original was not registered")
        registered = self.native_originals[name]
        _check(_digest(descriptor.get("sha256"), "actual native SHA") == registered["sha256"],
               "Native locator differs from its actual registered byte copy")
        return _parse(self.read(registered["path"], registered["sha256"]))

    def _native_byte_binding(self, original: dict[str, Any]) -> None:
        binding = _object(
            self.case_binding.get("native_execution_binding"), "original native byte binding"
        )
        fragment = _object(original.get("execution_input"), "original execution fragment")
        validated = _object(
            binding.get("validated_execution_input"), "retained validated execution fragment"
        )
        _check(
            fragment.get("purpose") == self.bindings["purpose"] == validated.get("purpose")
            and fragment.get("frozen_case_sha256") is None,
            "Original native purpose was promoted or digest injected into INPUT",
        )
        _check(
            self.case_binding.get("input_sha256")
            == self.bindings["input_sha256"]
            == binding.get("original_case_input_sha256")
            == validated.get("frozen_case_sha256"),
            "Original whole INPUT and native binding differ",
        )
        _check(
            binding.get("conversion_method") == "ORIGINAL_CASE_INPUT_SHA_BINDING_V1"
            and _sha(_canonical(fragment)) == binding.get("input_json_sha256")
            and _sha(_canonical(validated)) == binding.get("validated_execution_input_sha256"),
            "Retained native fragment byte binding differs",
        )
        converted = dict(fragment, frozen_case_sha256=self.bindings["input_sha256"])
        _check(
            _sha(_canonical(converted)) == binding.get("converted_execution_input_sha256"),
            "Original native conversion byte SHA differs",
        )

    def _readonly_sources(self, oracle: dict[str, Any]) -> None:
        _check(
            oracle.get("metric_calculator_mode") == READONLY_MODE,
            "V2 requires its explicit registered readonly capability mode; no fallback",
        )
        registration = self.original(
            _object(oracle.get("readonly_registration_ref"), "original readonly registration")
        )
        _check(
            registration.get("protocol") == "bounded-funds-readonly-oracle-dag-v2"
            and registration.get("method") == READONLY_MODE
            and registration.get("metric_bindings") == oracle.get("metric_calculators"),
            "Original readonly graph/metric bindings differ",
        )
        _check(
            set(_object(oracle.get("metric_calculators"), "fourteen calculators"))
            == set(METRIC_IDS),
            "All fourteen original calculator slots are required",
        )
        archive_refs = _object(
            oracle.get("readonly_source_archive_refs"), "readonly actual original sources"
        )
        registered_paths: set[str] = set()
        for source in _list(registration.get("source_files"), "readonly registered sources"):
            row = _object(source, "readonly registered source")
            name = _text(row.get("path"), "readonly source path")
            _check(name not in registered_paths, "Readonly source is duplicated")
            registered_paths.add(name)
            _check(
                name in self.sources
                and self.sources[name]["sha256"] == row.get("sha256")
                and name in archive_refs,
                "Readonly source is unbound to current/frozen SOURCE",
            )
            _check(
                _sha(self.original_bytes(_object(archive_refs[name], "readonly source original")))
                == row["sha256"],
                "Readonly retained source differs",
            )
        framework = _object(registration.get("framework"), "explicit immutable typed runtime")
        _check(
            framework.get("protocol") == "LOCKED_IMMUTABLE_TYPED_MODEL_RUNTIME_V1"
            and framework.get("python") == sys.version
            and framework.get("lock_sha256") == self.sources["uv.lock"]["sha256"],
            "Readonly typed runtime Python/lock binding differs",
        )
        lock = tomllib.loads((self.source_root / "uv.lock").read_text(encoding="utf-8"))
        packages = {
            _text(row.get("name"), "locked package name"): row.get("version")
            for row in _list(lock.get("package"), "locked package originals")
        }
        distributions = _object(framework.get("distributions"), "typed runtime distributions")
        _check(
            set(distributions)
            == {
                "pydantic",
                "pydantic-core",
                "annotated-types",
                "typing-extensions",
                "typing-inspection",
            },
            "Complete typing runtime is missing",
        )
        for distribution, value in distributions.items():
            original = _object(value, "typing distribution")
            _check(
                original.get("version") == packages.get(distribution),
                "Typing distribution version differs from frozen lock",
            )
            prefix = Path(sys.prefix).resolve()
            rows = _list(original.get("files"), "typing original files")
            _check(bool(rows), "Typing distribution original inventory is empty")
            for row_value in rows:
                row = _object(row_value, "typing original file")
                name = _text(row.get("path"), "typing original relative path")
                archive_name = "typed-runtime/" + distribution + "/" + name
                _check(
                    archive_name not in registered_paths and archive_name in archive_refs,
                    "Typing original is duplicated or unregistered",
                )
                registered_paths.add(archive_name)
                raw = self.original_bytes(
                    _object(archive_refs[archive_name], "typing retained original")
                )
                expected = _digest(row.get("sha256"), "typing original SHA")
                _check(
                    len(raw) == _integer(row.get("bytes"), "typing original bytes")
                    and _sha(raw) == expected
                    and self.read(_relative(prefix, name, "typing original path"), expected) == raw,
                    "Current/archived typing runtime bytes differ",
                )
        _check(
            set(archive_refs) == registered_paths,
            "Readonly dependency archive inventory is not exact",
        )

    def _original_reference_graph(self) -> None:
        # This verifies original byte references only. The trusted outer native
        # verifier checks DTO/corpus/AST semantics; no financial code runs here.
        for path in self.logical.values():
            raw = self.read(path, self.read_hashes[path])
            if not raw.lstrip().startswith(b"{"):
                continue
            value = _parse(raw)
            pending: list[Any] = [value]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    if set(item) == {"path", "sha256"}:
                        name = _text(item["path"], "original graph reference")
                        digest = _digest(item["sha256"], "original graph SHA")
                        if name in self.logical:
                            self.read(self.logical[name], digest)
                        elif name in self.sources:
                            self.read(Path(self.sources[name]["path"]), digest)
                        else:
                            raise ObservationError(
                                "Original byte graph has an unregistered reference"
                            )
                    else:
                        pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)

    def _outer_proof(self, manifest: dict[str, Any]) -> None:
        descriptor = _object(
            manifest.get("trusted_outer_validation_ref"),
            "actual trusted outer native revalidation original",
        )
        _check(
            set(descriptor) == {"path", "sha256"},
            "Outer native proof needs exact original path/SHA",
        )
        _check(
            {**descriptor, "kind": "FROZEN_RUNTIME_REVALIDATION"}
            in _list(manifest.get("raw_refs"), "actual raw originals"),
            "Outer native proof is not a registered actual raw original",
        )
        physical = _relative(self.run_root, descriptor["path"], "actual outer proof path")
        original = _parse(
            self.read(physical, _digest(descriptor["sha256"], "actual outer proof SHA"))
        )
        _check(
            original.get("protocol") == RAW_PROTOCOL_V2
            and original.get("kind") == "FROZEN_RUNTIME_REVALIDATION"
            and original.get("bindings") == self.bindings,
            "Actual outer validation original's thirteen bindings differ",
        )
        result = _object(
            _object(original.get("payload"), "actual outer proof payload").get("result"),
            "actual original native revalidation result",
        )
        _check(
            result.get("protocol") == "bounded-funds-frozen-runtime-input-v2"
            and result.get("status") == "RUNTIME_INPUT_REVALIDATED_NOT_EXECUTED"
            and result.get("purpose") == self.bindings["purpose"]
            and result.get("case_id") == self.bindings["case_id"]
            and result.get("manifest_sha256") == self.archive_sha256
            and result.get("original_case_input_sha256") == self.bindings["input_sha256"],
            "Actual outer pre-run frozen revalidation result is missing/rebound",
        )
        _check(
            result.get("source_inventory") == self.archive["source_inventory"]
            and result.get("validated_execution_input_sha256")
            == self.case_binding["native_execution_binding"]["validated_execution_input_sha256"],
            "Actual outer native source/execution byte proof differs",
        )

    def source_originals(self, expected_paths: set[str]) -> list[dict[str, Any]]:
        _check(
            expected_paths.issubset(self.sources),
            "Required independent source originals are incomplete",
        )
        return [dict(self.sources[name]) for name in sorted(expected_paths)]

    def unchanged(self) -> None:
        for path, digest in self.read_hashes.items():
            _check(
                _sha(path.read_bytes()) == digest,
                "Frozen/current original changed during this readonly invocation",
            )
