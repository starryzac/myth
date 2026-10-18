"""Bounded, lossless originals for the explicit delegated SYSTEM v2 protocol.

V4 joint sharing and the legacy generic decoder retain their original caps.
An explicit asset carrier factors only three duplicated whole-mode originals:
the shared record stays within 64MiB/1m/depth64; exact logical expansion work
is verified before allocating those three fixed copies. Neither transport nor
its digest is an authority or a financial proof.
"""

import copy
from typing import Any

from app.domain.full_joint_goal_archive import decode_joint_archive, encode_joint_archive
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_asset_original_archive import (
    PROTOCOL as ASSET_REFERENCE_PROTOCOL,
)
from app.domain.standing_asset_original_archive import (
    pack_asset_original,
    unpack_asset_original,
)

PROTOCOL = "zhiyu-standing-native-original-transport-v2"
FIELDS = {
    "protocol",
    "input_archive",
    "joint_plan",
    "joint_plan_reference",
    "original_hash",
    "bank_authority",
    "financial_validation_performed",
}


def pack_native_original(value: dict[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(value)
    plan_record = plan_reference = None
    if raw["native"]["kind"] == "JOINT_GOALS":
        from app.domain.full_joint_goal_source_adapter import source_binding_for_plan
        from app.domain.full_joint_goal_source_archive import encode_shared_joint, reference

        plan = raw["native"].pop("plan")
        plan_record = encode_shared_joint(plan, source_binding_for_plan(plan), "PLAN")
        plan_reference = reference(plan_record)
    input_record = raw
    if (
        raw["native"]["kind"] in {"ASSET_PORTFOLIO", "MATURITY_REINVESTMENT"}
        and raw["native"].get("residual_mode_evaluation") is not None
    ):
        input_record = pack_asset_original(raw)
    result = {
        "protocol": PROTOCOL,
        "input_archive": encode_joint_archive(input_record),
        "joint_plan": plan_record,
        "joint_plan_reference": plan_reference,
        "original_hash": configuration_hash(value),
        "bank_authority": False,
        "financial_validation_performed": False,
    }
    # This hash must be recorded in an independently audited EvidenceItem and
    # passed back by that actual original reader, never trusted from HTTP JSON.
    return result


def read_native_original(record: dict[str, Any], *, expected_record_hash: str) -> dict[str, Any]:
    if (
        type(record) is not dict
        or set(record) != FIELDS
        or record["protocol"] != PROTOCOL
        or record["bank_authority"] is not False
        or record["financial_validation_performed"] is not False
        or configuration_hash(record) != expected_record_hash
    ):
        raise ValueError("Exact independently retained delegated transport differs")
    raw = decode_joint_archive(record["input_archive"])
    if raw.get("protocol") == ASSET_REFERENCE_PROTOCOL:
        raw = unpack_asset_original(raw, expected_expanded_hash=record["original_hash"])
    if raw["native"]["kind"] == "JOINT_GOALS":
        from app.domain.full_joint_goal_source_adapter import decode_source_record

        plan, ref = record["joint_plan"], record["joint_plan_reference"]
        if not isinstance(plan, dict) or not isinstance(ref, dict) or "plan" in raw["native"]:
            raise ValueError("Complete independently retained joint PLAN is required")
        raw["native"]["plan"] = decode_source_record(
            plan, ref["binding"], "PLAN", expected_reference=ref
        )
    elif record["joint_plan"] is not None or record["joint_plan_reference"] is not None:
        raise ValueError("A non-joint family cannot substitute a joint original")
    if configuration_hash(raw) != record["original_hash"]:
        raise ValueError("Complete delegated native original hash differs")
    return raw
