"""Synthetic complete-source risks only; no bank, PG or notification evidence."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.domain.full_action_set_boundary_composed import (
    ComposedActionSetInput,
    derive_composed_action_set,
)
from app.domain.full_action_set_boundary_recovery_composed import (
    ALGORITHM,
    RecoveryComposedActionSetInput,
    derive_recovery_composed_action_set,
)
from app.domain.full_action_set_payment_producers import PeriodicActionSetInput
from app.domain.full_action_set_recovery_producers import RecoveryActionSetInput
from app.domain.policy_configuration import configuration_hash
from app.services import full_action_set_boundary_recovery_composed as service
from app.services.decision_recording import DecisionCapture
from app.tests.test_full_action_set_payment_producers import fixture as periodic_fixture
from app.tests.test_full_action_set_recovery_producers import fixture as recovery_fixture


def fixture(*, delay: int = 0, cash: int = 70000) -> RecoveryComposedActionSetInput:
    recovery = recovery_fixture(delay=delay, cash=cash)
    periodic = PeriodicActionSetInput(
        original_actual_input=recovery.original_actual_input,
        expected_full_policy_ids=[],
        relation_source_count=0,
        relation_source_ids=[],
        producers=[],
    )
    return RecoveryComposedActionSetInput(
        composed=ComposedActionSetInput(periodic=periodic), recovery=recovery
    )


def test_exact_t0_recovery_family_is_added_without_hiding_original_unknown_or_old_v3() -> None:
    data = fixture()
    before = data.model_dump_json()
    original = derive_composed_action_set(data.composed)
    result = derive_recovery_composed_action_set(data)
    assert result.algorithm_version == ALGORITHM
    assert result.original_composed_snapshot == original
    assert data.model_dump_json() == before
    assert result.recovery_family.recovery_family_complete, result.recovery_family.reasons
    item = next(row for row in result.candidates if row.candidate_key.startswith("full-recovery:"))
    assert item.state == "INCLUDED" and item.amount_cents == 50000
    assert item.autonomy_level == "ASK_ONCE"
    assert set(original.expected_candidate_keys).issubset(result.expected_candidate_keys)
    assert all(row in result.candidates for row in original.candidates)
    assert not any("RecoveryPolicy:" in code for code in result.unsupported_producers)
    # The original unproved purchase remains; a completed family cannot certify everything.
    assert result.status == "UNKNOWN" and result.action_set_signature is None
    assert not result.global_action_set_complete
    assert not result.bank_authority and not result.financial_write and not result.grants_authority
    assert result.notification_support == "NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"
    assert result.snapshot_hash == configuration_hash(
        result.model_dump(mode="json", exclude={"snapshot_hash"})
    )


def test_complete_periodic_and_empty_recovery_keep_whole_denominator() -> None:
    periodic = periodic_fixture()
    actual = periodic.original_actual_input
    recovery = RecoveryActionSetInput(
        original_actual_input=actual,
        expected_full_policy_ids=[],
        original_position_ids=[],
        original_action_ids=[],
        original_commands=[],
        producers=[],
    )
    data = RecoveryComposedActionSetInput(
        composed=ComposedActionSetInput(periodic=periodic), recovery=recovery
    )
    original = derive_composed_action_set(data.composed)
    result = derive_recovery_composed_action_set(data)
    assert original.global_action_set_complete, original.reasons
    assert result.global_action_set_complete, result.reasons
    assert result.status == "COMPLETE"
    assert result.candidates == original.candidates
    assert result.expected_candidate_keys == original.expected_candidate_keys
    assert result.replaced_original_candidate_keys == original.replaced_original_candidate_keys
    assert result.action_set_signature is not None
    assert result.original_composed_snapshot.snapshot_hash == original.snapshot_hash


@pytest.mark.parametrize(
    "change",
    [
        "different-clock",
        "different-capture",
        "missing-family",
        "duplicate-policy",
        "incomplete-count",
        "delay-t1",
    ],
)
def test_unproved_binding_never_removes_original_views_or_adapter_missing(change: str) -> None:
    data = fixture(delay=1 if change == "delay-t1" else 0)
    recovery = data.recovery
    if change in {"different-clock", "different-capture", "incomplete-count"}:
        actual = recovery.original_actual_input
        if change == "different-clock":
            actual = actual.model_copy(
                update={
                    "base": actual.base.model_copy(
                        update={"as_of": actual.base.as_of + timedelta(seconds=1)}
                    )
                }
            )
        elif change == "different-capture":
            actual = actual.model_copy(
                update={
                    "base": actual.base.model_copy(
                        update={"source_reasons": ["CURRENT_SOURCE_NOT_VERIFIED"]}
                    )
                }
            )
        else:
            rows = list(actual.table_coverage)
            rows[0] = rows[0].model_copy(update={"actual_count": rows[0].captured_count + 1})
            actual = actual.model_copy(update={"table_coverage": rows})
        recovery = recovery.model_copy(update={"original_actual_input": actual})
    elif change == "missing-family":
        recovery = recovery.model_copy(update={"producers": []})
    elif change == "duplicate-policy":
        recovery = recovery.model_copy(
            update={"producers": [*recovery.producers, recovery.producers[0]]}
        )
    data = data.model_copy(update={"recovery": recovery})
    original = derive_composed_action_set(data.composed)
    result = derive_recovery_composed_action_set(data)
    assert result.status == "UNKNOWN" and not result.global_action_set_complete
    assert result.action_set_signature is None
    assert result.replaced_original_candidate_keys == original.replaced_original_candidate_keys
    assert all(row in result.candidates for row in original.candidates)
    assert set(original.unsupported_producers).issubset(result.unsupported_producers)
    assert "RECOVERY_COMPOSED_COMPLETE_UNIQUE_MAPPING_NOT_PROVEN" in result.reasons


def test_closed_input_rejects_supplied_results_authority_and_manual_money() -> None:
    raw = fixture().model_dump(mode="json")
    for name in ("computed_family", "handled_unsupported_codes", "bank_authority", "manual_intent"):
        with pytest.raises(ValueError):
            RecoveryComposedActionSetInput.model_validate({**raw, name: {"amount_cents": 50000}})


def test_request_capture_reuses_one_original_instance_for_both_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = fixture()
    session = MagicMock()
    original = SimpleNamespace(
        inputs=data.recovery.original_actual_input, originals=DecisionCapture()
    )
    calls: list[str] = []

    def capture_actual(*args: Any) -> Any:
        calls.append("actual")
        return original

    def capture_periodic(*args: Any, original_actual_capture: Any) -> Any:
        assert original_actual_capture is original
        calls.append("periodic")
        return SimpleNamespace(inputs=data.composed.periodic, originals=DecisionCapture())

    def capture_recovery(*args: Any, original_actual_capture: Any) -> Any:
        assert original_actual_capture is original
        calls.append("recovery")
        return SimpleNamespace(inputs=data.recovery, originals=DecisionCapture())

    monkeypatch.setattr(service, "capture_actual_action_set", capture_actual)
    monkeypatch.setattr(service, "capture_current_periodic_payment_producers", capture_periodic)
    monkeypatch.setattr(service, "capture_current_recovery_producers", capture_recovery)
    base = original.inputs.base
    result = service.capture_recovery_composed_action_set(session, base.user_id, base.as_of)
    assert calls == ["actual", "periodic", "recovery"]
    assert result.inputs == data
    assert result.result == derive_recovery_composed_action_set(data)
    session.commit.assert_not_called()
    session.flush.assert_not_called()
