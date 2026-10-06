"""Explicit counterfactual financial comparison; never an execution authorization."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.services.dashboard_types import FinancialBoundaryCard
from pydantic import BaseModel, ConfigDict


class PolicyChangePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version_id: UUID
    configuration: dict[str, Any]


class PolicyChangePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["policy-change-preview-v1"] = "policy-change-preview-v1"
    simulation: Literal[True] = True
    preview_only: Literal[True] = True
    financial_only: Literal[True] = True
    user_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    policy_id: UUID
    expected_version_id: UUID
    configuration: dict[str, Any]
    configuration_hash: str
    assumed_status: Literal["ACTIVE", "CONFIRMED", "SUSPENDED", "EXPIRED"]
    assumed_valid_from: datetime
    assumed_valid_until: datetime | None
    assumption_digest: str
    current_fact_input_digest: str
    hypothetical_input_digest: str
    before: FinancialBoundaryCard
    after: FinancialBoundaryCard
    delta_safe_idle_cents: int | None
    delta_minimum_margin_cents: int | None
    notes: list[str]
