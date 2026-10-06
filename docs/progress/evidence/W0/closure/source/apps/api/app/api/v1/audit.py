"""Read-only simulated audit browsing and verification; no repair endpoint."""

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from app.api.dependencies import DemoUserDependency, SessionDependency
from app.domain.audit_chain_types import AuditCheckpoint, AuditHead, AuditVerification
from app.services.audit_chain import (
    AuditEventPage,
    get_audit_head,
    list_audit_events,
    verify_audit_chain,
)
from app.services.policy_lifecycle import PolicyLifecycleError
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

router = APIRouter(prefix="/api/v1/audit", tags=["simulated-audit"])


class EventQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch_id: UUID | None = None
    limit: Annotated[int, Query(ge=1, le=100)] = 50
    cursor: str | None = None


class HeadQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch_id: UUID | None = None


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch_id: UUID | None = None
    checkpoint: AuditCheckpoint | None = None
    mode: Literal["PREFIX", "EXACT"] = "PREFIX"

    @field_validator("checkpoint", mode="before")
    @classmethod
    def json_checkpoint(cls, value: Any) -> AuditCheckpoint | None:
        if value is None:
            return None
        if type(value) is not dict:
            raise ValueError("检查点必须是完整JSON对象")
        return AuditCheckpoint.model_validate_json(json.dumps(value, allow_nan=False))


class HeadResponse(BaseModel):
    simulation: Literal[True] = True
    user_id: UUID
    completeness: Literal["COMPLETE", "LEGACY_UNAUDITED"]
    head: AuditHead | None


def _queries(request: Request, fields: set[str]) -> None:
    if any(key not in fields for key in request.query_params):
        raise PolicyLifecycleError("INVALID_QUERY", "审计查询含未支持参数", 422)


def _epoch(session: SessionDependency, user: DemoUserDependency, identity: UUID | None) -> None:
    if identity is not None and get_audit_head(session, user.id, identity) is None:
        raise PolicyLifecycleError("NOT_FOUND", "审计轮次不存在", 404)


@router.get("/events", response_model=AuditEventPage)
def events(
    request: Request,
    session: SessionDependency,
    user: DemoUserDependency,
    query: Annotated[EventQuery, Depends()],
) -> AuditEventPage:
    _queries(request, {"epoch_id", "limit", "cursor"})
    _epoch(session, user, query.epoch_id)
    return list_audit_events(session, user.id, query.epoch_id, query.limit, query.cursor)


@router.get("/head", response_model=HeadResponse)
def head(
    request: Request,
    session: SessionDependency,
    user: DemoUserDependency,
    query: Annotated[HeadQuery, Depends()],
) -> HeadResponse:
    _queries(request, {"epoch_id"})
    _epoch(session, user, query.epoch_id)
    saved = get_audit_head(session, user.id, query.epoch_id)
    return HeadResponse(
        user_id=user.id,
        completeness="LEGACY_UNAUDITED" if saved is None else "COMPLETE",
        head=saved,
    )


@router.post("/verify", response_model=AuditVerification)
def verify(
    request: Request,
    body: VerifyRequest,
    session: SessionDependency,
    user: DemoUserDependency,
) -> AuditVerification:
    _queries(request, set())
    _epoch(session, user, body.epoch_id)
    return verify_audit_chain(session, user.id, body.epoch_id, body.checkpoint, body.mode)
