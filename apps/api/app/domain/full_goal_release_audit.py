"""Historical identity of the new release protocol, never current financial authority."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.full_goal_release_execution import (
    GoalReleaseBankCommand,
    GoalReleaseEffect,
    goal_release_leg_specs,
)
from app.domain.goal_release_provenance import GoalCashSourceProof
from app.domain.policy_configuration import configuration_hash


def read_frozen_goal_release_action(
    action: dict[str, Any],
) -> tuple[GoalReleaseBankCommand, GoalCashSourceProof]:
    """An exact new key cannot be interpreted as an old ExecutionEffect."""
    request = action["request"]
    command = GoalReleaseBankCommand.model_validate_json(
        json.dumps(request["goal_release_execution"], allow_nan=False)
    )
    basis = GoalCashSourceProof.model_validate_json(
        json.dumps(request["goal_release_source_basis"], allow_nan=False)
    )
    effect = command.effect
    if (
        configuration_hash(request) != action["request_hash"]
        or UUID(action["id"]) != effect.operation_id
        or UUID(action["user_id"]) != effect.user_id
        or action["action_type"] != "RELEASE_GOAL"
        or type(action["amount_cents"]) is not int
        or action["amount_cents"] != effect.amount_cents
        or action["idempotency_key"] != effect.bank_idempotency_key
        or action["source_account_id"] != str(effect.source_account_id)
        or action["destination_account_id"] != str(effect.destination_account_id)
        or action["goal_id"] != str(effect.source_goal_id)
        or action["policy_version_id"] != str(effect.original_goal_policy_version_id)
        or action.get("position_id") is not None
        or action.get("product_id") is not None
        or datetime.fromisoformat(action["expires_at"].replace("Z", "+00:00")) != effect.expires_at
        or basis.state != "VERIFIED_CASH_ONLY"
        or basis.user_id != effect.user_id
        or basis.epoch_id != effect.epoch_id
        or basis.goal_id != effect.source_goal_id
        or basis.goal_account_id != effect.source_account_id
        or basis.goal_policy_version_id != effect.original_goal_policy_version_id
        or basis.source_binding_hash != effect.source_provenance_hash
        or basis.as_of > effect.valid_from
        or "execution" in request
    ):
        raise ValueError("Original dedicated release action or retained cash basis differs")
    originals = {
        (row.allocation_action_id, row.fragment_id): row for row in basis.original_allocation_slices
    }
    for use in effect.release_uses:
        original = originals.get((use.allocation_action_id, use.fragment_id))
        if (
            original is None
            or (
                use.original_policy_version_id,
                use.origin_transaction_id,
                use.income_location_account_id,
                use.allocation_effect_hash,
                use.allocation_bank_request_hash,
                use.allocation_action_request_hash,
            )
            != (
                original.original_policy_version_id,
                original.origin_transaction_id,
                original.income_location_account_id,
                original.allocation_effect_hash,
                original.allocation_bank_request_hash,
                original.allocation_action_request_hash,
            )
            or use.amount_cents > original.original_allocated_cents
        ):
            raise ValueError(
                "Release use differs from its exact retained original allocation slice"
            )
    return command, basis


def frozen_goal_release_identity(
    operation: dict[str, Any], action: dict[str, Any], observed_at: datetime
) -> tuple[GoalReleaseEffect, dict[str, tuple[str, str, int]]]:
    command, _ = read_frozen_goal_release_action(action)
    effect = command.effect
    requested = datetime.fromisoformat(operation["requested_at"].replace("Z", "+00:00"))
    available = datetime.fromisoformat(operation["available_at"].replace("Z", "+00:00"))
    if (
        operation["request"] != command.model_dump(mode="json")
        or operation["request_hash"] != configuration_hash(operation["request"])
        or operation["id"] != action["id"]
        or operation["action_plan_id"] != action["id"]
        or operation["user_id"] != action["user_id"]
        or operation["operation_type"] != "RELEASE_GOAL"
        or operation["business_key"] != effect.business_key
        or operation["idempotency_key"] != effect.bank_idempotency_key
        or operation.get("legacy_redemption_id") is not None
        or operation.get("closing_position_id") is not None
        or not effect.valid_from <= requested < effect.expires_at
        or not requested <= available <= observed_at
    ):
        raise ValueError("Original independent release request differs from its original action")
    return effect, {
        leg.leg_ref: (leg.ledger_key, leg.ledger_dimension, leg.delta_cents)
        for leg in goal_release_leg_specs(command)
    }


def verify_frozen_goal_release_posting(effect: GoalReleaseEffect, row: dict[str, Any]) -> None:
    """Extra identity checks in addition to the common frozen ledger verifier."""
    expected_account = (
        effect.destination_account_id
        if row["leg_ref"] == f"cash:{effect.destination_account_id}"
        else effect.source_account_id
    )
    if (
        row.get("account_id") != str(expected_account)
        or row.get("position_id") is not None
        or row.get("redemption_id") is not None
        or row.get("external_fact_id") is not None
        or type(row["balance_before_cents"]) is not int
        or type(row["balance_after_cents"]) is not int
        or min(row["balance_before_cents"], row["balance_after_cents"]) < 0
        or (
            row["leg_ref"] == "goal_cash"
            and row.get("ledger_metadata")
            != {
                "goal_id": str(effect.source_goal_id),
                "account_id": str(effect.source_account_id),
            }
        )
    ):
        raise ValueError(
            "Release leg changed source ownership, dimension identity or original origin"
        )
