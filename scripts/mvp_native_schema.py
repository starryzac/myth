"""Pure current original DTO binding; symbolic references never become actual results."""

from __future__ import annotations

import importlib
import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    create_model,
    field_validator,
)

METHOD = "CORPUS_V2_NATIVE_SCENARIO_DTO_SYMBOLIC_REFERENCES_AND_NEGATIVE_TRANSFER_V2"
BINDING_METHOD = "ORIGINAL_CASE_INPUT_SHA_BINDING_V1"
SOURCES = (
    "apps/api/app/services/scenario_types.py",
    "apps/api/app/services/scenario_references.py",
    "apps/api/app/services/scenario_runner.py",
    "apps/api/app/services/scenario_service_steps.py",
    "apps/api/app/services/scenario_policy_declaration.py",
    "apps/api/app/services/action_contracts.py",
    "apps/api/app/services/demo_console_types.py",
    "apps/api/app/domain/external_bank_fact_types.py",
    "apps/api/app/domain/policy_configuration.py",
    "apps/api/app/domain/demo_identity.py",
    "apps/api/app/services/demo_seed.py",
    "scripts/mvp_corpus.py",
)


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "Duplicate original JSON key")
            result[key] = value
        return result

    def nonfinite(value: str) -> Any:
        raise ValueError("Nonfinite original JSON: " + value)

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)


