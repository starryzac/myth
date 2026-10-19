"""Request-local reuse of a successful immutable v3 plan proof, never current facts.

Every call hashes the complete original content again. Current
ownership, scope, policies, money, bank rows, audit anchors and clock checks are
outside this helper and must always run. Legacy v2 keeps its existing behavior.
"""

import hashlib
import marshal
import math
from _thread import LockType
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from importlib import import_module
from threading import Lock
from types import FunctionType, UnionType
from typing import Annotated, Any, Union, get_args, get_origin
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.policy_configuration import configuration_hash
from pydantic import BaseModel
from pydantic_core import SchemaSerializer, SchemaValidator, TzInfo

V3 = "registered-joint-goal-execution-archive-v3"
V4 = "registered-joint-goal-execution-source-dag-v4"
MAX_PROOFS = 16
_proofs: ContextVar[set[str] | None] = ContextVar("joint_immutable_request_proofs", default=None)


# Fixed-size positive mathematical metadata, owned by one app/worker instance.
# This object never retains originals, typed models, users, current facts or audit results.
MAX_HISTORICAL_PURE_PROOFS = 256


@dataclass(frozen=True, eq=False, slots=True)
class _JsonPlanBinding:
    # One fixed native implementation per instance; never JSON or typed originals.
    references: tuple[object, ...]
    constants: str

    def matches(self, other: "_JsonPlanBinding") -> bool:
        return self.constants == other.constants and len(self.references) == len(
            other.references
        ) and all(left is right for left, right in zip(
            self.references, other.references, strict=True
        ))


@dataclass(slots=True)
class _HistoricalPureProofMemo:
    _digests: set[str] = field(default_factory=set, init=False)
    _lock: LockType = field(default_factory=Lock, init=False)
    _json_plan_binding: _JsonPlanBinding | None = field(default=None, init=False)


_historical_pure_proofs: ContextVar[_HistoricalPureProofMemo | None] = ContextVar(
    "joint_historical_pure_proofs", default=None
)


@contextmanager
def historical_pure_proof_scope(memo: _HistoricalPureProofMemo) -> Iterator[None]:
    if type(memo) is not _HistoricalPureProofMemo:
        raise ValueError("Exact instance-private historical proof metadata required")
    token = _historical_pure_proofs.set(memo)
    try:
        yield
    finally:
        _historical_pure_proofs.reset(token)


class _HistoricalOriginalNotEligible(ValueError):
    """A conservative miss must run the complete original verifier."""


