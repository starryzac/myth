"""Deterministic historical observations, never a grant of execution authority."""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from app.db.models import Account, CreditCardBill, EvidenceItem, PolicyProposal, Transaction, User
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.policy_lifecycle import PolicyLifecycleError
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

RULE_VERSION = "mvp-104-v1"
SOURCE_TYPE = "DETERMINISTIC_POLICY_DISCOVERY"


class DiscoverySkip(BaseModel):
    model_config = ConfigDict(frozen=True)
    reason_code: str
    source_ref: str


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    user_id: UUID
    rule_version: str = RULE_VERSION
    as_of: datetime
    created_proposal_ids: list[UUID]
    reused_proposal_ids: list[UUID]
    skipped: list[DiscoverySkip]


def _clock(now: datetime, user: User) -> tuple[datetime, timezone, date, date]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    if user.timezone not in {"Asia/Shanghai", "UTC"}:
        raise PolicyLifecycleError("UNSUPPORTED_TIMEZONE", "当前仅支持 Asia/Shanghai 或 UTC")
    zone = timezone(timedelta(hours=8)) if user.timezone == "Asia/Shanghai" else UTC
    try:
        now = now.astimezone(UTC)
        end = now.astimezone(zone).date()
        return now, zone, end - timedelta(days=59), end
    except (OverflowError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间超出观察窗口支持范围") from error


def _evidence_reason(evidence: EvidenceItem | None, user_id: UUID, now: datetime) -> str | None:
    if evidence is None or evidence.user_id != user_id:
        return "MISSING_EVIDENCE"
    if evidence.evidence_level != "BANK_CONFIRMED" or evidence.status != "VALID":
        return "UNTRUSTED_EVIDENCE"
    if (
        evidence.observed_at > now
        or evidence.valid_from > now
        or (evidence.valid_to is not None and evidence.valid_to <= now)
    ):
        return "EVIDENCE_OUTSIDE_TIME"
    try:
        if configuration_hash(evidence.content) != evidence.content_hash:
            return "EVIDENCE_HASH_MISMATCH"
    except (ValueError, TypeError):
        return "EVIDENCE_HASH_MISMATCH"
    return None


def _transaction_payload(row: Transaction) -> dict[str, Any]:
    return {
        "transaction_id": str(row.id),
        "account_id": str(row.account_id),
        "direction": row.direction,
        "amount_cents": row.amount_cents,
        "balance_after_cents": row.balance_after_cents,
        "occurred_at": row.occurred_at.isoformat(),
        "counterparty_ref": row.counterparty_ref,
    }


def _bill_payload(row: CreditCardBill) -> dict[str, Any]:
    return {
        "total_cents": row.total_cents,
        "paid_cents": row.paid_cents,
        "statement_date": row.statement_date.isoformat(),
        "due_date": row.due_date.isoformat(),
        "status": row.status,
    }


def _source(
    row: Transaction | CreditCardBill, evidence: EvidenceItem, payload: dict[str, Any]
) -> dict[str, Any]:
    return {
        "fact_type": row.__tablename__,
        "fact_id": str(row.id),
        "source_ref": row.source_ref,
        "account_id": str(row.account_id),
        "evidence_id": str(evidence.id),
        "evidence_hash": evidence.content_hash,
        "evidence_source_type": evidence.source_type,
        "evidence_source_ref": evidence.source_ref,
        "evidence_observed_at": evidence.observed_at.isoformat(),
        "fact": payload,
    }


def _matches(content: dict[str, Any], expected: dict[str, Any]) -> bool:
    # Equality alone would accept True for a 1-cent amount. Compare canonical JSON values.
    return configuration_hash({key: content.get(key) for key in expected}) == configuration_hash(
        expected
    ) and all(key in content for key in expected)


def _consecutive(days: list[date]) -> bool:
    months = sorted(day.year * 12 + day.month for day in days)
    return len(months) >= 2 and all(
        right - left == 1 for left, right in zip(months, months[1:], strict=False)
    )


def discover_policies(session: Session, user_id: UUID, now: datetime) -> DiscoveryResult:
    """Write proposals and observation evidence inside the caller's transaction."""
    with session.begin_nested():
        user = session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or not user.is_simulated:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        now, zone, start, end = _clock(now, user)
        result = DiscoveryResult(
            user_id=user_id, as_of=now, created_proposal_ids=[], reused_proposal_ids=[], skipped=[]
        )
        previous = list(
            session.scalars(
                select(PolicyProposal)
                .where(PolicyProposal.user_id == user_id, PolicyProposal.source_type == SOURCE_TYPE)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        accounts = {
            row.id: row
            for row in session.scalars(select(Account).where(Account.user_id == user_id))
        }
        evidence_by_id = {
            row.id: row
            for row in session.scalars(
                select(EvidenceItem)
                .where(EvidenceItem.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        rent_groups: dict[tuple[UUID, str], list[Transaction]] = defaultdict(list)
        for row in session.scalars(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ):
            if (
                row.category != "rent"
                or row.direction != "DEBIT"
                or row.is_one_off
                or not row.counterparty_ref
                or not row.counterparty_ref.strip()
                or accounts[row.account_id].account_type != "CASH"
            ):
                continue
            if row.occurred_at > now or row.observed_at > now:
                result.skipped.append(
                    DiscoverySkip(reason_code="FUTURE_FACT", source_ref=row.source_ref)
                )
                continue
            if start <= row.occurred_at.astimezone(zone).date() <= end:
                rent_groups[(row.account_id, row.counterparty_ref)].append(row)
        bill_groups: dict[UUID, list[CreditCardBill]] = defaultdict(list)
        bills = list(
            session.scalars(
                select(CreditCardBill)
                .where(CreditCardBill.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        bill_evidence_uses = Counter(
            row.evidence_id for row in bills if row.evidence_id is not None
        )
        for bill in bills:
            if accounts[bill.account_id].account_type != "CREDIT_CARD":
                continue
            if bill.statement_date > end:
                result.skipped.append(
                    DiscoverySkip(reason_code="FUTURE_FACT", source_ref=bill.source_ref)
                )
            elif bill.statement_date >= start:
                bill_groups[bill.account_id].append(bill)
        for (account_id, payee), rows in sorted(rent_groups.items()):
            days = [row.occurred_at.astimezone(zone).date() for row in rows]
            if (
                not _consecutive(days)
                or len({day.day for day in days}) != 1
                or len({row.amount_cents for row in rows}) != 1
            ):
                result.skipped.append(
                    DiscoverySkip(
                        reason_code="RENT_PATTERN_MISMATCH", source_ref=f"rent:{account_id}:{payee}"
                    )
                )
                continue
            sources: list[dict[str, Any]] = []
            for row in sorted(rows, key=lambda item: item.occurred_at):
                evidence = evidence_by_id.get(row.evidence_id) if row.evidence_id else None
                reason = _evidence_reason(evidence, user_id, now)
                payload = _transaction_payload(row)
                if (
                    reason is None
                    and evidence is not None
                    and not _matches(evidence.content, payload)
                ):
                    reason = "EVIDENCE_FACT_MISMATCH"
                if reason is not None:
                    result.skipped.append(
                        DiscoverySkip(reason_code=reason, source_ref=row.source_ref)
                    )
                    break
                assert evidence is not None
                sources.append(_source(row, evidence, payload))
            else:
                configuration = validate_configuration(
                    {
                        "type": "recurring_obligation",
                        "name": "房租预留候选",
                        "payee_id": payee,
                        "due_day": days[0].day,
                        "amount_rule": {
                            "kind": "range",
                            "min_cents": min(row.amount_cents for row in rows),
                            "max_cents": max(row.amount_cents for row in rows),
                        },
                        "auto_execute": False,
                    }
                )
                _create(
                    session,
                    user,
                    now,
                    start,
                    end,
                    "monthly_rent",
                    account_id,
                    payee,
                    sources,
                    configuration,
                    result,
                )
        for account_id, card_rows in sorted(bill_groups.items()):
            due_dates = [bill.due_date for bill in card_rows]
            if not _consecutive(due_dates) or len({day.day for day in due_dates}) != 1:
                result.skipped.append(
                    DiscoverySkip(
                        reason_code="BILL_PATTERN_MISMATCH", source_ref=f"credit-card:{account_id}"
                    )
                )
                continue
            sources = []
            for bill in sorted(card_rows, key=lambda item: item.due_date):
                evidence = evidence_by_id.get(bill.evidence_id) if bill.evidence_id else None
                reason = _evidence_reason(evidence, user_id, now)
                payload = _bill_payload(bill)
                if (
                    reason is None
                    and evidence is not None
                    and (
                        not _matches(evidence.content, payload)
                        or evidence.source_ref != bill.source_ref
                        or bill_evidence_uses[evidence.id] != 1
                        or (
                            "account_id" in evidence.content
                            and evidence.content["account_id"] != str(account_id)
                        )
                        or (
                            "bill_id" in evidence.content
                            and evidence.content["bill_id"] != str(bill.id)
                        )
                    )
                ):
                    reason = "EVIDENCE_FACT_MISMATCH"
                if reason is not None:
                    result.skipped.append(
                        DiscoverySkip(reason_code=reason, source_ref=bill.source_ref)
                    )
                    break
                assert evidence is not None
                sources.append(_source(bill, evidence, payload))
            else:
                payee = f"credit-card:{account_id}"
                configuration = validate_configuration(
                    {
                        "type": "recurring_obligation",
                        "name": "信用卡还款预留候选",
                        "payee_id": payee,
                        "due_day": due_dates[0].day,
                        "amount_rule": {"kind": "bill_balance", "account_id": str(account_id)},
                        "auto_execute": False,
                    }
                )
                _create(
                    session,
                    user,
                    now,
                    start,
                    end,
                    "credit_card_cycle",
                    account_id,
                    payee,
                    sources,
                    configuration,
                    result,
                )
        retained = set(result.created_proposal_ids + result.reused_proposal_ids)
        for proposal in previous:
            if proposal.status == "PROPOSED" and proposal.id not in retained:
                proposal.status = "EXPIRED"
        session.flush()
        return result


def _create(
    session: Session,
    user: User,
    now: datetime,
    start: date,
    end: date,
    rule: str,
    account_id: UUID,
    payee: str,
    sources: list[dict[str, Any]],
    configuration: dict[str, Any],
    result: DiscoveryResult,
) -> None:
    object_key = configuration_hash(
        {"user_id": str(user.id), "rule": rule, "account_id": str(account_id), "payee_id": payee}
    )
    revision = configuration_hash(
        {
            "rule_version": RULE_VERSION,
            "sources": sources,
            "configuration_hash": configuration_hash(configuration),
        }
    )
    key = f"discovery:{object_key}:{revision}"
    same_object = list(
        session.scalars(
            select(PolicyProposal).where(
                PolicyProposal.user_id == user.id,
                PolicyProposal.source_type == SOURCE_TYPE,
                PolicyProposal.idempotency_key.startswith(f"discovery:{object_key}:"),
            )
        )
    )
    if any(p.confirmed_policy_id is not None or p.status == "CONFIRMED" for p in same_object):
        result.skipped.append(
            DiscoverySkip(reason_code="ALREADY_CONFIRMED", source_ref=f"discovery:{object_key}")
        )
        return
    existing = next((p for p in same_object if p.idempotency_key == key), None)
    if existing is not None:
        if existing.status == "PROPOSED":
            result.reused_proposal_ids.append(existing.id)
        else:
            result.skipped.append(DiscoverySkip(reason_code="CLOSED_REVISION", source_ref=key))
        return
    evidence_id = uuid4()
    content = {
        "simulation": True,
        "rule_version": RULE_VERSION,
        "rule": rule,
        "object_key": object_key,
        "revision": revision,
        "sources": sources,
        "matched_rule": "相邻自然月各一次、同日号"
        + ("、同金额" if rule == "monthly_rent" else "；账期按到期月份"),
        "future_obligation_guaranteed": False,
        "limitation": "仅历史观察，不能保证未来义务；须用户审核确认，自动执行默认关闭。",
        "observation_window": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": user.timezone,
            "days": 60,
        },
        "configuration_hash": configuration_hash(configuration),
    }
    session.add(
        EvidenceItem(
            id=evidence_id,
            user_id=user.id,
            evidence_level="BANK_OBSERVED",
            source_type=SOURCE_TYPE,
            source_ref=key,
            content=content,
            content_hash=configuration_hash(content),
            valid_from=now,
            observed_at=now,
            created_at=now,
            status="VALID",
        )
    )
    proposal = PolicyProposal(
        id=uuid4(),
        user_id=user.id,
        source_type=SOURCE_TYPE,
        source_text=content["limitation"],
        compiler_version=RULE_VERSION,
        proposed_configuration=configuration,
        evidence_ids=[source["evidence_id"] for source in sources] + [str(evidence_id)],
        status="PROPOSED",
        idempotency_key=key,
        created_at=now,
    )
    session.add(proposal)
    result.created_proposal_ids.append(proposal.id)
