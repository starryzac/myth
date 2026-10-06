"""Native request and isolation contract only: no actual PG, bank, or experiment results."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from app.domain.demo_identity import DEMO_USER_ID
from app.domain.full_native_operations import native_request
from app.services.full_native_steps import FullNativeSteps, require_native_target
from sqlalchemy import create_engine

NOW = datetime(2026, 10, 6, tzinfo=UTC)
DATABASE = "bf_test_" + "a" * 32


@pytest.mark.parametrize(
    "inputs",
    [
        {"role": "USER"},
        {"principal": {"role": "USER"}},
        {"secret": "caller"},
        {"body": {"username": "bounded-user", "secret": "caller"}},
        {"headers": {"Authorization": "Bearer caller"}},
        {"url": "https://example.com"},
    ],
)
def test_login_accepts_no_caller_credential_actor_or_url(inputs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="INPUT_KEYS_NOT_EXACT"):
        native_request("LOCAL_USER_LOGIN", inputs)
    plan = native_request("LOCAL_USER_LOGIN", {})
    assert plan.server_login and plan.body is None


@pytest.mark.parametrize(
    "identity",
    [
        "../reset",
        "00000000-0000-0000-0000-000000000001?amount=1",
        "00000000-0000-0000-0000-00000000000A",
        1,
        True,
    ],
)
def test_no_path_query_or_noncanonical_identity(identity: Any) -> None:
    with pytest.raises(ValueError):
        native_request("FULL_POLICY_READ", {"policy_id": identity})


@pytest.mark.parametrize("change", ["fault", "arbitrary_operation", "extra_body", "missing_body"])
def test_unsupported_fault_and_arbitrary_rpc_are_explicit(change: str) -> None:
    inputs: dict[str, Any] = {"body": {"template_name": "DatedExpensePolicy"}}
    kind, fault = "FULL_POLICY_VALIDATE", "NONE"
    if change == "fault":
        fault = "DROP_BANK_RESPONSE"
    elif change == "arbitrary_operation":
        kind = "RESET_DATABASE"
    elif change == "extra_body":
        inputs["principal"] = {"role": "USER"}
    else:
        inputs = {}
    with pytest.raises(ValueError):
        native_request(kind, inputs, fault=fault)


@pytest.mark.parametrize(
    "url,user,now",
    [
        ("postgresql+psycopg://unused@127.0.0.1:54329/bounded_funds", DEMO_USER_ID, NOW),
        (f"postgresql+psycopg://unused@127.0.0.1:5432/{DATABASE}", DEMO_USER_ID, NOW),
        (f"postgresql+psycopg://unused@localhost:54329/{DATABASE}", DEMO_USER_ID, NOW),
        (f"postgresql+psycopg://unused@127.0.0.1:54329/{DATABASE}", UUID(int=42), NOW),
        (
            f"postgresql+psycopg://unused@127.0.0.1:54329/{DATABASE}",
            DEMO_USER_ID,
            NOW.replace(tzinfo=None),
        ),
        ("sqlite://", DEMO_USER_ID, NOW),
    ],
)
def test_target_guard_rejects_before_any_database_connection(
    url: str, user: UUID, now: datetime
) -> None:
    engine = create_engine(url)
    try:
        with pytest.raises(ValueError):
            require_native_target(engine, user, now)
    finally:
        engine.dispose()


def test_unopened_invocation_cannot_execute_or_reuse_a_cookie() -> None:
    engine = create_engine(f"postgresql+psycopg://unused@127.0.0.1:54329/{DATABASE}")
    try:
        value = FullNativeSteps(engine, DEMO_USER_ID, UUID(int=1), NOW)
        with pytest.raises(ValueError, match="INVOCATION_NOT_OPEN"):
            value.dispatch("LOCAL_USER_LOGIN", {}, NOW)
    finally:
        engine.dispose()


def test_request_forwards_only_original_body_without_financial_result_changes() -> None:
    original = {"expected_epoch_id": str(UUID(int=2)), "idempotency_key": "original-key"}
    plan = native_request("PAYMENT_EXECUTE", {"action_id": str(UUID(int=1)), "body": original})
    assert plan.path == f"/api/v1/full-payment-relations/actions/{UUID(int=1)}/execute"
    assert plan.body == original and plan.method == "POST" and not plan.server_login
