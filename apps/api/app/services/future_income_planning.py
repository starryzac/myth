"""Append-only user assumptions from complete native income origins; no funds calls."""

import json
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.db.models import AuditEpoch, EvidenceItem, Transaction, User
from app.domain.boundary_types import BoundaryModel
from app.domain.future_income_planning import (
    FutureIncomeAssumption,
    FutureIncomeCandidate,
    FutureIncomeCandidateRequest,
    FutureIncomeCommandLookup,
    FutureIncomeConfirmation,
    FutureIncomeConfirmationRequest,
    FutureIncomePlanningResponse,
    FutureIncomePlanState,
    FutureIncomeSourceInventory,
    PlanningIncomeSource,
    assumption_for_source,
    conditional_schedule,
    source_hash,
)
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.local_actor_session_types import LocalActorPrincipal, require_local_user
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch, row_copy
from app.services.boundary import Sources
from app.services.dashboard_helpers import current_epoch_audit
from app.services.evidence_graph import readonly
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from sqlalchemy import select
from sqlalchemy.orm import Session

NAMESPACE = UUID("effcb4a8-1729-47d0-870c-f575c19e0ef2")
CANDIDATE_SOURCE = "FUTURE_INCOME_PLANNING_CANDIDATE"
CONFIRM_SOURCE = "FUTURE_INCOME_PLANNING_CONFIRM"
METADATA_LIMIT = 200
ORIGIN_LIMIT = 1000


class CandidatePayload(BoundaryModel):
    protocol: Literal["future-income-candidate-v1"] = "future-income-candidate-v1"
    simulation: Literal[True] = True
    candidate_id: UUID
    user_id: UUID
    epoch_id: UUID
    admitted_at: datetime
    confirmation_deadline: datetime
    actor: LocalActorPrincipal
    original_request: FutureIncomeCandidateRequest
    request_hash: str
    source: PlanningIncomeSource
    assumption: FutureIncomeAssumption
    candidate_hash: str
    grants_authority: Literal[False] = False


class ConfirmationPayload(BoundaryModel):
    protocol: Literal["future-income-confirmation-v1"] = "future-income-confirmation-v1"
    simulation: Literal[True] = True
    confirmation_id: UUID
    user_id: UUID
    epoch_id: UUID
    candidate_id: UUID
    candidate_hash: str
    confirmed_at: datetime
    actor: LocalActorPrincipal
    original_request: FutureIncomeConfirmationRequest
    request_hash: str
    original_candidate_snapshot: dict[str, Any]
    grants_authority: Literal[False] = False


def identity(user_id: UUID, epoch_id: UUID, key: str) -> tuple[UUID, str]:
    basis = f"future-income-command-v1:{user_id}:{epoch_id}:{key}"
    return uuid5(NAMESPACE, basis), f"income-plan:{epoch_id}:" + configuration_hash(
        {"basis": basis}
    )


def _zone(user: User) -> Literal["Asia/Shanghai", "UTC"]:
    if user.timezone not in {"Asia/Shanghai", "UTC"}:
        raise PolicyLifecycleError(
            "UNSUPPORTED_TIMEZONE", "规划只支持原登记UTC/Asia/Shanghai时区", 409
        )
    return cast(Literal["Asia/Shanghai", "UTC"], user.timezone)


def _records(session: Session, user_id: UUID, epoch_id: UUID) -> list[EvidenceItem]:
    records = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type.in_([CANDIDATE_SOURCE, CONFIRM_SOURCE]),
                EvidenceItem.source_ref.startswith(f"income-plan:{epoch_id}:"),
            )
            .order_by(EvidenceItem.id)
            .limit(METADATA_LIMIT + 1)
        )
    )
    if len(records) > METADATA_LIMIT:
        raise PolicyLifecycleError(
            "PLANNING_METADATA_CAPACITY_EXCEEDED",
            "当前轮次规划声明超过200原件，不能省略后报完整",
            409,
        )
    return records


def read_future_income_sources(
    session: Session, user_id: UUID, now: datetime
) -> FutureIncomeSourceInventory:
    readonly(session)
    return _sources(session, user_id, _now(now))


