"""Persisted, anchored candidate compilation. No policy authority or money operations."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from app.db.audit_guard import transaction_gate
from app.db.models import EvidenceItem, PolicyProposal, User
from app.domain.policy_compiler import (
    COMPILER_VERSION,
    CompilationIssue,
    CompilationResult,
    CompileContext,
    compile_policy,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

MODEL_COMPILER_VERSION = "candidate-provider-v1"
SOURCE_TYPE = "POLICY_COMPILATION"


class CandidateProvider(Protocol):
    def propose(self, text: str, context: CompileContext) -> dict[str, Any]: ...


class CompilationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    compilation_id: UUID
    compilation: CompilationResult
    configuration: dict[str, Any] | None
    configuration_hash: str | None
    proposal_id: UUID | None
    proposal_status: str | None


def _context(session: Session, user_id: UUID, now: datetime) -> tuple[datetime, CompileContext]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    transaction_gate(session, user_id)
    user = session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    if user.timezone not in {"Asia/Shanghai", "UTC"}:
        raise PolicyLifecycleError("UNSUPPORTED_TIMEZONE", "当前仅支持 Asia/Shanghai 或 UTC")
    zone = timezone(timedelta(hours=8)) if user.timezone == "Asia/Shanghai" else UTC
    try:
        now = now.astimezone(UTC)
        context = CompileContext(reference_date=now.astimezone(zone).date(), timezone=user.timezone)
    except (ValueError, OverflowError) as error:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间无法转换为编译日期") from error
    return now, context


def _configuration(configuration: dict[str, Any], today: date) -> dict[str, Any]:
    try:
        canonical = validate_configuration(configuration)
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_CONFIGURATION", "候选配置未通过严格格式校验") from error
    if canonical["type"] not in {"goal_saving", "emergency_buffer"}:
        raise PolicyLifecycleError("UNSUPPORTED_CONFIGURATION", "当前编译仅支持储蓄目标和应急金")
    if canonical.get("cross_goal_reallocation_allowed") is True:
        raise PolicyLifecycleError("UNSUPPORTED_CONFIGURATION", "候选编译不允许跨目标调拨授权")
    for field in ("deadline", "valid_until"):
        value = canonical.get(field)
        if value is not None and date.fromisoformat(value) < today:
            raise PolicyLifecycleError("PAST_DEADLINE", "目标截止或策略结束日期已经过去")
    return canonical


def _source_ref(text: str, context: CompileContext, engine: str, version: str) -> str:
    return "compilation:" + configuration_hash(
        {
            "text": text,
            "reference_date": context.reference_date.isoformat(),
            "timezone": context.timezone,
            "engine": engine,
            "compiler_version": version,
        }
    )


def _valid_evidence(item: EvidenceItem, now: datetime) -> None:
    try:
        valid = configuration_hash(item.content) == item.content_hash
    except (TypeError, ValueError):
        valid = False
    if (
        not valid
        or item.status != "VALID"
        or item.observed_at > now
        or item.valid_from > now
        or (item.valid_to is not None and item.valid_to <= now)
    ):
        raise PolicyLifecycleError("INVALID_EVIDENCE", "编译证据缺失、冲突、失效或内容已变化")


def _load(
    session: Session,
    user_id: UUID,
    compilation_id: UUID,
    now: datetime,
    *,
    lock: bool = True,
) -> tuple[EvidenceItem, CompilationResult]:
    query = select(EvidenceItem).where(
        EvidenceItem.id == compilation_id,
        EvidenceItem.user_id == user_id,
        EvidenceItem.source_type == SOURCE_TYPE,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    item = session.scalar(query)
    if item is None:
        raise PolicyLifecycleError("NOT_FOUND", "编译记录不存在", 404)
    _valid_evidence(item, now)
    try:
        result = CompilationResult.model_validate(item.content["compilation"])
        context = CompileContext(reference_date=result.reference_date, timezone=result.timezone)
        if (
            item.evidence_level != "USER_DECLARED"
            or item.content.get("user_id") != str(user_id)
            or item.content.get("simulation") is not True
            or item.content["reference_date"] != result.reference_date.isoformat()
            or item.content["timezone"] != result.timezone
            or item.content["engine"] not in {"rules", "llm"}
            or item.source_ref
            != _source_ref(
                item.content["text"], context, item.content["engine"], result.compiler_version
            )
            + ":"
            + configuration_hash(result.model_dump(mode="json"))
        ):
            raise ValueError("Compilation identity mismatch")
    except (KeyError, TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_EVIDENCE", "原始编译锚点或结果已变化") from error
    return item, result


def _read_clock(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    try:
        return now.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间无法转换为读取时钟") from error


def _proposal_source(
    session: Session, user_id: UUID, proposal: PolicyProposal, now: datetime
) -> tuple[EvidenceItem, CompilationResult]:
    try:
        prefix, identifier, digest = proposal.idempotency_key.split(":")
        if prefix != "compilation" or proposal.user_id != user_id:
            raise ValueError("Proposal compilation ownership or key is invalid")
        source, result = _load(session, user_id, UUID(identifier), now, lock=False)
        configuration = validate_configuration(proposal.proposed_configuration)
        original_configuration = (
            validate_configuration(result.configuration)
            if result.configuration is not None
            else None
        )
        ids = [UUID(identity) for identity in proposal.evidence_ids]
        if (
            proposal.source_type != SOURCE_TYPE
            or proposal.source_text != source.content["text"]
            or proposal.compiler_version != result.compiler_version
            or proposal.created_at > now
            or proposal.created_at < source.created_at
            or configuration != proposal.proposed_configuration
            or digest != configuration_hash(configuration)
            or len(ids) != len(set(ids))
            or source.id not in ids
        ):
            raise ValueError("Proposal does not bind its exact original compilation")
        expected_ids = {source.id}
        model_id = source.content.get("model_evidence_id")
        if model_id is not None:
            expected_ids.add(UUID(model_id))
        edits = []
        for identity in ids:
            if identity in expected_ids:
                continue
            edit = session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.id == identity,
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == "POLICY_COMPILATION_EDIT",
                )
            )
            if edit is None:
                raise ValueError("Compilation revision lacks its actual owned edit evidence")
            _valid_evidence(edit, now)
            if (
                edit.evidence_level != "USER_DECLARED"
                or edit.source_ref != proposal.idempotency_key
                or edit.content
                != {
                    "simulation": True,
                    "user_id": str(user_id),
                    "compilation_id": str(source.id),
                    "source_hash": source.content_hash,
                    "configuration": configuration,
                    "configuration_hash": configuration_hash(configuration),
                    "reference_date": source.content["reference_date"],
                    "timezone": source.content["timezone"],
                }
            ):
                raise ValueError(
                    "Revision evidence differs from its original source or configuration"
                )
            edits.append(edit)
        if len(edits) > 1 or (configuration != original_configuration and not edits):
            raise ValueError("Revision must have one exact original edit evidence")
        if set(ids) != expected_ids | {row.id for row in edits}:
            raise ValueError("Compilation proposal evidence references are incomplete")
        return source, result
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, PolicyLifecycleError):
            raise
        raise PolicyLifecycleError("INVALID_EVIDENCE", "候选与原编译记录不一致", 409) from error


def proposal_compilation_id(
    session: Session, user_id: UUID, proposal: PolicyProposal, now: datetime
) -> UUID | None:
    """Return only a verified owned compilation identity, without locks or writes."""
    if proposal.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "候选不存在", 404)
    if proposal.source_type != SOURCE_TYPE:
        return None
    with session.no_autoflush:
        source, _ = _proposal_source(session, user_id, proposal, _read_clock(now))
        return source.id


def read_compilation(
    session: Session, user_id: UUID, compilation_id: UUID, now: datetime
) -> CompilationResponse:
    """Recover original anchored results and the current revision in a caller-owned snapshot."""
    now = _read_clock(now)
    with session.no_autoflush:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        source, result = _load(session, user_id, compilation_id, now, lock=False)
        proposals = session.scalars(
            select(PolicyProposal)
            .where(
                PolicyProposal.user_id == user_id,
                PolicyProposal.source_type == SOURCE_TYPE,
                PolicyProposal.idempotency_key.startswith(f"compilation:{source.id}:"),
            )
            .order_by(PolicyProposal.created_at, PolicyProposal.id)
            .limit(1001)
        ).all()
        if len(proposals) > 1000:
            raise PolicyLifecycleError(
                "COMPILATION_CAPACITY_EXCEEDED", "编译修订数量超出读取上限", 409
            )
        for proposal in proposals:
            _proposal_source(session, user_id, proposal, now)
        current = [row for row in proposals if row.status in {"PROPOSED", "CONFIRMED"}]
        if len(current) > 1:
            raise PolicyLifecycleError(
                "CONFLICTING_COMPILATION_STATE", "原编译存在多个当前候选", 409
            )
        selected: PolicyProposal | None
        if current:
            selected = current[0]
        elif proposals:
            latest = [row for row in proposals if row.created_at == proposals[-1].created_at]
            if len(latest) > 1:
                raise PolicyLifecycleError(
                    "CONFLICTING_COMPILATION_STATE", "原编译历史候选无法唯一定位", 409
                )
            selected = latest[0]
        else:
            selected = None
        configuration = (
            selected.proposed_configuration if selected is not None else result.configuration
        )
        return _response(source, result, configuration, selected)


def _response(
    source: EvidenceItem,
    result: CompilationResult,
    configuration: dict[str, Any] | None,
    proposal: PolicyProposal | None,
) -> CompilationResponse:
    return CompilationResponse(
        user_id=source.user_id,
        compilation_id=source.id,
        compilation=result,
        configuration=configuration,
        configuration_hash=configuration_hash(configuration) if configuration is not None else None,
        proposal_id=proposal.id if proposal is not None else None,
        proposal_status=proposal.status if proposal is not None else None,
    )


def _candidate(
    session: Session,
    source: EvidenceItem,
    configuration: dict[str, Any],
    now: datetime,
    *,
    edited: bool,
) -> PolicyProposal:
    prefix = f"compilation:{source.id}:"
    key = prefix + configuration_hash(configuration)
    proposals = list(
        session.scalars(
            select(PolicyProposal)
            .where(
                PolicyProposal.user_id == source.user_id,
                PolicyProposal.source_type == SOURCE_TYPE,
                PolicyProposal.idempotency_key.startswith(prefix),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if edited and any(
        p.status == "CONFIRMED" or p.confirmed_policy_id is not None for p in proposals
    ):
        raise PolicyLifecycleError(
            "INVALID_COMPILATION_STATE", "已确认候选请通过策略版本变更修改", 409
        )
    existing = next((p for p in proposals if p.idempotency_key == key), None)
    if existing is not None:
        return existing
    evidence_ids = [str(source.id)]
    if edited:
        edit = {
            "simulation": True,
            "user_id": str(source.user_id),
            "compilation_id": str(source.id),
            "source_hash": source.content_hash,
            "configuration": configuration,
            "configuration_hash": configuration_hash(configuration),
            "reference_date": source.content["reference_date"],
            "timezone": source.content["timezone"],
        }
        edit_id = uuid4()
        session.add(
            EvidenceItem(
                id=edit_id,
                user_id=source.user_id,
                evidence_level="USER_DECLARED",
                source_type="POLICY_COMPILATION_EDIT",
                source_ref=key,
                content=edit,
                content_hash=configuration_hash(edit),
                valid_from=now,
                observed_at=now,
                created_at=now,
                status="VALID",
            )
        )
        evidence_ids.append(str(edit_id))
    if source.content.get("model_evidence_id") is not None:
        evidence_ids.append(source.content["model_evidence_id"])
    for old in proposals:
        if old.status == "PROPOSED":
            old.status = "EXPIRED"
    proposal = PolicyProposal(
        id=uuid4(),
        user_id=source.user_id,
        source_type=SOURCE_TYPE,
        source_text=source.content["text"],
        compiler_version=source.content["compilation"]["compiler_version"],
        proposed_configuration=configuration,
        evidence_ids=evidence_ids,
        status="PROPOSED",
        idempotency_key=key,
        created_at=now,
    )
    session.add(proposal)
    return proposal


def compile_candidate(
    session: Session,
    user_id: UUID,
    text: str,
    now: datetime,
    engine: str = "rules",
    llm_enabled: bool = False,
    provider: CandidateProvider | None = None,
) -> CompilationResponse:
    with session.begin_nested():
        now, context = _context(session, user_id, now)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise PolicyLifecycleError("INVALID_TEXT", "请输入不超过2000字的非空策略原文")
        if engine not in {"rules", "llm"}:
            raise PolicyLifecycleError("UNSUPPORTED_ENGINE", "不支持该候选编译引擎")
        if engine == "llm" and not llm_enabled:
            raise PolicyLifecycleError("LLM_DISABLED", "可选模型编译当前未启用")
        if engine == "llm" and provider is None:
            raise PolicyLifecycleError("LLM_UNAVAILABLE", "未配置候选模型提供方", 503)
        version = COMPILER_VERSION if engine == "rules" else MODEL_COMPILER_VERSION
        reference = _source_ref(text, context, engine, version)
        existing = list(
            session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == SOURCE_TYPE,
                    EvidenceItem.source_ref.startswith(reference + ":"),
                )
                .limit(2)
            )
        )
        if len(existing) > 1:
            raise PolicyLifecycleError(
                "COMPILATION_CONFLICT", "同一编译输入存在冲突的原始记录", 409
            )
        if existing:
            source, result = _load(session, user_id, existing[0].id, now)
        else:
            model_evidence_id: UUID | None = None
            model_evidence_hash: str | None = None
            if engine == "rules":
                result = compile_policy(text, context)
            else:
                assert provider is not None
                try:
                    raw = provider.propose(text, context)
                except Exception as error:
                    raise PolicyLifecycleError(
                        "LLM_UNAVAILABLE", "候选模型调用失败", 503
                    ) from error
                try:
                    configuration_hash(raw)
                except (TypeError, ValueError) as error:
                    raise PolicyLifecycleError(
                        "INVALID_LLM_OUTPUT", "模型输出必须是严格JSON对象"
                    ) from error
                issues: list[CompilationIssue] = []
                canonical: dict[str, Any] | None = None
                try:
                    canonical = _configuration(raw, context.reference_date)
                except PolicyLifecycleError as error:
                    issues.append(
                        CompilationIssue(
                            code=error.code,
                            field="configuration",
                            message=error.message,
                            source_fragment=text,
                        )
                    )
                result = CompilationResult(
                    compiler_version=version,
                    reference_date=context.reference_date,
                    timezone=context.timezone,
                    draft=raw,
                    configuration=canonical,
                    issues=issues,
                    assumptions=["可选模型输出仅为候选，已独立校验；不代表用户确认或执行授权。"],
                )
                model_evidence_id = uuid4()
                model_content = {
                    "simulation": True,
                    "provider": type(provider).__name__,
                    "text": text,
                    "context": context.model_dump(mode="json"),
                    "candidate": raw,
                }
                model_evidence_hash = configuration_hash(model_content)
                session.add(
                    EvidenceItem(
                        id=model_evidence_id,
                        user_id=user_id,
                        evidence_level="MODEL_INFERRED",
                        source_type="POLICY_COMPILATION_MODEL",
                        source_ref=reference
                        + ":"
                        + configuration_hash(result.model_dump(mode="json")),
                        content=model_content,
                        content_hash=model_evidence_hash,
                        valid_from=now,
                        observed_at=now,
                        created_at=now,
                        status="VALID",
                    )
                )
            content = {
                "simulation": True,
                "user_id": str(user_id),
                "text": text,
                "engine": engine,
                "reference_date": context.reference_date.isoformat(),
                "timezone": context.timezone,
                "compilation": result.model_dump(mode="json"),
                "model_evidence_id": str(model_evidence_id)
                if model_evidence_id is not None
                else None,
                "model_evidence_hash": model_evidence_hash,
            }
            source = EvidenceItem(
                id=uuid4(),
                user_id=user_id,
                evidence_level="USER_DECLARED",
                source_type=SOURCE_TYPE,
                source_ref=reference + ":" + configuration_hash(result.model_dump(mode="json")),
                content=content,
                content_hash=configuration_hash(content),
                valid_from=now,
                observed_at=now,
                created_at=now,
                status="VALID",
            )
            session.add(source)
            session.flush()
        proposal = None
        if result.configuration is not None:
            canonical = _configuration(result.configuration, context.reference_date)
            proposal = _candidate(session, source, canonical, now, edited=False)
        session.flush()
        return _response(source, result, result.configuration, proposal)


def revise_compilation(
    session: Session,
    user_id: UUID,
    compilation_id: UUID,
    configuration: dict[str, Any],
    now: datetime,
) -> CompilationResponse:
    with session.begin_nested():
        now, context = _context(session, user_id, now)
        source, result = _load(session, user_id, compilation_id, now)
        canonical = _configuration(configuration, context.reference_date)
        proposal = _candidate(session, source, canonical, now, edited=True)
        session.flush()
        return _response(source, result, canonical, proposal)
