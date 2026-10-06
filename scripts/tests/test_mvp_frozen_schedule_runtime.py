"""Private complete-original refusal gates; all hostile fixtures TOOL_TEST_ONLY."""

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event

from scripts import mvp_frozen_schedule_runtime as runtime
from scripts.mvp_frozen_runtime import FrozenRuntimeRefused
from scripts.mvp_native_schema import canonical


def fixture(arm: str = "P") -> tuple[Path, dict[str, Any]]:
    bindings = {
        "experiment_run_id": str(uuid4()),
        "case_id": "TOOL_TEST_ONLY-ABSENT",
        "arm_id": arm,
        "execution_mode": "SERVICE_INTEGRATION",
        **{
            name + "_sha256": name[0] * 64
            for name in ("input", "oracle", "design", "rule", "source")
        },
        "seed_version": "mvp-301-v6",
        "isolated_db_epoch": str(uuid4()),
        "purpose": "MVP_FROZEN",
        "user_id": str(uuid4()),
    }
    # These hostile fields have no actual original corpus or authority. Use
    # valid byte-hash syntax so the absent complete-graph gate itself is tested.
    for index, name in enumerate(("input", "oracle", "design", "rule", "source")):
        bindings[name + "_sha256"] = str(index + 1) * 64
    value = {
        "protocol": runtime.PROTOCOL,
        "bindings": bindings,
        "database_name": "bf_test_" + uuid4().hex,
        "corpus_directory": ".runtime/TOOL_TEST_ONLY-ABSENT-" + uuid4().hex,
        "manifest_sha256": "a" * 64,
        "typed_execution_sha256": "b" * 64,
        "source_inventory_sha256": "c" * 64,
    }
    directory = runtime.ROOT / ".runtime/W1-frozen-case-run-registry/TOOL_TEST_ONLY" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "registration.json"
    path.write_bytes(canonical(value))
    return path, value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("arm", ["B0", "B1", "B2", "B3", "P"])
def test_every_arm_refuses_absent_whole_corpus_before_any_database_call(arm: str) -> None:
    path, value = fixture(arm)
    engine = create_engine(
        "postgresql+psycopg://bounded:TOOL_TEST_ONLY@127.0.0.1:54329/" + value["database_name"]
    )
    attempts = []

    @event.listens_for(engine, "do_connect")
    def forbidden(*args: Any, **kwargs: Any) -> None:
        attempts.append(True)
        pytest.fail("No original freeze: database access is forbidden")

    output = runtime.ROOT / ".runtime/W1-frozen-case-run-output/TOOL_TEST_ONLY" / uuid4().hex
    with pytest.raises(FrozenRuntimeRefused, match="full corpus verification refused"):
        runtime.run_registered_schedule(
            engine,
            UUID(value["bindings"]["user_id"]),
            UUID(value["bindings"]["isolated_db_epoch"]),
            path,
            digest(path),
            output,
        )
    assert attempts == [] and not output.exists()
    engine.dispose()


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("protocol", "public-ready-token"),
        ("database_name", "bounded_funds"),
        ("manifest_sha256", True),
        ("typed_execution_sha256", "A" * 64),
        ("source_inventory_sha256", "0" * 63),
        ("corpus_directory", "../shared"),
        ("corpus_directory", "F:/external"),
        ("success", True),
    ],
)
def test_registration_fields_cannot_supply_authority(field: str, wrong: Any) -> None:
    path, value = fixture()
    value[field] = wrong
    path.write_bytes(canonical(value))
    with pytest.raises((ValueError, TypeError)):
        runtime.read_registration(path, digest(path))


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("purpose", "DEVELOPMENT"),
        ("purpose", "TOOL_TEST_ONLY"),
        ("execution_mode", "MODEL_ONLY"),
        ("arm_id", "P-copy"),
        ("user_id", "bad-uuid"),
        ("seed_version", "new-unregistered-seed"),
        ("input_sha256", False),
        ("ready", True),
    ],
)
def test_original_thirteen_bindings_remain_exact(field: str, wrong: Any) -> None:
    path, value = fixture()
    value["bindings"][field] = wrong
    path.write_bytes(canonical(value))
    with pytest.raises((ValueError, TypeError)):
        runtime.read_registration(path, digest(path))


