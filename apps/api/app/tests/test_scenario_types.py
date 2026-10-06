"""Protocol/security checks; these do not claim bank integration acceptance."""

from contextlib import nullcontext
from contextvars import Context
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.services import execution, scenario_runner
from app.services.demo_console_types import DemoCommandView, DemoEventRequest
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.scenario_runner import ScenarioRunner, _fault, _periods
from app.services.scenario_types import (
    InitialState,
    Scenario,
    ScenarioResult,
    ScenarioRPC,
    ScenarioStep,
)
from pydantic import ValidationError
from sqlalchemy import create_engine

NOW = datetime(2026, 10, 4, 1, tzinfo=UTC)


def scenario(**changes: Any) -> Scenario:
    values: dict[str, Any] = {
        "scenario_id": "pure-protocol-test",
        "purpose": "DEVELOPMENT",
        "dataset_id": "development-only",
        "family_id": "protocol-negative",
        "initial_state": InitialState(expected_epoch_id=uuid4()),
        "steps": [],
    }
    return Scenario(**(values | changes))


@pytest.mark.parametrize(
    "purpose,digest",
    [("DEVELOPMENT", "a" * 64), ("MVP_FROZEN", None), ("FULL_FAMILY_FROZEN", None)],
)
def test_unfrozen_data_cannot_claim_frozen_purpose(purpose: str, digest: str | None) -> None:
    with pytest.raises(ValidationError):
        scenario(purpose=purpose, frozen_case_sha256=digest)


def test_clocks_step_identity_and_extra_expected_outcomes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ScenarioStep(step_id="naive", kind="EXECUTE_ACTION", at=NOW.replace(tzinfo=None), inputs={})
    step = ScenarioStep(step_id="same", kind="EXECUTE_ACTION", at=NOW, inputs={})
    with pytest.raises(ValidationError):
        scenario(steps=[step, step])
    with pytest.raises(ValidationError):
        scenario(
            steps=[
                step,
                step.model_copy(update={"step_id": "later", "at": NOW - timedelta(seconds=1)}),
            ]
        )
    with pytest.raises(ValidationError):
        scenario(expected_balance_cents=123)


@pytest.mark.parametrize(
    "database,host,port",
    [
        ("bounded_funds", "127.0.0.1", 54329),
        ("bf_test_" + "a" * 32, "remote.invalid", 54329),
        ("bf_test_" + "a" * 32, "127.0.0.1", 5432),
    ],
)
def test_arbitrary_scenarios_refuse_formal_or_remote_engines(
    database: str, host: str, port: int
) -> None:
    engine = create_engine(f"postgresql+psycopg://unused@{host}:{port}/{database}")
    try:
        with pytest.raises((PolicyLifecycleError, ValueError)):
            ScenarioRunner(engine, uuid4()).run(scenario())
    finally:
        engine.dispose()


def test_digest_shape_alone_does_not_enable_frozen_execution() -> None:
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32)
    try:
        with pytest.raises(PolicyLifecycleError, match="manifest verification"):
            ScenarioRunner(engine, uuid4()).run(
                scenario(purpose="MVP_FROZEN", frozen_case_sha256="a" * 64)
            )
    finally:
        engine.dispose()


def test_fault_does_not_leak_to_a_concurrent_invocation_or_after_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32)
    user_id = uuid4()
    calls: list[tuple[Any, ...]] = []

    def original(*args: Any, **kwargs: Any) -> str:
        calls.append(args)
        return "original-return"

    monkeypatch.setattr(execution, "process_operation", original)
    try:
        with _fault("DROP_BANK_RESPONSE", "EXECUTE_ACTION", engine, user_id):
            active = execution.__dict__["process_operation"]
            assert Context().run(active, engine, user_id) == "original-return"
            with pytest.raises(TimeoutError, match="RESPONSE_LOST"):
                active(engine, user_id)
        assert execution.__dict__["process_operation"] is original
        assert original(engine, user_id) == "original-return"
        assert len(calls) == 3
    finally:
        engine.dispose()


def test_empty_properties_do_not_open_a_post_command_epoch_transaction() -> None:
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32)
    try:
        assert ScenarioRunner(engine, uuid4()).check_properties([], uuid4(), NOW) == []
    finally:
        engine.dispose()


def test_recurring_periods_follow_original_user_timezone_at_month_boundary() -> None:
    boundary = datetime(2026, 10, 31, 16, 30, tzinfo=UTC)
    assert _periods(boundary, "Asia/Shanghai")[0] == "2026-11"
    assert _periods(boundary, "UTC")[0] == "2026-10"


def test_rpc_rejects_wrong_resource_and_browser_financial_overrides() -> None:
    base = {"scenario_id": "rpc-negative", "purpose": "DEVELOPMENT", "expected_epoch_id": uuid4()}
    with pytest.raises(ValidationError):
        ScenarioRPC.model_validate(base | {"operation": "verify_legacy_recovery"})
    with pytest.raises(ValidationError):
        ScenarioRPC.model_validate(base | {"operation": "snapshot", "action_id": uuid4()})
    with pytest.raises(ValidationError):
        ScenarioRPC.model_validate(
            base
            | {"operation": "prepare_rent_old_action", "policy_id": uuid4(), "amount_cents": 99}
        )


def test_demo_json_roundtrip_preserves_strict_bank_fact_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.external_bank_fact_types import ExternalFactResult

    user_id, epoch_id, command_id = uuid4(), uuid4(), uuid4()
    body = DemoEventRequest(event_kind="SALARY_RECEIVED", expected_epoch_id=epoch_id)
    actual = DemoCommandView(
        command_id=command_id,
        epoch_id=epoch_id,
        event_kind="SALARY_RECEIVED",
        admitted_at=NOW,
        status="PENDING",
        message="Serialization regression only",
        fact=ExternalFactResult(
            external_fact_id=uuid4(),
            bank_status="SETTLED",
            projection_status="PROJECTED",
            economic_posting_ids=(uuid4(), uuid4()),
            transaction_id=uuid4(),
        ),
    )
    monkeypatch.setattr(scenario_runner, "audit_command_guard", lambda *args: nullcontext())

    def stored_result(self: ScenarioRunner, inputs: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_id=inputs.scenario_id,
            purpose="DEVELOPMENT",
            dataset_id=inputs.dataset_id,
            family_id=inputs.family_id,
            input_sha256="a" * 64,
            status="EXECUTED",
            steps=[{"result": actual.model_dump(mode="json")}],
            properties=[],
        )

    monkeypatch.setattr(ScenarioRunner, "_execute", stored_result)
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + "a" * 32)
    try:
        assert ScenarioRunner(engine, user_id).demo_event(body, NOW) == actual
    finally:
        engine.dispose()
