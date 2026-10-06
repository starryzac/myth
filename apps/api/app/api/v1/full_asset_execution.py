"""Strict whole portfolio preview; no client amount, facts, permission or clock."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency, get_engine
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.domain.full_asset_execution import (
    FullAssetConfirmRequest,
    FullAssetExecuteRequest,
    FullAssetPrepareRequest,
    Key,
)
from app.services.full_asset_execution import (
    FullAssetExecutionPreview,
    preview_full_asset_execution,
)
from app.services.full_asset_execution_dispatch import (
    confirm_full_asset_execution,
    execute_full_asset_execution,
    prepare_full_asset_execution,
)
from app.services.full_asset_execution_store import (
    FullAssetExecutionLookup,
    FullAssetExecutionResponse,
    read_full_asset_execution,
    read_full_asset_execution_by_key,
)
from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

router = APIRouter(
    prefix="/api/v1/full-asset-executions",
    tags=["完整资产组合执行"],
    dependencies=[Depends(require_no_query)],
    responses={409: {"model": ErrorEnvelope}},
)


@router.post("/preview", response_model=FullAssetExecutionPreview)
def preview(
    body: FullAssetPrepareRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAssetExecutionPreview:
    return preview_full_asset_execution(session, user.id, body, now)


@router.post(
    "/prepare",
    response_model=FullAssetExecutionResponse,
    operation_id="prepare_original_full_asset_portfolio",
)
def prepare(
    body: FullAssetPrepareRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAssetExecutionResponse:
    return prepare_full_asset_execution(engine, user.id, body, now)


@router.get(
    "/portfolios/{portfolio_id}",
    response_model=FullAssetExecutionResponse,
    operation_id="read_original_full_asset_portfolio",
)
def read(
    portfolio_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullAssetExecutionResponse:
    return read_full_asset_execution(session, user.id, portfolio_id, now)


@router.get(
    "/commands/{epoch_id}/by-key/{key}",
    response_model=FullAssetExecutionLookup,
    operation_id="lookup_original_full_asset_portfolio",
)
def by_key(
    epoch_id: UUID,
    key: Key,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAssetExecutionLookup:
    return read_full_asset_execution_by_key(session, user.id, epoch_id, key, now)


@router.post(
    "/portfolios/{portfolio_id}/confirm",
    response_model=FullAssetExecutionResponse,
    operation_id="confirm_original_whole_asset_portfolio",
)
def confirm(
    portfolio_id: UUID,
    body: FullAssetConfirmRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAssetExecutionResponse:
    return confirm_full_asset_execution(engine, user.id, portfolio_id, body, now)


@router.post(
    "/portfolios/{portfolio_id}/execute-next",
    response_model=FullAssetExecutionResponse,
    operation_id="execute_next_original_asset_portfolio_batch",
)
def execute_next(
    portfolio_id: UUID,
    body: FullAssetExecuteRequest,
    engine: Annotated[Engine, Depends(get_engine)],
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullAssetExecutionResponse:
    return execute_full_asset_execution(engine, user.id, portfolio_id, body, now)
