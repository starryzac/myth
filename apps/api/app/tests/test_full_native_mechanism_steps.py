"""Private consumer contract risks with synthetic signed sessions/doubles, no PG or bank proof."""

import copy
import json
from contextlib import nullcontext
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from app.domain.boundary import compute_boundary
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.execution import execution_effect_hash
from app.domain.execution_types import ExecutionValidation
from app.domain.full_asset_execution import FullAssetPrepareRequest
from app.domain.full_experiment_asset_execution import FullExperimentAssetRequest
from app.domain.full_experiment_asset_selection import RegisteredFullMechanismRule
from app.services import full_native_mechanism_steps as module
from app.services.action_contracts import ActionResponse
from app.services.full_experiment_asset_execution import FullExperimentAssetLookup
from app.services.full_native_steps import FullNativeSteps
from app.services.local_actor_sessions import COOKIE_NAME, issue_local_user_session
from app.services.policy_lifecycle import PolicyLifecycleError
from app.tests.test_execution_domain import NOW, context, purchase_example
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

EPOCH = UUID(int=77)
KEY = "SYNTHETIC-TOOL-ONLY-GENERAL"
SECRET = "SYNTHETIC_LOCAL_USER_SECRET_NO_REAL_CREDENTIAL"


def request() -> FullAssetPrepareRequest:
    return FullAssetPrepareRequest(
        full_policy_id=UUID(int=20),
        expected_full_policy_version_id=UUID(int=21),
        mvp_asset_policy_id=UUID(int=22),
        expected_mvp_policy_version_id=UUID(int=23),
        expected_epoch_id=EPOCH,
        idempotency_key=KEY,
    )


def locator() -> RegisteredFullMechanismRule:
    return RegisteredFullMechanismRule(original_path=".runtime/TOOL_ONLY.json", sha256="a" * 64)


def action(status: str = "PLANNED") -> ActionResponse:
    effect, _ = purchase_example()
    effect = effect.model_copy(update={"user_id": DEMO_USER_ID})
    return ActionResponse(
        user_id=DEMO_USER_ID,
        action_id=effect.operation_id,
        decision_run_id=UUID(int=88),
        status=status,
        autonomy_level="ASK_ONCE",
        effect=effect,
        effect_hash=execution_effect_hash(effect),
        prepared_at=NOW,
        as_of=NOW,
        prepared_validation=ExecutionValidation(
            status="CONFIRMATION_REQUIRED",
            effect_hash=execution_effect_hash(effect),
            baseline_boundary=compute_boundary(context().snapshot, [], [], []),
        ),
        bank_status="UNKNOWN" if status == "UNKNOWN" else None,
    )


def original(status: str = "PLANNED") -> FullExperimentAssetLookup:
    return FullExperimentAssetLookup(
        user_id=DEMO_USER_ID,
        idempotency_key=KEY,
        status="RECORDED",
        original_request=FullExperimentAssetRequest(
            original_request=request(), rule_original=locator()
        ),
        original_action_request={"tool_only_original": True},
        request_hash="b" * 64,
        action=action(status),
    )


@pytest.fixture
def consumer(monkeypatch: pytest.MonkeyPatch) -> module.FullNativeMechanismSteps:
    monkeypatch.setenv("BF_LOCAL_USER_SECRET", SECRET)
    monkeypatch.setenv("BF_LOCAL_SIGNING_KEY", "71" * 32)
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "b" * 32)
    native = FullNativeSteps(engine, DEMO_USER_ID, EPOCH, NOW)
    monkeypatch.setattr(native, "_context", lambda now: None)  # Explicit TOOL_ONLY, never SQL.
    client = TestClient(FastAPI())
    native._client = client
    token, _ = issue_local_user_session(DEMO_USER_ID, "bounded-user", SECRET, NOW)
    client.cookies.set(COOKIE_NAME, token)
    monkeypatch.setattr(module, "audit_command_guard", lambda *args: nullcontext())
    return module.FullNativeMechanismSteps(native, {"registered-P": locator()})


