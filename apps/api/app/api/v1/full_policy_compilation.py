"""Read-only candidate previews. Text and candidates cannot supply authority or facts."""

from typing import Annotated

from app.api.dependencies import ClockDependency, DemoUserDependency
from app.api.errors import ErrorEnvelope
from app.api.v1.full_policies import require_no_query
from app.services.full_policy_compilation import (
    FullCompilationRequest,
    FullCompilationResponse,
    FullCompilerRuntime,
    FullGrammarResponse,
    grammar,
    preview_full_policy_candidate,
)
from fastapi import APIRouter, Depends


def get_full_compiler_runtime() -> FullCompilerRuntime:
    return FullCompilerRuntime()


router = APIRouter(
    prefix="/api/v1/full-policy-compilations",
    tags=["完整自然策略候选"],
    dependencies=[Depends(require_no_query)],
    responses={422: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}},
)


@router.get("/grammar", response_model=FullGrammarResponse, operation_id="full_policy_grammar")
def read_grammar() -> FullGrammarResponse:
    return grammar()


@router.post(
    "/preview",
    response_model=FullCompilationResponse,
    operation_id="preview_full_natural_policy_candidate",
)
def preview(
    body: FullCompilationRequest,
    user: DemoUserDependency,
    now: ClockDependency,
    runtime: Annotated[FullCompilerRuntime, Depends(get_full_compiler_runtime)],
) -> FullCompilationResponse:
    return preview_full_policy_candidate(user, now, body, runtime)