def _complete_model_original(model: BaseModel, expected: type[BaseModel]) -> dict[str, Any]:
    """Fresh strict complete fields, including excluded fields, plus raw-type digest."""
    if type(model) is not expected:
        raise ValueError("Exact native PLAN/trace model required")
    digest = hashlib.sha256()

    def token(kind: str, value: str = "") -> None:
        raw = value.encode("utf-8")
        digest.update(kind.encode("ascii") + b":" + len(raw).to_bytes(8, "big") + raw)

    def container_args(annotation: Any, container: type) -> tuple[Any, ...] | None:
        # Only the native declared container and Optional/Annotated wrappers.
        origin = get_origin(annotation)
        if origin is Annotated:
            return container_args(get_args(annotation)[0], container)
        if origin in (Union, UnionType):
            choices = [item for item in get_args(annotation) if item is not type(None)]
            if len(choices) == 1:
                return container_args(choices[0], container)
            return None
        return get_args(annotation) if origin is container else None

    def visit(value: Any, annotation: Any = Any) -> Any:
        cls = type(value)
        if isinstance(value, BaseModel):
            if not cls.__module__.startswith(("app.domain.", "app.services.")):
                raise _HistoricalOriginalNotEligible(
                    "Only declared native model classes are eligible"
                )
            fields = cls.model_fields
            if (
                set(value.__dict__) != set(fields)
                or value.__pydantic_extra__
                or value.__pydantic_private__
            ):
                raise ValueError("Hidden, missing or private model state cannot form a proof key")
            token("model", cls.__module__ + "." + cls.__qualname__)
            result = {}
            for key in sorted(fields):
                token("field", key)
                # model_dump would omit required Field(exclude=True) originals.
                result[key] = visit(value.__dict__[key], fields[key].annotation)
            return result
        if cls is dict:
            declared = container_args(annotation, dict)
            key_type, value_type = (
                declared if declared is not None and len(declared) == 2 else (str, Any)
            )
            if any(
                type(key) is not str and not (key_type is UUID and type(key) is UUID)
                for key in value
            ):
                raise _HistoricalOriginalNotEligible("Undeclared non-string dictionary keys")
            token("dict", str(len(value)))
            result = {}
            for key in sorted(value, key=lambda item: (type(item).__name__, str(item))):
                token("key:" + type(key).__name__, str(key))
                result[key] = visit(value[key], value_type)
            return result
        if cls is list:
            declared = container_args(annotation, list)
            item_type = declared[0] if declared is not None and len(declared) == 1 else Any
            token("list", str(len(value)))
            return [visit(child, item_type) for child in value]
        if cls is tuple:
            declared = container_args(annotation, tuple)
            if declared is None:
                raise _HistoricalOriginalNotEligible(
                    "Tuple requires an explicit typed tuple declaration"
                )
            token("tuple", str(len(value)))
            if len(declared) == 2 and declared[1] is Ellipsis:
                return tuple(visit(child, declared[0]) for child in value)
            if len(value) != len(declared):
                raise ValueError("Declared tuple length differs")
            return tuple(
                visit(child, child_type)
                for child, child_type in zip(value, declared, strict=True)
            )
        if value is None:
            token("null")
        elif cls in (str, int, bool):
            token(cls.__name__, str(value))
        elif cls is float and math.isfinite(value):
            token("float", value.hex())
        elif cls is datetime:
            if value.utcoffset() is None or type(value.tzinfo) not in (timezone, ZoneInfo, TzInfo):
                raise _HistoricalOriginalNotEligible(
                    "Aware immutable native timezone originals required"
                )
            token("datetime", value.isoformat() + ":fold=" + str(value.fold))
            token(
                "timezone",
                type(value.tzinfo).__module__
                + "."
                + type(value.tzinfo).__qualname__
                + ":"
                + str(value.tzinfo),
            )
        elif cls is date:
            token("date", value.isoformat())
        elif cls is UUID:
            token("uuid", value.hex)
        else:
            raise _HistoricalOriginalNotEligible(
                "Original type is not eligible for historical reuse"
            )
        return value

    # Complete field projection reconstructs excluded required inputs as well.
    # No nested model instance can bypass strict reconstruction via instance reuse.
    complete_python = visit(model)
    type(model).model_validate(complete_python, strict=True)
    return {"wire": model.model_dump(mode="json"), "python_types_sha256": digest.hexdigest()}


def _historical_key(
    role: str,
    protocol: str,
    implementation: Callable[..., Any] | None,
    fresh_original: Callable[[], dict[str, Any]] | None,
) -> str | None:
    if _historical_pure_proofs.get() is None or fresh_original is None or protocol not in {V3, V4}:
        return None
    expected = {
        "plan": ("app.domain.full_joint_goal_execution", "_verify_frozen_joint_plan_uncached"),
        "wire": ("app.domain.full_joint_goal_execution_trace", "_verify"),
    }
    if (
        not isinstance(implementation, FunctionType) or type(implementation) is not FunctionType
        or (implementation.__module__, implementation.__qualname__) != expected.get(role)
        or implementation.__closure__
        or implementation.__defaults__
        or implementation.__kwdefaults__
    ):
        # An unknown/different delegate keeps only the original request-local path.
        return None
    try:
        original = fresh_original()
    except _HistoricalOriginalNotEligible:
        return None

    def strict_json(value: Any) -> None:
        if value is None or type(value) in (str, int, bool):
            return
        if type(value) is float and math.isfinite(value):
            return
        if type(value) is dict and all(type(key) is str for key in value):
            for child in value.values():
                strict_json(child)
            return
        if type(value) is list:
            for child in value:
                strict_json(child)
            return
        raise ValueError("Complete proof originals require exact finite JSON types")

    strict_json(original)
    return configuration_hash({
        "role": role, "protocol": protocol,
        "implementation": hashlib.sha256(marshal.dumps(implementation.__code__)).hexdigest(),
        "original": original,
    })


