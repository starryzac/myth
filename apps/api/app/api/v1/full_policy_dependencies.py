"""Current declaration review only; no client actor, clock, amount or mutation."""

from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.domain.full_policy_dependencies import FullPolicyDependencyReview
from app.services.full_policy_dependencies import read_full_policy_dependencies
from fastapi import APIRouter, Depends, HTTPException, Request


def no_query(request: Request) -> None:
    if request.query_params:
        raise HTTPException(status_code=422)


router = APIRouter(
    prefix="/api/v1/full-policy-dependencies",
    tags=["当前完整策略依赖复核"],
    dependencies=[Depends(no_query)],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


@router.get(
    "/{policy_id}",
    response_model=FullPolicyDependencyReview,
    operation_id="read_current_full_policy_dependencies",
)
def read_dependencies(
    policy_id: UUID, session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> FullPolicyDependencyReview:
    return read_full_policy_dependencies(session, user.id, policy_id, now)
