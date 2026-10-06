"""Atomically confirm classification and append typed originals; never move funds."""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.audit_guard import audit_command_guard
from app.db.models import EvidenceItem, Transaction, User
from app.domain.history_coverage import bank_fact_snapshot
from app.domain.policy_configuration import configuration_hash
from app.domain.transaction_category import (
    REVIEW_PROTOCOL,
    CategoryConfirmationRequest,
    CategoryKey,
    Digest,
    category_command,
    category_review_hash,
    verify_category_transition,
)
from app.services.audit_chain import current_audit_epoch, find_audit_fact, verify_audit_chain
from app.services.audit_recording import _record, audit_subject_data
from app.services.living_reserve import CATEGORY_SOURCE_TYPE, _valid
from app.services.policy_lifecycle import PolicyLifecycleError, _now, _user
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

EVENT = "TRANSACTION_CATEGORY_CONFIRMED"


class CategoryReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol: Literal["transaction-category-review-v1"] = REVIEW_PROTOCOL
    simulation: Literal[True] = True
    user_id: UUID
    transaction_id: UUID
    epoch_id: UUID
    as_of: datetime
    transaction: dict[str, Any]
    bank_fact: dict[str, Any]
    reviewed_transaction_hash: Digest
    first_confirmation_supported: bool
    grants_authority: Literal[False] = False
    bank_facts_changed: Literal[False] = False


class CategoryCommandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol: Literal["transaction-category-command-result-v1"] = (
        "transaction-category-command-result-v1"
    )
    simulation: Literal[True] = True
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    user_id: UUID
    transaction_id: UUID
    epoch_id: UUID
    idempotency_key: CategoryKey
    original_command: dict[str, Any] | None
    request_hash: Digest | None
    original_receipt: dict[str, Any] | None
    evidence_id: UUID | None
    audit_event_id: UUID | None
    audit_event_hash: Digest | None
    replayed_original: bool
    grants_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    bank_facts_changed: Literal[False] = False
    replacement_allowed: Literal[False] = False


def _error(message: str) -> PolicyLifecycleError:
    return PolicyLifecycleError("CATEGORY_CONFIRMATION_NOT_READY", message, 409)


def _row(session: Session, user_id: UUID, transaction_id: UUID) -> Transaction:
    row = session.get(Transaction, transaction_id)
    if row is None or row.user_id != user_id:
        raise PolicyLifecycleError("NOT_FOUND", "原本人交易不存在", 404)
    return row


def _identity(epoch_id: UUID, user_id: UUID, transaction_id: UUID, key: str) -> UUID:
    return uuid5(
        epoch_id,
        "category:"
        + configuration_hash(
            {"user_id": str(user_id), "transaction_id": str(transaction_id), "key": key}
        ),
    )


def review_category(
    session: Session, user_id: UUID, transaction_id: UUID, now: datetime
) -> CategoryReviewResponse:
    now = _now(now)
    user = session.get(User, user_id)
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    epoch = current_audit_epoch(session, user_id)
    if epoch is None or epoch.status != "OPEN":
        raise _error("缺少当前开放模拟期")
    row = _row(session, user_id, transaction_id)
    evidence = session.get(EvidenceItem, row.evidence_id) if row.evidence_id else None
    if (
        evidence is None
        or evidence.user_id != user_id
        or not _valid(evidence, "BANK_CONFIRMED", now)
        or row.observed_at > now
        or row.occurred_at > now
    ):
        raise _error("交易或独立银行原件未验真或尚不可知")
    try:
        bank = bank_fact_snapshot(row, evidence)
    except (TypeError, ValueError) as exc:
        raise _error("交易金额与独立银行原件不一致") from exc
    if bank["economic_role"] != "CONSUMPTION" or row.direction != "DEBIT":
        raise _error("只能确认原银行消费支出，不能将收入、转账或本金返还伪装消费")
    raw = audit_subject_data(row)
    return CategoryReviewResponse(
        user_id=user_id,
        transaction_id=transaction_id,
        epoch_id=epoch.id,
        as_of=now,
        transaction=raw,
        bank_fact=bank,
        reviewed_transaction_hash=category_review_hash(epoch.id, raw, bank),
        first_confirmation_supported=not row.category_confirmed,
    )


def lookup_category_command(
    session: Session, user_id: UUID, transaction_id: UUID, epoch_id: UUID, key: str, now: datetime
) -> CategoryCommandResponse:
    now = _now(now)
    _row(session, user_id, transaction_id)
    identity = _identity(epoch_id, user_id, transaction_id, key)
    evidence = session.get(EvidenceItem, identity)
    common: dict[str, Any] = dict(
        user_id=user_id, transaction_id=transaction_id, epoch_id=epoch_id, idempotency_key=key
    )
    if evidence is None:
        return CategoryCommandResponse(
            **common,
            status="NOT_FOUND_NOT_FINAL",
            original_command=None,
            request_hash=None,
            original_receipt=None,
            evidence_id=None,
            audit_event_id=None,
            audit_event_hash=None,
            replayed_original=True,
        )
    event = find_audit_fact(session, user_id, EVENT, f"CATEGORY_CONFIRMED:{identity}", epoch_id)
    verified = verify_audit_chain(session, user_id, epoch_id=epoch_id)
    if (
        evidence.user_id != user_id
        or evidence.source_type != CATEGORY_SOURCE_TYPE
        or evidence.evidence_level != "USER_DECLARED"
        or evidence.observed_at > now
        or configuration_hash(evidence.content) != evidence.content_hash
        or event is None
        or verified.chain_status != "VALID"
        or verified.reference_status not in {"VALID", "LEGACY_UNAUDITED"}
        or verified.errors
    ):
        raise _error("原分类命令、typed审计或来源未验真")
    try:
        command = evidence.content["original_command"]
        body = CategoryConfirmationRequest.model_validate_json(json.dumps(command["request"]))
        if (
            command != category_command(user_id, transaction_id, body)
            or body.expected_epoch_id != epoch_id
            or body.idempotency_key != key
            or configuration_hash(command) != evidence.content["command_hash"]
        ):
            raise ValueError("Original command mismatch")
        receipt = evidence.content["original_receipt"]
    except (KeyError, TypeError, ValueError) as exc:
        raise _error("原分类封套不完整") from exc
    return CategoryCommandResponse(
        **common,
        status="RECORDED",
        original_command=command,
        request_hash=configuration_hash(command),
        original_receipt=receipt,
        evidence_id=evidence.id,
        audit_event_id=event.id,
        audit_event_hash=event.event_hash,
        replayed_original=True,
    )


