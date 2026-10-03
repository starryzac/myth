"""Explicit confirmation and versioned lifecycle for the synthetic MVP user."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.api.errors import ErrorEnvelope
from app.db.models import Policy, PolicyProposal, PolicyVersion
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.policy_lifecycle import (
    LifecycleResult,
    change_policy,
    confirm_proposal,
    effective_status,
    is_version_authorized,
    revoke_policy,
    suspend_policy,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/v1",
    tags=["策略生命周期"],
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmationRequest(RequestModel):
    accepted: StrictBool
    reviewed_hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]

    @field_validator("accepted")
    @classmethod
    def explicit_acceptance(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit acceptance is required")
        return value


class PolicyChangeRequest(ConfirmationRequest):
    expected_version_id: UUID
    configuration: dict[str, Any]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=160)]


class StateChangeRequest(RequestModel):
    expected_version_id: UUID


class PolicyVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    policy_id: UUID
    version_number: int
    configuration: dict[str, Any]
    summary: str
    confirmation: dict[str, Any]
    confirmed_at: datetime | None
    valid_from: datetime | None
    valid_until: datetime | None
    change_reason: str
    evidence_ids: list[str]
    content_hash: str
    previous_hash: str | None
    impact_analysis: dict[str, Any]
    created_at: datetime


class PolicyView(BaseModel):
    id: UUID
    name: str
    policy_type: str
    status: str
    effective_status: str
    version_authorized: bool = Field(
        description="当前确认版本、生命周期和证据是否有效；不代表已满足资金或具体动作约束。"
    )
    current_version: PolicyVersionView | None
    updated_at: datetime


class PolicyList(BaseModel):
    simulation: Literal[True] = True
    items: list[PolicyView]


class PolicyVersionList(BaseModel):
    simulation: Literal[True] = True
    items: list[PolicyVersionView]


class ProposalView(BaseModel):
    id: UUID
    status: str
    source_type: str
    source_text: str
    compiler_version: str
    evidence_ids: list[str]
    configuration: dict[str, Any]
    configuration_hash: str
    validation_ready: bool
    confirmed_policy_id: UUID | None


class ProposalList(BaseModel):
    simulation: Literal[True] = True
    items: list[ProposalView]


def current_version(session: Session, policy_id: UUID, user_id: UUID) -> PolicyVersion | None:
    return session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.policy_id == policy_id, PolicyVersion.user_id == user_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )


@router.get("/policy-proposals", response_model=ProposalList, operation_id="list_policy_proposals")
def list_policy_proposals(session: SessionDependency, user: DemoUserDependency) -> ProposalList:
    proposals = session.scalars(
        select(PolicyProposal)
        .where(PolicyProposal.user_id == user.id)
        .order_by(PolicyProposal.created_at, PolicyProposal.id)
    )
    items = []
    for proposal in proposals:
        ready = True
        try:
            configuration = validate_configuration(proposal.proposed_configuration)
        except ValueError:
            configuration = proposal.proposed_configuration
            ready = False
        items.append(
            ProposalView(
                id=proposal.id,
                status=proposal.status,
                source_type=proposal.source_type,
                source_text=proposal.source_text,
                compiler_version=proposal.compiler_version,
                evidence_ids=proposal.evidence_ids,
                configuration=configuration,
                configuration_hash=configuration_hash(configuration),
                validation_ready=ready,
                confirmed_policy_id=proposal.confirmed_policy_id,
            )
        )
    return ProposalList(items=items)


@router.post(
    "/policy-proposals/{proposal_id}/confirm",
    response_model=LifecycleResult,
    operation_id="confirm_policy_proposal",
)
def confirm_policy_proposal(
    proposal_id: UUID,
    body: ConfirmationRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> LifecycleResult:
    return confirm_proposal(
        session=session,
        user_id=user.id,
        proposal_id=proposal_id,
        reviewed_hash=body.reviewed_hash,
        accepted=body.accepted,
        now=now,
    )


@router.get("/policies", response_model=PolicyList, operation_id="list_policies")
def list_policies(
    session: SessionDependency, user: DemoUserDependency, now: ClockDependency
) -> PolicyList:
    records = session.scalars(
        select(Policy).where(Policy.user_id == user.id).order_by(Policy.created_at, Policy.id)
    )
    items = []
    for policy in records:
        version = current_version(session, policy.id, user.id)
        items.append(
            PolicyView(
                id=policy.id,
                name=policy.name,
                policy_type=policy.policy_type,
                status=policy.status,
                effective_status=effective_status(policy, version, now),
                version_authorized=(
                    is_version_authorized(session, user.id, version.id, now) if version else False
                ),
                current_version=PolicyVersionView.model_validate(version) if version else None,
                updated_at=policy.updated_at,
            )
        )
    return PolicyList(items=items)


@router.get(
    "/policies/{policy_id}/versions",
    response_model=PolicyVersionList,
    operation_id="list_policy_versions",
)
def list_policy_versions(
    policy_id: UUID, session: SessionDependency, user: DemoUserDependency
) -> PolicyVersionList:
    policy = session.get(Policy, policy_id)
    if policy is None or policy.user_id != user.id:
        raise HTTPException(status_code=404)
    records = session.scalars(
        select(PolicyVersion)
        .where(PolicyVersion.policy_id == policy.id, PolicyVersion.user_id == user.id)
        .order_by(PolicyVersion.version_number)
    )
    return PolicyVersionList(items=[PolicyVersionView.model_validate(row) for row in records])


@router.patch("/policies/{policy_id}", response_model=LifecycleResult, operation_id="change_policy")
def update_policy(
    policy_id: UUID,
    body: PolicyChangeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> LifecycleResult:
    return change_policy(
        session=session,
        user_id=user.id,
        policy_id=policy_id,
        expected_version_id=body.expected_version_id,
        configuration=body.configuration,
        reviewed_hash=body.reviewed_hash,
        accepted=body.accepted,
        reason=body.reason,
        idempotency_key=body.idempotency_key,
        now=now,
    )


@router.post(
    "/policies/{policy_id}/suspend", response_model=LifecycleResult, operation_id="suspend_policy"
)
def suspend(
    policy_id: UUID,
    body: StateChangeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> LifecycleResult:
    return suspend_policy(
        session=session,
        user_id=user.id,
        policy_id=policy_id,
        expected_version_id=body.expected_version_id,
        now=now,
    )


@router.post(
    "/policies/{policy_id}/revoke", response_model=LifecycleResult, operation_id="revoke_policy"
)
def revoke(
    policy_id: UUID,
    body: StateChangeRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> LifecycleResult:
    return revoke_policy(
        session=session,
        user_id=user.id,
        policy_id=policy_id,
        expected_version_id=body.expected_version_id,
        now=now,
    )
