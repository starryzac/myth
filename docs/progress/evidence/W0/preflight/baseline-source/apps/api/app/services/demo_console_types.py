"""Fixed synthetic demonstration inputs; no browser supplied financial facts."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.domain.external_bank_fact_types import ExternalFactResult
from app.services.action_contracts import ActionResponse
from app.services.recovery import RecoveryRunResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

PRESET_VERSION: Literal["demo-console-v1"] = "demo-console-v1"
TemplateKind = Literal["CAR_GOAL", "LIQUID_ASSET", "FIXED_ASSET", "RENT"]
EventKind = Literal[
    "SALARY_RECEIVED",
    "CREATE_CAR_GOAL",
    "LARGE_CONSUMPTION",
    "AUTO_REDEEM",
    "FIXED_EARLY_WITHDRAWAL",
    "CHANGE_RENT",
]


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DemoEventRequest(DemoModel):
    event_kind: EventKind
    expected_epoch_id: UUID


class DemoTemplateRequest(DemoModel):
    expected_epoch_id: UUID


class DemoResetRequest(DemoModel):
    reset_key: Annotated[str, Field(min_length=1, max_length=160)]
    expected_epoch_id: UUID | None
    accepted: StrictBool

    @field_validator("accepted")
    @classmethod
    def explicit_acceptance(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit reset acceptance is required")
        return value

    @field_validator("reset_key")
    @classmethod
    def nonblank_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank reset key is required")
        return value


class DemoTemplateView(DemoModel):
    simulation: Literal[True] = True
    kind: TemplateKind
    title: str
    configuration: dict[str, Any]
    configuration_hash: str
    proposal_id: UUID | None = None
    evidence_id: UUID | None = None
    confirmed_policy_id: UUID | None = None
    status: str = "NOT_PREPARED"


class DemoEventPreset(DemoModel):
    event_kind: EventKind | Literal["RESET"]
    title: str
    description: str
    required_templates: list[TemplateKind] = Field(default_factory=list)
    amount_cents: int | None = None


class DemoPresets(DemoModel):
    simulation: Literal[True] = True
    preset_version: Literal["demo-console-v1"] = PRESET_VERSION
    templates: list[DemoTemplateView]
    events: list[DemoEventPreset]


class DemoPolicyChange(DemoModel):
    policy_id: UUID
    expected_version_id: UUID
    configuration: dict[str, Any]
    reviewed_hash: str
    reason: str
    idempotency_key: str


class DemoCommandView(DemoModel):
    simulation: Literal[True] = True
    preset_version: Literal["demo-console-v1"] = PRESET_VERSION
    command_id: UUID
    epoch_id: UUID
    event_kind: EventKind
    admitted_at: datetime
    status: Literal[
        "WAITING_TEMPLATE",
        "WAITING_ACTION_CONFIRMATION",
        "WAITING_POLICY_CHANGE",
        "UNKNOWN",
        "COMPLETED",
        "BLOCKED",
        "PENDING",
    ]
    message: str
    proposal_ids: list[UUID] = Field(default_factory=list)
    goal_id: UUID | None = None
    fact: ExternalFactResult | None = None
    actions: list[ActionResponse] = Field(default_factory=list)
    recovery: RecoveryRunResponse | None = None
    policy_change: DemoPolicyChange | None = None


class DemoState(DemoModel):
    simulation: Literal[True] = True
    preset_version: Literal["demo-console-v1"] = PRESET_VERSION
    epoch_id: UUID | None
    available: bool
    reason: str | None = None
    templates: list[DemoTemplateView]
    commands: list[DemoCommandView]


class DemoResetResponse(DemoModel):
    simulation: Literal[True] = True
    reset_key: str
    reset_epoch_id: UUID
    epoch_id: UUID
    seed_summary: dict[str, Any]
