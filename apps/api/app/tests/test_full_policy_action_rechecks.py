"""Synthetic complete row counts, not a database or bank truth claim."""

import pytest
from app.domain.full_policy_action_rechecks import release_recheck_disposition


@pytest.mark.parametrize("status", ["PLANNED", "AUTHORIZED"])
def test_only_never_submitted_originals_can_be_invalidated(status: str) -> None:
    assert (
        release_recheck_disposition(
            status,
            permission_changed=True,
            bank_operation_count=0,
            receipt_count=0,
            posting_count=0,
        )
        == "INVALIDATE_UNSUBMITTED"
    )
    assert (
        release_recheck_disposition(
            status,
            permission_changed=False,
            bank_operation_count=0,
            receipt_count=0,
            posting_count=0,
        )
        == "KEEP"
    )


@pytest.mark.parametrize(
    "status", ["SUBMITTED", "UNKNOWN", "FAILED", "SUCCEEDED", "RECONCILED", "UNRECOGNIZED"]
)
def test_uncertain_status_never_becomes_withdrawn_or_no_effect(status: str) -> None:
    assert (
        release_recheck_disposition(
            status,
            permission_changed=True,
            bank_operation_count=0,
            receipt_count=0,
            posting_count=0,
        )
        == "RETAIN_INFLIGHT"
    )


@pytest.mark.parametrize("counts", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (2, 1, 3)])
def test_any_original_bank_receipt_or_leg_blocks_initial_invalidation(
    counts: tuple[int, int, int],
) -> None:
    assert (
        release_recheck_disposition(
            "PLANNED",
            permission_changed=True,
            bank_operation_count=counts[0],
            receipt_count=counts[1],
            posting_count=counts[2],
        )
        == "RETAIN_INFLIGHT"
    )


@pytest.mark.parametrize("status", ["SUCCEEDED", "RECONCILED"])
def test_verified_original_settlement_is_terminal_without_rollback(status: str) -> None:
    assert (
        release_recheck_disposition(
            status,
            permission_changed=True,
            bank_operation_count=1,
            receipt_count=1,
            posting_count=3,
            verified_final_settlement=True,
        )
        == "TERMINAL"
    )


@pytest.mark.parametrize("counts", [(0, 1, 3), (1, 0, 3), (1, 1, 2), (2, 1, 3)])
def test_final_status_without_complete_originals_cannot_claim_verified_settlement(
    counts: tuple[int, int, int],
) -> None:
    with pytest.raises(ValueError):
        release_recheck_disposition(
            "SUCCEEDED",
            permission_changed=True,
            bank_operation_count=counts[0],
            receipt_count=counts[1],
            posting_count=counts[2],
            verified_final_settlement=True,
        )


@pytest.mark.parametrize("status", ["INVALIDATED", "CANCELLED", "EXPIRED", "REJECTED"])
def test_original_unsubmitted_terminal_is_not_reanimated(status: str) -> None:
    assert (
        release_recheck_disposition(
            status,
            permission_changed=True,
            bank_operation_count=0,
            receipt_count=0,
            posting_count=0,
        )
        == "TERMINAL"
    )


@pytest.mark.parametrize("count", [True, -1, 0.0])
def test_counts_cannot_be_coerced_or_negative(count: int) -> None:
    with pytest.raises(ValueError):
        release_recheck_disposition(
            "PLANNED",
            permission_changed=True,
            bank_operation_count=count,
            receipt_count=0,
            posting_count=0,
        )
