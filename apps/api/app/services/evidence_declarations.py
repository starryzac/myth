"""Append user declarations and late corrections; never mint bank facts or authority."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.db.models import EvidenceItem
from app.domain.policy_configuration import Reference, configuration_hash
from app.services.audit_chain import current_audit_epoch
from app.services.evidence_graph import FactView
from app.services.policy_lifecycle import PolicyLifecycleError, _user
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session


class DeclarationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["EXPENSE", "GOAL_PREFERENCE", "NOTE"]
    source_ref: Reference
    content: dict[str, Any]
    expected_epoch_id: UUID
    idempotency_key: Reference
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    supersedes_id: UUID | None = None

    @model_validator(mode="after")
    def bounded_content(self) -> Self:
        configuration_hash(self.content)
        if len(json.dumps(self.content, ensure_ascii=False).encode("utf-8")) > 32768:
            raise ValueError("User declaration exceeds 32 KiB")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("The declared valid interval must be nonempty")
        return self


class DeclarationResponse(BaseModel):
    simulation: Literal[True] = True
    evidence: FactView
    request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    observed_at_from_server: Literal[True] = True
    grants_authority: Literal[False] = False
    bank_verified: Literal[False] = False
    declaration_audit_event_recorded: Literal[False] = False


def provenance(row: EvidenceItem, epoch_id: UUID) -> dict[str, Any]:
    return {
        "protocol": "declared-fact-v1",
        "user_id": str(row.user_id),
        "epoch_id": str(epoch_id),
        "source_type": row.source_type,
        "source_ref": row.source_ref,
        "observed_at": row.observed_at.isoformat(),
        "valid_from": row.valid_from.isoformat(),
        "valid_to": row.valid_to.isoformat() if row.valid_to else None,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
    }


def append_declaration(
    session: Session, user_id: UUID, request: DeclarationRequest, now: datetime
) -> DeclarationResponse:
    request = DeclarationRequest.model_validate_json(request.model_dump_json())
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    now = now.astimezone(UTC)
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != request.expected_epoch_id:
        raise PolicyLifecycleError("STALE_EVIDENCE_EPOCH", "声明的原模拟期已失效", 409)
    source_type = "USER_DECLARED_" + request.kind
    intent_hash = configuration_hash(request.model_dump(mode="json"))
    identity = uuid5(
        epoch.id,
        "declaration:"
        + configuration_hash({"user_id": str(user_id), "key": request.idempotency_key}),
    )
    existing = session.scalar(
        select(EvidenceItem).where(EvidenceItem.id == identity, EvidenceItem.user_id == user_id)
    )
    if existing is not None:
        if existing.source_type != source_type or existing.source_ref != request.source_ref:
            raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "声明原键不能修改来源", 409)
        expected = request.model_dump(mode="json")
        stored = existing.content
        if (
            stored.get("declaration_request") != expected
            or stored.get("provenance") != provenance(existing, epoch.id)
            or existing.evidence_level != "USER_DECLARED"
            or existing.observed_at > now
            or configuration_hash(stored) != existing.content_hash
        ):
            raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "声明原键不能改变原内容", 409)
        return DeclarationResponse(
            evidence=FactView.model_validate(existing), request_hash=intent_hash
        )
    valid_from = request.valid_from.astimezone(UTC) if request.valid_from else now
    valid_to = request.valid_to.astimezone(UTC) if request.valid_to else None
    if valid_to is not None and valid_to <= valid_from:
        raise PolicyLifecycleError("INVALID_FACT_WINDOW", "声明有效区间必须非空")
    if request.supersedes_id is not None:
        predecessor = session.get(EvidenceItem, request.supersedes_id)
        if predecessor is None or predecessor.user_id != user_id:
            raise PolicyLifecycleError("NOT_FOUND", "原声明不存在", 404)
        if (
            predecessor.evidence_level != "USER_DECLARED"
            or predecessor.source_type != source_type
            or predecessor.source_ref != request.source_ref
            or predecessor.observed_at > now
            or configuration_hash(predecessor.content) != predecessor.content_hash
        ):
            raise PolicyLifecycleError("INVALID_SUPERSESSION", "不能更正非同来源原声明", 409)
        # Do not alter the predecessor's status, content, observed clock or hash.
    evidence = EvidenceItem(
        id=identity,
        user_id=user_id,
        created_at=now,
        evidence_level="USER_DECLARED",
        source_type=source_type,
        source_ref=request.source_ref,
        content={},
        content_hash="0" * 64,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=now,
        supersedes_id=request.supersedes_id,
        status="VALID",
    )
    evidence.content = {
        "declaration_request": request.model_dump(mode="json"),
        "value": request.content,
        "provenance": provenance(evidence, epoch.id),
    }
    evidence.content_hash = configuration_hash(evidence.content)
    session.add(evidence)
    session.flush()
    return DeclarationResponse(evidence=FactView.model_validate(evidence), request_hash=intent_hash)
