"""Server-owned calendar discovery; public input consists only of bounded rule options."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.calendar_periodic_suggestions import CalendarPeriodicParameters
from app.services.calendar_periodic_suggestions import (
    CalendarPeriodicSuggestions,
    calendar_periodic_suggestions,
)
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(
    prefix="/api/v1/policy-suggestions",
    tags=["周期日历候选 v2"],
    responses={409: {"model": ErrorEnvelope}},
)


class CalendarPeriodicQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_days: Annotated[int, Field(ge=1, le=365)] = 365
    minimum_cycles: Annotated[int, Field(ge=3, le=12)] = 3
    maximum_day_spread: Annotated[int, Field(ge=0, le=2)] = 2
    maximum_cv_bps: Annotated[int, Field(ge=0, le=1000)] = 1000


@router.get(
    "/calendar-periodic",
    response_model=CalendarPeriodicSuggestions,
    operation_id="suggest_calendar_periodic_policies",
)
def discover_calendar_periodic(
    query: Annotated[CalendarPeriodicQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> CalendarPeriodicSuggestions:
    return calendar_periodic_suggestions(
        session, user.id, now, CalendarPeriodicParameters.model_validate(query.model_dump())
    )