def _reuse_historical_proof(
    before: str | None, fresh_key: Callable[[], str | None], verifier: Callable[[], None]
) -> None:
    memo = _historical_pure_proofs.get()
    if memo is None or before is None:
        verifier()
        return
    with memo._lock:
        if before in memo._digests:
            return
    verifier()
    if before != fresh_key():
        raise ValueError("Pure verifier changed the fresh complete original")
    with memo._lock:
        if len(memo._digests) < MAX_HISTORICAL_PURE_PROOFS:
            memo._digests.add(before)


@contextmanager
def immutable_joint_validation_scope() -> Iterator[None]:
    """One fresh bounded scope per request; nested native calls share only this scope."""
    current = _proofs.get()
    if current is not None:
        yield
        return
    token = _proofs.set(set())
    try:
        yield
    finally:
        _proofs.reset(token)


def verify_original_content(
    protocol: str,
    original_without_plan_hash: Mapping[str, Any],
    claimed_plan_hash: str,
    uncached_complete_verifier: Callable[[], None],
    *,
    fresh_original: Callable[[], dict[str, Any]] | None = None,
    verifier_implementation: Callable[..., Any] | None = None,
) -> None:
    computed = configuration_hash(dict(original_without_plan_hash))
    if computed != claimed_plan_hash:
        raise ValueError("Complete original immutable plan hash differs")

    def complete() -> dict[str, Any]:
        assert fresh_original is not None
        original = fresh_original()
        wire = original["wire"]
        if (
            type(wire) is not dict or wire.get("protocol") != protocol
            or wire.get("plan_hash") != claimed_plan_hash
            or configuration_hash({key: value for key, value in wire.items() if key != "plan_hash"})
            != claimed_plan_hash
        ):
            raise ValueError("Fresh complete PLAN differs from original claimed identity")
        return original

    getter = complete if fresh_original is not None else None
    proof_key = _historical_key("plan", protocol, verifier_implementation, getter)
    if (
        fresh_original is not None
        and _historical_pure_proofs.get() is not None
        and proof_key is None
    ):
        # Unknown types/delegates cannot reuse or seed a JSON-equivalent request proof.
        uncached_complete_verifier()
        return

    def verify() -> None:
        _reuse_historical_proof(
            proof_key,
            lambda: _historical_key("plan", protocol, verifier_implementation, getter),
            uncached_complete_verifier,
        )

    current = _proofs.get()
    if protocol not in {V3, V4} or current is None:
        verify()
        return
    identity = configuration_hash({"protocol": protocol, "full_content_hash": computed})
    if proof_key is not None:
        identity = configuration_hash({"request_identity": identity, "native_proof_key": proof_key})
    if identity in current:
        return
    # A failed or interrupted verifier cannot leave a positive proof.
    verify()
    if len(current) < MAX_PROOFS:
        current.add(identity)