def _sources(session: Session, user_id: UUID, now: datetime) -> FutureIncomeSourceInventory:
    user = session.get(User, user_id)
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "实际模拟用户不存在", 404)
    zone = _zone(user)
    epoch = current_audit_epoch(session, user_id)
    sources = []
    issues = []
    count = None
    try:
        if epoch is None or epoch.opened_at > now:
            raise PolicyLifecycleError("UNKNOWN_CURRENT_EPOCH", "缺当前已知OPEN轮次", 409)
        audit = current_epoch_audit(session, user_id, [])
        if audit.status != "VALID" or not audit.complete:
            raise PolicyLifecycleError("AUDIT_NOT_VERIFIED", "当前完整审计未验真", 409)
        state = read_income_state(session, user_id, now)
        count = len(state.ledger.origins)
        if count > ORIGIN_LIMIT:
            raise PolicyLifecycleError(
                "PLANNING_SOURCE_CAPACITY_EXCEEDED", "超过1000原收入来源，不能截断登记", 409
            )
        sources = [
            PlanningIncomeSource(
                user_id=user_id,
                origin=origin,
                origin_hash=source_hash(origin),
                ledger_evidence_id=state.evidence_id,
                ledger_evidence_hash=state.evidence_hash,
            )
            for origin in sorted(state.ledger.origins, key=lambda item: item.origin_transaction_id)
        ]
    except (PolicyLifecycleError, ValueError, TypeError) as error:
        sources = []
        issues = [
            error.code
            if isinstance(error, PolicyLifecycleError)
            else "UNKNOWN_ORIGINAL_INCOME_SOURCE"
        ]
    complete = not issues
    return FutureIncomeSourceInventory(
        user_id=user_id,
        epoch_id=epoch.id if epoch else None,
        as_of=now,
        timezone=zone,
        status="VERIFIED_ORIGINAL_INCOME_SOURCES" if complete else "UNKNOWN",
        original_origin_count=count,
        captured_origin_count=len(sources),
        complete=complete,
        sources=sources,
        issues=issues,
    )


def _metadata_fields(
    row: EvidenceItem,
    user_id: UUID,
    epoch_id: UUID,
    key: str,
    now: datetime,
    timestamp: datetime,
    source_type: str,
    payload: dict[str, Any],
) -> None:
    identifier, reference = identity(user_id, epoch_id, key)
    if (
        row.id != identifier
        or row.user_id != user_id
        or row.source_ref != reference
        or row.source_type != source_type
        or row.evidence_level != "USER_DECLARED"
        or row.status != "VALID"
        or row.supersedes_id is not None
        or row.valid_to is not None
        or row.created_at != timestamp
        or row.valid_from != timestamp
        or row.observed_at != timestamp
        or timestamp > now
        or row.content != payload
        or row.content_hash != configuration_hash(payload)
    ):
        raise ValueError(
            "Immutable planning metadata identity, status, clock or hash does not match"
        )


def _original_source(session: Session, source: PlanningIncomeSource, now: datetime) -> None:
    origin = source.origin
    transaction = session.get(Transaction, origin.origin_transaction_id)
    evidence = session.get(EvidenceItem, origin.bank_evidence_id)
    ledger_original = session.get(EvidenceItem, source.ledger_evidence_id)
    if (
        transaction is None
        or evidence is None
        or ledger_original is None
        or transaction.user_id != source.user_id
        or transaction.account_id != origin.origin_account_id
        or transaction.direction != "CREDIT"
        or transaction.amount_cents != origin.amount_cents
        or transaction.occurred_at != origin.occurred_at
        or transaction.observed_at != origin.observed_at
        or origin.observed_at > now
        or origin.occurred_at > now
        or evidence.content_hash != origin.bank_evidence_hash
        or ledger_original.user_id != source.user_id
        or ledger_original.source_type != "SIMULATED_NEW_FUNDS_LEDGER"
        or ledger_original.evidence_level != "BANK_CONFIRMED"
        or ledger_original.status not in {"VALID", "SUPERSEDED"}
        or ledger_original.content_hash != source.ledger_evidence_hash
        or configuration_hash(ledger_original.content) != source.ledger_evidence_hash
        or not Sources(source.user_id, now, [evidence]).valid(evidence, {})
    ):
        raise ValueError("Retained native original income source does not match")
    if bank_fact_snapshot(transaction, evidence)["economic_role"] != "INCOME":
        raise ValueError("Opening, principal return or transfers are not an original income")


