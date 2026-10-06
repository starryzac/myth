"""Exact family-composition risks; synthetic original inputs, not bank evidence."""

import copy
from uuid import UUID

import pytest
from app.domain.full_action_set_boundary import unsupported_producers
from app.domain.full_action_set_boundary_actual import derive_actual_action_set
from app.domain.full_action_set_boundary_composed import (
    ALGORITHM,
    ComposedActionSetInput,
    derive_composed_action_set,
)
from app.tests.test_full_action_set_boundary_actual import coverage
from app.tests.test_full_action_set_payment_producers import fixture


@pytest.mark.parametrize("auto", [True, False])
def test_original_300_cent_payment_is_replaced_once_and_keeps_original_v2(auto: bool) -> None:
    family = fixture(auto=auto)
    before = family.model_dump_json()
    original = derive_actual_action_set(family.original_actual_input)
    current = derive_composed_action_set(ComposedActionSetInput(periodic=family))
    assert current.algorithm_version == ALGORITHM
    assert current.original_actual_snapshot == original
    assert family.model_dump_json() == before
    assert current.global_action_set_complete, current.reasons
    assert current.status == "COMPLETE" and not current.unsupported_producers
    assert len(current.candidates) == len(current.expected_candidate_keys) == 1
    candidate = current.candidates[0]
    assert candidate.candidate_key.startswith("full-periodic:")
    assert candidate.state == "INCLUDED" and candidate.amount_cents == 300
    assert candidate.autonomy_level == ("AUTO_EXECUTE" if auto else "ASK_ONCE")
    assert family.producers[0].candidate is not None
    assert current.replaced_original_candidate_keys == [family.producers[0].candidate.candidate_key]
    assert current.action_set_signature is not None
    assert (
        not current.bank_authority and not current.grants_authority and not current.financial_write
    )
    assert current.notification_support == "NOT_IMPLEMENTED_FOR_COMPOSED_V3"


def test_unhandled_recovery_family_remains_unknown_after_actual_periodic_replacement() -> None:
    family = fixture()
    actual = family.original_actual_input
    inventory = copy.deepcopy(actual.base.original_inventory)
    identity = UUID(int=3407)
    inventory["full_policies"].append(
        {
            "id": str(identity),
            "user_id": str(actual.base.user_id),
            "epoch_id": str(actual.base.epoch_id),
            "template_name": "RecoveryPolicy",
            "status": "ACTIVE",
        }
    )
    actual = actual.model_copy(
        update={
            "base": actual.base.model_copy(
                update={
                    "original_inventory": inventory,
                    "unsupported_producers": unsupported_producers(inventory, actual.base.epoch_id),
                }
            ),
            "table_coverage": coverage(inventory),
        }
    )
    current = derive_composed_action_set(
        ComposedActionSetInput(periodic=family.model_copy(update={"original_actual_input": actual}))
    )
    assert current.periodic_family.periodic_family_complete, current.periodic_family.reasons
    assert current.status == "UNKNOWN" and not current.global_action_set_complete
    assert current.unsupported_producers == [
        "FULL_PRODUCER_ADAPTER_MISSING:RecoveryPolicy:" + str(identity)
    ]
    assert len(current.replaced_original_candidate_keys) == 1
    assert current.action_set_signature is None


@pytest.mark.parametrize(
    "change", ["family-denominator", "source", "original-count", "missing-proof"]
)
def test_incomplete_replacement_keeps_original_candidate_and_never_hides_unknown(
    change: str,
) -> None:
    family = fixture()
    assert family.producers[0].candidate is not None
    original_key = family.producers[0].candidate.candidate_key
    if change == "family-denominator":
        family = family.model_copy(update={"producers": [*family.producers, family.producers[0]]})
    elif change == "source":
        family = family.model_copy(
            update={"source_reasons": ["ORIGINAL_RELATION_SOURCE_NOT_VERIFIED"]}
        )
    elif change == "original-count":
        actual = family.original_actual_input
        rows = list(actual.table_coverage)
        rows[0] = rows[0].model_copy(update={"actual_count": rows[0].captured_count + 1})
        family = family.model_copy(
            update={"original_actual_input": actual.model_copy(update={"table_coverage": rows})}
        )
    else:
        producer = family.producers[0].model_copy(update={"relation_binding": None})
        family = family.model_copy(update={"producers": [producer]})
    current = derive_composed_action_set(ComposedActionSetInput(periodic=family))
    assert current.status == "UNKNOWN" and not current.global_action_set_complete
    assert original_key in {row.candidate_key for row in current.candidates}
    assert current.replaced_original_candidate_keys == []
    assert current.unsupported_producers
    assert current.action_set_signature is None


def test_user_cannot_supply_a_computed_family_result_or_manual_intent() -> None:
    raw = ComposedActionSetInput(periodic=fixture()).model_dump(mode="json")
    for field in ("manual_intent", "handled_unsupported_codes", "computed_family_result"):
        with pytest.raises(ValueError):
            ComposedActionSetInput.model_validate({**raw, field: {"amount_cents": 300}})
