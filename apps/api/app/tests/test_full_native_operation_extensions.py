"""Fixed-route contract risks only; no PG, financial runs, or frozen cases."""

from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from app.domain.full_native_operations import OPERATIONS, native_request

IDENTITY = str(UUID(int=7))


def test_actual_planning_options_are_consumed_in_fixed_query() -> None:
    plan = native_request(
        "FULL_ASSET_PLAN",
        {
            "policy_id": IDENTITY,
            "planning_constraints": {
                "comparison_days": 30,
                "max_components": 2,
                "max_turnover_cents": 770003,
                "funds_use_date": "2026-11-04",
                "mode": "FIXED_LADDER",
            },
        },
    )
    parsed = urlsplit(plan.path)
    assert plan.method == "GET" and plan.body is None
    assert parsed.path == f"/api/v1/full-policies/{IDENTITY}/asset-allocation"
    assert parse_qs(parsed.query) == {
        "planning_comparison_days": ["30"],
        "planning_max_components": ["2"],
        "planning_max_turnover_cents": ["770003"],
        "planning_funds_use_date": ["2026-11-04"],
        "planning_mode": ["FIXED_LADDER"],
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("comparison_days", True),
        ("max_components", 1.0),
        ("max_turnover_cents", True),
        ("max_turnover_cents", -1),
        ("mode", "AUTO"),
        ("role", "USER"),
        ("url", "https://example.com"),
        ("funds_use_date", "2026-02-30"),
    ],
)
def test_planning_query_cannot_smuggle_authority_or_coerce_amounts(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        native_request(
            "FULL_ASSET_PLAN",
            {
                "policy_id": IDENTITY,
                "planning_constraints": {field: value},
            },
        )


def test_payment_original_key_has_separate_exact_identity_and_no_body() -> None:
    plan = native_request("PAYMENT_COMMAND_READ", {"epoch_id": IDENTITY, "key": "original:1-2"})
    assert plan.path == f"/api/v1/full-payment-relations/commands/{IDENTITY}/by-key/original:1-2"
    assert plan.body is None


@pytest.mark.parametrize("key", ["../reset", "one/two", "a?role=USER", "a#x", "a%2Fb", "", True])
def test_original_key_cannot_change_route(key: Any) -> None:
    with pytest.raises(ValueError):
        native_request("PAYMENT_COMMAND_READ", {"epoch_id": IDENTITY, "key": key})


def test_all_added_full_fault_pairs_are_still_explicitly_unsupported() -> None:
    for kind in ("FULL_ASSET_EXECUTE", "FULL_RECOVERY_EXECUTE", "PAYMENT_EXECUTE"):
        with pytest.raises(ValueError, match="FAULT_PAIR_NOT_IMPLEMENTED"):
            native_request(kind, {}, fault="DROP_BANK_RESPONSE")
    assert len(OPERATIONS) == 50


def test_additions_never_accept_arbitrary_paths_queries_or_result_fields() -> None:
    for kind in ("CATALOG_REGISTER_CURRENT", "FULL_GOAL_REPAIR_PREVIEW", "MATURITY_REPLAN"):
        with pytest.raises(ValueError, match="INPUT_KEYS_NOT_EXACT"):
            native_request(kind, {"body": {}, "result": {"success": True}})
