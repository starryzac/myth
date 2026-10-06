"""Synthetic composition risks; original family math is independently recomputed."""

from datetime import timedelta

import pytest
from app.domain.full_action_set_boundary_composed import ComposedActionSetInput
from app.domain.full_action_set_boundary_recovery_composed import (
    RecoveryComposedActionSetInput,
    derive_recovery_composed_action_set,
)
from app.domain.full_action_set_boundary_registered import (
    RegisteredActionSetInput,
    derive_registered_action_set,
)
from app.domain.full_action_set_joint_producers import JointActionSetInput
from app.domain.full_action_set_recovery_producers import RecoveryActionSetInput
from app.domain.full_action_set_release_producers import ReleaseActionSetInput
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_action_set_payment_producers import fixture as periodic_fixture


def fixture(*, auto: bool = True) -> RegisteredActionSetInput:
    periodic = periodic_fixture(auto=auto)
    actual = periodic.original_actual_input
    return RegisteredActionSetInput(
        recovery_composed=RecoveryComposedActionSetInput(
            composed=ComposedActionSetInput(periodic=periodic),
            recovery=RecoveryActionSetInput(
                original_actual_input=actual,
                expected_full_policy_ids=[],
                original_position_ids=[],
                original_action_ids=[],
                original_commands=[],
                producers=[],
            ),
        ),
        release=ReleaseActionSetInput(
            original_actual_input=actual,
            expected_full_policy_ids=[],
            original_authorization_source_ids=[],
            original_action_ids=[],
            authorizations=[],
            original_commands=[],
            producers=[],
        ),
        joint=JointActionSetInput(
            original_actual_input=actual,
            expected_goal_ids=[],
            original_action_ids=[],
            original_commands=[],
        ),
    )


def test_complete_empty_release_and_joint_preserve_exact_old_snapshot_and_payment() -> None:
    data = fixture()
    original = derive_recovery_composed_action_set(data.recovery_composed)
    result = derive_registered_action_set(data)
    assert result.global_action_set_complete, result.reasons
    assert result.original_recovery_composed_snapshot == original
    assert result.candidates == original.candidates
    assert result.expected_candidate_keys == original.expected_candidate_keys
    assert (
        result.release_family.release_family_complete and result.joint_family.joint_family_complete
    )
    assert result.original_inventory_hash == original.original_inventory_hash
    assert result.financial_input_hash == original.financial_input_hash
    assert result.notification_support == "NOT_IMPLEMENTED_FOR_REGISTERED_V5"
    assert not result.bank_authority and not result.grants_authority and not result.financial_write
    assert result.snapshot_hash == configuration_hash(
        result.model_dump(mode="json", exclude={"snapshot_hash"})
    )


@pytest.mark.parametrize("family", ["release", "joint"])
def test_independently_captured_family_cannot_remove_any_original_candidate(family: str) -> None:
    data = fixture()
    current = getattr(data, family)
    other = current.original_actual_input.model_copy(
        update={
            "base": current.original_actual_input.base.model_copy(
                update={
                    "as_of": current.original_actual_input.base.as_of + timedelta(seconds=1),
                }
            ),
        }
    )
    invalid = data.model_copy(
        update={family: current.model_copy(update={"original_actual_input": other})}
    )
    result = derive_registered_action_set(invalid)
    original = derive_recovery_composed_action_set(data.recovery_composed)
    assert not result.global_action_set_complete and result.action_set_signature is None
    assert result.candidates == original.candidates
    assert result.original_recovery_composed_snapshot == original
    assert f"REGISTERED_{family.upper()}_COMPLETE_EXACT_BINDING_NOT_PROVEN" in result.reasons


@pytest.mark.parametrize("family", ["release", "joint"])
def test_missing_family_sources_keep_original_set_and_whole_global_unknown(family: str) -> None:
    data = fixture()
    broken = getattr(data, family).model_copy(
        update={"source_reasons": ["MISSING_ACTUAL_BANK_COPY"]}
    )
    result = derive_registered_action_set(data.model_copy(update={family: broken}))
    assert not result.global_action_set_complete and result.status == "UNKNOWN"
    assert "MISSING_ACTUAL_BANK_COPY" in result.reasons
    assert result.action_set_signature is None
    assert result.original_recovery_composed_snapshot.global_action_set_complete


def test_current_permission_changes_registered_signature_without_granting_authority() -> None:
    before, after = (
        derive_registered_action_set(fixture()),
        derive_registered_action_set(fixture(auto=False)),
    )
    assert before.global_action_set_complete and after.global_action_set_complete
    assert before.action_set_signature != after.action_set_signature
    assert before.candidates[0].amount_cents == after.candidates[0].amount_cents == 300
    assert after.candidates[0].autonomy_level == "ASK_ONCE"
    assert after.bank_authority is False and after.financial_write is False
