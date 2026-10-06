"""TOOL_TEST_ONLY refusal capture gates; no real engine connection or financial evaluation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.services import execution
from app.services import experiment_arms as provider
from app.services.policy_lifecycle import PolicyLifecycleError
from sqlalchemy import create_engine

TABLES = (
    "bank_operations",
    "simulated_bank_redemptions",
    "action_receipts",
    "simulated_bank_postings",
)


def rows() -> dict[str, Any]:
    return {name: [] for name in TABLES} | {"users": [{"id": "TOOL_TEST_ONLY"}]}


@pytest.mark.parametrize("error", [None, {"original_ref": "TOOL_TEST_ONLY"}])
def test_only_real_captured_error_can_enter_no_bank_gate(error: Any) -> None:
    assert provider._original_no_bank_refusal(rows(), rows(), "action", error) == (
        error is not None
    )


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("delta", ["MISSING", "CHANGED", "MALFORMED", "ACTION_ROW"])
def test_any_economic_inventory_gap_delta_or_prior_action_row_prevents_refusal(
    table: str, delta: str
) -> None:
    before, after = rows(), rows()
    if delta == "MISSING":
        del before[table]
    elif delta == "CHANGED":
        after[table] = [{"id": "other", "amount_cents": 1}]
    elif delta == "MALFORMED":
        before[table] = after[table] = ["not-an-original-row"]
    else:
        before[table] = after[table] = [{"action_plan_id": "action"}]
    assert not provider._original_no_bank_refusal(
        before, after, "action", {"original_ref": "error"}
    )


@pytest.mark.parametrize("reference", ["operation_id", "redemption_id"])
def test_existing_economic_leg_for_original_action_is_never_zero_effect(reference: str) -> None:
    original = rows()
    original["simulated_bank_postings"] = [{reference: "action", "id": "old"}]
    assert not provider._original_no_bank_refusal(original, deepcopy(original), "action", "error")


def test_original_other_actions_are_retained_and_no_economic_claim_is_made() -> None:
    original = rows()
    for table in TABLES:
        original[table] = [{"id": "old-other", "action_plan_id": "other-action", "nullable": None}]
    preserved = deepcopy(original)
    assert provider._original_no_bank_refusal(
        original, preserved, "action", {"original_ref": "error"}
    )
    assert original == preserved


class Writer:
    def __init__(self) -> None:
        self.service_call_id: str | None = None
        self.records: list[tuple[str, dict[str, Any]]] = []

    def write(self, kind: str, payload: dict[str, Any], pointer: str) -> dict[str, Any]:
        self.records.append((kind, deepcopy(payload)))
        return {"tool_test_only_ref": len(self.records), "kind": kind}

    def stage(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No-bank refusal cannot manufacture pipeline stages")

    def capture(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"kind": kind, "payload": deepcopy(payload)}


@pytest.mark.parametrize(
    "cause", ["POLICY_REFUSAL", "UNEXPECTED_ERROR", "NO_ERROR", "CHANGED_LEDGER"]
)
def test_actual_provider_error_control_flow_keeps_raw_error_and_refuses_fake_bank(
    cause: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Python-only control flow: service read/execute doubles are not product evidence.
    engine = create_engine("postgresql+psycopg://unused@127.0.0.1:54329/bf_test_" + uuid4().hex)
    owner, identity, epoch = uuid4(), uuid4(), uuid4()
    context = SimpleNamespace(
        bindings={"user_id": str(owner), "isolated_db_epoch": str(epoch)},
        now="2026-10-05T11:00:00Z",
    )
    writer = Writer()
    before, after = rows(), rows()
    if cause == "CHANGED_LEDGER":
        after["simulated_bank_postings"] = [{"id": "real-new-leg-in-fixture", "amount_cents": 1}]
    snapshots = iter([before, after])
    actions = iter(
        [
            SimpleNamespace(effect_hash="same-original", status="PREPARED"),
            SimpleNamespace(effect_hash="same-original", status="INVALIDATED"),
        ]
    )

    @contextmanager
    def invocation(value: Any) -> Iterator[Any]:
        assert value is context
        yield engine, {}, writer

    def execute(*args: Any, **kwargs: Any) -> None:
        assert len(args) == 4 and args[:3] == (engine, owner, identity) and not kwargs
        if cause in {"POLICY_REFUSAL", "CHANGED_LEDGER"}:
            raise PolicyLifecycleError("ORIGINAL_EXPIRED", "original refusal", 409)
        if cause == "UNEXPECTED_ERROR":
            raise RuntimeError("original unexpected error")

    def no_connect(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("TOOL_ONLY test cannot connect or execute SQL")

    monkeypatch.setattr(engine, "connect", no_connect)
    monkeypatch.setattr(provider, "_invocation", invocation)
    monkeypatch.setattr(provider, "_sql_context", lambda *args: {})
    monkeypatch.setattr(provider, "_rows", lambda *args: next(snapshots))
    monkeypatch.setattr(execution, "get_action", lambda *args: next(actions))
    monkeypatch.setattr(execution, "execute_action", execute)
    try:
        if cause in {"NO_ERROR", "CHANGED_LEDGER"}:
            with pytest.raises(provider.ProviderNotImplemented, match="no-bank/ambiguous-bank"):
                provider.execute_arm_action(context, str(identity))
            return
        result = provider.execute_arm_action(context, str(identity))
        payload = result["payload"]
        assert result["kind"] == "ARM_EXECUTION"
        assert payload["capability_status"] == "ACTUAL_ORIGINAL_SERVICE_REJECTED_BEFORE_BANK"
        assert (
            payload["action_status"] == "INVALIDATED" and payload["effect_hash"] == "same-original"
        )
        assert payload["pipeline"] == {} and payload["bank_operation_ref"] is None
        assert payload["economic_execution_verified"] is False
        assert payload["live_phase_evidence_status"] == "CAPTURED_NOT_INDEPENDENTLY_VERIFIED"
        assert (
            payload["error_ref"]
            and payload["before_snapshot_ref"]
            and payload["after_snapshot_ref"]
        )
        error = next(
            value["error"] for kind, value in writer.records if kind == "ACTUAL_EXECUTION_ERROR"
        )
        assert error["status_code"] == (409 if cause == "POLICY_REFUSAL" else None)
        assert error["code"] == ("ORIGINAL_EXPIRED" if cause == "POLICY_REFUSAL" else None)
        assert error["message"] == (
            "original refusal" if cause == "POLICY_REFUSAL" else "original unexpected error"
        )
        assert not any(
            kind in {"ACTUAL_BANK_OPERATION", "PIPELINE_STAGE", "ACTUAL_RECEIPT"}
            for kind, _ in writer.records
        )
    finally:
        engine.dispose()


def test_default_four_position_execution_core_signature_is_unchanged() -> None:
    import inspect

    assert list(inspect.signature(execution.execute_action).parameters) == [
        "engine",
        "user_id",
        "action_id",
        "now",
    ]
