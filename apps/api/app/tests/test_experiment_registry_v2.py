"""TOOL_TEST_ONLY pure byte/reference gates; no financial mock, SQL, or actual run."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from app.services import experiment_registry_v2 as b
from app.services.scenario_references import resolve_inputs

from scripts import mvp_arm_executor as arms
from scripts.tests.test_mvp_arm_executor import Fixture as ArmFixture

ROOT = Path(__file__).resolve().parents[4]


def fixture() -> tuple[Path, dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    root = (ROOT / ".runtime/W1-registry-v2-tool-fixtures" / uuid4().hex).resolve()
    root.mkdir(parents=True)
    bindings = {key: "TOOL_TEST_ONLY" for key in b.BINDINGS}
    bindings.update(
        {
            "user_id": str(uuid4()),
            "isolated_db_epoch": str(uuid4()),
            "experiment_run_id": str(uuid4()),
            "execution_mode": "SERVICE_INTEGRATION",
            "purpose": "MVP_FROZEN",
            "arm_id": "P",
            "seed_version": "mvp-301-v6",
        }
    )
    steps: list[dict[str, Any]] = [
        {"step_id": "declare", "kind": "DECLARE_POLICY", "at": "2026-10-03T16:00:00Z"},
        {"step_id": "prepare", "kind": "PREPARE_ACTION", "at": "2026-10-03T16:01:00Z"},
    ]
    return root, {}, bindings, steps


def previous(root: Path, bindings: dict[str, str], steps: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "step_id": "declare",
        "kind": steps[0]["kind"],
        "at": steps[0]["at"],
        "capture_origin": "PRODUCTION_SERVICE_CALL",
        "result": {"policy_id": str(uuid4())},
    }
    raw = b.canonical(
        {
            "protocol": "mvp-raw-observation-v1",
            "kind": "SCENARIO_SERVICE_STEP_RESULT",
            "bindings": bindings,
            "payload": payload,
        }
    )
    path = root / (uuid4().hex + ".json")
    with path.open("xb") as stream:
        stream.write(raw)
    entries = [{"step_id": "declare", "ref": {"path": str(path), "sha256": b.digest(raw)}}]
    return {
        "previous_step_originals": entries,
        "previous_step_inventory_sha256": b.digest(b.canonical(entries)),
    }


def rewrite(reg: dict[str, Any], mutate: Any) -> None:
    ref = reg["previous_step_originals"][0]["ref"]
    path = Path(ref["path"])
    body = b.strict(path.read_bytes())
    mutate(body)
    # New synthetic original, never overwrite an old fixture artifact.
    new_path = path.with_name(uuid4().hex + ".json")
    raw = b.canonical(body)
    with new_path.open("xb") as stream:
        stream.write(raw)
    ref["path"], ref["sha256"] = str(new_path), b.digest(raw)
    reg["previous_step_inventory_sha256"] = b.digest(b.canonical(reg["previous_step_originals"]))


def test_previous_copy_is_bound_and_not_aliased() -> None:
    root, _, bindings, steps = fixture()
    reg = previous(root, bindings, steps)
    result, raw_bytes = b.read_previous(root, reg, bindings, steps, 1)
    assert set(result) == {"declare"}
    assert result["declare"]["result"]["policy_id"]
    assert len(raw_bytes) == 1
    old = next(iter(raw_bytes.values()))
    result["declare"]["result"]["policy_id"] = "changed"
    assert next(iter(raw_bytes.values())) == old


@pytest.mark.parametrize(
    "key",
    [
        "experiment_run_id",
        "case_id",
        "arm_id",
        "user_id",
        "isolated_db_epoch",
        "purpose",
        "input_sha256",
        "source_sha256",
        "rule_sha256",
    ],
)
def test_cross_run_owner_arm_epoch_or_source_rejected(key: str) -> None:
    root, _, bindings, steps = fixture()
    reg = previous(root, bindings, steps)
    rewrite(reg, lambda body: body["bindings"].update({key: "another"}))
    with pytest.raises(b.BridgeRefused, match="STEP_RAW_PROTOCOL_OR_RUN_OWNER_ARM_EPOCH_MISMATCH"):
        b.read_previous(root, reg, bindings, steps, 1)


@pytest.mark.parametrize(
    "case",
    [
        "future",
        "missing_result",
        "failed",
        "clock",
        "origin",
        "hash",
        "inventory",
        "duplicate",
        "outside",
        "naive",
    ],
)
def test_prior_origin_and_inventory_fail_closed(case: str) -> None:
    root, _, bindings, steps = fixture()
    reg = previous(root, bindings, steps)
    if case == "future":
        reg["previous_step_originals"][0]["step_id"] = "prepare"
    elif case == "missing_result":
        rewrite(reg, lambda body: body["payload"].pop("result"))
    elif case == "failed":
        rewrite(reg, lambda body: body["payload"].update({"error": {"code": "FAILED"}}))
    elif case == "clock":
        rewrite(reg, lambda body: body["payload"].update({"at": "2026-10-03T17:00:00Z"}))
    elif case == "origin":
        rewrite(
            reg, lambda body: body["payload"].update({"capture_origin": "UNEXECUTED_EXPECTATION"})
        )
    elif case == "hash":
        reg["previous_step_originals"][0]["ref"]["sha256"] = "0" * 64
    elif case == "inventory":
        reg["previous_step_inventory_sha256"] = "0" * 64
    elif case == "duplicate":
        reg["previous_step_originals"].append(deepcopy(reg["previous_step_originals"][0]))
    elif case == "outside":
        reg["previous_step_originals"][0]["ref"]["path"] = str(root.parent / "external.json")
    elif case == "naive":
        rewrite(reg, lambda body: body["payload"].update({"at": "2026-10-03T16:00:00"}))
    if case != "inventory":
        reg["previous_step_inventory_sha256"] = b.digest(
            b.canonical(reg["previous_step_originals"])
        )
    with pytest.raises(b.BridgeRefused):
        b.read_previous(root, reg, bindings, steps, 1)


def test_standalone_success_message_cannot_be_prior_result() -> None:
    root, _, bindings, steps = fixture()
    reg = previous(root, bindings, steps)
    rewrite(reg, lambda body: body["payload"].update({"result": None, "message": "SUCCESS"}))
    with pytest.raises(b.BridgeRefused, match="NO_ACTUAL_PRIOR_SERVICE_RESULT"):
        b.read_previous(root, reg, bindings, steps, 1)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_original_duplicate_nonfinite_refused(raw: bytes) -> None:
    with pytest.raises(b.BridgeRefused):
        b.strict(raw)


def test_new_bridge_never_downgrades_development_to_formal() -> None:
    root, _, bindings, _ = fixture()
    bindings["purpose"] = "DEVELOPMENT"
    with pytest.raises(b.BridgeRefused, match="EXACT_FROZEN_CONTEXT_REQUIRED"):
        b.FrozenInvocationBridge(
            root=root,
            root_registration_ref={"path": str(root / "absent.json"), "sha256": "0" * 64},
            bindings=bindings,
            database_name="bf_test_" + uuid4().hex,
            now="2026-10-03T16:00:00Z",
        )


def test_root_external_sha_required_before_any_corpus_import() -> None:
    root, _, bindings, _ = fixture()
    path = root / "registration.json"
    path.write_bytes(b'{"protocol":"mvp-arm-provider-registry-v2","status":"READY"}')
    with pytest.raises(b.BridgeRefused, match="ORIGINAL_BYTE_DRIFT"):
        b.FrozenInvocationBridge(
            root=root,
            root_registration_ref={"path": str(path), "sha256": "0" * 64},
            bindings=bindings,
            database_name="bf_test_" + uuid4().hex,
            now="2026-10-03T16:00:00Z",
        )


@pytest.mark.parametrize("hijack", [False, True])
def test_loaded_function_is_actual_code_bytes_not_named_label(hijack: bool) -> None:
    root, _, _, _ = fixture()
    path = root / "pure_function.py"
    raw = b"def resolve_inputs(value):\n    return value\n"
    path.write_bytes(raw)
    module = ModuleType("TOOL_TEST_ONLY_pure")
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    if hijack:
        exec(
            compile(
                b"def resolve_inputs(value):\n    return {'status':'SUCCESS'}\n",
                str(path),
                "exec",
                dont_inherit=True,
            ),
            module.__dict__,
        )
        with pytest.raises(b.BridgeRefused, match="LOADED_GRAPH_OR_REFERENCE_FUNCTION_DRIFT"):
            b.bind_functions(module, path, ("resolve_inputs",))
    else:
        b.bind_functions(module, path, ("resolve_inputs",))


def arm_fixture(arm: str) -> tuple[ArmFixture, arms.SimulationContext]:
    root, _, _, _ = fixture()
    f = ArmFixture(root, arm)
    value = deepcopy(f.context_value)
    value["protocol"] = "mvp-arm-isolated-context-v2"
    value["bindings"].update(purpose="MVP_FROZEN", execution_mode="SERVICE_INTEGRATION")
    path = root / "TOOL_ONLY_root_registration.json"
    path.write_bytes(b'{"TOOL_TEST_ONLY":true}')
    value["root_registration_ref"] = {"path": str(path), "sha256": b.digest(path.read_bytes())}
    context = arms.SimulationContext.parse(value)
    f.rule["purpose"] = "MVP_FROZEN"
    f.rule = deepcopy(f.rule)
    f.view["bindings"] = context.bindings
    return f, context


@pytest.mark.parametrize(("arm", "amount"), [("B0", 200), ("B1", 700), ("B2", 750)])
def test_actual_v2_arithmetic_with_dynamic_ids_keeps_original_rule(arm: str, amount: int) -> None:
    f, context = arm_fixture(arm)
    if arm == "B0":
        f.rule["manual_actions"][0]["intent"]["policy_id"] = {
            "$ref": {"step_id": "declare", "pointer": "/result/policy_id"}
        }
        # A later manual opportunity must remain unresolved and never affect this one.
        f.rule["manual_actions"].append(
            {
                "opportunity_id": "future",
                "amount_cents": 500,
                "intent": {"$ref": {"step_id": "unexecuted", "pointer": "/result/intent"}},
            }
        )
    if arm == "B2":
        f.rule["fixed_policy_version_ids"] = [
            {"$ref": {"step_id": "declare", "pointer": "/result/version_id"}}
        ]
    original = b.canonical(f.rule)
    previous_rows = {
        "declare": {
            "result": {
                "policy_id": f.intent["policy_id"],
                "version_id": f.view["policy_versions"][0]["id"],
            }
        }
    }
    resolution = b.resolve_rule_inputs(
        context.bindings,
        context.root_registration_ref or {},
        f.rule,
        "tool-op",
        previous_rows,
        resolve_inputs,
    )
    f.view["rule_resolution"] = resolution
    selector = arms.candidate_selector_v2(context, f.rule, "tool-op")
    assert selector is not None
    candidate = selector(f.view)
    assert candidate is not None
    assert candidate["amount_cents"] == amount
    assert candidate["rule_value_sha256"] == b.digest(original)
    assert candidate["rule_resolution_binding_sha256"] == b.digest(b.canonical(resolution))
    assert candidate["execution_mode"] is None and candidate["execution_status"] == "NOT_EXECUTED"
    assert resolution["financial_permission_verified"] is False
    assert resolution["financial_effect_evidence"] is False
    assert b.canonical(f.rule) == original
    if arm in {"B0", "B2"}:
        assert resolution["resolution_refs"][0]["step_id"] == "declare"


@pytest.mark.parametrize("arm", ["P", "B3"])
def test_v2_retains_actual_planner_selector_none(arm: str) -> None:
    f, context = arm_fixture(arm)
    assert arms.candidate_selector_v2(context, f.rule, "tool-op") is None


@pytest.mark.parametrize("arm", ["B0", "B1"])
def test_manual_amount_and_shared_threshold_cannot_reference_p_result(arm: str) -> None:
    f, context = arm_fixture(arm)
    ref = {"$ref": {"step_id": "p_plan", "pointer": "/result/amount_cents"}}
    if arm == "B0":
        f.rule["manual_actions"][0]["amount_cents"] = ref
    else:
        f.rule["threshold_cents"] = ref
    with pytest.raises(b.BridgeRefused, match="FROZEN_LITERAL"):
        b.resolve_rule_inputs(
            context.bindings,
            context.root_registration_ref or {},
            f.rule,
            "tool-op",
            {"p_plan": {"result": {"amount_cents": 100}}},
            resolve_inputs,
        )


@pytest.mark.parametrize("field", ["root_registration_ref", "bindings"])
def test_v2_context_mutation_is_not_cross_request_authority(field: str) -> None:
    _, context = arm_fixture("B1")
    if field == "bindings":
        context.bindings["user_id"] = str(uuid4())
    else:
        assert context.root_registration_ref is not None
        context.root_registration_ref["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="context changed"):
        context.assert_current()


def test_v1_original_digest_and_default_no_v2_registration() -> None:
    root, _, _, _ = fixture()
    f = ArmFixture(root)
    assert f.context.root_registration_ref is None
    assert f.context.original_digest == b.digest(
        b.canonical([f.context.bindings, f.context.database_name, f.context.now])
    )
    extra = deepcopy(f.context_value)
    extra["root_registration_ref"] = {"path": str(root / "no.json"), "sha256": "a" * 64}
    with pytest.raises(ValueError, match="V1 cannot silently"):
        arms.SimulationContext.parse(extra)


def test_missing_v2_registry_refuses_before_any_database_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import experiment_arms as provider

    _, context = arm_fixture("B1")
    assert context.root_registration_ref is not None
    context.root_registration_ref["sha256"] = "0" * 64
    altered = arms.SimulationContext(
        context.bindings, context.database_name, context.now, context.root_registration_ref
    )
    called = []

    def no_engine(*args: Any, **kwargs: Any) -> Any:
        called.append("DB_FACTORY_CALLED")
        raise AssertionError("No database factory allowed in pure registry refusal")

    monkeypatch.setattr(provider, "create_engine", no_engine)
    with pytest.raises(b.BridgeRefused, match="ORIGINAL_BYTE_DRIFT"):
        provider.verify_simulation_context(altered)
    assert called == []


def test_resolved_rule_value_hash_and_original_digest_tamper_refused() -> None:
    f, context = arm_fixture("B1")
    resolution = b.resolve_rule_inputs(
        context.bindings, context.root_registration_ref or {}, f.rule, "tool-op", {}, resolve_inputs
    )
    f.view["rule_resolution"] = resolution
    selector = arms.candidate_selector_v2(context, f.rule, "tool-op")
    assert selector is not None
    resolution["resolved_arm_algorithm"]["threshold_cents"] = 0
    with pytest.raises(ValueError, match="Resolved V2 rule bytes"):
        selector(f.view)


def test_v2_zero_candidate_stays_absence() -> None:
    f, context = arm_fixture("B1")
    f.rule["threshold_cents"] = 1000
    f.view["rule_resolution"] = b.resolve_rule_inputs(
        context.bindings, context.root_registration_ref or {}, f.rule, "tool-op", {}, resolve_inputs
    )
    selector = arms.candidate_selector_v2(context, f.rule, "tool-op")
    assert selector is not None and selector(f.view) is None
