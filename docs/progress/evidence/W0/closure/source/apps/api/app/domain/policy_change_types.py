"""Strict server-built hypothetical parameters; validation creates no authority."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.boundary_details_types import BoundaryDisplayDetails
from app.domain.boundary_types import BoundaryModel, BoundaryResult, SourceIssue
from app.domain.policy_configuration import MoneyCents, configuration_hash, validate_configuration
from pydantic import ConfigDict, Field, computed_field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PolicyChangeAssumption(BoundaryModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    schema_version: Literal["policy-change-assumption-v1"] = "policy-change-assumption-v1"
    simulation: Literal[True] = True
    preview_only: Literal[True] = True
    kind: Literal["HYPOTHETICAL_CONFIRMED_CHANGE"] = "HYPOTHETICAL_CONFIRMED_CHANGE"
    user_id: UUID
    policy_id: UUID
    source_version_id: UUID
    configuration: dict[str, Any]
    configuration_hash: Digest
    timezone: Literal["Asia/Shanghai", "UTC"]
    assumed_confirmation_at: datetime
    assumed_valid_from: datetime
    assumed_valid_until: datetime | None = None
    source_status: Literal["ACTIVE", "CONFIRMED", "SUSPENDED"]

    @field_validator("simulation", "preview_only", mode="before")
    @classmethod
    def strictly_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("Hypothetical flags must be the boolean true")
        return value

    @field_validator("configuration", mode="before")
    @classmethod
    def canonical_configuration(cls, value: Any) -> dict[str, Any]:
        return validate_configuration(value)

    @field_validator("assumed_confirmation_at", "assumed_valid_from", "assumed_valid_until")
    @classmethod
    def utc_clocks(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Hypothetical clocks must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def exact_configuration_window(self) -> Self:
        if configuration_hash(self.configuration) != self.configuration_hash:
            raise ValueError("Hypothetical configuration hash does not match")
        zone = UTC if self.timezone == "UTC" else timezone(timedelta(hours=8))
        try:
            start_text = self.configuration.get("valid_from")
            end_text = self.configuration.get("valid_until")
            start = (
                datetime.combine(date.fromisoformat(start_text), time.min, zone).astimezone(UTC)
                if start_text
                else self.assumed_confirmation_at
            )
            end = (
                datetime.combine(
                    date.fromisoformat(end_text) + timedelta(days=1), time.min, zone
                ).astimezone(UTC)
                if end_text
                else None
            )
        except (ValueError, OverflowError) as error:
            raise ValueError("Hypothetical policy window cannot be represented") from error
        if end is not None and start > end:
            raise ValueError("Hypothetical policy window is reversed")
        if start != self.assumed_valid_from or end != self.assumed_valid_until:
            raise ValueError("Hypothetical window differs from the canonical configuration")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_status(self) -> Literal["ACTIVE", "CONFIRMED", "SUSPENDED", "EXPIRED"]:
        # The lifecycle's effective status checks expiration before the suspended column.
        if (
            self.assumed_valid_until is not None
            and self.assumed_confirmation_at >= self.assumed_valid_until
        ):
            return "EXPIRED"
        if self.source_status == "SUSPENDED":
            return "SUSPENDED"
        return "CONFIRMED" if self.assumed_valid_from > self.assumed_confirmation_at else "ACTIVE"


class LivingReserveChangeEstimate(BoundaryModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    status: Literal["READY", "INSUFFICIENT_EVIDENCE"]
    amount_cents: MoneyCents | None
    as_of: datetime
    configuration_hash: Digest
    estimation_input_digest: Digest
    issues: Annotated[list[SourceIssue], Field(max_length=1000)] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def utc_clock(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Hypothetical living estimate must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def proof_shape(self) -> Self:
        if (self.status == "READY") != (self.amount_cents is not None):
            raise ValueError("Only a ready hypothetical living estimate has a precise amount")
        if self.status == "READY" and self.issues:
            raise ValueError("A ready hypothetical living estimate cannot have estimation issues")
        return self


class PolicyChangeBoundaryComputation(BoundaryModel):
    schema_version: Literal["policy-change-boundary-v1"] = "policy-change-boundary-v1"
    simulation: Literal[True] = True
    preview_only: Literal[True] = True
    financial_only: Literal[True] = True
    assumption_digest: Digest
    boundary: BoundaryResult
    details: BoundaryDisplayDetails


def validated_assumption(assumption: PolicyChangeAssumption) -> PolicyChangeAssumption:
    """Recheck mutable nested contents, with no trusted-instance shortcut."""
    return PolicyChangeAssumption.model_validate(
        assumption.model_dump(exclude={"effective_status"})
    )


def calculate_assumption_digest(
    assumption: PolicyChangeAssumption,
    living_estimate: LivingReserveChangeEstimate | None = None,
) -> str:
    assumption = validated_assumption(assumption)
    if living_estimate is not None:
        living_estimate = LivingReserveChangeEstimate.model_validate(living_estimate.model_dump())
        if (
            assumption.configuration["type"] != "living_reserve"
            or living_estimate.configuration_hash != assumption.configuration_hash
            or living_estimate.as_of != assumption.assumed_confirmation_at
        ):
            raise ValueError("Hypothetical living estimate differs from its configuration or clock")
    return configuration_hash(
        {
            "algorithm": "policy-change-boundary-v1",
            "assumption": assumption.model_dump(mode="json"),
            "living_estimate": living_estimate.model_dump(mode="json") if living_estimate else None,
        }
    )
