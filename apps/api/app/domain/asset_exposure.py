"""Canonical complete simulated exposure imports; constructing a statement grants no authority."""

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

EXPOSURE_SOURCE = "SIMULATED_ASSET_EXPOSURE"
EXPOSURE_PROTOCOL = "asset-exposure-v1"


class AssetExposure(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    as_of: AwareDatetime
    scope: Literal["general_idle_funds", "goal"]
    goal_id: UUID | None = None
    managed_principal_cents: MoneyCents
    pending_purchase_cents: MoneyCents
    reserved_cash_by_account: dict[UUID, MoneyCents] = Field(default_factory=dict)
    reserved_goal_cash_by_goal: dict[UUID, MoneyCents] = Field(default_factory=dict)
    counted_position_ids: list[UUID] = Field(default_factory=list)
    counted_action_ids: list[UUID] = Field(default_factory=list)
    excluded_manual_position_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)


def _get(row: object, key: str) -> Any:
    return row[key] if isinstance(row, Mapping) else getattr(row, key)


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Exposure timestamps must be aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, UUID | date):
        return str(value)
    if isinstance(value, dict):
        return {key: _json(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json(child) for child in value]
    return value


def _manifest(rows: Iterable[object], fields: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        record = {key: _json(_get(row, key)) for key in fields.split()}
        result.append({"id": str(_get(row, "id")), "digest": configuration_hash(record)})
    result.sort(key=lambda item: item["id"])
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("Exposure identities must be unique")
    return result


def asset_exposure_snapshot(
    user_id: UUID,
    as_of: datetime,
    *,
    accounts: Iterable[object],
    positions: Iterable[object],
    actions: Iterable[object],
    receipts: Iterable[object],
    evidence: Iterable[object],
    settlements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Importer helper accepting ORM objects or mappings, without depending on SQLAlchemy.

    All four row collections must be complete for the user. The adapter independently reloads
    them and verifies exact equality. Settlement declarations are importer assertions, never
    inferred from an action status or a sum of receipt attempts.
    """
    bank_sources = {
        "SIMULATED_BANK_BALANCE",
        "SIMULATED_BANK_POSITION",
        "SIMULATED_BANK_TRANSACTION",
        "SIMULATED_GOAL_OWNERSHIP",
        "SIMULATED_GOAL_MONTH_CONTRIBUTION",
        "SIMULATED_PRINCIPAL_AVAILABILITY",
    }
    return {
        "simulation": True,
        "protocol": EXPOSURE_PROTOCOL,
        "user_id": str(user_id),
        "complete": True,
        "as_of": _json(as_of),
        "accounts": _manifest(accounts, "id user_id account_type balance_cents observed_at"),
        "positions": _manifest(
            positions,
            "id user_id account_id product_id goal_id policy_version_id principal_cents "
            "purchased_at maturity_at available_at status",
        ),
        "actions": _manifest(
            actions,
            "id user_id decision_run_id policy_version_id source_account_id "
            "destination_account_id goal_id product_id position_id action_type amount_cents "
            "status idempotency_key request request_hash authorized_at expires_at created_at",
        ),
        "receipts": _manifest(
            receipts,
            "id user_id action_plan_id attempt_number receipt_ref status executed_cents "
            "fee_cents loss_cents response occurred_at reconciled_at created_at",
        ),
        "evidence": _manifest(
            (row for row in evidence if _get(row, "source_type") in bank_sources),
            "id user_id source_type source_ref evidence_level content_hash status "
            "observed_at valid_from valid_to",
        ),
        "settlements": sorted(
            (_json(item) for item in settlements or []), key=lambda item: item["action_id"]
        ),
    }
