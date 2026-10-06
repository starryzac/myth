"""A policy change never proves that an already submitted bank effect was withdrawn."""

from typing import Literal

RecheckDisposition = Literal["INVALIDATE_UNSUBMITTED", "RETAIN_INFLIGHT", "TERMINAL", "KEEP"]


def release_recheck_disposition(
    action_status: str,
    *,
    permission_changed: bool,
    bank_operation_count: int,
    receipt_count: int,
    posting_count: int,
    verified_final_settlement: bool = False,
) -> RecheckDisposition:
    """Counts must come from all original rows under the actual owner's write lock.

    This finite decision supplies no evidence itself. The production caller must
    verify original action/decision identity and complete bank/receipt history.
    Even a REJECTED bank string does not justify clearing an uncertain action.
    """
    counts = (bank_operation_count, receipt_count, posting_count)
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("Original inventory counts must be nonnegative strict integers")
    if type(permission_changed) is not bool or type(verified_final_settlement) is not bool:
        raise ValueError("Recheck flags must be booleans")
    if verified_final_settlement:
        if (
            action_status not in {"SUCCEEDED", "RECONCILED"}
            or bank_operation_count != 1
            or receipt_count != 1
            or posting_count != 3
        ):
            raise ValueError(
                "A verified release has one original operation, receipt and three legs"
            )
        return "TERMINAL"
    if action_status in {"SUBMITTED", "UNKNOWN", "FAILED", "SUCCEEDED", "RECONCILED"} or any(
        counts
    ):
        return "RETAIN_INFLIGHT"
    if action_status in {"INVALIDATED", "CANCELLED", "EXPIRED", "REJECTED"}:
        return "TERMINAL"
    if action_status not in {"PLANNED", "AUTHORIZED"}:
        return "RETAIN_INFLIGHT"
    return "INVALIDATE_UNSUBMITTED" if permission_changed else "KEEP"
