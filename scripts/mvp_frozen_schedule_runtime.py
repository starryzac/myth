"""Private conditional runtime for complete authored frozen cases.

Each service invocation reopens the original freeze. A dispatch choice carries
no permission; the original financial service still applies its own gates.
Skipped and rejected opportunities remain in the record and no ScenarioResult
prefix or independent metric success is manufactured.
"""

from __future__ import annotations

import hashlib
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any, cast
from uuid import UUID

from app.db.audit_guard import audit_command_guard
from app.db.models import User
from app.db.testing import require_test_database
from app.services import execution, experiment_arms
from app.services.audit_chain import current_audit_epoch
from app.services.experiment_registry_v2 import bind_functions
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_references import resolve_inputs
from app.services.scenario_runner import ScenarioRunner, _fault
from app.services.scenario_types import ScenarioStep
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scripts import mvp_frozen_runtime, mvp_readonly_collector, mvp_schedule_control
from scripts.mvp_arm_executor import OriginalReader, SimulationContext, candidate_selector_v2
from scripts.mvp_authored_schedule import PROTOCOL as SCHEDULE_PROTOCOL
from scripts.mvp_corpus_v2 import revalidate_archive
from scripts.mvp_native_schema import canonical, strict_json
from scripts.mvp_observations import BINDINGS

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "bounded-funds-trusted-frozen-schedule-registration-v1"
KEYS = {
    "protocol",
    "bindings",
    "database_name",
    "corpus_directory",
    "manifest_sha256",
    "typed_execution_sha256",
    "source_inventory_sha256",
}
OPERATIONS = {"PREPARE_ACTION": "PREPARE", "CONFIRM_ACTION": "CONFIRM", "EXECUTE_ACTION": "EXECUTE"}


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise mvp_frozen_runtime.FrozenRuntimeRefused(reason)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_registration(path: Path, external_sha: str) -> dict[str, Any]:
    path = mvp_frozen_runtime._inside(path, ROOT / ".runtime/W1-frozen-case-run-registry")
    raw = path.read_bytes()
    check(sha(raw) == mvp_frozen_runtime._digest(external_sha), "REGISTRATION_BYTE_DRIFT")
    value = strict_json(raw)
    check(isinstance(value, dict) and set(value) == KEYS, "EXACT_SCHEDULE_REGISTRATION_REQUIRED")
    check(value["protocol"] == PROTOCOL, "UNKNOWN_SCHEDULE_REGISTRATION")
    bindings = value["bindings"]
    check(isinstance(bindings, dict) and set(bindings) == set(BINDINGS), "EXACT_THIRTEEN_BINDINGS")
    SimulationContext.parse(
        {
            "protocol": "mvp-arm-isolated-context-v2",
            "bindings": bindings,
            "database_name": value["database_name"],
            "database_host": "127.0.0.1",
            "database_port": 54329,
            "now": "2026-10-04T00:00:00+08:00",
            "root_registration_ref": {"path": str(path), "sha256": external_sha},
        }
    )
    for key in ("manifest_sha256", "typed_execution_sha256", "source_inventory_sha256"):
        mvp_frozen_runtime._digest(value[key])
    directory = value["corpus_directory"]
    check(type(directory) is str, "CORPUS_DIRECTORY_REQUIRED")
    relative = Path(directory)
    check(not relative.is_absolute() and ".." not in relative.parts, "EXTERNAL_CORPUS_DIRECTORY")
    mvp_frozen_runtime._inside(ROOT / relative, ROOT)
    return cast(dict[str, Any], value)


