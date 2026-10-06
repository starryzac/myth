"""Production simulated-user candidates, separate from guarded experiment declarations."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

from app.db.models import EvidenceItem, PolicyProposal, PolicyVersion
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.evidence_graph import readonly
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select
from sqlalchemy.orm import Session

PROTOCOL: Literal["user-policy-declaration-v1"] = "user-policy-declaration-v1"
SOURCE_TYPE = "USER_STRUCTURED_POLICY_DECLARATION"
SOURCE_TEXT = "用户明确提交完整结构化候选；仅创建提案，需另行复核确认原配置摘要。"
NAMESPACE = UUID("8c024c3e-e931-4d6b-90ae-0564e94b4a0c")
Key = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")]


class UserDeclarationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: dict[str, Any]
    idempotency_key: Key
    expected_epoch_id: UUID
    source_proposal_id: UUID | None = None


class UserDeclarationRecord(BaseModel):
    simulation: Literal[True] = True
    protocol: Literal["user-policy-declaration-v1"] = PROTOCOL
    grants_authority: Literal[False] = False
    dedicated_audit_event_recorded: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    status: Literal["PROPOSED"] = "PROPOSED"
    current_proposal_status: str
    proposal_id: UUID
    evidence_id: UUID
    epoch_id: UUID
    admitted_at: datetime
    configuration: dict[str, Any]
    configuration_hash: str
    original_request: dict[str, Any]
    request_hash: str


class UserDeclarationLookup(BaseModel):
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    status: Literal["NOT_FOUND", "RECORDED"]
    not_found_is_final: Literal[False] = False
    record: UserDeclarationRecord | None


def identity(user_id: UUID, epoch_id: UUID, key: str) -> tuple[UUID, UUID, str]:
    basis = f"{PROTOCOL}:{user_id}:{epoch_id}:{key}"
    reference = f"{PROTOCOL}:{epoch_id}:" + sha256(basis.encode("utf-8")).hexdigest()
    return uuid5(NAMESPACE, basis + ":proposal"), uuid5(NAMESPACE, basis + ":evidence"), reference


def candidate(body: UserDeclarationRequest) -> tuple[dict[str, Any], str]:
    try:
        configuration = validate_configuration(body.configuration)
    except (TypeError, ValueError, RecursionError) as error:
        raise PolicyLifecycleError(
            "INVALID_CONFIGURATION", "原执行策略候选字段不完整或无效", 422
        ) from error
    return configuration, configuration_hash(body.model_dump(mode="json"))


def verified_original(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    key: str,
    now: datetime,
    proposal: PolicyProposal,
    evidence: EvidenceItem,
) -> UserDeclarationRecord:
    proposal_id, evidence_id, reference = identity(user_id, epoch_id, key)
    try:
        body = UserDeclarationRequest.model_validate(evidence.content["original_request"])
        canonical, request_hash = candidate(body)
    except (KeyError, ValueError, TypeError, PolicyLifecycleError) as error:
        raise PolicyLifecycleError(
            "INVALID_DECLARATION_ORIGINAL", "原声明请求无法核验", 409
        ) from error
    digest = configuration_hash(canonical)
    expected = {
        "protocol": PROTOCOL,
        "simulation": True,
        "user_id": str(user_id),
        "epoch_id": str(epoch_id),
        "admitted_at": evidence.created_at.isoformat(),
        "configuration": canonical,
        "configuration_hash": digest,
        "original_request": body.model_dump(mode="json"),
        "request_hash": request_hash,
        "source_proposal_snapshot": evidence.content.get("source_proposal_snapshot"),
        "grants_authority": False,
    }
    source = expected["source_proposal_snapshot"]
    source_matches = (
        source is None
        if body.source_proposal_id is None
        else (
            isinstance(source, dict)
            and source.get("id") == str(body.source_proposal_id)
            and source.get("user_id") == str(user_id)
        )
    )
    if (
        body.expected_epoch_id != epoch_id
        or body.idempotency_key != key
        or proposal.id != proposal_id
        or evidence.id != evidence_id
        or proposal.user_id != user_id
        or evidence.user_id != user_id
        or proposal.source_type != SOURCE_TYPE
        or proposal.source_text != SOURCE_TEXT
        or proposal.compiler_version != PROTOCOL
        or proposal.idempotency_key != reference
        or proposal.proposed_configuration != canonical
        or proposal.evidence_ids != [str(evidence_id)]
        or evidence.source_type != SOURCE_TYPE
        or evidence.source_ref != reference
        or evidence.evidence_level != "USER_DECLARED"
        or evidence.status != "VALID"
        or evidence.valid_to is not None
        or evidence.supersedes_id is not None
        or proposal.created_at != evidence.created_at
        or evidence.created_at > now
        or evidence.valid_from != evidence.created_at
        or evidence.observed_at != evidence.created_at
        or evidence.content != expected
        or evidence.content_hash != configuration_hash(expected)
        or not source_matches
        or proposal.status not in {"PROPOSED", "CONFIRMED", "REJECTED", "EXPIRED"}
    ):
        raise PolicyLifecycleError(
            "INVALID_DECLARATION_ORIGINAL", "原候选、时钟或声明哈希不匹配", 409
        )
    if proposal.status == "CONFIRMED":
        first = session.scalar(
            select(PolicyVersion).where(
                PolicyVersion.user_id == user_id,
                PolicyVersion.policy_id == proposal.confirmed_policy_id,
                PolicyVersion.version_number == 1,
            )
        )
        if (
            first is None
            or first.configuration != canonical
            or first.content_hash != digest
            or first.confirmed_at is None
            or not evidence.created_at <= first.confirmed_at <= now
            or str(evidence.id) not in first.evidence_ids
            or first.confirmation.get("accepted") is not True
            or first.confirmation.get("user_id") != str(user_id)
            or first.confirmation.get("policy_id") != str(first.policy_id)
            or first.confirmation.get("version_id") != str(first.id)
            or first.confirmation.get("reviewed_hash") != digest
        ):
            raise PolicyLifecycleError("INVALID_DECLARATION_ORIGINAL", "原首次确认版本不匹配", 409)
    elif proposal.confirmed_policy_id is not None:
        raise PolicyLifecycleError("INVALID_DECLARATION_ORIGINAL", "未确认候选不应已有策略", 409)
    return UserDeclarationRecord(
        current_proposal_status=proposal.status,
        proposal_id=proposal.id,
        evidence_id=evidence.id,
        epoch_id=epoch_id,
        admitted_at=evidence.created_at,
        configuration=canonical,
        configuration_hash=digest,
        original_request=body.model_dump(mode="json"),
        request_hash=request_hash,
    )


def declare_user_policy(
    session: Session, user_id: UUID, body: UserDeclarationRequest, now: datetime
) -> UserDeclarationRecord:
    now = _now(now)
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("DIRTY_COMMAND_SESSION", "候选声明不能携带其他待写入行", 409)
    canonical, request_hash = candidate(body)
    _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
        raise PolicyLifecycleError("STALE_DEMO_EPOCH", "声明须绑定当前实际模拟轮次", 409)
    proposal_id, evidence_id, reference = identity(user_id, epoch.id, body.idempotency_key)
    with session.begin_nested():
        proposal = session.get(PolicyProposal, proposal_id)
        evidence = session.get(EvidenceItem, evidence_id)
        if proposal is not None or evidence is not None:
            if proposal is None or evidence is None:
                raise PolicyLifecycleError(
                    "INVALID_DECLARATION_ORIGINAL", "原声明残缺，拒绝修补", 409
                )
            original = verified_original(
                session, user_id, epoch.id, body.idempotency_key, now, proposal, evidence
            )
            if original.request_hash != request_hash:
                raise PolicyLifecycleError(
                    "IDEMPOTENCY_CONFLICT", "同一原键不得更换候选或来源", 409
                )
            return original
        source: dict[str, Any] | None = None
        if body.source_proposal_id is not None:
            previous = session.get(PolicyProposal, body.source_proposal_id)
            if previous is None or previous.user_id != user_id or previous.created_at > now:
                raise PolicyLifecycleError("NOT_FOUND", "原来源提案不存在", 404)
            source = row_copy(previous)
        content = {
            "protocol": PROTOCOL,
            "simulation": True,
            "user_id": str(user_id),
            "epoch_id": str(epoch.id),
            "admitted_at": now.isoformat(),
            "configuration": canonical,
            "configuration_hash": configuration_hash(canonical),
            "original_request": body.model_dump(mode="json"),
            "request_hash": request_hash,
            "source_proposal_snapshot": source,
            "grants_authority": False,
        }
        evidence = EvidenceItem(
            id=evidence_id,
            user_id=user_id,
            created_at=now,
            evidence_level="USER_DECLARED",
            source_type=SOURCE_TYPE,
            source_ref=reference,
            content=content,
            content_hash=configuration_hash(content),
            valid_from=now,
            observed_at=now,
            valid_to=None,
            supersedes_id=None,
            status="VALID",
        )
        proposal = PolicyProposal(
            id=proposal_id,
            user_id=user_id,
            created_at=now,
            source_type=SOURCE_TYPE,
            source_text=SOURCE_TEXT,
            compiler_version=PROTOCOL,
            proposed_configuration=canonical,
            evidence_ids=[str(evidence_id)],
            status="PROPOSED",
            confirmed_policy_id=None,
            idempotency_key=reference,
        )
        session.add_all([evidence, proposal])
        session.flush()
        return verified_original(
            session, user_id, epoch.id, body.idempotency_key, now, proposal, evidence
        )


def lookup_user_declaration(
    session: Session, user_id: UUID, epoch_id: UUID, key: str, now: datetime
) -> UserDeclarationLookup:
    readonly(session)
    now = _now(now)
    proposal_id, evidence_id, _ = identity(user_id, epoch_id, key)
    proposal = session.get(PolicyProposal, proposal_id)
    evidence = session.get(EvidenceItem, evidence_id)
    if proposal is None and evidence is None:
        return UserDeclarationLookup(status="NOT_FOUND", record=None)
    if proposal is None or evidence is None:
        raise PolicyLifecycleError("INVALID_DECLARATION_ORIGINAL", "原声明残缺，拒绝推断结果", 409)
    return UserDeclarationLookup(
        status="RECORDED",
        record=verified_original(session, user_id, epoch_id, key, now, proposal, evidence),
    )