def _candidate(
    session: Session, row: EvidenceItem, user_id: UUID, epoch_id: UUID, now: datetime
) -> FutureIncomeCandidate:
    try:
        payload = CandidatePayload.model_validate_json(json.dumps(row.content))
        body = payload.original_request
        _metadata_fields(
            row,
            user_id,
            epoch_id,
            body.idempotency_key,
            now,
            payload.admitted_at,
            CANDIDATE_SOURCE,
            payload.model_dump(mode="json"),
        )
        require_local_user(payload.actor, user_id, payload.admitted_at)
        assumption = payload.assumption
        expected = assumption_for_source(
            payload.source, epoch_id, payload.admitted_at, assumption.timezone
        )
        epoch = session.get(AuditEpoch, epoch_id)
        if (
            payload.candidate_id != row.id
            or payload.user_id != user_id
            or payload.epoch_id != epoch_id
            or body.expected_epoch_id != epoch_id
            or body.origin_transaction_id != payload.source.origin.origin_transaction_id
            or body.expected_origin_hash != payload.source.origin_hash
            or payload.source.user_id != user_id
            or payload.request_hash != configuration_hash(body.model_dump(mode="json"))
            or expected != assumption
            or payload.candidate_hash != configuration_hash(assumption.model_dump(mode="json"))
            or payload.confirmation_deadline != payload.admitted_at + timedelta(minutes=15)
            or epoch is None
            or epoch.user_id != user_id
            or epoch.opened_at > payload.admitted_at
        ):
            raise ValueError("Original candidate owner, source, window or request differs")
        _original_source(session, payload.source, now)
        return FutureIncomeCandidate(
            candidate_id=row.id,
            user_id=user_id,
            epoch_id=epoch_id,
            admitted_at=payload.admitted_at,
            confirmation_deadline=payload.confirmation_deadline,
            source=payload.source,
            assumption=assumption,
            candidate_hash=payload.candidate_hash,
            original_request=body,
            request_hash=payload.request_hash,
            original_evidence=row_copy(row),
            evidence_hash=row.content_hash,
            state="EXPIRED"
            if now >= payload.confirmation_deadline
            else "REQUIRES_EXPLICIT_CONFIRMATION",
        )
    except (ValueError, TypeError, KeyError) as error:
        raise PolicyLifecycleError(
            "INVALID_FUTURE_INCOME_ORIGINAL", "原条件候选/来源/声明无法核验", 409
        ) from error


def _confirmation(
    session: Session, row: EvidenceItem, user_id: UUID, epoch_id: UUID, now: datetime
) -> tuple[FutureIncomeCandidate, FutureIncomeConfirmation]:
    try:
        payload = ConfirmationPayload.model_validate_json(json.dumps(row.content))
        body = payload.original_request
        _metadata_fields(
            row,
            user_id,
            epoch_id,
            body.idempotency_key,
            now,
            payload.confirmed_at,
            CONFIRM_SOURCE,
            payload.model_dump(mode="json"),
        )
        require_local_user(payload.actor, user_id, payload.confirmed_at)
        candidate_row = session.get(EvidenceItem, payload.candidate_id)
        if candidate_row is None:
            raise ValueError("Original candidate is missing")
        candidate = _candidate(session, candidate_row, user_id, epoch_id, now)
        if (
            payload.confirmation_id != row.id
            or payload.user_id != user_id
            or payload.epoch_id != epoch_id
            or body.expected_epoch_id != epoch_id
            or body.candidate_id != payload.candidate_id
            or body.reviewed_candidate_hash != payload.candidate_hash
            or candidate.candidate_hash != payload.candidate_hash
            or not candidate.admitted_at <= payload.confirmed_at < candidate.confirmation_deadline
            or payload.request_hash != configuration_hash(body.model_dump(mode="json"))
            or payload.original_candidate_snapshot != row_copy(candidate_row)
        ):
            raise ValueError("Exact original confirmation does not match this candidate")
        return candidate.model_copy(update={"state": "USER_CONFIRMED"}), FutureIncomeConfirmation(
            confirmation_id=row.id,
            user_id=user_id,
            epoch_id=epoch_id,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            confirmed_at=payload.confirmed_at,
            original_request=body,
            request_hash=payload.request_hash,
            original_evidence=row_copy(row),
            evidence_hash=row.content_hash,
        )
    except (ValueError, TypeError, KeyError) as error:
        if isinstance(error, PolicyLifecycleError):
            raise
        raise PolicyLifecycleError(
            "INVALID_FUTURE_INCOME_ORIGINAL", "原明确确认无法核验", 409
        ) from error


