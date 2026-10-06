"""Private admission and no-effect refusal risks, all fixtures TOOL_TEST_ONLY."""

import copy
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event

from scripts import mvp_frozen_runtime as runtime
from scripts.mvp_native_schema import canonical


def registration() -> tuple[Path, dict[str, Any]]:
    # This formal-shaped registration is a hostile TOOL_TEST_ONLY fixture. It has
    # no corpus, oracle or authority, so it can never reach actual execution.
    directory = runtime.ROOT / ".runtime/W1-frozen-case-run-registry/TOOL_TEST_ONLY" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "registration.json"
    value = {
        "protocol": runtime.PROTOCOL,
        "experiment_run_id": str(uuid4()),
        "case_id": "TOOL_TEST_ONLY",
        "purpose": "MVP_FROZEN",
        "user_id": str(uuid4()),
        "isolated_db_epoch": str(uuid4()),
        "database_name": "bf_test_" + uuid4().hex,
        "corpus_directory": ".runtime/TOOL_TEST_ONLY-ABSENT-CORPUS-" + uuid4().hex,
        "manifest_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "typed_execution_sha256": "c" * 64,
        "source_inventory_sha256": "d" * 64,
    }
    path.write_bytes(canonical(value))
    return path, value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "change",
    [
        {"protocol": "public-ready-token"},
        {"purpose": "DEVELOPMENT"},
        {"purpose": "TOOL_ONLY"},
        {"purpose": "MVP_FROZEN "},
        {"database_name": "bounded_funds"},
        {"database_name": "postgres"},
        {"database_name": True},
        {"database_name": "bf_test_short"},
        {"user_id": "not-uuid"},
        {"manifest_sha256": "A" * 64},
        {"input_sha256": True},
        {"typed_execution_sha256": "0" * 63},
        {"source_inventory_sha256": "0" * 65},
        {"corpus_directory": "../shared-corpus"},
        {"corpus_directory": "F:/outside"},
        {"success": True},
        {"permissions_cache": {}},
    ],
)
def test_hostile_registration_does_not_supply_private_admission(change: dict[str, Any]) -> None:
    path, value = registration()
    value.update(change)
    path.write_bytes(canonical(value))
    with pytest.raises((ValueError, TypeError)):
        runtime.read_registration(path, digest(path))


def test_external_sha_cannot_be_obtained_from_the_request_registration_itself() -> None:
    path, _ = registration()
    with pytest.raises(runtime.FrozenRuntimeRefused, match="byte SHA differs"):
        runtime.read_registration(path, "0" * 64)


def test_registration_drift_and_missing_original_preserve_absence_of_authority() -> None:
    path, value = registration()
    original_sha = digest(path)
    original = copy.deepcopy(value)
    value["case_id"] = "changed"
    path.write_bytes(canonical(value))
    with pytest.raises(runtime.FrozenRuntimeRefused):
        runtime.read_registration(path, original_sha)
    assert original["case_id"] == "TOOL_TEST_ONLY"
    with pytest.raises(OSError):
        runtime.read_registration(path.with_name("missing.json"), "0" * 64)


def test_duplicate_keys_are_rejected_even_when_external_bytes_match() -> None:
    path, _ = registration()
    path.write_bytes(b'{"protocol":"one","protocol":"two"}')
    with pytest.raises(ValueError, match="Duplicate"):
        runtime.read_registration(path, digest(path))


def test_incomplete_original_corpus_refuses_before_database_connection() -> None:
    from uuid import UUID

    path, value = registration()
    engine = create_engine(
        "postgresql+psycopg://bounded:TOOL_TEST_ONLY@127.0.0.1:54329/" + value["database_name"]
    )
    attempts = []

    @event.listens_for(engine, "do_connect")
    def refused_connection(*args: Any, **kwargs: Any) -> None:
        attempts.append(True)
        pytest.fail("Missing frozen original must fail before any database call")

    output = runtime.ROOT / ".runtime/W1-frozen-case-run-output/TOOL_TEST_ONLY" / uuid4().hex
    with pytest.raises(runtime.FrozenRuntimeRefused, match="full corpus verification refused"):
        runtime.run_registered_frozen_case(
            engine,
            UUID(value["user_id"]),
            UUID(value["isolated_db_epoch"]),
            path,
            digest(path),
            output,
        )
    assert attempts == [] and not output.exists()
    engine.dispose()


def test_unregistered_output_or_wrong_owner_refuses_before_missing_corpus() -> None:
    from uuid import UUID

    path, value = registration()
    engine = create_engine(
        "postgresql+psycopg://bounded:TOOL_TEST_ONLY@127.0.0.1:54329/" + value["database_name"]
    )
    with pytest.raises(runtime.FrozenRuntimeRefused, match="owner/epoch"):
        runtime.run_registered_frozen_case(
            engine,
            uuid4(),
            UUID(value["isolated_db_epoch"]),
            path,
            digest(path),
            runtime.ROOT / "docs/hostile-output",
        )
    with pytest.raises(runtime.FrozenRuntimeRefused, match="private run/corpus path"):
        runtime.run_registered_frozen_case(
            engine,
            UUID(value["user_id"]),
            UUID(value["isolated_db_epoch"]),
            path,
            digest(path),
            runtime.ROOT / "docs/hostile-output",
        )
    engine.dispose()