def test_external_byte_anchor_rejects_changed_registration() -> None:
    path, value = fixture()
    external = digest(path)
    value["bindings"]["arm_id"] = "B3"
    path.write_bytes(canonical(value))
    with pytest.raises(ValueError, match="BYTE_DRIFT"):
        runtime.read_registration(path, external)


def test_duplicate_original_json_keys_and_public_output_are_refused() -> None:
    path, value = fixture()
    external = digest(path)
    engine = create_engine(
        "postgresql+psycopg://bounded:TOOL_TEST_ONLY@127.0.0.1:54329/" + value["database_name"]
    )
    with pytest.raises(ValueError, match="private run/corpus path"):
        runtime.run_registered_schedule(
            engine,
            UUID(value["bindings"]["user_id"]),
            UUID(value["bindings"]["isolated_db_epoch"]),
            path,
            external,
            runtime.ROOT / "docs/TOOL_TEST_ONLY-output",
        )
    engine.dispose()
    path.write_bytes(b'{"protocol":"a","protocol":"b"}')
    with pytest.raises(ValueError, match="Duplicate"):
        runtime.read_registration(path, digest(path))


def test_exclusive_original_writer_does_not_replace_existing_failure() -> None:
    folder = runtime.ROOT / ".runtime/W1-frozen-case-run-output/TOOL_TEST_ONLY" / uuid4().hex
    path = folder / "original.json"
    ref = runtime.original(path, {"status": "ACTUAL_FAILED", "financial_effect_evidence": False})
    original = path.read_bytes()
    assert digest(path) == ref["sha256"]
    with pytest.raises(FileExistsError):
        runtime.original(path, {"status": "success"})
    assert path.read_bytes() == original


def test_actual_traceback_retains_leaf_line_and_source_bytes_without_guessing_cause() -> None:
    try:
        raise TimeoutError("TOOL_TEST_ONLY actual original exception")
    except TimeoutError as error:
        original = runtime.capture_exception(
            error,
            "SIMULATED_TRANSPORT_TIMEOUT",
            409,
            {Path(__file__).relative_to(runtime.ROOT).as_posix(): digest(Path(__file__))},
        )
    assert original["exception_type"] == "builtins.TimeoutError"
    assert original["original_exception_code"] is None
    assert original["original_exception_status_code"] is None
    assert original["formatted_traceback"].endswith(
        "TimeoutError: TOOL_TEST_ONLY actual original exception\n"
    )
    leaf = original["stack_leaf_source_ref"]
    assert leaf == original["frames"][-1]
    assert leaf["path"] == Path(__file__).relative_to(runtime.ROOT).as_posix()
    assert leaf["sha256"] == digest(Path(__file__))
    assert leaf["is_registered_source"] is True
    assert leaf["symbol"] == (
        "test_actual_traceback_retains_leaf_line_and_source_bytes_without_guessing_cause"
    )
    assert "raise TimeoutError" in leaf["line_text"]
    assert "cause_code" not in original and "logical_cause_key" not in original
    assert original["independent_causal_oracle_verified"] is False


def test_unregistered_actual_stack_leaf_remains_unverified() -> None:
    try:
        raise RuntimeError("TOOL_TEST_ONLY unregistered source")
    except RuntimeError as error:
        original = runtime.capture_exception(error, "UNREGISTERED", 500, {})
    assert original["stack_leaf_source_ref"]["is_registered_source"] is False
    assert original["stack_leaf_source_ref"]["sha256"] == digest(Path(__file__))