@pytest.mark.parametrize("field", ["amount_cents", "role", "now", "success", "rule_original"])
def test_case_cannot_supply_private_locator_money_actor_or_outcome(field: str) -> None:
    inputs: dict[str, Any] = {
        "rule_id": "registered-P",
        "body": request().model_dump(mode="json"),
        field: 1,
    }
    with pytest.raises(ValueError):
        module.parse_inputs("FULL_MECHANISM_PREPARE", inputs)
    inputs.pop(field)
    inputs["body"][field] = 1
    with pytest.raises(ValueError):
        module.parse_inputs("FULL_MECHANISM_PREPARE", inputs)


def test_prepare_calls_exact_private_request_and_records_service_bytes_without_fake_http(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def prepare(*args: Any) -> ActionResponse:
        calls.append(args)
        return action()

    monkeypatch.setattr(module, "prepare_full_experiment_asset_execution", prepare)
    capture = consumer.dispatch(
        "FULL_MECHANISM_PREPARE",
        {"rule_id": "registered-P", "body": request().model_dump(mode="json")},
        NOW,
    )
    assert calls == [
        (
            consumer.native.engine,
            DEMO_USER_ID,
            FullExperimentAssetRequest(original_request=request(), rule_original=locator()),
            NOW,
        )
    ]
    assert capture["signed_user_session"]["authentication_source"] == "LOCAL_SIGNED_SESSION"
    assert capture["signed_user_session"]["human_identity_verified"] is False
    row: dict[str, Any] = {}
    module.apply_service_capture(capture, row)
    assert row["result"] == action().model_dump(mode="json")
    assert "status_code" not in row and "native_response" not in row
    assert capture["financial_effect_verified"] is False
    assert COOKIE_NAME not in json.dumps(capture) and SECRET not in json.dumps(capture)


@pytest.mark.parametrize("change", ["unregistered", "missing-cookie", "expired", "wrong-epoch"])
def test_no_private_prepare_before_exact_server_rule_current_user_and_epoch(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        module, "prepare_full_experiment_asset_execution", lambda *args: calls.append(args)
    )
    body = request().model_dump(mode="json")
    rule, now = "registered-P", NOW
    if change == "unregistered":
        rule = "caller-path"
    elif change == "missing-cookie":
        assert consumer.native._client is not None
        consumer.native._client.cookies.clear()
    elif change == "expired":
        now += timedelta(minutes=15)
    else:
        body["expected_epoch_id"] = str(UUID(int=999))
    if change == "wrong-epoch":
        with pytest.raises(ValueError, match="OPEN epoch"):
            consumer.dispatch("FULL_MECHANISM_PREPARE", {"rule_id": rule, "body": body}, now)
    else:
        capture = consumer.dispatch("FULL_MECHANISM_PREPARE", {"rule_id": rule, "body": body}, now)
        assert capture["outcome"] == "RAISED" and "result" not in capture
    assert calls == []


def test_b4_missing_producer_refusal_is_retained_without_any_fallback(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any) -> ActionResponse:
        raise PolicyLifecycleError("FULL_EXPERIMENT_ASSET_REJECTED", "B4_MODEL_NOT_CALLED", 409)

    monkeypatch.setattr(module, "prepare_full_experiment_asset_execution", missing)
    capture = consumer.dispatch(
        "FULL_MECHANISM_PREPARE",
        {"rule_id": "registered-P", "body": request().model_dump(mode="json")},
        NOW,
    )
    row: dict[str, Any] = {}
    module.apply_service_capture(capture, row)
    assert row["error"]["message"] == "B4_MODEL_NOT_CALLED" and "result" not in row


def test_unknown_lookup_has_no_cookie_or_rule_dependency_and_never_executes(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert consumer.native._client is not None
    consumer.native._client.cookies.clear()
    consumer = module.FullNativeMechanismSteps(consumer.native, {})
    calls: list[tuple[str, Any]] = []

    def lookup(key: str, now: Any) -> FullExperimentAssetLookup:
        calls.append((key, now))
        return original("UNKNOWN")

    monkeypatch.setattr(consumer, "_lookup", lookup)
    capture = consumer.dispatch("FULL_MECHANISM_LOOKUP", {"idempotency_key": KEY}, NOW)
    assert calls == [(KEY, NOW)] and capture["result"]["action"]["status"] == "UNKNOWN"
    assert capture["result"]["receipt_is_current_authority"] is False
    assert "signed_user_session" not in capture and "private_request" not in capture


@pytest.mark.parametrize("change", ["action", "owner", "epoch", "auto", "not-found"])
def test_confirmation_or_execution_cannot_replace_exact_original_identity(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    saved = original()
    assert saved.action is not None and saved.original_request is not None
    if change in {"action", "owner", "auto"}:
        saved = saved.model_copy(
            update={
                "action": saved.action.model_copy(
                    update={
                        "action_id"
                        if change == "action"
                        else "user_id"
                        if change == "owner"
                        else "autonomy_level": "AUTO" if change == "auto" else UUID(int=999)
                    }
                )
            }
        )
    elif change == "epoch":
        request_original = saved.original_request
        assert request_original is not None
        saved = saved.model_copy(
            update={
                "original_request": request_original.model_copy(
                    update={
                        "original_request": request().model_copy(
                            update={"expected_epoch_id": UUID(int=999)}
                        )
                    }
                )
            }
        )
    else:
        saved = FullExperimentAssetLookup(
            user_id=DEMO_USER_ID, idempotency_key=KEY, status="NOT_FOUND_NOT_FINAL"
        )
    monkeypatch.setattr(consumer, "_lookup", lambda *args: saved)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(module, "execute_action", lambda *args: calls.append(args))
    capture = consumer.dispatch(
        "FULL_MECHANISM_EXECUTE",
        {"idempotency_key": KEY, "action_id": str(action().action_id), "body": {}},
        NOW,
    )
    assert capture["error"]["code"] == "FULL_MECHANISM_ORIGINAL_IDENTITY_REQUIRED" and calls == []


def test_confirmation_delegates_original_reviewed_hash_and_execute_fault_keeps_same_key(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consumer, "_lookup", lambda *args: original())
    calls: list[tuple[Any, ...]] = []

    def confirm(*args: Any) -> ActionResponse:
        calls.append(args)
        return action("AUTHORIZED")

    monkeypatch.setattr(module, "confirm_action", confirm)
    body = {
        "idempotency_key": KEY,
        "action_id": str(action().action_id),
        "body": {"accepted": True, "effect_hash": action().effect_hash},
    }
    capture = consumer.dispatch("FULL_MECHANISM_CONFIRM", body, NOW)
    assert capture["outcome"] == "RETURNED" and calls[0][3].effect_hash == action().effect_hash
    observed: list[tuple[Any, ...]] = []

    def fault(*args: Any) -> Any:
        observed.append(args)
        return nullcontext()

    monkeypatch.setattr(module, "_fault", fault)

    def lost(*args: Any) -> ActionResponse:
        raise TimeoutError("SIMULATED_BANK_RESPONSE_LOST")  # TOOL_ONLY: no actual bank commit.

    monkeypatch.setattr(module, "execute_action", lost)
    same = {"idempotency_key": KEY, "action_id": str(action().action_id), "body": {}}
    failed = consumer.dispatch("FULL_MECHANISM_EXECUTE", same, NOW, fault="DROP_BANK_RESPONSE")
    assert observed == [
        (
            "DROP_BANK_RESPONSE",
            "EXECUTE_ACTION",
            consumer.native.engine,
            DEMO_USER_ID,
            {"action_id": str(action().action_id)},
        )
    ]
    assert failed["error"]["code"] == "SIMULATED_BANK_RESPONSE_LOST"
    assert failed["request"] == same and "result" not in failed
    assert failed["financial_effect_verified"] is False


@pytest.mark.parametrize(
    "kind", ["FULL_MECHANISM_PREPARE", "FULL_MECHANISM_LOOKUP", "FULL_MECHANISM_CONFIRM"]
)
def test_faults_are_only_the_original_execute_pair(kind: str) -> None:
    assert module.supported_fault(kind, "NONE")
    assert not module.supported_fault(kind, "DROP_BANK_RESPONSE")
    assert not module.supported_fault(kind, "FAIL_APPLICATION_PROJECTION")


def test_rehashed_capture_substitution_cannot_supply_result(
    consumer: module.FullNativeMechanismSteps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(consumer, "_lookup", lambda *args: original())
    capture = consumer.dispatch("FULL_MECHANISM_LOOKUP", {"idempotency_key": KEY}, NOW)
    altered = copy.deepcopy(capture)
    altered["result"]["action"]["status"] = "SUCCEEDED"
    with pytest.raises(ValueError, match="bytes/result/hash"):
        module.apply_service_capture(altered, {})


@pytest.mark.parametrize("accepted", [False, 1, "true"])
def test_ask_confirmation_requires_explicit_strict_true(accepted: Any) -> None:
    with pytest.raises(ValueError):
        module.parse_inputs(
            "FULL_MECHANISM_CONFIRM",
            {
                "idempotency_key": KEY,
                "action_id": str(action().action_id),
                "body": {"accepted": accepted, "effect_hash": action().effect_hash},
            },
        )


def test_mixed_entry_registers_private_consumer_and_resolves_actual_prior_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import full_native_cases as runner
    from app.tests.test_full_native_cases import ToolOnlyNative, case, raw, step

    calls: list[tuple[str, dict[str, Any], str]] = []
    registered: list[dict[str, RegisteredFullMechanismRule]] = []

    class ToolOnlyPrivateConsumer:
        def __init__(self, native: Any, rules: Any):
            registered.append(dict(rules))

        def dispatch(
            self, kind: str, inputs: dict[str, Any], now: Any, *, fault: str
        ) -> dict[str, Any]:
            calls.append((kind, inputs, fault))
            value = {"action_id": str(action().action_id), "effect_hash": action().effect_hash}
            text = json.dumps(value)
            from hashlib import sha256

            return {
                "protocol": module.CAPTURE_PROTOCOL,
                "channel": "PRIVATE_PRODUCTION_SERVICE_CALL",
                "outcome": "RETURNED",
                "result": value,
                "original_return_text": text,
                "return_bytes_sha256": sha256(text.encode()).hexdigest(),
            }

    monkeypatch.setattr(runner, "FullNativeSteps", ToolOnlyNative)
    monkeypatch.setattr(runner, "FullNativeMechanismSteps", ToolOnlyPrivateConsumer)
    monkeypatch.setattr(runner, "require_native_target", lambda *args: NOW)
    engine = create_engine("sqlite://")  # TOOL_ONLY: original target gate explicitly doubled.
    try:
        native = runner.FullNativeCaseRunner(
            engine, DEMO_USER_ID, mechanism_rules={"registered-P": locator()}
        )
        body = case(
            [
                step(
                    "prepare",
                    "FULL_MECHANISM_PREPARE",
                    {"rule_id": "registered-P", "body": request().model_dump(mode="json")},
                ),
                step(
                    "confirm",
                    "FULL_MECHANISM_CONFIRM",
                    {
                        "idempotency_key": KEY,
                        "action_id": {
                            "$ref": {"step_id": "prepare", "pointer": "/result/action_id"}
                        },
                        "body": {
                            "accepted": True,
                            "effect_hash": {
                                "$ref": {"step_id": "prepare", "pointer": "/result/effect_hash"}
                            },
                        },
                    },
                ),
            ]
        )
        value = native.run(raw(body), EPOCH)
        assert registered == [{"registered-P": locator()}]
        assert [row[0] for row in calls] == ["FULL_MECHANISM_PREPARE", "FULL_MECHANISM_CONFIRM"]
        assert calls[1][1]["action_id"] == str(action().action_id)
        assert value["steps"][1]["input_references"][0]["step_id"] == "prepare"
        assert "private_service_response" in value["steps"][0]
        assert "native_response" not in value["steps"][0]
        assert value["financial_acceptance"] is False and value["metric_results"] is None
        assert value["seven_arm_experiment_performed"] is False
        assert value["original_step_count"] == value["returned_step_count"] == 2
    finally:
        engine.dispose()
