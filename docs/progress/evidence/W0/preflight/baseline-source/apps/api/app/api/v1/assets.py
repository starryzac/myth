"""Read-only previews; monetary facts and time always come from the server snapshot."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.asset_allocation import AssetAllocationResponse, preview_asset_allocation
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

router = APIRouter(
    prefix="/api/v1/asset-policies",
    tags=["资产配置"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class AssetPreviewQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.get(
    "/{policy_id}/allocation-preview",
    response_model=AssetAllocationResponse,
    operation_id="preview_asset_allocation",
)
def read_asset_allocation(
    policy_id: UUID,
    query: Annotated[AssetPreviewQuery, Query()],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> AssetAllocationResponse:
    return preview_asset_allocation(session, user.id, policy_id, now)