def _admit(
    session: Session, user_id: UUID, epoch_id: UUID, principal: LocalActorPrincipal, now: datetime
) -> User:
    require_local_user(principal, user_id, now)
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("DIRTY_PLANNING_SESSION", "不能混入其他待写入行", 409)
    user = _user(session, user_id)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.id != epoch_id or epoch.opened_at > now:
        raise PolicyLifecycleError("STALE_PLANNING_EPOCH", "只接受当前实际已知OPEN轮次", 409)
    return user


def _append(
    session: Session,
    user_id: UUID,
    epoch_id: UUID,
    key: str,
    source: str,
    now: datetime,
    payload: BoundaryModel,
) -> EvidenceItem:
    identifier, reference = identity(user_id, epoch_id, key)
    content = payload.model_dump(mode="json")
    row = EvidenceItem(
        id=identifier,
        user_id=user_id,
        created_at=now,
        evidence_level="USER_DECLARED",
        source_type=source,
        source_ref=reference,
        content=content,
        content_hash=configuration_hash(content),
        valid_from=now,
        valid_to=None,
        observed_at=now,
        supersedes_id=None,
        status="VALID",
    )
    session.add(row)
    session.flush()
    return row


def create_future_income_candidate(
    session: Session,
    user_id: UUID,
    body: FutureIncomeCandidateRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FutureIncomeCandidate:
    now = _now(now)
    user = _admit(session, user_id, body.expected_epoch_id, principal, now)
    identifier, _ = identity(user_id, body.expected_epoch_id, body.idempotency_key)
    previous = session.get(EvidenceItem, identifier)
    if previous is not None:
        original = _candidate(session, previous, user_id, body.expected_epoch_id, now)
        if original.original_request != body:
            raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "同原键不能换来源或命令", 409)
        return original
    if len(_records(session, user_id, body.expected_epoch_id)) >= METADATA_LIMIT:
        raise PolicyLifecycleError(
            "PLANNING_METADATA_CAPACITY_EXCEEDED", "声明容量已满，拒绝新增，不省略原件", 409
        )
    sources = _sources(session, user_id, now)
    source = next(
        (
            item
            for item in sources.sources
            if item.origin.origin_transaction_id == body.origin_transaction_id
        ),
        None,
    )
    if not sources.complete or source is None or body.expected_origin_hash != source.origin_hash:
        raise PolicyLifecycleError(
            "UNKNOWN_OR_UNREGISTERED_INCOME_SOURCE",
            "须选择完整原银行收入来源，不能推断或输入金额",
            409,
        )
    assumption = assumption_for_source(source, body.expected_epoch_id, now, _zone(user))
    payload = CandidatePayload(
        candidate_id=identifier,
        user_id=user_id,
        epoch_id=body.expected_epoch_id,
        admitted_at=now,
        confirmation_deadline=now + timedelta(minutes=15),
        actor=principal,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        source=source,
        assumption=assumption,
        candidate_hash=configuration_hash(assumption.model_dump(mode="json")),
    )
    with session.begin_nested():
        row = _append(
            session,
            user_id,
            body.expected_epoch_id,
            body.idempotency_key,
            CANDIDATE_SOURCE,
            now,
            payload,
        )
        return _candidate(session, row, user_id, body.expected_epoch_id, now)


