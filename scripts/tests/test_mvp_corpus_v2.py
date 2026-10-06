"""TOOL_ONLY V2 schema and original-file risks; no PG, financial or frozen24 data."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts import mvp_corpus_v2 as tool

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "scripts"
from scripts.mvp_native_schema import (  # noqa: E402
    BINDING_METHOD,
    SOURCES,
    NativeAdapter,
    canonical,
    strict_json,
    validate_json_schema,
)


def inventory() -> dict[str, str]:
    names = set(SOURCES) | {
        path.relative_to(ROOT).as_posix()
        for path in (CANDIDATE / "mvp_corpus_v2.py", CANDIDATE / "mvp_native_schema.py")
    }
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


@pytest.fixture
def native() -> NativeAdapter:
    return NativeAdapter(ROOT, inventory())


def execution(kind: str = "SNAPSHOT", inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "protocol": "bounded-funds-scenario-v1",
        "scenario_id": "TOOL_ONLY_case",
        "purpose": "DEVELOPMENT",
        "dataset_id": "TOOL_ONLY_schema",
        "family_id": "TOOL_ONLY_family",
        "initial_state": {"mode": "EXISTING"},
        "steps": [
            {"step_id": "one", "kind": kind, "at": "2026-10-05T00:00:00Z", "inputs": inputs or {}}
        ],
    }


def validate(native: NativeAdapter, data: dict[str, Any]) -> dict[str, Any]:
    return native.validate_execution(canonical(data), "DEVELOPMENT")


@pytest.mark.parametrize(
    "kind",
    [
        "SNAPSHOT",
        "LOOKUP_ACCOUNT",
        "READ_ACTION",
        "READ_POLICY",
        "SUSPEND_POLICY",
        "REVOKE_POLICY",
        "CREATE_GOAL",
        "RUN_RECOVERY",
        "READ_RECOVERY",
        "ISSUE_EARLY_QUOTE",
    ],
)
def test_original_new_kind_dto_contract_without_io(native: NativeAdapter, kind: str) -> None:
    keys = {
        "SNAPSHOT": {},
        "LOOKUP_ACCOUNT": {"external_ref": "TOOL_ONLY-original"},
        "READ_ACTION": {"action_id": str(uuid4())},
        "READ_POLICY": {"policy_id": str(uuid4())},
        "SUSPEND_POLICY": {"policy_id": str(uuid4()), "expected_version_id": str(uuid4())},
        "REVOKE_POLICY": {"policy_id": str(uuid4()), "expected_version_id": str(uuid4())},
        "CREATE_GOAL": {
            "policy_id": str(uuid4()),
            "expected_version_id": str(uuid4()),
            "account_id": str(uuid4()),
        },
        "RUN_RECOVERY": {"idempotency_key": "TOOL_ONLY-recovery"},
        "READ_RECOVERY": {"decision_run_id": str(uuid4())},
        "ISSUE_EARLY_QUOTE": {"position_id": str(uuid4())},
    }
    result = validate(native, execution(kind, keys[kind]))
    assert result["status"] == "NATIVE_SCHEMA_VALID"
    assert result["financial_effect_evidence"] is False
    assert result["schema_sources"] == inventory()


def test_symbolic_original_refs_stay_pending_and_never_become_fake_values(
    native: NativeAdapter,
) -> None:
    data = execution("LOOKUP_ACCOUNT", {"external_ref": "TOOL_ONLY-cash"})
    reference = {"$ref": {"step_id": "one", "pointer": "/result/account/id"}}
    data["steps"].append(
        {
            "step_id": "two",
            "kind": "CREATE_GOAL",
            "at": "2026-10-05T00:00:01Z",
            "inputs": {
                "policy_id": str(uuid4()),
                "expected_version_id": str(uuid4()),
                "account_id": reference,
            },
        }
    )
    result = validate(native, data)
    assert result["status"] == "NATIVE_SCHEMA_VALID_SYMBOLIC_RESULTS_PENDING"
    assert result["unresolved_original_references"] == 1
    assert result["validated_execution_input"]["steps"][1]["inputs"]["account_id"] == reference
    assert result["runtime_source_results_verified"] is False


@pytest.mark.parametrize(
    "reference",
    [
        "one#/result/account/id",
        {"step_id": "one", "pointer": "/result/account/id", "grant": True},
        {"step_id": "future", "pointer": "/result/account/id"},
        {"step_id": "one", "pointer": "/error/code"},
    ],
)
def test_legacy_extended_forward_or_error_refs_rejected(
    native: NativeAdapter, reference: Any
) -> None:
    data = execution("LOOKUP_ACCOUNT", {"external_ref": "TOOL_ONLY-cash"})
    data["steps"].append(
        {
            "step_id": "two",
            "kind": "READ_ACTION",
            "at": "2026-10-05T00:00:01Z",
            "inputs": {"action_id": {"$ref": reference}},
        }
    )
    with pytest.raises(ValueError):
        validate(native, data)


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "principal_id",
        "amount_cents",
        "balance_cents",
        "autonomy_level",
        "result",
        "success",
        "grant",
    ],
)
def test_unregistered_money_principal_or_success_injection_rejected(
    native: NativeAdapter, field: str
) -> None:
    with pytest.raises(ValueError):
        validate(native, execution("READ_ACTION", {"action_id": str(uuid4()), field: True}))


@pytest.mark.parametrize(
    "kind,fault,allowed",
    [
        ("RUN_RECOVERY", "DROP_BANK_RESPONSE", False),
        ("RUN_RECOVERY", "FAIL_APPLICATION_PROJECTION", True),
        ("READ_ACTION", "DROP_BANK_RESPONSE", False),
        ("EXECUTE_ACTION", "DROP_BANK_RESPONSE", True),
        ("EXECUTE_ACTION", "FAIL_APPLICATION_PROJECTION", True),
    ],
)
def test_current_kind_fault_pair_is_original_schema_contract(
    native: NativeAdapter, kind: str, fault: str, allowed: bool
) -> None:
    inputs = (
        {"idempotency_key": "TOOL_ONLY"} if kind == "RUN_RECOVERY" else {"action_id": str(uuid4())}
    )
    data = execution(kind, inputs)
    data["steps"][0]["fault"] = fault
    if allowed:
        assert validate(native, data)["status"] == "NATIVE_SCHEMA_VALID"
    else:
        with pytest.raises(ValueError):
            validate(native, data)


@pytest.mark.parametrize(
    "case",
    ["naive", "backward", "duplicate", "expected_error_bool", "unexpected_result", "promote"],
)
def test_full_scenario_and_step_contract_not_just_kind_literals(
    native: NativeAdapter, case: str
) -> None:
    data = execution()
    if case == "naive":
        data["steps"][0]["at"] = "2026-10-05T00:00:00"
    elif case in {"backward", "duplicate"}:
        item = copy.deepcopy(data["steps"][0])
        if case == "backward":
            item.update(step_id="two", at="2026-10-04T00:00:00Z")
        data["steps"].append(item)
    elif case == "expected_error_bool":
        data["steps"][0]["expected_error"] = {"code": "EXPECTED", "status_code": True}
    elif case == "unexpected_result":
        data["steps"][0]["result"] = {"status": "SUCCEEDED"}
    else:
        data.update(purpose="MVP_FROZEN", frozen_case_sha256="a" * 64)
    with pytest.raises(ValueError):
        validate(native, data)


def test_explicit_expected_error_is_retained_without_becoming_observed_error(
    native: NativeAdapter,
) -> None:
    data = execution("READ_ACTION", {"action_id": str(uuid4())})
    data["steps"][0]["expected_error"] = {"code": "NOT_FOUND", "status_code": 404}
    output = validate(native, data)
    assert output["validated_execution_input"]["steps"][0]["expected_error"] == {
        "code": "NOT_FOUND",
        "status_code": 404,
    }
    assert output["financial_effect_evidence"] is False


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_strict_original_json_rejects_ambiguous_bytes(raw: bytes) -> None:
    with pytest.raises(ValueError):
        strict_json(raw)


def test_unknown_native_schema_keyword_is_not_ignored() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        validate_json_schema(4, {"type": "integer", "multipleOf": 3}, {})


def test_registration_does_not_accept_subset_or_fake_source_sha() -> None:
    rows = inventory()
    rows.pop(SOURCES[0])
    with pytest.raises(ValueError, match="inventory incomplete"):
        NativeAdapter(ROOT, rows)


def test_all_21_registered_kinds_match_actual_typed_and_delegated_dispatch(
    native: NativeAdapter,
) -> None:
    registered = native.registration("TOOL_ONLY")
    actual = tool.runtime_kinds(
        (ROOT / SOURCES[0]).read_bytes(),
        (ROOT / SOURCES[2]).read_bytes(),
        (ROOT / SOURCES[3]).read_bytes(),
    )
    assert len(actual) == 21
    assert set(registered["step_schemas"]) == actual
    assert tool.MVP_QUOTAS == {
        "NORMAL": 6,
        "GOAL_OBLIGATION_CONFLICT": 6,
        "CONSUMPTION_SHRINK": 4,
        "FIXED_LIQUIDITY_CONFLICT": 4,
        "POLICY_VERSION": 2,
        "TRANSFER_AMBIGUITY": 2,
    }
    assert tool.ARMS == {"B0", "B1", "B2", "B3", "P"} and len(tool.METRICS) == 14
    assert tool.digest((ROOT / "scripts/mvp_corpus.py").read_bytes()) == tool.LEGACY_SOURCE_SHA256


@pytest.mark.parametrize(
    "fault,allowed", [("FAIL_APPLICATION_PROJECTION", True), ("DROP_BANK_RESPONSE", False)]
)
def test_external_fact_original_fault_contract_and_principal(
    native: NativeAdapter, fault: str, allowed: bool
) -> None:
    inputs = {
        "user_id": str(native.user_id),
        "idempotency_key": "TOOL_ONLY:external",
        "external_ref": "TOOL_ONLY:original",
        "kind": "INCOME",
        "account_id": str(uuid4()),
        "amount_cents": 1234,
        "counterparty_ref": "TOOL_ONLY-counterparty",
        "occurred_at": "2026-10-05T00:00:00Z",
    }
    data = execution("EXTERNAL_FACT", inputs)
    data["steps"][0]["fault"] = fault
    if allowed:
        assert validate(native, data)["status"] == "NATIVE_SCHEMA_VALID"
    else:
        with pytest.raises(ValueError):
            validate(native, data)
    data["steps"][0]["fault"] = "NONE"
    data["steps"][0]["inputs"]["user_id"] = str(uuid4())
    with pytest.raises(ValueError, match="principal"):
        validate(native, data)


def test_v1_conversion_requires_explicit_author_revision_and_both_original_shas() -> None:
    fixture = Fixture()
    old = fixture.original(
        "V1-TOOL_ONLY-original.json",
        {
            "protocol": "bounded-funds-case-input-v1",
            "intended_purpose": "TOOL_ONLY",
            "scope": "Preserved authored V1 fixture; never silently converted",
        },
    )
    fixture.input["translation"] = {
        "protocol": "EXPLICIT_V1_TO_V2_AUTHORED_INPUT_REVISION",
        "original_input_ref": old,
    }
    path = fixture.save()
    result = tool.validate(path, ROOT)
    assert result["status"] == "VALID_CORPUS_STRUCTURE", result
    draft = tool.Draft(path, ROOT)
    assert fixture.root / old["path"] in draft.originals
    assert tool.digest(draft.originals[fixture.root / old["path"]]) == old["sha256"]
    fixture.input["translation"]["protocol"] = "SILENT_AUTO_TRANSLATION"
    assert tool.validate(fixture.save(), ROOT)["status"] == "INVALID"


def test_v1_development_input_cannot_be_promoted_by_v2_translation() -> None:
    fixture = Fixture()
    old = fixture.original(
        "V1-development.json",
        {
            "protocol": "bounded-funds-case-input-v1",
            "intended_purpose": "DEVELOPMENT",
        },
    )
    fixture.input["translation"] = {
        "protocol": "EXPLICIT_V1_TO_V2_AUTHORED_INPUT_REVISION",
        "original_input_ref": old,
    }
    result = tool.validate(fixture.save(), ROOT)
    assert result["status"] == "INVALID" and "purpose" in result["reason"]


class Fixture:
    def __init__(self) -> None:
        self.root = ROOT / ".runtime" / "W1-corpus-v2-installed-TOOL_ONLY-fixtures" / uuid4().hex
        self.root.mkdir(parents=True)
        self.sources = inventory()
        self.oracle = self.root / "calculator.py"
        self.oracle.write_bytes(
            b"# TOOL_ONLY stdlib counter, not a financial oracle\n"
            b"def count(rows):\n    return len(rows)\n"
        )
        self.sources[self.oracle.relative_to(ROOT).as_posix()] = tool.digest(
            self.oracle.read_bytes()
        )
        self.adapter = NativeAdapter(ROOT, self.sources)
        self.current: dict[str, Any] = {}
        self.input = self.envelope("TOOL_ONLY_case", "TOOL_ONLY", execution())
        development = execution("LOOKUP_ACCOUNT", {"external_ref": "TOOL_ONLY-dev"})
        development["scenario_id"] = "TOOL_ONLY_dev"
        self.development = self.envelope("TOOL_ONLY_dev", "DEVELOPMENT", development)

    @staticmethod
    def envelope(identity: str, purpose: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": tool.CASE_PROTOCOL,
            "case_id": identity,
            "family_id": "TOOL_ONLY_family",
            "intended_purpose": purpose,
            "seed_version": "mvp-301-v6",
            "execution_input": data,
            "data_origin": {"kind": "TOOL_ONLY"},
        }

    def original(self, name: str, value: Any) -> dict[str, str]:
        raw = canonical(value)
        path = self.root / name
        path.write_bytes(raw)
        return {"path": name, "sha256": tool.digest(raw)}

    def save(self) -> Path:
        fact = self.original("facts.json", {"purpose": "TOOL_ONLY", "scope": "No real bank state"})
        source = self.original(
            "source.json",
            {
                "purpose": "TOOL_ONLY",
                "files": [
                    {"path": name, "sha256": digest}
                    for name, digest in sorted(self.sources.items())
                ],
            },
        )
        seed = self.original(
            "seed.json",
            {
                "purpose": "TOOL_ONLY",
                "seed_version": "mvp-301-v6",
                "seed_source_sha256": self.sources["apps/api/app/services/demo_seed.py"],
                "initial_fact_refs": [fact],
            },
        )
        schema = self.original("schema.json", self.adapter.registration("TOOL_ONLY"))
        design = self.original(
            "design.json", {"purpose": "TOOL_ONLY", "scope": "No formal case design"}
        )
        development = self.original(
            "development.json",
            {
                "purpose": "DEVELOPMENT",
                "inventory_status": "REGISTERED_COMPLETE",
                "inputs": [self.original("development-input.json", self.development)],
            },
        )
        self.input.update(seed_sha256=seed["sha256"], source_sha256=source["sha256"])
        input_ref = self.original("input.json", self.input)
        oracle_ref = {"path": self.oracle.name, "sha256": tool.digest(self.oracle.read_bytes())}
        rules = {
            arm: self.original(
                arm + ".json",
                {
                    "purpose": "TOOL_ONLY",
                    "arm_id": arm,
                    "mechanism_id": "TOOL_ONLY-" + arm,
                    "execution_mode": "TOOL_ONLY",
                    "implementation_source_refs": [oracle_ref],
                },
            )
            for arm in tool.ARMS
        }
        oracle = {
            "purpose": "TOOL_ONLY",
            "case_id": self.input["case_id"],
            "input_sha256": input_ref["sha256"],
            "seed_sha256": seed["sha256"],
            "source_sha256": source["sha256"],
            "design_sha256": design["sha256"],
            "implementation_origin": "INDEPENDENT_INTEGER_ORACLE",
            "source_refs": [oracle_ref],
            "metric_calculators": {"TOOL_ONLY": "count"},
            "rule_sha256_by_arm": {arm: ref["sha256"] for arm, ref in rules.items()},
            **{
                key: []
                for key in (
                    "protection_timeline",
                    "permission_intervals",
                    "due_checkpoints",
                    "safe_auto_opportunity_ids",
                    "required_evidence_manifest",
                    "audit_checkpoint_ids",
                    "expected_causes",
                )
            },
        }
        self.current = {
            "protocol": tool.DRAFT_PROTOCOL,
            "purpose": "TOOL_ONLY",
            "profile": "TOOL",
            "source_ref": source,
            "seed_ref": seed,
            "schema_ref": schema,
            "design_ref": design,
            "development_inventory_ref": development,
            "cases": [
                {
                    "case_id": self.input["case_id"],
                    "family_id": self.input["family_id"],
                    "input_ref": input_ref,
                    "oracle_ref": self.original("oracle.json", oracle),
                    "rule_refs": rules,
                }
            ],
        }
        return self.root / self.original("draft.json", self.current)["path"]


def test_native_input_original_bytes_sha_and_validated_execution_sha_are_both_bound() -> None:
    fixture = Fixture()
    path = fixture.save()
    result = tool.validate(path, ROOT)
    assert result["status"] == "VALID_CORPUS_STRUCTURE", result
    binding = result["cases"][0]["native_execution_binding"]
    assert (
        binding["original_input_byte_sha256"] == fixture.current["cases"][0]["input_ref"]["sha256"]
    )
    assert binding["validated_execution_input_sha256"] == tool.digest(
        canonical(binding["validated_execution_input"])
    )
    assert binding["purpose"] == "DEVELOPMENT" and binding["corpus_purpose"] == "TOOL_ONLY"
    assert binding["financial_effect_evidence"] is False


@pytest.mark.parametrize(
    "case",
    [
        "legacy_protocol",
        "unimplemented",
        "wrong_source",
        "missing_oracle",
        "wrong_quota",
        "financial_output",
    ],
)
def test_old_original_gates_are_retained_in_v2(case: str) -> None:
    fixture = Fixture()
    if case == "legacy_protocol":
        fixture.input["protocol"] = "bounded-funds-case-input-v1"
    elif case == "unimplemented":
        fixture.input["execution_input"]["steps"][0]["kind"] = "ADVANCE_BANK"
    elif case == "financial_output":
        fixture.input["result"] = {"status": "SUCCEEDED"}
    path = fixture.save()
    if case == "wrong_source":
        source = json.loads((fixture.root / "source.json").read_bytes())
        source["files"][0]["sha256"] = "0" * 64
        fixture.current["source_ref"] = fixture.original("source.json", source)
    elif case == "missing_oracle":
        fixture.current["cases"][0].pop("oracle_ref")
    elif case == "wrong_quota":
        fixture.current["profile"] = "MVP"
    path = fixture.root / fixture.original("draft.json", fixture.current)["path"]
    assert tool.validate(path, ROOT)["status"] == "INVALID"


def test_tool_only_byte_archive_is_not_formal_freeze_or_case_execution() -> None:
    fixture = Fixture()
    path = fixture.save()
    destination = fixture.root / "tool-only-byte-archive"
    archived = tool.freeze(path, destination, ROOT)
    assert archived["purpose"] == "TOOL_ONLY" and archived["financial_effect_evidence"] is False
    assert (
        tool.verify(destination, ROOT, archived["manifest_sha256"])["status"]
        == "VERIFIED_FROZEN_BYTES_AND_CURRENT_SOURCE"
    )
    replayed = tool.revalidate_archive(destination, ROOT, archived["manifest_sha256"])
    assert replayed.cases == archived["cases"]
    assert not (destination / "_logical_originals").exists()
    rejected = tool.prepare_frozen_case(
        destination, ROOT, archived["manifest_sha256"], "TOOL_ONLY_case", "MVP_FROZEN"
    )
    assert rejected["status"] == "REFUSED"
    with pytest.raises(ValueError):
        tool.freeze(path, destination, ROOT)


def general_configuration() -> dict[str, Any]:
    # Pure authored fixture, not a registered initial-24 input or observed policy.
    return {
        "type": "asset_authorization",
        "scope": "general_idle_funds",
        "allowed_asset_classes": ["CASH_MGMT_T0"],
        "max_auto_managed_cents": 80000,
        "single_action_cap_cents": 30000,
        "max_redemption_delay_days": 0,
        "max_lock_days": 0,
        "allow_auto_recovery_without_penalty": True,
    }


def test_declaration_original_dto_configuration_validated_without_authority(
    native: NativeAdapter,
) -> None:
    data = execution(
        "DECLARE_POLICY",
        {
            "configuration": general_configuration(),
            "idempotency_key": "TOOL_ONLY:declare.1",
            "expected_epoch_id": str(uuid4()),
        },
    )
    result = validate(native, data)
    assert result["status"] == "NATIVE_SCHEMA_VALID"
    assert result["financial_effect_evidence"] is False
    assert result["runtime_source_results_verified"] is False


@pytest.mark.parametrize("accepted", [True, False])
def test_original_confirmation_false_is_valid_input_for_real_expected_refusal(
    native: NativeAdapter, accepted: bool
) -> None:
    data = execution(
        "CONFIRM_POLICY",
        {"proposal_id": str(uuid4()), "reviewed_hash": "a" * 64, "accepted": accepted},
    )
    if not accepted:
        data["steps"][0]["expected_error"] = {"code": "CONFIRMATION_REQUIRED", "status_code": 422}
    result = validate(native, data)
    assert result["validated_execution_input"]["steps"][0]["inputs"]["accepted"] is accepted


@pytest.mark.parametrize(
    "attack",
    [
        "extra_grant",
        "configuration_grant",
        "invalid_cap",
        "nonascii_key",
        "blank_key",
        "boolean_amount",
        "symbolic_configuration",
    ],
)
def test_declaration_cannot_hide_financial_grants_or_configuration_validators(
    native: NativeAdapter, attack: str
) -> None:
    data = execution("LOOKUP_ACCOUNT", {"external_ref": "TOOL_ONLY"})
    declaration: dict[str, Any] = {
        "configuration": general_configuration(),
        "idempotency_key": "TOOL_ONLY-declare",
        "expected_epoch_id": str(uuid4()),
    }
    if attack == "extra_grant":
        declaration["grants_authority"] = True
    elif attack == "configuration_grant":
        declaration["configuration"]["autonomy_level"] = "AUTO_EXECUTE"
    elif attack == "invalid_cap":
        declaration["configuration"]["single_action_cap_cents"] = 80001
    elif attack == "nonascii_key":
        declaration["idempotency_key"] = "非ASCII"
    elif attack == "blank_key":
        declaration["idempotency_key"] = " "
    elif attack == "boolean_amount":
        declaration["configuration"]["single_action_cap_cents"] = True
    else:
        declaration["configuration"] = {"$ref": {"step_id": "one", "pointer": "/result/account"}}
    data["steps"].append(
        {
            "step_id": "two",
            "kind": "DECLARE_POLICY",
            "at": "2026-10-05T00:00:01Z",
            "inputs": declaration,
        }
    )
    with pytest.raises(ValueError):
        validate(native, data)


def test_changed_policy_uses_original_configuration_hash_and_acceptance(
    native: NativeAdapter,
) -> None:
    config = native.validate_configuration(general_configuration())
    data = execution(
        "CHANGE_POLICY",
        {
            "policy_id": str(uuid4()),
            "expected_version_id": str(uuid4()),
            "configuration": config,
            "reviewed_hash": native.configuration_hash(config),
            "accepted": True,
            "reason": "TOOL_ONLY-decrease",
            "idempotency_key": "TOOL_ONLY-change",
        },
    )
    assert validate(native, data)["status"] == "NATIVE_SCHEMA_VALID"
    data["steps"][0]["inputs"]["reviewed_hash"] = "f" * 64
    with pytest.raises(ValueError, match="hash differs"):
        validate(native, data)


def test_original_case_digest_conversion_is_explicit_and_never_mutates_input(
    native: NativeAdapter,
) -> None:
    data = execution()
    data["purpose"] = "MVP_FROZEN"
    raw = canonical(data)
    # Schema-only unit: digest of these authored bytes, no fabricated 24-case freeze.
    original_sha = tool.digest(raw)
    result = native.validate_execution(raw, "MVP_FROZEN", original_case_input_sha256=original_sha)
    assert canonical(data) == raw and "frozen_case_sha256" not in data
    assert result["conversion_method"] == BINDING_METHOD
    assert result["input_json_sha256"] == original_sha
    assert result["original_case_input_sha256"] == original_sha
    assert result["validated_execution_input"]["frozen_case_sha256"] == original_sha
    assert result["purpose"] == "MVP_FROZEN" and result["financial_effect_evidence"] is False
    assert (
        tool.digest(canonical(result["validated_execution_input"]))
        == result["validated_execution_input_sha256"]
    )


@pytest.mark.parametrize(
    "attack", ["development", "foreign_digest", "missing_original_sha", "uppercase_sha"]
)
def test_digest_conversion_cannot_promote_or_rebind_authored_input(
    native: NativeAdapter, attack: str
) -> None:
    data = execution()
    original = "a" * 64
    if attack != "development":
        data["purpose"] = "MVP_FROZEN"
    if attack == "foreign_digest":
        data["frozen_case_sha256"] = "b" * 64
    if attack == "uppercase_sha":
        original = "A" * 64
    with pytest.raises(ValueError):
        native.validate_execution(
            canonical(data),
            "MVP_FROZEN",
            original_case_input_sha256=None if attack == "missing_original_sha" else original,
        )


@pytest.mark.parametrize(
    "attack",
    [
        "manifest_sha",
        "original_bytes",
        "graph_alias",
        "case_binding",
        "tool_promoted",
        "extra_file",
    ],
)
def test_fresh_archive_hook_refuses_tampered_graph_originals_and_purpose(attack: str) -> None:
    fixture = Fixture()
    destination = fixture.root / "TOOL_ONLY-archive"
    archived = tool.freeze(fixture.save(), destination, ROOT)
    original_sha = archived["manifest_sha256"]
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if attack == "manifest_sha":
        original_sha = "0" * 64
    elif attack == "original_bytes":
        (destination / manifest["files"][0]["path"]).write_bytes(b"tampered TOOL_ONLY")
    elif attack == "extra_file":
        (destination / "unregistered.json").write_bytes(b"{}")
    else:
        if attack == "graph_alias":
            artifacts = [
                entry
                for entry in manifest["files"]
                if entry["original_artifact_relative_path"] is not None
            ]
            artifacts[1]["original_artifact_relative_path"] = artifacts[0][
                "original_artifact_relative_path"
            ]
        elif attack == "case_binding":
            manifest["cases"][0]["native_execution_binding"]["validated_execution_input"][
                "dataset_id"
            ] = "tampered"
        else:
            manifest["purpose"] = "MVP_FROZEN"
        manifest_path.write_bytes(canonical(manifest))
        original_sha = tool.digest(manifest_path.read_bytes())
    with pytest.raises(ValueError):
        tool.revalidate_archive(destination, ROOT, original_sha)
