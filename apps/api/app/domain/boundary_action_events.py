"""Semantic single-action comparisons; financial magnitudes alone are not events."""

from typing import Literal

from app.domain.autonomy import _economic_payload
from app.domain.execution_types import ExecutionEffect, ExecutionValidation
from app.domain.policy_configuration import configuration_hash

ALGORITHM_VERSION = "boundary-action-observation-v1"


def action_signature(
    effect: ExecutionEffect, validation: ExecutionValidation, autonomy_level: str
) -> str:
    if autonomy_level not in {"AUTO_EXECUTE", "ASK_ONCE", "ADVISE_ONLY", "BLOCKED"}:
        raise ValueError("The original action classification is unknown")
    if validation.status == "INSUFFICIENT_EVIDENCE":
        raise ValueError("Missing evidence is not an empty verified action set")
    if autonomy_level == "BLOCKED" and validation.status == "BLOCKED":
        return configuration_hash({"admissible_actions": []})
    value = _economic_payload(effect)
    # The old effect/hash stay untouched. This separate signature ignores generated
    # identities and receipt metadata, while retaining actual money, ownership,
    # bank destination, product/position, loss, fee and availability consequences.
    for field in (
        "policy_version_id",
        "policy_version_ids",
        "original_policy_version_id",
    ):
        value.pop(field)
    if value["liability"] is not None:
        value["liability"].pop("policy_version_id", None)
    return configuration_hash(
        {"effect": value, "autonomy_level": autonomy_level, "validation_status": validation.status}
    )


def event_kind(before: str, after: str) -> Literal["BoundaryCrossed", "BoundaryObserved"]:
    return "BoundaryCrossed" if before != after else "BoundaryObserved"
