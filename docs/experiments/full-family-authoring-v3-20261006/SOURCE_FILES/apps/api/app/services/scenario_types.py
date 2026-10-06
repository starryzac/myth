"""Versioned simulation inputs shared by demo, experiments, and evidence tools."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.services.scenario_references import resolve_inputs
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

PROTOCOL: Literal["bounded-funds-scenario-v1"] = "bounded-funds-scenario-v1"
Purpose = Literal["DEVELOPMENT", "MVP_FROZEN", "FULL_FAMILY_FROZEN"]
PropertyName = Literal[
    "BANK_LEDGER_VALID", "AUDIT_VALID", "NO_LOSS_AUTOMATIC", "DEMO_ROUND_COMPLETED"
]


class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InitialState(ScenarioModel):
    seed_version: Literal["mvp-301-v6"] = "mvp-301-v6"
    mode: Literal["EXISTING", "SEED_NEW"] = "EXISTING"
    expected_epoch_id: UUID | None = None


class ExpectedScenarioError(ScenarioModel):
    code: Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")]
    status_code: Annotated[int, Field(strict=True, ge=400, le=599)]


class ScenarioStep(ScenarioModel):
    step_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    kind: Literal[
        "DEMO_EVENT",
        "PREPARE_TEMPLATE",
        "CONFIRM_TEMPLATE",
        "EXTERNAL_FACT",
        "PREPARE_ACTION",
        "CONFIRM_ACTION",
        "EXECUTE_ACTION",
        "OBSERVE_RECURRING_PAYMENT",
        "LOOKUP_ACCOUNT",
        "READ_ACTION",
        "READ_POLICY",
        "SNAPSHOT",
        "CHANGE_POLICY",
        "SUSPEND_POLICY",
        "REVOKE_POLICY",
        "CREATE_GOAL",
        "RUN_RECOVERY",
        "READ_RECOVERY",
        "ISSUE_EARLY_QUOTE",
        "DECLARE_POLICY",
        "CONFIRM_POLICY",
    ]
    at: datetime
    inputs: dict[str, Any]
    fault: Literal["NONE", "DROP_BANK_RESPONSE", "FAIL_APPLICATION_PROJECTION"] = "NONE"
    expected_error: ExpectedScenarioError | None = None

    @model_validator(mode="after")
    def implemented_fault(self) -> Self:
        admitted = (
            self.fault == "NONE"
            or (self.fault == "DROP_BANK_RESPONSE" and self.kind == "EXECUTE_ACTION")
            or (
                self.fault == "FAIL_APPLICATION_PROJECTION"
                and self.kind in {"EXECUTE_ACTION", "EXTERNAL_FACT", "RUN_RECOVERY"}
            )
        )
        if not admitted:
            raise ValueError("This fault-kind pair has no original service injection")
        return self

    @field_validator("at")
    @classmethod
    def trusted_clock(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("The simulation clock must be timezone aware")
        return value


class Scenario(ScenarioModel):
    protocol: Literal["bounded-funds-scenario-v1"] = PROTOCOL
    scenario_id: Annotated[str, Field(min_length=1, max_length=160)]
    purpose: Purpose
    dataset_id: Annotated[str, Field(min_length=1, max_length=160)]
    family_id: Annotated[str, Field(min_length=1, max_length=160)]
    frozen_case_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    initial_state: InitialState
    steps: Annotated[list[ScenarioStep], Field(max_length=1000)]
    expected_properties: list[PropertyName] = Field(default_factory=list)

    @model_validator(mode="after")
    def purpose_and_order(self) -> Self:
        if (self.purpose == "DEVELOPMENT") != (self.frozen_case_sha256 is None):
            raise ValueError(
                "Frozen data requires its digest; development data cannot claim frozen status"
            )
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("Step identities must be unique")
        if any(right.at < left.at for left, right in zip(self.steps, self.steps[1:], strict=False)):
            raise ValueError("A scenario clock cannot move backwards")
        if len(set(self.expected_properties)) != len(self.expected_properties):
            raise ValueError("Property identities must be unique")
        previous: dict[str, dict[str, Any]] = {}
        for step in self.steps:
            resolve_inputs(step.inputs, previous, validate_only=True)
            previous[step.step_id] = {}
        return self


class PropertyResult(ScenarioModel):
    name: PropertyName
    passed: StrictBool
    scope: str
    detail: dict[str, Any]


class ScenarioResult(ScenarioModel):
    protocol: Literal["bounded-funds-scenario-v1"] = PROTOCOL
    scenario_id: str
    purpose: Purpose
    dataset_id: str
    family_id: str
    input_sha256: str
    status: Literal["EXECUTED", "FAILED", "PROPERTY_FAILED"]
    steps: list[dict[str, Any]]
    properties: list[PropertyResult]


class ScenarioRPC(ScenarioModel):
    protocol: Literal["bounded-funds-scenario-v1"] = PROTOCOL
    scenario_id: Annotated[str, Field(min_length=1, max_length=160)]
    purpose: Literal["DEVELOPMENT"]
    operation: Literal[
        "snapshot",
        "prepare_rent_old_action",
        "verify_round",
        "verify_legacy_recovery",
        "ingest_goal_income",
    ]
    expected_epoch_id: UUID
    policy_id: UUID | None = None
    action_id: UUID | None = None
    goal_id: UUID | None = None

    @model_validator(mode="after")
    def original_identity(self) -> Self:
        if (self.operation == "prepare_rent_old_action") != (self.policy_id is not None):
            raise ValueError("Only the original rent preparation accepts a policy identity")
        if (self.operation == "verify_legacy_recovery") != (self.action_id is not None):
            raise ValueError(
                "Only original legacy recovery verification accepts an action identity"
            )
        if (self.operation == "ingest_goal_income") != (self.goal_id is not None):
            raise ValueError("Only fixed synthetic goal income accepts an original goal identity")
        return self