# This finite list is the audited native replay seam, not a recursive Python
# dependency engine. Frozen modules/factories remain a prerequisite. Every call
# still runs the caller's actual strict JSON constructor before consulting it.
_JSON_PLAN_SCHEMAS = (
    ("full_joint_goal_execution", "FullJointFrozenPlan FullJointGoalExecutionInput"),
    ("full_dynamic_goal_execution", "FullDynamicGoalInput"),
    ("full_protection_projection", "FullProtectionProjectionInput FullProtectionProjectionResult"),
    ("full_policy_configuration", "GoalAllocationPolicy LongTermGoalPolicy"),
    ("multi_goal_allocation", "MultiGoalAllocationInput SourceReference"),
)
_JSON_PLAN_FUNCTIONS = (
    ("full_joint_goal_execution", "verify_frozen_joint_plan _verify_frozen_joint_plan_uncached "
     "derive_archived_joint_execution_plan derive_source_archived_joint_execution_plan "
     "_derive_joint_execution_plan _scope _structure _history _sources "
     "_original_funds_and_goals _protection_basis project_full_protection "
     "bind_full_joint_input solve_multi_goal_allocation apply_joint_batch_cap "
     "fixed_joint_effect derive_full_dynamic_goal_proof revalidate_execution "
     "validate_full_execution_protection configuration_hash"),
    ("full_dynamic_goal_execution", "_originals derive_full_dynamic_goal_proof "
     "project_full_protection configuration_hash"),
    ("full_execution_protection", "validate_full_execution_protection project_full_protection"),
    ("full_action_set_joint_producers", "_request_hash _raw_sources _originals "
     "native_income_original_matches configuration_hash"),
    ("immutable_joint_validation_scope", "_complete_model_original verify_original_content "
     "_historical_key _reuse_historical_proof"),
    ("full_joint_goal_archive_protocol", "binding_for_plan encode_joint_record "
     "validate_joint_original_capacity"),
    ("full_joint_goal_source_adapter", "source_binding_for_input source_binding_for_plan"),
    ("full_joint_goal_source_archive", "encode_shared_joint"),
)
_JSON_PLAN_CONSTANTS = (
    ("full_joint_goal_execution", "ALGORITHM ALGORITHM_V4 NAMESPACE MAX_PLAN_BYTES MARKER"),
    ("full_joint_goal_planning", "ORIGINAL_FLOORS EXTRA_FLOORS PHASES POINT_COUNT"),
    ("full_action_set_joint_producers", "MAX_GOALS MAX_BYTES"),
    ("full_joint_goal_archive_protocol", "ALGORITHM_V3 CODEC MAX_WIRE_BYTES "
     "MAX_DECODED_BYTES MAX_DEPTH MAX_NODES MAX_COMPRESSED_BYTES"),
    ("full_joint_goal_source_adapter", "MAX_DECODED_BYTES MAX_DEPTH MAX_NODES"),
    ("full_joint_goal_archive", "MAX_DECODED_BYTES MAX_DEPTH MAX_NODES MAX_COMPRESSED_BYTES"),
    ("full_joint_goal_source_archive", "PROTOCOL DAG_PROTOCOL CODEC MAX_SEQUENCES "
     "MAX_SEQUENCE_ITEMS MAX_REFERENCE_OCCURRENCES MAX_AMPLIFICATION MAX_WIRE_BYTES "
     "MAX_DECODED_BYTES MAX_DEPTH MAX_NODES MAX_LOGICAL_NODES"),
)
_JSON_PLAN_ZONE_MODULES = (
    "full_joint_goal_execution", "full_joint_goal_planning", "full_dynamic_goal_execution",
    "full_protection_projection", "full_registered_account_debits",
)
MAX_JSON_PLAN_ORIGINAL_BYTES = 64 * 1024 * 1024  # admission only, never a new input refusal


def _json_plan_native_binding(
    model_type: type[BaseModel], verify: Callable[..., None], plan: BaseModel,
) -> _JsonPlanBinding | None:
    from app.domain import full_joint_goal_execution as native
    from app.services.full_goals import FullGoalModelContent

    if (
        model_type is not native.FullJointFrozenPlan
        or verify is not native.verify_frozen_joint_plan
    ):
        return None
    if (
        type(plan) is not native.FullJointFrozenPlan
        or getattr(plan, "protocol", None) not in {V3, V4}
    ):
        return None
    # Main and per-goal wrapper families have different replay dependencies.
    # The initial native fast path deliberately does not admit those wrappers.
    joint = plan.inputs.joint
    if joint.protection_inputs is None or any(
        source.reference_snapshots or source.protected_references
        for source in joint.protection_inputs.policies
    ) or any(
        source.reference_snapshots or source.protected_references
        for goal in joint.original_actual_input.dynamic_goals if goal.data is not None
        for source in goal.data.protection_policies
    ):
        return None
    references: list[object] = []
    constants: list[Any] = []

    def small(value: Any) -> Any:
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is UUID:
            return ["native-uuid", str(value)]
        if type(value) is tuple:
            return [small(item) for item in value]
        if type(value) is dict and all(type(key) is str for key in value):
            return {key: small(item) for key, item in value.items()}
        raise _HistoricalOriginalNotEligible("Unknown native implementation default")

    def function(value: Any) -> None:
        value = getattr(value, "__func__", value)
        if type(value) is not FunctionType or value.__closure__:
            raise _HistoricalOriginalNotEligible("Unknown native delegate")
        if not value.__module__.startswith(("app.domain.", "app.services.", "pydantic.")):
            raise _HistoricalOriginalNotEligible("Unknown native delegate module")
        references.extend((value, value.__code__))
        constants.extend((small(value.__defaults__), small(value.__kwdefaults__)))

    schemas: list[type[BaseModel]] = [FullGoalModelContent]
    if (FullGoalModelContent.__module__, FullGoalModelContent.__qualname__) != (
        "app.services.full_goals", "FullGoalModelContent"
    ):
        return None
    for module_name, names in _JSON_PLAN_SCHEMAS:
        module = import_module("app.domain." + module_name)
        for name in names.split():
            schema = getattr(module, name)
            if (schema.__module__, schema.__qualname__) != (module.__name__, name):
                return None
            schemas.append(schema)
    for schema in schemas:
        if (
            not isinstance(schema, type) or not issubclass(schema, BaseModel)
            or type(schema.__pydantic_validator__) is not SchemaValidator
            or type(schema.__pydantic_serializer__) is not SchemaSerializer
        ):
            return None
        for method in ("model_validate_json", "model_validate", "model_dump", "model_dump_json"):
            actual, original = getattr(schema, method), getattr(BaseModel, method)
            if getattr(actual, "__func__", actual) is not getattr(original, "__func__", original):
                return None
            function(actual)
        references.extend((schema, schema.__pydantic_validator__, schema.__pydantic_serializer__))
        constants.append(small(schema.model_config))
        for name, field_info in schema.model_fields.items():
            references.append(field_info)
            constants.append(name)
            factory = field_info.default_factory
            if factory is not None:
                if factory is not list and factory is not dict:
                    return None
                references.append(factory)
            elif not field_info.is_required():
                constants.append(small(field_info.default))
        for group in (
            "validators", "field_validators", "root_validators", "field_serializers",
            "model_serializers", "model_validators",
        ):
            for name, decorator in getattr(schema.__pydantic_decorators__, group).items():
                constants.append(name)
                function(decorator.func)
    for module_name, names in _JSON_PLAN_FUNCTIONS:
        module = import_module("app.domain." + module_name)
        for name in names.split():
            function(getattr(module, name))
    for module_name, names in _JSON_PLAN_CONSTANTS:
        module = import_module("app.domain." + module_name)
        constants.extend(small(getattr(module, name)) for name in names.split())
    for module_name in _JSON_PLAN_ZONE_MODULES:
        if import_module("app.domain." + module_name).ZoneInfo is not ZoneInfo:
            return None
    references.extend((ZoneInfo("UTC"), ZoneInfo("Asia/Shanghai")))
    return _JsonPlanBinding(tuple(references), configuration_hash({"native": constants}))