def confirm_future_income_candidate(
    session: Session,
    user_id: UUID,
    body: FutureIncomeConfirmationRequest,
    principal: LocalActorPrincipal,
    now: datetime,
) -> FutureIncomeConfirmation:
    now = _now(now)
    _admit(session, user_id, body.expected_epoch_id, principal, now)
    identifier, _ = identity(user_id, body.expected_epoch_id, body.idempotency_key)
    previous = session.get(EvidenceItem, identifier)
    if previous is not None:
        _, original = _confirmation(session, previous, user_id, body.expected_epoch_id, now)
        if original.original_request != body:
            raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "同原键不能换候选/摘要", 409)
        return original
    row = session.get(EvidenceItem, body.candidate_id)
    if row is None:
        raise PolicyLifecycleError("NOT_FOUND", "原条件候选不存在", 404)
    candidate = _candidate(session, row, user_id, body.expected_epoch_id, now)
    if candidate.state == "EXPIRED" or candidate.candidate_hash != body.reviewed_candidate_hash:
        raise PolicyLifecycleError(
            "STALE_CANDIDATE_REVIEW", "复核摘要不匹配或候选已过确认窗口", 409
        )
    sources = _sources(session, user_id, now)
    if not sources.complete or not any(
        item.origin_hash == candidate.source.origin_hash for item in sources.sources
    ):
        raise PolicyLifecycleError(
            "UNKNOWN_CURRENT_INCOME_SOURCE", "确认时原收入来源须再次独立核验", 409
        )
    existing = _records(session, user_id, body.expected_epoch_id)
    if len(existing) >= METADATA_LIMIT:
        raise PolicyLifecycleError(
            "PLANNING_METADATA_CAPACITY_EXCEEDED", "声明容量已满，拒绝新增，不省略原件", 409
        )
    for previous_row in existing:
        if previous_row.source_type != CONFIRM_SOURCE:
            _candidate(session, previous_row, user_id, body.expected_epoch_id, now)
            continue
        other, _ = _confirmation(session, previous_row, user_id, body.expected_epoch_id, now)
        if (
            other.candidate_id == body.candidate_id
            or other.source.origin_hash == candidate.source.origin_hash
        ):
            raise PolicyLifecycleError(
                "ALREADY_REGISTERED_ORIGINAL_SOURCE",
                "同原收入来源已有确认，只能读原记录，不重复计入",
                409,
            )
    payload = ConfirmationPayload(
        confirmation_id=identifier,
        user_id=user_id,
        epoch_id=body.expected_epoch_id,
        candidate_id=body.candidate_id,
        candidate_hash=candidate.candidate_hash,
        confirmed_at=now,
        actor=principal,
        original_request=body,
        request_hash=configuration_hash(body.model_dump(mode="json")),
        original_candidate_snapshot=row_copy(row),
    )
    with session.begin_nested():
        confirmed = _append(
            session,
            user_id,
            body.expected_epoch_id,
            body.idempotency_key,
            CONFIRM_SOURCE,
            now,
            payload,
        )
        return _confirmation(session, confirmed, user_id, body.expected_epoch_id, now)[1]


def lookup_future_income_command(
    session: Session, user_id: UUID, epoch_id: UUID, key: str, now: datetime
) -> FutureIncomeCommandLookup:
    readonly(session)
    now = _now(now)
    identifier, _ = identity(user_id, epoch_id, key)
    row = session.get(EvidenceItem, identifier)
    if row is None:
        return FutureIncomeCommandLookup(
            user_id=user_id,
            epoch_id=epoch_id,
            idempotency_key=key,
            status="NOT_FOUND_NOT_FINAL",
            command_kind=None,
            original_request=None,
            request_hash=None,
            candidate=None,
            confirmation=None,
        )
    if row.source_type == CANDIDATE_SOURCE:
        candidate = _candidate(session, row, user_id, epoch_id, now)
        return FutureIncomeCommandLookup(
            user_id=user_id,
            epoch_id=epoch_id,
            idempotency_key=key,
            status="RECORDED",
            command_kind="CANDIDATE",
            original_request=candidate.original_request,
            request_hash=candidate.request_hash,
            candidate=candidate,
            confirmation=None,
        )
    candidate, confirmation = _confirmation(session, row, user_id, epoch_id, now)
    return FutureIncomeCommandLookup(
        user_id=user_id,
        epoch_id=epoch_id,
        idempotency_key=key,
        status="RECORDED",
        command_kind="CONFIRM",
        original_request=confirmation.original_request,
        request_hash=confirmation.request_hash,
        candidate=candidate,
        confirmation=confirmation,
    )


