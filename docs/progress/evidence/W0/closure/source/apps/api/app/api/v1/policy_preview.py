"""Snapshot-only hypothetical changes to a real policy's financial parameters."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.policy_preview_types import (
    PolicyChangePreviewRequest,
    PolicyChangePreviewResponse,
)
from fastapi import APIRouter

router = APIRouter(
    prefix="/api/v1/policies",
    tags=["策略变更预览"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/{policy_id}/change-preview",
    response_model=PolicyChangePreviewResponse,
    operation_id="preview_policy_change",
)
def preview_policy_change(
    policy_id: UUID,
    body: PolicyChangePreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> PolicyChangePreviewResponse:
    from app.services.policy_preview import preview_change

    return preview_change(
        session,
        user.id,
        policy_id,
        body.expected_version_id,
        body.configuration,
        now,
    )
