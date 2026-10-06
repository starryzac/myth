"""Persistent FULL planning declarations; no bank authority or candidate auto-consent."""

from typing import Annotated
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services import full_policy_lifecycle as service
from app.services.full_policy_change_impact import (
    FullPolicyChangeFinancialPreview,
    preview_full_policy_financial_impact,
)
from app.services.full_policy_lifecycle import (
    FullChangePreview,
    FullChangeRequest,
    FullCommandList,
    FullCommandLookup,
    FullCreateRequest,
    FullLifecycleResult,
    FullPolicyList,
    FullPolicyView,
    FullPreviewRequest,
    FullRefreshRequest,
    FullRefreshResult,
    FullResumeRequest,
    FullStateRequest,
    FullVersionList,
)
from fastapi import APIRouter, Depends, HTTPException, Path, Request


def require_no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/full-policies",
    tags=["完整策略声明生命周期"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
    dependencies=[Depends(require_no_query)],
)


@router.get("", response_model=FullPolicyList, operation_id="list_full_policy_declarations")
def list_declarations(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullPolicyList:
    return service.list_full_policies(session, user.id, now)


@router.post(
    "/confirm", response_model=FullLifecycleResult, operation_id="confirm_full_policy_declaration"
)
def confirm_declaration(
    body: FullCreateRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullLifecycleResult:
    return service.confirm_full_policy(session, user.id, body, now)


@router.post(
    "/time-refresh",
    response_model=FullRefreshResult,
    operation_id="refresh_full_policy_declarations",
)
def time_refresh(
    body: FullRefreshRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullRefreshResult:
    return service.refresh_full_policy_time(session, user.id, now)


@router.get(
    "/commands/by-key/{idempotency_key:path}",
    response_model=FullCommandLookup,
    operation_id="lookup_full_policy_command_by_key",
)
def command_by_key(
    idempotency_key: Annotated[str, Path(min_length=1, max_length=160, pattern=r"\S")],
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullCommandLookup:
    return service.lookup_full_policy_command(session, user.id, idempotency_key, now)


@router.get(
    "/{policy_id}", response_model=FullPolicyView, operation_id="read_full_policy_declaration"
)
def read_declaration(
    policy_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullPolicyView:
    return service.read_full_policy(session, user.id, policy_id, now)


@router.get(
    "/{policy_id}/versions",
    response_model=FullVersionList,
    operation_id="list_full_policy_versions",
)
def read_versions(
    policy_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullVersionList:
    return service.list_full_versions(session, user.id, policy_id, now)


@router.get(
    "/{policy_id}/commands",
    response_model=FullCommandList,
    operation_id="list_full_policy_commands",
)
def read_commands(
    policy_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullCommandList:
    return service.list_full_commands(session, user.id, policy_id, now)


@router.post(
    "/{policy_id}/change",
    response_model=FullLifecycleResult,
    operation_id="change_full_policy_declaration",
)
def change_declaration(
    policy_id: UUID,
    body: FullChangeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullLifecycleResult:
    return service.change_full_policy(session, user.id, policy_id, body, now)


@router.post(
    "/{policy_id}/resume",
    response_model=FullLifecycleResult,
    operation_id="resume_full_policy_declaration",
)
def resume_declaration(
    policy_id: UUID,
    body: FullResumeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullLifecycleResult:
    return service.change_full_policy(session, user.id, policy_id, body, now, resume=True)


@router.post(
    "/{policy_id}/suspend",
    response_model=FullLifecycleResult,
    operation_id="suspend_full_policy_declaration",
)
def suspend_declaration(
    policy_id: UUID,
    body: FullStateRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullLifecycleResult:
    return service.stop_full_policy(session, user.id, policy_id, body, now)


@router.post(
    "/{policy_id}/revoke",
    response_model=FullLifecycleResult,
    operation_id="revoke_full_policy_declaration",
)
def revoke_declaration(
    policy_id: UUID,
    body: FullStateRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullLifecycleResult:
    return service.stop_full_policy(session, user.id, policy_id, body, now, revoke=True)


@router.post(
    "/{policy_id}/change-preview",
    response_model=FullChangePreview,
    operation_id="preview_full_policy_declaration_change",
)
def preview_declaration(
    policy_id: UUID,
    body: FullPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullChangePreview:
    return service.preview_full_policy_change(session, user.id, policy_id, body, now)


@router.post(
    "/{policy_id}/financial-change-preview",
    response_model=FullPolicyChangeFinancialPreview,
    operation_id="preview_full_policy_financial_change",
)
def preview_financial_change(
    policy_id: UUID,
    body: FullPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullPolicyChangeFinancialPreview:
    return preview_full_policy_financial_impact(session, user.id, policy_id, body, now)
