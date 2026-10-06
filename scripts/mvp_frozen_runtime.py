"""Trusted local harness: revalidate original frozen bytes before each actual run.

The public ScenarioRunner.run still accepts DEVELOPMENT only. This private,
source-bound entry receives an externally registered byte SHA, exact simulated
owner/epoch and a generated bf_test database; no ready token comes from Web.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any, cast
from uuid import UUID

from app.db.audit_guard import audit_command_guard
from app.db.models import User
from app.db.settings import DatabaseSettings
from app.db.testing import require_test_database
from app.services.audit_chain import current_audit_epoch
from app.services.scenario_runner import ScenarioRunner
from app.services.scenario_types import Scenario
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scripts.mvp_corpus_v2 import prepare_frozen_case
from scripts.mvp_native_schema import canonical, strict_json

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-trusted-frozen-runtime-registration-v1"
KEYS = {
    "protocol",
    "experiment_run_id",
    "case_id",
    "purpose",
    "user_id",
    "isolated_db_epoch",
    "database_name",
    "corpus_directory",
    "manifest_sha256",
    "input_sha256",
    "typed_execution_sha256",
    "source_inventory_sha256",
}


class FrozenRuntimeRefused(ValueError):
    """An original binding is absent, changed or outside this local harness."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise FrozenRuntimeRefused(message)


def _digest(value: Any) -> str:
    _check(
        type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "An externally registered original SHA is required",
    )
    return str(value)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inside(path: Path, folder: Path) -> Path:
    resolved = path.resolve()
    _check(
        not path.is_symlink() and resolved.is_relative_to(folder.resolve()),
        "The private run/corpus path must stay in its registered workspace",
    )
    return resolved


def read_registration(path: Path, external_sha256: str) -> dict[str, Any]:
    _digest(external_sha256)
    path = _inside(path, ROOT / ".runtime/W1-frozen-case-run-registry")
    raw = path.read_bytes()
    _check(_sha(raw) == external_sha256, "The external registration byte SHA differs")
    value = strict_json(raw)
    _check(type(value) is dict and set(value) == KEYS, "Exact private registration fields required")
    _check(value["protocol"] == PROTOCOL, "Unknown private runtime registration")
    _check(
        value["purpose"] in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"},
        "Development/tool inputs cannot gain a frozen purpose",
    )
    for field in ("experiment_run_id", "user_id", "isolated_db_epoch"):
        _check(
            type(value[field]) is str and str(UUID(value[field])) == value[field],
            "Canonical original runtime identity required",
        )
    _check(
        type(value["case_id"]) is str and 1 <= len(value["case_id"]) <= 160,
        "The original frozen case identity is required",
    )
    _check(
        type(value["database_name"]) is str
        and re.fullmatch(r"bf_test_[0-9a-f]{32}", value["database_name"]) is not None,
        "Formal/shared/default databases cannot enter this harness",
    )
    require_test_database(value["database_name"])
    for field in (
        "manifest_sha256",
        "input_sha256",
        "typed_execution_sha256",
        "source_inventory_sha256",
    ):
        _digest(value[field])
    _check(type(value["corpus_directory"]) is str, "Actual corpus directory required")
    corpus = Path(value["corpus_directory"])
    _check(
        not corpus.is_absolute() and ".." not in corpus.parts,
        "Corpus registration cannot select an external directory",
    )
    _inside(ROOT / corpus, ROOT)
    return cast(dict[str, Any], value)


def _prepared(registration: dict[str, Any]) -> tuple[Scenario, dict[str, Any]]:
    prepared = prepare_frozen_case(
        ROOT / registration["corpus_directory"],
        ROOT,
        registration["manifest_sha256"],
        registration["case_id"],
        registration["purpose"],
    )
    _check(
        prepared.get("status") == "RUNTIME_INPUT_REVALIDATED_NOT_EXECUTED",
        "Original full corpus verification refused: " + str(prepared.get("reason", "missing")),
    )
    _check(
        prepared["original_case_input_sha256"] == registration["input_sha256"]
        and prepared["validated_execution_input_sha256"] == registration["typed_execution_sha256"]
        and _sha(canonical(prepared["source_inventory"]))
        == registration["source_inventory_sha256"],
        "The registered original case/typed input/full source inventory differs",
    )
    scenario = Scenario.model_validate_json(
        json.dumps(prepared["validated_execution_input"], allow_nan=False)
    )
    _check(
        scenario.purpose == registration["purpose"]
        and scenario.frozen_case_sha256 == registration["input_sha256"]
        and _sha(canonical(scenario.model_dump(mode="json")))
        == registration["typed_execution_sha256"],
        "Only the exact revalidated typed Scenario can execute",
    )
    _check(
        scenario.initial_state.mode == "EXISTING"
        and scenario.initial_state.expected_epoch_id
        in {None, UUID(registration["isolated_db_epoch"])},
        "The owned harness must initialize a fresh seed before registering its actual epoch",
    )
    required = {"scripts/mvp_frozen_runtime.py", "apps/api/app/services/scenario_runner.py"}
    _check(
        required <= prepared["source_inventory"].keys(), "Trusted runtime source is unregistered"
    )
    path = ROOT / "apps/api/app/services/scenario_runner.py"
    raw = path.read_bytes()
    _check(
        _sha(raw) == prepared["source_inventory"][path.relative_to(ROOT).as_posix()],
        "Original runner source drifted",
    )
    compiled = compile(raw, str(path), "exec", dont_inherit=True)
    classes = {item.co_name: item for item in compiled.co_consts if isinstance(item, CodeType)}
    original_class = classes.get("ScenarioRunner")
    _check(original_class is not None, "Original runner class is missing")
    assert original_class is not None
    methods = {
        item.co_name: item for item in original_class.co_consts if isinstance(item, CodeType)
    }
    for name in ("_isolated", "_execute", "_dispatch", "check_properties"):
        method = inspect.getattr_static(ScenarioRunner, name)
        _check(
            isinstance(method, FunctionType)
            and method.__module__ == "app.services.scenario_runner"
            and method.__code__ == methods.get(name),
            "Loaded original runner callable differs",
        )
    return scenario, prepared