def prepared(
    registration: dict[str, Any],
) -> tuple[Any, dict[str, Any], Any, dict[str, Any], dict[str, Any]]:
    bindings = registration["bindings"]
    old = {
        "protocol": mvp_frozen_runtime.PROTOCOL,
        **{
            key: bindings[key]
            for key in (
                "experiment_run_id",
                "case_id",
                "purpose",
                "user_id",
                "isolated_db_epoch",
                "input_sha256",
            )
        },
        **{
            key: registration[key]
            for key in (
                "database_name",
                "corpus_directory",
                "manifest_sha256",
                "typed_execution_sha256",
                "source_inventory_sha256",
            )
        },
    }
    scenario, verified = mvp_frozen_runtime._prepared(old)
    inventory = verified["source_inventory"]
    required = {
        "scripts/mvp_frozen_schedule_runtime.py",
        "scripts/mvp_schedule_control.py",
        "scripts/mvp_readonly_collector.py",
    }
    check(required <= inventory.keys(), "CONDITIONAL_DRIVER_SOURCE_UNREGISTERED")
    bind_functions(
        sys.modules[__name__],
        Path(__file__),
        (
            "read_registration",
            "prepared",
            "run_registered_schedule",
            "dispatch_arm",
            "exact_actor",
            "capture_snapshot",
            "capture_exception",
        ),
    )
    bind_functions(
        mvp_schedule_control,
        ROOT / "scripts/mvp_schedule_control.py",
        (
            "plan_dispatch",
            "required_sources",
            "response",
            "decision",
        ),
    )
    draft = revalidate_archive(
        ROOT / registration["corpus_directory"],
        ROOT,
        registration["manifest_sha256"],
    )
    metadata = [row for row in draft.cases if row["case_id"] == bindings["case_id"]]
    entries = [row for row in draft.manifest["cases"] if row["case_id"] == bindings["case_id"]]
    check(len(metadata) == len(entries) == 1, "EXACT_FROZEN_CASE_REQUIRED")
    schedule = metadata[0]["native_execution_binding"].get("authored_schedule_binding")
    check(
        isinstance(schedule, dict)
        and schedule.get("protocol") == SCHEDULE_PROTOCOL
        and schedule["case_input_sha256"] == bindings["input_sha256"],
        "COMPLETE_AUTHORED_CONTROL_BINDING_REQUIRED",
    )
    entry = entries[0]
    refs = {
        "input": entry["input_ref"],
        "oracle": entry["oracle_ref"],
        "rule": entry["rule_refs"][bindings["arm_id"]],
        "source": draft.manifest["source_ref"],
        "design": draft.manifest["design_ref"],
    }
    for name, ref in refs.items():
        check(
            ref["sha256"] == bindings[name + "_sha256"], "ORIGINAL_ARTIFACT_BINDING_DIFFERS:" + name
        )
    rule = draft.load_ref(refs["rule"])
    draft.unchanged()
    return scenario, verified, draft, schedule, rule


def original(path: Path, value: Any) -> dict[str, str]:
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": str(path.resolve()), "sha256": sha(raw)}


def envelope(bindings: dict[str, str], kind: str, payload: Any, **extra: Any) -> dict[str, Any]:
    return {
        "protocol": "mvp-raw-observation-v1",
        "kind": kind,
        "bindings": dict(bindings),
        "payload": payload,
        **extra,
    }


def capture_exception(
    error: Exception, code: str, status_code: int, source_inventory: dict[str, str]
) -> dict[str, Any]:
    """Record actual traceback frames, without inventing an independent causal oracle."""
    frames: list[dict[str, Any]] = []
    current = error.__traceback__
    while current is not None:
        path = Path(current.tb_frame.f_code.co_filename).resolve()
        relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
        raw = path.read_bytes() if path.is_file() else None
        line = current.tb_lineno
        lines = raw.decode("utf-8", errors="replace").splitlines() if raw is not None else []
        actual_sha = sha(raw) if raw is not None else None
        frames.append(
            {
                "original_path": relative,
                "path": relative,
                "sha256": actual_sha,
                "line": line,
                "line_text": lines[line - 1] if 0 < line <= len(lines) else None,
                "symbol": current.tb_frame.f_code.co_name,
                "is_registered_source": source_inventory.get(relative) == actual_sha
                and actual_sha is not None,
            }
        )
        current = current.tb_next
    return {
        "code": code,
        "message": getattr(error, "message", str(error)),
        "status_code": status_code,
        "original_exception_code": getattr(error, "code", None),
        "original_exception_status_code": getattr(error, "status_code", None),
        "exception_type": type(error).__module__ + "." + type(error).__qualname__,
        "formatted_traceback": "".join(traceback.format_exception(error)),
        "frames": frames,
        "stack_leaf_source_ref": frames[-1] if frames else None,
        "independent_causal_oracle_verified": False,
    }