def parse_verified_joint_plan_original_json[JsonPlan: BaseModel](
    document: str, model_type: type[JsonPlan], verify: Callable[[JsonPlan], None],
) -> JsonPlan:
    """Fresh caller-owned strict JSON model; reuse only a prior native pure proof.

    Unknown caller aliases retain their constructor and verifier. This helper
    neither retains models nor skips any current/owner/permission/trace checks.
    """
    plan = model_type.model_validate_json(document)
    memo, current = _historical_pure_proofs.get(), _proofs.get()
    binding = None
    key = None
    try:
        from app.domain.immutable_joint_archive_scope import _request

        if (
            type(memo) is _HistoricalPureProofMemo and current is not None
            and _request.get() is not None and type(document) is str
            and len(document) <= MAX_JSON_PLAN_ORIGINAL_BYTES and document.isascii()
        ):
            binding = _json_plan_native_binding(model_type, verify, plan)
            if binding is not None:
                key = configuration_hash({
                    "role": "strict-json-plan-original-v1",
                    "protocol": getattr(plan, "protocol", None),
                    "utf8_bytes": len(document),
                    "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                })
                with memo._lock:
                    compatible = memo._json_plan_binding
                    if compatible is not None and compatible.matches(binding):
                        if key in memo._digests:
                            return plan
                    elif compatible is not None:
                        binding = None  # Never rebind an instance after implementation drift.
    except Exception:
        # Only admission/introspection failures fall back; never catch parser or verifier errors.
        binding = None
    verify(plan)
    if binding is None or key is None or memo is None or current is None:
        return plan
    try:
        after = _json_plan_native_binding(model_type, verify, plan)
        if after is None or not binding.matches(after):
            return plan
        with memo._lock:
            previous = memo._json_plan_binding
            if previous is not None and not previous.matches(binding):
                return plan
            if len(memo._digests) < MAX_HISTORICAL_PURE_PROOFS:
                memo._json_plan_binding = binding
                if len(memo._digests) < MAX_HISTORICAL_PURE_PROOFS:
                    memo._digests.add(key)
    except Exception:
        pass  # A post-success metadata failure cannot replace the original verifier's result.
    return plan
