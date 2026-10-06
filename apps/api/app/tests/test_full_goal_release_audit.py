"""Synthetic new-protocol identity risks; these are not actual bank execution evidence."""

from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid5

import pytest
from app.domain.audit_chain import AuditContractError, verify_frozen_settlement
from app.domain.full_goal_release_audit import (
    frozen_goal_release_identity,
    read_frozen_goal_release_action,
    verify_frozen_goal_release_posting,
)
from app.domain.full_goal_release_execution import (
    GoalReleaseBankCommand,
    GoalReleaseEffect,
    GoalReleaseUse,
    goal_release_effect_hash,
    goal_release_leg_specs,
)
from app.domain.goal_release_provenance import prove_goal_cash_sources
from app.domain.policy_configuration import configuration_hash
from app.tests.test_goal_release_provenance import NOW, fixture


def originals() -> tuple[dict[str, Any], dict[str, Any]]:
    basis = prove_goal_cash_sources(fixture())
    source = basis.original_allocation_slices[0]
    effect = GoalReleaseEffect(
        operation_id=UUID(int=8800),
        user_id=basis.user_id,
        epoch_id=basis.epoch_id,
        business_key="synthetic-release-business",
        bank_idempotency_key="synthetic-release-original-key",
        policy_id=UUID(int=8801),
        policy_version_id=UUID(int=8802),
        policy_configuration_hash="a" * 64,
        authorization_id=UUID(int=8803),
        authorization_evidence_id=UUID(int=8804),
        authorization_evidence_hash="b" * 64,
        authorization_scope_hash="c" * 64,
        authorization_request_hash="d" * 64,
        source_goal_id=basis.goal_id,
        original_goal_policy_id=UUID(int=8805),
        original_goal_policy_version_id=basis.goal_policy_version_id,
        full_model_evidence_id=UUID(int=8806),
        full_model_evidence_hash="e" * 64,
        full_configuration_hash="f" * 64,
        minimum_guarantee_cents=200,
        source_account_id=basis.goal_account_id,
        destination_account_id=UUID(int=8807),
        amount_cents=100,
        emergency_conditions=["HARD_OBLIGATION_SHORTFALL"],
        release_uses=[
            GoalReleaseUse(
                allocation_action_id=source.allocation_action_id,
                original_policy_version_id=source.original_policy_version_id,
                fragment_id=source.fragment_id,
                origin_transaction_id=source.origin_transaction_id,
                income_location_account_id=source.income_location_account_id,
                allocation_effect_hash=source.allocation_effect_hash,
                allocation_bank_request_hash=source.allocation_bank_request_hash,
                allocation_action_request_hash=source.allocation_action_request_hash,
                amount_cents=100,
            )
        ],
        source_provenance_hash=basis.source_binding_hash,
        financial_input_hash="0" * 64,
        valid_from=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    command = GoalReleaseBankCommand(effect=effect, effect_hash=goal_release_effect_hash(effect))
    request = {
        "goal_release_execution": command.model_dump(mode="json"),
        "goal_release_source_basis": basis.model_dump(mode="json"),
    }
    action: dict[str, Any] = {
        "id": str(effect.operation_id),
        "user_id": str(effect.user_id),
        "action_type": "RELEASE_GOAL",
        "amount_cents": 100,
        "source_account_id": str(effect.source_account_id),
        "destination_account_id": str(effect.destination_account_id),
        "goal_id": str(effect.source_goal_id),
        "policy_version_id": str(effect.original_goal_policy_version_id),
        "product_id": None,
        "position_id": None,
        "idempotency_key": effect.bank_idempotency_key,
        "expires_at": effect.expires_at.isoformat(),
        "request": request,
        "request_hash": configuration_hash(request),
    }
    operation = {
        "id": action["id"],
        "action_plan_id": action["id"],
        "user_id": action["user_id"],
        "operation_type": "RELEASE_GOAL",
        "business_key": effect.business_key,
        "idempotency_key": effect.bank_idempotency_key,
        "request": command.model_dump(mode="json"),
        "request_hash": configuration_hash(command.model_dump(mode="json")),
        "legacy_redemption_id": None,
        "closing_position_id": None,
        "requested_at": NOW.isoformat(),
        "available_at": NOW.isoformat(),
    }
    return action, operation


def test_new_release_binds_original_goal_mvp_version_without_changing_old_effect() -> None:
    action, operation = originals()
    before = deepcopy((action, operation))
    effect, legs = frozen_goal_release_identity(operation, action, NOW)
    assert effect.original_goal_policy_version_id != effect.policy_version_id
    assert legs == {
        f"cash:{effect.source_account_id}": (f"CASH:{effect.source_account_id}", "ECONOMIC", -100),
        f"cash:{effect.destination_account_id}": (
            f"CASH:{effect.destination_account_id}",
            "ECONOMIC",
            100,
        ),
        "goal_cash": (f"GOAL_CASH:{effect.source_goal_id}", "GOAL_OWNERSHIP", -100),
    }
    assert (action, operation) == before
    assert not any(key.startswith("income") for key in legs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", str(UUID(int=9999))),
        ("user_id", str(UUID(int=9999))),
        ("action_type", "TRANSFER_INTERNAL"),
        ("amount_cents", True),
        ("amount_cents", 101),
        ("policy_version_id", str(UUID(int=8802))),
        ("source_account_id", str(UUID(int=9999))),
        ("destination_account_id", str(UUID(int=9999))),
        ("goal_id", str(UUID(int=9999))),
        ("idempotency_key", "a-new-key"),
        ("product_id", str(UUID(int=9999))),
        ("position_id", str(UUID(int=9999))),
        ("expires_at", (NOW + timedelta(hours=1)).isoformat()),
    ],
)
def test_original_action_identity_tamper_rejected(field: str, value: Any) -> None:
    action, _ = originals()
    action[field] = value
    with pytest.raises(ValueError):
        read_frozen_goal_release_action(action)