def capture_snapshot(
    registration: dict[str, Any], draft: Any, step: ScenarioStep, folder: Path, role: str
) -> dict[str, Any]:
    """Capture the actual complete SQL state at this step's own logical clock."""
    source = draft.manifest["source_ref"]
    logical = (draft.root / source["path"]).resolve()
    retained = draft.backing[logical]
    check(sha(retained.read_bytes()) == source["sha256"], "SNAPSHOT_SOURCE_ORIGINAL_DRIFT")
    ref = original(
        folder / (role.lower() + "-collector-registration.original.json"),
        {
            "protocol": mvp_readonly_collector.PROTOCOL,
            "bindings": registration["bindings"],
            "database_name": registration["database_name"],
            "as_of": step.at.isoformat(),
            "role": role,
            "source_ref": {"path": str(retained), "sha256": source["sha256"]},
            # This broad snapshot is linked by the source-bound step original.
            # It never claims to be a provider phase snapshot or BankOperation.
            "root_registration_ref": None,
        },
    )
    output = folder / (role.lower() + "-readonly")
    manifest = mvp_readonly_collector.collect_readonly(ref, output, workspace=ROOT)
    manifest_path = output / "manifest.json"
    return {
        "role": role,
        "step_id": step.step_id,
        "as_of": step.at.isoformat(),
        "manifest_ref": {
            "path": str(manifest_path.resolve()),
            "sha256": sha(manifest_path.read_bytes()),
        },
        "snapshot_refs": manifest["snapshot_refs"],
        "collector_registration_ref": ref,
        "is_provider_phase_proof": False,
        "financial_effect_verified": False,
    }


def exact_actor(
    engine: Engine,
    context: SimulationContext,
    rule: dict[str, Any],
    data: dict[str, Any],
    folder: Path,
) -> dict[str, str]:
    """A transparent scripted actor reads this actual immutable effect before review."""
    actor = rule["actor_registration"]
    source = actor["implementation_source_ref"]
    check(
        source
        == {
            "path": "scripts/mvp_frozen_schedule_runtime.py",
            "sha256": sha(Path(__file__).read_bytes()),
        },
        "ACTOR_IMPLEMENTATION_IS_NOT_THIS_FROZEN_SOURCE",
    )
    with Session(engine) as session:
        actual = execution.get_action(
            session,
            UUID(context.bindings["user_id"]),
            UUID(data["action_id"]),
            datetime.fromisoformat(context.now),
        )
    check(
        actual.effect_hash == data["effect_hash"] and data["accepted"] is True,
        "EXACT_ACTOR_HASH_REQUIRED",
    )
    review = {
        "action_id": data["action_id"],
        "effect_hash": data["effect_hash"],
        "accepted": True,
        "user_id": context.bindings["user_id"],
        "exact_effect": actual.effect.model_dump(mode="json"),
        "actor_id": actor["actor_id"],
        "actor_kind": "SYNTHETIC_SCRIPTED_ACTOR",
        "implementation_source_ref": source,
        "occurred_at": context.now,
    }
    raw = canonical(
        envelope(
            context.bindings,
            "EXACT_EFFECT_REVIEW",
            {"review": review},
            root_registration_ref=context.root_registration_ref,
        )
    )
    digest = sha(raw)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / (digest + ".json")).open("xb") as stream:
        stream.write(raw)
    return {
        "artifact_sha256": digest,
        "json_pointer": "/payload/review",
        "value_sha256": sha(canonical(review)),
    }


