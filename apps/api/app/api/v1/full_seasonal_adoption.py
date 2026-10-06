"""Finite original seasonal source review; only signed USER may adopt it."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.api.v1.local_actor_sessions import LocalActorDependency
from app.domain.full_seasonal_adoption import (
    Key,
    SeasonalAdoptionConfirmRequest,
    SeasonalAdoptionPreviewRequest,
    SeasonalAdoptionProof,
)
from app.services.full_seasonal_adoption import (
    SeasonalAdoptionLookup,
    SeasonalAdoptionPreview,
    SeasonalAdoptionReceipt,
    confirm_seasonal_adoption,
    preview_seasonal_adoption,
    read_current_seasonal_adoption,
    read_seasonal_adoption_command,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/seasonal-reserve-adoptions",
    tags=["节日准备金显式采纳"],
    dependencies=[Depends(require_no_query)],
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
    },
)
EngineDependency = Annotated[Engine, Depends(get_engine)]


@router.post(
    "/{policy_id}/preview",
    response_model=SeasonalAdoptionPreview,
    operation_id="preview_original_seasonal_adoption",
)
def preview(
    policy_id: UUID,
    body: SeasonalAdoptionPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> SeasonalAdoptionPreview:
    return preview_seasonal_adoption(session, user.id, policy_id, body, now)


@router.post(
    "/{policy_id}/confirm",
    response_model=SeasonalAdoptionReceipt,
    operation_id="confirm_user_seasonal_adoption",
)
def confirm(
    policy_id: UUID,
    body: SeasonalAdoptionConfirmRequest,
    engine: EngineDependency,
    user: DemoUserDependency,
    principal: LocalActorDependency,
    now: ClockDependency,
) -> SeasonalAdoptionReceipt:
    return confirm_seasonal_adoption(engine, user.id, policy_id, body, principal, now)


@router.get(
    "/commands/{epoch_id}/by-key/{key}",
    response_model=SeasonalAdoptionLookup,
    operation_id="read_original_seasonal_adoption_command",
)
def lookup(
    epoch_id: UUID,
    key: Key,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> SeasonalAdoptionLookup:
    return read_seasonal_adoption_command(session, user.id, epoch_id, key, now)


@router.get(
    "/{policy_id}",
    response_model=SeasonalAdoptionProof,
    operation_id="read_current_seasonal_adoption",
)
def current(
    policy_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> SeasonalAdoptionProof:
    return read_current_seasonal_adoption(session, user.id, policy_id, now)
