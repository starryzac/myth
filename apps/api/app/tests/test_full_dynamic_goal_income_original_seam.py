"""Strict synthetic native income seam risks; never claims actual bank evidence."""

from copy import deepcopy

import pytest
from app.domain.full_dynamic_goal_execution import native_income_original_matches
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_dynamic_goal_execution import fixture


def test_native_datetime_spelling_preserves_original_bytes_hash_and_values() -> None:
    ledger = fixture().income
    raw = ledger.model_dump(mode="json")
    raw["as_of"] = ledger.as_of.isoformat()
    before = deepcopy(raw)
    digest = configuration_hash(raw)
    assert raw != ledger.model_dump(mode="json")
    assert native_income_original_matches(raw, ledger)
    assert raw == before and configuration_hash(raw) == digest
    assert digest != configuration_hash(ledger.model_dump(mode="json"))


@pytest.mark.parametrize(
    "field", ["protocol", "user_id", "as_of", "complete", "extra", "fragments"]
)
def test_native_ledger_economic_identity_scope_or_shape_changes_remain_refused(field: str) -> None:
    ledger = fixture().income
    raw = ledger.model_dump(mode="json")
    changes = {
        "protocol": "new-funds-ledger-v1",
        "user_id": "00000000-0000-0000-0000-000000000099",
        "as_of": "2026-10-06T12:00:00Z",
        "complete": False,
        "extra": "forbidden",
        "fragments": [],
    }
    raw[field] = changes[field]
    assert not native_income_original_matches(raw, ledger)
