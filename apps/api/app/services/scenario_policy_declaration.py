"""Isolated structured user declaration; creates no authority or money result.

The caller must subsequently use the original explicit confirm_proposal with the
returned original reviewed hash. Historical rows are verified, never repaired.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.models import AuditEpoch, EvidenceItem, Policy, PolicyProposal, PolicyVersion, User
from app.db.testing import require_test_database
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

PROTOCOL: Literal["scenario-user-declaration-v1"] = "scenario-user-declaration-v1"
SOURCE_TYPE = "SCENARIO_USER_DECLARATION"
SOURCE_TEXT = "用户声明完整结构化策略；仅产生候选，完整配置须另行明确确认。"


class DeclarationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    simulation: Literal[True] = True
    protocol: Literal["scenario-user-declaration-v1"] = PROTOCOL
    proposal_id: UUID
    evidence_id: UUID
    user_id: UUID
    epoch_id: UUID
    configuration: dict[str, Any]
    configuration_hash: str
    status: Literal["PROPOSED", "CONFIRMED", "REJECTED", "EXPIRED"]
    admitted_at: datetime
    grants_authority: Literal[False] = False


def _error(code: str, message: str, status: int = 409) -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, status)


def canonical_inputs(
    configuration: dict[str, Any], idempotency_key: str, now: datetime
) -> tuple[dict[str, Any], str, datetime]:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise _error("INVALID_CLOCK", "声明必须使用带时区的服务器模拟时刻", 422)
    if (
        not isinstance(idempotency_key, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", idempotency_key) is None
    ):
        raise _error("INVALID_DECLARATION_KEY", "声明幂等键必须为1至160个明确ASCII标识字符", 422)
    try:
        canonical = validate_configuration(configuration)
        digest = configuration_hash(canonical)
    except (ValueError, TypeError, RecursionError) as error:
        raise _error("INVALID_CONFIGURATION", "声明必须通过完整原策略配置验证", 422) from error
    return canonical, digest, now.astimezone(UTC)


def declaration_identity(user_id: UUID, epoch_id: UUID, key: str) -> tuple[UUID, UUID, str]:
    if type(user_id) is not UUID or type(epoch_id) is not UUID:
        raise _error("INVALID_DECLARATION_IDENTITY", "原用户和原epoch必须是明确UUID", 422)
    binding = f"{PROTOCOL}:{user_id}:{key}"
    proposal_id = uuid5(epoch_id, binding + ":proposal")
    evidence_id = uuid5(epoch_id, binding + ":evidence")
    key_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
    source_ref = f"{PROTOCOL}:{epoch_id}:{key_hash}"
    return proposal_id, evidence_id, source_ref


def require_isolated(engine: Engine) -> None:
    # Guard precedes audit_command_guard's connection creation. No formal/remote DSN.
    try:
        require_test_database(engine.url.database)
    except ValueError as error:
        raise _error("INVALID_SCENARIO_INPUT", "仅允许生成的bf_test隔离模拟库") from error
    if (
        engine.url.get_backend_name() != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.port != 54329
    ):
        raise _error("INVALID_SCENARIO_INPUT", "仅允许真实127.0.0.1:54329模拟隔离端点")


def _original(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    key: str,
    canonical: dict[str, Any],
    digest: str,
    now: datetime,
    proposal: PolicyProposal,
    evidence: EvidenceItem,
    source_ref: str,
    proposal_id: UUID,
    evidence_id: UUID,
) -> DeclarationResult:
    try:
        stored_canonical = validate_configuration(proposal.proposed_configuration)
    except (ValueError, TypeError) as error:
        raise _error(
            "DECLARATION_ORIGINAL_CONFLICT", "原提案配置不再通过完整原strict schema"
        ) from error
    if (
        proposal.id != proposal_id
        or proposal.user_id != user_id
        or proposal.source_type != SOURCE_TYPE
        or proposal.source_text != SOURCE_TEXT
        or proposal.compiler_version != PROTOCOL
        or proposal.idempotency_key != source_ref + ":proposal"
        or proposal.proposed_configuration != stored_canonical
        or stored_canonical != canonical
        or proposal.evidence_ids != [str(evidence_id)]
        or proposal.created_at != evidence.created_at
        or proposal.created_at > now
        or evidence.id != evidence_id
        or evidence.user_id != user_id
        or evidence.evidence_level != "USER_DECLARED"
        or evidence.source_type != SOURCE_TYPE
        or evidence.source_ref != source_ref
        or evidence.status != "VALID"
        or evidence.valid_to is not None
        or evidence.supersedes_id is not None
        or evidence.observed_at != evidence.created_at
        or evidence.valid_from != evidence.created_at
        or evidence.created_at > now
    ):
        raise _error(
            "DECLARATION_ORIGINAL_CONFLICT", "原声明的身份、状态、时刻或内容已经冲突；拒绝替换"
        )
    expected = {
        "simulation": True,
        "protocol": PROTOCOL,
        "user_id": str(user_id),
        "epoch_id": str(epoch_id),
        "idempotency_key": key,
        "admitted_at": evidence.created_at.isoformat(),
        "configuration": canonical,
        "configuration_hash": digest,
    }
    if (
        evidence.content != expected
        or evidence.content.get("simulation") is not True
        or evidence.content_hash != configuration_hash(expected)
        or evidence.content_hash != configuration_hash(evidence.content)
    ):
        raise _error("DECLARATION_ORIGINAL_CONFLICT", "原声明内容或原摘要不一致；拒绝重算覆盖")
    if proposal.status not in {"PROPOSED", "CONFIRMED", "REJECTED", "EXPIRED"}:
        raise _error("DECLARATION_ORIGINAL_CONFLICT", "原提案状态不属于已登记原协议")
    if proposal.status == "CONFIRMED":
        policy = (
            session.get(Policy, proposal.confirmed_policy_id)
            if proposal.confirmed_policy_id
            else None
        )
        first_version = session.scalar(
            select(PolicyVersion).where(
                PolicyVersion.user_id == user_id,
                PolicyVersion.policy_id == proposal.confirmed_policy_id,
                PolicyVersion.version_number == 1,
            )
        )
        try:
            first_configuration = (
                validate_configuration(first_version.configuration) if first_version else None
            )
            first_digest = (
                configuration_hash(first_version.configuration) if first_version else None
            )
        except (ValueError, TypeError) as error:
            raise _error(
                "DECLARATION_ORIGINAL_CONFLICT", "原首次确认配置不再通过原strict schema"
            ) from error
        if (
            policy is None
            or policy.user_id != user_id
            or first_version is None
            or first_configuration != canonical
            or first_digest != digest
            or first_version.content_hash != digest
            or str(evidence_id) not in first_version.evidence_ids
            or first_version.confirmed_at is None
            or not evidence.created_at <= first_version.confirmed_at <= now
            or first_version.valid_from is None
        ):
            raise _error("DECLARATION_ORIGINAL_CONFLICT", "原提案的首次明确确认回执已经冲突")
        expected_confirmation = {
            "accepted": True,
            "user_id": str(user_id),
            "policy_id": str(policy.id),
            "version_id": str(first_version.id),
            "reviewed_hash": digest,
            "confirmed_at": first_version.confirmed_at.isoformat(),
            "request_key": f"proposal:{proposal_id}",
            "request_hash": digest,
            "effective_from": first_version.valid_from.isoformat(),
            "effective_until": (
                first_version.valid_until.isoformat() if first_version.valid_until else None
            ),
        }
        if (
            first_version.confirmation != expected_confirmation
            or first_version.confirmation.get("accepted") is not True
        ):
            raise _error("DECLARATION_ORIGINAL_CONFLICT", "原首次确认的完整绑定或时刻与原版本冲突")
    elif proposal.confirmed_policy_id is not None:
        raise _error("DECLARATION_ORIGINAL_CONFLICT", "未确认原提案不能声称已有策略授权")
    return DeclarationResult(
        proposal_id=proposal_id,
        evidence_id=evidence_id,
        user_id=user_id,
        epoch_id=epoch_id,
        configuration=canonical,
        configuration_hash=digest,
        status=cast(Literal["PROPOSED", "CONFIRMED", "REJECTED", "EXPIRED"], proposal.status),
        admitted_at=evidence.created_at,
    )


def prepare_declared_policy(
    engine: Engine,
    user_id: UUID,
    configuration: dict[str, Any],
    idempotency_key: str,
    expected_epoch_id: UUID,
    now: datetime,
) -> DeclarationResult:
    """Persist a user input declaration plus PROPOSED proposal, atomically.

    Same epoch/key/canonical configuration returns the stored original fields even
    after original confirmation. New/different input never overwrites history.
    Current authorization must always use the original separate confirmation path.
    """
    require_isolated(engine)
    canonical, digest, now = canonical_inputs(configuration, idempotency_key, now)
    proposal_id, evidence_id, source_ref = declaration_identity(
        user_id, expected_epoch_id, idempotency_key
    )
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        transaction_gate(session, user_id)
        if session.scalar(text("SELECT current_database()")) != engine.url.database:
            raise _error("INVALID_SCENARIO_INPUT", "Actual isolated database identity required")
        user = session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or not user.is_simulated:
            raise _error("NOT_FOUND", "实际模拟用户不存在", 404)
        epochs = list(
            session.scalars(
                select(AuditEpoch)
                .where(AuditEpoch.user_id == user_id, AuditEpoch.status == "OPEN")
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if len(epochs) != 1 or epochs[0].id != expected_epoch_id:
            raise _error("STALE_DEMO_EPOCH", "实际原OPENepoch与声明不一致；不得跨轮复用")
        proposal = session.get(
            PolicyProposal, proposal_id, populate_existing=True, with_for_update=True
        )
        evidence = session.get(
            EvidenceItem, evidence_id, populate_existing=True, with_for_update=True
        )
        # Catch both deterministic-ID collisions and the independent DB unique key.
        by_key = session.scalar(
            select(PolicyProposal)
            .where(
                PolicyProposal.user_id == user_id,
                PolicyProposal.idempotency_key == source_ref + ":proposal",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (proposal is None) != (evidence is None) or (
            by_key is not None and by_key.id != proposal_id
        ):
            raise _error("DECLARATION_ORIGINAL_CONFLICT", "原声明或原提案缺失/身份冲突；拒绝修补")
        if proposal is not None and evidence is not None:
            return _original(
                session,
                user_id,
                expected_epoch_id,
                idempotency_key,
                canonical,
                digest,
                now,
                proposal,
                evidence,
                source_ref,
                proposal_id,
                evidence_id,
            )
        content = {
            "simulation": True,
            "protocol": PROTOCOL,
            "user_id": str(user_id),
            "epoch_id": str(expected_epoch_id),
            "idempotency_key": idempotency_key,
            "admitted_at": now.isoformat(),
            "configuration": canonical,
            "configuration_hash": digest,
        }
        evidence = EvidenceItem(
            id=evidence_id,
            user_id=user_id,
            created_at=now,
            evidence_level="USER_DECLARED",
            source_type=SOURCE_TYPE,
            source_ref=source_ref,
            content=content,
            content_hash=configuration_hash(content),
            valid_from=now,
            valid_to=None,
            observed_at=now,
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
            idempotency_key=source_ref + ":proposal",
        )
        session.add_all([evidence, proposal])
        session.flush()
        return _original(
            session,
            user_id,
            expected_epoch_id,
            idempotency_key,
            canonical,
            digest,
            now,
            proposal,
            evidence,
            source_ref,
            proposal_id,
            evidence_id,
        )
