"""Explicit multi-template hypothetical financial previews over actual RR/RO sources."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.full_policy_change_multi import SourceKind
from app.services.full_policy_change_multi import (
    MultiTemplatePreviewRequest,
    MultiTemplatePreviewResponse,
    preview_multi_template_financial_change,
)
from fastapi import APIRouter, Depends, HTTPException, Request


def require_no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/policy-financial-previews",
    tags=["多模板金融修改预览"],
    responses={409: {"model": ErrorEnvelope}},
    dependencies=[Depends(require_no_query)],
)


@router.post(
    "/{source_kind}/{policy_id}",
    response_model=MultiTemplatePreviewResponse,
    operation_id="preview_multi_template_policy_financial_change",
)
def preview(
    source_kind: SourceKind,
    policy_id: UUID,
    body: MultiTemplatePreviewRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> MultiTemplatePreviewResponse:
    return preview_multi_template_financial_change(
        session, user.id, source_kind, policy_id, body, now
    )