@pytest.mark.parametrize("field", ["state", "source_binding_hash", "goal_id", "as_of"])
def test_retained_basis_tamper_not_upgraded_by_recomputed_outer_hash(field: str) -> None:
    action, _ = originals()
    values = {
        "state": "UNKNOWN",
        "source_binding_hash": "a" * 64,
        "goal_id": str(UUID(int=9999)),
        "as_of": (NOW + timedelta(seconds=1)).isoformat(),
    }
    action["request"]["goal_release_source_basis"][field] = values[field]
    action["request_hash"] = configuration_hash(action["request"])
    with pytest.raises(ValueError):
        read_frozen_goal_release_action(action)


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_plan_id", str(UUID(int=9999))),
        ("operation_type", "ALLOCATE_GOAL"),
        ("business_key", "different-business"),
        ("idempotency_key", "new-bank-key"),
        ("legacy_redemption_id", str(UUID(int=9999))),
        ("closing_position_id", str(UUID(int=9999))),
        ("requested_at", (NOW + timedelta(hours=1)).isoformat()),
    ],
)
def test_independent_original_operation_cannot_change_its_bound_action(
    field: str, value: Any
) -> None:
    action, operation = originals()
    operation[field] = value
    with pytest.raises(ValueError):
        frozen_goal_release_identity(operation, action, NOW)


def test_goal_ownership_leg_cannot_be_rebound_to_another_origin_or_account() -> None:
    action, _ = originals()
    effect = read_frozen_goal_release_action(action)[0].effect
    posting: dict[str, Any] = {
        "leg_ref": "goal_cash",
        "account_id": str(effect.source_account_id),
        "position_id": None,
        "redemption_id": None,
        "external_fact_id": None,
        "balance_before_cents": 300,
        "balance_after_cents": 200,
        "ledger_metadata": {
            "goal_id": str(effect.source_goal_id),
            "account_id": str(effect.source_account_id),
        },
    }
    verify_frozen_goal_release_posting(effect, posting)
    posting["external_fact_id"] = str(UUID(int=9999))
    with pytest.raises(ValueError):
        verify_frozen_goal_release_posting(effect, posting)


def settled_originals() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    action, operation = originals()
    operation.update({"status": "SETTLED", "settled_at": NOW.isoformat()})
    command = read_frozen_goal_release_action(action)[0]
    rows = [
        {
            "id": str(uuid5(command.effect.operation_id, "posting:" + leg.leg_ref)),
            "user_id": action["user_id"],
            "operation_id": action["id"],
            "leg_ref": leg.leg_ref,
            "ledger_key": leg.ledger_key,
            "ledger_dimension": leg.ledger_dimension,
            "ledger_metadata": leg.required_metadata,
            "account_id": str(leg.account_id),
            "position_id": None,
            "redemption_id": None,
            "external_fact_id": None,
            "entry_kind": "SETTLEMENT",
            "balance_before_cents": 300,
            "delta_cents": leg.delta_cents,
            "balance_after_cents": 300 + leg.delta_cents,
            "created_at": NOW.isoformat(),
            "occurred_at": NOW.isoformat(),
        }
        for leg in goal_release_leg_specs(command)
    ]
    return action, operation, rows


def test_original_frozen_bank_verifier_reads_new_protocol_without_old_effect_changes() -> None:
    action, operation, rows = settled_originals()
    before = deepcopy((action, operation, rows))
    verify_frozen_settlement(operation, action, rows, observed_at=NOW)
    assert (action, operation, rows) == before


@pytest.mark.parametrize(
    "tamper", ["missing_leg", "duplicate_leg", "relabel_income", "owner", "metadata"]
)
def test_frozen_new_protocol_rejects_missing_duplicate_or_relabelled_original_legs(
    tamper: str,
) -> None:
    action, operation, rows = settled_originals()
    if tamper == "missing_leg":
        rows.pop()
    elif tamper == "duplicate_leg":
        rows.append(deepcopy(rows[0]))
    elif tamper == "relabel_income":
        rows[-1]["ledger_key"] = "LOT_AVAILABLE:" + str(UUID(int=9999))
        rows[-1]["ledger_dimension"] = "INCOME_LOCATION"
    elif tamper == "owner":
        rows[-1]["account_id"] = str(UUID(int=9999))
    else:
        rows[-1]["ledger_metadata"]["goal_id"] = str(UUID(int=9999))
    with pytest.raises(AuditContractError):
        verify_frozen_settlement(operation, action, rows, observed_at=NOW)
