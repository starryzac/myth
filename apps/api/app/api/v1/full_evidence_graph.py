"""Expanded persisted evidence navigation, independent from the original seven roots."""

from datetime import datetime
from uuid import UUID

from app.api.dependencies import ClockDependency, DemoUserDependency, SessionDependency
from app.domain.full_evidence_graph import FullEvidenceGraph, GraphRootKind
from app.services.full_evidence_graph import full_evidence_graph
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/v1/evidence/full-graph", tags=["完整版原件证据图"])


@router.get("/{kind}/{identity}", response_model=FullEvidenceGraph)
def get_full_evidence_graph(
    kind: GraphRootKind,
    identity: UUID,
    session: SessionDependency,
    user: DemoUserDependency,
    now: ClockDependency,
    request: Request,
    known_at: datetime | None = None,
) -> FullEvidenceGraph:
    if (
        set(request.query_params) - {"known_at"}
        or len(request.query_params.getlist("known_at")) > 1
    ):
        raise HTTPException(422, "仅支持一次明确的known_at；不接受actor、资金或成功标签")
    return full_evidence_graph(session, user.id, kind, identity, known_at or now, now)