def run_registered_frozen_case(
    engine: Engine,
    user_id: UUID,
    epoch_id: UUID,
    registration_path: Path,
    externally_registered_sha256: str,
    output_directory: Path,
) -> dict[str, Any]:
    """Run once in a new output directory; no cached permission or case admission."""
    registration = read_registration(registration_path, externally_registered_sha256)
    _check(
        str(user_id) == registration["user_id"]
        and str(epoch_id) == registration["isolated_db_epoch"],
        "Trusted caller owner/epoch differs",
    )
    _check(
        engine.url.host == "127.0.0.1"
        and engine.url.port == 54329
        and engine.url.database == registration["database_name"],
        "Actual engine endpoint differs",
    )
    require_test_database(engine.url.database)
    output_directory = _inside(output_directory, ROOT / ".runtime/W1-frozen-case-run-output")
    _check(not output_directory.exists(), "Original run output must never be overwritten")
    runner = ScenarioRunner(engine, user_id)
    runner._isolated()
    # The reset-exclusion guard itself opens a separate PostgreSQL connection.
    # Reject absent/drifted corpus originals before entering that guard.
    _prepared(registration)
    with audit_command_guard(engine, user_id):
        scenario, prepared = _prepared(registration)  # fresh complete graph on every call
        with Session(engine) as session, session.begin():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            actual_database = session.scalar(text("SELECT current_database()"))
            user = session.get(User, user_id)
            epoch = current_audit_epoch(session, user_id)
            _check(
                actual_database == registration["database_name"]
                and user is not None
                and user.is_simulated
                and epoch is not None
                and epoch.id == epoch_id
                and epoch.status == "OPEN",
                "Actual SQL simulated owner/open epoch differs",
            )
            actual_context = {
                "database_name": actual_database,
                "user_id": str(user_id),
                "isolated_db_epoch": str(epoch_id),
                "read_only": True,
                "isolation_level": "REPEATABLE READ",
                "backend_pid": session.scalar(text("SELECT pg_backend_pid()")),
            }
        # No database transaction remains open across the original three-phase services.
        _check(
            read_registration(registration_path, externally_registered_sha256) == registration,
            "The external registration changed before the original execution",
        )
        scenario, prepared = _prepared(registration)
        output_directory.mkdir(parents=True, exist_ok=False)
        registration_raw = registration_path.read_bytes()
        (output_directory / "registration.original.json").write_bytes(registration_raw)
        (output_directory / "prepared.original.json").write_bytes(canonical(prepared))
        began = datetime.now(UTC)
        marker = {
            "status": "RUNNING_NOT_ACCEPTANCE",
            "started_at": began.isoformat(),
            "registration_sha256": externally_registered_sha256,
        }
        (output_directory / "INCOMPLETE.json").write_bytes(canonical(marker))
        try:
            actual = runner._execute(scenario)  # purpose is already original, never promoted
        except Exception as error:
            (output_directory / "execution-error.original.json").write_bytes(
                canonical(
                    {
                        "type": type(error).__name__,
                        "message": str(error),
                        "financial_effect_evidence": False,
                        "status": "ACTUAL_EXECUTION_EXCEPTION",
                    }
                )
            )
            raise
        raw_result = actual.model_dump_json().encode()
        (output_directory / "scenario-result.original.json").write_bytes(raw_result)
        after = read_registration(registration_path, externally_registered_sha256)
        _, final_prepared = _prepared(after)
        _check(
            after == registration and final_prepared == prepared,
            "Original registration/source changed",
        )
        result = {
            "protocol": "bounded-funds-trusted-frozen-runtime-result-v1",
            "status": "ORIGINAL_SCENARIO_EXECUTED_NOT_ECONOMIC_ACCEPTANCE",
            "scenario_status": actual.status,
            "actual_context": actual_context,
            "case_id": registration["case_id"],
            "purpose": registration["purpose"],
            "experiment_run_id": registration["experiment_run_id"],
            "input_sha256": registration["input_sha256"],
            "typed_execution_sha256": registration["typed_execution_sha256"],
            "scenario_result_sha256": _sha(raw_result),
            "registered_manifest_sha256": registration["manifest_sha256"],
            "started_at": began.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "financial_effect_evidence": False,
            "uncovered": ["Five-arm adapter/independent metric proof is separate", "No real funds"],
        }
        (output_directory / "manifest.json").write_bytes(canonical(result))
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--registered-sha256", required=True)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--epoch-id", type=UUID, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    registration = read_registration(args.registration, args.registered_sha256)
    _prepared(registration)
    if not args.run:
        print(
            json.dumps({"status": "REVALIDATED_NOT_EXECUTED", "financial_effect_evidence": False})
        )
        return 0
    engine = create_engine(DatabaseSettings().database_url)
    try:
        result = run_registered_frozen_case(
            engine,
            args.user_id,
            args.epoch_id,
            args.registration,
            args.registered_sha256,
            args.output_directory,
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
