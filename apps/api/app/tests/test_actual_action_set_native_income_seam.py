"""Strict native original values for actual-v2; historical full-v1 stays exact JSON."""

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

import pytest
from app.domain.full_action_set_boundary_actual import (
    ActualActionSetInput,
    derive_actual_action_set,
)
from app.domain.full_action_set_boundary_full import dynamic_candidate
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_action_set_boundary_actual import coverage, fixture


def with_native_spelling(change: str = "equivalent") -> ActualActionSetInput:
    value = fixture()
    producer = value.dynamic_goals[0]
    data = producer.data
    assert data is not None
    inventory = deepcopy(value.base.original_inventory)
    income = next(
        row for row in inventory["evidence_items"] if row["id"] == str(data.income_evidence_id)
    )
    raw: dict[str, Any] = income["content"]
    raw["as_of"] = data.income.as_of.isoformat()
    if change == "clock":
        raw["as_of"] = (data.income.as_of + timedelta(seconds=1)).isoformat()
    elif change == "protocol":
        raw["schema_version"] = "income-ledger-v1"
    elif change == "amount":
        raw["origins"][0]["amount_cents"] += 1
    elif change == "boolean":
        raw["origins"][0]["amount_cents"] = True
    digest = configuration_hash(raw)
    income["content_hash"] = digest
    basis = deepcopy(value.base.financial_basis)
    basis["income"] = deepcopy(raw)
    for source in basis["evidence"]:
        if source["id"] == str(data.income_evidence_id):
            source["hash"] = digest
    data = data.model_copy(
        update={
            "income_evidence_hash": digest,
            "source_refs": [
                ref.model_copy(update={"content_hash": digest})
                if ref.evidence_id == data.income_evidence_id
                else ref
                for ref in data.source_refs
            ],
        }
    )
    return value.model_copy(
        update={
            "base": value.base.model_copy(
                update={
                    "original_inventory": inventory,
                    "financial_basis": basis,
                    "financial_input_hash": configuration_hash(basis),
                }
            ),
            "table_coverage": coverage(inventory),
            "dynamic_goals": [producer.model_copy(update={"data": data})],
        }
    )


def test_actual_v2_accepts_same_instant_native_original_and_keeps_full_v1_refusal() -> None:
    value = with_native_spelling()
    original = value.model_dump_json()
    legacy, shadow = dynamic_candidate(value.base, value.dynamic_goals[0])
    assert legacy.state == "UNKNOWN" and shadow is None
    assert legacy.reasons == ["DYNAMIC_ORIGINAL_MODEL_OR_INCOME_COPY_DIFFERS"]
    actual = derive_actual_action_set(value)
    assert actual.global_action_set_complete, actual.reasons
    assert actual.candidates[0].state == "INCLUDED"
    assert actual.candidates[0].amount_cents == 150000
    assert value.model_dump_json() == original


@pytest.mark.parametrize("change", ["clock", "protocol", "amount", "boolean"])
def test_actual_v2_does_not_accept_native_clock_protocol_or_integer_changes(change: str) -> None:
    value = with_native_spelling(change)
    result = derive_actual_action_set(value)
    assert result.status == "UNKNOWN" and result.action_set_signature is None
    assert result.candidates[0].state == "UNKNOWN"
    assert "DYNAMIC_ORIGINAL_MODEL_OR_INCOME_COPY_DIFFERS" in result.candidates[0].reasons


def test_original_hash_is_verified_before_same_instant_value_comparison() -> None:
    value = with_native_spelling()
    for raw in value.base.original_inventory["evidence_items"]:
        if value.dynamic_goals[0].data is not None and raw["id"] == str(
            value.dynamic_goals[0].data.income_evidence_id
        ):
            raw["content_hash"] = "0" * 64
    value = value.model_copy(update={"table_coverage": coverage(value.base.original_inventory)})
    result = derive_actual_action_set(value)
    assert result.status == "UNKNOWN" and result.action_set_signature is None
    assert any(
        "DYNAMIC_ORIGINAL_EVIDENCE_NOT_VERIFIED" in reason
        for reason in result.candidates[0].reasons
    )


def with_confirmation_clock(change: str = "equivalent") -> ActualActionSetInput:
    value = fixture()
    inventory = deepcopy(value.base.original_inventory)
    version = inventory["policy_versions"][0]
    clock = datetime.fromisoformat(version["confirmed_at"])
    if change == "equivalent":
        version["confirmed_at"] = clock.isoformat().replace("+00:00", "Z")
    elif change == "different":
        version["confirmed_at"] = (clock + timedelta(seconds=1)).isoformat()
    elif change == "naive":
        version["confirmed_at"] = clock.replace(tzinfo=None).isoformat()
    elif change == "invalid":
        version["confirmed_at"] = "not-a-clock"
    return value.model_copy(
        update={
            "base": value.base.model_copy(update={"original_inventory": inventory}),
            "table_coverage": coverage(inventory),
        }
    )


def test_native_original_confirmation_same_instant_only_in_actual_v2() -> None:
    value = with_confirmation_clock()
    before = value.model_dump_json()
    legacy, shadow = dynamic_candidate(value.base, value.dynamic_goals[0])
    assert legacy.state == "UNKNOWN" and shadow is None
    assert legacy.reasons == ["DYNAMIC_ORIGINAL_POLICY_CONFIRMATION_NOT_BOUND"]
    actual = derive_actual_action_set(value)
    assert actual.global_action_set_complete and actual.candidates[0].state == "INCLUDED"
    assert value.model_dump_json() == before


@pytest.mark.parametrize("change", ["different", "naive", "invalid"])
def test_actual_v2_rejects_confirmation_time_change_or_missing_awareness(change: str) -> None:
    result = derive_actual_action_set(with_confirmation_clock(change))
    assert result.status == "UNKNOWN" and result.action_set_signature is None
    assert result.candidates[0].state == "UNKNOWN"
