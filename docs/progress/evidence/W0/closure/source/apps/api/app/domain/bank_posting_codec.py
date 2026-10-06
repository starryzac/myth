"""Explicit original posting layouts; do not re-encode existing audit originals."""

from collections.abc import Mapping
from typing import Any

POSTING_V1_FIELDS = (
    "id",
    "user_id",
    "created_at",
    "ledger_key",
    "ledger_dimension",
    "ledger_metadata",
    "account_id",
    "position_id",
    "redemption_id",
    "operation_id",
    "leg_ref",
    "previous_posting_id",
    "sequence_number",
    "entry_kind",
    "balance_before_cents",
    "delta_cents",
    "balance_after_cents",
    "occurred_at",
)
POSTING_V2_FIELDS = (*POSTING_V1_FIELDS, "external_fact_id")
CLEARING_PREFIX = "CLEARING:bounded-funds-external-v1:"


def _origin(data: Mapping[str, Any], *, external: Any) -> int:
    key = data["ledger_key"]
    if type(key) is not str:
        raise ValueError("Posting ledger identity must be a string")
    opening = data["entry_kind"] == "OPENING"
    if opening:
        if (
            any(
                data[name] is not None
                for name in ("operation_id", "redemption_id", "leg_ref", "previous_posting_id")
            )
            or external is not None
        ):
            raise ValueError("Opening cannot acquire an economic event origin")
        if data["sequence_number"] != 1 or data["balance_before_cents"] != 0:
            raise ValueError("Opening must start its ledger at sequence one")
    else:
        if data["sequence_number"] <= 1:
            raise ValueError("Non-opening posting must follow an existing ledger entry")
        if (data["operation_id"] is None) == (external is None):
            raise ValueError("A posting requires exactly one command or external fact origin")
        if external is not None and data["redemption_id"] is not None:
            raise ValueError("An external fact cannot borrow a legacy redemption origin")
        if data["leg_ref"] is None or data["previous_posting_id"] is None:
            raise ValueError("A posting requires its original leg and predecessor")
    if key.startswith("CLEARING:"):
        if not key.startswith(CLEARING_PREFIX) or key == CLEARING_PREFIX:
            raise ValueError("Unknown external clearing ledger protocol")
        if data["ledger_dimension"] != "ECONOMIC" or any(
            data[name] is not None for name in ("account_id", "position_id", "redemption_id")
        ):
            raise ValueError("Clearing is a bank subledger, not a customer account")
        if not opening and external is None:
            raise ValueError("Clearing legs require an external fact origin")
        return 2
    return 2 if external is not None else 1


def bank_posting_snapshot_version(data: Mapping[str, Any]) -> int:
    """Determine a supported original layout, with exact fields and origin validation."""
    fields = set(data)
    if fields not in (set(POSTING_V1_FIELDS), set(POSTING_V2_FIELDS)):
        raise ValueError("Unknown or incomplete bank posting column layout")
    for name in ("sequence_number", "balance_before_cents", "delta_cents", "balance_after_cents"):
        if type(data[name]) is not int:
            raise ValueError("Posting amounts and sequence must be strict integers")
    if (
        data["sequence_number"] < 1
        or data["balance_before_cents"] < 0
        or data["balance_after_cents"] < 0
    ):
        raise ValueError("Posting sequence and balances are outside their valid ranges")
    if data["balance_before_cents"] + data["delta_cents"] != data["balance_after_cents"]:
        raise ValueError("Posting balances are not conserved")
    if type(data["ledger_metadata"]) is not dict:
        raise ValueError("Posting metadata must retain its complete original object")
    version = _origin(data, external=data.get("external_fact_id"))
    if version == 2 and fields != set(POSTING_V2_FIELDS):
        raise ValueError("External postings require their full explicit v2 layout")
    return version


def bank_posting_data(full_live_row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every live column before applying the single registered original layout."""
    if set(full_live_row) != set(POSTING_V2_FIELDS):
        raise ValueError("Live posting must include the explicit external-origin column")
    version = bank_posting_snapshot_version(full_live_row)
    columns = POSTING_V1_FIELDS if version == 1 else POSTING_V2_FIELDS
    return {name: full_live_row[name] for name in columns}


def validate_posting_original(data: Mapping[str, Any], snapshot_version: int) -> None:
    version = bank_posting_snapshot_version(data)
    columns = POSTING_V1_FIELDS if snapshot_version == 1 else POSTING_V2_FIELDS
    if snapshot_version not in (1, 2) or version != snapshot_version or set(data) != set(columns):
        raise ValueError("Posting original fields do not match its frozen snapshot version")


def validate_live_posting(
    original: Mapping[str, Any], snapshot_version: int, full_live_row: Mapping[str, Any]
) -> None:
    validate_posting_original(original, snapshot_version)
    live = bank_posting_data(full_live_row)
    if bank_posting_snapshot_version(live) != snapshot_version or live != dict(original):
        raise ValueError("Original posting content or economic origin has changed")