class Inputs(BaseModel):
    """Strict shims only for old dispatcher branches without an input DTO class."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Template(Inputs):
    kind: Literal["CAR_GOAL", "LIQUID_ASSET", "FIXED_ASSET", "RENT"]
    expected_epoch_id: UUID


class ReviewTemplate(Inputs):
    proposal_id: UUID
    reviewed_hash: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def affirmative(cls, value: bool) -> bool:
        require(value is True, "Explicit acceptance required")
        return value


class Observe(Inputs):
    policy_id: UUID
    period: Annotated[StrictStr, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


def validate_json_schema(
    value: Any, schema: dict[str, Any], definitions: dict[str, Any], path: str = "inputs"
) -> int:
    """Current Pydantic schema structure plus strict unresolved reference markers.

    Custom DTO validators run separately on reference-free subobjects/literals.
    This does not resolve a source pointer or fabricate its runtime value.
    """
    known = {
        "$defs",
        "$ref",
        "anyOf",
        "oneOf",
        "const",
        "enum",
        "format",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "title",
        "description",
        "default",
        "discriminator",
    }
    require(not set(schema) - known, "Unsupported original native schema keyword")
    if isinstance(value, dict) and "$ref" in value:
        require(
            set(value) == {"$ref"}
            and isinstance(value["$ref"], dict)
            and set(value["$ref"]) == {"step_id", "pointer"},
            "Old string or extended reference is not a V2 object reference",
        )
        require(
            path.rsplit(".", 1)[-1] not in {"kind", "accepted", "user_id", "simulation"},
            "Discriminators, explicit acceptance and principal cannot be symbolic grants",
        )
        return 1
    if "$ref" in schema:
        reference = schema["$ref"]
        require(reference.startswith("#/$defs/"), "Foreign schema reference")
        return validate_json_schema(
            value, definitions[reference.removeprefix("#/$defs/")], definitions, path
        )
    choices = schema.get("anyOf", schema.get("oneOf"))
    if choices is not None:
        matches = []
        for choice in choices:
            try:
                matches.append(validate_json_schema(value, choice, definitions, path))
            except (ValueError, KeyError, TypeError):
                pass
        require(
            bool(matches) and ("oneOf" not in schema or len(matches) == 1),
            "No exact original DTO variant: " + path,
        )
        return min(matches)
    kind = schema.get("type")
    admitted = {
        "object": type(value) is dict,
        "array": type(value) is list,
        "string": type(value) is str,
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "boolean": type(value) is bool,
        "null": value is None,
    }
    require(kind in admitted and admitted[kind], "Original DTO type differs: " + path)
    if "const" in schema:
        require(
            type(value) is type(schema["const"]) and value == schema["const"],
            "Original DTO const differs",
        )
    if "enum" in schema:
        require(
            any(type(value) is type(item) and value == item for item in schema["enum"]),
            "Original DTO enum differs",
        )
    if kind == "object":
        properties = schema.get("properties", {})
        require(
            set(schema.get("required", [])) <= set(value), "Required original DTO fields missing"
        )
        additional = schema.get("additionalProperties", True)
        require(
            additional is not False or not set(value) - set(properties),
            "Extra financial/grant/result field",
        )
        count = 0
        for key, item in value.items():
            if key in properties:
                count += validate_json_schema(item, properties[key], definitions, path + "." + key)
            elif isinstance(additional, dict):
                # Empty schema is original Any, not an invented permissive input schema.
                if additional:
                    count += validate_json_schema(item, additional, definitions, path + "." + key)
        return count
    if kind == "array":
        require(
            schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100000),
            "Original array bounds differ",
        )
        return sum(
            validate_json_schema(item, schema["items"], definitions, path + "[]") for item in value
        )
    if kind in {"integer", "number"}:
        require(
            schema.get("minimum", -(2**63)) <= value <= schema.get("maximum", 2**63 - 1),
            "Original numeric bounds differ",
        )
        if "exclusiveMinimum" in schema:
            require(value > schema["exclusiveMinimum"], "Original positive amount differs")
        if "exclusiveMaximum" in schema:
            require(value < schema["exclusiveMaximum"], "Original amount bound differs")
    if kind == "string":
        require(
            schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1000000),
            "Original string bounds differ",
        )
        if "pattern" in schema:
            require(
                re.search(schema["pattern"], value) is not None, "Original string pattern differs"
            )
        if schema.get("format") == "uuid":
            UUID(value)
        if schema.get("format") == "date-time":
            clock = datetime.fromisoformat(value)
            require(
                clock.tzinfo is not None and clock.utcoffset() is not None,
                "Aware fact clock required",
            )
        require(schema.get("format") in {None, "uuid", "date-time"}, "Unsupported native format")
    return 0


def reference_count(value: Any) -> int:
    if isinstance(value, dict):
        return 1 if "$ref" in value else sum(reference_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(map(reference_count, value))
    return 0


class NativeAdapter:
    def __init__(self, root: Path, source_inventory: dict[str, str]):
        from hashlib import sha256

        self.root = root.resolve()
        self.sources = {
            name: sha256((self.root / name).read_bytes()).hexdigest() for name in SOURCES
        }
        for path in (Path(__file__), Path(__file__).with_name("mvp_corpus_v2.py")):
            name = path.resolve().relative_to(self.root).as_posix()
            self.sources[name] = sha256(path.read_bytes()).hexdigest()
        require(
            all(source_inventory.get(name) == digest for name, digest in self.sources.items()),
            "Actual schema source inventory incomplete or changed",
        )
        sys.path.insert(0, str(self.root / "apps/api"))
        self.runtime = importlib.import_module("app.services.scenario_types")
        self.service = importlib.import_module("app.services.scenario_service_steps")
        action = importlib.import_module("app.services.action_contracts")
        demo = importlib.import_module("app.services.demo_console_types")
        fact = importlib.import_module("app.domain.external_bank_fact_types")
        configuration = importlib.import_module("app.domain.policy_configuration")
        identity = importlib.import_module("app.domain.demo_identity")
        self.validate_configuration = configuration.validate_configuration
        self.configuration_hash = configuration.configuration_hash
        self.user_id = identity.DEMO_USER_ID
        self.modules = (self.runtime, self.service, action, demo, fact, configuration, identity)
        for module in self.modules:
            module_path = module.__file__
            if module_path is None:
                raise ValueError("Loaded native DTO module has no original file")
            require(
                Path(module_path).resolve().is_relative_to(self.root / "apps/api"),
                "Foreign loaded DTO source",
            )

        ConfirmAction = create_model(
            "ConfirmAction", __base__=action.ConfirmActionRequest, action_id=(UUID, ...)
        )

        self.models: dict[str, type[BaseModel]] = {
            "DEMO_EVENT": demo.DemoEventRequest,
            "PREPARE_TEMPLATE": Template,
            "CONFIRM_TEMPLATE": ReviewTemplate,
            "EXTERNAL_FACT": fact.ExternalFactRequest,
            "PREPARE_ACTION": action.PrepareActionRequest,
            "CONFIRM_ACTION": ConfirmAction,
            "EXECUTE_ACTION": self.service.ActionIdentity,
            "OBSERVE_RECURRING_PAYMENT": Observe,
            "LOOKUP_ACCOUNT": self.service.LookupAccount,
            "READ_ACTION": self.service.ActionIdentity,
            "READ_POLICY": self.service.PolicyIdentity,
            "SNAPSHOT": self.service.Inputs,
            "CHANGE_POLICY": self.service.PolicyChange,
            "SUSPEND_POLICY": self.service.PolicyState,
            "REVOKE_POLICY": self.service.PolicyState,
            "CREATE_GOAL": self.service.CreateGoal,
            "RUN_RECOVERY": self.service.RecoveryKey,
            "READ_RECOVERY": self.service.RecoveryIdentity,
            "ISSUE_EARLY_QUOTE": self.service.PositionIdentity,
            "DECLARE_POLICY": self.service.PolicyDeclaration,
            "CONFIRM_POLICY": self.service.ProposalConfirmation,
        }
        self.unchanged()

    def unchanged(self) -> None:
        from hashlib import sha256

        require(
            all(
                sha256((self.root / name).read_bytes()).hexdigest() == expected
                for name, expected in self.sources.items()
            ),
            "Schema source changed during native validation",
        )

    def registration(self, purpose: str) -> dict[str, Any]:
        scenario_path, runner_path = SOURCES[0], SOURCES[2]
        return {
            "purpose": purpose,
            "contract_status": "SUPPORTED_INPUTS_REGISTERED",
            "method": METHOD,
            "contract_source_path": scenario_path,
            "contract_source_sha256": self.sources[scenario_path],
            "runner_source_path": runner_path,
            "runner_source_sha256": self.sources[runner_path],
            "schema_sources": self.sources,
            "initial_state_schema": self.runtime.InitialState.model_json_schema(),
            "scenario_schema": self.runtime.Scenario.model_json_schema(),
            "step_schemas": {
                kind: model.model_json_schema() for kind, model in self.models.items()
            },
            "faults": ["NONE", "DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"],
            "reference_resolution": "STRICT_ORIGINAL_BACKWARD_OBJECT_PENDING_RUNTIME",
            "expected_dto_rejection_contracts": [
                {
                    "kind": "PREPARE_ACTION",
                    "intent_kind": "transfer_internal",
                    "field": "intent.destination_account_id",
                    "authored_invalid_values": ["MISSING", "NULL"],
                    "expected_error": {"code": "INVALID_SCENARIO_STEP", "status_code": 422},
                    "runtime_rejection_observed": False,
                }
            ],
        }

    def validate_execution(
        self, raw: bytes, expected_purpose: str, *, original_case_input_sha256: str | None = None
    ) -> dict[str, Any]:
        from hashlib import sha256

        self.unchanged()
        authored = strict_json(raw)
        require(type(authored) is dict, "Complete original Scenario object required")
        require(authored.get("purpose") == expected_purpose, "Original execution purpose differs")
        converted = dict(authored)
        if original_case_input_sha256 is not None:
            require(
                expected_purpose in {"MVP_FROZEN", "FULL_FAMILY_FROZEN"},
                "Development cannot receive a frozen digest",
            )
            require(
                re.fullmatch(r"[0-9a-f]{64}", original_case_input_sha256) is not None,
                "Original INPUT byte SHA required",
            )
            require(
                authored.get("frozen_case_sha256") is None,
                "Authored INPUT cannot substitute a self-referential or external frozen digest",
            )
            converted["frozen_case_sha256"] = original_case_input_sha256
        elif expected_purpose != "DEVELOPMENT":
            raise ValueError("Frozen Scenario validation requires original Case INPUT byte SHA")
        actual = self.runtime.Scenario.model_validate_json(canonical(converted))
        require(
            actual.purpose == expected_purpose,
            "Development or TOOL input cannot be promoted to frozen execution",
        )
        literal_count, symbolic_count = 0, 0
        expected_dto_rejections: list[dict[str, Any]] = []
        for step in actual.steps:
            require(step.kind in self.models, "Actual native DTO kind is not implemented")
            values = step.inputs
            model = self.models[step.kind]
            schema = model.model_json_schema()
            # An authored negative may be frozen only through this exact contract.
            # The original runtime DTO remains strict and must actually reject it.
            negative_destination = (
                step.kind == "PREPARE_ACTION"
                and step.expected_error is not None
                and step.expected_error.code == "INVALID_SCENARIO_STEP"
                and step.expected_error.status_code == 422
                and type(values.get("intent")) is dict
                and values["intent"].get("kind") == "transfer_internal"
                and values["intent"].get("destination_account_id") is None
            )
            if negative_destination:
                schema = deepcopy(schema)
                transfer = schema["$defs"]["TransferIntent"]
                transfer["properties"]["destination_account_id"] = {"type": "null"}
                transfer["required"].remove("destination_account_id")
            unresolved = validate_json_schema(values, schema, schema.get("$defs", {}))
            require(
                unresolved == reference_count(values),
                "An Any field cannot hide an unvalidated symbolic reference",
            )
            if step.kind in {"CONFIRM_ACTION", "CONFIRM_TEMPLATE", "CHANGE_POLICY"}:
                require(
                    values.get("accepted") is True,
                    "Explicit original acceptance must be literal true",
                )
            for name in ("idempotency_key", "reason"):
                if name in values and isinstance(values[name], str):
                    require(
                        bool(values[name].strip()), "Nonblank original identity/reason required"
                    )
            if step.kind == "EXTERNAL_FACT":
                require(
                    values.get("user_id") == str(self.user_id),
                    "The original seed principal cannot be changed",
                )
            if step.kind == "CHANGE_POLICY" and reference_count(values.get("configuration")) == 0:
                normalized = self.validate_configuration(values["configuration"])
                if not isinstance(values.get("reviewed_hash"), dict):
                    require(
                        values["reviewed_hash"] == self.configuration_hash(normalized),
                        "Literal changed policy review hash differs",
                    )
            if step.kind == "DECLARE_POLICY":
                require(
                    reference_count(values.get("configuration")) == 0,
                    "Declaration requires the full original literal policy configuration",
                )
                self.validate_configuration(values["configuration"])
            if negative_destination:
                expected_dto_rejections.append(
                    {
                        "step_id": step.step_id,
                        "kind": step.kind,
                        "field": "intent.destination_account_id",
                        "expected_error": step.expected_error.model_dump(mode="json"),
                        "runtime_rejection_observed": False,
                    }
                )
            elif not unresolved:
                model.model_validate_json(canonical(values))
                literal_count += 1
            symbolic_count += unresolved
        payload = json.loads(actual.model_dump_json())
        validated = canonical(payload)
        self.unchanged()
        return {
            "status": "EXPECTED_DTO_REJECTION_PENDING"
            if expected_dto_rejections
            else (
                "NATIVE_SCHEMA_VALID_SYMBOLIC_RESULTS_PENDING"
                if symbolic_count
                else "NATIVE_SCHEMA_VALID"
            ),
            "method": METHOD,
            "purpose": actual.purpose,
            "input_json_sha256": sha256(raw).hexdigest(),
            "conversion_method": BINDING_METHOD
            if original_case_input_sha256
            else "ORIGINAL_PURPOSE_UNCHANGED_NATIVE_NORMALIZATION_V1",
            "converted_execution_input_sha256": sha256(canonical(converted)).hexdigest(),
            "original_case_input_sha256": original_case_input_sha256,
            "validated_execution_input_sha256": sha256(validated).hexdigest(),
            "validated_execution_input": payload,
            "schema_sources": self.sources,
            "literal_dto_steps": literal_count,
            "unresolved_original_references": symbolic_count,
            "expected_dto_rejections": expected_dto_rejections,
            "runtime_source_results_verified": False,
            "financial_effect_evidence": False,
        }
