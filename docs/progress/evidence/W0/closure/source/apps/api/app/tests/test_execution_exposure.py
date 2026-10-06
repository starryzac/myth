"""Complete execution exposure must bind independent resources and auxiliary ledgers."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.domain.asset_exposure import asset_exposure_snapshot

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def test_v3_requires_both_complete_operation_and_resource_collections() -> None:
    kwargs: dict[str, Any] = dict(
        accounts=[],
        positions=[],
        actions=[],
        receipts=[],
        evidence=[],
        bank_requests=[],
        bank_postings=[],
    )
    with pytest.raises(ValueError):
        asset_exposure_snapshot(uuid4(), NOW, **kwargs, bank_operations=[])
    result = asset_exposure_snapshot(
        uuid4(), NOW, **kwargs, bank_operations=[], resource_reservations=[]
    )
    assert result["protocol"] == "asset-exposure-v3"
    assert result["bank_operations"] == result["resource_reservations"] == []


def test_v3_posting_digest_includes_dimension_operation_and_metadata() -> None:
    user_id = uuid4()
    posting = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        ledger_key="LOT_AVAILABLE:example",
        account_id=None,
        position_id=None,
        redemption_id=None,
        previous_posting_id=None,
        sequence_number=1,
        entry_kind="OPENING",
        balance_before_cents=0,
        delta_cents=100,
        balance_after_cents=100,
        occurred_at=NOW,
        created_at=NOW,
        ledger_dimension="INCOME_LOCATION",
        ledger_metadata={"origin": "original"},
        operation_id=None,
        leg_ref=None,
    )
    kwargs: dict[str, Any] = dict(
        accounts=[],
        positions=[],
        actions=[],
        receipts=[],
        evidence=[],
        bank_requests=[],
        bank_postings=[posting],
        bank_operations=[],
        resource_reservations=[],
    )
    before = asset_exposure_snapshot(user_id, NOW, **kwargs)
    posting.ledger_metadata = {"origin": "rebound"}
    after = asset_exposure_snapshot(user_id, NOW, **kwargs)
    assert before["bank_postings"] != after["bank_postings"]