def confirm_category(
    engine: Engine,
    user_id: UUID,
    transaction_id: UUID,
    body: CategoryConfirmationRequest,
    now: datetime,
) -> CategoryCommandResponse:
    body = CategoryConfirmationRequest.model_validate_json(body.model_dump_json())
    now = _now(now)
    with audit_command_guard(engine, user_id), Session(engine) as session, session.begin():
        _user(session, user_id)
        epoch = current_audit_epoch(session, user_id)
        if epoch is None or epoch.status != "OPEN" or epoch.id != body.expected_epoch_id:
            raise _error("原模拟期已失效，不能新分类或迁移原键")
        identity = _identity(epoch.id, user_id, transaction_id, body.idempotency_key)
        if session.get(EvidenceItem, identity) is not None:
            existing = lookup_category_command(
                session, user_id, transaction_id, epoch.id, body.idempotency_key, now
            )
            if existing.original_command != category_command(user_id, transaction_id, body):
                raise PolicyLifecycleError("IDEMPOTENCY_CONFLICT", "同原分类键不能改变原请求", 409)
            return existing
        review = review_category(session, user_id, transaction_id, now)
        if review.reviewed_transaction_hash != body.reviewed_transaction_hash:
            raise _error("交易、来源或已复核类别发生变化，请重新读取并明确确认")
        row = _row(session, user_id, transaction_id)
        if row.category_confirmed:
            raise _error("此交易已确认；本入口仅支持首次确认，更正需独立版本流程")
        prior = list(
            session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == CATEGORY_SOURCE_TYPE,
                    EvidenceItem.content["transaction_id"].astext == str(transaction_id),
                )
            )
        )
        if prior:
            raise _error("未确认投影已有分类声明，原件不一致，不能覆盖")
        command = category_command(user_id, transaction_id, body)
        receipt = {
            "transaction_id": str(transaction_id),
            "before_category": row.category,
            "category": body.category,
            "before_confirmed": False,
            "confirmed": True,
            "bank_evidence_id": str(row.evidence_id),
            "bank_evidence_hash": review.bank_fact["evidence_hash"],
            "confirmed_at": now.isoformat(),
        }
        content = {
            "simulation": True,
            "transaction_id": str(transaction_id),
            "category": body.category,
            "confirmed": True,
            "actor": "synthetic_user",
            "basis": "explicit_user_category_selection",
            "original_command": command,
            "command_hash": configuration_hash(command),
            "original_receipt": receipt,
        }
        evidence = EvidenceItem(
            id=identity,
            user_id=user_id,
            created_at=now,
            evidence_level="USER_DECLARED",
            source_type=CATEGORY_SOURCE_TYPE,
            source_ref=f"category-command:{identity}",
            content=content,
            content_hash=configuration_hash(content),
            observed_at=now,
            valid_from=now,
            valid_to=None,
            status="VALID",
        )
        bank = session.get(EvidenceItem, row.evidence_id)
        assert bank is not None
        row.category, row.category_confirmed = body.category, True
        session.add(evidence)
        session.flush()
        verify_category_transition(
            user_id=user_id,
            epoch_id=epoch.id,
            transaction_id=transaction_id,
            before=review.transaction,
            after=audit_subject_data(row),
            bank=audit_subject_data(bank),
            declaration=audit_subject_data(evidence),
        )
        _record(
            session,
            user_id=user_id,
            event_type=EVENT,
            aggregate_type="TRANSACTION",
            aggregate_id=transaction_id,
            correlation_id=transaction_id,
            correlation_kind="TRANSACTION",
            occurred_at=now,
            observed_at=now,
            fact_key=f"CATEGORY_CONFIRMED:{identity}",
            subjects=[
                ("TRANSACTION", transaction_id, "BEFORE", review.transaction),
                ("TRANSACTION", transaction_id, "AFTER", None),
                ("EVIDENCE", bank.id, "BASIS", None),
                ("EVIDENCE", identity, "AFTER", None),
            ],
            anchor_specs=[
                ("EVIDENCE_CONTENT", identity, evidence.content_hash, "configuration-sha256-v1")
            ],
            context={
                "reason_code": "USER_CLASSIFICATION_CONFIRMED",
                "cause_ref": str(identity),
                "details": {
                    "category_evidence_id": str(identity),
                    "command_hash": configuration_hash(command),
                },
            },
        )
        result = lookup_category_command(
            session, user_id, transaction_id, epoch.id, body.idempotency_key, now
        )
        return result.model_copy(update={"replayed_original": False})
