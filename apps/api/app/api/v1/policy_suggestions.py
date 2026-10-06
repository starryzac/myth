"""Clients select registered windows or tighten rule limits; no client facts or authority."""

from typing import Annotated, Self

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.pattern_suggestions import (
    ESSENTIAL_CATEGORIES,
    PeriodicParameters,
    SeasonalParameters,
)
from app.domain.policy_configuration import Category, Reference
from app.services.policy_suggestions import (
    PeriodicSuggestions,
    SeasonalSuggestions,
    periodic_suggestions,
    seasonal_suggestions,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter(
    prefix="/api/v1/policy-suggestions",
    tags=["历史规律候选"],
    responses={404: {"model": ErrorEnvelope}},
)


class PeriodicQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_days: Annotated[int, Field(ge=1, le=56)] = 56
    minimum_cycles: Annotated[int, Field(ge=2, le=12)] = 2
    maximum_day_spread: Annotated[int, Field(ge=0, le=2)] = 2
    maximum_cv_bps: Annotated[int, Field(ge=0, le=1000)] = 1000


class SeasonalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_id: Reference
    lookback_days: Annotated[int, Field(ge=1, le=1096)] = 1096
    minimum_historical_windows: Annotated[int, Field(ge=2, le=10)] = 2
    quantile_bps: Annotated[int, Field(ge=8000, le=10000)] = 8000
    essential_categories: Annotated[list[Category], Field(min_length=1, max_length=3)] = Field(
        default_factory=lambda: list(ESSENTIAL_CATEGORIES)
    )
    adjustment_cap_cents: Annotated[int, Field(ge=0, le=500_000)] = 500_000

    def parameters(self) -> SeasonalParameters:
        return SeasonalParameters.model_validate(self.model_dump())

    @model_validator(mode="after")
    def tighten(self) -> Self:
        self.parameters()
        return self


@router.get(
    "/periodic", response_model=PeriodicSuggestions, operation_id="suggest_periodic_policies"
)
def periodic(
    query: Annotated[PeriodicQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> PeriodicSuggestions:
    return periodic_suggestions(
        session, user.id, now, PeriodicParameters.model_validate(query.model_dump())
    )


@router.get(
    "/seasonal", response_model=SeasonalSuggestions, operation_id="suggest_seasonal_reserve"
)
def seasonal(
    query: Annotated[SeasonalQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> SeasonalSuggestions:
    return seasonal_suggestions(session, user.id, now, query.parameters())
