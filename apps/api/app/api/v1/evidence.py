"""Bitemporal source facts and actual persisted provenance, read-only."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.services.evidence_declarations import (
    DeclarationRequest,
    DeclarationResponse,
    append_declaration,
)
from app.services.evidence_graph import Kind, evidence_graph, facts_at
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/evidence", tags=["事实与证据关联"])


@router.post("/declarations", response_model=DeclarationResponse)
def declare_fact(
    body: DeclarationRequest,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
) -> DeclarationResponse:
    return append_declaration(session, user.id, body, now)


@router.get("/facts")
def get_facts(
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    valid_at: datetime | None = None,
    known_at: datetime | None = None,
    source_type: Annotated[str | None, Query(min_length=1, max_length=48)] = None,
    source_ref: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
) -> dict[str, Any]:
    return facts_at(
        session, user.id, valid_at or now, known_at or now, now, source_type, source_ref
    )


@router.get("/graph/{kind}/{identity}")
def get_graph(
    kind: Kind,
    identity: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    known_at: datetime | None = None,
) -> dict[str, Any]:
    return evidence_graph(session, user.id, kind, identity, known_at or now, now)
