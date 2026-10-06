"""Private GENERAL experiment integration; formal evidence remains unverified.

Runtime rejects missing original registry, source drift, non-local/non-owned DBs,
wrong epoch/user and an absent core hook. Targeted real risks are documented;
independent phase, economic and formal experiment proofs remain separate gates.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import traceback
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.base import Base
from app.db.models import AuditEpoch, AuditEvent, EvidenceItem, User
from app.db.settings import REPOSITORY_ROOT, DatabaseSettings
from app.db.testing import require_test_database
from app.domain.audit_chain import parse_event
from app.domain.audit_chain_types import AuditEnvelope
from app.services import execution
from app.services.action_contracts import ConfirmActionRequest, PrepareActionRequest
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

STATE = "GENERAL_TARGETED_RISKS_VERIFIED_PHASE_AND_FORMAL_PROOF_PENDING"
BINDINGS = (
    "experiment_run_id",
    "case_id",
    "arm_id",
    "execution_mode",
    "input_sha256",
    "oracle_sha256",
    "design_sha256",
    "rule_sha256",
    "source_sha256",
    "seed_version",
    "isolated_db_epoch",
    "purpose",
    "user_id",
)


class ProviderNotImplemented(ValueError):
    pass


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ProviderNotImplemented(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _value(value: Any) -> str:
    return _sha(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )


def _json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            _check(key not in result, "Duplicate original JSON key")
            result[key] = value
        return result

    def finite(value: str) -> Any:
        raise ProviderNotImplemented("Nonfinite original JSON: " + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=finite)


def _bindings(context: Any) -> dict[str, str]:
    context.assert_current()
    bindings = dict(context.bindings)
    _check(set(bindings) == set(BINDINGS), "Exact original thirteen bindings required")
    _check(
        bindings["execution_mode"] == "SERVICE_INTEGRATION", "Tool/model data cannot call provider"
    )
    _check(
        bindings["purpose"] in {"DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN"},
        "Unknown original purpose",
    )
    _check(
        bindings["seed_version"] == "mvp-301-v6"
        and bindings["arm_id"] in {"B0", "B1", "B2", "B3", "P"},
        "Unknown seed/arm",
    )
    for name in ("experiment_run_id", "user_id", "isolated_db_epoch"):
        _check(str(UUID(bindings[name])) == bindings[name], "Noncanonical original UUID")
    return bindings


def _original_file(ref: dict[str, Any]) -> Any:
    path = Path(ref["path"]).resolve()
    _check(
        path.is_absolute() and path.is_file() and path.is_relative_to(REPOSITORY_ROOT.resolve()),
        "Actual root original is missing",
    )
    raw = path.read_bytes()
    _check(_sha(raw) == ref["sha256"], "Actual original bytes drifted")
    return _json(raw)


def _registry(context: Any) -> dict[str, Any]:
    if getattr(context, "root_registration_ref", None) is not None:
        return _registry_v2(context)
    bindings = _bindings(context)
    # Hash the case/arm key; case labels cannot select paths outside the registry.
    key = _value([bindings["case_id"], bindings["arm_id"]])
    path = (
        REPOSITORY_ROOT
        / ".runtime/W1-experiment-run-registry"
        / bindings["experiment_run_id"]
        / (key + ".json")
    )
    _check(path.is_file(), "NOT_IMPLEMENTED: actual root run registration is missing")
    registry = _json(path.read_bytes())
    _check(
        registry.get("protocol") == "mvp-arm-provider-registry-v1"
        and registry.get("bindings") == bindings
        and registry.get("database_name") == context.database_name,
        "Original provider registry is rebound",
    )
    for name in ("input", "oracle", "design", "rule", "source"):
        ref = registry["artifact_refs"][name]
        _check(ref["sha256"] == bindings[name + "_sha256"], "Original artifact binding differs")
        body = _original_file(ref)
        _check(
            body.get("case_id") == bindings["case_id"]
            and body.get("purpose") == bindings["purpose"],
            "Original case/purpose differs",
        )
    source = _original_file(registry["artifact_refs"]["source"])
    original_input = _original_file(registry["artifact_refs"]["input"])
    _check(
        context.now in original_input["registered_business_clocks"],
        "Original business clock is not registered in this case",
    )
    paths = set()
    actual_paths = set()
    for ref in source["implementation_files"]:
        relative = ref["original_path"]
        actual = (REPOSITORY_ROOT / relative).resolve()
        _check(
            actual.is_relative_to(REPOSITORY_ROOT.resolve())
            and relative not in paths
            and actual not in actual_paths,
            "Repeated/out-of-root source",
        )
        paths.add(relative)
        actual_paths.add(actual)
        archive = Path(ref["path"]).resolve()
        _check(
            archive.is_absolute()
            and archive.is_file()
            and actual.is_file()
            and archive.is_relative_to(REPOSITORY_ROOT.resolve())
            and _sha(actual.read_bytes()) == ref["sha256"]
            and archive.read_bytes() == actual.read_bytes(),
            "Current/archived actual source drifted",
        )
    required = {
        str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/")
        for path in (REPOSITORY_ROOT / "apps/api/app").rglob("*.py")
        if "tests" not in path.relative_to(REPOSITORY_ROOT / "apps/api/app").parts
    }
    required |= {
        "scripts/mvp_arm_executor.py",
        "scripts/mvp_observations.py",
        "scripts/mvp_trace_metrics.py",
    }
    _check(required <= paths, "Complete actual API runtime source original set is missing")
    _check(
        "_experiment_candidate_selector" in inspect.signature(execution.prepare_action).parameters,
        "NOT_IMPLEMENTED: original same-transaction candidate hook is absent",
    )
    provider = REPOSITORY_ROOT / "apps/api/app/services/experiment_arms.py"
    _check(
        Path(__file__).resolve() == provider.resolve(),
        "A runtime review file is not a deployed provider",
    )
    compiled = compile(provider.read_bytes(), str(provider), "exec", dont_inherit=True)
    definitions = {code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)}
    for name in (
        "verify_simulation_context",
        "prepare_arm_action",
        "confirm_arm_action",
        "execute_arm_action",
        "_execution_fault_scope",
        "_original_no_bank_refusal",
        "_execution_error_stack",
    ):
        function = globals()[name]
        _check(
            isinstance(function, FunctionType)
            and function.__module__ == "app.services.experiment_arms"
            and function.__code__ == definitions.get(name),
            "Loaded provider functions differ from actual frozen source",
        )
    return cast(dict[str, Any], registry)


def _registry_v2(context: Any) -> dict[str, Any]:
    from app.services.experiment_registry_v2 import FrozenInvocationBridge

    bindings = _bindings(context)
    bridge = FrozenInvocationBridge(
        root=REPOSITORY_ROOT,
        root_registration_ref=context.root_registration_ref,
        bindings=bindings,
        database_name=context.database_name,
        now=context.now,
    )
    provider = REPOSITORY_ROOT / "apps/api/app/services/experiment_arms.py"
    _check(Path(__file__).resolve() == provider.resolve(), "Only deployed V2 provider is allowed")
    compiled = compile(provider.read_bytes(), str(provider), "exec", dont_inherit=True)
    definitions = {code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)}
    for name in (
        "verify_simulation_context",
        "prepare_arm_action",
        "confirm_arm_action",
        "execute_arm_action",
        "_execution_fault_scope",
        "_original_no_bank_refusal",
        "_execution_error_stack",
    ):
        function = globals()[name]
        _check(
            isinstance(function, FunctionType)
            and function.__module__ == "app.services.experiment_arms"
            and function.__code__ == definitions.get(name),
            "Loaded V2 provider source differs",
        )
    _check(
        "_experiment_candidate_selector" in inspect.signature(execution.prepare_action).parameters,
        "Original same-lock hook is absent",
    )
    registry = dict(bridge.registration)
    registry["_invocation_bridge"] = bridge
    return registry


def _bridge(registry: dict[str, Any]) -> Any:
    return registry.get("_invocation_bridge")


def _registry_current(context: Any, registry: dict[str, Any]) -> None:
    bridge = _bridge(registry)
    if bridge is None:
        _check(_registry(context) == registry, "Original registry changed within this invocation")
    else:
        _check(
            _bindings(context) == bridge.bindings
            and context.root_registration_ref == bridge.registration_ref
            and context.database_name == bridge.database_name
            and context.now == bridge.now,
            "V2 invocation context differs",
        )
        bridge.checkpoint()
        provider = REPOSITORY_ROOT / "apps/api/app/services/experiment_arms.py"
        compiled = compile(provider.read_bytes(), str(provider), "exec", dont_inherit=True)
        definitions = {
            code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)
        }
        for name in (
            "verify_simulation_context",
            "prepare_arm_action",
            "confirm_arm_action",
            "execute_arm_action",
            "_execution_fault_scope",
            "_original_no_bank_refusal",
            "_execution_error_stack",
        ):
            function = globals()[name]
            _check(
                isinstance(function, FunctionType) and function.__code__ == definitions.get(name),
                "Loaded V2 provider changed within invocation",
            )


def _artifact(registry: dict[str, Any], name: str) -> Any:
    bridge = _bridge(registry)
    if bridge is None:
        return _original_file(registry["artifact_refs"][name])
    bridge.checkpoint()
    return bridge.artifacts[name]


def _now(context: Any) -> datetime:
    value = datetime.fromisoformat(context.now)
    _check(
        value.tzinfo is not None and value.utcoffset() is not None, "Naive original business clock"
    )
    return value.astimezone(UTC)


def _pointer(original: Any, pointer: str) -> Any:
    _check(
        isinstance(pointer, str) and (pointer == "" or pointer.startswith("/")),
        "Invalid original pointer",
    )
    value = original
    if pointer:
        for part in pointer[1:].split("/"):
            _check(re.search(r"~(?![01])", part) is None, "Original pointer escape differs")
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                _check(
                    re.fullmatch(r"0|[1-9][0-9]*", part) is not None,
                    "Original array pointer differs",
                )
                value = value[int(part)]
            else:
                _check(
                    isinstance(value, dict) and part in value, "Original pointer value is missing"
                )
                value = value[part]
    return value


class CaptureWriter:
    def __init__(self, context: Any) -> None:
        self.bindings = _bindings(context)
        root_ref = getattr(context, "root_registration_ref", None)
        self.root_registration_ref = dict(root_ref) if root_ref is not None else None
        self.service_call_id: str | None = None
        self.directory = (
            REPOSITORY_ROOT
            / ".runtime/W1-arm-provider-originals"
            / self.bindings["experiment_run_id"]
            / uuid4().hex
        )
        self.directory.mkdir(parents=True, exist_ok=False)
        self.paths: dict[str, str] = {}

    def write(self, kind: str, payload: dict[str, Any], pointer: str = "") -> dict[str, Any]:
        original: dict[str, Any] = {
            "protocol": "mvp-raw-observation-v1",
            "kind": kind,
            "bindings": self.bindings,
            "payload": payload,
        }
        if self.service_call_id is not None:
            original["service_call_id"] = self.service_call_id
        if getattr(self, "root_registration_ref", None) is not None:
            original["root_registration_ref"] = self.root_registration_ref
        raw = json.dumps(original, ensure_ascii=False, sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
        digest = _sha(raw)
        path = self.directory / (uuid4().hex + ".json")
        with path.open("xb") as stream:
            stream.write(raw)
        self.paths[digest] = str(path)
        value = _pointer(original, pointer)
        return {"artifact_sha256": digest, "json_pointer": pointer, "value_sha256": _value(value)}

    def capture(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        ref = self.write(kind, payload)
        return {
            "protocol": "mvp-arm-original-capture-v1",
            "artifact_ref": ref,
            "original_paths": dict(self.paths),
        }

    def stage(
        self,
        name: str,
        owner: UUID,
        action: UUID | None,
        now: datetime,
        basis: dict[str, Any],
        state: str,
    ) -> dict[str, Any]:
        return {
            "original_ref": self.write(
                "PIPELINE_STAGE",
                {
                    "stage": {
                        "stage": name,
                        "user_id": str(owner),
                        "action_id": str(action) if action else None,
                        "occurred_at": now.isoformat(),
                        "capture_origin": "PRODUCTION_SERVICE_CALL",
                        "stage_state": state,
                        "basis": basis,
                        "economic_execution_verified": False,
                    }
                },
                "/payload/stage",
            )
        }


def _sql_context(session: Session, context: Any, registry: dict[str, Any]) -> dict[str, Any]:
    # Re-read source and root original files every invocation and again under prepare lock.
    _registry_current(context, registry)
    owner = UUID(context.bindings["user_id"])
    database = session.scalar(text("SELECT current_database()"))
    _check(database == require_test_database(context.database_name), "Actual SQL database differs")
    user = session.get(User, owner)
    epoch = current_audit_epoch(session, owner)
    _check(
        user is not None
        and user.is_simulated
        and epoch is not None
        and str(epoch.id) == context.bindings["isolated_db_epoch"]
        and epoch.status == "OPEN",
        "Actual simulated user/open epoch differs",
    )
    epoch = cast(AuditEpoch, epoch)
    genesis = session.get(AuditEvent, epoch.genesis_event_id)
    _check(
        genesis is not None and genesis.user_id == owner and genesis.epoch_id == epoch.id,
        "Actual original seed genesis is missing",
    )
    genesis = cast(AuditEvent, genesis)
    event = cast(AuditEnvelope, parse_event(genesis.canonical_text or ""))
    transition = event.payload.epoch_transition
    _check(
        transition is not None and transition.seed_version == context.bindings["seed_version"],
        "Actual original genesis seed differs",
    )
    return {
        "user": row_copy(user),
        "epoch": row_copy(epoch),
        "genesis": row_copy(genesis),
        "database_name": database,
        "backend_pid": session.scalar(text("SELECT pg_backend_pid()")),
        "transaction_id": session.scalar(text("SELECT txid_current()")),
    }


@contextmanager
def _invocation(context: Any) -> Iterator[tuple[Engine, dict[str, Any], CaptureWriter]]:
    registry = _registry(context)  # Missing hook/registry/source fails before any DB call.
    name = require_test_database(context.database_name)
    source = make_url(DatabaseSettings().database_url)
    _check(
        source.host == "127.0.0.1"
        and source.port == 54329
        and source.get_backend_name() == "postgresql",
        "Only the original local simulation endpoint is allowed",
    )
    engine = create_engine(
        source.set(database=name), poolclass=NullPool, connect_args={"options": "-c timezone=UTC"}
    )
    writer = CaptureWriter(context)
    try:
        # Preserve the original reset exclusion across all independent commit phases.
        with audit_command_guard(engine, UUID(context.bindings["user_id"])):
            with Session(engine) as session, session.begin():
                _sql_context(session, context, registry)
            yield engine, registry, writer
            if _bridge(registry) is None:
                _registry(
                    context
                )  # Source drift means the captured run cannot be frozen as evidence.
            else:
                _registry_current(context, registry)
    finally:
        engine.dispose()


def verify_simulation_context(context: Any) -> dict[str, Any]:
    with _invocation(context) as (engine, registry, writer):
        with Session(engine) as session, session.begin():
            actual = _sql_context(session, context, registry)
        ref = writer.write("ACTUAL_SQL_CONTEXT", actual, "/payload/user")
        return writer.capture(
            "ARM_CONTEXT",
            {
                "database_host": "127.0.0.1",
                "database_port": 54329,
                "database_name": context.database_name,
                "user_id": context.bindings["user_id"],
                "is_simulated": actual["user"]["is_simulated"],
                "isolated_db_epoch": str(actual["epoch"]["id"]),
                "original_user_ref": ref,
            },
        )


def _rows(session: Session, owner: UUID) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for table in Base.metadata.sorted_tables:
        if "user_id" in table.c:
            query = select(table).where(table.c.user_id == owner).order_by(table.c.id)
        elif table.name == "asset_products":
            query = select(table).order_by(table.c.id)
        elif table.name == "users":
            query = select(table).where(table.c.id == owner).order_by(table.c.id)
        else:
            continue
        # Canonical conversion only; no P financial predicate or expected result is installed.
        rows = [dict(row) for row in session.execute(query).mappings()]
        result[table.name] = _json(
            json.dumps(
                rows,
                default=lambda value: (
                    value.isoformat() if isinstance(value, datetime) else str(value)
                ),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        )
    return result


def _verified_selector(
    context: Any,
    request: dict[str, Any],
    selector: Callable[[dict[str, Any]], dict[str, Any] | None],
    registry: dict[str, Any],
) -> None:
    # No user-supplied callback may relabel a P amount as a baseline.
    path = REPOSITORY_ROOT / "scripts/mvp_arm_executor.py"
    compiled = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
    v2 = _bridge(registry) is not None
    factory = next(
        code
        for code in compiled.co_consts
        if isinstance(code, CodeType)
        and code.co_name == ("candidate_selector_v2" if v2 else "candidate_selector")
    )
    expected = next(
        code
        for code in factory.co_consts
        if isinstance(code, CodeType) and code.co_name == ("select_v2" if v2 else "select")
    )
    _check(
        isinstance(selector, FunctionType)
        and selector.__module__ == "scripts.mvp_arm_executor"
        and selector.__code__ == expected,
        "Baseline must use its original frozen independent selector",
    )
    closure = dict(
        zip(
            selector.__code__.co_freevars,
            (cell.cell_contents for cell in selector.__closure__ or ()),
            strict=True,
        )
    )
    rule = _artifact(registry, "rule")
    algorithm = rule.get("arm_algorithm", rule)
    _check(
        closure.get("context") is context
        and closure.get("original_registration") == rule
        and (v2 or closure.get("frozen_rule") == algorithm)
        and closure.get("frozen_digest") == _value(rule),
        "Actual independent selector closure is rebound",
    )
    if v2:
        opportunity = _bridge(registry).opportunity("PREPARE", request)
        _check(
            opportunity["opportunity_id"] == closure.get("opportunity_id"),
            "V2 selector opportunity differs",
        )
        return
    original_input = _original_file(registry["artifact_refs"]["input"])
    opportunities = [
        row
        for row in original_input["opportunities"]
        if row["opportunity_id"] == closure.get("opportunity_id")
    ]
    _check(
        len(opportunities) == 1 and opportunities[0]["request"] == request,
        "Small candidate intent/opportunity is absent from its actual frozen input",
    )


def prepare_arm_action(
    context: Any,
    request: dict[str, Any],
    *,
    candidate_selector: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    typed = PrepareActionRequest.model_validate(request)
    arm = context.bindings["arm_id"]
    _check(
        (arm in {"B3", "P"}) == (candidate_selector is None),
        "Original arm/planner selector differs",
    )
    if candidate_selector is not None and typed.intent.kind != "purchase_asset":
        raise ProviderNotImplemented(
            "NOT_IMPLEMENTED: baseline override only supports GENERAL purchase; "
            "original other intents remain unchanged"
        )
    with _invocation(context) as (engine, registry, writer):
        owner, now = UUID(context.bindings["user_id"]), _now(context)
        if _bridge(registry) is None:
            original_input = _original_file(registry["artifact_refs"]["input"])
            _check(
                sum(row["request"] == request for row in original_input["opportunities"]) == 1,
                "Small request is missing or ambiguous in actual frozen input",
            )
        else:
            _bridge(registry).opportunity("PREPARE", request)
        if candidate_selector is not None:
            _verified_selector(context, request, candidate_selector, registry)
        stages: dict[str, Any] = {}
        attempt: UUID | None = None

        def guard(session: Session, user_id: UUID) -> None:
            _check(user_id == owner, "Actual same-lock owner differs")
            _sql_context(session, context, registry)

        def observe(name: str, action: UUID, data: dict[str, Any]) -> None:
            nonlocal attempt
            attempt = action
            stages[name] = writer.stage(name, owner, action, now, data, "ACTUAL_CORE_HOOK_CALL")

        def select_candidate(
            session: Session, user_id: UUID, action: UUID, intent: dict[str, Any], clock: datetime
        ) -> dict[str, Any] | None:
            nonlocal attempt
            attempt = action
            _check(user_id == owner and clock == now, "Actual candidate invocation differs")
            tables = _rows(session, owner)
            view = {
                "protocol": "mvp-arm-planning-view-v1",
                "bindings": dict(context.bindings),
                "user_id": str(owner),
                "as_of": context.now,
                "intent": intent,
                "trigger": "REGISTERED_MANUAL" if arm == "B0" else "REGISTERED_RULE",
                "accounts": tables["accounts"],
                "policy_versions": tables["policy_versions"],
            }
            rule_resolution = None
            if _bridge(registry) is not None:
                opportunity_id = _bridge(registry).registration["opportunity_id"]
                rule_resolution = _bridge(registry).rule_inputs(opportunity_id)
                view["rule_resolution"] = rule_resolution
                writer.write(
                    "ARM_RULE_RESOLUTION",
                    {"rule_resolution": rule_resolution},
                    "/payload/rule_resolution",
                )
            view_ref = writer.write("ARM_RAW_PLANNING_VIEW", {"view": view}, "/payload/view")
            assert candidate_selector is not None
            candidate = candidate_selector(view)
            candidate_ref = writer.write(
                "ARM_RAW_CANDIDATE",
                {"candidate": candidate, "planning_view_ref": view_ref},
                "/payload/candidate",
            )
            stages["candidate_before_new_effect"] = writer.stage(
                "candidate_before_new_effect",
                owner,
                action,
                now,
                {
                    "raw_candidate_ref": candidate_ref,
                    "planning_view_ref": view_ref,
                    "unsafe_candidate_status": "NOT_MEASURED",
                },
                "ACTUAL_SELECTOR_CALL",
            )
            if candidate is None:
                return None  # Original hook must raise EXPERIMENT_NO_CANDIDATE; never P fallback.
            _check(
                candidate.get("protocol") == "mvp-arm-candidate-v1"
                and candidate.get("bindings") == context.bindings
                and candidate.get("intent") == intent
                and candidate.get("source_view_sha256") == _value(view),
                "Actual raw candidate is rebound or grants an effect",
            )
            candidate_fields = {
                "protocol",
                "bindings",
                "opportunity_id",
                "arm_id",
                "amount_cents",
                "cash_uses",
                "intent",
                "manual_choice",
                "execution_status",
                "unsafe_candidate_status",
                "source_view_sha256",
                "rule_value_sha256",
                "execution_mode",
                "capability_status",
            }
            if rule_resolution is not None:
                candidate_fields.add("rule_resolution_binding_sha256")
                _check(
                    candidate.get("rule_resolution_binding_sha256") == _value(rule_resolution),
                    "V2 original rule resolution differs",
                )
            _check(
                set(candidate) == candidate_fields
                and candidate["arm_id"] == arm
                and candidate["execution_status"] == "NOT_EXECUTED"
                and candidate["execution_mode"] is None,
                "Candidate cannot inject authority/full economics/result",
            )
            rule = _artifact(registry, "rule")
            _check(
                candidate["rule_value_sha256"] == _value(rule),
                "Candidate actual frozen rule differs",
            )
            return {"amount_cents": candidate["amount_cents"], "cash_uses": candidate["cash_uses"]}

        try:
            response = execution.prepare_action(
                engine,
                owner,
                typed,
                now,
                _experiment_candidate_selector=select_candidate
                if candidate_selector is not None
                else None,
                _experiment_context_guard=guard,
                _experiment_stage_capture=observe,
            )
            payload: dict[str, Any] = {
                "capture_origin": "PRODUCTION_SERVICE_CALL",
                "outcome": "PREPARED",
                "attempt_action_id": str(response.action_id),
                "response": response.model_dump(mode="json"),
            }
        except PolicyLifecycleError as error:
            original = {
                "code": error.code,
                "message": error.message,
                "http_status": error.status_code,
            }
            ref = writer.write("ACTUAL_COMMON_ERROR", {"error": original}, "/payload/error")
            if error.code == "EXPERIMENT_NOT_IMPLEMENTED":
                raise ProviderNotImplemented(
                    "NOT_IMPLEMENTED: unsupported original candidate family; original error="
                    + str(ref)
                ) from error
            payload = {
                "capture_origin": "PRODUCTION_SERVICE_CALL",
                "outcome": "NO_CANDIDATE"
                if error.code == "EXPERIMENT_NO_CANDIDATE"
                else "COMMON_GATEWAY_REJECTED",
                "attempt_action_id": str(attempt) if attempt else None,
                "response": None,
                "error": {**original, "original_ref": ref},
            }
        required = {
            "user_lock",
            "prepare_transaction",
            "candidate_before_new_effect",
            "economic_hash",
            "source_context",
            "execution_revalidation",
            "decision_trace",
        }
        for name in required - set(stages):
            # An explicit unobserved/never-reached stage is not a successful pipeline proof.
            stages[name] = writer.stage(
                name,
                owner,
                UUID(payload["attempt_action_id"]) if payload["attempt_action_id"] else None,
                now,
                {
                    "original_outcome": payload["outcome"],
                    "reason": "No live callback was observed; not a verified completed stage",
                },
                "NOT_OBSERVED_OR_NOT_REACHED",
            )
        payload["pipeline"] = stages
        payload["capability_status"] = (
            STATE if _bridge(registry) is None else "GENERAL_V2_ACTUAL_SERVICE_VERIFICATION_PENDING"
        )
        return writer.capture("ARM_PREPARE", payload)


def _actor_original(
    registry: dict[str, Any],
    reference: dict[str, Any],
    context: Any,
    action_id: str,
    effect_hash: str,
    actual_effect: dict[str, Any],
    writer: CaptureWriter,
) -> dict[str, Any]:
    _check(
        set(reference) == {"artifact_sha256", "json_pointer", "value_sha256"},
        "Exact actor original reference is required",
    )
    digest = reference["artifact_sha256"]
    path = (Path(registry["actor_original_directory"]) / (digest + ".json")).resolve()
    _check(
        path.is_file() and path.is_relative_to(REPOSITORY_ROOT.resolve()),
        "Actual root actor original is missing",
    )
    raw = path.read_bytes()
    _check(_sha(raw) == digest, "Actual registered actor review bytes drifted")
    original = _json(raw)
    if _bridge(registry) is not None:
        _check(
            original.get("root_registration_ref") == context.root_registration_ref,
            "Exact actor original V2 invocation differs",
        )
    _check(
        original.get("protocol") == "mvp-raw-observation-v1"
        and original.get("kind") == "EXACT_EFFECT_REVIEW"
        and original.get("bindings") == context.bindings,
        "Actual actor review is another run",
    )
    value = _pointer(original, reference["json_pointer"])
    rule = _artifact(registry, "rule")
    actor = rule["actor_registration"]
    source = _artifact(registry, "source")
    actor_source = actor["implementation_source_ref"]
    _check(
        (
            actor_source in source["implementation_files"]
            if _bridge(registry) is None
            else _bridge(registry).source_descriptor(actor_source)["sha256"]
            == actor_source["sha256"]
        )
        and actor["actor_kind"] == "SYNTHETIC_SCRIPTED_ACTOR"
        and actor["confirmation_mode"] == "EXACT_EFFECT_PER_ACTION",
        "Actual frozen scripted actor source is missing",
    )
    _check(
        _value(value) == reference["value_sha256"]
        and value.get("action_id") == action_id
        and value.get("effect_hash") == effect_hash
        and value.get("accepted") is True
        and value.get("user_id") == context.bindings["user_id"]
        and value.get("exact_effect") == actual_effect
        and value.get("actor_id") == actor["actor_id"]
        and value.get("actor_kind") == actor["actor_kind"]
        and value.get("implementation_source_ref") == actor_source
        and (
            value.get("occurred_at") == _now(context).isoformat()
            if _bridge(registry) is None
            else datetime.fromisoformat(value.get("occurred_at", "")) == _now(context)
        ),
        "Exact registered runtime actor review is missing",
    )
    writer.paths[digest] = str(
        path
    )  # Locator only; bytes/pointer/full bindings were checked above.
    return cast(dict[str, Any], value)


def confirm_arm_action(
    context: Any, action_id: str, effect_hash: str, actor_registration_ref: dict[str, Any]
) -> dict[str, Any]:
    with _invocation(context) as (engine, registry, writer):
        if _bridge(registry) is not None:
            _bridge(registry).opportunity(
                "CONFIRM", {"action_id": action_id, "effect_hash": effect_hash, "accepted": True}
            )
        owner, now, identity = UUID(context.bindings["user_id"]), _now(context), UUID(action_id)
        with Session(engine) as session:
            actual = execution.get_action(session, owner, identity, now)
            _check(
                actual.effect_hash == effect_hash, "Actor cannot confirm another original effect"
            )
            _check(
                session.get(EvidenceItem, uuid5(identity, "confirmation:" + effect_hash)) is None,
                "NOT_IMPLEMENTED: prior consent requires original prior actor reuse; "
                "no fresh review/event",
            )
        review = _actor_original(
            registry,
            actor_registration_ref,
            context,
            action_id,
            effect_hash,
            actual.effect.model_dump(mode="json"),
            writer,
        )
        response = execution.confirm_action(
            engine,
            owner,
            identity,
            ConfirmActionRequest(effect_hash=effect_hash, accepted=True),
            now,
        )
        with Session(engine) as session:
            evidence = session.get(EvidenceItem, uuid5(identity, "confirmation:" + effect_hash))
            _check(
                evidence is not None and evidence.user_id == owner,
                "Actual original service confirmation is missing",
            )
            proof = row_copy(evidence)
        # A repeated confirmation requires the original actor record.
        _check(
            proof["content"]["confirmed_at"] == now.isoformat(),
            "NOT_IMPLEMENTED: original prior-consent actor reuse must be registered separately",
        )
        event = {
            "event_id": str(uuid4()),
            "actor_kind": "SYNTHETIC_SCRIPTED_ACTOR",
            "event_type": "AFFIRMATIVE_CONFIRMATION",
            "phase": "RUNTIME_INTERVENTION",
            "action_id": action_id,
            "effect_hash": effect_hash,
            "occurred_at": now.isoformat(),
            "exact_review_ref": actor_registration_ref,
            "review": review,
        }
        return writer.capture(
            "ARM_CONFIRMATION",
            {
                "capture_origin": "PRODUCTION_SERVICE_CALL",
                "response": response.model_dump(mode="json"),
                "confirmation_evidence_ref": writer.write(
                    "ACTUAL_CONFIRMATION_EVIDENCE", {"evidence": proof}, "/payload/evidence"
                ),
                "actor_event_ref": writer.write(
                    "ACTUAL_ACTOR_LOG", {"event": event}, "/payload/event"
                ),
            },
        )


def _execution_fault_scope(
    registry: dict[str, Any], engine: Engine, owner: UUID, action_id: str
) -> AbstractContextManager[None]:
    """Only an actual frozen V2 EXECUTE step may select the original scoped fault."""
    bridge = _bridge(registry)
    if bridge is None:
        return nullcontext()
    bridge.checkpoint()
    _check(bridge.step["kind"] == "EXECUTE_ACTION", "Frozen fault step is not EXECUTE_ACTION")
    kind = bridge.step["fault"]
    if kind == "NONE":
        return nullcontext()
    _check(
        kind in {"DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"},
        "NOT_IMPLEMENTED: frozen execute fault is not an original implemented kind",
    )
    from app.services import scenario_runner

    relative = "apps/api/app/services/scenario_runner.py"
    source = (REPOSITORY_ROOT / relative).resolve()
    _check(
        bridge.source_paths.get(relative) == _sha(source.read_bytes())
        and Path(scenario_runner.__file__ or "").resolve() == source,
        "Original frozen scenario fault source differs",
    )
    compiled = compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
    definitions = {code.co_name: code for code in compiled.co_consts if isinstance(code, CodeType)}

    def template() -> Iterator[None]:
        yield

    wrapper_code = contextmanager(template).__code__
    for name in ("_fault", "_inject_fault"):
        factory = getattr(scenario_runner, name, None)
        original = getattr(factory, "__wrapped__", None)
        _check(
            isinstance(factory, FunctionType)
            and factory.__module__ == "app.services.scenario_runner"
            and factory.__code__ == wrapper_code
            and isinstance(original, FunctionType)
            and original.__module__ == "app.services.scenario_runner"
            and original.__code__ == definitions.get(name)
            and Path(inspect.getfile(original)).resolve() == source
            and factory.__closure__ is not None
            and len(factory.__closure__) == 1
            and factory.__closure__[0].cell_contents is original,
            "Loaded original scenario fault function differs",
        )
    return scenario_runner._fault(kind, "EXECUTE_ACTION", engine, owner, {"action_id": action_id})


def _execution_error_stack(error: Exception, registry: dict[str, Any]) -> dict[str, Any]:
    """Capture actual frames only; missing provenance cannot replace the business cause."""
    sources: dict[str, dict[str, Any]] = {}
    registration_error = None
    try:
        source = _artifact(registry, "source")
        bridge = _bridge(registry)
        for entry in source["implementation_files" if bridge is None else "files"]:
            name = entry["original_path" if bridge is None else "path"]
            _check(name not in sources, "Duplicate original traceback source")
            current = (REPOSITORY_ROOT / name).resolve()
            _check(current.is_relative_to(REPOSITORY_ROOT.resolve()), "External traceback source")
            retained = Path(entry["path"]) if bridge is None else bridge.draft.backing[current]
            sources[name] = {"sha256": entry["sha256"], "retained": retained}
    except Exception as source_error:
        # Source/capture metadata failure must not substitute for the original exception.
        sources = {}
        registration_error = {"type": type(source_error).__name__, "message": str(source_error)}

    frames: list[dict[str, Any]] = []
    parsed: dict[Path, tuple[bytes, str, list[str], list[CodeType]]] = {}

    def codes(code: CodeType) -> list[CodeType]:
        return [code] + [
            nested
            for item in code.co_consts
            if isinstance(item, CodeType)
            for nested in codes(item)
        ]

    for frame, line in traceback.walk_tb(error.__traceback__):
        code = frame.f_code
        path = Path(code.co_filename).resolve()
        name = (
            path.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
            if path.is_relative_to(REPOSITORY_ROOT.resolve())
            else None
        )
        current_sha, line_text = None, None
        bound = False
        missing = "ORIGINAL_SOURCE_UNREGISTERED"
        try:
            if path not in parsed:
                raw = path.read_bytes()
                compiled = compile(raw, code.co_filename, "exec", dont_inherit=True)
                parsed[path] = raw, _sha(raw), raw.decode("utf-8").splitlines(), codes(compiled)
            raw, current_sha, lines, originals = parsed[path]
            if 0 < line <= len(lines):
                line_text = lines[line - 1]
            entry = sources.get(name or "")
            if entry is not None:
                bound = (
                    current_sha == entry["sha256"]
                    and Path(entry["retained"]).read_bytes() == raw
                    and line_text is not None
                    and code in originals
                )
                missing = "ORIGINAL_SOURCE_HASH_LINE_OR_LOADED_CODE_MISMATCH"
        except Exception as frame_error:
            missing = "ORIGINAL_SOURCE_UNAVAILABLE:" + type(frame_error).__name__
        frames.append(
            {
                "original_path": name,
                "path": name if name is not None else code.co_filename,
                "absolute_path": str(path),
                "sha256": current_sha,
                "line": line,
                "line_text": line_text,
                "symbol": code.co_name,
                "is_registered_source": bound,
                "source_status": "SOURCE_BOUND_ACTUAL_FRAME" if bound else "MISSING",
                "missing_reason": None if bound else missing,
            }
        )
    leaf = frames[-1] if frames else None
    source_ref = (
        {
            key: leaf[key]
            for key in ("original_path", "path", "sha256", "line", "line_text", "symbol")
        }
        if leaf is not None and leaf["is_registered_source"] is True
        else None
    )
    return {
        "exception_type": type(error).__name__,
        "formatted_traceback": "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ),
        "frames": frames,
        "stack_leaf_source_ref": source_ref,
        "stack_source_status": "CAPTURED_SOURCE_BOUND_ACTUAL_LEAF"
        if source_ref is not None
        else "MISSING",
        "stack_missing_reason": None
        if source_ref is not None
        else "ACTUAL_LEAF_OR_ORIGINAL_SOURCE_MISSING",
        "source_registration_error": registration_error,
    }


def _original_no_bank_refusal(
    before: dict[str, Any], after: dict[str, Any], action_id: str, error_ref: Any
) -> bool:
    """A capture gate, never an economic/phase oracle or a successful execution."""
    if error_ref is None:
        return False
    economic_tables = (
        "bank_operations",
        "simulated_bank_redemptions",
        "action_receipts",
        "simulated_bank_postings",
    )
    for table in economic_tables:
        if not isinstance(before.get(table), list) or not isinstance(after.get(table), list):
            return False
        if not all(isinstance(row, dict) for row in before[table] + after[table]):
            return False
        if _value(before[table]) != _value(after[table]):
            return False
        if any(
            row.get("action_plan_id") == action_id
            or row.get("operation_id") == action_id
            or row.get("redemption_id") == action_id
            for row in after[table]
        ):
            return False
    return True


def execute_arm_action(context: Any, action_id: str) -> dict[str, Any]:
    with _invocation(context) as (engine, registry, writer):
        if _bridge(registry) is not None:
            _bridge(registry).opportunity("EXECUTE", {"action_id": action_id})
        owner, now, identity = UUID(context.bindings["user_id"]), _now(context), UUID(action_id)
        service_call_id = uuid4()
        writer.service_call_id = str(service_call_id)
        with Session(engine) as session:
            original = execution.get_action(session, owner, identity, now)
            before = _rows(session, owner)
        before_ref = writer.write(
            "BUSINESS_SNAPSHOT",
            {"service_call_id": str(service_call_id), "tables": before},
            "/payload/tables",
        )
        error_ref = None
        live_phase_refs: list[dict[str, Any]] = []
        phase_recheck_refs: list[dict[str, Any]] = []
        from app.services.execution_observations import observation_scope

        def live_guard(session: Session) -> None:
            _sql_context(session, context, registry)

        def capture_live_phase(raw: bytes) -> None:
            body = _json(raw)
            _check(
                body.get("service_call_id") == str(service_call_id), "Original invocation differs"
            )
            if body.get("protocol") == "execution-independent-phase-recheck-v2":
                phase_recheck_refs.append(
                    writer.write(
                        "ACTUAL_INDEPENDENT_PHASE_RECHECK", {"recheck": body}, "/payload/recheck"
                    )
                )
            else:
                _check(
                    body.get("protocol") == "execution-live-phase-observation-v2",
                    "Unknown phase protocol",
                )
                live_phase_refs.append(
                    writer.write("ACTUAL_LIVE_PHASE", {"phase": body}, "/payload/phase")
                )

        try:
            with observation_scope(
                engine,
                owner,
                identity,
                UUID(context.bindings["isolated_db_epoch"]),
                now,
                context.bindings,
                service_call_id=service_call_id,
                guard=live_guard,
                callback=capture_live_phase,
            ):
                with _execution_fault_scope(registry, engine, owner, action_id):
                    execution.execute_action(engine, owner, identity, now)
        except Exception as error:
            # Preserve the actual SQL UNKNOWN/refusal state and original error.
            error_ref = writer.write(
                "ACTUAL_EXECUTION_ERROR",
                {
                    "service_call_id": str(service_call_id),
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                        "code": getattr(error, "code", None),
                        "status_code": getattr(error, "status_code", None),
                        "notes": list(getattr(error, "__notes__", ())),
                        **_execution_error_stack(error, registry),
                    },
                },
                "/payload/error",
            )
        with Session(engine) as session:
            _sql_context(session, context, registry)
            after = _rows(session, owner)
            actual = execution.get_action(session, owner, identity, now)
        _check(actual.effect_hash == original.effect_hash, "Original economic hash drifted")
        after_ref = writer.write(
            "BUSINESS_SNAPSHOT",
            {"service_call_id": str(service_call_id), "tables": after},
            "/payload/tables",
        )
        operations = [row for row in after["bank_operations"] if row["action_plan_id"] == action_id]
        if not operations and _original_no_bank_refusal(before, after, action_id, error_ref):
            return writer.capture(
                "ARM_EXECUTION",
                {
                    "capture_origin": "PRODUCTION_SERVICE_CALL",
                    "action_id": action_id,
                    "effect_hash": original.effect_hash,
                    "action_status": actual.status,
                    "bank_operation_ref": None,
                    "pipeline": {},
                    "service_call_id": str(service_call_id),
                    "live_phase_observation_refs": live_phase_refs,
                    "independent_phase_recheck_refs": phase_recheck_refs,
                    "independent_phase_recheck_status": "CAPTURED_VALIDATOR_PENDING",
                    "live_phase_evidence_status": "CAPTURED_NOT_INDEPENDENTLY_VERIFIED",
                    "before_snapshot_ref": before_ref,
                    "after_snapshot_ref": after_ref,
                    "error_ref": error_ref,
                    "capability_status": "ACTUAL_ORIGINAL_SERVICE_REJECTED_BEFORE_BANK",
                    "no_bank_capture_status": "RAW_ERROR_WITH_UNCHANGED_ECONOMIC_ROW_INVENTORY",
                    "economic_execution_verified": False,
                },
            )
        _check(
            len(operations) == 1,
            "NOT_IMPLEMENTED: no-bank/ambiguous-bank refusal capture has no original bank row",
        )
        bank_ref = writer.write(
            "ACTUAL_BANK_OPERATION", {"operation": operations[0]}, "/payload/operation"
        )
        receipts = [row for row in after["action_receipts"] if row["action_plan_id"] == action_id]
        postings = [
            row
            for row in after["simulated_bank_postings"]
            if row.get("operation_id") == operations[0]["id"]
        ]
        stages = {
            name: writer.stage(
                name,
                owner,
                identity,
                now,
                {
                    "before_ref": before_ref,
                    "after_ref": after_ref,
                    "error_ref": error_ref,
                    "bank_operation_ref": bank_ref,
                    "service_call_id": str(service_call_id),
                    "phase_commit_independence_verified": False,
                },
                "PERSISTED_ROWS_CAPTURED_NOT_PHASE_PROOF",
            )
            for name in {
                "user_lock",
                "reservation_transaction",
                "bank_request_commit",
                "business_projection_commit",
            }
        }
        payload = {
            "capture_origin": "PRODUCTION_SERVICE_CALL",
            "action_id": action_id,
            "effect_hash": original.effect_hash,
            "action_status": actual.status,
            "bank_operation_ref": bank_ref,
            "pipeline": stages,
            "service_call_id": str(service_call_id),
            "live_phase_observation_refs": live_phase_refs,
            "independent_phase_recheck_refs": phase_recheck_refs,
            "independent_phase_recheck_status": "CAPTURED_VALIDATOR_PENDING",
            "live_phase_evidence_status": "CAPTURED_NOT_INDEPENDENTLY_VERIFIED",
            "before_snapshot_ref": before_ref,
            "after_snapshot_ref": after_ref,
            "error_ref": error_ref,
            "capability_status": STATE
            if _bridge(registry) is None
            else "GENERAL_V2_ACTUAL_SERVICE_VERIFICATION_PENDING",
            "economic_execution_verified": False,
        }
        if actual.status == "SUCCEEDED":
            _check(
                len(receipts) == 1 and bool(postings), "Actual original receipt/postings missing"
            )
            payload["receipt_ref"] = writer.write(
                "ACTUAL_RECEIPT", {"receipt": receipts[0]}, "/payload/receipt"
            )
            payload["posting_inventory_ref"] = writer.write(
                "ACTUAL_POSTING_INVENTORY", {"rows": postings}, "/payload/rows"
            )
        return writer.capture("ARM_EXECUTION", payload)
