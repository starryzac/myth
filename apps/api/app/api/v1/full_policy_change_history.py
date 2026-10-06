"""Explicit read-only future-Dated-history preview; no command or authorization."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.services.full_policy_change_history import (
    FullPolicyHistoryChangeFinancialPreview,
    preview_full_policy_history_financial_impact,
)
from app.services.full_policy_lifecycle import FullPreviewRequest
from fastapi import APIRouter, Depends, HTTPException, Request


def require_no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/full-policies",
    tags=["只读未来支出历史修改预览"],
    responses={409: {"model": ErrorEnvelope}},
    dependencies=[Depends(require_no_query)],
)


@router.post(
    "/{policy_id}/financial-change-preview-history",
    response_model=FullPolicyHistoryChangeFinancialPreview,
    operation_id="preview_full_policy_history_financial_change",
)
def preview(
    policy_id: UUID,
    body: FullPreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> FullPolicyHistoryChangeFinancialPreview:
    return preview_full_policy_history_financial_impact(session, user.id, policy_id, body, now)
