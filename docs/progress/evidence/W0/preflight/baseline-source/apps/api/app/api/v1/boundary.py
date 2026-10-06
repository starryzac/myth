"""A server-owned financial snapshot; requests cannot supply money or authority."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.boundary import BoundaryResponse, compute_user_boundary
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1", tags=["自主资金边界"], responses={404: {"model": ErrorEnvelope}}
)


class BoundaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get("/boundary", response_model=BoundaryResponse, operation_id="read_boundary")
def read_boundary(
    query: Annotated[BoundaryQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> BoundaryResponse:
    return compute_user_boundary(session=session, user_id=user.id, now=now)
