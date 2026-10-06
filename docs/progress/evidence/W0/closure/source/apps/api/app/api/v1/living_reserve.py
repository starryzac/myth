"""Read-only estimation parameters; clients cannot manufacture history coverage."""

from typing import Annotated, Any, Self

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.policy_configuration import Category
from app.services.living_reserve import ReserveEstimationResponse, estimate_living_reserve
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter(
    prefix="/api/v1/living-reserve",
    tags=["生活准备金估算"],
    responses={404: {"model": ErrorEnvelope}},
)


class ReserveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_days: Annotated[int, Field(ge=1, le=366)] = 14
    lookback_days: Annotated[int, Field(ge=1, le=366)] = 56
    quantile: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)] = 0.8
    extra_buffer_cents: Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)] = 50000
    essential_categories: Annotated[list[Category], Field(min_length=1, max_length=48)] = Field(
        default_factory=lambda: ["food", "transport", "daily_necessities"]
    )
    exclude_one_off: bool = True

    @model_validator(mode="after")
    def enough_days(self) -> Self:
        if self.horizon_days > self.lookback_days:
            raise ValueError("horizon_days must not exceed lookback_days")
        return self

    def configuration(self) -> dict[str, Any]:
        return {
            "type": "living_reserve",
            "horizon_days": self.horizon_days,
            "method": {
                "name": "rolling_window_quantile",
                "lookback_days": self.lookback_days,
                "quantile": self.quantile,
                "essential_categories": self.essential_categories,
                "exclude_one_off": self.exclude_one_off,
            },
            "extra_buffer_cents": self.extra_buffer_cents,
            "reconfirm_on_boundary_crossing": True,
        }


@router.get(
    "/estimate", response_model=ReserveEstimationResponse, operation_id="estimate_living_reserve"
)
def estimate(
    query: Annotated[ReserveQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ReserveEstimationResponse:
    return estimate_living_reserve(
        session=session, user_id=user.id, now=now, configuration=query.configuration()
    )
