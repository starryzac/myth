"""Signed USER adoption, exact original recovery and fresh read-only protection proof."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import EvidenceItem, User
from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace import build_trace
from app.domain.full_policy_configuration import SeasonalReservePolicy, validate_full_configuration
from app.domain.full_seasonal_adoption import (
    MAX_ADOPTIONS,
    PROTOCOL,
    SOURCE,
    Key,
    SeasonalAdoptionConfirmRequest,
    SeasonalAdoptionOriginal,
    SeasonalAdoptionPreviewRequest,
    SeasonalAdoptionProof,
    SeasonalAdoptionScope,
    assert_no_seasonal_overlap,
    original_from_json,
    seasonal_command_id,
    seasonal_request_hash,
    seasonal_review_hash,
    seasonal_source_hash,
    validate_seasonal_scope,
    verify_frozen_seasonal_adoption_trace,
)
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.pattern_suggestions import SeasonalParameters
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import (
    audit_read_scope,
    current_audit_epoch,
    row_copy,
    verify_audit_chain,
)
from app.services.decision_trace import evidence_copy, get_decision_trace, record_trace
from app.services.financial_read import load_verified_financial_context
from app.services.full_policy_lifecycle import _read_snapshot, read_full_policy
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from app.services.policy_suggestions import seasonal_suggestions
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MAX_SCOPE_BYTES = 524_288


class SeasonalAdoptionPreview(BoundaryModel):
    simulation: Literal[True] = True
    status: Literal["REVIEW_REQUIRED", "UNKNOWN"]
    scope: SeasonalAdoptionScope | None
    reviewed_hash: str | None
    reasons: list[str]
    bank_authority: Literal[False] = False
    hard_protection_changed: Literal[False] = False


class SeasonalAdoptionReceipt(BoundaryModel):
    simulation: Literal[True] = True
    original: SeasonalAdoptionOriginal
    evidence_id: UUID
    evidence_hash: str
    trace_hash: str
    idempotent_replay: bool
    bank_authority: Literal[False] = False
    financial_execution_performed: Literal[False] = False


class SeasonalAdoptionLookup(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    idempotency_key: Key
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    original_receipt: SeasonalAdoptionReceipt | None
    bank_authority: Literal[False] = False


def _error(message: str, code: str = "SEASONAL_ADOPTION_SOURCE_UNKNOWN") -> PolicyLifecycleError:
    return PolicyLifecycleError(code, message, 409)


@contextmanager
def _reader(engine: Engine) -> Iterator[Session]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin(), Session(connection) as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            with audit_read_scope(session):
                yield session


def _scope(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    request: SeasonalAdoptionPreviewRequest,
    now: datetime,
) -> SeasonalAdoptionScope:
    _read_snapshot(session)
    now = _now(now)
    user = session.get(User, user_id)
    epoch = current_audit_epoch(session, user_id)
    if user is None or not user.is_simulated or user.timezone != "Asia/Shanghai":
        raise _error("仅支持当前模拟用户的中国日历来源")
    if epoch is None or epoch.status != "OPEN":
        raise _error("当前原审计周期缺失")
    if verify_audit_chain(session, user_id, epoch.id).status != "VALID":
        raise _error("当前完整审计原件未通过")
    context, bank_matched, _ = load_verified_financial_context(session, user_id, now)
    if not bank_matched or context.sources.issues:
        raise _error("当前银行、收入及原财务来源未完整验真")
    policy = read_full_policy(session, user_id, policy_id, now)
    version = policy.current_version
    if (
        policy.template_name != "SeasonalReservePolicy"
        or policy.epoch_id != epoch.id
        or version.version_id != request.expected_version_id
        or not policy.planning_confirmation_valid
        or policy.reference_validation != "CURRENT"
        or policy.effective_status not in {"ACTIVE", "CONFIRMED"}
    ):
        raise _error("当前已明确确认的节日策略版本或来源已变化")
    config = SeasonalReservePolicy.model_validate(
        validate_full_configuration("SeasonalReservePolicy", version.configuration)
    )
    quantile = Decimal(str(config.quantile)) * 10_000
    if quantile != quantile.to_integral_value():
        raise _error("当前策略分位数不在原建议整基点合同内")
    try:
        parameters = SeasonalParameters(
            window_id=request.window_id,
            lookback_days=config.lookback_days,
            minimum_historical_windows=config.minimum_historical_windows,
            quantile_bps=int(quantile),
            essential_categories=config.essential_categories,
            adjustment_cap_cents=config.adjustment_cap_cents,
        )
    except ValueError as error:
        raise _error("原策略参数超出已实现建议范围，不自动修改参数") from error
    suggestion = seasonal_suggestions(session, user_id, now, parameters)
    row = suggestion.suggestion
    if (
        row.status != "READY"
        or row.target is None
        or row.effective_window_start is None
        or row.effective_window_end is None
        or row.proposed_adjustment_cents is None
        or row.required_adjustment_cents is None
        or row.cap_limited is None
    ):
        raise _error("原节日历史或建议不足，不能采纳金额")
    ids = set(suggestion.source_evidence_ids) | set(version.evidence_ids)
    sources = [session.get(EvidenceItem, identifier) for identifier in sorted(ids)]
    if any(source is None for source in sources):
        raise _error("原完整历史和确认来源缺失")
    scope = SeasonalAdoptionScope(
        user_id=user_id,
        epoch_id=epoch.id,
        policy_id=policy_id,
        version_id=version.version_id,
        configuration_hash=version.content_hash,
        evaluated_at=now,
        window_id=row.target.window_id,
        holiday_code=row.target.holiday_code,
        official_start=row.target.start,
        official_end=row.target.end,
        protection_start=row.effective_window_start,
        protection_end=row.effective_window_end,
        adopted_adjustment_cents=row.proposed_adjustment_cents,
        required_adjustment_cents=row.required_adjustment_cents,
        cap_limited=row.cap_limited,
        full_policy_original=policy.model_dump(mode="json"),
        suggestion_original=suggestion.model_dump(mode="json"),
        source_evidence_originals=[row_copy(source) for source in sources if source is not None],
        parameters=parameters,
    )
    try:
        validate_seasonal_scope(scope)
        if len(scope.model_dump_json().encode("utf-8")) > MAX_SCOPE_BYTES:
            raise ValueError("Original complete scope exceeds the explicit capacity")
    except (ValueError, KeyError, TypeError) as error:
        raise _error("原建议、完整来源和当前策略窗口未完整绑定") from error
    return scope


def preview_seasonal_adoption(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    body: SeasonalAdoptionPreviewRequest,
    now: datetime,
) -> SeasonalAdoptionPreview:
    _read_snapshot(session)
    try:
        with audit_read_scope(session):
            scope = _scope(session, user_id, policy_id, body, now)
            assert_no_seasonal_overlap(
                scope, [row.original for row in _originals(session, user_id, scope.epoch_id, now)]
            )
        return SeasonalAdoptionPreview(
            status="REVIEW_REQUIRED",
            scope=scope,
            reviewed_hash=seasonal_review_hash(scope),
            reasons=[],
        )
    except (PolicyLifecycleError, ValueError) as error:
        return SeasonalAdoptionPreview(
            status="UNKNOWN", scope=None, reviewed_hash=None, reasons=[str(error)]
        )


def _original(
    session: Session, user_id: UUID, command_id: UUID, now: datetime, *, replay: bool
) -> SeasonalAdoptionReceipt | None:
    evidence = session.get(EvidenceItem, uuid5(command_id, "evidence"))
    if evidence is None:
        return None
    try:
        original = original_from_json(evidence.content)
        trace = get_decision_trace(session, user_id, command_id, now)
        if (
            evidence.user_id != user_id
            or original.user_id != user_id
            or original.command_id != command_id
            or evidence.source_type != SOURCE
            or evidence.source_ref != str(command_id)
            or evidence.evidence_level != "USER_CONFIRMED_POLICY"
            or evidence.status != "VALID"
            or evidence.content_hash != configuration_hash(evidence.content)
            or evidence.created_at != original.recorded_at
            or evidence.observed_at != original.recorded_at
            or evidence.valid_from != original.recorded_at
            or evidence.valid_to is not None
            or trace.completeness != "COMPLETE"
            or trace.audit_chain_status != "VALID"
            or trace.trace is None
            or trace.trace.action_id is not None
            or trace.trace.parent_run_id is not None
            or trace.trace.algorithm_versions
            != {"trace": "decision-trace-v1", "seasonal_adoption": PROTOCOL}
            or trace.trace.outcome
            != {"seasonal_adoption_original": original.model_dump(mode="json")}
            or trace.trace.inputs
            != {
                "original_request": original.original_request.model_dump(mode="json"),
                "request_hash": original.request_hash,
                "reviewed_hash": original.reviewed_hash,
            }
        ):
            raise ValueError("Original adoption metadata/trace/hash changed")
        expected = {
            row["id"]: row["content_hash"] for row in original.scope.source_evidence_originals
        }
        expected[str(evidence.id)] = evidence.content_hash
        actual = {str(row.id): row.content_hash for row in trace.trace.sources}
        if actual != expected:
            raise ValueError("Original adoption source denominator changed")
        if verify_frozen_seasonal_adoption_trace(trace.trace) != original:
            raise ValueError("Original seasonal math/command replay is not exact")
    except (ValueError, TypeError, KeyError) as error:
        raise _error(
            "节日采纳原命令、确认或审计被改变", "SEASONAL_ADOPTION_INTEGRITY_ERROR"
        ) from error
    return SeasonalAdoptionReceipt(
        original=original,
        evidence_id=evidence.id,
        evidence_hash=evidence.content_hash,
        trace_hash=trace.trace.trace_hash,
        idempotent_replay=replay,
    )


def _originals(
    session: Session, user_id: UUID, epoch_id: UUID, now: datetime
) -> list[SeasonalAdoptionReceipt]:
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(EvidenceItem.user_id == user_id, EvidenceItem.source_type == SOURCE)
            .order_by(EvidenceItem.id)
            .limit(MAX_ADOPTIONS + 1)
        )
    )
    if len(rows) > MAX_ADOPTIONS:
        raise _error("采纳原件超出明确完整容量，不截断历史")
    result = []
    for row in rows:
        # Parse and verify every registered row before excluding another epoch.
        original = original_from_json(row.content)
        receipt = _original(session, user_id, original.command_id, now, replay=True)
        if receipt is None or receipt.evidence_id != row.id:
            raise _error("原采纳证据身份不一致")
        if original.epoch_id == epoch_id:
            result.append(receipt)
    return result


def read_seasonal_adoption_command(
    session: Session, user_id: UUID, epoch_id: UUID, key: Key, now: datetime
) -> SeasonalAdoptionLookup:
    _read_snapshot(session)
    with audit_read_scope(session):
        receipt = _original(
            session, user_id, seasonal_command_id(user_id, epoch_id, key), _now(now), replay=True
        )
    return SeasonalAdoptionLookup(
        user_id=user_id,
        epoch_id=epoch_id,
        idempotency_key=key,
        status="RECORDED" if receipt else "NOT_FOUND_NOT_FINAL",
        original_receipt=receipt,
    )


def confirm_seasonal_adoption(
    engine: Engine,
    user_id: UUID,
    policy_id: UUID,
    body: SeasonalAdoptionConfirmRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> SeasonalAdoptionReceipt:
    now = _now(now)
    try:
        require_local_user(principal, user_id, now)
    except ValueError as error:
        raise PolicyLifecycleError(
            "LOCAL_USER_SESSION_REQUIRED", "需要当前签名本地USER会话", 403
        ) from error
    identity = seasonal_command_id(user_id, body.expected_epoch_id, body.idempotency_key)
    digest = seasonal_request_hash(user_id, policy_id, body)
    with audit_command_guard(engine, user_id), Session(engine) as writer, writer.begin():
        _user(writer, user_id)
        with _reader(engine) as reader:
            existing = _original(reader, user_id, identity, now, replay=True)
            if existing is not None:
                if existing.original.request_hash != digest:
                    raise _error("原键不能替换原采纳请求", "IDEMPOTENCY_CONFLICT")
                return existing
            preview_request = SeasonalAdoptionPreviewRequest(
                expected_version_id=body.expected_version_id, window_id=body.window_id
            )
            scope = _scope(reader, user_id, policy_id, preview_request, now)
            if (
                scope.epoch_id != body.expected_epoch_id
                or seasonal_review_hash(scope) != body.reviewed_hash
            ):
                raise _error(
                    "原周期、版本或已复核建议来源已变化", "SEASONAL_ADOPTION_REVIEW_MISMATCH"
                )
            assert_no_seasonal_overlap(
                scope, [row.original for row in _originals(reader, user_id, scope.epoch_id, now)]
            )
            source_rows = [
                reader.get(EvidenceItem, UUID(row["id"])) for row in scope.source_evidence_originals
            ]
            source_copies = [evidence_copy(row) for row in source_rows if row is not None]
        original = SeasonalAdoptionOriginal(
            command_id=identity,
            user_id=user_id,
            epoch_id=body.expected_epoch_id,
            policy_id=policy_id,
            idempotency_key=body.idempotency_key,
            original_request=body,
            request_hash=digest,
            reviewed_hash=body.reviewed_hash,
            source_binding_hash=seasonal_source_hash(scope),
            principal_at_command=principal,
            recorded_at=now,
            scope=scope,
        )
        content = original.model_dump(mode="json")
        evidence = EvidenceItem(
            id=uuid5(identity, "evidence"),
            user_id=user_id,
            created_at=now,
            evidence_level="USER_CONFIRMED_POLICY",
            source_type=SOURCE,
            source_ref=str(identity),
            content=content,
            content_hash=configuration_hash(content),
            status="VALID",
            observed_at=now,
            valid_from=now,
            valid_to=None,
        )
        writer.add(evidence)
        writer.flush()
        trace = build_trace(
            run_id=identity,
            user_id=user_id,
            phase="EVALUATION",
            as_of=now,
            action_id=None,
            parent_run_id=None,
            algorithm_versions={"trace": "decision-trace-v1", "seasonal_adoption": PROTOCOL},
            inputs={
                "original_request": body.model_dump(mode="json"),
                "request_hash": digest,
                "reviewed_hash": body.reviewed_hash,
            },
            sources=[*source_copies, evidence_copy(evidence)],
            policies=[],
            outcome={"seasonal_adoption_original": content},
        )
        record_trace(writer, trace)
    with _reader(engine) as reader:
        receipt = _original(reader, user_id, identity, now, replay=False)
    if receipt is None:
        raise _error("原命令可能已提交，保留原键查询，不换键重发")
    return receipt


def read_current_seasonal_adoption(
    session: Session, user_id: UUID, policy_id: UUID, now: datetime
) -> SeasonalAdoptionProof:
    _read_snapshot(session)
    now = _now(now)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN":
        raise _error("当前原审计周期缺失")
    values: dict[str, Any] = dict(
        user_id=user_id, epoch_id=epoch.id, policy_id=policy_id, as_of=now
    )
    with audit_read_scope(session):
        policy = read_full_policy(session, user_id, policy_id, now)
        # Empty owner-filtered rows must still have the full current audit anchor.
        if verify_audit_chain(session, user_id, epoch.id).status != "VALID":
            raise _error("当前完整审计未通过，不能判无采纳", "SEASONAL_ADOPTION_INTEGRITY_ERROR")
        if policy.template_name != "SeasonalReservePolicy" or policy.epoch_id != epoch.id:
            raise _error("当前原策略不是该用户本周期的节日策略")
        receipts = _originals(session, user_id, epoch.id, now)
        ids = [row.original.command_id for row in receipts]
        selected = [row for row in receipts if row.original.policy_id == policy_id]
        if not selected:
            return SeasonalAdoptionProof(
                status="ADVICE_ONLY",
                actual_adoption_count=len(ids),
                retained_command_ids=ids,
                reasons=["NO_ORIGINAL_USER_ADOPTION"],
                **values,
            )
        if len(selected) != 1:
            return SeasonalAdoptionProof(
                status="UNKNOWN",
                actual_adoption_count=len(ids),
                retained_command_ids=ids,
                reasons=["NONUNIQUE_REGISTERED_ADOPTION"],
                **values,
            )
        receipt = selected[0]
        original = receipt.original
        reasons = []
        current = None
        try:
            current = _scope(
                session,
                user_id,
                policy_id,
                SeasonalAdoptionPreviewRequest(
                    expected_version_id=original.scope.version_id,
                    window_id=original.scope.window_id,
                ),
                now,
            )
            if seasonal_source_hash(current) != original.source_binding_hash:
                reasons.append("ADOPTED_ORIGINAL_SOURCE_OR_POLICY_CHANGED")
            assert_no_seasonal_overlap(
                current, [row.original for row in receipts if row != receipt]
            )
        except (PolicyLifecycleError, ValueError) as error:
            reasons.append(str(error))
        return SeasonalAdoptionProof(
            status="UNKNOWN" if reasons else "VERIFIED",
            original=original,
            evidence_id=receipt.evidence_id,
            evidence_hash=receipt.evidence_hash,
            trace_hash=receipt.trace_hash,
            current_scope=current,
            actual_adoption_count=len(ids),
            retained_command_ids=ids,
            reasons=reasons,
            **values,
        )
