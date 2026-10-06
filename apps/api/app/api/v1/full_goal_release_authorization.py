"""Dedicated explicit consent, never an implicit permission from planning preview."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_goal_release_authorization import (
    Key,
    ReleaseAuthorizationConfirmation,
    ReleaseAuthorizationPreviewRequest,
)
from app.services.full_goal_release_authorization import (
    ReleaseAuthorizationLookup,
    ReleaseAuthorizationPreview,
    ReleaseAuthorizationResponse,
    confirm_release_authorization,
    preview_release_authorization,
    read_release_authorization,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/goal-release-authorizations",
    tags=["目标现金紧急回拨专用授权"],
    dependencies=[Depends(require_no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.post(
    "/policies/{policy_id}/preview",
    response_model=ReleaseAuthorizationPreview,
    operation_id="preview_goal_release_authorization_scope",
)
def preview(
    policy_id: UUID,
    body: ReleaseAuthorizationPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ReleaseAuthorizationPreview:
    return preview_release_authorization(session, user.id, policy_id, body, now)


@router.post(
    "/policies/{policy_id}/confirm",
    response_model=ReleaseAuthorizationResponse,
    operation_id="confirm_dedicated_goal_release_authorization",
)
def confirm(
    policy_id: UUID,
    body: ReleaseAuthorizationConfirmation,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> ReleaseAuthorizationResponse:
    return confirm_release_authorization(engine, user.id, policy_id, body, now)


@router.get(
    "/commands/{epoch_id}/by-key/{key}",
    response_model=ReleaseAuthorizationLookup,
    operation_id="read_original_goal_release_authorization_by_key",
)
def read(
    epoch_id: UUID,
    key: Key,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> ReleaseAuthorizationLookup:
    return read_release_authorization(session, user.id, epoch_id, key, now)