def dispatch_arm(
    engine: Engine,
    registration: dict[str, Any],
    rule: dict[str, Any],
    step: ScenarioStep,
    prior: list[dict[str, Any]],
    folder: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    operation = OPERATIONS[step.kind]
    opportunities = [
        row
        for row in rule["opportunities"]
        if row["step_id"] == step.step_id and row["operation"] == operation
    ]
    check(len(opportunities) == 1, "EXACT_REGISTERED_OPERATION_REQUIRED")
    actor_folder = folder / "actor-originals"
    invocation = {
        "protocol": "mvp-arm-provider-registry-v2",
        "bindings": registration["bindings"],
        **{
            key: registration[key]
            for key in (
                "database_name",
                "corpus_directory",
                "manifest_sha256",
                "typed_execution_sha256",
                "source_inventory_sha256",
            )
        },
        "scenario_step_id": step.step_id,
        "operation": operation,
        "opportunity_id": opportunities[0]["opportunity_id"],
        "previous_step_originals": prior,
        "previous_step_inventory_sha256": sha(canonical(prior)),
        "actor_original_directory": str(actor_folder.resolve()),
    }
    reference = original(folder / "provider-registration.original.json", invocation)
    context = SimulationContext.parse(
        {
            "protocol": "mvp-arm-isolated-context-v2",
            "bindings": registration["bindings"],
            "database_name": registration["database_name"],
            "database_host": "127.0.0.1",
            "database_port": 54329,
            "now": step.at.isoformat(),
            "root_registration_ref": reference,
        }
    )
    if operation == "PREPARE":
        selector = candidate_selector_v2(context, rule, opportunities[0]["opportunity_id"])
        capture = experiment_arms.prepare_arm_action(
            context, step.inputs, candidate_selector=selector
        )
    elif operation == "CONFIRM":
        actor_ref = exact_actor(engine, context, rule, step.inputs, actor_folder)
        capture = experiment_arms.confirm_arm_action(
            context, step.inputs["action_id"], step.inputs["effect_hash"], actor_ref
        )
    else:
        capture = experiment_arms.execute_arm_action(context, step.inputs["action_id"])
    reader = OriginalReader(context, capture["original_paths"])
    payload = reader.resolve(capture["artifact_ref"])
    original(folder / "provider-capture.original.json", capture)
    if operation == "EXECUTE":
        if payload.get("error_ref") is not None:
            error = reader.resolve(payload["error_ref"])
            return None, {
                "provider_capture": capture,
                "actual_error": error,
                "provider_registration_ref": reference,
            }
        with Session(engine) as session:
            observed = execution.get_action(
                session, UUID(context.bindings["user_id"]), UUID(step.inputs["action_id"]), step.at
            )
        return observed.model_dump(mode="json"), {
            "provider_capture": capture,
            "result_origin": "ORIGINAL_ACTION_READ_AFTER_EXECUTION_CAPTURE",
            "provider_registration_ref": reference,
        }
    return payload.get("response"), {
        "provider_capture": capture,
        "original_outcome": payload.get("outcome"),
        "provider_registration_ref": reference,
    }


def sql_context(engine: Engine, registration: dict[str, Any]) -> dict[str, Any]:
    bindings = registration["bindings"]
    with Session(engine) as session, session.begin():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        session.execute(text("SET TRANSACTION READ ONLY"))
        user = session.get(User, UUID(bindings["user_id"]))
        epoch = current_audit_epoch(session, UUID(bindings["user_id"]))
        database = session.scalar(text("SELECT current_database()"))
        check(
            database == registration["database_name"]
            and user is not None
            and user.is_simulated
            and epoch is not None
            and str(epoch.id) == bindings["isolated_db_epoch"]
            and epoch.status == "OPEN",
            "ACTUAL_SQL_OWNER_OPEN_EPOCH_DIFFERS",
        )
        return {
            "database_name": database,
            "user_id": bindings["user_id"],
            "isolated_db_epoch": bindings["isolated_db_epoch"],
            "isolation_level": "REPEATABLE READ",
            "read_only": True,
            "backend_pid": session.scalar(text("SELECT pg_backend_pid()")),
        }


def run_registered_schedule(
    engine: Engine,
    user_id: UUID,
    epoch_id: UUID,
    registration_path: Path,
    external_sha: str,
    output_directory: Path,
) -> dict[str, Any]:
    registration = read_registration(registration_path, external_sha)
    bindings = registration["bindings"]
    check(
        str(user_id) == bindings["user_id"] and str(epoch_id) == bindings["isolated_db_epoch"],
        "OWNER_EPOCH_REBOUND",
    )
    check(
        engine.url.host == "127.0.0.1"
        and engine.url.port == 54329
        and engine.url.database == registration["database_name"],
        "ENGINE_ENDPOINT_DIFFERS",
    )
    require_test_database(engine.url.database)
    output_directory = mvp_frozen_runtime._inside(
        output_directory, ROOT / ".runtime/W1-frozen-case-run-output"
    )
    check(not output_directory.exists(), "ORIGINAL_OUTPUT_MUST_NOT_BE_OVERWRITTEN")
    scenario, verified, _, schedule, _ = prepared(registration)  # all originals before any SQL
    runner = ScenarioRunner(engine, user_id)
    runner._isolated()
    output_directory.mkdir(parents=True, exist_ok=False)
    original(output_directory / "registration.original.json", registration)
    original(output_directory / "prepared.original.json", verified)
    original(output_directory / "schedule.original.json", schedule)
    original(
        output_directory / "INCOMPLETE.json",
        {"status": "RUNNING_NOT_ACCEPTANCE", "started_at": datetime.now(UTC).isoformat()},
    )
    previous: dict[str, dict[str, Any]] = {}
    prior: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    step_original_refs: list[dict[str, Any]] = []
    all_nodes = schedule["control_schedule"]["nodes"]
    nodes = [node for node in all_nodes if "step_id" in node]
    check(
        [node["step_id"] for node in nodes] == [step.step_id for step in scenario.steps],
        "CONTROL_ORDER_DIFFERS",
    )
    with audit_command_guard(engine, user_id):
        for index, (step, node) in enumerate(zip(scenario.steps, nodes, strict=True)):
            check(
                read_registration(registration_path, external_sha) == registration,
                "REGISTRATION_CHANGED",
            )
            _, fresh, draft, current_schedule, rule = prepared(registration)
            check(fresh == verified and current_schedule == schedule, "FROZEN_INPUT_SOURCE_CHANGED")
            sql_observation = sql_context(engine, registration)
            folder = output_directory / "steps" / f"{index:04d}"
            started = perf_counter_ns()
            branch = mvp_schedule_control.plan_dispatch(
                step.model_dump(mode="json"), node, bindings["arm_id"], previous
            )
            row: dict[str, Any] = {
                "step_id": step.step_id,
                "kind": step.kind,
                "at": step.at.isoformat(),
                "dispatch": branch,
                "actual_sql_context": sql_observation,
                "financial_effect_evidence": False,
            }
            if branch["status"] != mvp_schedule_control.DISPATCH:
                row["skip"] = branch
            else:
                row["before_state"] = capture_snapshot(registration, draft, step, folder, "BEFORE")
                try:
                    data, references = resolve_inputs(step.inputs, previous)
                    row["resolved_inputs"], row["input_references"] = data, references
                    actual_step = step.model_copy(update={"inputs": data})
                    if step.kind in OPERATIONS:
                        result, capture = dispatch_arm(
                            engine, registration, rule, actual_step, prior, folder
                        )
                        row["actual_provider_observation"] = capture
                        if result is not None:
                            row["result"] = result
                        elif "actual_error" in capture:
                            row["error"] = capture["actual_error"]
                        else:
                            row["skip"] = {
                                "status": "ORIGINAL_NO_SELECTED_CANDIDATE",
                                "opportunity_denominator_retained": True,
                            }
                    else:
                        with _fault(step.fault, step.kind, engine, user_id, data):
                            row["result"] = runner._dispatch(actual_step)
                except PolicyLifecycleError as error:
                    row["error"] = capture_exception(
                        error, error.code, error.status_code, fresh["source_inventory"]
                    )
                except ValidationError as error:
                    row["error"] = capture_exception(
                        error, "INVALID_SCENARIO_STEP", 422, fresh["source_inventory"]
                    )
                except TimeoutError as error:
                    row["error"] = capture_exception(
                        error, "SIMULATED_TRANSPORT_TIMEOUT", 409, fresh["source_inventory"]
                    )
                finally:
                    row["after_state"] = capture_snapshot(
                        registration, draft, step, folder, "AFTER"
                    )
            if step.expected_error is not None:
                problem = row.get("error")
                row["expected_error"] = step.expected_error.model_dump(mode="json")
                row["expected_error_matched"] = (
                    isinstance(problem, dict)
                    and problem.get("code") == step.expected_error.code
                    and problem.get("status_code") == step.expected_error.status_code
                )
            ended = perf_counter_ns()
            row["actual_timing"] = {
                "clock": "perf_counter_ns",
                "start_ns": started,
                "end_ns": ended,
                "duration_ns": ended - started,
            }
            draft.unchanged()
            check(
                read_registration(registration_path, external_sha) == registration,
                "REGISTRATION_CHANGED_DURING_STEP",
            )
            step_ref = original(folder / "dispatch.original.json", row)
            step_original_refs.append({"step_id": step.step_id, "ref": step_ref})
            if "result" in row and "error" not in row and "skip" not in row:
                raw = envelope(
                    bindings,
                    "SCENARIO_SERVICE_STEP_RESULT",
                    {
                        "step_id": step.step_id,
                        "kind": step.kind,
                        "at": step.at.isoformat(),
                        "capture_origin": "PRODUCTION_SERVICE_CALL",
                        "result": row["result"],
                        "dispatch_original_value_sha256": sha(canonical(row)),
                    },
                    trusted_schedule_registration_ref={
                        "path": str(registration_path.resolve()),
                        "sha256": external_sha,
                    },
                )
                ref = original(folder / "service-result.original.json", raw)
                prior.append({"step_id": step.step_id, "ref": ref})
            previous[step.step_id] = row
            rows.append(row)
    result = {
        "protocol": "bounded-funds-frozen-conditional-run-result-v1",
        "bindings": bindings,
        "status": "ACTUAL_CONDITIONAL_SERVICE_OBSERVATIONS_NOT_ECONOMIC_ACCEPTANCE",
        "steps": rows,
        "original_case_input_sha256": bindings["input_sha256"],
        "validated_execution_input_sha256": registration["typed_execution_sha256"],
        "trusted_schedule_registration_ref": {
            "path": str(registration_path.resolve()),
            "sha256": external_sha,
        },
        "complete_authored_input_ref": schedule["complete_authored_input_ref"],
        "step_original_refs": step_original_refs,
        "all_opportunities_retained": len(rows) == len(scenario.steps),
        "financial_effect_evidence": False,
        "independent_metrics_verified": False,
        "is_scenario_result_prefix": False,
        "uncovered": [
            "B3 recovery before-bank per-action review",
            "T02 source-bound actual clarification actor",
            "Independent observation bridge and economic acceptance",
            "No human research",
        ],
    }
    conditional_run_ref = original(
        output_directory / "conditional-run.original.json",
        {
            "protocol": "mvp-raw-observation-v2",
            "kind": "CONDITIONAL_SERVICE_RUN",
            "bindings": dict(bindings),
            "payload": {"result": result},
        },
    )
    original(
        output_directory / "error-records.original.json",
        {
            "protocol": "mvp-raw-observation-v2",
            "kind": "ERROR_RECORDS",
            "bindings": dict(bindings),
            "payload": {
                "errors": [
                    {
                        "failure_channel": "CONDITIONAL_SERVICE_STEP",
                        "failure_id": row["step_id"],
                        "step_id": row["step_id"],
                        "step_index": index,
                        "occurred_at": row["at"],
                        "user_id": bindings["user_id"],
                        "error": row["error"],
                        "conditional_run_ref": conditional_run_ref,
                        "step_ref": step_original_refs[index]["ref"],
                        "independent_causal_oracle_verified": False,
                    }
                    for index, row in enumerate(rows)
                    if "error" in row
                ],
                "complete_actual_step_order": [row["step_id"] for row in rows],
                "conditional_run_ref": conditional_run_ref,
                "independent_causal_oracle_verified": False,
            },
        },
    )
    original(output_directory / "manifest.json", result)
    return result