def read_future_income_planning(
    session: Session, user_id: UUID, now: datetime
) -> FutureIncomePlanningResponse:
    readonly(session)
    now = _now(now)
    sources = _sources(session, user_id, now)
    issues = list(sources.issues)
    states: dict[UUID, FutureIncomePlanState] = {}
    records = []
    if sources.epoch_id is not None:
        try:
            records = _records(session, user_id, sources.epoch_id)
        except PolicyLifecycleError as error:
            issues.append(error.code)
    candidates = [row for row in records if row.source_type == CANDIDATE_SOURCE]
    confirmations = [row for row in records if row.source_type == CONFIRM_SOURCE]
    for row in candidates:
        try:
            candidate = _candidate(session, row, user_id, cast(UUID, sources.epoch_id), now)
            states[row.id] = FutureIncomePlanState(
                candidate_id=row.id,
                confirmation_id=None,
                state="EXPIRED"
                if candidate.state == "EXPIRED"
                else "REQUIRES_EXPLICIT_CONFIRMATION",
                candidate=candidate,
                confirmation=None,
                original_metadata=[row_copy(row)],
                issues=[],
            )
        except PolicyLifecycleError as error:
            issues.append(error.code)
            states[row.id] = FutureIncomePlanState(
                candidate_id=row.id,
                confirmation_id=None,
                state="UNKNOWN",
                candidate=None,
                confirmation=None,
                original_metadata=[row_copy(row)],
                issues=[error.code],
            )
    source_ids: dict[str, UUID] = {}
    for row in confirmations:
        try:
            candidate, confirmation = _confirmation(
                session, row, user_id, cast(UUID, sources.epoch_id), now
            )
            origin = candidate.source.origin_hash
            old = source_ids.get(origin)
            state: Literal["CONFLICTED", "UNKNOWN", "EXPIRED", "USER_CONFIRMED_CONDITION"]
            if old is not None or (
                candidate.candidate_id in states
                and states[candidate.candidate_id].confirmation is not None
            ):
                issues.append("CONFLICTING_CONFIRMED_INCOME_SOURCE")
                if old is not None:
                    states[old] = states[old].model_copy(update={"state": "CONFLICTED"})
                state = "CONFLICTED"
            elif not any(item.origin_hash == origin for item in sources.sources):
                state = "UNKNOWN"
                issues.append("UNKNOWN_CURRENT_INCOME_SOURCE")
            elif candidate.assumption.timezone != sources.timezone:
                state = "UNKNOWN"
                issues.append("REGISTERED_PLANNING_TIMEZONE_CHANGED")
            elif (
                candidate.assumption.valid_until < now.astimezone(ZoneInfo(sources.timezone)).date()
            ):
                state = "EXPIRED"
            else:
                state = "USER_CONFIRMED_CONDITION"
            source_ids[origin] = candidate.candidate_id
            retained = states.get(candidate.candidate_id)
            metadata = (
                list(retained.original_metadata)
                if retained is not None
                else [candidate.original_evidence]
            )
            if not any(item.get("id") == str(row.id) for item in metadata):
                metadata.append(row_copy(row))
            states[candidate.candidate_id] = FutureIncomePlanState(
                candidate_id=candidate.candidate_id,
                confirmation_id=confirmation.confirmation_id,
                state=state,
                candidate=candidate,
                confirmation=confirmation,
                original_metadata=metadata,
                issues=[],
            )
        except PolicyLifecycleError as error:
            issues.append(error.code)
            states[row.id] = FutureIncomePlanState(
                candidate_id=row.id,
                confirmation_id=row.id,
                state="UNKNOWN",
                candidate=None,
                confirmation=None,
                original_metadata=[row_copy(row)],
                issues=[error.code],
            )
    active = [
        (key, view.candidate.assumption)
        for key, view in states.items()
        if view.state == "USER_CONFIRMED_CONDITION" and view.candidate is not None
    ]
    status: Literal["UNKNOWN", "CONDITIONAL_PLANNING", "NO_CONFIRMED_REGISTERED_SOURCE"] = (
        "UNKNOWN"
        if issues
        else "CONDITIONAL_PLANNING"
        if active
        else "NO_CONFIRMED_REGISTERED_SOURCE"
    )
    try:
        schedule = conditional_schedule(
            now, sources.timezone, active if status == "CONDITIONAL_PLANNING" else None
        )
        total = (
            sum(item.conditional_income_cents or 0 for item in schedule)
            if status == "CONDITIONAL_PLANNING"
            else None
        )
        if total is not None and total > 9_223_372_036_854_775_807:
            raise ValueError("Conditional total exceeds the exact cents range")
    except ValueError:
        issues.append("CONDITIONAL_PLANNING_TOTAL_EXCEEDS_CENTS_RANGE")
        status, total = "UNKNOWN", None
        schedule = conditional_schedule(now, sources.timezone, None)
    digest = configuration_hash(
        {
            "protocol": "future-income-read-input-v1",
            "user_id": str(user_id),
            "epoch_id": str(sources.epoch_id),
            "as_of": now.isoformat(),
            "sources": sources.model_dump(mode="json"),
            "metadata": [row_copy(row) for row in records],
            "issues": issues,
        }
    )
    return FutureIncomePlanningResponse(
        user_id=user_id,
        epoch_id=sources.epoch_id,
        as_of=now,
        timezone=sources.timezone,
        status=status,
        sources=sources,
        plans=[states[key] for key in sorted(states)],
        total_conditional_income_cents=total,
        daily_schedule=schedule,
        input_hash=digest,
        issues=issues,
    )
